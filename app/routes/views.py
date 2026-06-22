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
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username and password:
            user = authenticate_user(username, password)
            if user:
                set_session_user(user)
                return redirect(next_url if next_url.startswith("/") else "/")
            return render_template("login.html", error="Usuario o contraseña incorrectos", next=next_url), 401
        key = (request.form.get("api_key") or password or "").strip()
        if verify_api_key(key):
            set_session_api_key()
            return redirect(next_url if next_url.startswith("/") else "/")
        return render_template("login.html", error="Clave incorrecta", next=next_url), 401
    return render_template("login.html", next=next_url)


@views_bp.route("/auth/login", methods=["POST"])
def auth_login_json():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if username and password:
        user = authenticate_user(username, password)
        if user:
            set_session_user(user)
            return jsonify({"ok": True, "user": user})
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
