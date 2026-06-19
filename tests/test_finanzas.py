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
