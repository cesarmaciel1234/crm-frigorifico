from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, session
import os

from app.config import Config
from app.security import verify_api_key

views_bp = Blueprint('views', __name__)


@views_bp.route("/health")
def health():
    return jsonify({"status": "ok", "service": "master-total"})


@views_bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.args.get("next") or "/"
    if request.method == "POST":
        key = (request.form.get("api_key") or "").strip()
        if verify_api_key(key):
            session.permanent = True
            session["authenticated"] = True
            return redirect(next_url if next_url.startswith("/") else "/")
        return render_template("login.html", error="Clave incorrecta", next=next_url), 401
    return render_template("login.html", next=next_url)


@views_bp.route("/auth/login", methods=["POST"])
def auth_login_json():
    data = request.get_json(silent=True) or {}
    key = (data.get("api_key") or request.form.get("api_key") or "").strip()
    if verify_api_key(key):
        session.permanent = True
        session["authenticated"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "Clave incorrecta"}), 401


@views_bp.route("/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@views_bp.route("/")
def index():
    return render_template("terminal.html")


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
