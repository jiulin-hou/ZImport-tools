import pytest
from zimport_tools import zimbra_search, zimbra_auth


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Cfg:
    soap_url = "https://h:8443/service/soap"
    admin_soap_url = "https://h:7071/service/admin/soap"
    verify_tls = False
    svc_name = "svc@d"
    svc_password = "svcpw"


def test_query_too_short(monkeypatch):
    # 不应触发任何网络调用
    def boom(*a, **kw):
        raise AssertionError("network must not be called")
    monkeypatch.setattr(zimbra_search.requests, "post", boom)
    assert zimbra_search.search_accounts(_Cfg, "a") == []
    assert zimbra_search.search_accounts(_Cfg, "") == []


def test_returns_accounts(monkeypatch):
    def fake_post(url, **kw):
        body = kw["json"]["Body"]
        if "AuthRequest" in body:  # 获取 admin token
            return _Resp({"Body": {"AuthResponse": {
                "authToken": [{"_content": "ADMTOK"}]}}})
        if "SearchDirectoryRequest" in body:
            return _Resp({"Body": {"SearchDirectoryResponse": {"account": [
                {"name": "alice@d",
                 "a": [{"n": "displayName", "_content": "Alice"}]},
                {"name": "bob@d", "a": []},
            ]}}})
        raise AssertionError("unexpected " + str(body))

    monkeypatch.setattr(zimbra_search.requests, "post", fake_post)
    monkeypatch.setattr(zimbra_auth.requests, "post", fake_post)
    out = zimbra_search.search_accounts(_Cfg, "ali")
    assert out == [{"name": "alice@d", "display": "Alice"},
                   {"name": "bob@d", "display": ""}]


def test_sanitizes_query(monkeypatch):
    captured = {}

    def fake_post(url, **kw):
        body = kw["json"]["Body"]
        if "AuthRequest" in body:
            return _Resp({"Body": {"AuthResponse": {
                "authToken": [{"_content": "T"}]}}})
        captured["query"] = body["SearchDirectoryRequest"]["query"]
        return _Resp({"Body": {"SearchDirectoryResponse": {"account": []}}})

    monkeypatch.setattr(zimbra_search.requests, "post", fake_post)
    monkeypatch.setattr(zimbra_auth.requests, "post", fake_post)
    # 试图注入 LDAP 元字符,应被剥掉
    zimbra_search.search_accounts(_Cfg, "ali)(uid=*)(|(uid=*")
    assert "(" not in captured["query"].replace("(uid", "").replace("(mail",
        "").replace("(displayName", "").replace("(|", "").replace(")(", "")
    # 安全做法:剩下的 query 字符串里只有 alphanum 等
    assert "uid=*ali" in captured["query"]


def test_fault(monkeypatch):
    def fake_post(url, **kw):
        body = kw["json"]["Body"]
        if "AuthRequest" in body:
            return _Resp({"Body": {"AuthResponse": {
                "authToken": [{"_content": "T"}]}}})
        return _Resp({"Body": {"Fault": {"Reason": {"Text": "denied"}}}})

    monkeypatch.setattr(zimbra_search.requests, "post", fake_post)
    monkeypatch.setattr(zimbra_auth.requests, "post", fake_post)
    with pytest.raises(zimbra_search.SearchError):
        zimbra_search.search_accounts(_Cfg, "ali")
