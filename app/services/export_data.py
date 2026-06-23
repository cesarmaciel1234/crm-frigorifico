"""Exportación completa de datos del tenant."""
from __future__ import annotations

from datetime import datetime, timezone

from app.database import get_db
from app.services.users import get_empresa_config

EXPORT_VERSION = 2

TABLES = [
    "entidades_bancarias",
    "clientes",
    "compras_bulk",
    "operaciones_financieras",
    "remitos_carga",
    "pagos_cuotas",
    "pagos_clientes",
    "aplicacion_pagos",
    "remitos_fracciones",
    "perdidas_acumuladas",
    "ventas_mostrador",
    "auditoria_operaciones",
]


def _table_rows(conn, table: str) -> list[dict]:
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def export_all_data() -> dict:
    with get_db() as conn:
        payload = {
            "version": EXPORT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "empresa": get_empresa_config(),
        }
        for table in TABLES:
            payload[table] = _table_rows(conn, table)
    return payload
