from flask import Blueprint, jsonify, request
import sqlite3
from datetime import date

from app.database import get_db
from app.utils import parse_operacion_payload, fmt_plazo_dias, _i, _f, resolve_remito_kg, pesos_piezas_to_json
from app.services.finanzas import (
    panel_estrategia, ranking_enemigos, historial_vencimientos, calc_cfr
)
from app.services.pagos import (
    list_historial_pagos, get_pagos_operacion, registrar_pago,
    calc_estado_vencimiento, calc_plan_cuotas, calc_diferencia_pago
)
from app.services.remitos import list_remitos, marcar_remito_pagado, registrar_pago_remito
from app.services.bancos import list_bancos
from app.services.bulk import registrar_lote_bulk, list_bulk_lots, fraccionar_lote_fifo
from app.services.clientes import list_clientes, registrar_cliente, get_cliente_detalle, buscar_o_crear_cliente, recalcular_saldo_cliente, marcar_cliente_incobrable, list_perdidas_acumuladas, registrar_pago_cliente_global
from app.services.ventas_mostrador import list_ventas_mostrador, sync_ventas_offline
from app.security import verify_audit_password

# ==============================================================================
# 🤵 EL MOZO DEL RESTAURANTE (api_bp)
# Esto es como la libreta del mozo. Cuando el cliente (la pantalla de la app)
# quiere hacer algo (ver datos, guardar ventas), llama a una "ruta" de aquí.
# ==============================================================================
api_bp = Blueprint('api', __name__, url_prefix='/api')

# ------------------------------------------------------------------------------
# 📊 RUTA: EL TABLERO PRINCIPAL (DASHBOARD)
# ¿Qué hace esto? Imagina que el dueño del restaurante pregunta: 
# "¿Cómo nos fue hoy?". El mozo corre a la cocina, le pide a todos los expertos 
# un resumen (bancos, ventas, remitos), los junta en un solo paquete y se lo
# entrega al dueño (la pantalla del celular) en formato de diccionario (JSON).
# ------------------------------------------------------------------------------
@api_bp.route("/dashboard")
def api_dashboard():
    # 1. Pide la estrategia y la lista de "enemigos" (deudores peligrosos)
    estrategia = panel_estrategia()
    enemigos = ranking_enemigos()
    
    # 2. Pide los últimos 8 remitos de carne
    remitos = list_remitos(8)
    
    # 3. Pide la historia clínica de vencimientos
    historial = historial_vencimientos()
    
    vencidos = [h for h in historial if h.get("vencido")]
    
    sangre_diaria = 0
    interes_diario = 0
    deuda_total = 0
    interes_acumulado = 0

    for d in enemigos:
        monto = d.get('recibido') or 0  # El capital original sin intereses
        interes = d.get('interes') or 0 # El interés calculado
        dias = max(1, d.get('dias_faltantes') or 30)

        # Usamos tu fórmula explícita
        sangre_diaria += (monto + interes) / dias
        interes_diario += interes / dias
        deuda_total += (monto + interes)
        interes_acumulado += interes

    # Lógica de cálculo solicitada por el usuario
    # Al capital neto (que restaba la deuda total) le devolvemos (descontamos) el interés acumulado para que sea puro capital
    capital_disponible = estrategia.get("activo", {}).get("capital_neto", 0) + interes_acumulado
    cubre = estrategia.get("activo", {}).get("activo_pendiente", 0) >= (estrategia.get("activo", {}).get("deuda_real", 0) - interes_acumulado)
    tendencia_capital = 'up' if cubre else 'down'
    
    metricas_flotantes = {
        "sangre": sangre_diaria,
        "int_diario": interes_diario,
        "deuda": deuda_total,
        "int_acumulado": interes_acumulado,
        "capital": capital_disponible,
        "tendencia": tendencia_capital
    }
    
    # 5. Devuelve todo empaquetado en una caja llamada "JSON" para que la app lo lea
    return jsonify(
        {
            "estrategia": estrategia,
            "enemigos": enemigos,
            "remitos": remitos,
            "bancos": list_bancos(),
            "historial": historial,
            "perdidas": list_perdidas_acumuladas(),
            "metricas_flotantes": metricas_flotantes,
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
                    (uuid, alias, tipo, recibido, pagar, meses, fecha_cierre, fecha_vencimiento, cuotas, kg, precio_kg, plazo_dias, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                params = (
                    uuid_val, payload["alias"], payload["tipo"], payload["recibido"], payload["pagar"],
                    payload["meses"], payload["fecha_cierre"], payload["fecha_vencimiento"],
                    payload["cuotas"], payload.get("kg"), payload.get("precio_kg"), payload.get("plazo_dias"),
                    fecha_inicio_val
                )
            else:
                query = """
                INSERT INTO operaciones_financieras
                    (uuid, alias, tipo, recibido, pagar, meses, fecha_cierre, fecha_vencimiento, cuotas, kg, precio_kg, plazo_dias)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                params = (
                    uuid_val, payload["alias"], payload["tipo"], payload["recibido"], payload["pagar"],
                    payload["meses"], payload["fecha_cierre"], payload["fecha_vencimiento"],
                    payload["cuotas"], payload.get("kg"), payload.get("precio_kg"), payload.get("plazo_dias")
                )

            cur = conn.execute(query, params)
            op_id = cur.lastrowid
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            return jsonify({"error": "Esta operación ya ha sido registrada"}), 400
        return jsonify({"error": f"Error de integridad: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Error al guardar la operación: {str(e)}"}), 500

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
        row = conn.execute("SELECT alias, recibido FROM operaciones_financieras WHERE id = ?", (op_id,)).fetchone()
        if not row:
            return jsonify({"error": "No encontrada"}), 404
        # Guardar en la bóveda de auditoría
        conn.execute(
            "INSERT INTO auditoria_operaciones (operacion_id, alias, accion, monto) VALUES (?, ?, 'ELIMINADO', ?)",
            (op_id, row["alias"], row["recibido"])
        )
        # Limpiar dependencias para no chocar con la base de datos
        conn.execute("DELETE FROM pagos_cuotas WHERE operacion_id = ?", (op_id,))
        # Eliminar finalmente
        conn.execute("DELETE FROM operaciones_financieras WHERE id = ?", (op_id,))
    return jsonify({"ok": True})

@api_bp.route("/auditoria", methods=["GET"])
def api_get_auditoria():
    with get_db() as conn:
        rows = conn.execute("SELECT id, operacion_id, alias, accion, monto, fecha FROM auditoria_operaciones ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])

@api_bp.route("/auditoria/<int:audit_id>", methods=["DELETE"])
def api_delete_auditoria(audit_id: int):
    d = request.get_json(silent=True) or {}
    if not verify_audit_password(d.get("password")):
        return jsonify({"error": "Contraseña incorrecta"}), 403
    with get_db() as conn:
        n = conn.execute("DELETE FROM auditoria_operaciones WHERE id = ?", (audit_id,)).rowcount
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
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            return jsonify({"error": "Error: Remito duplicado o conflicto de integridad."}), 400
        return jsonify({"error": f"Error de integridad en base de datos: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Error inesperado al registrar remito: {str(e)}"}), 500


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
        costo_reparto = _f(d.get("costo_reparto") or 0, "costo_reparto")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        lote_id = registrar_lote_bulk(kg_totales, costo_total_bulk, costo_reparto, fecha)
        return jsonify({
            "id": lote_id,
            "fecha": fecha or date.today().isoformat(),
            "kg_totales": kg_totales,
            "costo_total_bulk": costo_total_bulk,
            "costo_reparto": costo_reparto
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
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            return jsonify({"error": "El nombre del cliente ya existe"}), 400
        return jsonify({"error": f"Error de integridad: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Error inesperado al registrar cliente: {str(e)}"}), 500

@api_bp.route("/clientes/<int:cid>", methods=["GET"])
def api_cliente_detalle_endpoint(cid: int):
    try:
        return jsonify(get_cliente_detalle(cid))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Error al cargar detalle: {str(e)}"}), 500

@api_bp.route("/clientes/<int:cid>/cobrar", methods=["POST"])
def api_cliente_cobrar_endpoint(cid: int):
    try:
        d = request.get_json(silent=True) or {}
        monto = d.get("monto_pagado")
        if monto is None:
            return jsonify({"error": "monto_pagado es requerido"}), 400
        result = registrar_pago_cliente_global(cid, float(monto))
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al registrar cobro: {str(e)}"}), 500

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

@api_bp.route("/clientes/<int:cid>/incobrable", methods=["POST"])
def api_cliente_incobrable_endpoint(cid: int):
    try:
        pid = marcar_cliente_incobrable(cid)
        return jsonify({"ok": True, "perdida_id": pid, "message": "Cliente declarado como incobrable. Deuda transferida a Pérdidas Acumuladas."})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al declarar incobrable: {str(e)}"}), 500

@api_bp.route("/perdidas", methods=["GET"])
def api_perdidas_endpoint():
    return jsonify(list_perdidas_acumuladas())

@api_bp.route("/ventas_mostrador", methods=["GET"])
def api_ventas_mostrador_list():
    return jsonify(list_ventas_mostrador())

@api_bp.route("/ventas_mostrador/sync", methods=["POST"])
def api_ventas_mostrador_sync():
    d = request.get_json(silent=True) or {}
    ventas = d.get("ventas")
    if not isinstance(ventas, list) or not ventas:
        return jsonify({"error": "ventas: lista requerida"}), 400
    try:
        synced_ids = sync_ventas_offline(ventas)
        return jsonify({"ok": True, "synced_ids": synced_ids, "count": len(synced_ids)}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al sincronizar ventas offline: {str(e)}"}), 500
