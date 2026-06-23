"""Tests del motor de sincronización."""
import pytest

from app.config import Config
from app.database import get_db, init_db
from app.services.sync_hub import MAX_NODOS, build_client_app_data, list_sync_nodos, save_sync_nodo


@pytest.fixture()
def tenant_db(tmp_path):
    Config.DB_PATH = str(tmp_path / "sync_test.db")
    Config.TESTING = True
    Config.DATABASE_URL = ""
    init_db()
    yield


def test_save_and_list_sync_nodo(tenant_db):
    snapshot = {"enemigos": [{"alias": "Visa", "recibido": 100}], "clientes": []}
    save_sync_nodo("dev-1", "Celular A", snapshot)
    nodos = list_sync_nodos()
    assert len(nodos) == 1
    assert nodos[0]["device_id"] == "dev-1"
    assert nodos[0]["etiqueta"] == "Celular A"


def test_max_nodos_rotates_oldest(tenant_db):
    for i in range(MAX_NODOS + 2):
        save_sync_nodo(f"dev-{i}", f"N{i}", {"i": i})
    nodos = list_sync_nodos()
    assert len(nodos) == MAX_NODOS
    ids = {n["device_id"] for n in nodos}
    assert "dev-0" not in ids
    assert "dev-1" not in ids
    assert f"dev-{MAX_NODOS + 1}" in ids


def test_build_client_app_data_shape(tenant_db):
    bundle = build_client_app_data()
    assert "enemigos" in bundle
    assert "clientes" in bundle
    assert "historialPagos" in bundle
    assert "bulk" in bundle
    assert "auditoria" in bundle
