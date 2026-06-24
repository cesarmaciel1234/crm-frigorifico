"""
Carga datos de prueba para Master Total.
Borra todo y regenera escenarios para Cobranzas, Pago Centralizado e impresión.

Uso: python seed_demo.py
"""
from datetime import date, timedelta

from app.database import get_db, init_db
from app.services.bulk import fraccionar_lote_fifo
from app.services.clientes import buscar_o_crear_cliente, recalcular_saldo_cliente
from app.services.pagos import registrar_pago
from app.utils import pesos_piezas_to_json

HOY = date.today()


def _clear_demo_data(conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in (
        "remitos_fracciones",
        "pagos_cuotas",
        "perdidas_acumuladas",
        "auditoria_operaciones",
        "ventas_mostrador",
        "instrumentos_financieros",
        "remitos_carga",
        "compras_bulk",
        "operaciones_financieras",
        "clientes",
        "entidades_bancarias",
    ):
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.execute("PRAGMA foreign_keys = ON")


def _insert_remito(
    conn,
    cliente,
    tipo_corte,
    kg,
    precio_por_kg,
    plazo,
    fecha=None,
    *,
    cantidad=0,
    pesos_piezas=None,
    cobrado=False,
    monto_pagado=0.0,
):
    cid = buscar_o_crear_cliente(conn, cliente)
    piezas = list(pesos_piezas or [])
    if piezas:
        kg = round(sum(piezas), 2)
    if not cantidad and piezas:
        cantidad = len(piezas)
    pesos_json = pesos_piezas_to_json(piezas)
    costo_carne, fracciones = fraccionar_lote_fifo(conn, kg)
    costo = sum(f["costo_logistica_porcion"] for f in fracciones)
    venta = round((kg * precio_por_kg) + costo, 2)

    pagado_flag = 0
    monto = round(float(monto_pagado or 0), 2)
    if cobrado:
        pagado_flag = 1
        monto = venta
    elif monto > 0:
        if monto >= venta - 0.009:
            pagado_flag = 1
            monto = venta
        else:
            pagado_flag = 0

    cols = (
        "cliente_id", "tipo_corte", "cantidad", "pesos_piezas", "kg", "precio_por_kg",
        "costo_total_logistica", "precio_venta_total", "plazo_cobro_dias",
        "costo_carne", "pagado", "monto_pagado",
    )
    vals = (
        cid, tipo_corte, int(cantidad or 0), pesos_json, kg, precio_por_kg,
        costo, venta, plazo, costo_carne, pagado_flag, monto,
    )

    if fecha:
        cur = conn.execute(
            f"""
            INSERT INTO remitos_carga
                (fecha, {", ".join(cols)})
            VALUES (?, {", ".join("?" * len(cols))})
            """,
            (fecha, *vals),
        )
    else:
        cur = conn.execute(
            f"""
            INSERT INTO remitos_carga
                ({", ".join(cols)})
            VALUES ({", ".join("?" * len(cols))})
            """,
            vals,
        )

    rid = cur.lastrowid
    for frac in fracciones:
        conn.execute(
            """
            INSERT INTO remitos_fracciones (remito_id, lote_id, kg_descontados, costo_porcion, costo_logistica_porcion)
            VALUES (?, ?, ?, ?, ?)
            """,
            (rid, frac["lote_id"], frac["kg_descontados"], frac["costo_porcion"], frac["costo_logistica_porcion"]),
        )

    if pagado_flag == 1 or monto > 0:
        conn.execute(
            "UPDATE clientes SET fecha_ultimo_pago = date('now', 'localtime') WHERE id = ?",
            (cid,),
        )

    recalcular_saldo_cliente(conn, cid)
    return rid


def seed():
    init_db()

    bancos = [
        ("Banco Nación — CC", 650_000),
        ("Visa Galicia", 220_000),
        ("Mercado Pago", 95_000),
    ]

    clientes = [
        ("Carnicería López", 800_000, "A"),
        ("Masivos del Sur", 500_000, "B"),
        ("Kiosco Central", 150_000, "C"),
        ("Distribuidora Norte", 1_200_000, "A"),
        ("El Asador", 350_000, "B"),
    ]

    # (cliente, corte, cantidad, kg, $/kg, plazo, fecha, cobrado, parcial, [pesos_piezas])
    MEDIA_10 = [58, 62, 59, 61, 60, 58, 63, 57, 61, 61]
    remitos = [
        ("Carnicería López", "Media Res", 6, 350, 1200, 21, (HOY - timedelta(days=10)).isoformat(), True, 0, [58, 58, 59, 59, 58, 58]),
        ("Carnicería López", "Parrilleros", 8, 200, 1280, 21, (HOY - timedelta(days=4)).isoformat(), False, 0, [25, 25, 25, 25, 25, 25, 25, 25]),
        ("Masivos del Sur", "Cuartos", 17, 500, 1220, 30, (HOY - timedelta(days=6)).isoformat(), False, 0, None),
        ("Kiosco Central", "Parrilleros", 4, 80, 1225, 14, (HOY - timedelta(days=25)).isoformat(), False, 0, [20, 20, 20, 20]),
        ("Distribuidora Norte", "Media Res", 10, 600, 1300, 45, (HOY - timedelta(days=2)).isoformat(), False, 0, MEDIA_10),
        ("Distribuidora Norte", "Cuartos", 8, 240, 1250, 21, (HOY - timedelta(days=40)).isoformat(), False, 0, [30, 30, 30, 30, 30, 30, 30, 30]),
        ("El Asador", "Pechos", 6, 120, 1208, 7, (HOY - timedelta(days=1)).isoformat(), True, 0, [20, 20, 20, 20, 20, 20]),
        ("Masivos del Sur", "Media Res", 5, 280, 1257, 30, HOY.isoformat(), False, 0, [56, 56, 56, 56, 56]),
        ("Kiosco Central", "Pechos", 3, 60, 1190, 7, (HOY - timedelta(days=12)).isoformat(), False, 30_000, [20, 20, 20]),
    ]

    with get_db() as conn:
        _clear_demo_data(conn)

        for nombre, limite in bancos:
            conn.execute(
                "INSERT INTO entidades_bancarias (nombre, limite) VALUES (?, ?)",
                (nombre, limite),
            )

        for nombre, techo, scoring in clientes:
            conn.execute(
                """
                INSERT INTO clientes (nombre, scoring, techo_deuda, saldo_actual)
                VALUES (?, ?, ?, 0)
                """,
                (nombre, scoring, techo),
            )

        conn.execute(
            """
            INSERT INTO compras_bulk (fecha, kg_totales, kg_remanentes, costo_total_bulk, costo_reparto)
            VALUES (?, 5000, 5000, 4250000, 250000)
            """,
            ((HOY - timedelta(days=20)).isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO compras_bulk (fecha, kg_totales, kg_remanentes, costo_total_bulk, costo_reparto)
            VALUES (?, 3000, 3000, 2760000, 160000)
            """,
            ((HOY - timedelta(days=8)).isoformat(),),
        )

        cierre = (HOY - timedelta(days=20)).isoformat()
        venc_tarjeta = (HOY + timedelta(days=4)).isoformat()
        venc_cheque = (HOY + timedelta(days=12)).isoformat()
        venc_proveedor = (HOY + timedelta(days=2)).isoformat()

        ops = [
            ("Visa Nación", "tarjeta", 500_000, 620_000, 6, cierre, venc_tarjeta, 6, None, None, None),
            ("Cheque Frigorífico", "cheque", 185_000, 185_000, 1, None, venc_cheque, None, None, None, None),
            ("Frigorífico La Pampa", "proveedor", 704_000, 704_000, 1, None, venc_proveedor, None, 800, 880, 2),
            ("Préstamo Banco Nación", "banco", 300_000, 396_000, 12, None, None, None, None, None, None),
            ("Mastercard BBVA", "tarjeta", 180_000, 225_000, 3, cierre, (HOY - timedelta(days=2)).isoformat(), 3, None, None, None),
            ("Proveedor Fletes Sur", "proveedor", 95_000, 98_500, 1, None, (HOY - timedelta(days=5)).isoformat(), None, 450, 110, 15),
        ]
        op_ids = []
        for row in ops:
            cur = conn.execute(
                """
                INSERT INTO operaciones_financieras
                    (alias, tipo, recibido, pagar, meses, fecha_cierre, fecha_vencimiento,
                     cuotas, kg, precio_kg, plazo_dias, cuotas_pagadas)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                row,
            )
            op_ids.append(cur.lastrowid)

        for r in remitos:
            cliente, corte, cant, kg, px, plazo, fecha, cobrado, parcial, piezas = r
            _insert_remito(
                conn, cliente, corte, kg, px, plazo, fecha,
                cantidad=cant, pesos_piezas=piezas, cobrado=cobrado, monto_pagado=parcial,
            )

    registrar_pago(op_ids[0], 1, 103_333.33)
    registrar_pago(op_ids[0], 2, 103_333.33)

    with get_db() as conn:
        stock = conn.execute(
            "SELECT COALESCE(SUM(kg_remanentes), 0) FROM compras_bulk"
        ).fetchone()[0]
        pendiente = conn.execute(
            "SELECT COALESCE(SUM(precio_venta_total - COALESCE(monto_pagado, 0)), 0) FROM remitos_carga WHERE pagado = 0"
        ).fetchone()[0]
        n_clientes_deuda = conn.execute(
            "SELECT COUNT(*) FROM clientes WHERE saldo_actual > 0"
        ).fetchone()[0]
        n_ops = conn.execute(
            "SELECT COUNT(*) FROM operaciones_financieras"
        ).fetchone()[0]

    print("=" * 58)
    print("  BASE RESETEADA — Datos de prueba cargados")
    print("=" * 58)
    print(f"  Bancos .............. {len(bancos)}")
    print(f"  Clientes ............ {len(clientes)}")
    print(f"  Lotes bulk .......... 2  (stock ~{stock:.0f} kg)")
    print(f"  Operaciones ......... {n_ops}  (tarjetas, cheques, proveedores)")
    print(f"  Remitos de venta .... {len(remitos)}")
    print(f"  Clientes con deuda .. {n_clientes_deuda}")
    print(f"  Por cobrar .......... ${pendiente:,.2f}")
    print()
    print("  Escenarios incluidos:")
    print("  · Distribuidora Norte: 10 Media Res (600 kg) — factura detallada")
    print("  · Kiosco Central: remitos EN MORA + pago parcial")
    print("  · Cobranzas: varios clientes con saldo y vencidos")
    print("  · Pago centralizado: obligaciones vencidas y próximas")
    print("  · Visa Nación: 2 cuotas pagadas de 6")
    print()
    print("  Abrí: http://127.0.0.1:5005  |  python run.py")
    print("=" * 58)


if __name__ == "__main__":
    seed()
