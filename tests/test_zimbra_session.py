import pytest
from zimport_tools import zimbra_session
from zimport_tools.zimbra_auth import Identity


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Cfg:
    soap_url = "https://h:8443/service/soap"
    verify_tls = False


def _info_ok(account="u@d", is_admin="FALSE"):
    return {"Body": {"GetInfoResponse": {
        "name": account,
        "attrs": {"_attrs": {"zimbraIsAdminAccount": is_admin}},
    }}}


def _fault():
    return {"Body": {"Fault": {"Reason": {"Text": "auth failed"}}}}


def test_validate_valid_token_returns_identity(monkeypatch):
    cache = zimbra_session._Cache()
    monkeypatch.setattr(zimbra_session.requests, "post",
                        lambda url, **kw: _Resp(_info_ok("u@d", "FALSE")))
    ident = zimbra_session.validate(_Cfg, "TOK", _cache=cache)
    assert isinstance(ident, Identity)
    assert ident.account == "u@d"
    assert ident.is_admin is False


def test_validate_admin_token(monkeypatch):
    cache = zimbra_session._Cache()
    monkeypatch.setattr(zimbra_session.requests, "post",
                        lambda url, **kw: _Resp(_info_ok("admin@d", "TRUE")))
    ident = zimbra_session.validate(_Cfg, "ADMTOK", _cache=cache)
    assert ident.is_admin is True


def test_validate_invalid_token_raises(monkeypatch):
    from zimport_tools.zimbra_auth import AuthError
    cache = zimbra_session._Cache()
    monkeypatch.setattr(zimbra_session.requests, "post",
                        lambda url, **kw: _Resp(_fault()))
    with pytest.raises(AuthError):
        zimbra_session.validate(_Cfg, "BADTOK", _cache=cache)


def test_validate_zimbra_unreachable(monkeypatch):
    import requests
    cache = zimbra_session._Cache()

    def boom(url, **kw):
        raise requests.ConnectionError("nope")
    monkeypatch.setattr(zimbra_session.requests, "post", boom)
    with pytest.raises(zimbra_session.ZimbraUnreachable):
        zimbra_session.validate(_Cfg, "TOK", _cache=cache)


def test_validate_caches_positive(monkeypatch):
    cache = zimbra_session._Cache()
    calls = []

    def fake_post(url, **kw):
        calls.append(1)
        return _Resp(_info_ok())
    monkeypatch.setattr(zimbra_session.requests, "post", fake_post)
    zimbra_session.validate(_Cfg, "TOK", _cache=cache)
    zimbra_session.validate(_Cfg, "TOK", _cache=cache)
    zimbra_session.validate(_Cfg, "TOK", _cache=cache)
    assert len(calls) == 1, "positive cache should prevent re-validation"


def test_validate_caches_negative(monkeypatch):
    from zimport_tools.zimbra_auth import AuthError
    cache = zimbra_session._Cache()
    calls = []

    def fake_post(url, **kw):
        calls.append(1)
        return _Resp(_fault())
    monkeypatch.setattr(zimbra_session.requests, "post", fake_post)
    for _ in range(3):
        with pytest.raises(AuthError):
            zimbra_session.validate(_Cfg, "BAD", _cache=cache)
    assert len(calls) == 1, "negative cache should prevent re-validation"


def test_cache_ttl_expiry(monkeypatch):
    cache = zimbra_session._Cache()
    calls = []

    def fake_post(url, **kw):
        calls.append(1)
        return _Resp(_info_ok())
    monkeypatch.setattr(zimbra_session.requests, "post", fake_post)
    now = [1000.0]
    monkeypatch.setattr(zimbra_session, "_now", lambda: now[0])
    zimbra_session.validate(_Cfg, "TOK", _cache=cache)
    now[0] += zimbra_session.POSITIVE_TTL + 1
    zimbra_session.validate(_Cfg, "TOK", _cache=cache)
    assert len(calls) == 2, "expired positive cache entry should re-validate"


def test_cache_lru_eviction():
    cache = zimbra_session._Cache(capacity=2)
    cache.put_positive("A", Identity(False, "a@d"))
    cache.put_positive("B", Identity(False, "b@d"))
    cache.put_positive("C", Identity(False, "c@d"))  # Should evict A
    assert cache.get("A") is False
    assert cache.get("B") is not False
    assert cache.get("C") is not False
