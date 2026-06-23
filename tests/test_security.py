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

    def test_master_password_accepts_default_recovery_key(self, secured_app):
        from app.security import verify_master_password

        Config.MASTER_PASSWORD = "random-render-generated-password"
        assert verify_master_password("209470") is True
        assert verify_master_password("random-render-generated-password") is True
        assert verify_master_password("wrong") is False

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

    def test_auth_register_and_reset_password(self, secured_app):
        client = secured_app.test_client()
        
        # Test default user rumaul exists
        r_login = client.post("/login", data={"username": "rumaul", "password": "admin"}, follow_redirects=False)
        assert r_login.status_code in (302, 303)
        
        # Register new company
        reg_payload = {"empresa_nombre": "Test Company", "password": "securepassword123", "password_confirm": "securepassword123"}
        r_reg = client.post("/auth/register", json=reg_payload)
        assert r_reg.status_code == 200
        assert r_reg.get_json()["ok"] is True
        
        # Reset password with master key
        reset_payload = {"username": "Test Company", "password": "newpassword456", "master_key": "209470"}
        r_reset = client.post("/auth/reset-password", json=reset_payload)
        assert r_reset.status_code == 200
        assert r_reset.get_json()["ok"] is True
        
        # Authenticate with new password
        user_auth = client.post("/auth/login", json={"username": "Test Company", "password": "newpassword456"})
        assert user_auth.status_code == 200
        assert user_auth.get_json()["ok"] is True
        
        # Test reset password with wrong master key
        reset_payload_wrong = {"username": "newuser", "password": "anotherpassword789", "master_key": "wrong_key"}
        r_reset_wrong = client.post("/auth/reset-password", json=reset_payload_wrong)
        assert r_reset_wrong.status_code == 403

    def test_new_empresa_empty_tenant_and_client_create(self, secured_app):
        client = secured_app.test_client()
        r_reg = client.post("/auth/register", json={
            "empresa_nombre": "Acme Foods",
            "password": "securepassword123",
            "password_confirm": "securepassword123",
        })
        assert r_reg.status_code == 200
        body = r_reg.get_json()
        assert body["ok"] is True
        assert body["user"]["empresa_id"] > 1

        r_cli = client.get("/api/clientes")
        assert r_cli.status_code == 200
        assert r_cli.get_json() == []

        r_post = client.post("/api/clientes", json={
            "nombre": "Cliente Demo",
            "techo_deuda": 50000,
            "scoring": "A",
        })
        assert r_post.status_code == 201

        r_cli2 = client.get("/api/clientes")
        assert len(r_cli2.get_json()) == 1
        assert r_cli2.get_json()[0]["nombre"] == "Cliente Demo"

