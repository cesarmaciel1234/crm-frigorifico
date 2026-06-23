from flask import jsonify, request
from app.routes.api import api_bp, _guard_master_admin
from app.database import get_db
from app.utils import parse_operacion_payload, fmt_plazo_dias, _i, _f, resolve_remito_kg, pesos_piezas_to_json
from app.security import require_master_password_in_request, role_at_least
from app.services.audit import log_audit
from datetime import date


from app.services.finanzas import panel_estrategia, ranking_enemigos, historial_vencimientos
from app.services.remitos import list_remitos
from app.services.bancos import list_bancos
from app.services.clientes import list_perdidas_acumuladas
from app.services.audit import list_audit_log
from app.services.export_data import export_all_data
from app.services.import_data import import_all_data
from app.services.users import get_empresa_config, save_empresa_config, list_users, create_user, update_user

@api_bp.route("/import", methods=["POST"])
@_guard_master_admin
def api_import():
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    
    # 1. Require master password
    try:
        require_master_password_in_request(password)
    except Exception as e:
        return jsonify({"error": str(e)}), 403

    # 2. Extract JSON payload
    backup_data = data.get("backup_data", {})
    if not backup_data:
        return jsonify({"error": "No backup data provided"}), 400

    # 3. Perform destructive import
    try:
        import_all_data(backup_data)
        log_audit("RESTORE", entidad="sistema", detalle="restauracion_backup_completa")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": f"Error al restaurar: {str(e)}"}), 500

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

@api_bp.route("/auditoria", methods=["GET"])
def api_get_auditoria():
    limit = request.args.get("limit", 200, type=int)
    offset = request.args.get("offset", 0, type=int)
    return jsonify(list_audit_log(limit=limit, offset=offset))

@api_bp.route("/auditoria/<int:audit_id>", methods=["DELETE"])
def api_delete_auditoria(audit_id: int):
    ok, msg = require_master_password_in_request()
    if not ok:
        return jsonify({"error": msg}), 403
    if not role_at_least("admin"):
        return jsonify({"error": "Solo administradores pueden eliminar auditoría"}), 403
    with get_db() as conn:
        n = conn.execute("DELETE FROM auditoria_operaciones WHERE id = ?", (audit_id,)).rowcount
    return jsonify({"ok": True}) if n else (jsonify({"error": "No encontrada"}), 404)

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
    except Exception as e:
        err_msg = str(e).lower()
        if "unique constraint" in err_msg or "already exists" in err_msg or "constraint failed" in err_msg:
            return jsonify({"error": "Entidad ya existe"}), 409
        return jsonify({"error": f"Error al registrar banco: {str(e)}"}), 500

@api_bp.route("/export")
def api_export():
    if not role_at_least("admin"):
        return jsonify({"error": "Permiso denegado"}), 403
    log_audit("EXPORT", entidad="sistema", detalle="exportacion_completa")
    return jsonify(export_all_data())

@api_bp.route("/empresa", methods=["GET", "PUT"])
def api_empresa():
    if request.method == "GET":
        return jsonify(get_empresa_config())
    if not role_at_least("admin"):
        return jsonify({"error": "Permiso denegado"}), 403
    d = request.get_json(silent=True) or {}
    saved = save_empresa_config(d)
    log_audit("CONFIG", entidad="empresa", detalle="actualizacion_datos")
    return jsonify(saved)

@api_bp.route("/usuarios", methods=["GET", "POST"])
def api_usuarios():
    if not role_at_least("admin"):
        return jsonify({"error": "Permiso denegado"}), 403
    if request.method == "GET":
        return jsonify(list_users())
    d = request.get_json(silent=True) or {}
    try:
        uid = create_user(
            d.get("username", ""),
            d.get("password", ""),
            d.get("role", "operador"),
            d.get("nombre", ""),
        )
        return jsonify({"id": uid, "ok": True}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@api_bp.route("/usuarios/<int:user_id>", methods=["PATCH"])
def api_usuario_update(user_id: int):
    if not role_at_least("admin"):
        return jsonify({"error": "Permiso denegado"}), 403
    d = request.get_json(silent=True) or {}
    try:
        user = update_user(
            user_id,
            role=d.get("role"),
            activo=d.get("activo") if "activo" in d else None,
            nombre=d.get("nombre"),
        )
        log_audit("CONFIG", entidad="usuario", entidad_id=user_id, detalle=f"rol={user['role']}")
        return jsonify(user)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
