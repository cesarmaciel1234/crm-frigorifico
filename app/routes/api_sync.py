"""API del motor de sincronización (caché ↔ base de datos ↔ nodos)."""
from flask import jsonify, request, session

from app.routes.api import api_bp
from app.services.sync_hub import (
    MAX_NODOS,
    build_sync_pull_bundle,
    get_sync_nodo,
    list_sync_nodos,
    save_sync_nodo,
)


@api_bp.route("/sync/estado")
def api_sync_estado():
    nodos = list_sync_nodos()
    return jsonify({
        "empresa_id": int(session.get("empresa_id") or 1),
        "username": session.get("username"),
        "max_nodos": MAX_NODOS,
        "nodos": nodos,
        "nodos_activos": len(nodos),
    })


@api_bp.route("/sync/pull")
def api_sync_pull():
    """Motor central → dispositivo (actualiza caché local)."""
    try:
        return jsonify(build_sync_pull_bundle())
    except Exception as e:
        return jsonify({"error": f"Error al sincronizar desde el motor: {str(e)}"}), 500


@api_bp.route("/sync/nodos", methods=["GET"])
def api_sync_nodos_list():
    return jsonify({"nodos": list_sync_nodos(), "max_nodos": MAX_NODOS})


@api_bp.route("/sync/nodo/<device_id>", methods=["GET"])
def api_sync_nodo_get(device_id: str):
    nodo = get_sync_nodo(device_id)
    if not nodo:
        return jsonify({"error": "Nodo no encontrado"}), 404
    return jsonify(nodo)


@api_bp.route("/sync/nodo", methods=["POST"])
def api_sync_nodo_push():
    """Dispositivo → motor central (publica caché como nodo de backup)."""
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    etiqueta = str(data.get("etiqueta") or "").strip()
    snapshot = data.get("snapshot")
    if not device_id:
        return jsonify({"error": "device_id obligatorio"}), 400
    if not isinstance(snapshot, dict):
        return jsonify({"error": "snapshot obligatorio (objeto JSON)"}), 400
    try:
        result = save_sync_nodo(device_id, etiqueta, snapshot)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error al guardar nodo: {str(e)}"}), 500
