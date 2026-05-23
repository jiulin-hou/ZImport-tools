"""Flask web layer for ZImport-tools.

Differs from the standalone ZImport's web.py in three ways:

  1. No /api/login or login form. Identity comes from the Zimbra
     ZM_AUTH_TOKEN cookie, validated through zimbra_session.

  2. CSRF protection on every state-changing request: requires a custom
     X-Zimport-CSRF header (which cross-site forms can't set) plus an
     Origin check that allows empty Origin (test client) and rejects any
     non-matching Origin.

  3. Account-switch protection: each request compares the cookie's token
     hash against the session's; if they differ, the session is cleared
     and rebuilt against the current cookie, so a different Zimbra account
     cannot inherit the previous one's session privileges.
"""

import functools
import os
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory, session

from zimport_tools import zimbra_session
from zimport_tools.store import TaskStore
from zimport_tools.zimbra_auth import AuthError

_STATIC = os.path.join(os.path.dirname(__file__), "static")
_CSRF_HEADER = "X-Zimport-CSRF"
_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def _origin_from_url(url):
    if not url:
        return None
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return None
    return "%s://%s" % (p.scheme, p.netloc)


def create_app(cfg):
    app = Flask(__name__, static_folder=None)
    app.secret_key = cfg.secret_key
    app.config["MAX_CONTENT_LENGTH"] = None
    app.config.update(
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
    )
    store = TaskStore(cfg.db_path)
    os.makedirs(cfg.temp_root, exist_ok=True)

    expected_origin = _origin_from_url(cfg.rest_base)

    def _csrf_check():
        if request.method not in _STATE_CHANGING:
            return None
        if request.headers.get(_CSRF_HEADER) != "1":
            return jsonify({"error": "非法请求来源"}), 403
        origin = request.headers.get("Origin")
        if origin and expected_origin and origin != expected_origin:
            return jsonify({"error": "非法请求来源"}), 403
        return None

    def _auth_via_cookie():
        """Return ('zimbra_unreachable', None) | (Identity, token) | None."""
        token = request.cookies.get("ZM_AUTH_TOKEN")
        if not token:
            return None
        try:
            ident = zimbra_session.validate(cfg, token)
        except zimbra_session.ZimbraUnreachable:
            return ("zimbra_unreachable", None)
        except AuthError:
            return None
        return ident, token

    def login_required(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            cookie_token = request.cookies.get("ZM_AUTH_TOKEN")
            current_hash = (zimbra_session.token_hash(cookie_token)
                            if cookie_token else None)
            # Drop session if its underlying cookie no longer matches
            if "account" in session and session.get("token_hash") != current_hash:
                session.clear()
            if "account" not in session:
                result = _auth_via_cookie()
                if result is None:
                    return jsonify({"error": "未登录"}), 401
                if result == ("zimbra_unreachable", None):
                    return jsonify({"error": "Zimbra 暂不可达"}), 503
                ident, token = result
                session["account"] = ident.account
                session["is_admin"] = ident.is_admin
                session["token_hash"] = zimbra_session.token_hash(token)
            csrf = _csrf_check()
            if csrf is not None:
                return csrf
            return fn(*a, **kw)
        return wrapper

    @app.route("/")
    def index():
        return send_from_directory(_STATIC, "index.html")

    @app.route("/static/<path:name>")
    def static_files(name):
        return send_from_directory(_STATIC, name)

    @app.route("/api/me")
    @login_required
    def me():
        return jsonify({"account": session["account"],
                        "is_admin": session.get("is_admin", False)})

    # No-op endpoint for CSRF unit tests. Registered unconditionally —
    # it is auth-protected and side-effect-free, so harmless in production.
    @app.route("/api/_test_csrf", methods=["POST"])
    @login_required
    def _test_csrf():
        return jsonify({"ok": True})

    # Business endpoints (upload / import / tasks / folders / admin) are
    # registered in Task 7.
    return app
