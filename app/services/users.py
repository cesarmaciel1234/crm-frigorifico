"""Usuarios, roles y configuración empresarial."""
from __future__ import annotations

import json
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from app.database import get_db
from app.config import Config

ROLES = frozenset({"admin", "operador", "visor"})
ROLE_RANK = {"visor": 0, "operador": 1, "admin": 2}


def _row_to_user(row) -> dict[str, Any]:
    r = dict(row)
    return {
        "id": r["id"],
        "username": r["username"],
        "nombre": r.get("nombre") or r["username"],
        "role": r["role"],
        "activo": bool(r.get("activo", 1)),
        "empresa_id": r.get("empresa_id"),
        "created_at": r.get("created_at"),
    }


def ensure_default_admin() -> None:
    """Crea usuarios admin y rumaul por defecto si no existen."""
    with get_db(empresa_id=0) as conn:
        # Check and insert admin
        admin_exists = conn.execute("SELECT 1 FROM usuarios WHERE username = 'admin'").fetchone()
        if not admin_exists:
            pwd = Config.ADMIN_INITIAL_PASSWORD or Config.master_password() or "admin"
            conn.execute(
                """
                INSERT INTO usuarios (username, nombre, password_hash, role, activo, empresa_id)
                VALUES ('admin', 'Administrador', ?, 'admin', 1, 1)
                """,
                (generate_password_hash(pwd),),
            )
        
        # Check and insert rumaul
        rumaul_exists = conn.execute("SELECT 1 FROM usuarios WHERE username = 'rumaul'").fetchone()
        if not rumaul_exists:
            conn.execute(
                """
                INSERT INTO usuarios (username, nombre, password_hash, role, activo, empresa_id)
                VALUES ('rumaul', 'Rumaul', ?, 'admin', 1, 1)
                """,
                (generate_password_hash("admin"),),
            )



def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    with get_db(empresa_id=0) as conn:
        row = conn.execute(
            "SELECT * FROM usuarios WHERE username = ? AND activo = 1",
            (username.strip().lower(),),
        ).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        return None
    return _row_to_user(row)


def get_user(user_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def list_users() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, nombre, role, activo, created_at FROM usuarios ORDER BY username"
        ).fetchall()
    return [_row_to_user(r) for r in rows]


def create_user(username: str, password: str, role: str = "operador", nombre: str = "", empresa_id: int | None = None) -> int:
    role = (role or "operador").lower()
    if role not in ROLES:
        raise ValueError("Rol inválido")
    username = username.strip().lower()
    if len(username) < 3:
        raise ValueError("Usuario demasiado corto")
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    with get_db(empresa_id=0) as conn:
        exists = conn.execute("SELECT 1 FROM usuarios WHERE username = ?", (username,)).fetchone()
        if exists:
            raise ValueError("El nombre de usuario ya está registrado")
        cur = conn.execute(
            """
            INSERT INTO usuarios (username, nombre, password_hash, role, activo, empresa_id)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (username, nombre or username, generate_password_hash(password), role, empresa_id),
        )
        return int(cur.lastrowid)


def update_user(user_id: int, *, role: str | None = None, activo: bool | None = None, nombre: str | None = None) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT id FROM usuarios WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("Usuario no encontrado")
        if role is not None:
            role = role.lower()
            if role not in ROLES:
                raise ValueError("Rol inválido")
            conn.execute("UPDATE usuarios SET role = ? WHERE id = ?", (role, user_id))
        if activo is not None:
            conn.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (1 if activo else 0, user_id))
        if nombre is not None:
            conn.execute("UPDATE usuarios SET nombre = ? WHERE id = ?", (nombre.strip() or "", user_id))
    user = get_user(user_id)
    if not user:
        raise ValueError("Usuario no encontrado")
    return user


def get_empresa_config() -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT datos FROM empresa_config WHERE id = 1").fetchone()
    if not row or not row["datos"]:
        return {
            "razon_social": "Master Total",
            "cuit": "",
            "direccion": "",
            "telefono": "",
            "email": "",
            "cotizacion_usd": 1000.0,
        }
    try:
        data = json.loads(row["datos"])
        if isinstance(data, dict):
            if "cotizacion_usd" not in data:
                data["cotizacion_usd"] = 1000.0
            return data
        return {}
    except (TypeError, json.JSONDecodeError):
        return {}


def save_empresa_config(datos: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(datos, ensure_ascii=False)
    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM empresa_config WHERE id = 1").fetchone()
        if exists:
            conn.execute(
                "UPDATE empresa_config SET datos = ?, updated_at = datetime('now', 'localtime') WHERE id = 1",
                (payload,),
            )
        else:
            conn.execute(
                "INSERT INTO empresa_config (id, datos) VALUES (1, ?)",
                (payload,),
            )
    return get_empresa_config()
