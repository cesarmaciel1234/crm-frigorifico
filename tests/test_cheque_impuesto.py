"""Tests de impuesto al cheque en operaciones financieras."""
from app.utils import parse_operacion_payload


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
