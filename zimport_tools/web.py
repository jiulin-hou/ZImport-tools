"""Flask web layer for ZImport-tools.

Differs from the standalone ZImport's web.py in three ways:

  1. No /api/login or login form. Identity comes from the Zimbra
     ZM_AUTH_TOKEN cookie, validated through zimbra_session.

  2. CSRF protection on every state-changing request: requires a custom
     X-Zimport-CSRF header (which cross-site forms can't set) plus an
     Origin check that allows empty Origin (test client) and rejects any
     non-matching Origin.

  3. Account-switch protection: each request compares the cookie's token
     hash against the session's; if they differ, the session is cleared
     and rebuilt against the current cookie, so a different Zimbra account
     cannot inherit the previous one's session privileges.
"""

import functools
import os
import re
import shutil
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory, session

from zimport_tools import (archive, uploads, zimbra_auth, zimbra_folders,
                           zimbra_search, zimbra_session)
from zimport_tools.store import TaskStore
from zimport_tools.zimbra_auth import AuthError

_STATIC = os.path.join(os.path.dirname(__file__), "static")
_CSRF_HEADER = "X-Zimport-CSRF"
_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _valid_upload_id(upload_id):
    return bool(upload_id) and bool(_UPLOAD_ID_RE.match(upload_id))


def _queue_limit_for(store, cfg):
    """Return the remaining queue capacity (exposed module-level for tests)."""
    return cfg.queue_limit - store.count_active()


def _origin_from_url(url):
    if not url:
        return None
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return None
    return "%s://%s" % (p.scheme, p.netloc)


def create_app(cfg):
    app = Flask(__name__, static_folder=None)
    app.secret_key = cfg.secret_key
    app.config["MAX_CONTENT_LENGTH"] = None
    app.config.update(
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
    )
    store = TaskStore(cfg.db_path)
    os.makedirs(cfg.temp_root, exist_ok=True)

    expected_origin = _origin_from_url(cfg.rest_base)

    def _csrf_check():
        if request.method not in _STATE_CHANGING:
            return None
        if request.headers.get(_CSRF_HEADER) != "1":
            return jsonify({"error": "非法请求来源"}), 403
        origin = request.headers.get("Origin")
        if origin and expected_origin and origin != expected_origin:
            return jsonify({"error": "非法请求来源"}), 403
        return None

    def _auth_via_cookie():
        """Return ('zimbra_unreachable', None) | (Identity, token) | None."""
        token = request.cookies.get("ZM_AUTH_TOKEN")
        if not token:
            return None
        try:
            ident = zimbra_session.validate(cfg, token)
        except zimbra_session.ZimbraUnreachable:
            return ("zimbra_unreachable", None)
        except AuthError:
            return None
        return ident, token

    def login_required(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            cookie_token = request.cookies.get("ZM_AUTH_TOKEN")
            current_hash = (zimbra_session.token_hash(cookie_token)
                            if cookie_token else None)
            if "account" in session and session.get("token_hash") != current_hash:
                session.clear()
            if "account" not in session:
                result = _auth_via_cookie()
                if result is None:
                    return jsonify({"error": "未登录"}), 401
                if result == ("zimbra_unreachable", None):
                    return jsonify({"error": "Zimbra 暂不可达"}), 503
                ident, token = result
                session["account"] = ident.account
                session["is_admin"] = ident.is_admin
                session["token_hash"] = zimbra_session.token_hash(token)
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

    # --- identity ------------------------------------------------------

    @app.route("/api/me")
    @login_required
    def me():
        return jsonify({"account": session["account"],
                        "is_admin": session.get("is_admin", False)})

    # --- tasks ---------------------------------------------------------

    @app.route("/api/tasks")
    @login_required
    def list_tasks():
        return jsonify(store.list_tasks(session["account"]))

    @app.route("/api/tasks/<task_id>")
    @login_required
    def get_task(task_id):
        task = store.get_task(task_id)
        if task is None or task["requester"] != session["account"]:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(task)

    @app.route("/api/tasks/<task_id>/retry", methods=["POST"])
    @login_required
    def retry_task(task_id):
        task = store.get_task(task_id)
        if task is None:
            return jsonify({"error": "任务不存在"}), 404
        if (task["requester"] != session["account"]
                and not session.get("is_admin")):
            return jsonify({"error": "无权重试此任务"}), 403
        if task["status"] not in ("failed", "interrupted"):
            return jsonify({"error": "仅失败/中断的任务能重试"}), 400
        if not os.path.isdir(task["temp_dir"]):
            return jsonify({"error": "任务文件已被清理,无法重试"}), 410
        if _queue_limit_for(store, cfg) <= 0:
            return jsonify({"error": "任务队列已满,请稍后再试"}), 429
        new_id = store.create_task(
            account=task["account"],
            requester=session["account"],
            target_folder=task["target_folder"],
            temp_dir=task["temp_dir"])
        return jsonify({"task_id": new_id})

    # --- folders / admin search ----------------------------------------

    @app.route("/api/folders")
    @login_required
    def folders():
        account = request.args.get("account") or session["account"]
        if account != session["account"] and not session.get("is_admin"):
            return jsonify({"error": "无权查询此账户"}), 403
        try:
            tok = zimbra_auth.delegate_token(cfg, account)
            paths = zimbra_folders.list_folders(cfg, tok)
            return jsonify({"folders": paths})
        except (AuthError, zimbra_folders.FolderError) as exc:
            return jsonify({"error": str(exc)}), 502

    @app.route("/api/admin/accounts/search")
    @login_required
    def admin_account_search():
        if not session.get("is_admin"):
            return jsonify({"error": "仅管理员可用"}), 403
        q = request.args.get("q", "")
        try:
            results = zimbra_search.search_accounts(cfg, q)
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
        upload_id = request.form["upload_id"]
        if not _valid_upload_id(upload_id):
            return jsonify({"error": "无效的 upload_id"}), 400
        file_index = int(request.form["file_index"])
        chunk_index = int(request.form["chunk_index"])
        blob = request.files["blob"].read()
        uploads.save_chunk(cfg.temp_root, upload_id, file_index,
                           chunk_index, blob)
        return jsonify({"ok": True})

    @app.route("/api/upload/status")
    @login_required
    def upload_status():
        upload_id = request.args["upload_id"]
        if not _valid_upload_id(upload_id):
            return jsonify({"error": "无效的 upload_id"}), 400
        file_index = int(request.args["file_index"])
        total = int(request.args["total_chunks"])
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
        upload_id = body["upload_id"]
        if not _valid_upload_id(upload_id):
            return jsonify({"error": "无效的 upload_id"}), 400
        files = body.get("files", [])
        folder = body.get("folder") or "Inbox"

        # 越权防护:管理员可指定目标账户,普通用户强制为本人
        account = session["account"]
        if session.get("is_admin") and body.get("account"):
            account = body["account"]

        for f in files:
            uploads.merge_file(cfg.temp_root, upload_id, int(f["index"]),
                               int(f["chunks"]), f["name"])

        input_path = uploads.input_dir(cfg.temp_root, upload_id)
        used = sum(os.path.getsize(os.path.join(input_path, n))
                   for n in os.listdir(input_path))
        free = shutil.disk_usage(cfg.temp_root).free
        if used > cfg.max_task_bytes:
            return jsonify({"error": "本次数据超过单任务大小上限"}), 413
        if free < used:
            return jsonify({"error": "服务器临时磁盘空间不足"}), 507

        task_id = store.create_task(
            account=account, requester=session["account"],
            target_folder=folder,
            temp_dir=uploads.upload_dir(cfg.temp_root, upload_id))
        return jsonify({"task_id": task_id})

    # --- CSRF unit-test endpoint ---------------------------------------
    # No-op endpoint for the CSRF unit tests. Registered unconditionally —
    # it is auth-protected and side-effect-free, so harmless in production.
    @app.route("/api/_test_csrf", methods=["POST"])
    @login_required
    def _test_csrf():
        return jsonify({"ok": True})

    return app
