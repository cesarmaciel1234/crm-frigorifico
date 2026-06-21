from typing import Optional, Any
from datetime import date, timedelta, datetime

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

    if tipo_l == "cheque":
        monto = _f(d.get("monto") or d.get("recibido"), "monto del cheque")
        if monto <= 0:
            raise ValueError("monto del cheque debe ser mayor a 0")
        recibido = monto
        pagar = monto
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
        "fecha_inicio": d.get("fecha_inicio") or None
    }
