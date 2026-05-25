"""Flask web layer for ZImport-tools.

Stateless: no Flask session, no app secret. Every request re-validates
the Zimbra ZM_AUTH_TOKEN cookie via zimbra_session (which is LRU-cached
for 5 minutes to protect Zimbra QPS during chunked uploads). The validated
Identity is attached to flask.g for the request lifetime.

CSRF defense: state-changing endpoints require the X-Zimport-CSRF custom
header — browsers refuse to set X-* headers on cross-origin form posts
without a successful CORS preflight, which our endpoints never grant.
"""

import functools
import json
import os
import re
import shutil

from flask import Flask, g, jsonify, request, send_from_directory

from zimport_tools import (__version__, uploads, zimbra_auth,
                           zimbra_folders, zimbra_search, zimbra_session)
from zimport_tools.store import TaskStore
from zimport_tools.zimbra_auth import AuthError

_STATIC = os.path.join(os.path.dirname(__file__), "static")
_CSRF_HEADER = "X-Zimport-CSRF"
_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _valid_upload_id(upload_id):
    return bool(upload_id) and bool(_UPLOAD_ID_RE.match(upload_id))


def _safe_folder(folder):
    """Reject folder names that could rewrite the Zimbra REST URL or escape
    the target mailbox tree. Allows arbitrary text otherwise (Chinese,
    spaces, etc.) — zimbra_inject percent-encodes the final value."""
    if not folder or len(folder) > 512:
        return False
    if any(ord(c) < 0x20 for c in folder):  # control chars incl. NUL/CR/LF
        return False
    if any(c in folder for c in "?#%"):
        return False
    for seg in folder.split("/"):
        if seg in ("..", "."):
            return False
    return True


_MAX_INDEX = 10000


def _bounded_int(raw):
    """Parse an int and require 0 <= n < _MAX_INDEX. Returns None if invalid."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < 0 or n >= _MAX_INDEX:
        return None
    return n


def _queue_limit_for(store, cfg):
    """Return the remaining queue capacity (exposed module-level for tests)."""
    return cfg.queue_limit - store.count_active()


def create_app(cfg):
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = None
    store = TaskStore(cfg.db_path)
    os.makedirs(cfg.temp_root, exist_ok=True)

    # CSRF defense: require a custom X-* header that cross-origin forms cannot
    # set (browsers refuse X-* headers without a successful CORS preflight,
    # which our endpoints never grant).
    def _csrf_check():
        if request.method not in _STATE_CHANGING:
            return None
        if request.headers.get(_CSRF_HEADER) != "1":
            return jsonify({"error": "非法请求来源"}), 403
        return None

    def login_required(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            token = request.cookies.get("ZM_AUTH_TOKEN")
            if not token:
                return jsonify({"error": "未登录"}), 401
            try:
                ident = zimbra_session.validate(cfg, token)
            except zimbra_session.ZimbraUnreachable:
                return jsonify({"error": "Zimbra 暂不可达"}), 503
            except AuthError:
                return jsonify({"error": "未登录"}), 401
            g.account = ident.account
            g.is_admin = ident.is_admin
            csrf = _csrf_check()
            if csrf is not None:
                return csrf
            return fn(*a, **kw)
        return wrapper

    # --- static pages --------------------------------------------------

    @app.route("/")
    def index():
        return send_from_directory(_STATIC, "index.html")

    @app.route("/static/<path:name>")
    def static_files(name):
        return send_from_directory(_STATIC, name)

    # --- identity / version -------------------------------------------

    @app.route("/api/version")
    def version():
        return jsonify({"version": __version__})

    @app.route("/api/me")
    @login_required
    def me():
        return jsonify({"account": g.account,
                        "is_admin": g.is_admin})

    # --- tasks ---------------------------------------------------------

    @app.route("/api/tasks")
    @login_required
    def list_tasks():
        return jsonify(store.list_tasks(g.account))

    @app.route("/api/tasks/<task_id>")
    @login_required
    def get_task(task_id):
        task = store.get_task(task_id)
        if task is None or task["requester"] != g.account:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(task)

    @app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
    @login_required
    def cancel_task(task_id):
        task = store.get_task(task_id)
        if task is None:
            return jsonify({"error": "任务不存在"}), 404
        if task["requester"] != g.account and not g.is_admin:
            return jsonify({"error": "无权取消此任务"}), 403
        # tgz 模式 Zimbra 内部一次性处理,worker 没机会在中间感知 cancel —
        # 拒绝避免任务永远卡在 cancelling。queued 阶段仍可取消(worker 还
        # 没开跑),所以只对 running 的 tgz 拒。
        if (task["status"] == "running"
                and task.get("kind") == "zimbra-export"):
            return jsonify({
                "error": "tgz 任务由 Zimbra 内部一次性处理,运行中无法取消"
            }), 400
        new_status = store.request_cancel(task_id)
        if new_status is None:
            return jsonify({"error": "仅排队/运行中的任务可取消"}), 400
        return jsonify({"status": new_status})

    @app.route("/api/tasks/<task_id>/delete", methods=["POST"])
    @login_required
    def delete_task(task_id):
        task = store.get_task(task_id)
        if task is None:
            return jsonify({"error": "任务不存在"}), 404
        if task["requester"] != g.account and not g.is_admin:
            return jsonify({"error": "无权删除此任务"}), 403
        # Running tasks must be cancelled first (worker holds open file
        # handles in temp_dir; deleting under its feet would leave half-state).
        if task["status"] in ("queued", "running", "cancelling"):
            return jsonify({
                "error": "请先取消任务再删除(正在运行的任务不能直接删)"
            }), 400
        temp_dir = store.delete_task(task_id)
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"ok": True})

    @app.route("/api/tasks/<task_id>/retry", methods=["POST"])
    @login_required
    def retry_task(task_id):
        task = store.get_task(task_id)
        if task is None:
            return jsonify({"error": "任务不存在"}), 404
        if (task["requester"] != g.account
                and not g.is_admin):
            return jsonify({"error": "无权重试此任务"}), 403
        if task["status"] not in ("failed", "interrupted", "cancelled"):
            return jsonify({"error": "仅失败/中断/取消的任务能重试"}), 400
        if not os.path.isdir(task["temp_dir"]):
            return jsonify({"error": "任务文件已被清理,无法重试"}), 410
        if _queue_limit_for(store, cfg) <= 0:
            return jsonify({"error": "任务队列已满,请稍后再试"}), 429

        body = request.get_json(force=True, silent=True) or {}
        only_failed = bool(body.get("only_failed"))
        keep_files = None
        if only_failed and task.get("kind") != "zimbra-export":
            failures_raw = task.get("failures") or "[]"
            try:
                failures = json.loads(failures_raw)
            except (TypeError, ValueError):
                failures = []
            # Warnings (no_message_id / dedupe_check_failed) mean the message
            # was already injected — only true failures (network / quota /
            # permission / invalid / unknown / transient) need re-running.
            _NOT_FAILURES = ("duplicate", "no_message_id",
                             "dedupe_check_failed")
            keep_files = [
                f["name"] for f in failures
                if isinstance(f, dict)
                and f.get("name")
                and not any(str(f.get("code", "")).startswith(p)
                            for p in _NOT_FAILURES)
            ]
            # Guard against the original input/ being partially cleaned or
            # otherwise missing the files we want to re-run. TOCTOU-safe:
            # listdir() can still race with a concurrent cleanup, so wrap.
            input_dir = os.path.join(task["temp_dir"], "input")
            try:
                existing = set(os.listdir(input_dir))
            except OSError:
                existing = set()
            keep_files = [n for n in keep_files if n in existing]
            if not keep_files:
                return jsonify({"error": "没有真正失败的文件可重试(原任务的文件可能已被清理)"}), 400

        # Keep the original requester so the task stays visible in the
        # original author's /api/tasks list; an admin re-running a failed
        # job for someone else should not orphan that user from their own
        # task history.
        new_id = store.create_task(
            account=task["account"],
            requester=task["requester"],
            target_folder=task["target_folder"],
            temp_dir=task["temp_dir"],
            label=task.get("label"),
            keep_files=keep_files)
        return jsonify({"task_id": new_id})

    # --- folders / admin search ----------------------------------------

    @app.route("/api/folders")
    @login_required
    def folders():
        account = request.args.get("account") or g.account
        if account != g.account and not g.is_admin:
            return jsonify({"error": "无权查询此账户"}), 403
        try:
            tok = zimbra_auth.delegate_token(cfg, account)
            paths = zimbra_folders.list_folders(cfg, tok)
            return jsonify({"folders": paths})
        except (AuthError, zimbra_folders.FolderError) as exc:
            return jsonify({"error": str(exc)}), 502

    @app.route("/api/folders", methods=["POST"])
    @login_required
    def create_folder():
        body = request.get_json(force=True, silent=True) or {}
        path = body.get("path") or ""
        if not _safe_folder(path):
            return jsonify({"error": "无效的文件夹路径"}), 400
        account = body.get("account") or g.account
        if account != g.account and not g.is_admin:
            return jsonify({"error": "无权创建此账户的文件夹"}), 403
        try:
            tok = zimbra_auth.delegate_token(cfg, account)
            zimbra_folders.create_folder(cfg, tok, path)
            return jsonify({"ok": True, "path": path.strip("/")})
        except (AuthError, zimbra_folders.FolderError) as exc:
            return jsonify({"error": str(exc)}), 502

    @app.route("/api/admin/accounts/search")
    @login_required
    def admin_account_search():
        if not g.is_admin:
            return jsonify({"error": "仅管理员可用"}), 403
        q = request.args.get("q", "")
        try:
            results = zimbra_search.search_accounts(cfg, q)
            return jsonify({"accounts": results})
        except zimbra_search.SearchError as exc:
            return jsonify({"error": str(exc)}), 502

    @app.route("/api/admin/accounts")
    @login_required
    def admin_account_list():
        """列出所有账户(给目标账户下拉用)。仅管理员可用。"""
        if not g.is_admin:
            return jsonify({"error": "仅管理员可用"}), 403
        try:
            results = zimbra_search.list_accounts(cfg)
            return jsonify({"accounts": results})
        except zimbra_search.SearchError as exc:
            return jsonify({"error": str(exc)}), 502

    # --- uploads -------------------------------------------------------

    @app.route("/api/upload/init", methods=["POST"])
    @login_required
    def upload_init():
        upload_id = uploads.new_upload(cfg.temp_root)
        return jsonify({"upload_id": upload_id})

    @app.route("/api/upload/chunk", methods=["POST"])
    @login_required
    def upload_chunk():
        upload_id = request.form.get("upload_id")
        if not _valid_upload_id(upload_id):
            return jsonify({"error": "无效的 upload_id"}), 400
        file_index = _bounded_int(request.form.get("file_index"))
        chunk_index = _bounded_int(request.form.get("chunk_index"))
        blob_file = request.files.get("blob")
        if file_index is None or chunk_index is None or blob_file is None:
            return jsonify({"error": "缺少或无效的参数"}), 400
        uploads.save_chunk(cfg.temp_root, upload_id, file_index,
                           chunk_index, blob_file.read())
        return jsonify({"ok": True})

    @app.route("/api/upload/status")
    @login_required
    def upload_status():
        upload_id = request.args.get("upload_id")
        if not _valid_upload_id(upload_id):
            return jsonify({"error": "无效的 upload_id"}), 400
        file_index = _bounded_int(request.args.get("file_index"))
        total = _bounded_int(request.args.get("total_chunks"))
        if file_index is None or total is None:
            return jsonify({"error": "缺少或无效的参数"}), 400
        missing = uploads.missing_chunks(cfg.temp_root, upload_id,
                                         file_index, total)
        return jsonify({"missing": missing})

    # --- import (enqueue) ----------------------------------------------

    @app.route("/api/import", methods=["POST"])
    @login_required
    def start_import():
        if _queue_limit_for(store, cfg) <= 0:
            return jsonify({"error": "任务队列已满,请稍后再试"}), 429

        body = request.get_json(force=True, silent=True) or {}
        upload_id = body.get("upload_id")
        if not _valid_upload_id(upload_id):
            return jsonify({"error": "无效的 upload_id"}), 400
        files = body.get("files") or []
        folder = body.get("folder") or "Inbox"
        if not _safe_folder(folder):
            return jsonify({"error": "无效的目标文件夹名"}), 400
        label = body.get("label") or None
        if label:
            label = str(label)[:120]  # cap to keep DB reasonable

        # 越权防护:管理员可指定目标账户,普通用户强制为本人
        account = g.account
        if g.is_admin and body.get("account"):
            account = body["account"]

        for f in files:
            if not isinstance(f, dict):
                return jsonify({"error": "无效的 files 项"}), 400
            file_index = _bounded_int(f.get("index"))
            chunks = _bounded_int(f.get("chunks"))
            name = f.get("name")
            if file_index is None or chunks is None or not name:
                return jsonify({"error": "缺少或无效的文件元数据"}), 400
            uploads.merge_file(cfg.temp_root, upload_id,
                               file_index, chunks, name)

        input_path = uploads.input_dir(cfg.temp_root, upload_id)
        # The merged input/ may already be gone if the upload was abandoned
        # half-way and a concurrent purge swept it. Surface as 410 instead
        # of bubbling an OSError to a 500.
        try:
            used = sum(os.path.getsize(os.path.join(input_path, n))
                       for n in os.listdir(input_path))
        except OSError:
            return jsonify({"error": "上传文件已不可用,请重新上传"}), 410
        free = shutil.disk_usage(cfg.temp_root).free
        if used > cfg.max_task_bytes:
            return jsonify({"error": "本次数据超过单任务大小上限"}), 413
        if free < used:
            return jsonify({"error": "服务器临时磁盘空间不足"}), 507

        task_id = store.create_task(
            account=account, requester=g.account,
            target_folder=folder,
            temp_dir=uploads.upload_dir(cfg.temp_root, upload_id),
            label=label)
        return jsonify({"task_id": task_id})

    # --- CSRF unit-test endpoint ---------------------------------------
    # No-op endpoint for the CSRF unit tests. Registered unconditionally —
    # it is auth-protected and side-effect-free, so harmless in production.
    @app.route("/api/_test_csrf", methods=["POST"])
    @login_required
    def _test_csrf():
        return jsonify({"ok": True})

    return app
