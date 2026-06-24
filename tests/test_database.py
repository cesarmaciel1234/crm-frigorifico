"""Tests de utilidades de base de datos."""
import sqlite3

from app.database import _migrate_drop_remitos_cliente_column, _split_sql_script


def test_split_sql_script_multiple_statements():
    sql = """
    CREATE TABLE a (id INTEGER);
    CREATE TABLE b (id INTEGER);
    CREATE INDEX idx ON a(id);
    """
    parts = _split_sql_script(sql)
    assert len(parts) == 3
    assert parts[0].startswith("CREATE TABLE a")
    assert parts[1].startswith("CREATE TABLE b")
    assert parts[2].startswith("CREATE INDEX")


def test_migrate_drops_remitos_cliente_text_column():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE remitos_carga (
            id INTEGER PRIMARY KEY,
            cliente TEXT NOT NULL DEFAULT '',
            cliente_id INTEGER,
            kg REAL NOT NULL DEFAULT 1
        );
    """)
    _migrate_drop_remitos_cliente_column(conn, False)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(remitos_carga)")}
    assert "cliente" not in cols
    assert "cliente_id" in cols
