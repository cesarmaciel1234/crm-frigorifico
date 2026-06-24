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
    return jsonify({
        "smtp_configurado": smtp_configured(emp),
        "transport": settings.get("transport") or "",
        "on_render": Config.ON_RENDER,
        "destinatarios": emp.get("reporte_email_destinatarios") or "",
        "activo": bool(emp.get("reporte_email_activo")),
        "hora": emp.get("reporte_email_hora") or "07:00",
        "smtp_host": emp.get("smtp_host") or Config.SMTP_HOST or "smtp.gmail.com",
        "smtp_port": emp.get("smtp_port") or Config.SMTP_PORT or 465,
        "smtp_user": emp.get("smtp_user") or Config.SMTP_USER or "",
        "smtp_from": emp.get("smtp_from") or Config.SMTP_FROM or "",
        "smtp_password_set": bool(emp.get("smtp_password") or Config.SMTP_PASSWORD),
        "resend_configured": bool(Config.RESEND_API_KEY or emp.get("resend_api_key")),
    })


@api_bp.route("/reportes/email/config", methods=["PUT"])
def api_reporte_email_config_put():
    guard = _admin_only()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    emp = get_empresa_config()
    emp["reporte_email_destinatarios"] = str(data.get("destinatarios") or "").strip()
    emp["reporte_email_activo"] = bool(data.get("activo"))
    hora = str(data.get("hora") or "07:00").strip()
    emp["reporte_email_hora"] = hora if hora else "07:00"
    if "smtp_host" in data:
        emp["smtp_host"] = str(data.get("smtp_host") or "smtp.gmail.com").strip()
    if "smtp_port" in data:
        try:
            emp["smtp_port"] = int(data.get("smtp_port") or 465)
        except (TypeError, ValueError):
            emp["smtp_port"] = 465
    smtp_user = str(data.get("smtp_user") or "").strip()
    if smtp_user:
        emp["smtp_user"] = smtp_user
        emp["smtp_from"] = str(data.get("smtp_from") or smtp_user).strip()
    elif str(data.get("smtp_from") or "").strip():
        emp["smtp_from"] = str(data.get("smtp_from")).strip()
    if "smtp_use_ssl" in data:
        emp["smtp_use_ssl"] = bool(data.get("smtp_use_ssl", True))
    pwd = str(data.get("smtp_password") or "").strip()
    if pwd:
        emp["smtp_password"] = pwd.replace(" ", "")
    resend = str(data.get("resend_api_key") or "").strip()
    if resend:
        emp["resend_api_key"] = resend
    save_empresa_config(emp)
    return jsonify({
        "ok": True,
        "config": {
            "destinatarios": emp["reporte_email_destinatarios"],
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
def api_reporte_cron_diario():
    """Endpoint para cron externo (Render cron, etc.). Requiere X-Report-Cron-Secret."""
    secret = request.headers.get("X-Report-Cron-Secret", "")
    expected = Config.REPORT_CRON_SECRET or ""
    if not expected or secret != expected:
        return jsonify({"error": "No autorizado"}), 403

    emp = get_empresa_config()
    if not emp.get("reporte_email_activo"):
        return jsonify({"ok": True, "skipped": True, "reason": "reporte_email_activo desactivado"})

    if not smtp_configured(emp):
        return jsonify({"ok": False, "error": "Email no configurado"}), 503

    recipients = get_report_recipients(emp)
    if not recipients:
        return jsonify({"ok": False, "error": "Sin destinatarios"}), 400

    try:
        report = build_daily_report(include_details=False)
        result = send_daily_report_email(report, recipients=recipients)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
