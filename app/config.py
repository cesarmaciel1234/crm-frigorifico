import os


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
    AUDIT_DELETE_PASSWORD = os.environ.get("AUDIT_DELETE_PASSWORD", "2094")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "auto") == "1" or (
        os.environ.get("SESSION_COOKIE_SECURE", "auto") == "auto"
        and os.environ.get("RENDER") == "true"
    )
    PERMANENT_SESSION_LIFETIME = 86400 * 7  # 7 días
