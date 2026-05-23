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
