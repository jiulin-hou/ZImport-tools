import pytest
from zimport_tools import zimbra_folders


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Cfg:
    soap_url = "https://h:8443/service/soap"
    verify_tls = False


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
