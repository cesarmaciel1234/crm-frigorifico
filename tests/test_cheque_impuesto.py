"""Tests de impuesto al cheque en operaciones financieras."""
from datetime import date, timedelta

import pytest

from app.utils import parse_operacion_payload
from app.services.finanzas import (
    _desglose_deuda_financiera,
    calc_metricas_flotantes,
    panel_estrategia,
    ranking_enemigos,
)


def test_cheque_sin_impuesto():
    p = parse_operacion_payload({
        "alias": "Banco",
        "tipo": "cheque",
        "monto": 100000,
        "fecha_vencimiento": "2026-07-01",
    })
    assert p["recibido"] == 100000
    assert p["pagar"] == 100000
    assert p["impuesto_cheque"] is None


def test_cheque_impuesto_porcentaje():
    p = parse_operacion_payload({
        "alias": "Banco",
        "tipo": "cheque",
        "monto": 100000,
        "fecha_vencimiento": "2026-07-01",
        "impuesto_cheque_tipo": "porcentaje",
        "impuesto_cheque_valor": 1.2,
    })
    assert p["impuesto_cheque"] == 1200.0
    assert p["pagar"] == 101200.0
    assert p["recibido"] == 100000


def test_cheque_impuesto_monto_fijo():
    p = parse_operacion_payload({
        "alias": "Banco",
        "tipo": "cheque",
        "monto": 50000,
        "fecha_vencimiento": "2026-07-01",
        "impuesto_cheque_tipo": "monto",
        "impuesto_cheque_valor": 750,
    })
    assert p["impuesto_cheque"] == 750.0
    assert p["pagar"] == 50750.0


def test_cheque_vencido_impuesto_como_interes_y_sangre(db):
    venc = (date.today() - timedelta(days=4)).isoformat()
    db.execute(
        """
        INSERT INTO operaciones_financieras
            (alias, tipo, recibido, pagar, meses, fecha_vencimiento, cuotas, cuotas_pagadas, impuesto_cheque)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Banco Provincia", "cheque", 8_000_000, 8_500_000, 1, venc, 1, 0, 500_000),
    )
    db.commit()

    enemigos = ranking_enemigos()
    ch = next(e for e in enemigos if e["alias"] == "Banco Provincia")
    assert ch["interes"] == 500_000
    assert ch["sin_interes"] is False
    assert ch["vencido"] is True
    assert ch["dias_retraso"] == 4

    _, deuda_neta, interes_neto = _desglose_deuda_financiera(db)
    assert interes_neto == pytest.approx(500_000, abs=1)
    assert deuda_neta == pytest.approx(8_000_000, abs=1)

    estrategia = panel_estrategia()
    mf = calc_metricas_flotantes(enemigos, estrategia)
    assert mf["sangre"] == pytest.approx(8_500_000 / 4, abs=1)
    assert mf["int_diario"] == pytest.approx(500_000 / 4, abs=1)
    assert mf["deuda"] == pytest.approx(8_500_000, abs=1)
