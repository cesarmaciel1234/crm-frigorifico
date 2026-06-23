from flask import jsonify, request
from app.routes.api import api_bp, _guard_master_admin
from app.database import get_db
from app.utils import parse_operacion_payload, fmt_plazo_dias, _i, _f, resolve_remito_kg, pesos_piezas_to_json
from app.security import require_master_password_in_request, role_at_least
from app.services.audit import log_audit
from datetime import date


from app.services.remitos import list_remitos, marcar_remito_pagado, registrar_pago_remito, get_remito_detalle, eliminar_remito
from app.services.bulk import registrar_lote_bulk, list_bulk_lots, fraccionar_lote_fifo
from app.services.clientes import buscar_o_crear_cliente, recalcular_saldo_cliente

@api_bp.route("/remitos", methods=["GET", "POST"])
def api_remitos_endpoint():
    if request.method == "GET":
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        return jsonify(list_remitos(limit=limit, offset=offset))

    d = request.get_json(silent=True) or {}
    try:
        fecha = str(d.get("fecha") or "").strip() or None
        kg, pesos_piezas, cantidad = resolve_remito_kg(d)
        precio_por_kg = _f(d.get("precio_por_kg"), "precio_por_kg")
        tipo_corte = str(d.get("tipo_corte") or "").strip()
        pesos_json = pesos_piezas_to_json(pesos_piezas)
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
            # 3. Aplicar descuento de stock FIFO y calcular costo de carne y logística
            costo_carne, fracciones = fraccionar_lote_fifo(conn, kg)
            costo = sum(f["costo_logistica_porcion"] for f in fracciones)
            
            venta = round((kg * precio_por_kg) + costo, 2)
            
            if saldo + venta > techo:
                raise ValueError(
                    f"Límite de crédito superado. Saldo actual: ${saldo:,.2f} + Venta: ${venta:,.2f} > Techo de deuda: ${techo:,.2f}"
                )
            
            # 4. Insertar el remito en remitos_carga
            if fecha:
                cur = conn.execute(
                    """
                    INSERT INTO remitos_carga
                        (fecha, cliente, cliente_id, tipo_corte, cantidad, pesos_piezas, kg, precio_por_kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (fecha, cliente, cid, tipo_corte, cantidad, pesos_json, kg, precio_por_kg, costo, venta, plazo, costo_carne),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO remitos_carga
                        (cliente, cliente_id, tipo_corte, cantidad, pesos_piezas, kg, precio_por_kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (cliente, cid, tipo_corte, cantidad, pesos_json, kg, precio_por_kg, costo, venta, plazo, costo_carne),
                )
            rid = cur.lastrowid
            
            # 5. Registrar el desglose de porciones descontadas
            for frac in fracciones:
                conn.execute(
                    """
                    INSERT INTO remitos_fracciones (remito_id, lote_id, kg_descontados, costo_porcion, costo_logistica_porcion)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (rid, frac["lote_id"], frac["kg_descontados"], frac["costo_porcion"], frac["costo_logistica_porcion"])
                )
                
            # 6. Recalcular el saldo actual de deuda del cliente
            recalcular_saldo_cliente(conn, cid)
            
        return jsonify({"id": rid, "margen": round(venta - costo - costo_carne, 2), "costo_carne": costo_carne}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        err_msg = str(e).lower()
        if "unique constraint" in err_msg or "already exists" in err_msg or "constraint failed" in err_msg:
            return jsonify({"error": "Error: Remito duplicado o conflicto de integridad."}), 400
        return jsonify({"error": f"Error al registrar remito: {str(e)}"}), 500

@api_bp.route("/bulk", methods=["GET", "POST"])
def api_bulk_endpoint():
    if request.method == "GET":
        return jsonify(list_bulk_lots())

    d = request.get_json(silent=True) or {}
    try:
        fecha = str(d.get("fecha") or "").strip() or None
        kg_totales = _f(d.get("kg_totales"), "kg_totales")
        costo_total_bulk = _f(d.get("costo_total_bulk"), "costo_total_bulk")
        costo_reparto = _f(d.get("costo_reparto") or 0, "costo_reparto")
        numero_lote = d.get("numero_lote")
        fecha_vencimiento = d.get("fecha_vencimiento")
        proveedor = d.get("proveedor")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        lote_id = registrar_lote_bulk(
            kg_totales, costo_total_bulk, costo_reparto, fecha,
            numero_lote, fecha_vencimiento, proveedor
        )
        return jsonify({
            "id": lote_id,
            "fecha": fecha or date.today().isoformat(),
            "kg_totales": kg_totales,
            "costo_total_bulk": costo_total_bulk,
            "costo_reparto": costo_reparto,
            "numero_lote": numero_lote or "",
            "fecha_vencimiento": fecha_vencimiento or "",
            "proveedor": proveedor or ""
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@api_bp.route("/remitos/<int:rid>/cobrar", methods=["POST"])
def api_remito_cobrar_endpoint(rid: int):
    try:
        d = request.get_json(silent=True) or {}
        monto = d.get("monto_pagado")
        if monto is None:
            marcar_remito_pagado(rid)
            return jsonify({"ok": True, "message": "Remito marcado como pagado/cobrado"})
        result = registrar_pago_remito(rid, float(monto))
        msg = "Pago parcial registrado" if not result["cobrado_completo"] else "Remito cobrado completamente"
        return jsonify({**result, "message": msg})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al registrar cobro de remito: {str(e)}"}), 500

@api_bp.route("/remitos/<int:rid>", methods=["GET"])
def api_remito_detalle_endpoint(rid: int):
    try:
        from app.services.remitos import get_remito_detalle
        return jsonify(get_remito_detalle(rid))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Error al cargar detalle de remito: {str(e)}"}), 500

@api_bp.route("/remitos/<int:rid>", methods=["DELETE"])
def api_eliminar_remito_endpoint(rid: int):
    try:
        guard = _guard_master_admin()
        if guard:
            return guard
        from app.services.remitos import eliminar_remito
        cliente_id = eliminar_remito(rid)
        return jsonify({"ok": True, "cliente_id": cliente_id, "message": "Remito eliminado con éxito (stock restituido)"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al eliminar remito: {str(e)}"}), 500

@api_bp.route("/remitos/<int:rid>/reset-pago", methods=["POST"])
def api_remito_reset_pago_endpoint(rid: int):
    try:
        guard = _guard_master_admin()
        if guard:
            return guard
        from app.services.clientes import recalcular_saldo_cliente
        with get_db() as conn:
            row = conn.execute("SELECT cliente_id FROM remitos_carga WHERE id = ?", (rid,)).fetchone()
            if not row:
                raise ValueError("Remito no encontrado")
            cliente_id = row["cliente_id"]
            
            conn.execute(
                "UPDATE remitos_carga SET monto_pagado = 0.0, pagado = 0 WHERE id = ?",
                (rid,)
            )
            if cliente_id:
                recalcular_saldo_cliente(conn, cliente_id)
                
        return jsonify({"ok": True, "message": "Pago restablecido con éxito. La deuda ha vuelto al cliente."})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al restablecer pago: {str(e)}"}), 500
