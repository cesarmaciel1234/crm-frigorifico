"""Autenticación, headers de seguridad y utilidades de hardening."""
import hmac
import secrets
from functools import wraps

from flask import jsonify, redirect, request, session, url_for

from app.config import Config


def auth_enabled() -> bool:
    if Config.TESTING and not Config.MT_API_KEY:
        return False
    return bool(Config.MT_API_KEY)


def _api_key_from_request() -> str | None:
    header = request.headers.get("X-API-Key")
    if header:
        return header.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def verify_api_key(value: str | None) -> bool:
    if not value or not Config.MT_API_KEY:
        return False
    return hmac.compare_digest(value, Config.MT_API_KEY)


def is_authenticated() -> bool:
    if not auth_enabled():
        return True
    if session.get("authenticated"):
        return True
    return verify_api_key(_api_key_from_request())


def verify_audit_password(value: str | None) -> bool:
    if not Config.AUDIT_DELETE_PASSWORD:
        return False
    if not value:
        return False
    return hmac.compare_digest(value, Config.AUDIT_DELETE_PASSWORD)


PUBLIC_PATHS = frozenset({"/login", "/auth/login", "/health", "/manifest.json", "/sw.js"})


def register_security(app):
    @app.before_request
    def _enforce_auth():
        if not auth_enabled():
            return None
        path = request.path
        if path.startswith("/static/"):
            return None
        if path in PUBLIC_PATHS:
            return None
        if is_authenticated():
            return None
        if path.startswith("/api/"):
            return jsonify({"error": "No autorizado"}), 401
        return redirect(url_for("views.login", next=path))

    @app.after_request
    def _security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self' https://api.open-meteo.com; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if not Config.DEBUG and request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def require_auth_json(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"error": "No autorizado"}), 401
        return f(*args, **kwargs)

    return wrapped


def generate_secret(length: int = 48) -> str:
    return secrets.token_urlsafe(length)
