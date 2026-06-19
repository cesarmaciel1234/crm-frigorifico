from flask import Blueprint, render_template, send_from_directory, current_app
import os

views_bp = Blueprint('views', __name__)

@views_bp.route("/")
def index():
    return render_template("terminal.html")

@views_bp.route("/manifest.json")
def serve_manifest():
    return send_from_directory(os.path.join(current_app.root_path, 'static'), 'manifest.json', mimetype='application/json')

@views_bp.route("/sw.js")
def serve_sw():
    response = send_from_directory(os.path.join(current_app.root_path, 'static'), 'sw.js', mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response
