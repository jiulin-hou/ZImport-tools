import requests


class FolderError(Exception):
    pass


def list_folders(cfg, account_token):
    """以委托 token 调用 GetFolderRequest,返回该账户下消息视图文件夹的
    扁平路径列表(不带前导 /),按字母序排好。

    例:["Drafts", "Inbox", "Inbox/2025", "Junk", "Sent", "Trash"]
    """
    header = {"context": {"_jsns": "urn:zimbra",
                          "authToken": {"_content": account_token}}}
    body = {"GetFolderRequest": {"_jsns": "urn:zimbraMail"}}
    r = requests.post(cfg.soap_url,
                      json={"Header": header, "Body": body},
                      verify=cfg.verify_tls, timeout=30)
    data = r.json()
    inner = data.get("Body", {})
    if "Fault" in inner:
        raise FolderError(inner["Fault"]["Reason"]["Text"])
    resp = inner.get("GetFolderResponse", {})
    roots = resp.get("folder", []) or []
    paths = []
    for root in roots:
        _walk(root, paths)
    # 系统文件夹常见序在前,自定义随其后
    return sorted(set(paths), key=_sort_key)


_SYSTEM_ORDER = {"Inbox": 0, "Sent": 1, "Drafts": 2, "Junk": 3, "Trash": 4}


def _sort_key(path):
    top = path.split("/", 1)[0]
    return (_SYSTEM_ORDER.get(top, 99), path.lower())


def _walk(node, paths):
    for child in node.get("folder", []) or []:
        view = child.get("view") or "message"
        if view == "message":
            path = child.get("absFolderPath") or child.get("path")
            if not path:
                path = "/" + child.get("name", "")
            paths.append(path.lstrip("/"))
        _walk(child, paths)
