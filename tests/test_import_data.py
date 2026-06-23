"""Tests de importación de backups."""
import json

import pytest

from app.config import Config
from app.database import get_db
from app.services.import_data import import_all_data
from app.services.users import get_empresa_config, normalize_empresa_config


@pytest.fixture()
def tenant_db(tmp_path):
    db_path = tmp_path / "import_test.db"
    Config.DB_PATH = str(db_path)
    Config.TESTING = True
    Config.DATABASE_URL = ""
    from app.database import init_db

    init_db()
    yield db_path


def test_normalize_empresa_maps_nombre_to_razon_social():
    out = normalize_empresa_config({"nombre": "Rumaul", "cuit": "20-12345678-9"})
    assert out["razon_social"] == "Rumaul"
    assert out["cuit"] == "20-12345678-9"


def test_import_restores_enemigos_and_empresa(tenant_db):
    payload = {
        "version": 2,
        "empresa": {"nombre": "Rumaul", "cuit": "20-11111111-1"},
        "clientes": [{"id": 1, "nombre": "Maciel", "scoring": "A", "techo_deuda": 1000, "saldo_actual": 100}],
        "enemigos": [{
            "id": 1, "alias": "MP Plus", "tipo": "tarjeta",
            "recibido": 1000, "pagar": 1100, "meses": 1,
            "fecha_cierre": "2026-06-23", "fecha_vencimiento": "2026-06-23", "cuotas": 1,
        }],
        "bulk": [{"id": 1, "fecha": "2026-06-22", "kg_totales": 100, "kg_remanentes": 90, "costo_total_bulk": 900}],
        "remitos": [{
            "id": 1, "fecha": "2026-06-22", "cliente": "Maciel", "cliente_id": 1,
            "kg": 10, "precio_venta_total": 100, "costo_total_logistica": 10, "costo_carne": 9,
            "plazo_cobro_dias": 7, "pagado": 0, "monto_pagado": 0,
        }],
    }
    import_all_data(payload)

    with get_db() as conn:
        ops = conn.execute("SELECT alias FROM operaciones_financieras").fetchall()
        clientes = conn.execute("SELECT nombre FROM clientes").fetchall()
    assert len(ops) == 1
    assert ops[0]["alias"] == "MP Plus"
    assert len(clientes) == 1
    empresa = get_empresa_config()
    assert empresa["razon_social"] == "Rumaul"


def test_import_cache_snapshot_v1(tenant_db):
    snapshot = {
        "version": "cache_snapshot_v1",
        "appData": {
            "enemigos": [{"id": 2, "alias": "Visa", "tipo": "tarjeta", "recibido": 500, "pagar": 550, "meses": 1, "cuotas": 1}],
            "clientes": [{"id": 3, "nombre": "Cliente X", "scoring": "A", "techo_deuda": 1, "saldo_actual": 0}],
        },
    }
    import_all_data(snapshot)
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM operaciones_financieras").fetchone()["c"]
    assert count == 1
