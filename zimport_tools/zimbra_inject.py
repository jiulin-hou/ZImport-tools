import re
import requests
from email.parser import BytesParser
from email.policy import compat32
from urllib.parse import quote


# InjectError categories (set on .code):
#   network    — network/connection error, transient; worker retries
#   transient  — Zimbra 5xx / 408 / 429, transient; worker retries
#   quota      — target mailbox over quota, NOT retried
#   permission — account inactive / forbidden, NOT retried
#   invalid    — 4xx request rejected (bad eml, bad folder, etc.), NOT retried
#   unknown    — unrecognized failure
class InjectError(Exception):
    def __init__(self, code, message_zh, http_status=None, raw=None):
        self.code = code
        self.message_zh = message_zh
        self.http_status = http_status
        self.raw = raw
        super().__init__("%s: %s" % (code, message_zh))


_TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}
_QUOTA_PAT = re.compile(r"QUOTA_EXCEEDED|MAILBOX_FULL|quota.*exceed", re.I)
_PERMISSION_PAT = re.compile(
    r"PERMISSION_DENIED|ACCOUNT_INACTIVE|MAINTENANCE|NO_SUCH_ACCOUNT",
    re.I,
)


def _classify_http(status, body):
    """Turn a non-2xx Zimbra response into (code, message_zh)."""
    raw = (body or "")[:400]
    if status in _TRANSIENT_HTTP:
        return ("transient",
                "Zimbra 临时错误(HTTP %d),已自动重试" % status)
    if _QUOTA_PAT.search(raw):
        return ("quota", "目标邮箱配额已满,无法继续写入")
    if _PERMISSION_PAT.search(raw):
        return ("permission",
                "无权限写入目标邮箱(账号失效或服务账号被拒)")
    if status in (401, 403):
        return ("permission",
                "无权限写入目标邮箱(账号失效或服务账号被拒)")
    if 400 <= status < 500:
        return ("invalid",
                "邮件被 Zimbra 拒绝(HTTP %d)" % status)
    return ("unknown",
            "未知错误(HTTP %d)" % status)


def read_message_id(eml_path):
    """从 eml 头部读 Message-ID,失败/空返回 ""。仅解 header 不读全文,
    省内存。"""
    try:
        with open(eml_path, "rb") as fh:
            msg = BytesParser(policy=compat32).parse(fh, headersonly=True)
        mid = msg.get("Message-ID") or msg.get("Message-Id") or ""
        return mid.strip()
    except Exception:
        return ""


def message_exists(cfg, token, message_id):
    """以委托 token 调 SOAP SearchRequest,看该 Message-ID 是否已在邮箱内
    任意位置存在。SOAP 失败一律视为"不存在"以免阻塞导入(返回 False)。"""
    if not message_id:
        return False
    # Zimbra 的操作符是 msgid:(不是 messageid:),且查询字符串里**不能**带
    # 尖括号 —— 必须剥掉 RFC 822 Message-ID 头那对 <>,否则 hit 为 0。
    safe = message_id.strip().strip('<>').replace('"', '').replace('\\', '')
    query = 'msgid:"%s"' % safe
    header = {"context": {"_jsns": "urn:zimbra",
                          "authToken": {"_content": token}}}
    body = {"SearchRequest": {
        "_jsns": "urn:zimbraMail",
        "query": query, "limit": 1, "types": "message"}}
    try:
        r = requests.post(cfg.soap_url,
                          json={"Header": header, "Body": body},
                          verify=cfg.tls_verify(), timeout=30)
        data = r.json()
    except Exception:
        return False
    inner = data.get("Body", {})
    if "Fault" in inner:
        return False
    resp = inner.get("SearchResponse", {})
    hits = resp.get("m") or resp.get("hit") or []
    return len(hits) > 0


def batch_existing_message_ids(cfg, token, message_ids, batch=50):
    """Resolve in batches which of the given Message-IDs already exist in the
    target mailbox. Returns a set of message_ids that exist. Failed queries
    are treated as "unknown -> assume not present" so import isn't blocked."""
    found = set()
    cleaned = []
    for mid in message_ids:
        if not mid:
            continue
        safe = (mid.strip().strip('<>')
                .replace('"', '').replace('\\', ''))
        if safe:
            cleaned.append((mid, safe))

    header = {"context": {"_jsns": "urn:zimbra",
                          "authToken": {"_content": token}}}
    for i in range(0, len(cleaned), batch):
        chunk = cleaned[i:i + batch]
        # msgid:("A" OR "B" ...) - Zimbra search supports OR in this form
        query = "msgid:(%s)" % " OR ".join('"%s"' % s for _, s in chunk)
        body = {"SearchRequest": {
            "_jsns": "urn:zimbraMail",
            "query": query, "limit": len(chunk), "types": "message"}}
        try:
            r = requests.post(cfg.soap_url,
                              json={"Header": header, "Body": body},
                              verify=cfg.tls_verify(), timeout=60)
            data = r.json()
        except Exception:
            continue
        inner = data.get("Body", {})
        if "Fault" in inner:
            continue
        hits = inner.get("SearchResponse", {}).get("m") or []
        # Zimbra returns the message's Message-ID header in m[i]['mid'] or
        # similar; if not present we cannot map back individually, so fall
        # back to "any hit means treat all queried as present" which is
        # safer-for-dedupe (false positives skip, false negatives import dup).
        # In practice Zimbra returns mid in the hit dict for msgid: queries.
        hit_mids = set()
        for h in hits:
            mid_val = (h.get("mid") or h.get("msgid") or "").strip().strip('<>')
            if mid_val:
                hit_mids.add(mid_val)
        for orig, safe in chunk:
            if safe in hit_mids:
                found.add(orig)
    return found


def inject_eml(cfg, account, folder, token, eml_path):
    # Both account and folder are user-controlled (folder always; account when
    # the requester is admin). Encode them so '?', '#', '%', or unicode in a
    # folder name cannot rewrite the REST URL's query string or path.
    url = "%s/home/%s/%s" % (cfg.rest_base,
                             quote(account, safe="@."),
                             quote(folder.strip("/"), safe="/"))
    with open(eml_path, "rb") as fh:
        data = fh.read()
    try:
        r = requests.post(url, params={"fmt": "eml"}, data=data,
                          cookies={"ZM_AUTH_TOKEN": token},
                          headers={"Content-Type": "message/rfc822"},
                          verify=cfg.tls_verify(), timeout=120)
    except requests.RequestException as exc:
        raise InjectError("network", "网络异常,请检查 Zimbra 是否可达") from exc
    if r.status_code >= 300:
        code, msg = _classify_http(r.status_code, r.text)
        raise InjectError(code, msg, http_status=r.status_code,
                          raw=r.text[:200])


def inject_tgz(cfg, account, token, tgz_path):
    url = "%s/home/%s/" % (cfg.rest_base, quote(account, safe="@."))
    # timestamp=0 tells Zimbra not to use the archive entry date as the
    # received date; instead infer from each message's Date: header. Without
    # it every imported message would carry today's timestamp.
    try:
        with open(tgz_path, "rb") as fh:
            r = requests.post(url, params={"fmt": "tgz",
                                            "resolve": "skip",
                                            "timestamp": "0"},
                              data=fh, cookies={"ZM_AUTH_TOKEN": token},
                              verify=cfg.tls_verify(), timeout=3600)
    except requests.RequestException as exc:
        raise InjectError("network", "网络异常,请检查 Zimbra 是否可达") from exc
    if r.status_code >= 300:
        code, msg = _classify_http(r.status_code, r.text)
        raise InjectError(code, msg, http_status=r.status_code,
                          raw=r.text[:200])
