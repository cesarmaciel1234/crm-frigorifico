import sqlite3
from contextlib import contextmanager
from app.config import Config

@contextmanager
def get_db():
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with open(Config.SCHEMA_PATH, encoding="utf-8") as f:
        sql = f.read()
    with get_db() as conn:
        conn.executescript(sql)
        _run_migrations(conn)

def _run_migrations(conn):
    """Migraciones incrementales sobre la conexión activa."""
    cols = {
        "fecha_cierre": "TEXT",
        "fecha_vencimiento": "TEXT",
        "cuotas": "INTEGER",
        "cuotas_pagadas": "INTEGER NOT NULL DEFAULT 0",
        "kg": "REAL",
        "precio_kg": "REAL",
        "plazo_dias": "INTEGER",
    }
    existing = {row[1] for row in conn.execute("PRAGMA table_info(operaciones_financieras)")}
    for name, col_type in cols.items():
        if name not in existing:
            default = " DEFAULT 0" if name == "cuotas_pagadas" else ""
            conn.execute(f"ALTER TABLE operaciones_financieras ADD COLUMN {name} {col_type}{default}")
    _migrate_pagar_constraint(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pagos_cuotas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operacion_id INTEGER NOT NULL,
            numero_cuota INTEGER NOT NULL,
            monto_cuota_esperado REAL NOT NULL,
            monto_pagado REAL NOT NULL,
            interes_punitorio REAL NOT NULL DEFAULT 0,
            descuento REAL NOT NULL DEFAULT 0,
            fecha_pago TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (operacion_id) REFERENCES operaciones_financieras(id)
        );
        CREATE INDEX IF NOT EXISTS idx_pagos_op ON pagos_cuotas(operacion_id);
        """
    )
    remito_cols = {
        "cliente": "TEXT NOT NULL DEFAULT ''",
        "costo_carne": "REAL NOT NULL DEFAULT 0",
        "cliente_id": "INTEGER",
        "pagado": "INTEGER NOT NULL DEFAULT 0"
    }
    remito_existing = {row[1] for row in conn.execute("PRAGMA table_info(remitos_carga)")}
    for name, col_type in remito_cols.items():
        if name not in remito_existing:
            conn.execute(f"ALTER TABLE remitos_carga ADD COLUMN {name} {col_type}")

    # Migración de clientes
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            scoring TEXT NOT NULL DEFAULT 'A',
            techo_deuda REAL NOT NULL DEFAULT 500000,
            saldo_actual REAL NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        """
    )

    # Migración de compras_bulk
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS compras_bulk (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL DEFAULT (date('now', 'localtime')),
            kg_totales REAL NOT NULL CHECK(kg_totales > 0),
            kg_remanentes REAL NOT NULL CHECK(kg_remanentes >= 0),
            costo_total_bulk REAL NOT NULL CHECK(costo_total_bulk > 0),
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        """
    )

    # Migración de remitos_fracciones
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS remitos_fracciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            remito_id INTEGER NOT NULL,
            lote_id INTEGER NOT NULL,
            kg_descontados REAL NOT NULL CHECK(kg_descontados > 0),
            costo_porcion REAL NOT NULL CHECK(costo_porcion >= 0),
            FOREIGN KEY (remito_id) REFERENCES remitos_carga(id) ON DELETE CASCADE,
            FOREIGN KEY (lote_id) REFERENCES compras_bulk(id),
            UNIQUE(remito_id, lote_id)
        );
        """
    )

    # Migración de perdidas_acumuladas
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS perdidas_acumuladas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            monto_nominal REAL NOT NULL CHECK(monto_nominal >= 0),
            fecha_perdida TEXT NOT NULL DEFAULT (date('now', 'localtime')),
            costo_oportunidad_interes REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        );
        """
    )

def _migrate_pagar_constraint(conn):
    """Permite pagar = recibido (cheques sin interés)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='operaciones_financieras'"
    ).fetchone()
    if not row or "pagar > recibido" not in (row[0] or ""):
        return
    conn.executescript(
        """
        CREATE TABLE operaciones_financieras_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'otro',
            recibido REAL NOT NULL CHECK(recibido > 0),
            pagar REAL NOT NULL CHECK(pagar >= recibido),
            meses INTEGER NOT NULL CHECK(meses > 0),
            fecha_cierre TEXT,
            fecha_vencimiento TEXT,
            cuotas INTEGER,
            cuotas_pagadas INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        INSERT INTO operaciones_financieras_new
            SELECT id, alias, tipo, recibido, pagar, meses,
                   fecha_cierre, fecha_vencimiento, cuotas,
                   COALESCE(cuotas_pagadas, 0), created_at
            FROM operaciones_financieras;
        DROP TABLE operaciones_financieras;
        ALTER TABLE operaciones_financieras_new RENAME TO operaciones_financieras;
        CREATE INDEX IF NOT EXISTS idx_op_alias ON operaciones_financieras(alias);
        CREATE INDEX IF NOT EXISTS idx_op_cfr ON operaciones_financieras(recibido, pagar, meses);
        """
    )
