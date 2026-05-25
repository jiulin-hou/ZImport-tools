"""Validate Zimbra ZM_AUTH_TOKEN cookies and cache results.

Used by ZImport-tools's web layer to identify the current Zimbra user from
the ambient session cookie, without ever seeing the user's password.

The cache exists for one reason: chunked uploads can hit the API hundreds of
times for a single large file. Validating against Zimbra on every request would
both add latency and pile load onto the Zimbra mailbox server. A short-lived
in-memory cache absorbs that, while still expiring quickly enough that a
revoked or expired token stops working within seconds.
"""

import threading
import time
from collections import OrderedDict

import requests

from zimport_tools.zimbra_auth import Identity, AuthError, _soap

POSITIVE_TTL = 300   # 5 minutes
NEGATIVE_TTL = 30    # 30 seconds
DEFAULT_CAPACITY = 1024


class ZimbraUnreachable(Exception):
    """Raised when the Zimbra SOAP endpoint is unreachable (network error)."""


def _now():
    return time.time()


class _Cache:
    """LRU cache: token -> (identity_or_None, expires_at).

    identity_or_None is None for negative entries (auth failed).
    `get` returns one of:
      - False:     not in cache (or expired)
      - None:      negative cache entry (token known invalid)
      - Identity:  positive cache entry
    """

    def __init__(self, capacity=DEFAULT_CAPACITY):
        self.capacity = capacity
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def get(self, token):
        with self._lock:
            entry = self._items.get(token)
            if entry is None:
                return False
            value, expires_at = entry
            if _now() >= expires_at:
                del self._items[token]
                return False
            self._items.move_to_end(token)
            return value  # Identity or None

    def put_positive(self, token, identity):
        self._put(token, identity, POSITIVE_TTL)

    def put_negative(self, token):
        self._put(token, None, NEGATIVE_TTL)

    def _put(self, token, value, ttl):
        with self._lock:
            self._items[token] = (value, _now() + ttl)
            self._items.move_to_end(token)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)


_default_cache = _Cache()


def validate(cfg, token, _cache=None):
    """Validate a Zimbra ZM_AUTH_TOKEN cookie value.

    Returns Identity on success.
    Raises AuthError if the token is invalid/expired/rejected.
    Raises ZimbraUnreachable if Zimbra is unreachable (network error).
    """
    cache = _cache if _cache is not None else _default_cache
    cached = cache.get(token)
    if cached is not False:
        if cached is None:
            raise AuthError("invalid token (cached)")
        return cached  # Identity

    body = {"GetInfoRequest": {
        "_jsns": "urn:zimbraAccount",
        "sections": "mbox,prefs,attrs,props",
    }}
    header = {"context": {"_jsns": "urn:zimbra",
                          "authToken": {"_content": token}}}
    try:
        inner = _soap(cfg.soap_url, body, cfg.tls_verify(), header=header)
    except requests.RequestException as exc:
        raise ZimbraUnreachable(str(exc))
    except AuthError:
        cache.put_negative(token)
        raise

    info = inner.get("GetInfoResponse", {})
    account = info.get("name") or _account_from_attrs(info)
    is_admin = _admin_from_attrs(info)
    identity = Identity(is_admin=is_admin, account=account)
    cache.put_positive(token, identity)
    return identity


def _account_from_attrs(info):
    attrs = info.get("attrs", {}).get("_attrs", {})
    return attrs.get("zimbraMailDeliveryAddress") or attrs.get("uid") or ""


def _admin_from_attrs(info):
    attrs = info.get("attrs", {}).get("_attrs", {})
    val = attrs.get("zimbraIsAdminAccount")
    return str(val).upper() == "TRUE"
