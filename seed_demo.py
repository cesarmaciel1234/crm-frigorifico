"""
Carga datos de prueba para Master Total.
Uso: python seed_demo.py
"""
from datetime import date, timedelta

from app.database import get_db, init_db
from app.services.bulk import fraccionar_lote_fifo
from app.services.clientes import buscar_o_crear_cliente, recalcular_saldo_cliente
from app.services.pagos import registrar_pago

HOY = date.today()


def _clear_demo_data(conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in (
        "remitos_fracciones",
        "pagos_cuotas",
        "perdidas_acumuladas",
        "ventas_mostrador",
        "instrumentos_financieros",
        "remitos_carga",
        "compras_bulk",
        "operaciones_financieras",
        "clientes",
        "entidades_bancarias",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("PRAGMA foreign_keys = ON")


def _insert_remito(conn, cliente, kg, costo_log, venta, plazo, fecha=None, cobrado=False):
    cid = buscar_o_crear_cliente(conn, cliente)
    costo_carne, fracciones = fraccionar_lote_fifo(conn, kg)
    if fecha:
        cur = conn.execute(
            """
            INSERT INTO remitos_carga
                (fecha, cliente, cliente_id, kg, costo_total_logistica, precio_venta_total,
                 plazo_cobro_dias, costo_carne, pagado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (fecha, cliente, cid, kg, costo_log, venta, plazo, costo_carne),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO remitos_carga
                (cliente, cliente_id, kg, costo_total_logistica, precio_venta_total,
                 plazo_cobro_dias, costo_carne, pagado)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (cliente, cid, kg, costo_log, venta, plazo, costo_carne),
        )
    rid = cur.lastrowid
    for frac in fracciones:
        conn.execute(
            """
            INSERT INTO remitos_fracciones (remito_id, lote_id, kg_descontados, costo_porcion)
            VALUES (?, ?, ?, ?)
            """,
            (rid, frac["lote_id"], frac["kg_descontados"], frac["costo_porcion"]),
        )
    recalcular_saldo_cliente(conn, cid)
    if cobrado:
        conn.execute("UPDATE remitos_carga SET pagado = 1 WHERE id = ?", (rid,))
        recalcular_saldo_cliente(conn, cid)
    return rid


def seed():
    init_db()

    bancos = [
        ("Banco Nación — CC", 650_000),
        ("Visa Galicia", 220_000),
        ("Mercado Pago", 95_000),
    ]
    # dni, nombre, techo, scoring
    clientes = [
        ("20111222333", "Carnicería López", 800_000, "A"),
        ("27222333444", "Masivos del Sur", 500_000, "B"),
        ("20333444555", "Kiosco Central", 150_000, "C"),
        ("27444555666", "Distribuidora Norte", 1_200_000, "A"),
        ("20555666777", "El Asador", 350_000, "B"),
    ]
    remitos = [
        ("Carnicería López", 350, 12_000, 420_000, 21, (HOY - timedelta(days=10)).isoformat(), True),
        ("Masivos del Sur", 500, 18_500, 610_000, 30, (HOY - timedelta(days=6)).isoformat(), False),
        ("Kiosco Central", 80, 4_200, 98_000, 14, (HOY - timedelta(days=3)).isoformat(), False),
        ("Distribuidora Norte", 600, 22_000, 780_000, 45, (HOY - timedelta(days=2)).isoformat(), False),
        ("El Asador", 120, 5_800, 145_000, 7, (HOY - timedelta(days=1)).isoformat(), True),
        ("Masivos del Sur", 280, 9_500, 352_000, 30, HOY.isoformat(), False),
    ]

    with get_db() as conn:
        _clear_demo_data(conn)

        for nombre, limite in bancos:
            conn.execute(
                "INSERT INTO entidades_bancarias (nombre, limite) VALUES (?, ?)",
                (nombre, limite),
            )

        cliente_ids = {}
        for dni, nombre, techo, scoring in clientes:
            cur = conn.execute(
                """
                INSERT INTO clientes (dni, nombre, scoring, techo_deuda, saldo_actual)
                VALUES (?, ?, ?, ?, 0)
                """,
                (dni, nombre, scoring, techo),
            )
            cliente_ids[nombre] = cur.lastrowid

        conn.execute(
            """
            INSERT INTO compras_bulk (fecha, kg_totales, kg_remanentes, costo_total_bulk)
            VALUES (?, 2000, 2000, 1700000)
            """,
            ((HOY - timedelta(days=15)).isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO compras_bulk (fecha, kg_totales, kg_remanentes, costo_total_bulk)
            VALUES (?, 1500, 1500, 1380000)
            """,
            ((HOY - timedelta(days=5)).isoformat(),),
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
            _insert_remito(conn, *r)

        # Ventas mostrador (efectivo / transferencia)
        ventas_mostrador = [
            (cliente_ids["Kiosco Central"], 28_500, "CONTADO", (HOY - timedelta(days=4)).isoformat()),
            (cliente_ids["El Asador"], 42_000, "FIADO", (HOY - timedelta(days=2)).isoformat()),
            (None, 15_800, "CONTADO", (HOY - timedelta(days=1)).isoformat()),
            (cliente_ids["Carnicería López"], 67_200, "FIADO", HOY.isoformat()),
        ]
        for cid, monto, tipo_pago, fecha in ventas_mostrador:
            conn.execute(
                """
                INSERT INTO ventas_mostrador (cliente_id, monto, tipo_pago, fecha)
                VALUES (?, ?, ?, ?)
                """,
                (cid, monto, tipo_pago, fecha),
            )

        # Instrumentos financieros (cheques / pagarés propios)
        instrumentos = [
            ("CHEQUE", 95_000, (HOY + timedelta(days=18)).isoformat(), 0, 0),
            ("CHEQUE", 62_500, (HOY + timedelta(days=35)).isoformat(), 0, 0),
            ("PRESTAMO", 120_000, (HOY + timedelta(days=60)).isoformat(), 1, 0),
            ("CHEQUE", 48_000, (HOY - timedelta(days=5)).isoformat(), 0, 1),
        ]
        for tipo, monto, venc, interes, pagado in instrumentos:
            conn.execute(
                """
                INSERT INTO instrumentos_financieros
                    (tipo, monto, fecha_vencimiento, interes_cargado, pagado)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tipo, monto, venc, interes, pagado),
            )

    registrar_pago(op_ids[0], 1, 103_333.33)

    with get_db() as conn:
        stock = conn.execute(
            "SELECT COALESCE(SUM(kg_remanentes), 0) FROM compras_bulk"
        ).fetchone()[0]
        pendiente = conn.execute(
            "SELECT COALESCE(SUM(precio_venta_total), 0) FROM remitos_carga WHERE pagado = 0"
        ).fetchone()[0]

    print("=" * 55)
    print("  DATOS DE PRUEBA CARGADOS — Master Total")
    print("=" * 55)
    print(f"  Bancos .............. {len(bancos)}")
    print(f"  Clientes ............ {len(clientes)}")
    print(f"  Lotes bulk .......... 2")
    print(f"  Stock remanente ..... {stock:.0f} kg")
    print(f"  Operaciones ......... {len(ops)}")
    print(f"  Remitos de venta .... {len(remitos)}")
    print(f"  Ventas mostrador .... {len(ventas_mostrador)}")
    print(f"  Instrumentos fin. ... {len(instrumentos)}")
    print(f"  Por cobrar .......... ${pendiente:,.2f}")
    print(f"  Pagos registrados ... 1 cuota Visa Nación")
    print()
    print("  Abrí: http://127.0.0.1:5005  |  python run.py")
    print("=" * 55)


if __name__ == "__main__":
    seed()
