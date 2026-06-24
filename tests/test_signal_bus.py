"""Tests del bus de señales SSE (pub/sub en memoria)."""
import pytest

from app.services.signal_bus import broadcast_refresh, subscribe, subscriber_count, unsubscribe


def test_broadcast_isolated_by_empresa():
    q1 = subscribe(1)
    q2 = subscribe(2)
    try:
        delivered = broadcast_refresh(1, reason="test", source_device_id="dev-a")
        assert delivered == 1
        msg = q1.get_nowait()
        assert msg["event"] == "refrescar"
        assert msg["empresa_id"] == 1
        assert msg["source_device_id"] == "dev-a"
        assert q2.empty()
    finally:
        unsubscribe(1, q1)
        unsubscribe(2, q2)


def test_unsubscribe_stops_delivery():
    q = subscribe(5)
    unsubscribe(5, q)
    assert broadcast_refresh(5) == 0
    assert subscriber_count(5) == 0


def test_api_stream_sse_format(client):
    resp = client.get("/api/stream")
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    chunk = next(resp.response)
    assert b": connected" in chunk


def test_sync_push_broadcasts_on_success(client, tenant_db):
    inbox = subscribe(1)
    try:
        payload = {
            "device_id": "dev-broadcast",
            "operations": [{
                "op_id": "op-test-broadcast-1",
                "entity": "cliente",
                "entity_uuid": "uuid-cliente-broadcast-1",
                "action": "CREATE",
                "payload": {
                    "uuid": "uuid-cliente-broadcast-1",
                    "nombre": "Cliente Radio",
                    "scoring": "A",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                },
                "updated_at_utc": "2026-01-01T00:00:00+00:00",
            }],
        }
        resp = client.post("/api/sync/push", json=payload)
        assert resp.status_code == 200
        assert resp.get_json().get("acked")

        msg = inbox.get(timeout=1)
        assert msg["event"] == "refrescar"
        assert msg["source_device_id"] == "dev-broadcast"
    finally:
        unsubscribe(1, inbox)


@pytest.fixture()
def tenant_db(tmp_path):
    from app.config import Config
    from app.database import init_db

    Config.DB_PATH = str(tmp_path / "signal_sync.db")
    Config.TESTING = True
    Config.DATABASE_URL = ""
    init_db()
    yield
