import sqlite3
import re
from contextlib import contextmanager
from app.config import Config

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

def is_postgres():
    return Config.DATABASE_URL and psycopg2

def table_exists(conn, name: str) -> bool:
    if type(conn).__name__ == "PostgresConnWrapper":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (name,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    return row is not None

class PostgresCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor
        self._lastrowid = None
        
    def execute(self, query, vars=None):
        if is_postgres():
            query = query.replace("?", "%s")
            # En queries, cambiar sqlite datetime por CURRENT_TIMESTAMP (postgres)
            query = query.replace("datetime('now', 'localtime')", "CURRENT_TIMESTAMP::varchar")
            query = query.replace("date('now', 'localtime')", "CURRENT_DATE::varchar")
        
        if "PRAGMA table_info" in query:
            match = re.search(r"PRAGMA table_info\((.+?)\)", query)
            if match:
                table = match.group(1).strip("'\"")
                # PRAGMA format: (cid, name, type, notnull, dflt_value, pk)
                query = f"SELECT ordinal_position as cid, column_name as name, data_type as type FROM information_schema.columns WHERE table_name = '{table}'"
        elif "sqlite_master" in query.lower():
            if "type='table' AND name=" in query:
                query = "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s"
            else:
                # Mock sqlite_master check for _migrate_pagar_constraint to return safely
                query = "SELECT '' as sql FROM information_schema.tables LIMIT 1"

        is_insert = query.strip().upper().startswith("INSERT")
        if is_insert and "RETURNING" not in query.upper():
            query += " RETURNING id"

        self.cursor.execute(query, vars)
        
        if self.cursor.description and is_insert:
            row = self.cursor.fetchone()
            self._lastrowid = row['id'] if row and 'id' in row else (row[0] if row else None)
        else:
            self._lastrowid = None
            
        return self
        
    def fetchone(self): return self.cursor.fetchone()
    def fetchall(self): return self.cursor.fetchall()
    def __iter__(self): return iter(self.cursor.fetchall())
    
    @property
    def lastrowid(self): return self._lastrowid

class PostgresConnWrapper:
    def __init__(self, conn):
        self.conn = conn
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def execute(self, query, vars=None):
        cur = PostgresCursorWrapper(self.conn.cursor())
        cur.execute(query, vars)
        return cur
    def executescript(self, sql):
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        sql = sql.replace("AUTOINCREMENT", "SERIAL")
        sql = sql.replace("datetime('now', 'localtime')", "CURRENT_TIMESTAMP::varchar")
        sql = sql.replace("date('now', 'localtime')", "CURRENT_DATE::varchar")
        sql = sql.replace("REAL", "DOUBLE PRECISION")
        cur = self.conn.cursor()
        cur.execute(sql)
    def commit(self): self.conn.commit()
    def rollback(self): self.conn.rollback()
    def close(self): self.conn.close()

@contextmanager
def get_db():
    if Config.DATABASE_URL and psycopg2:
        conn = psycopg2.connect(Config.DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.DictCursor
        try:
            yield PostgresConnWrapper(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
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

# ------------------------------------------------------------------------------
# 🏗️ CONSTRUIR LA BÓVEDA (init_db)
# ¿Qué hace esto? Imagina que el restaurante recién abre y no hay dónde guardar
# las facturas. Esta función lee el plano de construcción ("schema.sql"), que dice:
# "Construye una caja para los remitos, una caja para los bancos", y luego usa a 
# los obreros de la bóveda para armarlas (executescript).
# Finalmente, revisa si hay alguna caja vieja que necesite actualizarse (_run_migrations).
# ------------------------------------------------------------------------------
def init_db():
    with open(Config.SCHEMA_PATH, encoding="utf-8") as f:
        sql = f.read()
    with get_db() as conn:
        conn.executescript(sql)
        _run_migrations(conn)

def _table_exists(conn, name: str) -> bool:
    return table_exists(conn, name)


def _run_migrations(conn):
    """Migraciones incrementales sobre la conexión activa."""
    cols = {
        "uuid": "TEXT",
        "fecha_cierre": "TEXT",
        "fecha_vencimiento": "TEXT",
        "cuotas": "INTEGER",
        "cuotas_pagadas": "INTEGER NOT NULL DEFAULT 0",
        "kg": "REAL",
        "precio_kg": "REAL",
        "plazo_dias": "INTEGER",
    }
    is_pg = type(conn).__name__ == "PostgresConnWrapper"
    
    if _table_exists(conn, "operaciones_financieras"):
        if is_pg:
            existing = {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'operaciones_financieras'")}
        else:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(operaciones_financieras)")}
            
        for name, col_type in cols.items():
            if name not in existing:
                default = " DEFAULT 0" if name == "cuotas_pagadas" else ""
                pg_type = "DOUBLE PRECISION" if col_type == "REAL" else col_type
                final_type = pg_type if is_pg else col_type
                conn.execute(f"ALTER TABLE operaciones_financieras ADD COLUMN {name} {final_type}{default}")
                
        if "uuid" in existing or "uuid" in cols:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_op_uuid "
                "ON operaciones_financieras(uuid) WHERE uuid IS NOT NULL"
            )
        _migrate_pagar_constraint(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pagos_clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            monto REAL NOT NULL CHECK(monto > 0),
            fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        );
        CREATE TABLE IF NOT EXISTS aplicacion_pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pago_id INTEGER NOT NULL,
            remito_id INTEGER NOT NULL,
            monto_aplicado REAL NOT NULL CHECK(monto_aplicado > 0),
            FOREIGN KEY (pago_id) REFERENCES pagos_clientes(id) ON DELETE CASCADE,
            FOREIGN KEY (remito_id) REFERENCES remitos_carga(id) ON DELETE CASCADE
        );
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
        "pagado": "INTEGER NOT NULL DEFAULT 0",
        "monto_pagado": "REAL NOT NULL DEFAULT 0",
        "tipo_corte": "TEXT NOT NULL DEFAULT ''",
        "precio_por_kg": "REAL NOT NULL DEFAULT 0",
        "cantidad": "INTEGER NOT NULL DEFAULT 0",
        "pesos_piezas": "TEXT NOT NULL DEFAULT '[]'",
    }
    if _table_exists(conn, "remitos_carga"):
        if is_pg:
            remito_existing = {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'remitos_carga'")}
        else:
            remito_existing = {row[1] for row in conn.execute("PRAGMA table_info(remitos_carga)")}
            
        for name, col_type in remito_cols.items():
            if name not in remito_existing:
                pg_type = "DOUBLE PRECISION" if col_type.startswith("REAL") else col_type
                final_type = pg_type if is_pg else col_type
                conn.execute(f"ALTER TABLE remitos_carga ADD COLUMN {name} {final_type}")

    cliente_cols = {
        "fecha_ultimo_pago": "TEXT",
        "saldo_inicial": "REAL NOT NULL DEFAULT 0.0",
    }
    if _table_exists(conn, "clientes"):
        if is_pg:
            cliente_existing = {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'clientes'")}
        else:
            cliente_existing = {row[1] for row in conn.execute("PRAGMA table_info(clientes)")}
        for name, col_type in cliente_cols.items():
            if name not in cliente_existing:
                pg_type = "DOUBLE PRECISION DEFAULT 0.0" if col_type.startswith("REAL") else col_type
                final_type = pg_type if is_pg else col_type
                conn.execute(f"ALTER TABLE clientes ADD COLUMN {name} {final_type}")

    # Migración de clientes
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            scoring TEXT NOT NULL DEFAULT 'A',
            techo_deuda REAL NOT NULL DEFAULT 500000,
            saldo_actual REAL NOT NULL DEFAULT 0,
            saldo_inicial REAL NOT NULL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            fecha_ultimo_pago TEXT
        );
        """
    )

    # Migración de compras_bulk
    conn.executescript(
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
    conn.executescript(
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
    conn.executescript(
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
    
    # Migración de auditoria_operaciones
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS auditoria_operaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operacion_id INTEGER,
            alias TEXT NOT NULL,
            accion TEXT NOT NULL CHECK(accion IN ('CREADO', 'PAGADO', 'ELIMINADO')),
            monto REAL NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );
        """
    )

    # Migración de compras_bulk (costo_reparto)
    if _table_exists(conn, "compras_bulk"):
        if is_pg:
            bulk_existing = {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'compras_bulk'")}
        else:
            bulk_existing = {row[1] for row in conn.execute("PRAGMA table_info(compras_bulk)")}
        
        if "costo_reparto" not in bulk_existing:
            pg_type = "DOUBLE PRECISION"
            final_type = pg_type if is_pg else "REAL"
            conn.execute(f"ALTER TABLE compras_bulk ADD COLUMN costo_reparto {final_type} DEFAULT 0")

    # Migración de remitos_fracciones (costo_logistica_porcion)
    if _table_exists(conn, "remitos_fracciones"):
        if is_pg:
            frac_existing = {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'remitos_fracciones'")}
        else:
            frac_existing = {row[1] for row in conn.execute("PRAGMA table_info(remitos_fracciones)")}
        
        if "costo_logistica_porcion" not in frac_existing:
            pg_type = "DOUBLE PRECISION"
            final_type = pg_type if is_pg else "REAL"
            conn.execute(f"ALTER TABLE remitos_fracciones ADD COLUMN costo_logistica_porcion {final_type} DEFAULT 0")

    # Migración de clientes (telefono, cuit, direccion, email)
    if _table_exists(conn, "clientes"):
        if is_pg:
            cli_existing = {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'clientes'")}
        else:
            cli_existing = {row[1] for row in conn.execute("PRAGMA table_info(clientes)")}
        
        for col in ["telefono", "cuit", "direccion", "email"]:
            if col not in cli_existing:
                conn.execute(f"ALTER TABLE clientes ADD COLUMN {col} TEXT")

    # Migración ventas mostrador (POS offline sync)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ventas_mostrador (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER,
            producto TEXT NOT NULL DEFAULT '',
            monto REAL NOT NULL CHECK(monto > 0),
            tipo_pago TEXT NOT NULL CHECK(tipo_pago IN ('CONTADO', 'FIADO')) DEFAULT 'CONTADO',
            fecha TEXT NOT NULL DEFAULT (date('now', 'localtime')),
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        );
        """
    )
    if _table_exists(conn, "ventas_mostrador"):
        if is_pg:
            cols = {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'ventas_mostrador'")}
        else:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(ventas_mostrador)")}
            
        if "producto" not in cols:
            conn.execute("ALTER TABLE ventas_mostrador ADD COLUMN producto TEXT NOT NULL DEFAULT ''")
        if "offline_id" not in cols:
            conn.execute("ALTER TABLE ventas_mostrador ADD COLUMN offline_id INTEGER")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ventas_offline_id "
                "ON ventas_mostrador(offline_id) WHERE offline_id IS NOT NULL"
            )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            nombre TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operador',
            activo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS empresa_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            datos TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_clientes_nombre ON clientes(nombre);
        CREATE INDEX IF NOT EXISTS idx_remitos_cliente ON remitos_carga(cliente_id);
        CREATE INDEX IF NOT EXISTS idx_remitos_fecha ON remitos_carga(fecha);
        """
    )

    if _table_exists(conn, "auditoria_operaciones"):
        if is_pg:
            audit_existing = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'auditoria_operaciones'"
                )
            }
        else:
            audit_existing = {
                row[1] for row in conn.execute("PRAGMA table_info(auditoria_operaciones)")
            }
        for name, col_type in (
            ("entidad", "TEXT"),
            ("entidad_id", "INTEGER"),
            ("usuario", "TEXT"),
            ("detalle", "TEXT"),
        ):
            if name not in audit_existing:
                pg_type = "INTEGER" if col_type == "INTEGER" else "TEXT"
                final_type = pg_type if is_pg else col_type
                conn.execute(f"ALTER TABLE auditoria_operaciones ADD COLUMN {name} {final_type}")

def _migrate_pagar_constraint(conn):
    """Permite pagar = recibido (cheques sin interés)."""
    if type(conn).__name__ == "PostgresConnWrapper":
        return  # Schema.sql en Postgres ya tiene el CHECK(pagar >= recibido)
        
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
