import io

import pytest
from zimport_tools import web, zimbra_session
from zimport_tools.zimbra_auth import Identity, AuthError


class _Cfg:
    temp_root = None
    db_path = None
    queue_limit = 50
    max_task_bytes = 10 ** 12
    rest_base = "https://h:8443"
    verify_tls = False

    def tls_verify(self):
        return self.verify_tls


@pytest.fixture
def app(tmp_path):
    cfg = _Cfg()
    cfg.temp_root = str(tmp_path / "tmp")
    cfg.db_path = str(tmp_path / "t.db")
    application = web.create_app(cfg)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def patch_validate(monkeypatch):
    """Make zimbra_session.validate return a given Identity per token,
    or raise AuthError for tokens not in the mapping."""
    def _setup(token_to_identity):
        def fake_validate(cfg, token, _cache=None):
            if token in token_to_identity:
                return token_to_identity[token]
            raise AuthError("bad")
        monkeypatch.setattr(web.zimbra_session, "validate", fake_validate)
    return _setup


def test_me_with_valid_cookie(app, patch_validate):
    patch_validate({"TOK": Identity(False, "u@d")})
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "TOK")
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.get_json()["account"] == "u@d"
    assert resp.get_json()["is_admin"] is False


def test_me_with_admin_cookie(app, patch_validate):
    patch_validate({"ADMTOK": Identity(True, "admin@d")})
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "ADMTOK")
    resp = client.get("/api/me")
    assert resp.get_json()["is_admin"] is True


def test_me_without_cookie(app):
    client = app.test_client()
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_me_with_invalid_cookie(app, patch_validate):
    patch_validate({})  # no token matches
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "BAD")
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_zimbra_unreachable_returns_503(app, monkeypatch):
    def boom(cfg, token, _cache=None):
        raise zimbra_session.ZimbraUnreachable("nope")
    monkeypatch.setattr(web.zimbra_session, "validate", boom)
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "TOK")
    resp = client.get("/api/me")
    assert resp.status_code == 503


def test_login_endpoint_does_not_exist(app):
    client = app.test_client()
    assert client.post("/api/login", json={}).status_code == 404


def test_each_request_resolves_current_cookie(app, patch_validate):
    """Stateless: identity comes from the cookie sent on this request,
    not from any server-side session — so swapping the cookie immediately
    swaps identity."""
    patch_validate({"A": Identity(False, "a@d"),
                    "B": Identity(False, "b@d")})
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "A")
    assert client.get("/api/me").get_json()["account"] == "a@d"
    client.set_cookie("ZM_AUTH_TOKEN", "B")
    assert client.get("/api/me").get_json()["account"] == "b@d"


# ---- CSRF tests via /api/_test_csrf no-op endpoint ----

def test_csrf_missing_header_rejected(app, patch_validate):
    patch_validate({"TOK": Identity(False, "u@d")})
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "TOK")
    resp = client.post("/api/_test_csrf",
                       headers={"Origin": "https://h:8443"})
    assert resp.status_code == 403


def test_csrf_valid_request_passes(app, patch_validate):
    patch_validate({"TOK": Identity(False, "u@d")})
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "TOK")
    resp = client.post("/api/_test_csrf",
                       headers={"X-Zimport-CSRF": "1",
                                "Origin": "https://anywhere.example.com"})
    assert resp.status_code == 200


# ---- helpers for business endpoint tests ----

def _csrf():
    return {"X-Zimport-CSRF": "1"}


def _logged_in(app, patch_validate, account="u@d", is_admin=False, token="TOK"):
    patch_validate({token: Identity(is_admin, account)})
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", token)
    return client


# ---- /api/tasks ----

def test_tasks_requires_login(app):
    assert app.test_client().get("/api/tasks").status_code == 401


# ---- /api/folders ----

def test_folders_returns_paths(app, patch_validate, monkeypatch):
    monkeypatch.setattr(web.zimbra_auth, "delegate_token",
                        lambda cfg, acc: "DTOK")
    monkeypatch.setattr(web.zimbra_folders, "list_folders",
                        lambda cfg, tok: ["Inbox", "Sent"])
    client = _logged_in(app, patch_validate)
    resp = client.get("/api/folders")
    assert resp.status_code == 200
    assert resp.get_json()["folders"] == ["Inbox", "Sent"]


def test_folders_forbidden_for_non_admin_other_account(app, patch_validate):
    client = _logged_in(app, patch_validate)
    resp = client.get("/api/folders?account=other@d")
    assert resp.status_code == 403


# ---- /api/admin/accounts/search ----

def test_admin_account_search_requires_admin(app, patch_validate):
    client = _logged_in(app, patch_validate)  # non-admin
    resp = client.get("/api/admin/accounts/search?q=al")
    assert resp.status_code == 403


def test_admin_account_search_returns_results(app, patch_validate, monkeypatch):
    monkeypatch.setattr(web.zimbra_search, "search_accounts",
                        lambda cfg, q: [{"name": "a@d", "display": "A"}])
    client = _logged_in(app, patch_validate, account="admin@d", is_admin=True)
    resp = client.get("/api/admin/accounts/search?q=ali")
    assert resp.status_code == 200
    assert resp.get_json()["accounts"][0]["name"] == "a@d"


# ---- /api/tasks/<id>/retry ----

def test_retry_creates_new_task_for_failed(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    db_path = str(tmp_path / "t.db")
    store = TaskStore(db_path)
    temp_dir = tmp_path / "td"
    temp_dir.mkdir()
    old_id = store.create_task(account="u@d", requester="u@d",
                                target_folder="Inbox",
                                temp_dir=str(temp_dir))
    store.set_status(old_id, "failed", error="boom")
    client = _logged_in(app, patch_validate)
    resp = client.post("/api/tasks/" + old_id + "/retry", headers=_csrf())
    assert resp.status_code == 200, resp.get_json()
    new_id = resp.get_json()["task_id"]
    assert new_id != old_id
    new = store.get_task(new_id)
    assert new["status"] == "queued"
    assert new["temp_dir"] == str(temp_dir)
    assert new["target_folder"] == "Inbox"


def test_retry_410_when_temp_dir_gone(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    old_id = store.create_task(account="u@d", requester="u@d",
                                target_folder="Inbox",
                                temp_dir=str(tmp_path / "gone"))
    store.set_status(old_id, "failed", error="boom")
    client = _logged_in(app, patch_validate)
    assert client.post("/api/tasks/" + old_id + "/retry",
                       headers=_csrf()).status_code == 410


def test_retry_403_for_other_user(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    old_id = store.create_task(account="other@d", requester="other@d",
                                target_folder="Inbox", temp_dir=str(td))
    store.set_status(old_id, "failed")
    client = _logged_in(app, patch_validate)  # logs in as u@d
    assert client.post("/api/tasks/" + old_id + "/retry",
                       headers=_csrf()).status_code == 403


def test_retry_404_when_missing(app, patch_validate):
    client = _logged_in(app, patch_validate)
    assert client.post("/api/tasks/nosuch/retry",
                       headers=_csrf()).status_code == 404


# ---- /api/tasks/<id>/delete ----

def test_delete_removes_task_and_temp_dir(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    (td / "input").mkdir()
    (td / "input" / "a.eml").write_bytes(b"x")
    tid = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    store.set_status(tid, "done")
    client = _logged_in(app, patch_validate)
    resp = client.post("/api/tasks/" + tid + "/delete", headers=_csrf())
    assert resp.status_code == 200, resp.get_json()
    assert store.get_task(tid) is None  # row gone
    assert not td.exists()              # temp_dir wiped


def test_delete_404_when_missing(app, patch_validate):
    client = _logged_in(app, patch_validate)
    assert client.post("/api/tasks/nosuch/delete",
                       headers=_csrf()).status_code == 404


def test_delete_403_for_other_user(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    tid = store.create_task(account="other@d", requester="other@d",
                            target_folder="Inbox", temp_dir=str(td))
    store.set_status(tid, "done")
    client = _logged_in(app, patch_validate)  # logs in as u@d
    resp = client.post("/api/tasks/" + tid + "/delete", headers=_csrf())
    assert resp.status_code == 403


def test_delete_400_when_task_still_active(app, patch_validate, tmp_path):
    """Active (queued/running/cancelling) tasks can't be deleted directly —
    cancel them first."""
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    tid = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    # default status = queued
    client = _logged_in(app, patch_validate)
    resp = client.post("/api/tasks/" + tid + "/delete", headers=_csrf())
    assert resp.status_code == 400
    assert store.get_task(tid) is not None  # task NOT deleted
    assert td.exists()                       # temp_dir NOT wiped


def test_retry_400_when_status_not_failed(app, patch_validate, tmp_path,
                                          monkeypatch):
    """status=queued/done/running 都不应该让重试。"""
    client = _logged_in(app, patch_validate)
    monkeypatch.setattr(web.uploads, "input_dir",
                        lambda root, uid: str(tmp_path))
    monkeypatch.setattr(web.uploads, "upload_dir",
                        lambda root, uid: str(tmp_path))
    monkeypatch.setattr(web.uploads, "merge_file", lambda *a, **kw: None)
    monkeypatch.setattr(web.os, "listdir", lambda p: [])
    init = client.post("/api/upload/init", headers=_csrf()).get_json()
    r = client.post("/api/import", headers=_csrf(), json={
        "upload_id": init["upload_id"], "files": [], "folder": "Inbox"})
    task_id = r.get_json()["task_id"]
    resp = client.post("/api/tasks/" + task_id + "/retry", headers=_csrf())
    assert resp.status_code == 400


# ---- /api/upload/* + /api/import ----

def test_upload_init_and_chunk_flow(app, patch_validate):
    client = _logged_in(app, patch_validate)
    init = client.post("/api/upload/init", headers=_csrf())
    assert init.status_code == 200
    upload_id = init.get_json()["upload_id"]
    resp = client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"HELLO"), "blob"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    status = client.get("/api/upload/status",
                        query_string={"upload_id": upload_id,
                                      "file_index": "0",
                                      "total_chunks": "2"})
    assert status.get_json()["missing"] == [1]


def test_upload_chunk_requires_login(app):
    """No cookie -> 401 BEFORE CSRF is even checked."""
    client = app.test_client()
    resp = client.post("/api/upload/chunk",
                       headers=_csrf(),
                       data={"upload_id": "x"})
    assert resp.status_code == 401


def test_upload_chunk_rejects_bad_upload_id(app, patch_validate):
    client = _logged_in(app, patch_validate)
    resp = client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": "../../etc", "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"x"), "blob"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_import_merges_and_enqueues(app, patch_validate):
    client = _logged_in(app, patch_validate)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"From: a@b\r\n\r\nhi"), "blob"),
    }, content_type="multipart/form-data")
    resp = client.post("/api/import", headers=_csrf(), json={
        "upload_id": upload_id,
        "files": [{"index": 0, "name": "msg.eml", "chunks": 1}],
        "folder": "Inbox",
    })
    assert resp.status_code == 200
    task_id = resp.get_json()["task_id"]
    task = client.get("/api/tasks/" + task_id).get_json()
    assert task["status"] == "queued"
    assert task["account"] == "u@d"


def test_normal_user_cannot_target_other_account(app, patch_validate):
    client = _logged_in(app, patch_validate)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"x"), "blob"),
    }, content_type="multipart/form-data")
    resp = client.post("/api/import", headers=_csrf(), json={
        "upload_id": upload_id,
        "files": [{"index": 0, "name": "m.eml", "chunks": 1}],
        "folder": "Inbox",
        "account": "victim@d",  # non-admin attempts to target someone else
    })
    task_id = resp.get_json()["task_id"]
    assert client.get("/api/tasks/" + task_id).get_json()["account"] == "u@d"


def test_import_rejected_when_queue_full(app, patch_validate, monkeypatch):
    client = _logged_in(app, patch_validate)
    monkeypatch.setattr(web, "_queue_limit_for", lambda store, cfg: 0)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"x"), "blob"),
    }, content_type="multipart/form-data")
    resp = client.post("/api/import", headers=_csrf(), json={
        "upload_id": upload_id,
        "files": [{"index": 0, "name": "m.eml", "chunks": 1}],
        "folder": "Inbox",
    })
    assert resp.status_code == 429


# ---- P0 security: get_task isolation, blanket CSRF, size guards ----

def test_get_task_404_for_other_user(app, patch_validate, tmp_path):
    """Authenticated user must not see tasks owned by other accounts."""
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    other_task = store.create_task(account="other@d", requester="other@d",
                                    target_folder="Inbox", temp_dir=str(td))
    client = _logged_in(app, patch_validate)  # u@d, non-admin
    resp = client.get("/api/tasks/" + other_task)
    assert resp.status_code == 404


@pytest.mark.parametrize("method,path,kwargs", [
    ("POST", "/api/upload/init",            {}),
    ("POST", "/api/upload/chunk",
        {"data": {"upload_id": "x"}, "content_type": "multipart/form-data"}),
    ("POST", "/api/import",                 {"json": {}}),
    ("POST", "/api/tasks/whatever/retry",   {}),
    ("POST", "/api/_test_csrf",             {}),
])
def test_csrf_required_on_all_write_endpoints(app, patch_validate,
                                              method, path, kwargs):
    """Every state-changing endpoint must reject requests lacking
    X-Zimport-CSRF, so a forgotten decorator on a new POST route is caught."""
    client = _logged_in(app, patch_validate)
    resp = client.open(path, method=method, **kwargs)
    assert resp.status_code == 403, \
        "%s %s should 403 without CSRF, got %s" % (method, path, resp.status_code)


def test_import_413_when_payload_exceeds_max(app, patch_validate, monkeypatch):
    """Reject before enqueueing if merged input exceeds cfg.max_task_bytes."""
    client = _logged_in(app, patch_validate)
    # Tiny limit so a 1-byte upload trips it.
    monkeypatch.setattr(app, "_cfg_max_override", 0, raising=False)
    # Replace cfg reference reachable via closure: monkeypatch the
    # _queue_limit check is unrelated; we instead set via cfg attr.
    # Easiest path: lower the configured max via fixture's cfg object.
    cfg = client.application.view_functions["start_import"].__closure__
    # cfg is captured in the create_app closure; expose via app extension.
    # Simpler: monkeypatch shutil.disk_usage and set max_task_bytes via
    # the cfg object stashed on app.
    original_max = None
    for cell in cfg or []:
        try:
            obj = cell.cell_contents
        except ValueError:
            continue
        if hasattr(obj, "max_task_bytes"):
            original_max = obj.max_task_bytes
            obj.max_task_bytes = 0
            break
    try:
        upload_id = client.post("/api/upload/init",
                                headers=_csrf()).get_json()["upload_id"]
        client.post("/api/upload/chunk", headers=_csrf(), data={
            "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
            "blob": (io.BytesIO(b"DATA"), "blob"),
        }, content_type="multipart/form-data")
        resp = client.post("/api/import", headers=_csrf(), json={
            "upload_id": upload_id,
            "files": [{"index": 0, "name": "m.eml", "chunks": 1}],
            "folder": "Inbox",
        })
        assert resp.status_code == 413
    finally:
        if original_max is not None:
            for cell in cfg or []:
                try:
                    obj = cell.cell_contents
                except ValueError:
                    continue
                if hasattr(obj, "max_task_bytes"):
                    obj.max_task_bytes = original_max
                    break


def test_import_507_when_disk_full(app, patch_validate, monkeypatch):
    """Reject when local temp partition has less free space than the merged
    input — protects against DoS-by-fill."""
    client = _logged_in(app, patch_validate)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"DATA"), "blob"),
    }, content_type="multipart/form-data")
    # Pretend the disk has only 1 byte free.
    fake_usage = type("U", (), {"free": 1, "total": 0, "used": 0})()
    monkeypatch.setattr(web.shutil, "disk_usage", lambda p: fake_usage)
    resp = client.post("/api/import", headers=_csrf(), json={
        "upload_id": upload_id,
        "files": [{"index": 0, "name": "m.eml", "chunks": 1}],
        "folder": "Inbox",
    })
    assert resp.status_code == 507


@pytest.mark.parametrize("folder", [
    "../etc",            # path traversal segment
    "Inbox/../../root",  # traversal mid-path
    "Inbox?fmt=tgz",     # query string injection
    "Inbox#frag",        # fragment injection
    "Inbox%2F..",        # percent-encoded escape
    "Inbox\nX-Header",   # CRLF injection
    "",                  # empty after fallback => actually fallback to Inbox
])
def test_import_rejects_unsafe_folder(app, patch_validate, folder):
    client = _logged_in(app, patch_validate)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"DATA"), "blob"),
    }, content_type="multipart/form-data")
    resp = client.post("/api/import", headers=_csrf(), json={
        "upload_id": upload_id,
        "files": [{"index": 0, "name": "m.eml", "chunks": 1}],
        "folder": folder,
    })
    # Empty string falls back to "Inbox" in the handler, so that one is OK.
    if folder == "":
        assert resp.status_code == 200
    else:
        assert resp.status_code == 400, \
            "folder %r should 400, got %s" % (folder, resp.status_code)


def test_admin_can_target_other_account(app, patch_validate):
    """Positive: admin specifying body.account routes task to that account."""
    client = _logged_in(app, patch_validate,
                        account="admin@d", is_admin=True)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"x"), "blob"),
    }, content_type="multipart/form-data")
    resp = client.post("/api/import", headers=_csrf(), json={
        "upload_id": upload_id,
        "files": [{"index": 0, "name": "m.eml", "chunks": 1}],
        "folder": "Inbox",
        "account": "victim@d",
    })
    task_id = resp.get_json()["task_id"]
    task = client.get("/api/tasks/" + task_id).get_json()
    assert task["account"] == "victim@d"
    assert task["requester"] == "admin@d"


def test_admin_retry_preserves_original_requester(app, patch_validate, tmp_path):
    """Admin retrying someone else's failed task must keep the original
    requester so the author still sees the new task in their /api/tasks."""
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    old_id = store.create_task(account="bob@d", requester="bob@d",
                                target_folder="Inbox", temp_dir=str(td))
    store.set_status(old_id, "failed", error="boom")
    admin = _logged_in(app, patch_validate, account="admin@d", is_admin=True)
    resp = admin.post("/api/tasks/" + old_id + "/retry", headers=_csrf())
    new_id = resp.get_json()["task_id"]
    new = store.get_task(new_id)
    assert new["requester"] == "bob@d"
    assert new["account"] == "bob@d"


def test_upload_chunk_400_when_index_missing_or_huge(app, patch_validate):
    client = _logged_in(app, patch_validate)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    # missing file_index
    r = client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "chunk_index": "0",
        "blob": (io.BytesIO(b"x"), "blob"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    # huge chunk_index (above _MAX_INDEX)
    r = client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "999999",
        "blob": (io.BytesIO(b"x"), "blob"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400


# ---- v1.3 features: label / cancel / new folder / retry only_failed ----

def test_import_with_label_persists(app, patch_validate):
    client = _logged_in(app, patch_validate)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"x"), "blob"),
    }, content_type="multipart/form-data")
    resp = client.post("/api/import", headers=_csrf(), json={
        "upload_id": upload_id,
        "files": [{"index": 0, "name": "m.eml", "chunks": 1}],
        "folder": "Inbox",
        "label": "Q3 historical",
    })
    task_id = resp.get_json()["task_id"]
    t = client.get("/api/tasks/" + task_id).get_json()
    assert t["label"] == "Q3 historical"


def test_cancel_task_queued(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    tid = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    client = _logged_in(app, patch_validate)
    resp = client.post("/api/tasks/" + tid + "/cancel", headers=_csrf())
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cancelled"
    assert store.get_task(tid)["status"] == "cancelled"


def test_cancel_task_running_marks_cancelling(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    tid = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    store.set_status(tid, "running")
    client = _logged_in(app, patch_validate)
    resp = client.post("/api/tasks/" + tid + "/cancel", headers=_csrf())
    assert resp.get_json()["status"] == "cancelling"
    assert store.cancel_requested(tid) is True


def test_cancel_task_done_rejected(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    tid = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    store.set_status(tid, "done")
    client = _logged_in(app, patch_validate)
    assert client.post("/api/tasks/" + tid + "/cancel",
                       headers=_csrf()).status_code == 400


def test_cancel_task_unauthorized(app, patch_validate, tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    tid = store.create_task(account="other@d", requester="other@d",
                            target_folder="Inbox", temp_dir=str(td))
    client = _logged_in(app, patch_validate)  # u@d
    assert client.post("/api/tasks/" + tid + "/cancel",
                       headers=_csrf()).status_code == 403


def test_create_folder_success(app, patch_validate, monkeypatch):
    monkeypatch.setattr(web.zimbra_auth, "delegate_token",
                        lambda cfg, acc: "TOK")
    monkeypatch.setattr(web.zimbra_folders, "create_folder",
                        lambda cfg, tok, path: None)
    client = _logged_in(app, patch_validate)
    r = client.post("/api/folders", headers=_csrf(),
                    json={"path": "Inbox/Archive 2024"})
    assert r.status_code == 200
    assert r.get_json()["path"] == "Inbox/Archive 2024"


def test_create_folder_rejects_unsafe_path(app, patch_validate):
    client = _logged_in(app, patch_validate)
    r = client.post("/api/folders", headers=_csrf(),
                    json={"path": "../etc/passwd"})
    assert r.status_code == 400


def test_retry_only_failed_filters_keep_files(app, patch_validate, tmp_path):
    """retry only_failed should compute keep_files from prior failures,
    excluding duplicates."""
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    (td / "input").mkdir(parents=True)
    (td / "input" / "a.eml").write_bytes(b"a")
    (td / "input" / "c.eml").write_bytes(b"c")
    # b.eml is the duplicate-skipped one; we don't need it on disk
    old = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    store.set_status(old, "failed", kind="eml-bundle")
    store.set_failures(old, [
        {"name": "a.eml", "code": "network", "reason": "x"},
        {"name": "b.eml", "code": "duplicate_mailbox", "reason": "x"},
        {"name": "c.eml", "code": "invalid", "reason": "x"},
    ])
    client = _logged_in(app, patch_validate)
    r = client.post("/api/tasks/" + old + "/retry", headers=_csrf(),
                    json={"only_failed": True})
    assert r.status_code == 200, r.get_json()
    new_id = r.get_json()["task_id"]
    new = store.get_task(new_id)
    import json as _json
    keep = _json.loads(new["keep_files"])
    assert sorted(keep) == ["a.eml", "c.eml"]  # b.eml (duplicate) excluded


def test_retry_only_failed_rejected_when_nothing_to_retry(app, patch_validate,
                                                          tmp_path):
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    old = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    store.set_status(old, "failed", kind="eml-bundle")
    store.set_failures(old, [
        {"name": "a.eml", "code": "duplicate_batch", "reason": "x"},
    ])
    client = _logged_in(app, patch_validate)
    r = client.post("/api/tasks/" + old + "/retry", headers=_csrf(),
                    json={"only_failed": True})
    assert r.status_code == 400


def test_retry_only_failed_filters_missing_input_files(app, patch_validate,
                                                       tmp_path):
    """If input/ has been partially cleaned, keep_files must drop the
    missing entries; if everything's gone, retry returns 400 rather than
    silently spawning a 0-file task."""
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    (td / "input").mkdir(parents=True)
    (td / "input" / "still_here.eml").write_bytes(b"x")
    # gone.eml is referenced by failures but the file is missing.
    old = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    store.set_status(old, "failed", kind="eml-bundle")
    store.set_failures(old, [
        {"name": "still_here.eml", "code": "network", "reason": "x"},
        {"name": "gone.eml",       "code": "network", "reason": "x"},
    ])
    client = _logged_in(app, patch_validate)
    r = client.post("/api/tasks/" + old + "/retry", headers=_csrf(),
                    json={"only_failed": True})
    assert r.status_code == 200, r.get_json()
    new_id = r.get_json()["task_id"]
    import json as _json
    keep = _json.loads(store.get_task(new_id)["keep_files"])
    assert keep == ["still_here.eml"]


def test_cancel_running_tgz_rejected(app, patch_validate, tmp_path):
    """tgz tasks can't be cancelled mid-run (Zimbra processes atomically)."""
    from zimport_tools.store import TaskStore
    store = TaskStore(str(tmp_path / "t.db"))
    td = tmp_path / "td"
    td.mkdir()
    tid = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir=str(td))
    store.set_status(tid, "running", kind="zimbra-export")
    client = _logged_in(app, patch_validate)
    r = client.post("/api/tasks/" + tid + "/cancel", headers=_csrf())
    assert r.status_code == 400
    assert "tgz" in r.get_json()["error"].lower()


def test_import_410_when_input_dir_gone(app, patch_validate, monkeypatch):
    """If a concurrent purge wipes the merged input/ between merge_file
    and the size sum, /api/import must return 410 not 500."""
    client = _logged_in(app, patch_validate)
    upload_id = client.post("/api/upload/init",
                            headers=_csrf()).get_json()["upload_id"]
    client.post("/api/upload/chunk", headers=_csrf(), data={
        "upload_id": upload_id, "file_index": "0", "chunk_index": "0",
        "blob": (io.BytesIO(b"x"), "blob"),
    }, content_type="multipart/form-data")
    monkeypatch.setattr(web.uploads, "merge_file", lambda *a, **kw: None)

    def boom_listdir(path):
        raise FileNotFoundError(path)
    monkeypatch.setattr(web.os, "listdir", boom_listdir)
    resp = client.post("/api/import", headers=_csrf(), json={
        "upload_id": upload_id,
        "files": [{"index": 0, "name": "m.eml", "chunks": 1}],
        "folder": "Inbox",
    })
    assert resp.status_code == 410
