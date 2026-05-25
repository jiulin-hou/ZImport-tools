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
    with requests.post(cfg.soap_url,
                       json={"Header": header, "Body": body},
                       verify=cfg.tls_verify(), timeout=30) as r:
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


def _soap(cfg, account_token, request_name, request_body, ns="urn:zimbraMail"):
    header = {"context": {"_jsns": "urn:zimbra",
                          "authToken": {"_content": account_token}}}
    body = {request_name: dict(request_body, _jsns=ns)}
    with requests.post(cfg.soap_url,
                       json={"Header": header, "Body": body},
                       verify=cfg.tls_verify(), timeout=30) as r:
        data = r.json()
    inner = data.get("Body", {})
    if "Fault" in inner:
        raise FolderError(inner["Fault"].get("Reason", {})
                          .get("Text", "Zimbra fault"))
    return inner


def _get_folder_by_path(cfg, account_token, abs_path):
    """Return the folder dict for an absolute path like '/Inbox/2024', or
    None if Zimbra reports no such folder. Other faults are re-raised."""
    try:
        inner = _soap(cfg, account_token, "GetFolderRequest",
                      {"folder": {"path": abs_path}})
    except FolderError as exc:
        msg = str(exc).lower()
        # Zimbra returns "no such folder path: ..." when the folder doesn't
        # exist; we want to interpret that as None, not as an error.
        if "no such folder" in msg or "path does not exist" in msg:
            return None
        raise
    return inner.get("GetFolderResponse", {}).get("folder", [{}])[0] or None


def create_folder(cfg, account_token, path):
    """Create a message folder at `path` (forward-slash separated, with or
    without a leading slash, e.g. 'Inbox/2024' or '/Inbox/2024').

    Zimbra's CreateFolderRequest rejects names containing '/', so this
    walks the path segment by segment: for each missing intermediate it
    issues a CreateFolderRequest with the correct numeric parent id.
    Idempotent — already-existing segments are left alone."""
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        raise FolderError("文件夹路径为空")

    parent_id = "1"  # root
    cur_path = ""
    for seg in segments:
        cur_path = cur_path + "/" + seg
        existing = _get_folder_by_path(cfg, account_token, cur_path)
        if existing:
            parent_id = str(existing["id"])
            continue
        # Create this segment under parent_id.
        inner = _soap(cfg, account_token, "CreateFolderRequest", {
            "folder": {"name": seg, "l": parent_id,
                       "view": "message", "fie": "1"}
        })
        created = inner.get("CreateFolderResponse", {}).get("folder", [{}])[0]
        if not created:
            raise FolderError("创建失败:无返回 folder 信息")
        parent_id = str(created["id"])
