import collections
import requests

Identity = collections.namedtuple("Identity", ["is_admin", "account"])


class AuthError(Exception):
    pass


def _soap(url, body, verify, header=None):
    payload = {"Body": body}
    if header:
        payload["Header"] = header
    with requests.post(url, json=payload, verify=verify, timeout=30) as r:
        data = r.json()
    inner = data.get("Body", {})
    if "Fault" in inner:
        raise AuthError(inner["Fault"]["Reason"]["Text"])
    return inner


def admin_token(cfg):
    """以服务账号取得 admin authToken,供其它需要管理员凭据的模块复用。"""
    body = {"AuthRequest": {"_jsns": "urn:zimbraAdmin",
                            "name": cfg.svc_name,
                            "password": cfg.svc_password}}
    resp = _soap(cfg.admin_soap_url, body, cfg.tls_verify())
    return resp["AuthResponse"]["authToken"][0]["_content"]


def delegate_token(cfg, target_account):
    """用服务账号取得目标账户的委托 token。worker 注入前即时调用。"""
    admin_tok = admin_token(cfg)
    header = {"context": {"_jsns": "urn:zimbra",
                          "authToken": {"_content": admin_tok}}}
    body = {"DelegateAuthRequest": {
        "_jsns": "urn:zimbraAdmin",
        "account": {"by": "name", "_content": target_account}}}
    resp = _soap(cfg.admin_soap_url, body, cfg.tls_verify(), header=header)
    return resp["DelegateAuthResponse"]["authToken"][0]["_content"]
