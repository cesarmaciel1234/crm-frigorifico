from typing import Optional, Any
from datetime import date, timedelta, datetime
import json
import re

def fmt_plazo_dias(dias: Optional[int]) -> Optional[str]:
    if dias is None:
        return None
    d = int(dias)
    if d == 1:
        return "1 día"
    return f"{d} días"

def _f(val, field: str) -> float:
    try:
        n = float(val)
    except (TypeError, ValueError):
        raise ValueError(f"{field}: número inválido")
    if n < 0:
        raise ValueError(f"{field}: no puede ser negativo")
    return n


def _f_optional(val, field: str, default: float = 0.0) -> float:
    if val in (None, ""):
        return default
    return _f(val, field)


def _impuesto_cheque_from_payload(monto: float, d: dict) -> float:
    """Calcula impuesto al cheque desde porcentaje o monto fijo."""
    modo = str(d.get("impuesto_cheque_tipo") or "").strip().lower()
    if modo == "porcentaje":
        pct = _f_optional(d.get("impuesto_cheque_valor"), "impuesto al cheque (%)")
        if pct <= 0:
            return 0.0
        return round(monto * pct / 100.0, 2)
    if modo == "monto":
        return round(_f_optional(d.get("impuesto_cheque_valor"), "impuesto al cheque ($)"), 2)
    if d.get("impuesto_cheque") not in (None, ""):
        return round(_f_optional(d.get("impuesto_cheque"), "impuesto al cheque"), 2)
    return 0.0


def parse_kg_detalle(val) -> tuple[float, list[float]]:
    """Parsea kg total y lista de pesos por pieza (ej. '97+97+101+104')."""
    if isinstance(val, (int, float)):
        n = round(float(val), 2)
        if n <= 0:
            raise ValueError("kg debe ser > 0")
        return n, []

    if isinstance(val, list):
        pieces = []
        for item in val:
            p = round(float(item), 2)
            if p <= 0:
                raise ValueError("pesos_piezas: cada peso debe ser > 0")
            pieces.append(p)
        if not pieces:
            raise ValueError("pesos_piezas: lista vacía")
        return round(sum(pieces), 2), pieces

    s = str(val or "").replace(",", ".").strip()
    if not s:
        raise ValueError("kg: número inválido")

    if re.match(r"^\d+(?:\.\d+)?(?:\s*\+\s*\d+(?:\.\d+)?)+$", s):
        pieces = [round(float(p.strip()), 2) for p in s.split("+")]
        total = round(sum(pieces), 2)
        if total <= 0:
            raise ValueError("kg debe ser > 0")
        return total, pieces

    try:
        n = round(float(s), 2)
    except ValueError:
        raise ValueError("kg: número inválido")
    if n <= 0:
        raise ValueError("kg debe ser > 0")
    return n, []


def pesos_piezas_to_json(pieces: list[float]) -> str:
    return json.dumps(pieces) if pieces else "[]"


def pesos_piezas_from_json(raw) -> list[float]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [round(float(x), 2) for x in data if float(x) > 0]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def resolve_remito_kg(d: dict) -> tuple[float, list[float], int]:
    """Resuelve kg, pesos por pieza y cantidad desde el payload de un remito."""
    pesos_raw = d.get("pesos_piezas")
    if isinstance(pesos_raw, list) and pesos_raw:
        kg, pieces = parse_kg_detalle(pesos_raw)
    else:
        kg, pieces = parse_kg_detalle(d.get("kg"))

    cantidad_raw = d.get("cantidad")
    cantidad = int(cantidad_raw) if cantidad_raw not in (None, "", 0, "0") else 0
    if cantidad < 0:
        raise ValueError("cantidad inválida")
    if not cantidad and pieces:
        cantidad = len(pieces)
    return kg, pieces, cantidad

def _i(val, field: str, mn: int = 1) -> int:
    try:
        n = int(val)
    except (TypeError, ValueError):
        raise ValueError(f"{field}: entero inválido")
    if n < mn:
        raise ValueError(f"{field}: mínimo {mn}")
    return n

def _parse_fecha(val, field: str) -> str:
    fecha = str(val or "").strip()
    if not fecha:
        raise ValueError(f"{field} es obligatoria")
    if len(fecha) != 10 or fecha[4] != "-" or fecha[7] != "-":
        raise ValueError(f"{field} debe ser AAAA-MM-DD")
    return fecha

def parse_operacion_payload(d: dict) -> dict[str, Any]:
    alias = str(d.get("alias", "")).strip()
    if not alias:
        raise ValueError("alias obligatorio")
    tipo = str(d.get("tipo") or "otro").strip()[:30]
    tipo_l = tipo.lower()

    fecha_cierre = None
    fecha_vencimiento = None
    cuotas = None
    kg = None
    precio_kg = None
    plazo_dias = None
    impuesto_cheque = None

    if tipo_l == "cheque":
        monto = _f(d.get("monto") or d.get("recibido"), "monto del cheque")
        if monto <= 0:
            raise ValueError("monto del cheque debe ser mayor a 0")
        impuesto = _impuesto_cheque_from_payload(monto, d)
        recibido = monto
        pagar = round(monto + impuesto, 2)
        impuesto_cheque = impuesto if impuesto > 0 else None
        fecha_vencimiento = _parse_fecha(d.get("fecha_vencimiento"), "fecha de vencimiento")
        meses = 1
    elif tipo_l == "tarjeta":
        recibido = _f(d.get("recibido"), "recibido")
        pagar = _f(d.get("pagar"), "pagar")
        if pagar <= recibido:
            raise ValueError("pagar debe superar recibido")
        fecha_cierre = _parse_fecha(d.get("fecha_cierre"), "fecha de cierre")
        fecha_vencimiento = _parse_fecha(d.get("fecha_vencimiento"), "fecha de vencimiento")
        cuotas = _i(d.get("cuotas"), "cuotas")
        meses = cuotas
        if fecha_vencimiento < fecha_cierre:
            raise ValueError("vencimiento no puede ser anterior al cierre")
    elif tipo_l == "proveedor":
        kg = _f(d.get("kg"), "kg")
        if kg <= 0:
            raise ValueError("kg debe ser mayor a 0")
        precio_kg = _f(d.get("precio_kg"), "precio_kg")
        plazo_dias = _i(d.get("plazo_dias"), "plazo_dias", mn=1)
        monto = round(kg * precio_kg, 2)
        recibido = monto
        pagar_val = d.get("pagar")
        if pagar_val not in (None, ""):
            pagar = _f(pagar_val, "pagar")
            if pagar < recibido:
                raise ValueError("pagar no puede ser menor al total (kg × precio)")
        else:
            pagar = monto
        meses = max(1, (plazo_dias + 29) // 30)
        fecha_vencimiento = (date.today() + timedelta(days=plazo_dias)).isoformat()
    elif tipo_l == "prestamo":
        recibido = _f(d.get("recibido"), "recibido")
        pagar = _f(d.get("pagar"), "pagar")
        if pagar < recibido:
            raise ValueError("pagar no puede ser menor a recibido")
        plazo_dias = _i(d.get("plazo_dias"), "plazo_dias", mn=1)
        meses = max(1, (plazo_dias + 29) // 30)
        
        fecha_inicio_str = d.get("fecha_inicio")
        if fecha_inicio_str:
            fecha_vencimiento = (datetime.strptime(fecha_inicio_str, "%Y-%m-%d").date() + timedelta(days=plazo_dias)).isoformat()
        else:
            fecha_vencimiento = (date.today() + timedelta(days=plazo_dias)).isoformat()
    else:
        recibido = _f(d.get("recibido"), "recibido")
        pagar = _f(d.get("pagar"), "pagar")
        if pagar < recibido:
            raise ValueError("pagar no puede ser menor a recibido")
        meses = _i(d.get("meses"), "meses")

    return {
        "alias": alias,
        "tipo": tipo,
        "recibido": recibido,
        "pagar": pagar,
        "meses": meses,
        "fecha_cierre": fecha_cierre,
        "fecha_vencimiento": fecha_vencimiento,
        "cuotas": cuotas,
        "kg": kg,
        "precio_kg": precio_kg,
        "plazo_dias": plazo_dias,
        "fecha_inicio": d.get("fecha_inicio") or None,
        "impuesto_cheque": impuesto_cheque,
    }
