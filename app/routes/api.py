from flask import Blueprint, jsonify, request
import sqlite3
from datetime import date

from app.database import get_db
from app.utils import parse_operacion_payload, fmt_plazo_dias, _i, _f, resolve_remito_kg, pesos_piezas_to_json
from app.services.finanzas import (
    panel_estrategia, ranking_enemigos, historial_vencimientos, calc_cfr,
    calcular_antiguedad_deuda, calcular_margenes_ventas
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
from app.security import require_master_password_in_request, role_at_least
from app.services.audit import log_audit, list_audit_log
from app.services.export_data import export_all_data
from app.services.users import get_empresa_config, save_empresa_config, list_users, create_user, update_user

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

def _guard_master_admin():
    ok, msg = require_master_password_in_request()
    if not ok:
        return jsonify({"error": msg}), 403
    if not role_at_least("admin"):
        return jsonify({"error": "Solo administradores pueden realizar esta acción"}), 403
    return None


# ------------------------------------------------------------------------------
# IMPORTACIÓN DE SUBMÓDULOS (Rutas modulares)
# ------------------------------------------------------------------------------
from app.routes import api_sistema, api_finanzas, api_remitos, api_clientes, api_sync
