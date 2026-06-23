import os
import re

base_dir = r"c:\Users\cesar\Desktop\deudas pro\app\routes"
api_path = os.path.join(base_dir, "api.py")

with open(api_path, "r", encoding="utf-8") as f:
    api_content = f.read()

# Common imports for the submodules
common_imports = """from flask import jsonify, request
from app.routes.api import api_bp, _guard_master_admin
from app.database import get_db
from app.utils import parse_operacion_payload, fmt_plazo_dias, _i, _f, resolve_remito_kg, pesos_piezas_to_json
from app.security import require_master_password_in_request, role_at_least
from app.services.audit import log_audit
from datetime import date
"""

# Grouping definitions
groups = {
    "api_sistema.py": [
        "api_dashboard", "api_bancos_endpoint", "api_get_auditoria", "api_delete_auditoria",
        "api_export", "api_empresa", "api_usuarios", "api_usuario_update"
    ],
    "api_finanzas.py": [
        "api_historial_pagos", "api_historial", "api_estrategia", "api_enemigos",
        "api_finanzas_aging", "api_finanzas_margenes", "api_create_op", "api_delete_op",
        "api_plan_pago", "api_pagar_cuota", "api_preview_pago", "api_eliminar_pago_endpoint"
    ],
    "api_remitos.py": [
        "api_remitos_endpoint", "api_remito_cobrar_endpoint", "api_remito_detalle_endpoint",
        "api_eliminar_remito_endpoint", "api_remito_reset_pago_endpoint", "api_bulk_endpoint"
    ],
    "api_clientes.py": [
        "api_clientes_endpoint", "api_cliente_detalle_endpoint", "api_cliente_cobrar_endpoint",
        "api_cliente_incobrable_endpoint", "api_perdidas_endpoint", "api_ventas_mostrador_list",
        "api_ventas_mostrador_sync", "api_cliente_saldo_inicial_endpoint", "api_eliminar_cliente_endpoint"
    ]
}

# The regex will capture each route function
# We split by @api_bp.route
blocks = api_content.split("@api_bp.route")

# The first block is the header
header = blocks[0]

# remove the _guard_master_admin from header if it exists there, but actually it's below
new_api_content = header.strip() + "\n\n"

# Extract _guard_master_admin and put it in api.py
guard_match = re.search(r"def _guard_master_admin\(\):.*?return None\n", api_content, re.DOTALL)
if guard_match:
    new_api_content += guard_match.group(0) + "\n"

# Prepare files
files_content = {k: common_imports + "\n" for k in groups.keys()}

# Specific imports for each module
files_content["api_sistema.py"] += """
from app.services.finanzas import panel_estrategia, ranking_enemigos, historial_vencimientos
from app.services.remitos import list_remitos
from app.services.bancos import list_bancos
from app.services.clientes import list_perdidas_acumuladas
from app.services.audit import list_audit_log
from app.services.export_data import export_all_data
from app.services.users import get_empresa_config, save_empresa_config, list_users, create_user, update_user
"""

files_content["api_finanzas.py"] += """
from app.services.finanzas import (panel_estrategia, ranking_enemigos, historial_vencimientos, calc_cfr, calcular_antiguedad_deuda, calcular_margenes_ventas)
from app.services.pagos import (list_historial_pagos, get_pagos_operacion, registrar_pago, calc_estado_vencimiento, calc_plan_cuotas, calc_diferencia_pago)
from app.services.clientes import eliminar_pago_cliente
"""

files_content["api_remitos.py"] += """
from app.services.remitos import list_remitos, marcar_remito_pagado, registrar_pago_remito, get_remito_detalle, eliminar_remito
from app.services.bulk import registrar_lote_bulk, list_bulk_lots, fraccionar_lote_fifo
from app.services.clientes import buscar_o_crear_cliente, recalcular_saldo_cliente
"""

files_content["api_clientes.py"] += """
from app.services.clientes import (list_clientes, registrar_cliente, get_cliente_detalle, actualizar_cliente, registrar_pago_cliente_global, marcar_cliente_incobrable, list_perdidas_acumuladas, actualizar_saldo_inicial, eliminar_cliente)
from app.services.ventas_mostrador import list_ventas_mostrador, sync_ventas_offline
"""

for block in blocks[1:]:
    full_block = "@api_bp.route" + block
    # Find which function it is
    match = re.search(r"def (api_[a-zA-Z0-9_]+)\(", full_block)
    if not match:
        continue
    
    func_name = match.group(1)
    
    # Find where to put it
    placed = False
    for fname, func_list in groups.items():
        if func_name in func_list:
            files_content[fname] += "\n" + full_block.strip() + "\n"
            placed = True
            break
            
    if not placed:
        print(f"Warning: function {func_name} not found in any group!")
        new_api_content += "\n" + full_block.strip() + "\n"

# Write the submodules
for fname, content in files_content.items():
    with open(os.path.join(base_dir, fname), "w", encoding="utf-8") as f:
        f.write(content)

# Append the imports to api.py
new_api_content += "\n# ------------------------------------------------------------------------------\n"
new_api_content += "# IMPORTACIÓN DE SUBMÓDULOS (Rutas modulares)\n"
new_api_content += "# ------------------------------------------------------------------------------\n"
new_api_content += "from app.routes import api_sistema, api_finanzas, api_remitos, api_clientes\n"

# Rewrite api.py
with open(api_path, "w", encoding="utf-8") as f:
    f.write(new_api_content)

print("Modularization complete.")
