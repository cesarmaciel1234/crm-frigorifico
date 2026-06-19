from app.database import get_db

def list_bancos() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, nombre, limite FROM entidades_bancarias ORDER BY nombre"
        ).fetchall()
    return [dict(r) for r in rows]
