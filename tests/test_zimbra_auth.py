from zimport_tools import zimbra_auth


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


def _fault():
    return {"Body": {"Fault": {"Reason": {"Text": "auth failed"}}}}


def _admin_ok():
    return {"Body": {"AuthResponse": {"authToken": [{"_content": "ADMTOK"}]}}}


def test_delegate_token(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(kw.get("json"))
        body = kw["json"]["Body"]
        if "AuthRequest" in body:
            return _Resp(_admin_ok())
        if "DelegateAuthRequest" in body:
            return _Resp({"Body": {"DelegateAuthResponse": {
                "authToken": [{"_content": "DELEGTOK"}]}}})
        return _Resp(_fault())

    monkeypatch.setattr(zimbra_auth.requests, "post", fake_post)
    tok = zimbra_auth.delegate_token(_Cfg, "target@d")
    assert tok == "DELEGTOK"
    # 第二次调用必须带上 admin token 的 Header
    assert calls[1]["Header"]["context"]["authToken"]["_content"] == "ADMTOK"
