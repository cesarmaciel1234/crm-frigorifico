"""Motor central de sincronización: caché local ↔ base de datos ↔ nodos de dispositivo."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database import ensure_tenant_migrations, get_db
from app.services.audit import list_audit_log
from app.services.bancos import list_bancos
from app.services.bulk import list_bulk_lots
from app.services.clientes import list_clientes, list_perdidas_acumuladas
from app.services.export_data import export_all_data
from app.services.finanzas import historial_vencimientos, panel_estrategia, ranking_enemigos
from app.services.pagos import list_historial_pagos
from app.services.remitos import list_remitos

MAX_NODOS = 10
MAX_SNAPSHOT_CHARS = 2_500_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metricas_flotantes(enemigos: list[dict], estrategia: dict) -> dict[str, Any]:
    sangre_diaria = 0.0
    interes_diario = 0.0
    deuda_total = 0.0
    interes_acumulado = 0.0
    for d in enemigos:
        monto = d.get("recibido") or 0
        interes = d.get("interes") or 0
        dias = max(1, d.get("dias_faltantes") or 30)
        sangre_diaria += (monto + interes) / dias
        interes_diario += interes / dias
        deuda_total += monto + interes
        interes_acumulado += interes
    capital_disponible = estrategia.get("activo", {}).get("capital_neto", 0) + interes_acumulado
    cubre = estrategia.get("activo", {}).get("activo_pendiente", 0) >= (
        estrategia.get("activo", {}).get("deuda_real", 0) - interes_acumulado
    )
    return {
        "sangre": sangre_diaria,
        "int_diario": interes_diario,
        "deuda": deuda_total,
        "int_acumulado": interes_acumulado,
        "capital": capital_disponible,
        "tendencia": "up" if cubre else "down",
    }


def build_client_app_data() -> dict[str, Any]:
    """Paquete que la app guarda en caché local (misma forma que loadAll)."""
    estrategia = panel_estrategia()
    enemigos = ranking_enemigos()
    remitos = list_remitos(8)
    historial = historial_vencimientos()
    vencidos = [h for h in historial if h.get("vencido")]
    metricas = _metricas_flotantes(enemigos, estrategia)
    dash = {
        "estrategia": estrategia,
        "enemigos": enemigos,
        "remitos": remitos,
        "bancos": list_bancos(),
        "historial": historial,
        "perdidas": list_perdidas_acumuladas(),
        "metricas_flotantes": metricas,
        "totales": {
            "deudas_activas": len(enemigos),
            "urgentes": sum(1 for e in enemigos if e.get("urgente")),
            "remitos_recientes": len(remitos),
            "intereses_totales": estrategia["sangria"]["intereses_totales"],
            "tarjetas_vencidas": len(vencidos),
            "total_pagar_vencido": round(sum(h["total_pagar"] for h in vencidos), 2),
        },
    }
    return {
        **dash,
        "historialPagos": list_historial_pagos(),
        "bulk": list_bulk_lots(),
        "clientes": list_clientes(),
        "auditoria": list_audit_log(limit=200),
    }


def build_sync_pull_bundle() -> dict[str, Any]:
    """Descarga bidireccional: motor central → caché del dispositivo."""
    return {
        "version": "sync_bundle_v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "appData": build_client_app_data(),
        "fullBackup": export_all_data(),
    }


def list_sync_nodos() -> list[dict[str, Any]]:
    with get_db() as conn:
        ensure_tenant_migrations(conn)
        rows = conn.execute(
            """
            SELECT device_id, etiqueta, updated_at,
                   LENGTH(snapshot_json) AS snapshot_bytes
            FROM sync_nodos
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [
        {
            "device_id": r["device_id"],
            "etiqueta": r["etiqueta"] or "",
            "updated_at": r["updated_at"],
            "snapshot_bytes": int(r["snapshot_bytes"] or 0),
        }
        for r in rows
    ]


def get_sync_nodo(device_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        ensure_tenant_migrations(conn)
        row = conn.execute(
            "SELECT device_id, etiqueta, snapshot_json, updated_at FROM sync_nodos WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    if not row:
        return None
    try:
        snapshot = json.loads(row["snapshot_json"])
    except (TypeError, json.JSONDecodeError):
        snapshot = {}
    return {
        "device_id": row["device_id"],
        "etiqueta": row["etiqueta"] or "",
        "updated_at": row["updated_at"],
        "snapshot": snapshot,
    }


def save_sync_nodo(device_id: str, etiqueta: str, snapshot: dict) -> dict[str, Any]:
    if not device_id or len(device_id) > 80:
        raise ValueError("device_id inválido")
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot debe ser un objeto")
    raw = json.dumps(snapshot, ensure_ascii=False)
    if len(raw) > MAX_SNAPSHOT_CHARS:
        raise ValueError("El snapshot supera el tamaño máximo permitido para nodos")

    ts = _utc_now()

    with get_db() as conn:
        ensure_tenant_migrations(conn)
        exists = conn.execute(
            "SELECT 1 FROM sync_nodos WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE sync_nodos
                SET etiqueta = ?, snapshot_json = ?, updated_at = ?
                WHERE device_id = ?
                """,
                ((etiqueta or "")[:120], raw, ts, device_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO sync_nodos (device_id, etiqueta, snapshot_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (device_id, (etiqueta or "")[:120], raw, ts),
            )
        count = conn.execute("SELECT COUNT(*) AS c FROM sync_nodos").fetchone()["c"]
        if count > MAX_NODOS:
            excess = int(count) - MAX_NODOS
            conn.execute(
                """
                DELETE FROM sync_nodos
                WHERE device_id IN (
                    SELECT device_id FROM sync_nodos
                    ORDER BY updated_at ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
    return {"ok": True, "device_id": device_id, "nodos": len(list_sync_nodos())}
