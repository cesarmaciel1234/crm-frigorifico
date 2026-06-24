"""API del informe diario ejecutivo (PDF, WhatsApp, email)."""
from flask import Response, jsonify, request

from app.config import Config
from app.routes.api import api_bp
from app.security import role_at_least
from app.services.daily_report import build_daily_report
from app.services.report_delivery import (
    get_report_recipients,
    render_daily_report_html,
    render_daily_report_pdf,
    send_daily_report_email,
    smtp_configured,
    weekly_report_skip_reason,
    whatsapp_summary_text,
    _resolve_email_settings,
)
from app.services.users import get_empresa_config, save_empresa_config


def _admin_only():
    if not role_at_least("admin"):
        return jsonify({"error": "Solo administradores pueden gestionar informes"}), 403
    return None


@api_bp.route("/reportes/diario")
def api_reporte_diario_json():
    try:
        return jsonify(build_daily_report())
    except Exception as e:
        return jsonify({"error": f"Error al generar informe: {str(e)}"}), 500


@api_bp.route("/reportes/diario/html")
def api_reporte_diario_html():
    try:
        report = build_daily_report(include_details=False)
        html_doc = render_daily_report_html(report)
        return Response(html_doc, mimetype="text/html; charset=utf-8")
    except Exception as e:
        return jsonify({"error": f"Error al generar HTML: {str(e)}"}), 500


@api_bp.route("/reportes/diario/pdf")
def api_reporte_diario_pdf():
    try:
        report = build_daily_report(include_details=False)
        pdf = render_daily_report_pdf(report)
        fecha = (report.get("fecha") or "hoy").replace("-", "")
        return Response(
            pdf,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="informe-empresarial-{fecha}.pdf"',
            },
        )
    except Exception as e:
        return jsonify({"error": f"Error al generar PDF: {str(e)}"}), 500


@api_bp.route("/reportes/diario/whatsapp")
def api_reporte_diario_whatsapp():
    try:
        report = build_daily_report(include_details=False)
        return jsonify({"texto": whatsapp_summary_text(report), "reporte": report.get("resumen")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/reportes/email/config")
def api_reporte_email_config_get():
    guard = _admin_only()
    if guard:
        return guard
    emp = get_empresa_config()
    settings = _resolve_email_settings(emp)
    recipients = get_report_recipients(emp)
    return jsonify({
        "smtp_configurado": smtp_configured(emp),
        "email_configurado": smtp_configured(emp),
        "transport": settings.get("transport") or "",
        "on_render": Config.ON_RENDER,
        "email_perfil": emp.get("email") or "",
        "destinatarios": ", ".join(recipients),
        "destinatarios_efectivos": recipients,
        "activo": bool(emp.get("reporte_email_activo", True)),
        "hora": emp.get("reporte_email_hora") or "05:00",
        "programacion": "lunes 05:00 (Argentina)",
        "resend_from": emp.get("smtp_from") or Config.SMTP_FROM or "",
        "resend_configured": bool(Config.RESEND_API_KEY or emp.get("resend_api_key")),
    })


@api_bp.route("/reportes/email/config", methods=["PUT"])
def api_reporte_email_config_put():
    guard = _admin_only()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    emp = get_empresa_config()
    emp["reporte_email_activo"] = bool(data.get("activo", True))
    emp["reporte_email_hora"] = "05:00"
    resend = str(data.get("resend_api_key") or "").strip()
    if resend:
        emp["resend_api_key"] = resend
    resend_from = str(data.get("resend_from") or data.get("smtp_from") or "").strip()
    if resend_from:
        emp["smtp_from"] = resend_from
    save_empresa_config(emp)
    recipients = get_report_recipients(emp)
    return jsonify({
        "ok": True,
        "config": {
            "destinatarios": ", ".join(recipients),
            "activo": emp["reporte_email_activo"],
            "hora": emp["reporte_email_hora"],
            "smtp_configurado": smtp_configured(emp),
            "transport": _resolve_email_settings(emp).get("transport") or "",
        },
    })


@api_bp.route("/reportes/email/enviar", methods=["POST"])
def api_reporte_email_enviar():
    guard = _admin_only()
    if guard:
        return guard
    try:
        report = build_daily_report(include_details=False)
        data = request.get_json(silent=True) or {}
        extra = str(data.get("destinatarios") or "").strip()
        recipients = None
        if extra:
            from app.services.report_delivery import parse_recipients
            recipients = parse_recipients(extra)
        result = send_daily_report_email(report, recipients=recipients)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/reportes/cron/diario", methods=["POST"])
@api_bp.route("/reportes/cron/semanal", methods=["POST"])
def api_reporte_cron_semanal():
    """Cron externo (Render): informe empresarial los lunes a las 05:00 Argentina."""
    secret = request.headers.get("X-Report-Cron-Secret", "")
    expected = Config.REPORT_CRON_SECRET or ""
    if not expected or secret != expected:
        return jsonify({"error": "No autorizado"}), 403

    emp = get_empresa_config()
    if not emp.get("reporte_email_activo", True):
        return jsonify({"ok": True, "skipped": True, "reason": "reporte_email_activo desactivado"})

    skip = weekly_report_skip_reason(emp)
    if skip:
        return jsonify({"ok": True, "skipped": True, "reason": skip})

    if not smtp_configured(emp):
        return jsonify({"ok": False, "error": "Email no configurado (Resend API key)"}), 503

    recipients = get_report_recipients(emp)
    if not recipients:
        return jsonify({
            "ok": False,
            "error": "Sin email en el perfil de la empresa. Completá Email en Datos de la Empresa.",
        }), 400

    try:
        report = build_daily_report(include_details=False)
        result = send_daily_report_email(report, recipients=recipients)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
