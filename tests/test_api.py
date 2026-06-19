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
                "kg": 100,
                "costo_total_logistica": 3000,
                "precio_venta_total": 55_000,
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
                "kg": 9999,
                "costo_total_logistica": 0,
                "precio_venta_total": 1000,
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
                "kg": 50,
                "costo_total_logistica": 500,
                "precio_venta_total": 50_000,
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
