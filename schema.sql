-- MASTER TOTAL — Schema Distribuidora de Carne (Modular Version)

CREATE TABLE IF NOT EXISTS entidades_bancarias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    limite REAL NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS operaciones_financieras (
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
    kg REAL,
    precio_kg REAL,
    plazo_dias INTEGER,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
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

CREATE TABLE IF NOT EXISTS compras_bulk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL DEFAULT (date('now', 'localtime')),
    kg_totales REAL NOT NULL CHECK(kg_totales > 0),
    kg_remanentes REAL NOT NULL CHECK(kg_remanentes >= 0),
    costo_total_bulk REAL NOT NULL CHECK(costo_total_bulk > 0),
    costo_reparto REAL NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS clientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    scoring TEXT NOT NULL CHECK(scoring IN ('A', 'B', 'C', 'D')) DEFAULT 'A',
    techo_deuda REAL NOT NULL DEFAULT 500000,
    saldo_actual REAL NOT NULL DEFAULT 0,
    saldo_inicial REAL NOT NULL DEFAULT 0.0,
    telefono TEXT,
    cuit TEXT,
    direccion TEXT,
    email TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    fecha_ultimo_pago TEXT
);

CREATE TABLE IF NOT EXISTS remitos_carga (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL DEFAULT (date('now', 'localtime')),
    cliente TEXT NOT NULL DEFAULT '',
    cliente_id INTEGER,
    tipo_corte TEXT NOT NULL DEFAULT '',
    cantidad INTEGER NOT NULL DEFAULT 0,
    pesos_piezas TEXT NOT NULL DEFAULT '[]',
    kg REAL NOT NULL CHECK(kg > 0),
    precio_por_kg REAL NOT NULL DEFAULT 0 CHECK(precio_por_kg >= 0),
    costo_total_logistica REAL NOT NULL DEFAULT 0 CHECK(costo_total_logistica >= 0),
    precio_venta_total REAL NOT NULL CHECK(precio_venta_total >= 0),
    plazo_cobro_dias INTEGER NOT NULL DEFAULT 0 CHECK(plazo_cobro_dias >= 0),
    costo_carne REAL NOT NULL DEFAULT 0,
    pagado INTEGER NOT NULL DEFAULT 0 CHECK(pagado IN (0, 1, 2)),
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
);

CREATE TABLE IF NOT EXISTS perdidas_acumuladas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER NOT NULL,
    monto_nominal REAL NOT NULL CHECK(monto_nominal >= 0),
    fecha_perdida TEXT NOT NULL DEFAULT (date('now', 'localtime')),
    costo_oportunidad_interes REAL NOT NULL DEFAULT 0.0 CHECK(costo_oportunidad_interes >= 0),
    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
);

CREATE TABLE IF NOT EXISTS remitos_fracciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remito_id INTEGER NOT NULL,
    lote_id INTEGER NOT NULL,
    kg_descontados REAL NOT NULL CHECK(kg_descontados > 0),
    costo_porcion REAL NOT NULL CHECK(costo_porcion >= 0),
    costo_logistica_porcion REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (remito_id) REFERENCES remitos_carga(id) ON DELETE CASCADE,
    FOREIGN KEY (lote_id) REFERENCES compras_bulk(id),
    UNIQUE(remito_id, lote_id)
);

CREATE TABLE IF NOT EXISTS auditoria_operaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operacion_id INTEGER,
    alias TEXT NOT NULL,
    accion TEXT NOT NULL CHECK(accion IN ('CREADO', 'PAGADO', 'ELIMINADO')),
    monto REAL NOT NULL DEFAULT 0,
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_op_alias ON operaciones_financieras(alias);
CREATE INDEX IF NOT EXISTS idx_op_cfr ON operaciones_financieras(recibido, pagar, meses);
CREATE INDEX IF NOT EXISTS idx_remitos_fecha ON remitos_carga(fecha);
CREATE INDEX IF NOT EXISTS idx_bulk_fecha ON compras_bulk(fecha);
