"""Tests de sync push LWW y changelog."""
import pytest

from app.config import Config
from app.database import get_db, init_db
from app.services.sync_hub import apply_sync_operations, build_sync_pull_delta


@pytest.fixture()
def tenant_db(tmp_path):
    Config.DB_PATH = str(tmp_path / "sync_push_test.db")
    Config.TESTING = True
    Config.DATABASE_URL = ""
    init_db()
    yield


def _op(entity, entity_uuid, action, payload, ts="2026-06-23T12:00:00+00:00", op_id=None):
    import uuid
    return {
        "op_id": op_id or str(uuid.uuid4()),
        "entity": entity,
        "entity_uuid": entity_uuid,
        "action": action,
        "payload": payload,
        "updated_at_utc": ts,
    }


def test_push_operacion_idempotent(tenant_db):
    op_uuid = "11111111-1111-1111-1111-111111111111"
    op = _op("operacion", op_uuid, "CREATE", {
        "alias": "Visa", "tipo": "tarjeta", "recibido": 1000, "pagar": 1100, "meses": 1,
    })
    r1 = apply_sync_operations("dev-1", [op])
    assert op["op_id"] in r1["acked"]

    r2 = apply_sync_operations("dev-1", [op])
    assert op["op_id"] in r2["acked"]

    with get_db() as conn:
        rows = conn.execute(
            "SELECT alias FROM operaciones_financieras WHERE uuid = ?", (op_uuid,)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["alias"] == "Visa"


def test_push_cliente_and_pull_delta(tenant_db):
    cli_uuid = "22222222-2222-2222-2222-222222222222"
    op = _op("cliente", cli_uuid, "CREATE", {
        "nombre": "Distribuidora Norte",
        "scoring": "A",
        "techo_deuda": 100000,
        "saldo_inicial": 0,
    })
    apply_sync_operations("dev-2", [op])

    delta = build_sync_pull_delta(since=0)
    assert delta["version"] == "sync_bundle_v2"
    assert any(c["entity_uuid"] == cli_uuid for c in delta["changes"])

    delta2 = build_sync_pull_delta(since=delta["cursor"])
    assert delta2["changes"] == []


def test_lww_rejects_older_update(tenant_db):
    op_uuid = "33333333-3333-3333-3333-333333333333"
    apply_sync_operations("dev-3", [_op(
        "operacion", op_uuid, "CREATE",
        {"alias": "Nuevo", "tipo": "tarjeta", "recibido": 500, "pagar": 550, "meses": 1},
        ts="2026-06-23T14:00:00+00:00",
    )])
    apply_sync_operations("dev-4", [_op(
        "operacion", op_uuid, "CREATE",
        {"alias": "Viejo", "tipo": "tarjeta", "recibido": 500, "pagar": 550, "meses": 1},
        ts="2026-06-23T10:00:00+00:00",
    )])

    with get_db() as conn:
        row = conn.execute(
            "SELECT alias FROM operaciones_financieras WHERE uuid = ?", (op_uuid,)
        ).fetchone()
    assert row["alias"] == "Nuevo"
