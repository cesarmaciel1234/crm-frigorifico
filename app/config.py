import os
from pathlib import Path

_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_file)
    except ImportError:
        pass


FORBIDDEN_SECRETS = frozenset({"2094", "changeme", "admin", "password", ""})


class Config:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DB_PATH = os.path.join(BASE_DIR, "master_total.db")
    DATABASE_URL = os.environ.get("DATABASE_URL")
    SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
    TESTING = os.environ.get("TESTING", "0") == "1"
    HOST = os.environ.get("MT_HOST", "0.0.0.0")
    PORT = int(os.environ.get("PORT", os.environ.get("MT_PORT", "5005")))

    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    MT_API_KEY = os.environ.get("MT_API_KEY", "")
    MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "") or os.environ.get("AUDIT_DELETE_PASSWORD", "")
    AUDIT_DELETE_PASSWORD = MASTER_PASSWORD  # alias retrocompatible
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_INITIAL_PASSWORD = os.environ.get("ADMIN_INITIAL_PASSWORD", "")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "auto") == "1" or (
        os.environ.get("SESSION_COOKIE_SECURE", "auto") == "auto"
        and os.environ.get("RENDER") == "true"
    )
    PERMANENT_SESSION_LIFETIME = 86400 * 7  # 7 días
    APP_VERSION = os.environ.get("APP_VERSION", "3.7")

    # Email informe empresarial (Resend HTTPS)
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") != "0"
    SMTP_USE_SSL = os.environ.get(
        "SMTP_USE_SSL",
        "1" if int(os.environ.get("SMTP_PORT", "587")) == 465 else "0",
    ) != "0"
    REPORT_EMAIL_TO = os.environ.get("REPORT_EMAIL_TO", "")
    REPORT_CRON_SECRET = os.environ.get("REPORT_CRON_SECRET", "")
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
    REPORT_TIMEZONE = os.environ.get("REPORT_TIMEZONE", "America/Argentina/Buenos_Aires")
    ON_RENDER = os.environ.get("RENDER") == "true"

    @classmethod
    def master_password(cls) -> str:
        return cls.MASTER_PASSWORD or ""

    @classmethod
    def is_production(cls) -> bool:
        return not cls.TESTING and not cls.DEBUG and (
            os.environ.get("RENDER") == "true" or bool(cls.DATABASE_URL)
        )

    @classmethod
    def validate_production(cls) -> list[str]:
        """Devuelve lista de errores de configuración en producción."""
        if not cls.is_production():
            return []
        errors = []
        # Permitimos valores vacíos para que start_produccion.py los auto-genere de manera segura en el arranque.
        # Solo fallamos si se configuran explícitamente valores inseguros del listado FORBIDDEN_SECRETS.
        if cls.SECRET_KEY and cls.SECRET_KEY in FORBIDDEN_SECRETS:
            errors.append("SECRET_KEY tiene un valor inseguro prohibido")
        if cls.MT_API_KEY and cls.MT_API_KEY in FORBIDDEN_SECRETS:
            errors.append("MT_API_KEY tiene un valor inseguro prohibido")
        if cls.master_password() and cls.master_password() in FORBIDDEN_SECRETS:
            errors.append("MASTER_PASSWORD tiene un valor inseguro prohibido")
        return errors
