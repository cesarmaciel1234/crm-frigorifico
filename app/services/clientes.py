from app.database import get_db
from app.services.remitos import _estado_cobro
from app.utils import pesos_piezas_from_json

# ==============================================================================
# 🕵️ EL EXPERTO EN CLIENTES (clientes.py)
# Esta es una pequeña oficina dentro de la cocina del restaurante.
# Aquí trabaja la persona que conoce a todos los clientes: quién debe plata,
# quién tiene buen comportamiento (Scoring A) y a quién no hay que fiarle más.
# ==============================================================================

# ------------------------------------------------------------------------------
# 📝 ANOTAR UN NUEVO CLIENTE (registrar_cliente)
# ¿Qué hace esto? Imagina que llega alguien nuevo al restaurante y pide abrir
# una cuenta para pagar a fin de mes. El experto le pregunta su nombre y cuánto
# es lo máximo que le podemos fiar (techo_deuda).
# ------------------------------------------------------------------------------
def registrar_cliente(
    nombre: str,
    techo_deuda: float,
    scoring: str = "A",
    telefono: str = None,
    cuit: str = None,
    direccion: str = None,
    email: str = None,
    saldo_inicial: float = 0.0
) -> int:
    nombre = nombre.strip()
    # 1. El experto es estricto: ¡No puedes abrir una cuenta sin decir tu nombre!
    if not nombre:
        raise ValueError("El nombre del cliente no puede estar vacío")
    
    # 2. Tampoco puedes tener un límite negativo. ¡El restaurante no regala plata!
    if techo_deuda < 0:
        raise ValueError("El techo de deuda debe ser mayor o igual a 0")
        
    if scoring not in ("A", "B", "C", "D"):
        raise ValueError("Scoring inválido. Debe ser A, B, C o D")

    saldo_inicial = float(saldo_inicial)

    # 3. Va a la bóveda y anota al cliente nuevo en el cajón de "clientes"
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO clientes (nombre, scoring, techo_deuda, saldo_actual, saldo_inicial, telefono, cuit, direccion, email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (nombre, scoring, techo_deuda, saldo_inicial, saldo_inicial, telefono, cuit, direccion, email)
        )
        cliente_id = cur.lastrowid
        
    # 4. Le devuelve al Mozo el "número de cliente" para que le entregue su tarjeta
    return cliente_id

def list_clientes(limit: int | None = None, offset: int = 0) -> list[dict]:
    import datetime
    with get_db() as conn:
        # Consulta compatible con PostgreSQL (GROUP BY ANSI y sin aritmética de fecha nativa compleja)
        rows = conn.execute(
            """
            SELECT c.id, c.nombre, c.scoring, c.techo_deuda, c.saldo_actual, c.saldo_inicial, c.created_at, c.fecha_ultimo_pago,
                   c.telefono, c.cuit, c.direccion, c.email,
                   MIN(r.fecha) as oldest_unpaid
            FROM clientes c
            LEFT JOIN remitos_carga r ON c.id = r.cliente_id AND r.pagado = 0
            GROUP BY c.id, c.nombre, c.scoring, c.techo_deuda, c.saldo_actual, c.saldo_inicial, c.created_at, c.fecha_ultimo_pago,
                     c.telefono, c.cuit, c.direccion, c.email
            ORDER BY c.nombre ASC
            """
        ).fetchall()
        
        # Obtener los remitos impagos para calcular los vencidos en Python de forma dialécticamente neutra
        unpaid = conn.execute(
            "SELECT cliente_id, fecha, plazo_cobro_dias FROM remitos_carga WHERE pagado = 0"
        ).fetchall()
        
    vencidos_map = {}
    today = datetime.date.today()
    for rem in unpaid:
        cid = rem["cliente_id"]
        fecha_str = rem["fecha"]
        plazo_dias = int(rem["plazo_cobro_dias"] or 0)
        try:
            # Parsear fecha e incrementar contador si está vencido
            f_venc = datetime.datetime.strptime(fecha_str[:10], "%Y-%m-%d").date() + datetime.timedelta(days=plazo_dias)
            if f_venc < today:
                vencidos_map[cid] = vencidos_map.get(cid, 0) + 1
        except Exception:
            pass

    out = []
    for row in rows:
        r = dict(row)
        limite_superado = r["saldo_actual"] > r["techo_deuda"]
        inrecuperable = False
        
        if r["saldo_actual"] > 0:
            if r["fecha_ultimo_pago"]:
                try:
                    f_ultimo = datetime.datetime.strptime(r["fecha_ultimo_pago"], "%Y-%m-%d").date()
                    if (today - f_ultimo).days > 60:
                        inrecuperable = True
                except:
                    pass
            elif r["oldest_unpaid"]:
                try:
                    f_oldest = datetime.datetime.strptime(r["oldest_unpaid"], "%Y-%m-%d").date()
                    if (today - f_oldest).days > 60:
                        inrecuperable = True
                except:
                    pass

        remitos_vencidos = vencidos_map.get(r["id"], 0)
        en_mora = float(r["saldo_actual"]) > 0 and remitos_vencidos > 0

        out.append({
            "id": r["id"],
            "nombre": r["nombre"],
            "scoring": r["scoring"],
            "techo_deuda": r["techo_deuda"],
            "saldo_actual": r["saldo_actual"],
            "saldo_inicial": r.get("saldo_inicial", 0.0),
            "limite_superado": limite_superado,
            "telefono": r.get("telefono"),
            "cuit": r.get("cuit"),
            "direccion": r.get("direccion"),
            "email": r.get("email"),
            "created_at": r["created_at"],
            "fecha_ultimo_pago": r.get("fecha_ultimo_pago"),
            "oldest_unpaid": r.get("oldest_unpaid"),
            "en_mora": en_mora,
            "inrecuperable": inrecuperable
        })
    if limit is not None:
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 10_000))
        return out[offset : offset + limit]
    return out

def recalcular_saldo_cliente(conn, cliente_id: int) -> float:
    """
    Calcula la suma de remitos impagos (pagado = 0) para el cliente y actualiza saldo_actual.
    Debe ejecutarse dentro de la transacción activa (conn).
    """
    row_ini = conn.execute(
        "SELECT COALESCE(saldo_inicial, 0.0) AS saldo_inicial FROM clientes WHERE id = ?",
        (cliente_id,)
    ).fetchone()
    saldo_inicial = float(row_ini["saldo_inicial"]) if row_ini else 0.0

    row = conn.execute(
        """
        SELECT COALESCE(SUM(precio_venta_total - COALESCE(monto_pagado, 0)), 0) AS saldo
        FROM remitos_carga WHERE cliente_id = ? AND pagado = 0
        """,
        (cliente_id,)
    ).fetchone()
    nuevo_saldo = float(row["saldo"]) + saldo_inicial
    
    conn.execute(
        "UPDATE clientes SET saldo_actual = ? WHERE id = ?",
        (nuevo_saldo, cliente_id)
    )
    return nuevo_saldo

def get_cliente_detalle(cliente_id: int) -> dict:
    with get_db() as conn:
        cli = conn.execute(
            "SELECT id, nombre, scoring, techo_deuda, saldo_actual, saldo_inicial, telefono, cuit, direccion, email, created_at FROM clientes WHERE id = ?",
            (cliente_id,)
        ).fetchone()
        
        if not cli:
            raise ValueError("Cliente no encontrado")
            
        r_cli = dict(cli)
        
        remitos = conn.execute(
            """
            SELECT id, fecha, tipo_corte, cantidad, pesos_piezas, kg, precio_por_kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado, COALESCE(monto_pagado, 0) AS monto_pagado, created_at
            FROM remitos_carga
            WHERE cliente_id = ?
            ORDER BY id DESC
            """,
            (cliente_id,)
        ).fetchall()
        
        pagos = conn.execute(
            "SELECT id, monto, fecha FROM pagos_clientes WHERE cliente_id = ? ORDER BY fecha DESC",
            (cliente_id,)
        ).fetchall()
        
    list_rem = []
    for rem in remitos:
        r = dict(rem)
        pagado = int(r["pagado"] or 0)
        monto_pagado = float(r.get("monto_pagado") or 0)
        list_rem.append({
            "id": r["id"],
            "fecha": r["fecha"],
            "tipo_corte": r["tipo_corte"],
            "cantidad": int(r.get("cantidad") or 0),
            "pesos_piezas": pesos_piezas_from_json(r.get("pesos_piezas")),
            "kg": r["kg"],
            "precio_por_kg": r["precio_por_kg"],
            "costo_total_logistica": r["costo_total_logistica"],
            "precio_venta_total": r["precio_venta_total"],
            "plazo_cobro_dias": r["plazo_cobro_dias"],
            "costo_carne": r["costo_carne"],
            "pagado": pagado,
            "monto_pagado": monto_pagado,
            "estado_cobro": _estado_cobro(pagado, monto_pagado),
            "created_at": r["created_at"]
        })
        
    list_pagos = [{"id": p["id"], "monto": p["monto"], "fecha": p["fecha"]} for p in pagos]
        
    limite_superado = r_cli["saldo_actual"] > r_cli["techo_deuda"]
    return {
        "id": r_cli["id"],
        "nombre": r_cli["nombre"],
        "scoring": r_cli["scoring"],
        "techo_deuda": r_cli["techo_deuda"],
        "saldo_actual": r_cli["saldo_actual"],
        "saldo_inicial": r_cli.get("saldo_inicial", 0.0),
        "telefono": r_cli.get("telefono"),
        "cuit": r_cli.get("cuit"),
        "direccion": r_cli.get("direccion"),
        "email": r_cli.get("email"),
        "limite_superado": limite_superado,
        "created_at": r_cli["created_at"],
        "remitos": list_rem,
        "pagos": list_pagos
    }


def registrar_pago_cliente_global(cliente_id: int, monto: float) -> dict:
    """Aplica un pago global al cliente, descontando facturas de la más antigua a la más nueva."""
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero")

    monto = round(float(monto), 2)
    with get_db() as conn:
        cli = conn.execute(
            "SELECT id, nombre, saldo_actual FROM clientes WHERE id = ?",
            (cliente_id,),
        ).fetchone()
        if not cli:
            raise ValueError("Cliente no encontrado")

        saldo_cliente = float(cli["saldo_actual"])
        if monto > saldo_cliente + 0.009:
            raise ValueError(f"El monto supera la deuda pendiente (${saldo_cliente:,.2f})")

        remitos = conn.execute(
            """
            SELECT id, precio_venta_total, COALESCE(monto_pagado, 0) AS monto_pagado
            FROM remitos_carga
            WHERE cliente_id = ? AND pagado = 0
            ORDER BY fecha ASC, id ASC
            """,
            (cliente_id,),
        ).fetchall()
        if not remitos:
            raise ValueError("El cliente no tiene facturas pendientes")

        restante = monto
        aplicaciones = []

        for rem in remitos:
            if restante <= 0.009:
                break
            rid = int(rem["id"])
            total = float(rem["precio_venta_total"])
            ya_pagado = float(rem["monto_pagado"] or 0)
            saldo_rem = round(total - ya_pagado, 2)
            if saldo_rem <= 0:
                continue

            aplicar = round(min(restante, saldo_rem), 2)
            nuevo_pagado = round(ya_pagado + aplicar, 2)
            cobrado_completo = nuevo_pagado >= total - 0.009

            if cobrado_completo:
                conn.execute(
                    "UPDATE remitos_carga SET monto_pagado = ?, pagado = 1 WHERE id = ?",
                    (total, rid),
                )
            else:
                conn.execute(
                    "UPDATE remitos_carga SET monto_pagado = ? WHERE id = ?",
                    (nuevo_pagado, rid),
                )

            aplicaciones.append({
                "remito_id": rid,
                "monto": aplicar,
                "cobrado_completo": cobrado_completo,
            })
            restante = round(restante - aplicar, 2)

        if not aplicaciones:
            raise ValueError("No se pudo aplicar el pago a ninguna factura pendiente")

        # Insertar pago global
        cur_pago = conn.execute(
            "INSERT INTO pagos_clientes (cliente_id, monto) VALUES (?, ?)",
            (cliente_id, monto)
        )
        pago_id = cur_pago.lastrowid

        for app in aplicaciones:
            conn.execute(
                "INSERT INTO aplicacion_pagos (pago_id, remito_id, monto_aplicado) VALUES (?, ?, ?)",
                (pago_id, app["remito_id"], app["monto"])
            )

        conn.execute(
            "UPDATE clientes SET fecha_ultimo_pago = date('now', 'localtime') WHERE id = ?",
            (cliente_id,),
        )
        nuevo_saldo = recalcular_saldo_cliente(conn, cliente_id)

    facturas_cobradas = sum(1 for a in aplicaciones if a["cobrado_completo"])
    msg = f"Pago de ${monto:,.2f} aplicado"
    if facturas_cobradas:
        msg += f" · {facturas_cobradas} factura(s) saldada(s)"

    return {
        "ok": True,
        "monto_aplicado": monto,
        "aplicaciones": aplicaciones,
        "saldo_cliente": nuevo_saldo,
        "message": msg,
    }


def buscar_o_crear_cliente(conn, nombre: str) -> int:
    """
    Busca un cliente por nombre de forma insensible a mayúsculas/minúsculas.
    Si no existe, lo crea con un límite de deuda por defecto de $500,000.
    """
    nombre_clean = nombre.strip()
    row = conn.execute(
        "SELECT id FROM clientes WHERE LOWER(nombre) = LOWER(?)",
        (nombre_clean,)
    ).fetchone()
    
    if row:
        return int(row["id"])
        
    cur = conn.execute(
        "INSERT INTO clientes (nombre, scoring, techo_deuda, saldo_actual) VALUES (?, 'A', 500000.0, 0)",
        (nombre_clean,)
    )
    return cur.lastrowid

def marcar_cliente_incobrable(cliente_id: int) -> int:
    with get_db() as conn:
        cli = conn.execute("SELECT id, nombre, saldo_actual FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
        if not cli:
            raise ValueError("Cliente no encontrado")
        
        saldo = float(cli["saldo_actual"])
        if saldo <= 0:
            raise ValueError("El cliente no posee saldo deudor para declarar incobrable")
            
        # 1. Mover deuda a perdidas_acumuladas
        cur = conn.execute(
            """
            INSERT INTO perdidas_acumuladas (cliente_id, monto_nominal, fecha_perdida)
            VALUES (?, ?, date('now', 'localtime'))
            """,
            (cliente_id, saldo)
        )
        perdida_id = cur.lastrowid
        
        # 2. Marcar todos los remitos impagos como anulados/incobrables (pagado = 2)
        conn.execute(
            "UPDATE remitos_carga SET pagado = 2 WHERE cliente_id = ? AND pagado = 0",
            (cliente_id,)
        )
        
        # 3. Reiniciar saldo_actual, bloquear techo_deuda = 0 y asignar scoring 'D'
        conn.execute(
            "UPDATE clientes SET saldo_actual = 0, techo_deuda = 0, scoring = 'D' WHERE id = ?",
            (cliente_id,)
        )
        
    return perdida_id

def list_perdidas_acumuladas() -> list[dict]:
    from datetime import datetime
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.cliente_id, c.nombre AS cliente_nombre, p.monto_nominal, p.fecha_perdida
            FROM perdidas_acumuladas p
            LEFT JOIN clientes c ON p.cliente_id = c.id
            ORDER BY p.id DESC
            """
        ).fetchall()
        
    out = []
    tasa_anual = 0.30  # 30% costo de oportunidad anual
    hoy = datetime.now().date()
    
    for row in rows:
        r = dict(row)
        try:
            fecha_str = r["fecha_perdida"].split(" ")[0]
            fecha_p = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        except Exception:
            fecha_p = hoy
            
        dias = (hoy - fecha_p).days
        if dias < 0:
            dias = 0
        anios = dias / 365.0
        monto_total = r["monto_nominal"] * ((1 + tasa_anual) ** anios)
        costo_op = monto_total - r["monto_nominal"]
        
        out.append({
            "id": r["id"],
            "cliente_id": r["cliente_id"],
            "cliente_nombre": r["cliente_nombre"] or "Cliente Eliminado",
            "monto_nominal": r["monto_nominal"],
            "fecha_perdida": r["fecha_perdida"],
            "dias_transcurridos": dias,
            "costo_oportunidad_interes": round(costo_op, 2),
            "monto_total": round(monto_total, 2)
        })
    return out


def actualizar_saldo_inicial(cliente_id: int, saldo_inicial: float) -> float:
    with get_db() as conn:
        conn.execute(
            "UPDATE clientes SET saldo_inicial = ? WHERE id = ?",
            (saldo_inicial, cliente_id)
        )
        nuevo_saldo = recalcular_saldo_cliente(conn, cliente_id)
    return nuevo_saldo


def eliminar_cliente(cliente_id: int):
    from app.services.remitos import eliminar_remito_logic
    with get_db() as conn:
        # 1. Desvincular ventas de mostrador (POS) asociadas a este cliente para evitar violaciones de clave foránea
        conn.execute("UPDATE ventas_mostrador SET cliente_id = NULL WHERE cliente_id = ?", (cliente_id,))

        # 2. Eliminar aplicaciones explícitamente y luego los pagos (para soportar SQLite sin foreign_keys=ON)
        conn.execute("DELETE FROM aplicacion_pagos WHERE pago_id IN (SELECT id FROM pagos_clientes WHERE cliente_id = ?)", (cliente_id,))
        conn.execute("DELETE FROM pagos_clientes WHERE cliente_id = ?", (cliente_id,))
        
        # 3. Resetear monto_pagado a 0 para todos los remitos del cliente para evitar errores de validación de pagos en eliminar_remito_logic
        conn.execute("UPDATE remitos_carga SET monto_pagado = 0 WHERE cliente_id = ?", (cliente_id,))

        # 4. Obtener todos los remitos del cliente
        remitos = conn.execute("SELECT id FROM remitos_carga WHERE cliente_id = ?", (cliente_id,)).fetchall()
        
        # 5. Eliminar cada remito devolviendo su stock a compras_bulk
        for rem in remitos:
            eliminar_remito_logic(conn, rem["id"])
            
        # 6. Eliminar pérdidas acumuladas del cliente (si las hay)
        conn.execute("DELETE FROM perdidas_acumuladas WHERE cliente_id = ?", (cliente_id,))
        
        # 7. Eliminar el cliente
        conn.execute("DELETE FROM clientes WHERE id = ?", (cliente_id,))

def actualizar_cliente(
    cliente_id: int,
    nombre: str,
    techo_deuda: float,
    scoring: str = "A",
    telefono: str = None,
    cuit: str = None,
    direccion: str = None,
    email: str = None,
    saldo_inicial: float = 0.0
):
    nombre = nombre.strip()
    if not nombre:
        raise ValueError("El nombre del cliente no puede estar vacío")
    if techo_deuda < 0:
        raise ValueError("El techo de deuda debe ser mayor o igual a 0")
    if scoring not in ("A", "B", "C", "D"):
        raise ValueError("Scoring inválido. Debe ser A, B, C o D")
        
    saldo_inicial = float(saldo_inicial)
    if saldo_inicial < 0:
        raise ValueError("El saldo inicial debe ser mayor o igual a 0")

    with get_db() as conn:
        dup = conn.execute("SELECT id FROM clientes WHERE nombre = ? AND id != ?", (nombre, cliente_id)).fetchone()
        if dup:
            raise ValueError("Ya existe otro cliente con este nombre")

        conn.execute(
            """
            UPDATE clientes
            SET nombre = ?, scoring = ?, techo_deuda = ?, saldo_inicial = ?,
                telefono = ?, cuit = ?, direccion = ?, email = ?
            WHERE id = ?
            """,
            (nombre, scoring, techo_deuda, saldo_inicial, telefono, cuit, direccion, email, cliente_id)
        )
        recalcular_saldo_cliente(conn, cliente_id)

def eliminar_pago_cliente(pago_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT cliente_id FROM pagos_clientes WHERE id = ?", (pago_id,)).fetchone()
        if not row:
            raise ValueError("Pago no encontrado")
        cliente_id = row["cliente_id"]

        # Revertir los montos aplicados a los remitos
        aplicaciones = conn.execute("SELECT remito_id, monto_aplicado FROM aplicacion_pagos WHERE pago_id = ?", (pago_id,)).fetchall()
        for app in aplicaciones:
            remito_id = app["remito_id"]
            monto_aplicado = app["monto_aplicado"]
            conn.execute(
                "UPDATE remitos_carga SET monto_pagado = COALESCE(monto_pagado, 0) - ?, pagado = 0 WHERE id = ?",
                (monto_aplicado, remito_id)
            )
            # Asegurar que no quede negativo por errores de redondeo
            conn.execute("UPDATE remitos_carga SET monto_pagado = 0 WHERE monto_pagado < 0 AND id = ?", (remito_id,))

        # Eliminar el pago (sus aplicaciones se borran por CASCADE en BD, pero lo hacemos explícito si es necesario, o lo asume)
        conn.execute("DELETE FROM pagos_clientes WHERE id = ?", (pago_id,))
        
        recalcular_saldo_cliente(conn, cliente_id)

