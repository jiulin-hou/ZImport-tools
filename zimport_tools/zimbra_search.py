import re
import requests

from zimport_tools import zimbra_auth


class SearchError(Exception):
    pass


_SAFE = re.compile(r"[A-Za-z0-9._@\- ]")


def _sanitize(q):
    """SearchDirectoryRequest 的 query 是 LDAP filter,过滤掉控制字符
    和 LDAP 元字符,避免被注入。"""
    return "".join(c for c in q if _SAFE.match(c))


def search_accounts(cfg, query, limit=20):
    """以服务账号身份搜账户:uid/mail/displayName 任一字段包含 query。
    返回 [{"name": "user@dom", "display": "User Name"}, ...]

    query 长度小于 2 直接返回 []。
    """
    q = _sanitize((query or "").strip())
    if len(q) < 2:
        return []
    tok = zimbra_auth.admin_token(cfg)
    header = {"context": {"_jsns": "urn:zimbra",
                          "authToken": {"_content": tok}}}
    body = {"SearchDirectoryRequest": {
        "_jsns": "urn:zimbraAdmin",
        "query": "(|(uid=*%s*)(mail=*%s*)(displayName=*%s*))" % (q, q, q),
        "types": "accounts",
        "limit": int(limit),
        "attrs": "displayName"}}
    r = requests.post(cfg.admin_soap_url,
                      json={"Header": header, "Body": body},
                      verify=cfg.verify_tls, timeout=30)
    data = r.json()
    inner = data.get("Body", {})
    if "Fault" in inner:
        raise SearchError(inner["Fault"]["Reason"]["Text"])
    accounts = inner.get("SearchDirectoryResponse", {}).get("account", []) or []
    out = []
    for acc in accounts:
        name = acc.get("name")
        if not name:
            continue
        display = ""
        for attr in acc.get("a", []) or []:
            if attr.get("n") == "displayName":
                display = attr.get("_content", "") or ""
                break
        out.append({"name": name, "display": display})
    return out
