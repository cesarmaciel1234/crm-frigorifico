"""Tests de utilidades de base de datos."""
from app.database import _split_sql_script


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
