import os

class Config:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DB_PATH = os.path.join(BASE_DIR, "master_total.db")
    DATABASE_URL = os.environ.get("DATABASE_URL")
    SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
    HOST = os.environ.get("MT_HOST", "0.0.0.0")
    PORT = int(os.environ.get("PORT", os.environ.get("MT_PORT", "5005")))
