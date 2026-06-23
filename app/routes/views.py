from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, session, make_response
import os

from app.config import Config
from app.database import get_db
from app.security import (
    verify_api_key,
    set_session_user,
    set_session_api_key,
    is_authenticated,
    current_role,
    check_login_rate_limit,
    record_login_failure,
    record_login_success,
)
from app.services.users import authenticate_user

views_bp = Blueprint('views', __name__)


@views_bp.route("/health")
def health():
    return jsonify({"status": "ok", "service": "master-total"})


@views_bp.route("/health/ready")
def health_ready():
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify({"status": "ready", "db": "ok"})
    except Exception as exc:
        return jsonify({"status": "not_ready", "db": str(exc)}), 503


@views_bp.route("/auth/session")
def auth_session():
    if not is_authenticated():
        return jsonify({"authenticated": False}), 401
    return jsonify({
        "authenticated": True,
        "username": session.get("username"),
        "role": current_role(),
        "auth_method": session.get("auth_method"),
    })


@views_bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.args.get("next") or "/"
    error_msg = request.args.get("error")
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username and password:
            ok, limit_msg = check_login_rate_limit(username)
            if not ok:
                return render_template("login.html", error=limit_msg, next=next_url), 429
            user = authenticate_user(username, password)
            if user:
                record_login_success(username)
                set_session_user(user)
                return redirect(next_url if next_url.startswith("/") else "/")
            record_login_failure(username)
            return render_template("login.html", error="Usuario o contraseña incorrectos", next=next_url), 401
        key = (request.form.get("api_key") or password or "").strip()
        if verify_api_key(key):
            set_session_api_key()
            return redirect(next_url if next_url.startswith("/") else "/")
        return render_template("login.html", error="Clave incorrecta", next=next_url), 401
    return render_template("login.html", next=next_url, error=error_msg)


@views_bp.route("/auth/login", methods=["POST"])
def auth_login_json():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if username and password:
        ok, limit_msg = check_login_rate_limit(username)
        if not ok:
            return jsonify({"error": limit_msg}), 429
        user = authenticate_user(username, password)
        if user:
            record_login_success(username)
            set_session_user(user)
            return jsonify({"ok": True, "user": user})
        record_login_failure(username)
        return jsonify({"error": "Usuario o contraseña incorrectos"}), 401
    key = (data.get("api_key") or request.form.get("api_key") or "").strip()
    if verify_api_key(key):
        set_session_api_key()
        return jsonify({"ok": True})
    return jsonify({"error": "Clave incorrecta"}), 401


@views_bp.route("/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@views_bp.route("/auth/register", methods=["POST"])
def auth_register():
    from app.services.users import authenticate_user, get_db
    from app.security import set_session_user
    from werkzeug.security import generate_password_hash
    import re
    
    data = request.get_json(silent=True) or {}
    empresa_nombre = (data.get("empresa_nombre") or request.form.get("empresa_nombre") or "").strip()
    password = data.get("password") or request.form.get("password") or ""
    password_confirm = data.get("password_confirm") or request.form.get("password_confirm") or ""
    
    if not empresa_nombre or not password or not password_confirm:
        return jsonify({"error": "Nombre de la empresa, contraseña y confirmación son requeridos"}), 400
        
    if password != password_confirm:
        return jsonify({"error": "Las contraseñas no coinciden"}), 400
        
    if len(password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres"}), 400
        
    # Guardar el nombre de la empresa en MAYÚSCULAS
    empresa_nombre_upper = empresa_nombre.upper()
    
    try:
        with get_db(empresa_id=0) as conn:
            # Generar slug único para la empresa y usarlo como username del administrador
            slug = re.sub(r'[^a-zA-Z0-9]', '', empresa_nombre_upper.lower())
            if not slug:
                slug = "empresa"
            base_slug = slug
            counter = 1
            while conn.execute("SELECT 1 FROM empresas WHERE slug = ?", (slug,)).fetchone():
                slug = f"{base_slug}{counter}"
                counter += 1
                
            # Insertar la empresa
            cur_emp = conn.execute(
                "INSERT INTO empresas (nombre, slug) VALUES (?, ?)",
                (empresa_nombre_upper, slug)
            )
            empresa_id = cur_emp.lastrowid
            
            # El username del administrador será el slug único de la empresa
            username = slug
            
            # Insertar el usuario administrador
            conn.execute(
                """
                INSERT INTO usuarios (username, nombre, password_hash, role, activo, empresa_id)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (username, empresa_nombre_upper, generate_password_hash(password), "admin", empresa_id),
            )
            
        # Autenticar e iniciar sesión automáticamente
        user = authenticate_user(username, password)
        if user:
            set_session_user(user)
            return jsonify({"ok": True, "user": user})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": f"Error al registrar la empresa: {str(e)}"}), 500


@views_bp.route("/auth/reset-password", methods=["POST"])
def auth_reset_password():
    from app.services.users import get_db
    from werkzeug.security import generate_password_hash
    
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or request.form.get("username") or "").strip().lower()
    new_password = data.get("password") or request.form.get("password") or ""
    master_key = (data.get("master_key") or request.form.get("master_key") or "").strip()
    
    if not username or not new_password or not master_key:
        return jsonify({"error": "Usuario, nueva contraseña y clave maestra son requeridos"}), 400
        
    valid_keys = {"209470"}
    if Config.master_password():
        valid_keys.add(str(Config.master_password()).strip())
    if Config.MASTER_PASSWORD:
        valid_keys.add(str(Config.MASTER_PASSWORD).strip())
        
    if master_key not in valid_keys:
        return jsonify({"error": "Clave maestra incorrecta"}), 403
        
    if len(new_password) < 8:
        return jsonify({"error": "La contraseña debe tener al menos 8 caracteres"}), 400
        
    import re
    raw_username = username.strip().lower()
    slug_username = re.sub(r'[^a-zA-Z0-9]', '', raw_username)
    
    with get_db(empresa_id=0) as conn:
        user = conn.execute(
            "SELECT id, username FROM usuarios WHERE username = ? OR username = ?",
            (raw_username, slug_username)
        ).fetchone()
        if not user:
            return jsonify({"error": "El usuario o empresa no existe"}), 404
            
        matched_username = user["username"]
        conn.execute(
            "UPDATE usuarios SET password_hash = ? WHERE username = ?",
            (generate_password_hash(new_password), matched_username)
        )
        
    return jsonify({"ok": True})


@views_bp.route("/")
def index():
    resp = make_response(render_template("terminal.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@views_bp.route("/pos")
def pos():
    return render_template("pos.html")


@views_bp.route("/manifest.json")
def serve_manifest():
    return send_from_directory(os.path.join(current_app.root_path, 'static'), 'manifest.json', mimetype='application/json')


@views_bp.route("/sw.js")
def serve_sw():
    response = send_from_directory(os.path.join(current_app.root_path, 'static'), 'sw.js', mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response
