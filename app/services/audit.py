"""Auditoría empresarial — registro append-only de acciones sensibles."""
from __future__ import annotations

from flask import session, request, has_request_context

from app.database import ensure_tenant_migrations, get_db


def _actor() -> str:
    try:
        if has_request_context() and session.get("username"):
            return str(session["username"])
        if has_request_context() and session.get("auth_method") == "api_key":
            return "api_key"
    except RuntimeError:
        pass
    return "sistema"


def log_audit(
    accion: str,
    *,
    entidad: str = "",
    entidad_id: int | None = None,
    alias: str = "",
    monto: float | None = None,
    detalle: str = "",
    operacion_id: int | None = None,
) -> None:
    allowed = {"CREADO", "PAGADO", "ELIMINADO"}
    accion_db = accion if accion in allowed else "CREADO"
    if accion not in allowed:
        detalle = f"{accion}: {detalle}".strip(": ")
    alias_val = alias or entidad or "sistema"
    
    ip_addr = None
    u_agent = None
    try:
        if has_request_context():
            ip_addr = request.remote_addr
            u_agent = request.user_agent.string[:250] if request.user_agent.string else None
    except RuntimeError:
        pass

    with get_db() as conn:
        ensure_tenant_migrations(conn)
        conn.execute(
            """
            INSERT INTO auditoria_operaciones
                (operacion_id, alias, accion, monto, entidad, entidad_id, usuario, detalle, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operacion_id,
                alias_val,
                accion_db,
                monto or 0,
                entidad,
                entidad_id,
                _actor(),
                detalle[:500] if detalle else None,
                ip_addr,
                u_agent,
            ),
        )


def list_audit_log(limit: int = 200, offset: int = 0) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    with get_db() as conn:
        ensure_tenant_migrations(conn)
        rows = conn.execute(
            """
            SELECT id, operacion_id, alias, accion, monto, fecha,
                   entidad, entidad_id, usuario, detalle
            FROM auditoria_operaciones
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]
