import requests
from email.parser import BytesParser
from email.policy import compat32


class InjectError(Exception):
    pass


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
                          verify=cfg.verify_tls, timeout=30)
        data = r.json()
    except Exception:
        return False
    inner = data.get("Body", {})
    if "Fault" in inner:
        return False
    resp = inner.get("SearchResponse", {})
    hits = resp.get("m") or resp.get("hit") or []
    return len(hits) > 0


def inject_eml(cfg, account, folder, token, eml_path):
    url = "%s/home/%s/%s" % (cfg.rest_base, account, folder.strip("/"))
    with open(eml_path, "rb") as fh:
        data = fh.read()
    try:
        r = requests.post(url, params={"fmt": "eml"}, data=data,
                          cookies={"ZM_AUTH_TOKEN": token},
                          headers={"Content-Type": "message/rfc822"},
                          verify=cfg.verify_tls, timeout=120)
    except requests.RequestException as exc:
        raise InjectError("network: %s" % exc) from exc
    if r.status_code >= 300:
        raise InjectError("HTTP %s: %s" % (r.status_code, r.text[:200]))


def inject_tgz(cfg, account, token, tgz_path):
    url = "%s/home/%s/" % (cfg.rest_base, account)
    try:
        with open(tgz_path, "rb") as fh:
            r = requests.post(url, params={"fmt": "tgz", "resolve": "skip"},
                              data=fh, cookies={"ZM_AUTH_TOKEN": token},
                              verify=cfg.verify_tls, timeout=3600)
    except requests.RequestException as exc:
        raise InjectError("network: %s" % exc) from exc
    if r.status_code >= 300:
        raise InjectError("HTTP %s: %s" % (r.status_code, r.text[:200]))
