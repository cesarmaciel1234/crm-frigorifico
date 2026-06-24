"""Tests de matemática financiera — bloqueantes para producción."""
import pytest
from datetime import date, timedelta

from app.services.finanzas import (
    calc_cfr,
    panel_activo,
    sangria_diaria,
    _desglose_deuda_financiera,
    _deuda_pendiente_total,
    ranking_enemigos,
)
from app.services.pagos import registrar_pago


def _insert_op(conn, alias, tipo, recibido, pagar, meses=6, cuotas=6, cuotas_pagadas=0, venc=None):
    if venc is None:
        venc = (date.today() + timedelta(days=30)).isoformat()
    cur = conn.execute(
        """
        INSERT INTO operaciones_financieras
            (alias, tipo, recibido, pagar, meses, fecha_vencimiento, cuotas, cuotas_pagadas)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (alias, tipo, recibido, pagar, meses, venc, cuotas, cuotas_pagadas),
    )
    return cur.lastrowid


class TestFinanzasCore:
    def test_calc_cfr(self):
        assert calc_cfr(100_000, 112_000, 12) == pytest.approx(1.0, abs=0.01)
        assert calc_cfr(0, 100, 1) is None

    def test_deuda_neta_mas_interes_igual_deuda_real(self, db):
        _insert_op(db, "Visa Test", "tarjeta", 500_000, 620_000, cuotas=6)
        _insert_op(db, "Cheque Test", "cheque", 100_000, 100_000, cuotas=1, meses=1)
        _insert_op(db, "Proveedor", "proveedor", 200_000, 200_000, cuotas=1, meses=1)
        db.commit()

        _, _, deuda_real = _deuda_pendiente_total(db)
        _, deuda_neta, interes_neto = _desglose_deuda_financiera(db)

        assert deuda_neta + interes_neto == pytest.approx(deuda_real, abs=0.02)
        assert interes_neto == pytest.approx(120_000, abs=0.02)

    def test_proveedor_fuera_deuda_real(self, db):
        _insert_op(db, "Frigo", "proveedor", 300_000, 300_000, cuotas=1, meses=1)
        db.commit()
        a = panel_activo()
        assert a["deuda_comercial"] == pytest.approx(300_000, abs=0.01)
        assert a["deuda_real"] == pytest.approx(0, abs=0.01)

    def test_sangria_usa_interes_pendiente(self, db):
        op_id = _insert_op(db, "Visa", "tarjeta", 500_000, 620_000, cuotas=6)
        db.commit()
        registrar_pago(op_id, 1, 103_333.33)

        a = panel_activo()
        s = sangria_diaria()
        assert s["intereses_totales"] == pytest.approx(a["interes_neto"], abs=0.02)

    def test_pago_cuota_reduce_saldo(self, db):
        op_id = _insert_op(db, "Tarjeta", "tarjeta", 200_000, 240_000, cuotas=4)
        db.commit()
        antes = panel_activo()["deuda_real"]
        registrar_pago(op_id, 1, 60_000)
        despues = panel_activo()["deuda_real"]
        assert despues < antes
        assert despues == pytest.approx(180_000, abs=0.02)

    def test_proveedor_sin_cfr_en_ranking(self, db):
        _insert_op(db, "Visa", "tarjeta", 100_000, 130_000, cuotas=3)
        _insert_op(db, "Prov", "proveedor", 500_000, 500_000, cuotas=1, meses=1)
        db.commit()
        enemigos = ranking_enemigos()
        prov = next(e for e in enemigos if e["es_proveedor"])
        visa = next(e for e in enemigos if e["alias"] == "Visa")
        assert prov["cfr"] is None
        assert visa["cfr"] is not None

    def test_calcular_antiguedad_deuda(self, db):
        from app.services.finanzas import calcular_antiguedad_deuda
        from datetime import date, timedelta
        
        # Insert client
        cur = db.execute(
            "INSERT INTO clientes (nombre, saldo_actual, saldo_inicial) VALUES (?, ?, ?)",
            ("Cliente Test Aging", 10000.0, 1000.0) # $1000 saldo inicial goes to 90+ days bucket
        )
        cid = cur.lastrowid
        
        today_str = date.today().isoformat()
        days_45_ago = (date.today() - timedelta(days=45)).isoformat()
        days_120_ago = (date.today() - timedelta(days=120)).isoformat()
        
        # Remito 0-30 days:
        db.execute(
            """
            INSERT INTO remitos_carga
                (cliente_id, fecha, kg, precio_por_kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado, monto_pagado)
            VALUES (?, ?, 10, 1000, 1000, 10000.0, 30, 6000.0, 0, 8000.0) -- Outstanding: 2000.0
            """,
            (cid, today_str)
        )
        
        # Remito 31-60 days:
        db.execute(
            """
            INSERT INTO remitos_carga
                (cliente_id, fecha, kg, precio_por_kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado, monto_pagado)
            VALUES (?, ?, 10, 1000, 1000, 10000.0, 30, 6000.0, 0, 7000.0) -- Outstanding: 3000.0
            """,
            (cid, days_45_ago)
        )
        
        # Remito 90+ days:
        db.execute(
            """
            INSERT INTO remitos_carga
                (cliente_id, fecha, kg, precio_por_kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado, monto_pagado)
            VALUES (?, ?, 10, 1000, 1000, 10000.0, 30, 6000.0, 0, 6000.0) -- Outstanding: 4000.0
            """,
            (cid, days_120_ago)
        )
        
        db.commit()
        
        res = calcular_antiguedad_deuda()
        
        assert res["totales"]["0_30"] == pytest.approx(2000.0, abs=0.01)
        assert res["totales"]["31_60"] == pytest.approx(3000.0, abs=0.01)
        assert res["totales"]["61_90"] == pytest.approx(0.0, abs=0.01)
        assert res["totales"]["90_plus"] == pytest.approx(5000.0, abs=0.01) # 4000 from remito + 1000 from saldo_inicial
        
        # Check client details
        client_res = next(c for c in res["clientes"] if c["id"] == cid)
        assert client_res["nombre"] == "Cliente Test Aging"
        assert client_res["saldo_actual"] == pytest.approx(10000.0, abs=0.01)
        assert client_res["buckets"]["0_30"] == pytest.approx(2000.0, abs=0.01)
        assert client_res["buckets"]["31_60"] == pytest.approx(3000.0, abs=0.01)
        assert client_res["buckets"]["61_90"] == pytest.approx(0.0, abs=0.01)
        assert client_res["buckets"]["90_plus"] == pytest.approx(5000.0, abs=0.01)

    def test_calcular_margenes_ventas(self, db):
        from app.services.finanzas import calcular_margenes_ventas
        
        # Insert client
        cur = db.execute(
            "INSERT INTO clientes (nombre, saldo_actual, saldo_inicial) VALUES (?, ?, ?)",
            ("Cliente Test Margenes", 0.0, 0.0)
        )
        cid = cur.lastrowid
        
        # Insert remito
        db.execute(
            """
            INSERT INTO remitos_carga
                (cliente_id, fecha, kg, precio_por_kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado, monto_pagado)
            VALUES (?, ?, 10, 1000, 1500.0, 10000.0, 30, 6000.0, 0, 0.0)
            """,
            (cid, "2026-06-22")
        )
        db.commit()
        
        res = calcular_margenes_ventas(limit=1)
        assert len(res) >= 1
        item = res[0]
        assert item["cliente"] == "Cliente Test Margenes"
        assert item["precio_venta_total"] == 10000.0
        assert item["costo_carne"] == 6000.0
        assert item["costo_logistica"] == 1500.0
        assert item["margen_bruto"] == 4000.0
        assert item["margen_neto"] == 2500.0
        assert item["porcentaje_margen"] == pytest.approx(25.0, abs=0.1)

