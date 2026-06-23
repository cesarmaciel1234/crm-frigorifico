"""API del motor de sincronización (caché ↔ base de datos ↔ nodos)."""
from flask import jsonify, request, session

from app.routes.api import api_bp
from app.services.sync_hub import (
    MAX_NODOS,
    apply_sync_operations,
    build_sync_pull_delta,
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
    """Motor central → dispositivo (delta changelog + snapshot inicial)."""
    since = request.args.get("since", 0, type=int)
    include_full = request.args.get("full", 0, type=int) == 1
    try:
        return jsonify(build_sync_pull_delta(since, include_full=include_full))
    except Exception as e:
        return jsonify({"error": f"Error al sincronizar desde el motor: {str(e)}"}), 500


@api_bp.route("/sync/push", methods=["POST"])
def api_sync_push():
    """Dispositivo → motor central (outbox con LWW por UUID)."""
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    operations = data.get("operations") or []
    if not device_id:
        return jsonify({"error": "device_id obligatorio"}), 400
    if not isinstance(operations, list):
        return jsonify({"error": "operations debe ser una lista"}), 400
    try:
        result = apply_sync_operations(device_id, operations)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Error al aplicar operaciones: {str(e)}"}), 500


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
