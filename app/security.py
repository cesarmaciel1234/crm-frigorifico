"""Autenticación, roles, headers de seguridad y utilidades de hardening."""
import hmac
import secrets
import time
from datetime import datetime, timezone
from functools import wraps

from flask import jsonify, redirect, request, session, url_for

from app.config import Config

PUBLIC_PATHS = frozenset({"/login", "/auth/login", "/auth/register", "/auth/reset-password", "/health", "/health/ready", "/manifest.json", "/sw.js", "/debug-db", "/limpiar-sistema-ahora"})

FAILED_LOGINS = {}  # username -> {"count": int, "blocked_until": float}


def check_login_rate_limit(username: str) -> tuple[bool, str]:
    if not username:
        return True, ""
    username = username.strip().lower()
    record = FAILED_LOGINS.get(username)
    if record and record["count"] >= 5:
        now = time.time()
        time_left = int(record["blocked_until"] - now)
        if time_left > 0:
            return False, f"Usuario bloqueado temporalmente por seguridad. Intente de nuevo en {time_left} segundos."
        else:
            record["count"] = 0
    return True, ""


def record_login_failure(username: str) -> None:
    if not username:
        return
    username = username.strip().lower()
    now = time.time()
    record = FAILED_LOGINS.setdefault(username, {"count": 0, "blocked_until": 0.0})
    record["count"] += 1
    if record["count"] >= 5:
        record["blocked_until"] = now + 60  # Bloqueado por 60 segundos
    else:
        record["blocked_until"] = 0.0


def record_login_success(username: str) -> None:
    if not username:
        return
    username = username.strip().lower()
    if username in FAILED_LOGINS:
        FAILED_LOGINS[username]["count"] = 0
        FAILED_LOGINS[username]["blocked_until"] = 0.0


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


DEFAULT_MASTER_PASSWORD = "209470"


def _allowed_master_passwords() -> set[str]:
    """Claves válidas: la de entorno y la clave universal de recuperación."""
    allowed = {DEFAULT_MASTER_PASSWORD}
    pwd = Config.master_password()
    if pwd:
        allowed.add(pwd.strip())
    return allowed


def verify_master_password(value: str | None) -> bool:
    if not value:
        return False
    candidate = str(value).strip()
    for pwd in _allowed_master_passwords():
        if hmac.compare_digest(candidate, pwd):
            return True
    return False


def verify_audit_password(value: str | None) -> bool:
    return verify_master_password(value)


def is_authenticated() -> bool:
    if not auth_enabled():
        return True
    if session.get("authenticated"):
        return True
    return verify_api_key(_api_key_from_request())


def current_role() -> str:
    if not auth_enabled():
        return "admin"
    role = session.get("role")
    if role:
        return role
    if session.get("authenticated") or verify_api_key(_api_key_from_request()):
        return session.get("role") or "admin"
    return ""


def role_at_least(required: str) -> bool:
    from app.services.users import ROLE_RANK

    have = ROLE_RANK.get(current_role(), -1)
    need = ROLE_RANK.get(required, 99)
    return have >= need


def set_session_user(user: dict, *, auth_method: str = "password") -> None:
    session.permanent = True
    session["authenticated"] = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["empresa_id"] = user.get("empresa_id") or 1
    session["auth_method"] = auth_method
    session["last_activity"] = datetime.now(timezone.utc).timestamp()


def set_session_api_key() -> None:
    session.permanent = True
    session["authenticated"] = True
    session["user_id"] = None
    session["username"] = "api_key"
    session["role"] = "admin"
    session["empresa_id"] = 1
    session["auth_method"] = "api_key"
    session["last_activity"] = datetime.now(timezone.utc).timestamp()


def require_master_password_in_request() -> tuple[bool, str]:
    d = request.get_json(silent=True) or {}
    pwd = (
        d.get("password")
        or d.get("master_password")
        or request.args.get("password")
        or request.headers.get("X-Master-Password")
    )
    if not verify_master_password(pwd):
        return False, "Contraseña maestra incorrecta"
    return True, ""


def register_security(app):
    @app.before_request
    def _enforce_session_timeout():
        if not auth_enabled():
            return None
        path = request.path
        if path.startswith("/static/") or path in PUBLIC_PATHS:
            return None
        if not session.get("authenticated"):
            return None
            
        now = datetime.now(timezone.utc).timestamp()
        last_act = session.get("last_activity")
        TIMEOUT_SECONDS = 3600  # 60 minutos
        
        if last_act and (now - last_act > TIMEOUT_SECONDS):
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Sesión expirada por inactividad"}), 401
            return redirect(url_for("views.login", error="Sesión expirada por inactividad"))
            
        session["last_activity"] = now
        return None

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

    @app.before_request
    def _enforce_role_writes():
        if not auth_enabled() or request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        if not request.path.startswith("/api/"):
            return None
        if current_role() == "visor":
            return jsonify({"error": "Tu rol es solo lectura"}), 403
        return None

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
        
        # Evitar almacenamiento en caché de respuestas de la API por parte del navegador
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            
        return response

    @app.errorhandler(500)
    def _generic_500(e):
        app.logger.exception("Error interno: %s", e)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Error interno del servidor"}), 500
        return "Error interno", 500


def require_auth_json(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"error": "No autorizado"}), 401
        return f(*args, **kwargs)

    return wrapped


def require_role(min_role: str):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not is_authenticated():
                return jsonify({"error": "No autorizado"}), 401
            if not role_at_least(min_role):
                return jsonify({"error": "Permiso denegado"}), 403
            return f(*args, **kwargs)

        return wrapped

    return decorator


def require_master_password(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        ok, msg = require_master_password_in_request()
        if not ok:
            return jsonify({"error": msg}), 403
        if not role_at_least("admin"):
            return jsonify({"error": "Solo administradores pueden realizar esta acción"}), 403
        return f(*args, **kwargs)

    return wrapped


def generate_secret(length: int = 48) -> str:
    return secrets.token_urlsafe(length)
