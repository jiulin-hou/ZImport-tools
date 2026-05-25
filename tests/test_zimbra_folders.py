import pytest
from zimport_tools import zimbra_folders


class _Resp:
    def __init__(self, payload):
        self._payload = payload
    def __enter__(self): return self
    def __exit__(self, *a): pass

    def json(self):
        return self._payload


class _Cfg:
    soap_url = "https://h:8443/service/soap"
    verify_tls = False

    @staticmethod
    def tls_verify():
        return False


def _resp_ok(folders):
    """Build a fake GetFolderResponse with the given folder children
    under a USER_ROOT node."""
    return {"Body": {"GetFolderResponse": {
        "folder": [{"name": "USER_ROOT", "absFolderPath": "/",
                    "folder": folders}]}}}


def test_list_folders_flattens_and_sorts(monkeypatch):
    children = [
        {"name": "Inbox", "absFolderPath": "/Inbox", "view": "message",
         "folder": [
             {"name": "2025", "absFolderPath": "/Inbox/2025",
              "view": "message"}
         ]},
        {"name": "Sent", "absFolderPath": "/Sent", "view": "message"},
        {"name": "Calendar", "absFolderPath": "/Calendar",
         "view": "appointment"},  # 非 message,应跳过
        {"name": "Custom", "absFolderPath": "/Custom"},  # 无 view,默认收入
    ]
    monkeypatch.setattr(zimbra_folders.requests, "post",
                        lambda *a, **kw: _Resp(_resp_ok(children)))
    paths = zimbra_folders.list_folders(_Cfg, "USRTOK")
    # 系统文件夹靠前,自定义随后;不含 Calendar(appointment)
    assert paths == ["Inbox", "Inbox/2025", "Sent", "Custom"]


def test_list_folders_fault(monkeypatch):
    monkeypatch.setattr(zimbra_folders.requests, "post",
                        lambda *a, **kw: _Resp({"Body": {"Fault": {
                            "Reason": {"Text": "no permission"}}}}))
    with pytest.raises(zimbra_folders.FolderError):
        zimbra_folders.list_folders(_Cfg, "USRTOK")


def test_list_folders_empty(monkeypatch):
    monkeypatch.setattr(zimbra_folders.requests, "post",
                        lambda *a, **kw: _Resp(_resp_ok([])))
    assert zimbra_folders.list_folders(_Cfg, "USRTOK") == []


# ---- create_folder ----

def _fake_post_sequence(seq):
    """Return a function that, on each call, pops the next response from
    `seq` (a list of payload dicts). Lets us script multi-step SOAP calls."""
    it = iter(seq)
    def _post(*a, **kw):
        return _Resp(next(it))
    return _post


def test_create_folder_single_segment_creates_under_root(monkeypatch):
    """When the target is a top-level folder that doesn't yet exist, exactly
    one CreateFolderRequest is issued under parent_id='1'."""
    calls = []
    def fake_post(url, json, **kw):
        calls.append(json["Body"])
        body = json["Body"]
        if "GetFolderRequest" in body:
            # path doesn't exist
            return _Resp({"Body": {"Fault": {
                "Reason": {"Text": "no such folder path"}}}})
        return _Resp({"Body": {"CreateFolderResponse": {
            "folder": [{"id": "99", "name": "新文件夹",
                        "absFolderPath": "/新文件夹"}]}}})
    monkeypatch.setattr(zimbra_folders.requests, "post", fake_post)
    zimbra_folders.create_folder(_Cfg, "TOK", "新文件夹")
    # Two SOAP calls: GetFolder check + CreateFolder
    assert len(calls) == 2
    create_body = calls[1]["CreateFolderRequest"]["folder"]
    assert create_body["name"] == "新文件夹"
    assert create_body["l"] == "1"


def test_create_folder_walks_nested_path(monkeypatch):
    """Inbox/2024/Q3 with none existing -> 3 GetFolder + 3 CreateFolder,
    each CreateFolder uses the parent ID returned by the previous one."""
    fakes = [
        # GetFolder /Inbox -> not found
        {"Body": {"Fault": {"Reason": {"Text": "no such folder path"}}}},
        # CreateFolder /Inbox -> id=2
        {"Body": {"CreateFolderResponse": {
            "folder": [{"id": "2", "name": "Inbox"}]}}},
        # GetFolder /Inbox/2024 -> not found
        {"Body": {"Fault": {"Reason": {"Text": "no such folder path"}}}},
        # CreateFolder under id=2 -> id=10
        {"Body": {"CreateFolderResponse": {
            "folder": [{"id": "10", "name": "2024"}]}}},
        # GetFolder /Inbox/2024/Q3 -> not found
        {"Body": {"Fault": {"Reason": {"Text": "no such folder path"}}}},
        # CreateFolder under id=10 -> id=42
        {"Body": {"CreateFolderResponse": {
            "folder": [{"id": "42", "name": "Q3"}]}}},
    ]
    captured = []
    def fake_post(url, json, **kw):
        captured.append(json["Body"])
        return _Resp(fakes.pop(0))
    monkeypatch.setattr(zimbra_folders.requests, "post", fake_post)
    zimbra_folders.create_folder(_Cfg, "TOK", "Inbox/2024/Q3")
    # Pull out parent IDs from each CreateFolderRequest
    creates = [b["CreateFolderRequest"]["folder"]
               for b in captured if "CreateFolderRequest" in b]
    assert [c["name"] for c in creates] == ["Inbox", "2024", "Q3"]
    assert [c["l"] for c in creates] == ["1", "2", "10"]


def test_create_folder_idempotent_when_exists(monkeypatch):
    """If the full path already exists, no CreateFolderRequest is issued."""
    captured = []
    def fake_post(url, json, **kw):
        captured.append(json["Body"])
        # Every GetFolder returns "found"
        return _Resp({"Body": {"GetFolderResponse": {
            "folder": [{"id": "100", "name": "x",
                        "absFolderPath": "/x"}]}}})
    monkeypatch.setattr(zimbra_folders.requests, "post", fake_post)
    zimbra_folders.create_folder(_Cfg, "TOK", "Inbox/already-here")
    assert all("CreateFolderRequest" not in b for b in captured)


def test_create_folder_rejects_empty_path(monkeypatch):
    monkeypatch.setattr(zimbra_folders.requests, "post",
                        lambda *a, **kw: _Resp({}))
    with pytest.raises(zimbra_folders.FolderError):
        zimbra_folders.create_folder(_Cfg, "TOK", "/")
