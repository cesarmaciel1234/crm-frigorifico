from app.database import get_db


# ==============================================================================
# 🥩 EL EXPERTO EN CARNE (remitos.py)
# Esta es otra oficina en la cocina del restaurante. Aquí está el encargado
# de revisar todos los envíos de carne, calcular los costos de envío (logística)
# y ver si estamos ganando o perdiendo plata con la venta.
# ==============================================================================

def _estado_cobro(pagado: int) -> str:
    if pagado == 1:
        return "cobrado"
    if pagado == 2:
        return "incobrable"
    return "pendiente"

# ------------------------------------------------------------------------------
# 📋 REVISAR EL HISTORIAL DE ENVÍOS (list_remitos)
# ¿Qué hace esto? El dueño quiere ver los últimos envíos de carne.
# El experto va a la Bóveda, saca la lista (SELECT), y con su calculadora
# saca el "Margen Neto" (cuánta plata real nos quedó en el bolsillo).
# ------------------------------------------------------------------------------
def list_remitos(limit: int = 50) -> list[dict]:
    with get_db() as conn:
        # 1. Pide la lista a la Bóveda usando SQL
        rows = conn.execute(
            """
            SELECT r.id, r.fecha, COALESCE(c.nombre, r.cliente) AS cliente, r.cliente_id,
                   r.kg, r.costo_total_logistica, r.precio_venta_total,
                   r.plazo_cobro_dias, r.costo_carne, r.pagado, r.created_at
            FROM remitos_carga r
            LEFT JOIN clientes c ON r.cliente_id = c.id
            ORDER BY r.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        row_dict = dict(row)
        # Margen Neto Real = Venta - Logística - Costo de la Carne
        margen = row_dict["precio_venta_total"] - row_dict["costo_total_logistica"] - row_dict["costo_carne"]
        pagado = int(row_dict["pagado"] or 0)
        out.append(
            {
                "id": row_dict["id"],
                "fecha": row_dict["fecha"],
                "cliente": row_dict["cliente"] or "",
                "cliente_id": row_dict["cliente_id"],
                "kg": row_dict["kg"],
                "costo_total_logistica": row_dict["costo_total_logistica"],
                "precio_venta_total": row_dict["precio_venta_total"],
                "plazo_cobro_dias": row_dict["plazo_cobro_dias"],
                "costo_carne": row_dict["costo_carne"],
                "pagado": pagado,
                "estado_cobro": _estado_cobro(pagado),
                "margen": round(margen, 2),
                "created_at": row_dict["created_at"],
            }
        )
    return out

def marcar_remito_pagado(remito_id: int):
    from app.services.clientes import recalcular_saldo_cliente
    with get_db() as conn:
        row = conn.execute("SELECT cliente_id FROM remitos_carga WHERE id = ?", (remito_id,)).fetchone()
        if not row:
            raise ValueError("Remito no encontrado")
        cliente_id = row["cliente_id"]
        
        conn.execute("UPDATE remitos_carga SET pagado = 1 WHERE id = ?", (remito_id,))
        if cliente_id:
            conn.execute("UPDATE clientes SET fecha_ultimo_pago = date('now', 'localtime') WHERE id = ?", (cliente_id,))
            recalcular_saldo_cliente(conn, cliente_id)
