from flask import Blueprint, jsonify, request
import sqlite3
from datetime import date

from app.database import get_db
from app.utils import parse_operacion_payload, fmt_plazo_dias, _i, _f
from app.services.finanzas import (
    panel_estrategia, ranking_enemigos, historial_vencimientos, calc_cfr
)
from app.services.pagos import (
    list_historial_pagos, get_pagos_operacion, registrar_pago,
    calc_estado_vencimiento, calc_plan_cuotas, calc_diferencia_pago
)
from app.services.remitos import list_remitos, marcar_remito_pagado
from app.services.bancos import list_bancos
from app.services.bulk import registrar_lote_bulk, list_bulk_lots, fraccionar_lote_fifo
from app.services.clientes import list_clientes, registrar_cliente, get_cliente_detalle, buscar_o_crear_cliente, recalcular_saldo_cliente, marcar_cliente_incobrable, list_perdidas_acumuladas

api_bp = Blueprint('api', __name__, url_prefix='/api')

@api_bp.route("/dashboard")
def api_dashboard():
    estrategia = panel_estrategia()
    enemigos = ranking_enemigos()
    remitos = list_remitos(8)
    historial = historial_vencimientos()
    vencidos = [h for h in historial if h.get("vencido")]
    return jsonify(
        {
            "estrategia": estrategia,
            "enemigos": enemigos,
            "remitos": remitos,
            "bancos": list_bancos(),
            "historial": historial,
            "perdidas": list_perdidas_acumuladas(),
            "totales": {
                "deudas_activas": len(enemigos),
                "urgentes": sum(1 for e in enemigos if e.get("urgente")),
                "remitos_recientes": len(remitos),
                "intereses_totales": estrategia["sangria"]["intereses_totales"],
                "tarjetas_vencidas": len(vencidos),
                "total_pagar_vencido": round(sum(h["total_pagar"] for h in vencidos), 2),
            },
        }
    )

@api_bp.route("/historial-pagos")
def api_historial_pagos():
    tipo = (request.args.get("tipo") or "").strip().lower() or None
    if tipo and tipo not in ("tarjeta", "cheque", "proveedor", "banco", "otro"):
        return jsonify({"error": "tipo inválido"}), 400
    return jsonify(list_historial_pagos(tipo=tipo))

@api_bp.route("/historial")
def api_historial():
    return jsonify(historial_vencimientos())

@api_bp.route("/estrategia")
def api_estrategia():
    ex = request.args.get("excedente", type=float)
    return jsonify(panel_estrategia(ex))

@api_bp.route("/enemigos")
def api_enemigos():
    return jsonify(ranking_enemigos())

@api_bp.route("/operaciones", methods=["POST"])
def api_create_op():
    d = request.get_json(silent=True) or {}
    try:
        payload = parse_operacion_payload(d)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    cfr = None if payload["tipo"].lower() in ("cheque", "proveedor") else calc_cfr(payload["recibido"], payload["pagar"], payload["meses"])
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO operaciones_financieras
                (alias, tipo, recibido, pagar, meses, fecha_cierre, fecha_vencimiento, cuotas, kg, precio_kg, plazo_dias)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["alias"],
                payload["tipo"],
                payload["recibido"],
                payload["pagar"],
                payload["meses"],
                payload["fecha_cierre"],
                payload["fecha_vencimiento"],
                payload["cuotas"],
                payload.get("kg"),
                payload.get("precio_kg"),
                payload.get("plazo_dias"),
            ),
        )
        op_id = cur.lastrowid

    return jsonify(
        {
            "id": op_id,
            "alias": payload["alias"],
            "tipo": payload["tipo"],
            "cfr": round(cfr, 2) if cfr else None,
            "urgente": cfr is not None and cfr > 10,
            "fecha_cierre": payload["fecha_cierre"],
            "fecha_vencimiento": payload["fecha_vencimiento"],
            "cuotas": payload["cuotas"],
            "plazo_texto": fmt_plazo_dias(payload.get("plazo_dias")),
            "kg": payload.get("kg"),
            "precio_kg": payload.get("precio_kg"),
            "plazo_dias": payload.get("plazo_dias"),
        }
    ), 201

@api_bp.route("/operaciones/<int:op_id>", methods=["DELETE"])
def api_delete_op(op_id: int):
    with get_db() as conn:
        n = conn.execute(
            "DELETE FROM operaciones_financieras WHERE id = ?", (op_id,)
        ).rowcount
    return jsonify({"ok": True}) if n else (jsonify({"error": "No encontrada"}), 404)

@api_bp.route("/operaciones/<int:op_id>/plan-pago")
def api_plan_pago(op_id: int):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, alias, tipo, recibido, pagar, meses, fecha_cierre,
                   fecha_vencimiento, cuotas, COALESCE(cuotas_pagadas, 0) AS cuotas_pagadas
            FROM operaciones_financieras WHERE id = ?
            """,
            (op_id,),
        ).fetchone()
    if not row:
        return jsonify({"error": "No encontrada"}), 404

    row_dict = dict(row)
    tipo = (row_dict["tipo"] or "").lower()
    venc = calc_estado_vencimiento(
        row_dict["fecha_vencimiento"] if tipo in ("tarjeta", "cheque", "proveedor") else None
    )
    plan = calc_plan_cuotas(row_dict, venc)
    return jsonify(
        {
            "id": row_dict["id"],
            "alias": row_dict["alias"],
            "tipo": row_dict["tipo"],
            "total_pagar": round(row_dict["pagar"], 2),
            "pagos": get_pagos_operacion(op_id),
            **venc,
            **plan,
        }
    )

@api_bp.route("/operaciones/<int:op_id>/pagar", methods=["POST"])
def api_pagar_cuota(op_id: int):
    d = request.get_json(silent=True) or {}
    try:
        numero_cuota = _i(d.get("numero_cuota"), "numero_cuota")
        monto_pagado = _f(d.get("monto_pagado"), "monto_pagado")
        if monto_pagado <= 0:
            raise ValueError("monto_pagado debe ser mayor a 0")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = registrar_pago(op_id, numero_cuota, monto_pagado)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result), 201

@api_bp.route("/operaciones/<int:op_id>/preview-pago")
def api_preview_pago(op_id: int):
    numero = request.args.get("numero_cuota", type=int)
    monto = request.args.get("monto_pagado", type=float)
    if not numero or monto is None:
        return jsonify({"error": "numero_cuota y monto_pagado requeridos"}), 400
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT pagar, cuotas, cuotas_pagadas, tipo, fecha_vencimiento
                FROM operaciones_financieras WHERE id = ?
                """,
                (op_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "No encontrada"}), 404
            
        row_dict = dict(row)
        tipo = (row_dict["tipo"] or "").lower()
        venc = calc_estado_vencimiento(
            row_dict["fecha_vencimiento"] if tipo in ("tarjeta", "cheque", "proveedor") else None
        )
        plan = calc_plan_cuotas(row_dict, venc)
        if not plan["tiene_cuotas"]:
            return jsonify({"error": "Sin plan de cuotas"}), 400
        diff = calc_diferencia_pago(plan["monto_cuota"], monto)
        return jsonify(
            {
                "monto_cuota_esperado": plan["monto_cuota"],
                "monto_pagado": round(monto, 2),
                **diff,
            }
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@api_bp.route("/remitos", methods=["GET", "POST"])
def api_remitos_endpoint():
    if request.method == "GET":
        return jsonify(list_remitos())

    d = request.get_json(silent=True) or {}
    try:
        fecha = str(d.get("fecha") or "").strip() or None
        kg = _f(d.get("kg"), "kg")
        if kg <= 0:
            raise ValueError("kg debe ser > 0")
        costo = _f(d.get("costo_total_logistica"), "costo_total_logistica")
        venta = _f(d.get("precio_venta_total"), "precio_venta_total")
        plazo = int(d.get("plazo_cobro_dias") or 0)
        if plazo < 0:
            raise ValueError("plazo_cobro_dias inválido")
        cliente = str(d.get("cliente") or "").strip()
        if not cliente:
            raise ValueError("cliente: nombre o código requerido")
        if len(cliente) > 50:
            raise ValueError("cliente: máximo 50 caracteres")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        with get_db() as conn:
            # 1. Buscar o registrar cliente automáticamente
            cid = buscar_o_crear_cliente(conn, cliente)
            
            # 2. Obtener saldo actual y techo de deuda del cliente para validar límite
            cli = conn.execute("SELECT nombre, techo_deuda, saldo_actual FROM clientes WHERE id = ?", (cid,)).fetchone()
            if cli:
                techo = float(cli["techo_deuda"])
                saldo = float(cli["saldo_actual"])
                if saldo + venta > techo:
                    raise ValueError(
                        f"Límite de crédito superado. Saldo actual: ${saldo:,.2f} + Venta: ${venta:,.2f} > Techo de deuda: ${techo:,.2f}"
                    )
            
            # 3. Aplicar descuento de stock FIFO y calcular costo de carne
            costo_carne, fracciones = fraccionar_lote_fifo(conn, kg)
            
            # 4. Insertar el remito en remitos_carga con costo_carne, cliente_id y pagado=0
            if fecha:
                cur = conn.execute(
                    """
                    INSERT INTO remitos_carga
                        (fecha, cliente, cliente_id, kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (fecha, cliente, cid, kg, costo, venta, plazo, costo_carne),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO remitos_carga
                        (cliente, cliente_id, kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (cliente, cid, kg, costo, venta, plazo, costo_carne),
                )
            rid = cur.lastrowid
            
            # 5. Registrar el desglose de porciones descontadas
            for frac in fracciones:
                conn.execute(
                    """
                    INSERT INTO remitos_fracciones (remito_id, lote_id, kg_descontados, costo_porcion)
                    VALUES (?, ?, ?, ?)
                    """,
                    (rid, frac["lote_id"], frac["kg_descontados"], frac["costo_porcion"])
                )
                
            # 6. Recalcular el saldo actual de deuda del cliente
            recalcular_saldo_cliente(conn, cid)
            
        return jsonify({"id": rid, "margen": round(venta - costo - costo_carne, 2), "costo_carne": costo_carne}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@api_bp.route("/bancos", methods=["GET", "POST"])
def api_bancos_endpoint():
    if request.method == "GET":
        return jsonify(list_bancos())

    d = request.get_json(silent=True) or {}
    nombre = str(d.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "nombre obligatorio"}), 400
    try:
        limite = _f(d.get("limite"), "limite")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO entidades_bancarias (nombre, limite) VALUES (?, ?)",
                (nombre, limite),
            )
            bid = cur.lastrowid
        return jsonify({"id": bid, "nombre": nombre, "limite": limite}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Entidad ya existe"}), 409

@api_bp.route("/bulk", methods=["GET", "POST"])
def api_bulk_endpoint():
    if request.method == "GET":
        return jsonify(list_bulk_lots())

    d = request.get_json(silent=True) or {}
    try:
        fecha = str(d.get("fecha") or "").strip() or None
        kg_totales = _f(d.get("kg_totales"), "kg_totales")
        costo_total_bulk = _f(d.get("costo_total_bulk"), "costo_total_bulk")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        lote_id = registrar_lote_bulk(kg_totales, costo_total_bulk, fecha)
        return jsonify({
            "id": lote_id,
            "fecha": fecha or date.today().isoformat(),
            "kg_totales": kg_totales,
            "costo_total_bulk": costo_total_bulk
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@api_bp.route("/clientes", methods=["GET", "POST"])
def api_clientes_endpoint():
    if request.method == "GET":
        return jsonify(list_clientes())

    d = request.get_json(silent=True) or {}
    try:
        nombre = str(d.get("nombre") or "").strip()
        techo_deuda = _f(d.get("techo_deuda"), "techo_deuda")
        scoring = str(d.get("scoring") or "A").strip().upper()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        cid = registrar_cliente(nombre, techo_deuda, scoring)
        return jsonify({"id": cid, "nombre": nombre, "techo_deuda": techo_deuda, "scoring": scoring}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@api_bp.route("/clientes/<int:cid>", methods=["GET"])
def api_cliente_detalle_endpoint(cid: int):
    try:
        return jsonify(get_cliente_detalle(cid))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

@api_bp.route("/remitos/<int:rid>/cobrar", methods=["POST"])
def api_remito_cobrar_endpoint(rid: int):
    try:
        marcar_remito_pagado(rid)
        return jsonify({"ok": True, "message": "Remito marcado como pagado/cobrado"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@api_bp.route("/clientes/<int:cid>/incobrable", methods=["POST"])
def api_cliente_incobrable_endpoint(cid: int):
    try:
        pid = marcar_cliente_incobrable(cid)
        return jsonify({"ok": True, "perdida_id": pid, "message": "Cliente declarado como incobrable. Deuda transferida a Pérdidas Acumuladas."})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@api_bp.route("/perdidas", methods=["GET"])
def api_perdidas_endpoint():
    return jsonify(list_perdidas_acumuladas())
