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

        # Intentar crear un cliente con el mismo nombre debe fallar con 400
        r_dup = client.post(
            "/api/clientes",
            json={"nombre": "API Cliente", "techo_deuda": 150_000, "scoring": "A"},
        )
        assert r_dup.status_code == 400
        assert "error" in r_dup.get_json()

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

    def test_api_finanzas_endpoints(self, client):
        r_aging = client.get("/api/finanzas/aging")
        assert r_aging.status_code == 200
        data_aging = r_aging.get_json()
        assert "totales" in data_aging
        assert "clientes" in data_aging

        r_margenes = client.get("/api/finanzas/margenes")
        assert r_margenes.status_code == 200
        data_margenes = r_margenes.get_json()
        assert isinstance(data_margenes, list)

    def test_clientes_delete_with_payments_and_update(self, client):
        # 1. Crear un cliente
        r_create = client.post(
            "/api/clientes",
            json={"nombre": "Cliente Deletable", "techo_deuda": 500_000, "scoring": "A", "saldo_inicial": 20000},
        )
        assert r_create.status_code == 201
        cid = r_create.get_json()["id"]

        # Crear lote y remito para el cliente para tener una factura pendiente
        client.post("/api/bulk", json={"kg_totales": 100, "costo_total_bulk": 80000})
        client.post("/api/remitos", json={
            "cliente": "Cliente Deletable",
            "tipo_corte": "media res",
            "kg": 20,
            "precio_por_kg": 1000,
            "plazo_cobro_dias": 30
        })

        # 2. Registrar un pago global para el cliente
        r_pago = client.post(
            f"/api/clientes/{cid}/cobrar",
            json={"monto_pagado": 5000}
        )
        assert r_pago.status_code == 200

        # 3. Comprobar que el cliente tiene el pago y saldo actualizado (20000 inicial + 20000 remito - 5000 pago = 35000)
        det = client.get(f"/api/clientes/{cid}").get_json()
        assert det["saldo_actual"] == 35000

        # 4. Probar la actualización del cliente vía PUT /api/clientes/<cid>
        r_put = client.put(
            f"/api/clientes/{cid}",
            json={
                "nombre": "Cliente Actualizado",
                "techo_deuda": 600000,
                "scoring": "B",
                "telefono": "123456789",
                "direccion": "Nueva Calle 123",
                "saldo_inicial": 10000
            }
        )
        assert r_put.status_code == 200
        
        # Comprobar que los datos se actualizaron en base de datos
        det_updated = client.get(f"/api/clientes/{cid}").get_json()
        assert det_updated["nombre"] == "Cliente Actualizado"
        assert det_updated["techo_deuda"] == 600000
        assert det_updated["scoring"] == "B"
        assert det_updated["telefono"] == "123456789"
        assert det_updated["direccion"] == "Nueva Calle 123"
        # El saldo actual debe recalcularse (saldo_inicial = 10000 + remito = 20000 - pago = 5000 = 25000)
        assert det_updated["saldo_actual"] == 25000

        # 5. Intentar eliminar el cliente (que tiene pagos registrados)
        r_del = client.delete(f"/api/clientes/{cid}", json={"password": "test-master-pw"})
        assert r_del.status_code == 200

        # Verificar que el cliente ya no existe
        assert client.get(f"/api/clientes/{cid}").status_code == 404



