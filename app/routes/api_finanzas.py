from flask import jsonify, request
from app.routes.api import api_bp, _guard_master_admin
from app.database import get_db
from app.utils import parse_operacion_payload, fmt_plazo_dias, _i, _f, resolve_remito_kg, pesos_piezas_to_json
from app.security import require_master_password_in_request, role_at_least
from app.services.signal_bus import notify_tenant_refresh_from_request
from app.services.audit import log_audit
from datetime import date


from app.services.finanzas import (panel_estrategia, ranking_enemigos, historial_vencimientos, calc_cfr, calcular_antiguedad_deuda, calcular_margenes_ventas)
from app.services.pagos import (list_historial_pagos, get_pagos_operacion, registrar_pago, calc_estado_vencimiento, calc_plan_cuotas, calc_diferencia_pago)
from app.services.clientes import eliminar_pago_cliente

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

@api_bp.route("/finanzas/aging")
def api_finanzas_aging():
    return jsonify(calcular_antiguedad_deuda())

@api_bp.route("/finanzas/margenes")
def api_finanzas_margenes():
    limit = request.args.get("limit", 200, type=int)
    return jsonify(calcular_margenes_ventas(limit=limit))

# ------------------------------------------------------------------------------
# 📝 RUTA: GUARDAR UNA OPERACIÓN (NUEVA DEUDA)
# ¿Qué hace esto? Imagina que la pantalla del celular le dice al Mozo:
# "¡Oye, acabo de tomar una nueva deuda!". 
# Aquí el Mozo recibe la nota, revisa que esté bien escrita (parse_operacion),
# calcula si el interés es peligroso (CFR), y luego corre a la Bóveda a guardarla.
# ------------------------------------------------------------------------------

@api_bp.route("/operaciones", methods=["POST"])
def api_create_op():
    # 1. El Mozo lee la nota (el JSON) que le mandó la pantalla
    d = request.get_json(silent=True) or {}
    
    try:
        # 2. Revisa que no falten datos importantes (nombres, montos)
        payload = parse_operacion_payload(d)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # 3. Hace matemáticas: ¿Es un trato justo o un robo? Calcula el Costo Financiero Real (CFR)
    cfr = None if payload["tipo"].lower() in ("cheque", "proveedor") else calc_cfr(payload["recibido"], payload["pagar"], payload["meses"])
    
    # 4. El UUID es como un código de barras único para que no guardemos esto dos veces
    uuid_val = d.get("uuid")
    
    # 5. ¡Abre la Bóveda de acero!
    try:
        with get_db() as conn:
            if uuid_val:
                # Primero pregunta: "¿Ya guardé esto antes?"
                row = conn.execute("SELECT id FROM operaciones_financieras WHERE uuid = ?", (uuid_val,)).fetchone()
                if row:
                    return jsonify({"id": row["id"], "ok": True, "duplicate": True}), 200

            # 6. Lo guarda en el archivo correcto de la Bóveda usando el lenguaje secreto SQL (INSERT INTO)
            fecha_inicio_val = payload.get("fecha_inicio")
            if fecha_inicio_val:
                fecha_inicio_val = f"{fecha_inicio_val} 12:00:00"
                query = """
                INSERT INTO operaciones_financieras
                    (uuid, alias, tipo, recibido, pagar, meses, fecha_cierre, fecha_vencimiento, cuotas, kg, precio_kg, plazo_dias, impuesto_cheque, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                params = (
                    uuid_val, payload["alias"], payload["tipo"], payload["recibido"], payload["pagar"],
                    payload["meses"], payload["fecha_cierre"], payload["fecha_vencimiento"],
                    payload["cuotas"], payload.get("kg"), payload.get("precio_kg"), payload.get("plazo_dias"),
                    payload.get("impuesto_cheque"),
                    fecha_inicio_val
                )
            else:
                query = """
                INSERT INTO operaciones_financieras
                    (uuid, alias, tipo, recibido, pagar, meses, fecha_cierre, fecha_vencimiento, cuotas, kg, precio_kg, plazo_dias, impuesto_cheque)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                params = (
                    uuid_val, payload["alias"], payload["tipo"], payload["recibido"], payload["pagar"],
                    payload["meses"], payload["fecha_cierre"], payload["fecha_vencimiento"],
                    payload["cuotas"], payload.get("kg"), payload.get("precio_kg"), payload.get("plazo_dias"),
                    payload.get("impuesto_cheque"),
                )

            cur = conn.execute(query, params)
            op_id = cur.lastrowid
    except Exception as e:
        err_msg = str(e).lower()
        if "unique constraint" in err_msg or "already exists" in err_msg or "constraint failed" in err_msg:
            return jsonify({"error": "Esta operación ya ha sido registrada"}), 400
        return jsonify({"error": f"Error al guardar la operación: {str(e)}"}), 500

    notify_tenant_refresh_from_request("create_operacion")

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
    ok, msg = require_master_password_in_request()
    if not ok:
        return jsonify({"error": msg}), 403
    if not role_at_least("admin"):
        return jsonify({"error": "Solo administradores pueden eliminar operaciones"}), 403
    with get_db() as conn:
        row = conn.execute("SELECT alias, recibido FROM operaciones_financieras WHERE id = ?", (op_id,)).fetchone()
        if not row:
            return jsonify({"error": "No encontrada"}), 404
        notify_tenant_refresh_from_request("delete_operacion")
        conn.execute("DELETE FROM pagos_cuotas WHERE operacion_id = ?", (op_id,))
        conn.execute("DELETE FROM operaciones_financieras WHERE id = ?", (op_id,))
    log_audit(
        "ELIMINADO",
        entidad="operacion",
        entidad_id=op_id,
        alias=row["alias"],
        monto=row["recibido"],
        operacion_id=op_id,
    )
    return jsonify({"ok": True})

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
    notify_tenant_refresh_from_request("pago_cuota")
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

@api_bp.route("/pagos/<int:pago_id>", methods=["DELETE"])
def api_eliminar_pago_endpoint(pago_id: int):
    try:
        guard = _guard_master_admin()
        if guard:
            return guard
        notify_tenant_refresh_from_request("delete_pago")
        from app.services.clientes import eliminar_pago_cliente
        eliminar_pago_cliente(pago_id)
        return jsonify({"ok": True, "message": "Pago eliminado con éxito (deuda restituida)"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al eliminar pago: {str(e)}"}), 500
