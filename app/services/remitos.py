from app.database import get_db


# ==============================================================================
# 🥩 EL EXPERTO EN CARNE (remitos.py)
# Esta es otra oficina en la cocina del restaurante. Aquí está el encargado
# de revisar todos los envíos de carne, calcular los costos de envío (logística)
# y ver si estamos ganando o perdiendo plata con la venta.
# ==============================================================================

def _estado_cobro(pagado: int, monto_pagado: float = 0) -> str:
    if pagado == 1:
        return "cobrado"
    if pagado == 2:
        return "incobrable"
    if monto_pagado and monto_pagado > 0:
        return "parcial"
    return "pendiente"

# ------------------------------------------------------------------------------
# 📋 REVISAR EL HISTORIAL DE ENVÍOS (list_remitos)
# ¿Qué hace esto? El dueño quiere ver los últimos envíos de carne.
# El experto va a la Bóveda, saca la lista (SELECT), y con su calculadora
# saca el "Margen Neto" (cuánta plata real nos quedó en el bolsillo).
# ------------------------------------------------------------------------------
def list_remitos(limit: int = 50, offset: int = 0) -> list[dict]:
    limit = max(1, min(int(limit), 10_000))
    offset = max(0, int(offset))
    with get_db() as conn:
        # 1. Pide la lista a la Bóveda usando SQL
        rows = conn.execute(
            """
            SELECT r.id, r.fecha, COALESCE(c.nombre, '') AS cliente, r.cliente_id,
                   r.kg, r.costo_total_logistica, r.precio_venta_total,
                   r.plazo_cobro_dias, r.costo_carne, r.pagado, COALESCE(r.monto_pagado, 0) AS monto_pagado, r.created_at
            FROM remitos_carga r
            LEFT JOIN clientes c ON r.cliente_id = c.id
            ORDER BY r.id DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    out = []
    for row in rows:
        row_dict = dict(row)
        # Margen Neto Real = Venta - Logística - Costo de la Carne
        margen = row_dict["precio_venta_total"] - row_dict["costo_total_logistica"] - row_dict["costo_carne"]
        pagado = int(row_dict["pagado"] or 0)
        monto_pagado = float(row_dict.get("monto_pagado") or 0)
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
                "monto_pagado": monto_pagado,
                "estado_cobro": _estado_cobro(pagado, monto_pagado),
                "margen": round(margen, 2),
                "created_at": row_dict["created_at"],
            }
        )
    return out

def registrar_pago_remito(remito_id: int, monto: float) -> dict:
    from app.services.clientes import recalcular_saldo_cliente
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero")
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT cliente_id, precio_venta_total, COALESCE(monto_pagado, 0) AS monto_pagado, pagado
            FROM remitos_carga WHERE id = ?
            """,
            (remito_id,),
        ).fetchone()
        if not row:
            raise ValueError("Remito no encontrado")
        pagado_flag = int(row["pagado"] or 0)
        if pagado_flag == 1:
            raise ValueError("Este remito ya está cobrado")
        if pagado_flag == 2:
            raise ValueError("Remito incobrable")

        total = float(row["precio_venta_total"])
        ya_pagado = float(row["monto_pagado"] or 0)
        saldo = round(total - ya_pagado, 2)
        monto = round(float(monto), 2)
        if monto > saldo + 0.009:
            raise ValueError(f"El monto supera el saldo pendiente (${saldo:,.2f})")

        nuevo_pagado = round(ya_pagado + monto, 2)
        cobrado_completo = nuevo_pagado >= total - 0.009

        if cobrado_completo:
            conn.execute(
                "UPDATE remitos_carga SET monto_pagado = ?, pagado = 1 WHERE id = ?",
                (total, remito_id),
            )
        else:
            conn.execute(
                "UPDATE remitos_carga SET monto_pagado = ? WHERE id = ?",
                (nuevo_pagado, remito_id),
            )

        cliente_id = row["cliente_id"]
        if cliente_id:
            conn.execute(
                "UPDATE clientes SET fecha_ultimo_pago = date('now', 'localtime') WHERE id = ?",
                (cliente_id,),
            )
            recalcular_saldo_cliente(conn, cliente_id)

    return {
        "ok": True,
        "monto_pagado": monto,
        "total_pagado": nuevo_pagado,
        "saldo_restante": 0 if cobrado_completo else round(total - nuevo_pagado, 2),
        "cobrado_completo": cobrado_completo,
    }


def marcar_remito_pagado(remito_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT precio_venta_total, COALESCE(monto_pagado, 0) AS monto_pagado FROM remitos_carga WHERE id = ?",
            (remito_id,),
        ).fetchone()
        if not row:
            raise ValueError("Remito no encontrado")
        saldo = float(row["precio_venta_total"]) - float(row["monto_pagado"] or 0)
    registrar_pago_remito(remito_id, saldo)


def get_remito_detalle(remito_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT r.id, r.fecha, r.cliente_id, r.tipo_corte, r.cantidad, r.pesos_piezas, r.kg,
                   r.precio_por_kg, r.costo_total_logistica, r.precio_venta_total,
                   r.plazo_cobro_dias, r.costo_carne, r.pagado, COALESCE(r.monto_pagado, 0) AS monto_pagado, r.created_at,
                   c.nombre AS cliente_nombre, c.cuit AS cliente_cuit, c.direccion AS cliente_direccion,
                   c.telefono AS cliente_telefono, c.email AS cliente_email
            FROM remitos_carga r
            LEFT JOIN clientes c ON r.cliente_id = c.id
            WHERE r.id = ?
            """,
            (remito_id,)
        ).fetchone()
        
        if not row:
            raise ValueError("Remito no encontrado")
            
        r_dict = dict(row)
        
        # Obtener fracciones
        fracs = conn.execute(
            """
            SELECT id, lote_id, kg_descontados, costo_porcion, costo_logistica_porcion
            FROM remitos_fracciones
            WHERE remito_id = ?
            """,
            (remito_id,)
        ).fetchall()
        
    margen = r_dict["precio_venta_total"] - r_dict["costo_total_logistica"] - r_dict["costo_carne"]
    pagado = int(r_dict["pagado"] or 0)
    monto_pagado = float(r_dict.get("monto_pagado") or 0)
    
    from app.utils import pesos_piezas_from_json
    
    return {
        "id": r_dict["id"],
        "fecha": r_dict["fecha"],
        "cliente_id": r_dict["cliente_id"],
        "cliente_nombre": r_dict["cliente_nombre"] or "",
        "cliente_cuit": r_dict["cliente_cuit"] or "",
        "cliente_direccion": r_dict["cliente_direccion"] or "",
        "cliente_telefono": r_dict["cliente_telefono"] or "",
        "cliente_email": r_dict["cliente_email"] or "",
        "tipo_corte": r_dict["tipo_corte"],
        "cantidad": int(r_dict["cantidad"] or 0),
        "pesos_piezas": pesos_piezas_from_json(r_dict["pesos_piezas"]),
        "kg": r_dict["kg"],
        "precio_por_kg": r_dict["precio_por_kg"],
        "costo_total_logistica": r_dict["costo_total_logistica"],
        "precio_venta_total": r_dict["precio_venta_total"],
        "plazo_cobro_dias": r_dict["plazo_cobro_dias"],
        "costo_carne": r_dict["costo_carne"],
        "pagado": pagado,
        "monto_pagado": monto_pagado,
        "estado_cobro": _estado_cobro(pagado, monto_pagado),
        "margen": round(margen, 2),
        "created_at": r_dict["created_at"],
        "fracciones": [dict(f) for f in fracs]
    }


def eliminar_remito_logic(conn, remito_id: int):
    # 0. Verificar que no tenga pagos aplicados
    row = conn.execute("SELECT monto_pagado FROM remitos_carga WHERE id = ?", (remito_id,)).fetchone()
    if row and float(row["monto_pagado"] or 0) > 0:
        raise ValueError("Este remito tiene pagos aplicados. Por favor, elimine el pago primero.")

    # 1. Obtener las fracciones descontadas por FIFO para este remito
    fracs = conn.execute(
        "SELECT lote_id, kg_descontados FROM remitos_fracciones WHERE remito_id = ?",
        (remito_id,)
    ).fetchall()
    
    # 2. Devolver los kg a los lotes originales de compras_bulk
    for frac in fracs:
        conn.execute(
            """
            UPDATE compras_bulk
            SET kg_remanentes = CASE 
                WHEN kg_remanentes + ? > kg_totales THEN kg_totales 
                ELSE kg_remanentes + ? 
            END
            WHERE id = ?
            """,
            (frac["kg_descontados"], frac["kg_descontados"], frac["lote_id"])
        )
        
    # 3. Eliminar los registros de remitos_fracciones
    conn.execute("DELETE FROM remitos_fracciones WHERE remito_id = ?", (remito_id,))
    
    # 4. Eliminar el remito de remitos_carga
    conn.execute("DELETE FROM remitos_carga WHERE id = ?", (remito_id,))


def eliminar_remito(remito_id: int) -> int:
    from app.services.clientes import recalcular_saldo_cliente
    with get_db() as conn:
        row = conn.execute("SELECT cliente_id FROM remitos_carga WHERE id = ?", (remito_id,)).fetchone()
        if not row:
            raise ValueError("Remito no encontrado")
        cliente_id = row["cliente_id"]
        
        eliminar_remito_logic(conn, remito_id)
        
        if cliente_id:
            recalcular_saldo_cliente(conn, cliente_id)
            
    return cliente_id

