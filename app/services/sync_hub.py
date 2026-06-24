"""Motor central de sincronización: caché local ↔ base de datos ↔ nodos de dispositivo."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database import ensure_tenant_migrations, get_db
from app.utils import parse_operacion_payload
from app.services.audit import list_audit_log
from app.services.bancos import list_bancos
from app.services.bulk import list_bulk_lots
from app.services.clientes import list_clientes, list_perdidas_acumuladas
from app.services.export_data import export_all_data
from app.services.finanzas import (
    calc_metricas_flotantes,
    historial_vencimientos,
    panel_estrategia,
    ranking_enemigos,
)
from app.services.pagos import list_historial_pagos
from app.services.remitos import list_remitos

MAX_NODOS = 10
MAX_SNAPSHOT_CHARS = 2_500_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metricas_flotantes(enemigos: list[dict], estrategia: dict) -> dict[str, Any]:
    return calc_metricas_flotantes(enemigos, estrategia)


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
    return build_sync_pull_delta(since=0)


def _should_apply_lww(remote_ts: str, local_ts: str | None) -> bool:
    if not local_ts:
        return True
    return remote_ts >= local_ts


def _record_changelog(
    conn,
    *,
    op_id: str,
    device_id: str,
    entity: str,
    entity_uuid: str,
    action: str,
    payload: dict,
    updated_at_utc: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sync_changelog
            (op_id, device_id, entity, entity_uuid, action, payload_json, updated_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (op_id, device_id, entity, entity_uuid, action, json.dumps(payload, ensure_ascii=False), updated_at_utc),
    )


def _apply_operacion_lww(conn, entity_uuid: str, action: str, payload: dict, ts: str) -> bool:
    if action == "DELETE":
        conn.execute("DELETE FROM operaciones_financieras WHERE uuid = ?", (entity_uuid,))
        return True

    row = conn.execute(
        "SELECT id, updated_at_utc FROM operaciones_financieras WHERE uuid = ?",
        (entity_uuid,),
    ).fetchone()
    if row and not _should_apply_lww(ts, row["updated_at_utc"]):
        return False

    merged = {**payload, "uuid": entity_uuid}
    parsed = parse_operacion_payload(merged)

    fields = {
        "alias": parsed["alias"],
        "tipo": parsed["tipo"],
        "recibido": float(parsed["recibido"]),
        "pagar": float(parsed["pagar"]),
        "meses": int(parsed["meses"]),
        "fecha_cierre": parsed.get("fecha_cierre"),
        "fecha_vencimiento": parsed.get("fecha_vencimiento"),
        "cuotas": parsed.get("cuotas"),
        "kg": parsed.get("kg"),
        "precio_kg": parsed.get("precio_kg"),
        "plazo_dias": parsed.get("plazo_dias"),
        "impuesto_cheque": parsed.get("impuesto_cheque"),
    }

    if row:
        conn.execute(
            """
            UPDATE operaciones_financieras
            SET alias=?, tipo=?, recibido=?, pagar=?, meses=?, fecha_cierre=?,
                fecha_vencimiento=?, cuotas=?, kg=?, precio_kg=?, plazo_dias=?,
                impuesto_cheque=?, updated_at_utc=?
            WHERE uuid=?
            """,
            (
                fields["alias"], fields["tipo"], fields["recibido"], fields["pagar"],
                fields["meses"], fields["fecha_cierre"], fields["fecha_vencimiento"],
                fields["cuotas"], fields["kg"], fields["precio_kg"], fields["plazo_dias"],
                fields["impuesto_cheque"], ts, entity_uuid,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO operaciones_financieras
                (uuid, alias, tipo, recibido, pagar, meses, fecha_cierre, fecha_vencimiento,
                 cuotas, kg, precio_kg, plazo_dias, impuesto_cheque, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_uuid, fields["alias"], fields["tipo"], fields["recibido"],
                fields["pagar"], fields["meses"], fields["fecha_cierre"],
                fields["fecha_vencimiento"], fields["cuotas"], fields["kg"],
                fields["precio_kg"], fields["plazo_dias"], fields["impuesto_cheque"], ts,
            ),
        )
    return True


def _apply_cliente_lww(conn, entity_uuid: str, action: str, payload: dict, ts: str) -> bool:
    if action == "DELETE":
        return False

    nombre = str(payload.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("cliente sin nombre")

    row = conn.execute(
        "SELECT id, updated_at_utc FROM clientes WHERE uuid = ?",
        (entity_uuid,),
    ).fetchone()
    if row and not _should_apply_lww(ts, row["updated_at_utc"]):
        return False

    scoring = str(payload.get("scoring") or "A").upper()
    if scoring not in {"A", "B", "C", "D"}:
        scoring = "A"

    values = (
        nombre,
        scoring,
        float(payload.get("techo_deuda") or 500000),
        float(payload.get("saldo_actual") or payload.get("saldo_inicial") or 0),
        float(payload.get("saldo_inicial") or 0),
        payload.get("telefono"),
        payload.get("cuit"),
        payload.get("direccion"),
        payload.get("email"),
        ts,
        entity_uuid,
    )

    if row:
        conn.execute(
            """
            UPDATE clientes
            SET nombre=?, scoring=?, techo_deuda=?, saldo_actual=?, saldo_inicial=?,
                telefono=?, cuit=?, direccion=?, email=?, updated_at_utc=?
            WHERE uuid=?
            """,
            values,
        )
    else:
        conn.execute(
            """
            INSERT INTO clientes
                (uuid, nombre, scoring, techo_deuda, saldo_actual, saldo_inicial,
                 telefono, cuit, direccion, email, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_uuid, *values[:-1]),
        )
    return True


def _apply_entity_lww(conn, op: dict) -> bool:
    entity = op.get("entity")
    entity_uuid = op.get("entity_uuid")
    action = op.get("action") or "CREATE"
    payload = op.get("payload") or {}
    ts = op.get("updated_at_utc") or _utc_now()

    if not entity_uuid:
        raise ValueError("entity_uuid requerido")

    if entity == "operacion":
        return _apply_operacion_lww(conn, entity_uuid, action, payload, ts)
    if entity == "cliente":
        return _apply_cliente_lww(conn, entity_uuid, action, payload, ts)
    raise ValueError(f"entidad no soportada en sync v2: {entity}")


def apply_sync_operations(device_id: str, operations: list[dict]) -> dict[str, Any]:
    acked: list[str] = []
    rejected: list[dict] = []

    with get_db() as conn:
        ensure_tenant_migrations(conn)
        for op in operations or []:
            op_id = str(op.get("op_id") or "").strip()
            if not op_id:
                rejected.append({"op_id": None, "reason": "op_id requerido", "fatal": True})
                continue

            seen = conn.execute(
                "SELECT 1 FROM sync_changelog WHERE op_id = ?", (op_id,)
            ).fetchone()
            if seen:
                acked.append(op_id)
                continue

            try:
                _apply_entity_lww(conn, op)
                _record_changelog(
                    conn,
                    op_id=op_id,
                    device_id=device_id,
                    entity=str(op.get("entity") or ""),
                    entity_uuid=str(op.get("entity_uuid") or ""),
                    action=str(op.get("action") or "CREATE"),
                    payload=op.get("payload") or {},
                    updated_at_utc=op.get("updated_at_utc") or _utc_now(),
                )
                acked.append(op_id)
            except ValueError as e:
                rejected.append({"op_id": op_id, "reason": str(e), "fatal": True})
            except Exception as e:
                rejected.append({"op_id": op_id, "reason": str(e), "fatal": False})

    return {"acked": acked, "rejected": rejected}


def build_sync_pull_delta(since: int = 0, *, include_full: bool = False) -> dict[str, Any]:
    since = max(0, int(since or 0))
    with get_db() as conn:
        ensure_tenant_migrations(conn)
        rows = conn.execute(
            """
            SELECT id, entity, entity_uuid, action, payload_json, updated_at_utc
            FROM sync_changelog
            WHERE id > ?
            ORDER BY id ASC
            LIMIT 500
            """,
            (since,),
        ).fetchall()

    changes: list[dict[str, Any]] = []
    cursor = since
    for row in rows:
        cursor = max(cursor, int(row["id"]))
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        changes.append({
            "id": int(row["id"]),
            "entity": row["entity"],
            "entity_uuid": row["entity_uuid"],
            "action": row["action"],
            "payload": payload,
            "updated_at_utc": row["updated_at_utc"],
        })

    bundle: dict[str, Any] = {
        "version": "sync_bundle_v2",
        "updated_at": _utc_now(),
        "cursor": cursor,
        "changes": changes,
    }
    if since == 0:
        bundle["appData"] = build_client_app_data()
        if include_full:
            bundle["fullBackup"] = export_all_data()
    return bundle


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
