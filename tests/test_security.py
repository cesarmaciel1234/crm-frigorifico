"""Tests de seguridad — autenticación y endpoints protegidos."""
import os

import pytest

from app.config import Config


@pytest.fixture()
def secured_app(tmp_path):
    db_path = tmp_path / "test_secured.db"
    Config.DB_PATH = str(db_path)
    Config.TESTING = False
    Config.MT_API_KEY = os.environ.get("MT_API_KEY", "ci-test-key-not-for-production")
    Config.MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", os.environ.get("AUDIT_DELETE_PASSWORD", "ci-audit-pw"))
    Config.AUDIT_DELETE_PASSWORD = Config.MASTER_PASSWORD
    Config.SECRET_KEY = os.environ.get("SECRET_KEY", "ci-secret-key")

    from app import create_app

    return create_app()


class TestSecurity:
    def test_health_public(self, secured_app):
        client = secured_app.test_client()
        assert client.get("/health").status_code == 200

    def test_api_blocked_without_auth(self, secured_app):
        client = secured_app.test_client()
        assert client.get("/api/dashboard").status_code == 401

    def test_api_with_api_key(self, secured_app):
        client = secured_app.test_client()
        r = client.get("/api/dashboard", headers={"X-API-Key": Config.MT_API_KEY})
        assert r.status_code == 200

    def test_api_with_bearer_token(self, secured_app):
        client = secured_app.test_client()
        r = client.get(
            "/api/dashboard",
            headers={"Authorization": f"Bearer {Config.MT_API_KEY}"},
        )
        assert r.status_code == 200

    def test_login_sets_session(self, secured_app):
        client = secured_app.test_client()
        r = client.post("/login", data={"api_key": Config.MT_API_KEY}, follow_redirects=False)
        assert r.status_code in (302, 303)
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True

    def test_health_ready(self, secured_app):
        client = secured_app.test_client()
        r = client.get("/health/ready")
        assert r.status_code == 200
        assert r.get_json()["db"] == "ok"

    def test_delete_op_requires_password(self, secured_app):
        client = secured_app.test_client()
        headers = {"X-API-Key": Config.MT_API_KEY, "Content-Type": "application/json"}
        r_create = client.post(
            "/api/operaciones",
            json={"alias": "Del Test", "tipo": "otro", "recibido": 1000, "pagar": 1100, "meses": 1},
            headers=headers,
        )
        assert r_create.status_code == 201
        op_id = r_create.get_json()["id"]
        r_del = client.delete(f"/api/operaciones/{op_id}", json={"password": "wrong"}, headers=headers)
        assert r_del.status_code == 403

    def test_api_v1_alias(self, secured_app):
        client = secured_app.test_client()
        headers = {"X-API-Key": Config.MT_API_KEY}
        r_legacy = client.get("/api/dashboard", headers=headers)
        r_v1 = client.get("/api/v1/dashboard", headers=headers)
        assert r_legacy.status_code == 200
        assert r_v1.status_code == 200
        assert r_legacy.get_json().keys() == r_v1.get_json().keys()

    def test_audit_delete_requires_password(self, secured_app):
        client = secured_app.test_client()
        headers = {"X-API-Key": Config.MT_API_KEY, "Content-Type": "application/json"}
        r = client.delete("/api/auditoria/99999", json={"password": "wrong"}, headers=headers)
        assert r.status_code == 403

    def test_security_headers(self, secured_app):
        client = secured_app.test_client()
        r = client.get("/health")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"

    def test_ventas_sync_idempotent(self, secured_app):
        client = secured_app.test_client()
        headers = {"X-API-Key": Config.MT_API_KEY, "Content-Type": "application/json"}
        payload = {
            "ventas": [
                {"offline_id": 42, "producto": "Bife", "monto": 15000, "tipo_pago": "CONTADO"}
            ]
        }
        r1 = client.post("/api/ventas_mostrador/sync", json=payload, headers=headers)
        assert r1.status_code == 201
        assert r1.get_json()["count"] == 1

        r2 = client.post("/api/ventas_mostrador/sync", json=payload, headers=headers)
        assert r2.status_code == 201
        assert r2.get_json()["count"] == 1

        lista = client.get("/api/ventas_mostrador", headers=headers).get_json()
        assert len([v for v in lista if v.get("producto") == "Bife"]) == 1
