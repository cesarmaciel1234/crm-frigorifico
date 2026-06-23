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
    summary = import_all_data(payload)
    assert summary["tablas"]["operaciones_financieras"]["insertados"] == 1

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
    summary = import_all_data(snapshot)
    assert summary["tablas"]["operaciones_financieras"]["insertados"] == 1


def test_import_merges_fullbackup_with_appdata_enemigos(tenant_db):
    snapshot = {
        "version": "cache_snapshot_v1",
        "fullBackup": {
            "version": 2,
            "clientes": [{"id": 1, "nombre": "Maciel", "scoring": "A", "techo_deuda": 1000, "saldo_actual": 0}],
            "operaciones_financieras": [],
        },
        "appData": {
            "enemigos": [{"id": 5, "alias": "MP", "tipo": "tarjeta", "recibido": 100, "pagar": 110, "meses": 1, "cuotas": 1}],
        },
    }
    summary = import_all_data(snapshot)
    assert summary["tablas"]["operaciones_financieras"]["insertados"] == 1
    assert summary["tablas"]["clientes"]["insertados"] == 1


def test_import_auditoria_with_extended_columns(tenant_db):
    payload = {
        "version": 2,
        "clientes": [{"id": 1, "nombre": "A", "scoring": "A", "techo_deuda": 1, "saldo_actual": 0}],
        "auditoria": [{
            "id": 1,
            "operacion_id": 10,
            "alias": "MP Plus",
            "accion": "CREADO",
            "monto": 1000,
            "fecha": "2026-06-23",
            "entidad": "operacion",
            "entidad_id": 10,
            "usuario": "admin",
            "detalle": "Alta manual",
        }],
    }
    summary = import_all_data(payload)
    assert summary["tablas"]["auditoria_operaciones"]["insertados"] == 1

    with get_db() as conn:
        row = conn.execute(
            "SELECT alias, entidad, usuario, detalle FROM auditoria_operaciones WHERE id = 1"
        ).fetchone()
    assert row["alias"] == "MP Plus"
    assert row["entidad"] == "operacion"
    assert row["usuario"] == "admin"
    assert row["detalle"] == "Alta manual"


def test_import_replaces_existing_rows(tenant_db):
    seed = {
        "version": 2,
        "clientes": [{"id": 1, "nombre": "Viejo", "scoring": "A", "techo_deuda": 1, "saldo_actual": 0}],
        "operaciones_financieras": [{
            "id": 1, "alias": "Vieja", "tipo": "tarjeta", "recibido": 100, "pagar": 110,
            "meses": 1, "cuotas": 1, "cuotas_pagadas": 0,
        }],
    }
    import_all_data(seed)
    replacement = {
        "version": 2,
        "clientes": [{"id": 2, "nombre": "Nuevo", "scoring": "A", "techo_deuda": 1, "saldo_actual": 0}],
        "enemigos": [{
            "id": 2, "alias": "Nueva", "tipo": "tarjeta", "recibido": 200, "pagar": 220,
            "meses": 1, "cuotas": 1,
        }],
    }
    import_all_data(replacement)
    with get_db() as conn:
        clientes = [r["nombre"] for r in conn.execute("SELECT nombre FROM clientes").fetchall()]
        ops = [r["alias"] for r in conn.execute("SELECT alias FROM operaciones_financieras").fetchall()]
    assert clientes == ["Nuevo"]
    assert ops == ["Nueva"]

    payload = {
        "version": 2,
        "enemigos": [
            {"id": 1, "alias": "OK", "tipo": "tarjeta", "recibido": 100, "pagar": 110, "meses": 1, "cuotas": 1},
            {"id": 2, "alias": "Bad", "tipo": "tarjeta", "recibido": 0, "pagar": 0, "meses": 1, "cuotas": 1},
        ],
        "clientes": [{"id": 1, "nombre": "A", "scoring": "A", "techo_deuda": 1, "saldo_actual": 0}],
    }
    summary = import_all_data(payload)
    assert summary["tablas"]["operaciones_financieras"]["insertados"] == 1
    assert summary["tablas"]["operaciones_financieras"]["omitidos"] == 0
