from flask import jsonify, request
from app.routes.api import api_bp, _guard_master_admin
from app.database import get_db
from app.utils import parse_operacion_payload, fmt_plazo_dias, _i, _f, resolve_remito_kg, pesos_piezas_to_json
from app.security import require_master_password_in_request, role_at_least
from app.services.audit import log_audit
from datetime import date


from app.services.clientes import (list_clientes, registrar_cliente, get_cliente_detalle, actualizar_cliente, registrar_pago_cliente_global, marcar_cliente_incobrable, list_perdidas_acumuladas, actualizar_saldo_inicial, eliminar_cliente)
from app.services.ventas_mostrador import list_ventas_mostrador, sync_ventas_offline
from app.services.signal_bus import notify_tenant_refresh_from_request

@api_bp.route("/clientes", methods=["GET", "POST"])
def api_clientes_endpoint():
    if request.method == "GET":
        limit = request.args.get("limit", type=int)
        offset = request.args.get("offset", 0, type=int)
        return jsonify(list_clientes(limit=limit, offset=offset))

    d = request.get_json(silent=True) or {}
    try:
        nombre = str(d.get("nombre") or "").strip()
        techo_deuda = _f(d.get("techo_deuda"), "techo_deuda")
        scoring = str(d.get("scoring") or "A").strip().upper()
        telefono = d.get("telefono")
        if telefono is not None:
            telefono = str(telefono).strip() or None
        cuit = d.get("cuit")
        if cuit is not None:
            cuit = str(cuit).strip() or None
        direccion = d.get("direccion")
        if direccion is not None:
            direccion = str(direccion).strip() or None
        email = d.get("email")
        if email is not None:
            email = str(email).strip() or None
        saldo_inicial = _f(d.get("saldo_inicial") or 0.0, "saldo_inicial")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        cid = registrar_cliente(nombre, techo_deuda, scoring, telefono, cuit, direccion, email, saldo_inicial)
        return jsonify({
            "id": cid,
            "nombre": nombre,
            "techo_deuda": techo_deuda,
            "scoring": scoring,
            "telefono": telefono,
            "cuit": cuit,
            "direccion": direccion,
            "email": email
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        err_msg = str(e).lower()
        if "unique constraint" in err_msg or "already exists" in err_msg or "constraint failed" in err_msg:
            return jsonify({"error": "¡Epa! Ese cliente ya está en la lista. ¡Probá con otro nombre!"}), 400
        return jsonify({"error": f"Error al registrar cliente: {str(e)}"}), 500

# ------------------------------------------------------------------------------
# 🗑️ RUTAS DE ELIMINACIÓN CON CONTRASEÑA MAESTRA
# ------------------------------------------------------------------------------
def _guard_master_admin():
    ok, msg = require_master_password_in_request()
    if not ok:
        return jsonify({"error": msg}), 403
    if not role_at_least("admin"):
        return jsonify({"error": "Solo administradores pueden realizar esta acción"}), 403
    return None

@api_bp.route("/clientes/<int:cid>", methods=["GET", "PUT"])
def api_cliente_detalle_endpoint(cid: int):
    if request.method == "GET":
        try:
            return jsonify(get_cliente_detalle(cid))
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            return jsonify({"error": f"Error al cargar detalle: {str(e)}"}), 500

    # PUT method
    d = request.get_json(silent=True) or {}
    try:
        nombre = str(d.get("nombre") or "").strip()
        techo_deuda = _f(d.get("techo_deuda"), "techo_deuda")
        scoring = str(d.get("scoring") or "A").strip().upper()
        telefono = d.get("telefono")
        if telefono is not None:
            telefono = str(telefono).strip() or None
        cuit = d.get("cuit")
        if cuit is not None:
            cuit = str(cuit).strip() or None
        direccion = d.get("direccion")
        if direccion is not None:
            direccion = str(direccion).strip() or None
        email = d.get("email")
        if email is not None:
            email = str(email).strip() or None
        saldo_inicial = _f(d.get("saldo_inicial") or 0.0, "saldo_inicial")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        from app.services.clientes import actualizar_cliente
        actualizar_cliente(cid, nombre, techo_deuda, scoring, telefono, cuit, direccion, email, saldo_inicial)
        return jsonify({"ok": True, "message": "Cliente actualizado con éxito"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al actualizar cliente: {str(e)}"}), 500

@api_bp.route("/clientes/<int:cid>/cobrar", methods=["POST"])
def api_cliente_cobrar_endpoint(cid: int):
    try:
        d = request.get_json(silent=True) or {}
        monto = d.get("monto_pagado")
        if monto is None:
            return jsonify({"error": "monto_pagado es requerido"}), 400
        result = registrar_pago_cliente_global(cid, float(monto))
        notify_tenant_refresh_from_request("cobro_cliente")
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al registrar cobro: {str(e)}"}), 500

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

@api_bp.route("/clientes/<int:cid>/saldo-inicial", methods=["POST"])
def api_cliente_saldo_inicial_endpoint(cid: int):
    try:
        guard = _guard_master_admin()
        if guard:
            return guard
        d = request.get_json(silent=True) or {}
        monto = float(d.get("saldo_inicial", 0.0))
        if monto < 0:
            raise ValueError("El saldo inicial no puede ser negativo")
            
        from app.services.clientes import actualizar_saldo_inicial
        nuevo_saldo = actualizar_saldo_inicial(cid, monto)
        return jsonify({"ok": True, "saldo_actual": nuevo_saldo, "message": "Saldo inicial actualizado con éxito"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al actualizar saldo inicial: {str(e)}"}), 500

@api_bp.route("/clientes/<int:cid>", methods=["DELETE"])
def api_eliminar_cliente_endpoint(cid: int):
    try:
        guard = _guard_master_admin()
        if guard:
            return guard
        notify_tenant_refresh_from_request("delete_cliente")
        from app.services.clientes import eliminar_cliente
        eliminar_cliente(cid)
        return jsonify({"ok": True, "message": "Cliente y sus remitos eliminados con éxito (stock restituido)"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al eliminar cliente: {str(e)}"}), 500
