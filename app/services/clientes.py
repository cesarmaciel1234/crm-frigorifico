from app.database import get_db
from app.services.remitos import _estado_cobro

def registrar_cliente(nombre: str, techo_deuda: float, scoring: str = "A") -> int:
    nombre = nombre.strip()
    if not nombre:
        raise ValueError("El nombre del cliente no puede estar vacío")
    if techo_deuda < 0:
        raise ValueError("El techo de deuda debe ser mayor o igual a 0")
    if scoring not in ("A", "B", "C", "D"):
        raise ValueError("Scoring inválido. Debe ser A, B, C o D")

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO clientes (nombre, scoring, techo_deuda, saldo_actual)
            VALUES (?, ?, ?, 0)
            """,
            (nombre, scoring, techo_deuda)
        )
        cliente_id = cur.lastrowid
    return cliente_id

def list_clientes() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, nombre, scoring, techo_deuda, saldo_actual, created_at
            FROM clientes ORDER BY nombre ASC
            """
        ).fetchall()
        
    out = []
    for row in rows:
        r = dict(row)
        limite_superado = r["saldo_actual"] > r["techo_deuda"]
        out.append({
            "id": r["id"],
            "nombre": r["nombre"],
            "scoring": r["scoring"],
            "techo_deuda": r["techo_deuda"],
            "saldo_actual": r["saldo_actual"],
            "limite_superado": limite_superado,
            "created_at": r["created_at"]
        })
    return out

def recalcular_saldo_cliente(conn, cliente_id: int) -> float:
    """
    Calcula la suma de remitos impagos (pagado = 0) para el cliente y actualiza saldo_actual.
    Debe ejecutarse dentro de la transacción activa (conn).
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(precio_venta_total), 0) AS saldo FROM remitos_carga WHERE cliente_id = ? AND pagado = 0",
        (cliente_id,)
    ).fetchone()
    nuevo_saldo = float(row["saldo"])
    
    conn.execute(
        "UPDATE clientes SET saldo_actual = ? WHERE id = ?",
        (nuevo_saldo, cliente_id)
    )
    return nuevo_saldo

def get_cliente_detalle(cliente_id: int) -> dict:
    with get_db() as conn:
        cli = conn.execute(
            "SELECT id, nombre, scoring, techo_deuda, saldo_actual, created_at FROM clientes WHERE id = ?",
            (cliente_id,)
        ).fetchone()
        
        if not cli:
            raise ValueError("Cliente no encontrado")
            
        r_cli = dict(cli)
        
        remitos = conn.execute(
            """
            SELECT id, fecha, kg, costo_total_logistica, precio_venta_total, plazo_cobro_dias, costo_carne, pagado, created_at
            FROM remitos_carga
            WHERE cliente_id = ?
            ORDER BY id DESC
            """,
            (cliente_id,)
        ).fetchall()
        
    list_rem = []
    for rem in remitos:
        r = dict(rem)
        pagado = int(r["pagado"] or 0)
        list_rem.append({
            "id": r["id"],
            "fecha": r["fecha"],
            "kg": r["kg"],
            "costo_total_logistica": r["costo_total_logistica"],
            "precio_venta_total": r["precio_venta_total"],
            "plazo_cobro_dias": r["plazo_cobro_dias"],
            "costo_carne": r["costo_carne"],
            "pagado": pagado,
            "estado_cobro": _estado_cobro(pagado),
            "created_at": r["created_at"]
        })
        
    limite_superado = r_cli["saldo_actual"] > r_cli["techo_deuda"]
    return {
        "id": r_cli["id"],
        "nombre": r_cli["nombre"],
        "scoring": r_cli["scoring"],
        "techo_deuda": r_cli["techo_deuda"],
        "saldo_actual": r_cli["saldo_actual"],
        "limite_superado": limite_superado,
        "created_at": r_cli["created_at"],
        "remitos": list_rem
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
