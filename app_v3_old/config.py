import os

class Config:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DB_PATH = os.path.join(BASE_DIR, "master_total.db")
    SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
