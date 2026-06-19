from typing import Any, Optional
from datetime import date
from app.database import get_db
from app.utils import fmt_plazo_dias

def calc_estado_vencimiento(fecha_vencimiento: Optional[str]) -> dict[str, Any]:
    """Calcula días al vencimiento, retraso y estado para tarjetas."""
    vacio = {
        "dias_faltantes": None,
        "dias_retraso": 0,
        "vencido": False,
        "estado_vencimiento": "sin_fecha",
        "mensaje_vencimiento": None,
    }
    if not fecha_vencimiento:
        return vacio
    try:
        vto = date.fromisoformat(str(fecha_vencimiento)[:10])
    except ValueError:
        return vacio

    delta = (vto - date.today()).days
    if delta < 0:
        retraso = abs(delta)
        return {
            "dias_faltantes": 0,
            "dias_retraso": retraso,
            "vencido": True,
            "estado_vencimiento": "vencido",
            "mensaje_vencimiento": f"Vencido · {retraso} días de retraso",
        }
    if delta == 0:
        return {
            "dias_faltantes": 0,
            "dias_retraso": 0,
            "vencido": False,
            "estado_vencimiento": "hoy",
            "mensaje_vencimiento": "Vence hoy",
        }
    if delta <= 7:
        return {
            "dias_faltantes": delta,
            "dias_retraso": 0,
            "vencido": False,
            "estado_vencimiento": "proximo",
            "mensaje_vencimiento": f"Faltan {delta} días",
        }
    return {
        "dias_faltantes": delta,
        "dias_retraso": 0,
        "vencido": False,
        "estado_vencimiento": "al_dia",
        "mensaje_vencimiento": f"Faltan {delta} días",
    }


def calc_diferencia_pago(monto_esperado: float, monto_pagado: float) -> dict[str, float]:
    diff = round(monto_pagado - monto_esperado, 2)
    if diff > 0.005:
        return {"interes_punitorio": diff, "descuento": 0.0}
    if diff < -0.005:
        return {"interes_punitorio": 0.0, "descuento": round(abs(diff), 2)}
    return {"interes_punitorio": 0.0, "descuento": 0.0}


def calc_plan_cuotas(row: dict, venc: dict) -> dict[str, Any]:
    """Plan de cuotas: monto, cuota en curso y cuotas vencidas."""
    tipo = (row["tipo"] or "").lower()
    pagadas = int(row["cuotas_pagadas"] or 0)

    if tipo == "tarjeta" and row.get("cuotas"):
        total = int(row["cuotas"])
    elif tipo in ("cheque", "proveedor"):
        total = 1
    else:
        return {
            "tiene_cuotas": False,
            "cuotas_total": None,
            "cuotas_pagadas": pagadas,
            "cuota_en_curso": None,
            "monto_cuota": None,
            "cuotas_vencidas": 0,
            "cuotas_vencidas_lista": [],
            "saldo_pendiente": round(float(row["pagar"]), 2),
            "completa": False,
        }

    monto_cuota = round(float(row["pagar"]) / total, 2)
    restantes = max(0, total - pagadas)
    cuota_en_curso = min(pagadas + 1, total) if restantes > 0 else total

    cuotas_vencidas = 0
    if venc.get("vencido") and restantes > 0:
        dias = venc.get("dias_retraso") or 0
        cuotas_vencidas = min(restantes, max(1, (dias + 29) // 30))

    vencidas_lista = list(range(pagadas + 1, pagadas + cuotas_vencidas + 1)) if cuotas_vencidas else []
    saldo_pendiente = round(monto_cuota * restantes, 2)
    completa = pagadas >= total

    plan = {
        "tiene_cuotas": True,
        "cuotas_total": total,
        "cuotas_pagadas": pagadas,
        "cuota_en_curso": cuota_en_curso,
        "monto_cuota": monto_cuota,
        "cuotas_vencidas": cuotas_vencidas,
        "cuotas_vencidas_lista": vencidas_lista,
        "saldo_pendiente": saldo_pendiente,
        "completa": completa,
        "reserva_diaria": 0.0,
    }
    if tipo == "cheque" and not completa:
        plan["reserva_diaria"] = round(_calc_reserva_diaria(saldo_pendiente, venc), 2)
    elif tipo == "proveedor" and not completa and float(row["pagar"]) <= float(row["recibido"]):
        plan["reserva_diaria"] = round(_calc_reserva_diaria(saldo_pendiente, venc), 2)
    return plan


def _calc_reserva_diaria(saldo: float, venc: dict) -> float:
    """Reserva diaria de caja para obligaciones sin interés (cheques)."""
    if saldo <= 0:
        return 0.0
    if venc.get("vencido"):
        dias = max(1, min(30, int(venc.get("dias_retraso") or 1)))
        return saldo / dias
    if venc.get("estado_vencimiento") == "hoy":
        return saldo
    dias_falt = venc.get("dias_faltantes")
    if dias_falt is not None and dias_falt > 0:
        return saldo / dias_falt
    return saldo / 30.0


def list_historial_pagos(limit: int = 500, tipo: Optional[str] = None) -> list[dict[str, Any]]:
    """Historial global de pagos con datos de la operación."""
    sql = """
            SELECT
                p.id, p.operacion_id, p.numero_cuota, p.monto_cuota_esperado,
                p.monto_pagado, p.interes_punitorio, p.descuento, p.fecha_pago,
                o.alias, o.tipo, o.cuotas, o.cuotas_pagadas, o.kg, o.precio_kg,
                o.plazo_dias, o.meses, o.recibido, o.pagar
            FROM pagos_cuotas p
            JOIN operaciones_financieras o ON o.id = p.operacion_id
            WHERE 1=1
            """
    params: list[Any] = []
    if tipo:
        sql += " AND LOWER(o.tipo) = ?"
        params.append(tipo.lower())
    sql += " ORDER BY p.fecha_pago DESC, p.id DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    out = []
    for row in rows:
        row_dict = dict(row)
        t = (row_dict["tipo"] or "").lower()
        cuotas_total = row_dict["cuotas"] or (1 if t in ("cheque", "proveedor") else row_dict["meses"])
        cuota_label = f"{row_dict['numero_cuota']}/{cuotas_total}" if cuotas_total else str(row_dict["numero_cuota"])
        detalle = ""
        if t == "proveedor" and row_dict["kg"]:
            detalle = f"{row_dict['kg']} kg × ${row_dict['precio_kg']:.2f}"
        elif t == "banco":
            detalle = f"Plazo {row_dict['meses']} meses · CFR op."
        elif t == "tarjeta":
            detalle = f"Tarjeta · cuota {cuota_label}"
        elif t == "cheque":
            detalle = "Cheque · sin interés"

        out.append(
            {
                "id": row_dict["id"],
                "fecha_pago": row_dict["fecha_pago"],
                "alias": row_dict["alias"],
                "tipo": row_dict["tipo"],
                "numero_cuota": row_dict["numero_cuota"],
                "cuota_label": cuota_label,
                "cuotas_pagadas_op": row_dict["cuotas_pagadas"],
                "plazo_texto": fmt_plazo_dias(row_dict["plazo_dias"]),
                "kg": row_dict["kg"],
                "precio_kg": row_dict["precio_kg"],
                "monto_cuota_esperado": row_dict["monto_cuota_esperado"],
                "monto_pagado": row_dict["monto_pagado"],
                "interes_punitorio": row_dict["interes_punitorio"],
                "descuento": row_dict["descuento"],
                "detalle": detalle,
            }
        )
    return out


def get_pagos_operacion(op_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, numero_cuota, monto_cuota_esperado, monto_pagado,
                   interes_punitorio, descuento, fecha_pago
            FROM pagos_cuotas WHERE operacion_id = ?
            ORDER BY id DESC
            """,
            (op_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def registrar_pago(op_id: int, numero_cuota: int, monto_pagado: float) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, alias, tipo, pagar, cuotas, cuotas_pagadas, fecha_vencimiento
            FROM operaciones_financieras WHERE id = ?
            """,
            (op_id,),
        ).fetchone()
        if not row:
            raise ValueError("Operación no encontrada")

        row_dict = dict(row)
        venc = calc_estado_vencimiento(
            row_dict["fecha_vencimiento"]
            if (row_dict["tipo"] or "").lower() in ("tarjeta", "cheque", "proveedor")
            else None
        )
        plan = calc_plan_cuotas(row_dict, venc)
        if not plan["tiene_cuotas"]:
            raise ValueError("Esta operación no maneja cuotas")
        if plan["completa"]:
            raise ValueError("Todas las cuotas ya fueron pagadas")

        total = plan["cuotas_total"]
        if numero_cuota < 1 or numero_cuota > total:
            raise ValueError(f"Cuota debe estar entre 1 y {total}")
        if numero_cuota <= int(row_dict["cuotas_pagadas"] or 0):
            raise ValueError(f"La cuota {numero_cuota} ya fue registrada como pagada")

        monto_esperado = plan["monto_cuota"]
        diff = calc_diferencia_pago(monto_esperado, monto_pagado)

        conn.execute(
            """
            INSERT INTO pagos_cuotas
                (operacion_id, numero_cuota, monto_cuota_esperado, monto_pagado,
                 interes_punitorio, descuento)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                op_id,
                numero_cuota,
                monto_esperado,
                monto_pagado,
                diff["interes_punitorio"],
                diff["descuento"],
            ),
        )
        nuevas_pagadas = max(int(row_dict["cuotas_pagadas"] or 0), numero_cuota)
        conn.execute(
            "UPDATE operaciones_financieras SET cuotas_pagadas = ? WHERE id = ?",
            (nuevas_pagadas, op_id),
        )

    return {
        "operacion_id": op_id,
        "alias": row_dict["alias"],
        "numero_cuota": numero_cuota,
        "cuotas_total": total,
        "cuotas_pagadas": nuevas_pagadas,
        "monto_cuota_esperado": monto_esperado,
        "monto_pagado": round(monto_pagado, 2),
        "interes_punitorio": diff["interes_punitorio"],
        "descuento": diff["descuento"],
        "completa": nuevas_pagadas >= total,
        "saldo_pendiente": round(monto_esperado * max(0, total - nuevas_pagadas), 2),
    }
