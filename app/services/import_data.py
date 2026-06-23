"""Servicio para importar un backup completo en formato JSON."""
from __future__ import annotations

import json

from app.database import get_db, is_postgres
from app.services.users import save_empresa_config

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


def _enemigos_a_operaciones(enemigos: list[dict]) -> list[dict]:
    """Convierte filas del dashboard (enemigos) a operaciones_financieras crudas."""
    out: list[dict] = []
    for e in enemigos or []:
        if not e.get("alias"):
            continue
        out.append({
            "id": e.get("id"),
            "uuid": e.get("uuid"),
            "alias": e.get("alias"),
            "tipo": e.get("tipo") or "otro",
            "recibido": e.get("recibido"),
            "pagar": e.get("pagar") if e.get("pagar") is not None else e.get("total_pagar"),
            "meses": e.get("meses") or 1,
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


def _row_values(row: dict, columns: list[str]) -> tuple:
    return tuple(row.get(col) for col in columns)


def _insert_rows(conn, table: str, rows: list[dict], columns: list[str] | None = None) -> None:
    if not rows:
        return
    cols = columns or [k for k in rows[0].keys() if k != "oldest_unpaid" and k not in (
        "limite_superado", "en_mora", "inrecuperable", "margen", "estado_cobro", "costo_kg", "activo"
    )]
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    for row in rows:
        conn.execute(sql, _row_values(row, cols))


def _operaciones_desde_payload(json_data: dict) -> list[dict]:
    if json_data.get("operaciones_financieras"):
        return json_data["operaciones_financieras"]
    if json_data.get("operaciones"):
        return json_data["operaciones"]
    enemigos = json_data.get("enemigos") or []
    if enemigos:
        return _enemigos_a_operaciones(enemigos)
    return []


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
            "pagos_clientes": json_data.get("pagos_clientes") or [],
            "aplicacion_pagos": json_data.get("aplicacion_pagos") or [],
            "remitos_fracciones": json_data.get("remitos_fracciones") or [],
            "perdidas_acumuladas": json_data.get("perdidas_acumuladas") or json_data.get("perdidas") or [],
            "ventas_mostrador": json_data.get("ventas_mostrador") or [],
            "auditoria_operaciones": json_data.get("auditoria_operaciones") or json_data.get("auditoria_reciente") or json_data.get("auditoria") or [],
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
        "compras_bulk": json_data.get("bulk") or [],
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
            for r in (json_data.get("remitos") or [])
        ],
        "pagos_cuotas": json_data.get("pagos_cuotas") or [],
        "pagos_clientes": json_data.get("pagos_clientes") or [],
        "aplicacion_pagos": json_data.get("aplicacion_pagos") or [],
        "remitos_fracciones": json_data.get("remitos_fracciones") or [],
        "perdidas_acumuladas": json_data.get("perdidas") or [],
        "ventas_mostrador": json_data.get("ventas_mostrador") or [],
        "auditoria_operaciones": json_data.get("auditoria_operaciones") or json_data.get("auditoria_reciente") or json_data.get("auditoria") or [],
    }


def import_all_data(json_data: dict) -> None:
    """Restaura todos los datos del tenant a partir de un backup JSON."""
    if not json_data:
        raise ValueError("El backup está vacío")

    if json_data.get("version") == "cache_snapshot_v1":
        if json_data.get("fullBackup"):
            json_data = json_data["fullBackup"]
        elif json_data.get("appData"):
            json_data = json_data["appData"]
        else:
            raise ValueError("El snapshot de caché no contiene datos")

    tables = _normalize_payload(json_data)
    has_rows = any(tables.get(t) for t in CLEAR_ORDER)
    if not has_rows and not json_data.get("empresa"):
        raise ValueError("El archivo no contiene datos para restaurar")

    with get_db() as conn:
        if not is_postgres():
            conn.execute("PRAGMA foreign_keys = OFF")

        for table in CLEAR_ORDER:
            conn.execute(f"DELETE FROM {table}")

        empresa_raw = json_data.get("empresa")
        if empresa_raw:
            try:
                save_empresa_config(empresa_raw)
            except Exception:
                # La empresa es opcional: no debe bloquear la restauración de datos.
                pass

        clientes = []
        for c in tables["clientes"]:
            clientes.append({
                "id": c.get("id"),
                "nombre": c.get("nombre"),
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

        _insert_rows(conn, "entidades_bancarias", tables["entidades_bancarias"])
        _insert_rows(conn, "clientes", clientes, CLIENTE_COLS)
        _insert_rows(conn, "compras_bulk", tables["compras_bulk"])
        _insert_rows(conn, "operaciones_financieras", tables["operaciones_financieras"], OPERACION_COLS)
        _insert_rows(conn, "remitos_carga", tables["remitos_carga"])
        _insert_rows(conn, "pagos_cuotas", tables["pagos_cuotas"])
        _insert_rows(conn, "pagos_clientes", tables["pagos_clientes"])
        _insert_rows(conn, "aplicacion_pagos", tables["aplicacion_pagos"])
        _insert_rows(conn, "remitos_fracciones", tables["remitos_fracciones"])
        _insert_rows(conn, "perdidas_acumuladas", tables["perdidas_acumuladas"])
        _insert_rows(conn, "ventas_mostrador", tables["ventas_mostrador"])
        _insert_rows(conn, "auditoria_operaciones", tables["auditoria_operaciones"])

        if not is_postgres():
            conn.execute("PRAGMA foreign_keys = ON")
