"""Servicio para importar un backup completo en formato JSON."""
from __future__ import annotations

import json
from typing import Any

from app.database import get_db, is_postgres
from app.services.users import normalize_empresa_config

CLEAR_ORDER = [
    "aplicacion_pagos",
    "pagos_clientes",
    "remitos_fracciones",
    "remitos_carga",
    "pagos_cuotas",
    "operaciones_financieras",
    "perdidas_acumuladas",
    "ventas_mostrador",
    "compras_bulk",
    "clientes",
    "entidades_bancarias",
    "auditoria_operaciones",
]

CLIENTE_COLS = [
    "id", "nombre", "scoring", "techo_deuda", "saldo_actual", "saldo_inicial",
    "telefono", "cuit", "direccion", "email", "created_at", "fecha_ultimo_pago",
]

OPERACION_COLS = [
    "id", "uuid", "alias", "tipo", "recibido", "pagar", "meses",
    "fecha_cierre", "fecha_vencimiento", "cuotas", "cuotas_pagadas",
    "kg", "precio_kg", "plazo_dias", "created_at",
]

SKIP_ROW_KEYS = frozenset({
    "oldest_unpaid", "limite_superado", "en_mora", "inrecuperable",
    "margen", "estado_cobro", "costo_kg", "activo", "cfr", "urgente",
    "es_tarjeta", "es_cheque", "es_proveedor", "sin_interes", "prioridad",
    "pos", "completa", "tiene_cuotas", "cuotas_total", "cuotas_vencidas",
    "cuotas_vencidas_lista", "dias_faltantes", "dias_retraso", "estado_vencimiento",
    "mensaje_vencimiento", "monto_cuota", "saldo_pendiente", "reserva_diaria",
    "total_pagar", "cuota_en_curso", "plazo_texto", "vencido", "interes",
})


def _enemigos_a_operaciones(enemigos: list[dict]) -> list[dict]:
    """Convierte filas del dashboard (enemigos) a operaciones_financieras crudas."""
    out: list[dict] = []
    for e in enemigos or []:
        if not e.get("alias"):
            continue
        recibido = float(e.get("recibido") or 0)
        pagar = float(e.get("pagar") if e.get("pagar") is not None else e.get("total_pagar") or 0)
        if recibido <= 0:
            continue
        if pagar < recibido:
            pagar = recibido
        out.append({
            "id": e.get("id"),
            "uuid": e.get("uuid"),
            "alias": e.get("alias"),
            "tipo": e.get("tipo") or "otro",
            "recibido": recibido,
            "pagar": pagar,
            "meses": max(int(e.get("meses") or 1), 1),
            "fecha_cierre": e.get("fecha_cierre"),
            "fecha_vencimiento": e.get("fecha_vencimiento"),
            "cuotas": e.get("cuotas") or e.get("cuotas_total") or 1,
            "cuotas_pagadas": e.get("cuotas_pagadas") or 0,
            "kg": e.get("kg"),
            "precio_kg": e.get("precio_kg"),
            "plazo_dias": e.get("plazo_dias"),
            "created_at": e.get("created_at"),
        })
    return out


def _operaciones_desde_payload(json_data: dict) -> list[dict]:
    raw = (
        json_data.get("operaciones_financieras")
        or json_data.get("operaciones")
        or []
    )
    if raw:
        return _enemigos_a_operaciones(raw) if raw[0].get("cfr") is not None else raw
    enemigos = json_data.get("enemigos") or []
    if enemigos:
        return _enemigos_a_operaciones(enemigos)
    return []


def _merge_app_into_payload(base: dict, app: dict) -> dict:
    """Completa un backup parcial con datos del snapshot appData."""
    merged = dict(base)
    if not _operaciones_desde_payload(merged) and app.get("enemigos"):
        merged["enemigos"] = app["enemigos"]
    pairs = (
        ("clientes", "clientes"),
        ("remitos_carga", "remitos"),
        ("remitos", "remitos"),
        ("compras_bulk", "bulk"),
        ("bulk", "bulk"),
        ("entidades_bancarias", "bancos"),
        ("bancos", "bancos"),
        ("auditoria_operaciones", "auditoria"),
        ("auditoria", "auditoria"),
        ("perdidas_acumuladas", "perdidas"),
        ("perdidas", "perdidas"),
        ("pagos_clientes", "historialPagos"),
        ("historialPagos", "historialPagos"),
    )
    for target, source in pairs:
        if not merged.get(target) and app.get(source):
            merged[target] = app[source]
    return merged


def unwrap_backup_payload(json_data: dict) -> dict:
    """Desempaqueta cache_snapshot_v1 y fusiona fullBackup + appData."""
    if json_data.get("version") != "cache_snapshot_v1":
        return json_data

    full = json_data.get("fullBackup")
    app = json_data.get("appData") or {}

    if full and isinstance(full, dict):
        payload = _merge_app_into_payload(full, app)
    elif app:
        payload = dict(app)
    else:
        raise ValueError("El snapshot de caché no contiene datos")

    return payload


def _normalize_payload(json_data: dict) -> dict[str, list[dict]]:
    """Convierte export v1 (legado) y v2 (tablas crudas) a un formato unificado."""
    version_raw = json_data.get("version")
    try:
        version = int(version_raw) if version_raw is not None else 1
    except (TypeError, ValueError):
        version = 1
    if version >= 2:
        return {
            "entidades_bancarias": json_data.get("entidades_bancarias") or json_data.get("bancos") or [],
            "clientes": json_data.get("clientes") or [],
            "compras_bulk": json_data.get("compras_bulk") or json_data.get("bulk") or [],
            "operaciones_financieras": _operaciones_desde_payload(json_data),
            "remitos_carga": json_data.get("remitos_carga") or json_data.get("remitos") or [],
            "pagos_cuotas": json_data.get("pagos_cuotas") or [],
            "pagos_clientes": json_data.get("pagos_clientes") or json_data.get("historialPagos") or [],
            "aplicacion_pagos": json_data.get("aplicacion_pagos") or [],
            "remitos_fracciones": json_data.get("remitos_fracciones") or [],
            "perdidas_acumuladas": json_data.get("perdidas_acumuladas") or json_data.get("perdidas") or [],
            "ventas_mostrador": json_data.get("ventas_mostrador") or [],
            "auditoria_operaciones": (
                json_data.get("auditoria_operaciones")
                or json_data.get("auditoria_reciente")
                or json_data.get("auditoria")
                or []
            ),
        }

    return {
        "entidades_bancarias": [
            {"id": b.get("id"), "nombre": b.get("nombre"), "limite": b.get("limite", 0)}
            for b in (json_data.get("bancos") or [])
        ],
        "clientes": [
            {col: c.get(col) for col in CLIENTE_COLS if col in c or col in ("id", "nombre")}
            for c in (json_data.get("clientes") or [])
        ],
        "compras_bulk": json_data.get("bulk") or json_data.get("compras_bulk") or [],
        "operaciones_financieras": _operaciones_desde_payload(json_data),
        "remitos_carga": [
            {
                "id": r.get("id"),
                "fecha": r.get("fecha"),
                "cliente": r.get("cliente", ""),
                "cliente_id": r.get("cliente_id"),
                "tipo_corte": r.get("tipo_corte", ""),
                "cantidad": r.get("cantidad", 0),
                "pesos_piezas": r.get("pesos_piezas", "[]") if isinstance(r.get("pesos_piezas"), str) else json.dumps(r.get("pesos_piezas") or []),
                "kg": r.get("kg", 0),
                "precio_por_kg": r.get("precio_por_kg", 0),
                "costo_total_logistica": r.get("costo_total_logistica", 0),
                "precio_venta_total": r.get("precio_venta_total", 0),
                "plazo_cobro_dias": r.get("plazo_cobro_dias", 0),
                "costo_carne": r.get("costo_carne", 0),
                "pagado": r.get("pagado", 0),
                "monto_pagado": r.get("monto_pagado", 0),
                "created_at": r.get("created_at"),
            }
            for r in (json_data.get("remitos") or json_data.get("remitos_carga") or [])
        ],
        "pagos_cuotas": json_data.get("pagos_cuotas") or [],
        "pagos_clientes": json_data.get("pagos_clientes") or json_data.get("historialPagos") or [],
        "aplicacion_pagos": json_data.get("aplicacion_pagos") or [],
        "remitos_fracciones": json_data.get("remitos_fracciones") or [],
        "perdidas_acumuladas": json_data.get("perdidas") or json_data.get("perdidas_acumuladas") or [],
        "ventas_mostrador": json_data.get("ventas_mostrador") or [],
        "auditoria_operaciones": (
            json_data.get("auditoria_operaciones")
            or json_data.get("auditoria_reciente")
            or json_data.get("auditoria")
            or []
        ),
    }


def _row_values(row: dict, columns: list[str]) -> tuple:
    return tuple(row.get(col) for col in columns)


def _insert_rows(
    conn,
    table: str,
    rows: list[dict],
    columns: list[str] | None = None,
) -> tuple[int, int]:
    if not rows:
        return 0, 0
    cols = columns or [
        k for k in rows[0].keys()
        if k not in SKIP_ROW_KEYS
    ]
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    inserted = 0
    skipped = 0
    for row in rows:
        try:
            conn.execute(sql, _row_values(row, cols))
            inserted += 1
        except Exception:
            skipped += 1
    return inserted, skipped


def _save_empresa_in_conn(conn, empresa_raw: dict) -> bool:
    try:
        payload = json.dumps(normalize_empresa_config(empresa_raw), ensure_ascii=False)
        exists = conn.execute("SELECT 1 FROM empresa_config WHERE id = 1").fetchone()
        if exists:
            conn.execute(
                "UPDATE empresa_config SET datos = ?, updated_at = datetime('now', 'localtime') WHERE id = 1",
                (payload,),
            )
        else:
            conn.execute("INSERT INTO empresa_config (id, datos) VALUES (1, ?)", (payload,))
        return True
    except Exception:
        return False


def _reset_pg_sequences(conn) -> None:
    if not is_postgres():
        return
    for table in CLEAR_ORDER + ["empresa_config"]:
        try:
            conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
            )
        except Exception:
            pass


def import_all_data(json_data: dict) -> dict[str, Any]:
    """Restaura todos los datos del tenant. Devuelve resumen de filas importadas."""
    if not json_data:
        raise ValueError("El backup está vacío")
    if not isinstance(json_data, dict):
        raise ValueError("El backup debe ser un objeto JSON")

    json_data = unwrap_backup_payload(json_data)
    tables = _normalize_payload(json_data)
    has_rows = any(tables.get(t) for t in CLEAR_ORDER)
    if not has_rows and not json_data.get("empresa"):
        raise ValueError("El archivo no contiene datos para restaurar")

    summary: dict[str, Any] = {"tablas": {}, "empresa": False, "advertencias": 0}

    with get_db() as conn:
        if not is_postgres():
            conn.execute("PRAGMA foreign_keys = OFF")

        for table in CLEAR_ORDER:
            conn.execute(f"DELETE FROM {table}")

        empresa_raw = json_data.get("empresa")
        if empresa_raw:
            summary["empresa"] = _save_empresa_in_conn(conn, empresa_raw)

        clientes = []
        for c in tables["clientes"]:
            nombre = (c.get("nombre") or "").strip()
            if not nombre:
                summary["advertencias"] += 1
                continue
            clientes.append({
                "id": c.get("id"),
                "nombre": nombre,
                "scoring": c.get("scoring") or "A",
                "techo_deuda": c.get("techo_deuda", 500000),
                "saldo_actual": c.get("saldo_actual", 0),
                "saldo_inicial": c.get("saldo_inicial", 0),
                "telefono": c.get("telefono"),
                "cuit": c.get("cuit"),
                "direccion": c.get("direccion"),
                "email": c.get("email"),
                "created_at": c.get("created_at"),
                "fecha_ultimo_pago": c.get("fecha_ultimo_pago"),
            })

        inserts = [
            ("entidades_bancarias", tables["entidades_bancarias"], None),
            ("clientes", clientes, CLIENTE_COLS),
            ("compras_bulk", tables["compras_bulk"], None),
            ("operaciones_financieras", tables["operaciones_financieras"], OPERACION_COLS),
            ("remitos_carga", tables["remitos_carga"], None),
            ("pagos_cuotas", tables["pagos_cuotas"], None),
            ("pagos_clientes", tables["pagos_clientes"], None),
            ("aplicacion_pagos", tables["aplicacion_pagos"], None),
            ("remitos_fracciones", tables["remitos_fracciones"], None),
            ("perdidas_acumuladas", tables["perdidas_acumuladas"], None),
            ("ventas_mostrador", tables["ventas_mostrador"], None),
            ("auditoria_operaciones", tables["auditoria_operaciones"], None),
        ]
        for table, rows, cols in inserts:
            ins, skip = _insert_rows(conn, table, rows, cols)
            summary["tablas"][table] = {"insertados": ins, "omitidos": skip}
            summary["advertencias"] += skip

        _reset_pg_sequences(conn)

        if not is_postgres():
            conn.execute("PRAGMA foreign_keys = ON")

    return summary
