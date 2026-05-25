import pytest
from zimport_tools import zimbra_search, zimbra_auth


class _Resp:
    def __init__(self, payload):
        self._payload = payload
    def __enter__(self): return self
    def __exit__(self, *a): pass

    def json(self):
        return self._payload


class _Cfg:
    soap_url = "https://h:8443/service/soap"
    admin_soap_url = "https://h:7071/service/admin/soap"
    verify_tls = False

    @staticmethod
    def tls_verify():
        return False
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


# ---- list_accounts ----

def test_list_accounts_returns_sorted_and_filters_system(monkeypatch):
    def fake_post(url, **kw):
        body = kw["json"]["Body"]
        if "AuthRequest" in body:
            return _Resp({"Body": {"AuthResponse": {
                "authToken": [{"_content": "T"}]}}})
        return _Resp({"Body": {"SearchDirectoryResponse": {"account": [
            {"name": "bob@d", "a": [{"n": "displayName", "_content": "Bob"}]},
            {"name": "alice@d", "a": [{"n": "displayName", "_content": "Alice"}]},
            # 系统账户应被过滤掉
            {"name": "galsync.xyz@d", "a": []},
            {"name": "spam.x@d", "a": []},
            {"name": "ham.y@d", "a": []},
            {"name": "virus-quarantine.x@d", "a": []},
            # 工具自己的服务账号(_Cfg.svc_name = "svc@d") —— 也要过滤
            {"name": "svc@d", "a": [{"n": "displayName", "_content": "ZImport"}]},
            # 大小写不敏感匹配:
            {"name": "SVC@D", "a": []},
        ]}}})

    monkeypatch.setattr(zimbra_search.requests, "post", fake_post)
    monkeypatch.setattr(zimbra_auth.requests, "post", fake_post)
    out = zimbra_search.list_accounts(_Cfg)
    # 排过序、过滤了系统账户和服务账号
    assert [a["name"] for a in out] == ["alice@d", "bob@d"]
    assert out[0]["display"] == "Alice"


def test_search_accounts_also_filters_service_account(monkeypatch):
    def fake_post(url, **kw):
        body = kw["json"]["Body"]
        if "AuthRequest" in body:
            return _Resp({"Body": {"AuthResponse": {
                "authToken": [{"_content": "T"}]}}})
        return _Resp({"Body": {"SearchDirectoryResponse": {"account": [
            {"name": "svc@d", "a": []},
            {"name": "alice@d", "a": [{"n": "displayName", "_content": "Alice"}]},
        ]}}})

    monkeypatch.setattr(zimbra_search.requests, "post", fake_post)
    monkeypatch.setattr(zimbra_auth.requests, "post", fake_post)
    out = zimbra_search.search_accounts(_Cfg, "any")
    assert [a["name"] for a in out] == ["alice@d"]  # svc@d 被过滤


def test_list_accounts_uses_correct_query(monkeypatch):
    captured = {}

    def fake_post(url, **kw):
        body = kw["json"]["Body"]
        if "AuthRequest" in body:
            return _Resp({"Body": {"AuthResponse": {
                "authToken": [{"_content": "T"}]}}})
        captured["query"] = body["SearchDirectoryRequest"]["query"]
        captured["types"] = body["SearchDirectoryRequest"]["types"]
        return _Resp({"Body": {"SearchDirectoryResponse": {"account": []}}})

    monkeypatch.setattr(zimbra_search.requests, "post", fake_post)
    monkeypatch.setattr(zimbra_auth.requests, "post", fake_post)
    zimbra_search.list_accounts(_Cfg)
    assert captured["query"] == "(objectClass=zimbraAccount)"
    assert captured["types"] == "accounts"
