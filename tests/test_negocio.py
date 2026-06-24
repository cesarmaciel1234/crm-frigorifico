"""Tests de flujo comercial — bulk, clientes, remitos."""
import pytest

from app.services.bulk import registrar_lote_bulk, list_bulk_lots, fraccionar_lote_fifo
from app.services.clientes import registrar_cliente, list_clientes, marcar_cliente_incobrable, registrar_pago_cliente_global
from app.services.remitos import list_remitos, marcar_remito_pagado, _estado_cobro
from app.database import get_db
from app.services.clientes import buscar_o_crear_cliente, recalcular_saldo_cliente


def _remito_via_conn(conn, cliente, kg, costo_log, venta, plazo=30):
    cid = buscar_o_crear_cliente(conn, cliente)
    costo_carne, fracs = fraccionar_lote_fifo(conn, kg)
    cur = conn.execute(
        """
        INSERT INTO remitos_carga
            (cliente_id, kg, costo_total_logistica, precio_venta_total,
             plazo_cobro_dias, costo_carne, pagado)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (cid, kg, costo_log, venta, plazo, costo_carne),
    )
    rid = cur.lastrowid
    for f in fracs:
        conn.execute(
            "INSERT INTO remitos_fracciones (remito_id, lote_id, kg_descontados, costo_porcion) VALUES (?,?,?,?)",
            (rid, f["lote_id"], f["kg_descontados"], f["costo_porcion"]),
        )
    recalcular_saldo_cliente(conn, cid)
    return rid, cid


class TestNegocio:
    def test_estado_cobro_remito(self):
        assert _estado_cobro(0) == "pendiente"
        assert _estado_cobro(0, 500) == "parcial"
        assert _estado_cobro(1) == "cobrado"
        assert _estado_cobro(2) == "incobrable"

    def test_bulk_fifo_descuenta_stock(self, db):
        registrar_lote_bulk(1000, 850_000)
        with get_db() as conn:
            costo, fracs = fraccionar_lote_fifo(conn, 200)
        assert costo == pytest.approx(170_000, abs=0.01)
        assert len(fracs) == 1
        lots = list_bulk_lots()
        assert lots[0]["kg_remanentes"] == pytest.approx(800, abs=0.01)

    def test_remito_sin_stock_falla(self, db):
        registrar_cliente("Cliente X", 500_000, "A")
        with pytest.raises(ValueError, match="Stock insuficiente"):
            with get_db() as conn:
                _remito_via_conn(conn, "Cliente X", 50, 1000, 60_000)

    def test_flujo_remito_cobro_saldo(self, db):
        registrar_cliente("Carniceria", 500_000, "A")
        registrar_lote_bulk(300, 240_000)
        with get_db() as conn:
            rid, cid = _remito_via_conn(conn, "Carniceria", 100, 2000, 80_000)
        clientes = list_clientes()
        c = next(x for x in clientes if x["nombre"] == "Carniceria")
        assert c["saldo_actual"] == pytest.approx(80_000, abs=0.01)

        marcar_remito_pagado(rid)
        c2 = next(x for x in list_clientes() if x["nombre"] == "Carniceria")
        assert c2["saldo_actual"] == pytest.approx(0, abs=0.01)

        rem = next(r for r in list_remitos() if r["id"] == rid)
        assert rem["pagado"] == 1
        assert rem["estado_cobro"] == "cobrado"

    def test_pago_global_fifo_factura_antigua(self, db):
        registrar_cliente("Fiado", 1_000_000, "A")
        registrar_lote_bulk(1000, 800_000)
        with get_db() as conn:
            rid1, cid = _remito_via_conn(conn, "Fiado", 100, 2000, 50_000)
            rid2, _ = _remito_via_conn(conn, "Fiado", 100, 2000, 80_000)
            conn.execute("UPDATE remitos_carga SET fecha = '2026-01-01' WHERE id = ?", (rid1,))
            conn.execute("UPDATE remitos_carga SET fecha = '2026-02-01' WHERE id = ?", (rid2,))

        registrar_pago_cliente_global(cid, 60_000)

        with get_db() as conn:
            r1 = dict(conn.execute(
                "SELECT monto_pagado, pagado FROM remitos_carga WHERE id = ?", (rid1,)
            ).fetchone())
            r2 = dict(conn.execute(
                "SELECT monto_pagado, pagado FROM remitos_carga WHERE id = ?", (rid2,)
            ).fetchone())

        assert r1["pagado"] == 1
        assert r1["monto_pagado"] == pytest.approx(50_000, abs=0.01)
        assert r2["pagado"] == 0
        assert r2["monto_pagado"] == pytest.approx(10_000, abs=0.01)

        c = next(x for x in list_clientes() if x["id"] == cid)
        assert c["saldo_actual"] == pytest.approx(70_000, abs=0.01)

    def test_cliente_incobrable(self, db):
        registrar_cliente("Moroso", 100_000, "D")
        registrar_lote_bulk(200, 160_000)
        with get_db() as conn:
            rid, cid = _remito_via_conn(conn, "Moroso", 50, 1000, 40_000)
        marcar_cliente_incobrable(cid)
        rem = next(r for r in list_remitos() if r["id"] == rid)
        assert rem["pagado"] == 2
        assert rem["estado_cobro"] == "incobrable"
        c = next(x for x in list_clientes() if x["nombre"] == "Moroso")
        assert c["saldo_actual"] == 0
        assert c["techo_deuda"] == 0


def test_parse_kg_detalle_piezas():
    from app.utils import parse_kg_detalle, pesos_piezas_to_json, pesos_piezas_from_json

    total, piezas = parse_kg_detalle("97+97+101+104")
    assert total == 399
    assert piezas == [97, 97, 101, 104]
    assert pesos_piezas_from_json(pesos_piezas_to_json(piezas)) == piezas


def test_remito_api_guarda_pesos_piezas(client):
    client.post("/api/bulk", json={"kg_totales": 500, "costo_total_bulk": 400_000})
    r = client.post(
        "/api/remitos",
        json={
            "cliente": "Cliente Piezas",
            "tipo_corte": "Media Res",
            "pesos_piezas": [58, 62, 59, 61, 60],
            "precio_por_kg": 1300,
            "plazo_cobro_dias": 30,
        },
    )
    assert r.status_code == 201
    det = client.get("/api/clientes").get_json()
    cli = next(c for c in det if c["nombre"] == "Cliente Piezas")
    full = client.get(f"/api/clientes/{cli['id']}").get_json()
    rem = full["remitos"][0]
    assert rem["cantidad"] == 5
    assert rem["pesos_piezas"] == [58, 62, 59, 61, 60]
    assert rem["kg"] == 300
