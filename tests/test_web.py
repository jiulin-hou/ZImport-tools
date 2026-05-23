import io

import pytest
from zimport_tools import web, zimbra_session
from zimport_tools.zimbra_auth import Identity, AuthError


class _Cfg:
    secret_key = "test-secret"
    temp_root = None
    db_path = None
    queue_limit = 50
    max_task_bytes = 10 ** 12
    chunk_size = 1024
    rest_base = "https://h:8443"
    verify_tls = False


@pytest.fixture
def app(tmp_path):
    cfg = _Cfg()
    cfg.temp_root = str(tmp_path / "tmp")
    cfg.db_path = str(tmp_path / "t.db")
    application = web.create_app(cfg)
    application.config["TESTING"] = True
    # SECURE cookies require https; relax for test client (http://localhost)
    application.config["SESSION_COOKIE_SECURE"] = False
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


def test_account_switch_rebuilds_session(app, patch_validate):
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


def test_csrf_bad_origin_rejected(app, patch_validate):
    patch_validate({"TOK": Identity(False, "u@d")})
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "TOK")
    resp = client.post("/api/_test_csrf",
                       headers={"X-Zimport-CSRF": "1",
                                "Origin": "https://evil.example.com"})
    assert resp.status_code == 403


def test_csrf_valid_request_passes(app, patch_validate):
    patch_validate({"TOK": Identity(False, "u@d")})
    client = app.test_client()
    client.set_cookie("ZM_AUTH_TOKEN", "TOK")
    resp = client.post("/api/_test_csrf",
                       headers={"X-Zimport-CSRF": "1",
                                "Origin": "https://h:8443"})
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
