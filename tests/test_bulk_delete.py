"""Eliminación de lotes bulk mal cargados."""
import pytest

from app.database import get_db
from app.services.bulk import eliminar_lote_bulk, registrar_lote_bulk


def test_eliminar_lote_bulk_sin_ventas(app):
    with app.app_context():
        lote_id = registrar_lote_bulk(1000, 9000)
        result = eliminar_lote_bulk(lote_id)
        assert result["id"] == lote_id
        with get_db() as conn:
            row = conn.execute("SELECT 1 FROM compras_bulk WHERE id = ?", (lote_id,)).fetchone()
        assert row is None


def test_eliminar_lote_bulk_con_remitos_bloqueado(app):
    with app.app_context():
        lote_id = registrar_lote_bulk(1000, 9000)
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO remitos_fracciones (remito_id, lote_id, kg_descontados, costo_porcion, costo_logistica_porcion)
                VALUES (1, ?, 100, 900, 0)
                """,
                (lote_id,),
            )
        with pytest.raises(ValueError, match="remitos"):
            eliminar_lote_bulk(lote_id)


def test_eliminar_lote_bulk_inexistente(app):
    with app.app_context():
        with pytest.raises(ValueError, match="no encontrado"):
            eliminar_lote_bulk(99999)
