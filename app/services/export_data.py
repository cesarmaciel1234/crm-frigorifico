"""Exportación completa de datos del tenant."""
from __future__ import annotations

from app.database import get_db
from app.services.clientes import list_clientes
from app.services.remitos import list_remitos
from app.services.bancos import list_bancos
from app.services.bulk import list_bulk_lots
from app.services.users import get_empresa_config
from app.services.audit import list_audit_log


def export_all_data() -> dict:
    with get_db() as conn:
        pagos_cuotas = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM pagos_cuotas ORDER BY id"
            ).fetchall()
        ]
        ventas = [
            dict(r)
            for r in conn.execute("SELECT * FROM ventas_mostrador ORDER BY id").fetchall()
        ]
        perdidas = [
            dict(r)
            for r in conn.execute("SELECT * FROM perdidas_acumuladas ORDER BY id").fetchall()
        ]
        operaciones = [
            dict(r)
            for r in conn.execute("SELECT * FROM operaciones_financieras ORDER BY id").fetchall()
        ]
    return {
        "empresa": get_empresa_config(),
        "clientes": list_clientes(),
        "remitos": list_remitos(10_000),
        "operaciones": operaciones,
        "bancos": list_bancos(),
        "bulk": list_bulk_lots(),
        "pagos_cuotas": pagos_cuotas,
        "ventas_mostrador": ventas,
        "perdidas": perdidas,
        "auditoria_reciente": list_audit_log(limit=500),
    }
