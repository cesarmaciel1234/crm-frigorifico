from typing import Any, Optional

from app.database import get_db
from app.utils import fmt_plazo_dias
from app.services.pagos import calc_estado_vencimiento, calc_plan_cuotas

_OPERACIONES_SQL = """
    SELECT id, alias, tipo, recibido, pagar, meses,
           fecha_cierre, fecha_vencimiento, cuotas,
           COALESCE(cuotas_pagadas, 0) AS cuotas_pagadas,
           kg, precio_kg, plazo_dias, created_at
    FROM operaciones_financieras
"""


def _get_operaciones_rows(conn=None) -> list[dict[str, Any]]:
    """Carga operaciones_financieras una sola vez por request HTTP."""
    try:
        from flask import g, has_request_context

        if has_request_context() and getattr(g, "_operaciones_fin_rows", None) is not None:
            return g._operaciones_fin_rows
    except ImportError:
        has_request_context = None

    if conn is not None:
        rows = [dict(row) for row in conn.execute(_OPERACIONES_SQL).fetchall()]
    else:
        with get_db() as db_conn:
            rows = [dict(row) for row in db_conn.execute(_OPERACIONES_SQL).fetchall()]

    try:
        if has_request_context and has_request_context():
            g._operaciones_fin_rows = rows
    except ImportError:
        pass
    return rows

def calc_cfr(recibido: float, pagar: float, meses: int) -> Optional[float]:
    """Costo Financiero Real mensual (%)."""
    if recibido <= 0 or meses <= 0:
        return None
    return ((pagar / recibido) - 1) / meses * 100

def sangria_diaria() -> dict[str, Any]:
    """
    Sangría diaria = costo financiero/30 + reserva diaria de cheques pendientes.
    Proveedores quedan fuera del circuito financiero.
    """
    rows = [
        row for row in _get_operaciones_rows()
        if (row.get("tipo") or "").lower() != "proveedor"
    ]

    total_intereses = 0.0
    sangria_cheques = 0.0
    saldo_cheques = 0.0
    cheques_activos = 0
    ops_activas = 0

    for r in rows:
        tipo = (r["tipo"] or "").lower()
        if r["cuotas"] is not None and r["cuotas_pagadas"] >= r["cuotas"]:
            continue

        if tipo == "cheque":
            venc = calc_estado_vencimiento(r["fecha_vencimiento"])
            plan = calc_plan_cuotas({**r, "cuotas": None}, venc)
            if plan["completa"]:
                continue
            cheques_activos += 1
            saldo_cheques += plan["saldo_pendiente"]
            sangria_cheques += plan["reserva_diaria"]
            ops_activas += 1
        else:
            saldo = _saldo_pendiente_operacion(r)
            if saldo <= 0:
                continue
            pagar = float(r["pagar"])
            recibido = float(r["recibido"])
            if pagar > recibido:
                total_intereses += saldo * (1.0 - recibido / pagar)
            ops_activas += 1

    sangria_fin = total_intereses / 30.0
    total_diaria = sangria_fin + sangria_cheques
    return {
        "intereses_totales": round(total_intereses, 2),
        "sangria_diaria": round(total_diaria, 2),
        "sangria_financiera_diaria": round(sangria_fin, 2),
        "sangria_cheques_diaria": round(sangria_cheques, 2),
        "saldo_cheques_pendiente": round(saldo_cheques, 2),
        "cheques_activos": cheques_activos,
        "operaciones": ops_activas,
    }

def _saldo_pendiente_operacion(row: dict) -> float:
    tipo = (row["tipo"] or "").lower()
    usa_venc = tipo in ("tarjeta", "cheque", "proveedor")
    venc = calc_estado_vencimiento(row["fecha_vencimiento"] if usa_venc else None)
    plan = calc_plan_cuotas(row, venc)
    return float(plan["saldo_pendiente"])

def _desglose_deuda_financiera(conn) -> tuple[float, float, float]:
    """Saldo pendiente de deuda real desglosado en capital e interés."""
    rows = [
        row for row in _get_operaciones_rows(conn)
        if (row.get("tipo") or "").lower() != "proveedor"
    ]
    total = capital = interes = 0.0
    for row_dict in rows:
        saldo = _saldo_pendiente_operacion(row_dict)
        if saldo <= 0:
            continue
        total += saldo
        recibido = float(row_dict["recibido"])
        pagar = float(row_dict["pagar"])
        tipo = (row_dict["tipo"] or "").lower()
        if tipo == "cheque" or pagar <= recibido:
            capital += saldo
        else:
            ratio_capital = recibido / pagar
            capital += saldo * ratio_capital
            interes += saldo * (1.0 - ratio_capital)
    return total, capital, interes

def _deuda_pendiente_total(conn) -> tuple[float, float, float]:
    rows = _get_operaciones_rows(conn)
    total = prov = fin = 0.0
    for row_dict in rows:
        saldo = _saldo_pendiente_operacion(row_dict)
        if saldo <= 0:
            continue
        total += saldo
        if (row_dict["tipo"] or "").lower() == "proveedor":
            prov += saldo
        else:
            fin += saldo
    return total, prov, fin

def panel_activo() -> dict[str, Any]:
    with get_db() as conn:
        rem = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN pagado = 0 THEN costo_total_logistica + costo_carne ELSE 0 END), 0) AS costo_pendiente,
                COALESCE(SUM(precio_venta_total - costo_total_logistica - costo_carne), 0) AS ganancia_total,
                COALESCE(SUM(CASE WHEN pagado = 0 THEN precio_venta_total - costo_total_logistica - costo_carne ELSE 0 END), 0) AS ganancia_pendiente,
                COALESCE(SUM(CASE WHEN pagado = 0 THEN precio_venta_total ELSE 0 END), 0) AS venta_total,
                COALESCE(SUM(kg), 0) AS kg_flujo,
                COUNT(*) AS n,
                COALESCE(AVG(plazo_cobro_dias), NULL) AS avg_cobro
            FROM remitos_carga
            """
        ).fetchone()
        _, deuda_comercial, deuda_real = _deuda_pendiente_total(conn)
        _, deuda_neta, interes_neto = _desglose_deuda_financiera(conn)
        pago = conn.execute(
            """
            SELECT AVG(meses * 30.0) AS avg FROM operaciones_financieras
            WHERE LOWER(tipo) != 'proveedor'
            """
        ).fetchone()
        
        # Consultas de inventario y caja para el Balance Dinámico
        stock = conn.execute(
            """
            SELECT
                COALESCE(SUM(kg_remanentes), 0) AS stock_kg,
                COALESCE(SUM(kg_remanentes * costo_total_bulk / kg_totales), 0) AS stock_valorizado
            FROM compras_bulk
            """
        ).fetchone()
        
        bancos = conn.execute("SELECT COALESCE(SUM(limite), 0) AS total FROM entidades_bancarias").fetchone()
        ops_row = conn.execute(
            "SELECT COALESCE(SUM(recibido), 0) AS total FROM operaciones_financieras WHERE LOWER(tipo) != 'proveedor'"
        ).fetchone()
        pagos_row = conn.execute("SELECT COALESCE(SUM(monto_pagado), 0) AS total FROM pagos_cuotas").fetchone()
        bulk_costo_row = conn.execute("SELECT COALESCE(SUM(costo_total_bulk), 0) AS total FROM compras_bulk").fetchone()

    activo_costo = float(rem["costo_pendiente"])
    ganancia = float(rem["ganancia_total"])
    ganancia_pendiente = float(rem["ganancia_pendiente"])
    activo_ventas = float(rem["venta_total"])
    kg_flujo = float(rem["kg_flujo"])
    remitos_n = int(rem["n"])
    deuda_comercial = float(deuda_comercial)
    deuda_real = float(deuda_real)
    deuda_neta = float(deuda_neta)
    interes_neto = float(interes_neto)
    deuda_total = deuda_comercial + deuda_real
    
    # Stock Valorizado
    stock_kg = float(stock["stock_kg"])
    activo_mercaderia = float(stock["stock_valorizado"])
    
    # Caja Real = Límite bancos + Ingresos préstamos/tarjetas - Pagos realizados - Compras bulk
    limite_bancos = float(bancos["total"])
    ingresos_ops = float(ops_row["total"])
    pagos_realizados = float(pagos_row["total"])
    pagos_bulk = float(bulk_costo_row["total"])
    caja_real = limite_bancos + ingresos_ops - pagos_realizados - pagos_bulk
    
    # Activo Total = Caja Real + Cuentas por cobrar (activo_ventas) + Inventario Valorizado (activo_mercaderia)
    activo_total = caja_real + activo_ventas + activo_mercaderia
    
    ciclo_cobro = float(rem["avg_cobro"]) if rem["avg_cobro"] is not None else None
    ciclo_pago = float(pago["avg"]) if pago["avg"] is not None else None

    capital_neto = activo_ventas - deuda_real

    if activo_ventas <= 0 and deuda_real <= 0 and deuda_comercial <= 0:
        estado = "SIN DATOS"
        ok = None
    elif activo_ventas >= deuda_real and (
        ciclo_cobro is None or ciclo_pago is None or ciclo_cobro <= ciclo_pago
    ):
        estado = "OK"
        ok = True
    else:
        estado = "PELIGRO"
        ok = False

    return {
        "activo_pendiente": round(activo_ventas, 2),
        "activo_ventas": round(activo_ventas, 2),
        "activo_costo": round(activo_costo, 2),
        "activo_clientes": round(activo_costo, 2),
        "ganancia_acumulada": round(ganancia, 2),
        "ganancia_pendiente": round(ganancia_pendiente, 2),
        "activo_mercaderia": round(activo_mercaderia, 2),
        "activo_total": round(activo_total, 2),
        "deuda_total": round(deuda_total, 2),
        "deuda_comercial": round(deuda_comercial, 2),
        "deuda_proveedores": round(deuda_comercial, 2),
        "deuda_real": round(deuda_real, 2),
        "deuda_financiera": round(deuda_real, 2),
        "deuda_neta": round(deuda_neta, 2),
        "interes_neto": round(interes_neto, 2),
        "capital_neto": round(capital_neto, 2),
        "kg_flujo": round(kg_flujo, 2),
        "remitos_count": remitos_n,
        "ciclo_cobro_dias": round(ciclo_cobro, 1) if ciclo_cobro is not None else None,
        "ciclo_pago_dias": round(ciclo_pago, 1) if ciclo_pago is not None else None,
        "estado": estado,
        "ok": ok,
        "caja_real": round(caja_real, 2),
        "stock_kg": round(stock_kg, 2),
        "pasivo_total": round(deuda_total, 2),
        "patrimonio_neto": round(activo_total - deuda_total, 2),
    }

def flujo_stock() -> dict[str, Any]:
    return panel_activo()

def indice_respiracion() -> dict[str, Any]:
    return flujo_stock()

def proyeccion_liberacion(excedente_mensual_manual: Optional[float] = None) -> dict[str, Any]:
    with get_db() as conn:
        _, _, deuda_real = _deuda_pendiente_total(conn)
        deuda_total = deuda_real
        rem_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(precio_venta_total - costo_total_logistica - costo_carne), 0) AS margen,
                COALESCE(AVG(plazo_cobro_dias), 30) AS avg_cobro
            FROM remitos_carga
            """
        ).fetchone()
        fin_row = conn.execute(
            """
            SELECT COALESCE(SUM(
                CASE WHEN LOWER(tipo) = 'cheque' THEN 0 ELSE (pagar - recibido) / meses END
            ), 0) AS carga_mensual
            FROM operaciones_financieras
            """
        ).fetchone()
    margen_bruto = float(rem_row["margen"])
    avg_cobro = max(float(rem_row["avg_cobro"]), 1.0)
    carga_mensual = float(fin_row["carga_mensual"])

    margen_mensual = margen_bruto * (30.0 / avg_cobro) if margen_bruto > 0 else 0.0
    excedente = (
        excedente_mensual_manual
        if excedente_mensual_manual is not None
        else margen_mensual - carga_mensual
    )

    if deuda_total <= 0:
        meses = 0.0
        meta = "SIN DEUDA"
    elif excedente <= 0:
        meses = None
        meta = "INSUFICIENTE"
    else:
        meses = deuda_total / excedente
        meta = f"{meses:.1f}"

    return {
        "deuda_total": round(deuda_total, 2),
        "margen_mensual_est": round(margen_mensual, 2),
        "carga_financiera_mensual": round(carga_mensual, 2),
        "excedente_mensual": round(excedente, 2),
        "meses_liberacion": round(meses, 1) if meses is not None else None,
        "meta_texto": meta,
    }

def panel_estrategia(excedente: Optional[float] = None) -> dict[str, Any]:
    s = sangria_diaria()
    a = panel_activo()
    p = proyeccion_liberacion(excedente)
    return {"sangria": s, "activo": a, "flujo": a, "respiracion": a, "proyeccion": p}

def _calc_cfr_from_row(row: dict) -> Optional[float]:
    tipo = (row.get("tipo") or "").lower()
    recibido = float(row.get("recibido") or 0)
    pagar = float(row.get("pagar") or 0)
    meses = int(row.get("meses") or 0)
    if tipo in ("cheque", "proveedor") and pagar <= recibido:
        return None
    if tipo == "cheque":
        return None
    if recibido > 0 and meses > 0:
        return ((pagar / recibido) - 1) / meses * 100
    return None

def _enemigo_from_row(row: dict, index: int) -> dict[str, Any]:
    tipo = row["tipo"] or ""
    es_tarjeta = tipo.lower() == "tarjeta"
    es_cheque = tipo.lower() == "cheque"
    es_proveedor = tipo.lower() == "proveedor"
    sin_interes = es_cheque or (es_proveedor and float(row["pagar"]) <= float(row["recibido"]))

    cfr_raw = row["cfr"] if "cfr" in row else _calc_cfr_from_row(row)
    cfr = None if sin_interes or es_cheque else cfr_raw
    interes = 0.0 if sin_interes else round(row["pagar"] - row["recibido"], 2)

    usa_venc = es_tarjeta or es_cheque or es_proveedor
    venc = calc_estado_vencimiento(row["fecha_vencimiento"] if usa_venc else None)
    plan = calc_plan_cuotas(row, venc)
    plazo_txt = fmt_plazo_dias(row["plazo_dias"]) if es_proveedor else None

    urgente = (cfr is not None and cfr > 10) or (venc.get("vencido") is True)

    return {
        "pos": index + 1,
        "id": row["id"],
        "alias": row["alias"],
        "tipo": row["tipo"],
        "recibido": row["recibido"],
        "pagar": row["pagar"],
        "total_pagar": round(row["pagar"], 2),
        "meses": row["meses"],
        "fecha_cierre": row["fecha_cierre"],
        "fecha_vencimiento": row["fecha_vencimiento"],
        "cuotas": row["cuotas"],
        "cuotas_pagadas": plan["cuotas_pagadas"],
        "interes": interes,
        "cfr": round(cfr, 2) if cfr is not None else None,
        "sin_interes": sin_interes,
        "prioridad": index == 0 and (cfr is not None or venc.get("vencido")),
        "urgente": urgente,
        "created_at": row["created_at"],
        "es_tarjeta": es_tarjeta,
        "es_cheque": es_cheque,
        "es_proveedor": es_proveedor,
        "kg": row["kg"],
        "precio_kg": row["precio_kg"],
        "plazo_dias": row["plazo_dias"],
        "plazo_texto": plazo_txt,
        **venc,
        **plan,
    }

def ranking_enemigos() -> list[dict[str, Any]]:
    rows = _get_operaciones_rows()
    enemigos = [_enemigo_from_row(row, i) for i, row in enumerate(rows)]

    def sort_key(e: dict[str, Any]) -> tuple:
        cfr = e.get("cfr")
        if cfr is None:
            return (1, 0.0, -int(e.get("id") or 0))
        return (0, -float(cfr), -int(e.get("id") or 0))

    enemigos.sort(key=sort_key)
    return enemigos

def historial_vencimientos(enemigos: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    fuente = enemigos if enemigos is not None else ranking_enemigos()
    items = [
        e for e in fuente
        if e.get("fecha_vencimiento") and (e.get("es_tarjeta") or e.get("es_cheque") or e.get("es_proveedor"))
    ]

    def sort_key(e: dict) -> tuple:
        if e.get("vencido"):
            return (0, -e.get("dias_retraso", 0))
        if e.get("estado_vencimiento") == "hoy":
            return (1, 0)
        if e.get("estado_vencimiento") == "proximo":
            return (2, e.get("dias_faltantes", 999))
        return (3, e.get("dias_faltantes", 999))

    items.sort(key=sort_key)
    return items

def calcular_antiguedad_deuda() -> dict[str, Any]:
    from datetime import date, datetime
    with get_db() as conn:
        clientes = conn.execute("SELECT id, nombre, saldo_actual, saldo_inicial FROM clientes").fetchall()
        remitos = conn.execute(
            """
            SELECT id, cliente_id, fecha, precio_venta_total, COALESCE(monto_pagado, 0.0) AS monto_pagado, pagado
            FROM remitos_carga
            WHERE pagado != 2
            """
        ).fetchall()
        
    now = date.today()
    buckets = {
        "0_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "90_plus": 0.0
    }
    
    detalles_clientes = {}
    for c in clientes:
        cid = c["id"]
        detalles_clientes[cid] = {
            "id": cid,
            "nombre": c["nombre"],
            "saldo_actual": float(c["saldo_actual"]),
            "buckets": {
                "0_30": 0.0,
                "31_60": 0.0,
                "61_90": 0.0,
                "90_plus": float(c["saldo_inicial"])
            }
        }
        
    for r in remitos:
        cid = r["cliente_id"]
        if not cid or cid not in detalles_clientes:
            continue
        try:
            r_fecha = datetime.strptime(r["fecha"], "%Y-%m-%d").date()
            diff_days = (now - r_fecha).days
        except Exception:
            diff_days = 0
            
        monto_deuda = float(r["precio_venta_total"]) - float(r["monto_pagado"])
        if monto_deuda <= 0:
            continue
            
        if diff_days <= 30:
            detalles_clientes[cid]["buckets"]["0_30"] += monto_deuda
        elif diff_days <= 60:
            detalles_clientes[cid]["buckets"]["31_60"] += monto_deuda
        elif diff_days <= 90:
            detalles_clientes[cid]["buckets"]["61_90"] += monto_deuda
        else:
            detalles_clientes[cid]["buckets"]["90_plus"] += monto_deuda

    for cid, det in detalles_clientes.items():
        for k in buckets:
            buckets[k] += det["buckets"][k]
            det["buckets"][k] = round(det["buckets"][k], 2)
        det["saldo_actual"] = round(det["saldo_actual"], 2)
            
    for k in buckets:
        buckets[k] = round(buckets[k], 2)
        
    return {
        "totales": buckets,
        "clientes": list(detalles_clientes.values())
    }

def calcular_margenes_ventas(limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.fecha, r.cliente, r.precio_venta_total, r.costo_carne, r.costo_total_logistica, r.kg
            FROM remitos_carga r
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        
    out = []
    for row in rows:
        r = dict(row)
        p_venta = float(r["precio_venta_total"])
        c_carne = float(r["costo_carne"])
        c_logistica = float(r["costo_total_logistica"])
        
        margen_bruto = p_venta - c_carne
        margen_neto = p_venta - c_carne - c_logistica
        pct_margen = (margen_neto / p_venta * 100.0) if p_venta > 0 else 0.0
        
        out.append({
            "id": r["id"],
            "fecha": r["fecha"],
            "cliente": r["cliente"],
            "kg": r["kg"],
            "precio_venta_total": round(p_venta, 2),
            "costo_carne": round(c_carne, 2),
            "costo_logistica": round(c_logistica, 2),
            "margen_bruto": round(margen_bruto, 2),
            "margen_neto": round(margen_neto, 2),
            "porcentaje_margen": round(pct_margen, 1)
        })
    return out
