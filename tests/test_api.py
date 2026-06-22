"""Tests de API HTTP — smoke de producción."""
from datetime import date, timedelta


class TestAPI:
    def test_index_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"Master Total" in r.data

    def test_dashboard(self, client):
        r = client.get("/api/dashboard")
        assert r.status_code == 200
        data = r.get_json()
        assert "estrategia" in data
        assert "enemigos" in data
        assert "totales" in data
        activo = data["estrategia"]["activo"]
        assert "deuda_neta" in activo
        assert "interes_neto" in activo
        assert activo["deuda_neta"] + activo["interes_neto"] == activo["deuda_real"]

    def test_crear_operacion_tarjeta(self, client):
        payload = {
            "alias": "Test Visa",
            "tipo": "tarjeta",
            "recibido": 100_000,
            "pagar": 118_000,
            "fecha_cierre": date.today().isoformat(),
            "fecha_vencimiento": (date.today() + timedelta(days=30)).isoformat(),
            "cuotas": 6,
        }
        r = client.post("/api/operaciones", json=payload)
        assert r.status_code == 201
        body = r.get_json()
        assert body["cfr"] is not None
        assert body["urgente"] is False

    def test_crear_bulk_y_remito(self, client):
        r1 = client.post("/api/bulk", json={"kg_totales": 500, "costo_total_bulk": 400_000})
        assert r1.status_code == 201

        r2 = client.post(
            "/api/remitos",
            json={
                "cliente": "Test Cliente API",
                "tipo_corte": "Novillo",
                "kg": 100,
                "precio_por_kg": 550,
                "plazo_cobro_dias": 21,
            },
        )
        assert r2.status_code == 201
        assert "costo_carne" in r2.get_json()

    def test_remito_sin_stock_400(self, client):
        r = client.post(
            "/api/remitos",
            json={
                "cliente": "Sin Stock",
                "tipo_corte": "Novillo",
                "kg": 9999,
                "precio_por_kg": 100,
                "plazo_cobro_dias": 7,
            },
        )
        assert r.status_code == 400

    def test_remito_supera_techo_credito(self, client):
        client.post("/api/bulk", json={"kg_totales": 500, "costo_total_bulk": 400_000})
        client.post(
            "/api/clientes",
            json={"nombre": "Cliente Chico", "techo_deuda": 10_000, "scoring": "C"},
        )
        r = client.post(
            "/api/remitos",
            json={
                "cliente": "Cliente Chico",
                "tipo_corte": "Novillo",
                "kg": 50,
                "precio_por_kg": 1000,
                "plazo_cobro_dias": 14,
            },
        )
        assert r.status_code == 400

    def test_historial_pagos(self, client):
        assert client.get("/api/historial-pagos").status_code == 200

    def test_clientes_crud(self, client):
        r = client.post(
            "/api/clientes",
            json={"nombre": "API Cliente", "techo_deuda": 300_000, "scoring": "B"},
        )
        assert r.status_code == 201
        lista = client.get("/api/clientes").get_json()
        assert any(c["nombre"] == "API Cliente" for c in lista)

    def test_bancos(self, client):
        r = client.post("/api/bancos", json={"nombre": "Banco Test", "limite": 50_000})
        assert r.status_code == 201
        assert client.get("/api/bancos").status_code == 200

    def test_manifest_y_sw(self, client):
        assert client.get("/manifest.json").status_code == 200
        assert client.get("/sw.js").status_code == 200

    def test_pos_offline_page(self, client):
        r = client.get("/pos")
        assert r.status_code == 200
        assert b"POS Carnicer" in r.data
        assert b"dexie.min.js" in r.data

    def test_ventas_mostrador_sync(self, client):
        r = client.post(
            "/api/ventas_mostrador/sync",
            json={
                "ventas": [
                    {"offline_id": 1, "producto": "Bife", "monto": 15000, "tipo_pago": "CONTADO"}
                ]
            },
        )
        assert r.status_code == 201
        assert r.get_json()["count"] == 1

    def test_saldos_y_eliminaciones_autorizadas(self, client):
        rc = client.post("/api/clientes", json={"nombre": "Test Admin", "techo_deuda": 500000})
        assert rc.status_code == 201
        cid = rc.get_json()["id"]

        r_saldo_fail = client.post(f"/api/clientes/{cid}/saldo-inicial", json={"saldo_inicial": 10000, "password": "wrong"})
        assert r_saldo_fail.status_code == 403

        r_saldo_ok = client.post(f"/api/clientes/{cid}/saldo-inicial", json={"saldo_inicial": 10000, "password": "test-master-pw"})
        assert r_saldo_ok.status_code == 200
        assert r_saldo_ok.get_json()["saldo_actual"] == 10000

        client.post("/api/bulk", json={"kg_totales": 100, "costo_total_bulk": 80000})
        r_rem = client.post("/api/remitos", json={
            "cliente": "Test Admin",
            "tipo_corte": "media res",
            "kg": 20,
            "precio_por_kg": 1000,
            "plazo_cobro_dias": 30
        })
        assert r_rem.status_code == 201
        rid = r_rem.get_json()["id"]

        r_cli_det = client.get(f"/api/clientes/{cid}").get_json()
        assert r_cli_det["saldo_actual"] == 30000
        
        r_bulk = client.get("/api/bulk").get_json()
        assert r_bulk[0]["kg_remanentes"] == 80

        client.post(f"/api/remitos/{rid}/cobrar", json={"monto_pagado": 5000})
        r_cli_det2 = client.get(f"/api/clientes/{cid}").get_json()
        assert r_cli_det2["saldo_actual"] == 25000

        r_reset_fail = client.post(f"/api/remitos/{rid}/reset-pago", json={"password": "wrong"})
        assert r_reset_fail.status_code == 403

        r_reset_ok = client.post(f"/api/remitos/{rid}/reset-pago", json={"password": "test-master-pw"})
        assert r_reset_ok.status_code == 200
        r_cli_det3 = client.get(f"/api/clientes/{cid}").get_json()
        assert r_cli_det3["saldo_actual"] == 30000

        r_del_rem_fail = client.delete(f"/api/remitos/{rid}", json={"password": "wrong"})
        assert r_del_rem_fail.status_code == 403

        r_del_rem_ok = client.delete(f"/api/remitos/{rid}", json={"password": "test-master-pw"})
        assert r_del_rem_ok.status_code == 200
        
        r_cli_det4 = client.get(f"/api/clientes/{cid}").get_json()
        assert r_cli_det4["saldo_actual"] == 10000
        r_bulk2 = client.get("/api/bulk").get_json()
        assert r_bulk2[0]["kg_remanentes"] == 100

        r_del_cli = client.delete(f"/api/clientes/{cid}", json={"password": "test-master-pw"})
        assert r_del_cli.status_code == 200
        r_cli_det5 = client.get(f"/api/clientes/{cid}")
        assert r_cli_det5.status_code == 404

