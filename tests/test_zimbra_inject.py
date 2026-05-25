import pytest
from zimport_tools import zimbra_inject


class _Cfg:
    rest_base = "https://h:8443"
    soap_url = "https://h:8443/service/soap"
    verify_tls = False

    @staticmethod
    def tls_verify():
        return False


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = "err" if status >= 300 else "ok"
    def __enter__(self): return self
    def __exit__(self, *a): pass


def test_inject_eml_builds_correct_request(tmp_path, monkeypatch):
    eml = tmp_path / "m.eml"
    eml.write_bytes(b"From: a@b\r\n\r\nhello")
    captured = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["params"] = kw.get("params")
        captured["cookies"] = kw.get("cookies")
        captured["data"] = kw.get("data")
        return _Resp(200)

    monkeypatch.setattr(zimbra_inject.requests, "post", fake_post)
    zimbra_inject.inject_eml(_Cfg, "u@d", "Inbox", "TOK", str(eml))
    assert captured["url"] == "https://h:8443/home/u@d/Inbox"
    assert captured["params"]["fmt"] == "eml"
    assert captured["cookies"]["ZM_AUTH_TOKEN"] == "TOK"
    assert captured["data"] == b"From: a@b\r\n\r\nhello"


def test_inject_eml_raises_on_http_error(tmp_path, monkeypatch):
    eml = tmp_path / "m.eml"
    eml.write_bytes(b"x")
    monkeypatch.setattr(zimbra_inject.requests, "post",
                        lambda url, **kw: _Resp(500))
    with pytest.raises(zimbra_inject.InjectError):
        zimbra_inject.inject_eml(_Cfg, "u@d", "Inbox", "TOK", str(eml))


def test_inject_eml_wraps_network_exception(tmp_path, monkeypatch):
    """requests.RequestException 必须被 wrap 成 InjectError,
    否则 worker 会把整任务 fail 而不是单封 fail。"""
    eml = tmp_path / "m.eml"
    eml.write_bytes(b"x")

    def boom(*a, **kw):
        raise zimbra_inject.requests.ConnectionError("conn refused")

    monkeypatch.setattr(zimbra_inject.requests, "post", boom)
    with pytest.raises(zimbra_inject.InjectError) as ei:
        zimbra_inject.inject_eml(_Cfg, "u@d", "Inbox", "TOK", str(eml))
    assert "network:" in str(ei.value)


def test_inject_tgz_wraps_network_exception(tmp_path, monkeypatch):
    tgz = tmp_path / "a.tgz"
    tgz.write_bytes(b"X")

    def boom(*a, **kw):
        raise zimbra_inject.requests.Timeout("timed out")

    monkeypatch.setattr(zimbra_inject.requests, "post", boom)
    with pytest.raises(zimbra_inject.InjectError) as ei:
        zimbra_inject.inject_tgz(_Cfg, "u@d", "TOK", str(tgz))
    assert "network:" in str(ei.value)


def test_read_message_id(tmp_path):
    eml = tmp_path / "m.eml"
    eml.write_bytes(
        b"From: a@b\r\n"
        b"To: c@d\r\n"
        b"Message-ID: <abc.123@example.com>\r\n"
        b"\r\nhello body")
    assert zimbra_inject.read_message_id(str(eml)) == "<abc.123@example.com>"


def test_read_message_id_missing(tmp_path):
    eml = tmp_path / "m.eml"
    eml.write_bytes(b"From: a@b\r\n\r\nbody")
    assert zimbra_inject.read_message_id(str(eml)) == ""


class _SoapResp:
    def __init__(self, payload):
        self._payload = payload
    def __enter__(self): return self
    def __exit__(self, *a): pass

    def json(self):
        return self._payload


def test_message_exists_hit(monkeypatch):
    monkeypatch.setattr(zimbra_inject.requests, "post",
                        lambda *a, **kw: _SoapResp({"Body": {
                            "SearchResponse": {"m": [{"id": "1"}]}}}))
    assert zimbra_inject.message_exists(_Cfg, "TOK", "<id@x>") is True


def test_message_exists_uses_msgid_operator_without_angle_brackets(monkeypatch):
    """Zimbra search 操作符是 msgid:(不是 messageid:),且 query 里**不能**
    带 <>,否则 hit 永远为 0。这两个细节误一个就静默失效,所以单独守住。"""
    captured = {}

    def fake_post(url, **kw):
        captured["query"] = kw["json"]["Body"]["SearchRequest"]["query"]
        return _SoapResp({"Body": {"SearchResponse": {}}})

    monkeypatch.setattr(zimbra_inject.requests, "post", fake_post)
    zimbra_inject.message_exists(_Cfg, "TOK", "<abc@example.com>")
    assert captured["query"].startswith("msgid:"), \
        "operator must be msgid: not messageid:"
    assert "<" not in captured["query"] and ">" not in captured["query"], \
        "angle brackets must be stripped"
    assert "abc@example.com" in captured["query"]


def test_message_exists_miss(monkeypatch):
    monkeypatch.setattr(zimbra_inject.requests, "post",
                        lambda *a, **kw: _SoapResp({"Body": {
                            "SearchResponse": {}}}))
    assert zimbra_inject.message_exists(_Cfg, "TOK", "<id@x>") is False


def test_message_exists_empty_id_skips_call(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("network must not be called for empty id")
    monkeypatch.setattr(zimbra_inject.requests, "post", boom)
    assert zimbra_inject.message_exists(_Cfg, "TOK", "") is False


def test_message_exists_fault_returns_false(monkeypatch):
    # SOAP 失败时不阻塞,默认 False(让 inject 继续走 — 重复了再让 Zimbra 拒)
    monkeypatch.setattr(zimbra_inject.requests, "post",
                        lambda *a, **kw: _SoapResp({"Body": {"Fault": {
                            "Reason": {"Text": "boom"}}}}))
    assert zimbra_inject.message_exists(_Cfg, "TOK", "<id@x>") is False


def test_inject_tgz_builds_correct_request(tmp_path, monkeypatch):
    tgz = tmp_path / "a.tgz"
    tgz.write_bytes(b"TGZDATA")
    captured = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["params"] = kw.get("params")
        return _Resp(200)

    monkeypatch.setattr(zimbra_inject.requests, "post", fake_post)
    zimbra_inject.inject_tgz(_Cfg, "u@d", "TOK", str(tgz))
    assert captured["url"] == "https://h:8443/home/u@d/"
    assert captured["params"]["fmt"] == "tgz"


def test_batch_existing_message_ids_returns_only_present(monkeypatch):
    """Regression for v1.3.0 bug: Zimbra SearchResponse hits don't carry
    Message-ID, so any batch implementation that relies on per-hit mid lookup
    silently returns empty -> dedupe goes dark and user re-imports duplicates.
    The contract is: pass a list of mids, get back the subset that exist
    (and an empty undecidable set when nothing errored)."""
    from zimport_tools import zimbra_inject
    existing = {"<a@x>", "<c@x>"}
    monkeypatch.setattr(zimbra_inject, "_check_one",
                        lambda cfg, tok, mid: mid in existing)
    result, undecidable = zimbra_inject.batch_existing_message_ids(
        _Cfg, "TOK", ["<a@x>", "<b@x>", "<c@x>", "", None])
    assert result == {"<a@x>", "<c@x>"}
    assert undecidable == set()


def test_batch_existing_returns_undecidable_on_soap_error(monkeypatch):
    """SOAP error during dedupe check must surface in `undecidable` so the
    caller can flag those messages instead of silently re-injecting."""
    from zimport_tools import zimbra_inject

    calls = []

    def fake_check_one(cfg, tok, mid):
        calls.append(mid)
        if mid == "<exists@x>":
            return True
        if mid == "<missing@x>":
            return False
        raise zimbra_inject.DedupeCheckError("network down")

    monkeypatch.setattr(zimbra_inject, "_check_one", fake_check_one)
    existing, undecidable = zimbra_inject.batch_existing_message_ids(
        _Cfg, "TOK",
        ["<exists@x>", "<missing@x>", "<broken@x>", "", None])
    assert existing == {"<exists@x>"}
    assert undecidable == {"<broken@x>"}
    # _check_one is not called for falsy mids
    assert "" not in calls and None not in calls
