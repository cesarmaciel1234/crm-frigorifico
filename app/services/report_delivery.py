"""Render PDF/HTML y envío por email del informe diario."""
from __future__ import annotations

import html
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from typing import Any

from app.config import Config


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _fmt_money(n: float | int | None) -> str:
    try:
        val = float(n or 0)
    except (TypeError, ValueError):
        val = 0.0
    return f"${val:,.0f}".replace(",", ".")


def _pdf_safe(text: Any) -> str:
    s = str(text if text is not None else "")
    return (
        s.replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
        .replace("Á", "A").replace("É", "E").replace("Í", "I")
        .replace("Ó", "O").replace("Ú", "U").replace("Ñ", "N")
    )


def _clientes_rows(clientes: list[dict], limit: int = 40) -> str:
    if not clientes:
        return '<tr><td colspan="5" class="empty">Sin registros</td></tr>'
    rows = []
    for c in clientes[:limit]:
        mora = "SI" if c.get("en_mora") else "No"
        rows.append(
            f"<tr>"
            f"<td>{_esc(c.get('nombre'))}</td>"
            f"<td class='num'>{_esc(c.get('scoring') or '—')}</td>"
            f"<td class='num'>{_fmt_money(c.get('techo_deuda'))}</td>"
            f"<td class='num strong'>{_fmt_money(c.get('saldo_actual'))}</td>"
            f"<td class='num'>{mora}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _obligaciones_rows(items: list[dict], limit: int = 30) -> str:
    if not items:
        return '<tr><td colspan="5" class="empty">Sin obligaciones pendientes</td></tr>'
    rows = []
    for e in items[:limit]:
        saldo = e.get("saldo_pendiente") or e.get("pagar") or 0
        rows.append(
            f"<tr>"
            f"<td>{_esc(e.get('alias') or e.get('tipo') or '—')}</td>"
            f"<td>{_esc((e.get('tipo') or '').upper())}</td>"
            f"<td class='num'>{_fmt_money(e.get('recibido'))}</td>"
            f"<td class='num strong'>{_fmt_money(saldo)}</td>"
            f"<td>{_esc(e.get('fecha_vencimiento') or e.get('plazo_texto') or '—')}</td>"
            f"</tr>"
        )
    return "".join(rows)


def render_daily_report_html(report: dict[str, Any]) -> str:
    emp = report.get("empresa") or {}
    r = report.get("resumen") or {}
    nombre_emp = emp.get("razon_social") or "Empresa"
    fecha = report.get("fecha_legible") or report.get("fecha") or ""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Informe diario — {_esc(nombre_emp)}</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Segoe UI',system-ui,sans-serif; color:#0f172a; font-size:10pt; padding:28px 32px; background:#fff; }}
@media print {{ * {{ -webkit-print-color-adjust:exact!important; print-color-adjust:exact!important; }} }}
.hdr {{ display:flex; justify-content:space-between; gap:20px; border-bottom:3px solid #0d6efd; padding-bottom:18px; margin-bottom:20px; }}
.brand {{ font-size:18pt; font-weight:800; }}
.sub {{ color:#64748b; font-size:9pt; margin-top:4px; }}
.badge {{ display:inline-block; background:#eff6ff; color:#1d4ed8; font-weight:700; font-size:8pt; padding:4px 10px; border-radius:6px; text-transform:uppercase; letter-spacing:.06em; }}
.kpi-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:22px; }}
.kpi {{ border:1px solid #e2e8f0; border-radius:10px; padding:12px 14px; background:#f8fafc; }}
.kpi .lbl {{ font-size:7.5pt; text-transform:uppercase; letter-spacing:.06em; color:#64748b; font-weight:700; }}
.kpi .val {{ font-size:14pt; font-weight:800; margin-top:6px; }}
.kpi .val.green {{ color:#059669; }}
.kpi .val.red {{ color:#dc2626; }}
.kpi .val.blue {{ color:#2563eb; }}
.section {{ margin-bottom:22px; }}
.section h3 {{ font-size:9pt; text-transform:uppercase; letter-spacing:.07em; color:#334155; border-bottom:1px solid #e2e8f0; padding-bottom:6px; margin-bottom:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:9pt; }}
th {{ background:#0f172a; color:#fff; text-align:left; padding:8px 7px; font-size:7.5pt; text-transform:uppercase; }}
td {{ padding:8px 7px; border-bottom:1px solid #e2e8f0; vertical-align:top; }}
tr:nth-child(even) td {{ background:#f8fafc; }}
.num {{ text-align:right; white-space:nowrap; }}
.strong {{ font-weight:700; }}
.empty {{ text-align:center; color:#94a3b8; padding:16px!important; }}
.footer {{ margin-top:24px; padding-top:12px; border-top:1px solid #e2e8f0; font-size:8pt; color:#94a3b8; text-align:center; }}
.alert {{ background:#fef2f2; border:1px solid #fecaca; color:#991b1b; border-radius:8px; padding:10px 12px; margin-bottom:16px; font-size:9pt; }}
</style>
</head>
<body>
<div class="hdr">
  <div>
    <div class="brand">{_esc(nombre_emp)}</div>
    <div class="sub">Informe ejecutivo diario · CRM Frigorífico</div>
  </div>
  <div style="text-align:right">
    <div class="badge">Briefing del jefe</div>
    <div style="font-weight:700;margin-top:8px">{_esc(fecha)}</div>
  </div>
</div>

<div class="kpi-grid">
  <div class="kpi"><div class="lbl">A cobrar (clientes)</div><div class="val green">{_fmt_money(r.get('total_a_cobrar'))}</div><div class="sub">{r.get('clientes_con_saldo', 0)} cuentas</div></div>
  <div class="kpi"><div class="lbl">En mora</div><div class="val red">{_fmt_money(r.get('monto_en_mora'))}</div><div class="sub">{r.get('clientes_en_mora', 0)} clientes</div></div>
  <div class="kpi"><div class="lbl">A pagar (financiero)</div><div class="val red">{_fmt_money(r.get('total_a_pagar_financiero'))}</div><div class="sub">{r.get('obligaciones_activas', 0)} obligaciones</div></div>
  <div class="kpi"><div class="lbl">Vencido a pagar</div><div class="val red">{_fmt_money(r.get('total_a_pagar_vencido'))}</div><div class="sub">{r.get('deudas_urgentes', 0)} urgentes</div></div>
  <div class="kpi"><div class="lbl">Caja real</div><div class="val blue">{_fmt_money(r.get('caja_real'))}</div></div>
  <div class="kpi"><div class="lbl">Patrimonio neto</div><div class="val blue">{_fmt_money(r.get('patrimonio_neto'))}</div></div>
  <div class="kpi"><div class="lbl">Stock (kg)</div><div class="val">{_fmt_money(r.get('stock_kg'))}</div></div>
  <div class="kpi"><div class="lbl">Sangria diaria</div><div class="val">{_fmt_money(r.get('sangria_diaria'))}</div><div class="sub">Salud: {_esc(r.get('estado_salud'))}</div></div>
</div>

{(f'<div class="alert">Atencion: hay {r.get("clientes_en_mora", 0)} clientes en mora por {_fmt_money(r.get("monto_en_mora"))} y {_fmt_money(r.get("total_a_pagar_vencido"))} vencidos a pagar hoy.</div>' if (r.get('clientes_en_mora') or r.get('total_a_pagar_vencido')) else '')}

<div class="section">
  <h3>Clientes a cobrar hoy</h3>
  <table>
    <thead><tr><th>Cliente</th><th>Scoring</th><th>Limite</th><th>Saldo</th><th>Mora</th></tr></thead>
    <tbody>{_clientes_rows(report.get('clientes_a_cobrar') or [])}</tbody>
  </table>
</div>

<div class="section">
  <h3>Obligaciones a pagar (tarjetas, cheques, bancos)</h3>
  <table>
    <thead><tr><th>Concepto</th><th>Tipo</th><th>Recibido</th><th>Saldo</th><th>Vencimiento</th></tr></thead>
    <tbody>{_obligaciones_rows(report.get('obligaciones_a_pagar') or [])}</tbody>
  </table>
</div>

<div class="footer">Generado automaticamente · { _esc(report.get('generado_at', '')[:19].replace('T', ' ')) } UTC</div>
<script>window.onload=function(){{/* listo para imprimir / guardar PDF */}};</script>
</body>
</html>"""


def render_daily_report_pdf(report: dict[str, Any]) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError as e:
        raise RuntimeError("fpdf2 no instalado") from e

    emp = report.get("empresa") or {}
    r = report.get("resumen") or {}
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _pdf_safe(emp.get("razon_social") or "Informe diario"), ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _pdf_safe(f"Informe ejecutivo · {report.get('fecha_legible', '')}"), ln=True)
    pdf.ln(4)

    kpis = [
        ("A cobrar", _fmt_money(r.get("total_a_cobrar")), f"{r.get('clientes_con_saldo', 0)} clientes"),
        ("En mora", _fmt_money(r.get("monto_en_mora")), f"{r.get('clientes_en_mora', 0)} clientes"),
        ("A pagar fin.", _fmt_money(r.get("total_a_pagar_financiero")), f"{r.get('obligaciones_activas', 0)} ops"),
        ("Vencido pagar", _fmt_money(r.get("total_a_pagar_vencido")), ""),
        ("Caja real", _fmt_money(r.get("caja_real")), ""),
        ("Patrimonio", _fmt_money(r.get("patrimonio_neto")), f"Salud: {r.get('estado_salud', '')}"),
    ]
    pdf.set_font("Helvetica", "B", 9)
    for label, val, sub in kpis:
        pdf.cell(45, 6, _pdf_safe(label), border=0)
        pdf.cell(40, 6, _pdf_safe(val), border=0)
        pdf.cell(0, 6, _pdf_safe(sub), ln=True)
    pdf.ln(4)

    def table_section(title: str, headers: list[str], rows: list[list[str]], widths: list[int]):
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, _pdf_safe(title), ln=True)
        pdf.set_font("Helvetica", "B", 8)
        for i, h in enumerate(headers):
            pdf.cell(widths[i], 6, _pdf_safe(h), border=1)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        if not rows:
            pdf.cell(sum(widths), 6, "Sin registros", border=1, ln=True)
            return
        for row in rows[:35]:
            for i, cell in enumerate(row):
                pdf.cell(widths[i], 6, _pdf_safe(cell)[:42], border=1)
            pdf.ln()
        pdf.ln(3)

    cli_rows = [
        [
            str(c.get("nombre") or ""),
            str(c.get("scoring") or ""),
            _fmt_money(c.get("saldo_actual")),
            "SI" if c.get("en_mora") else "No",
        ]
        for c in (report.get("clientes_a_cobrar") or [])
    ]
    table_section(
        "Clientes a cobrar",
        ["Cliente", "Scor.", "Saldo", "Mora"],
        cli_rows,
        [75, 15, 35, 20],
    )

    pag_rows = [
        [
            str(e.get("alias") or e.get("tipo") or ""),
            str((e.get("tipo") or "").upper()),
            _fmt_money(e.get("saldo_pendiente") or e.get("pagar")),
            str(e.get("fecha_vencimiento") or "—"),
        ]
        for e in (report.get("obligaciones_a_pagar") or [])
    ]
    table_section(
        "Obligaciones a pagar",
        ["Concepto", "Tipo", "Saldo", "Vto."],
        pag_rows,
        [65, 25, 35, 30],
    )

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def smtp_configured() -> bool:
    return bool(Config.SMTP_HOST and Config.SMTP_USER and Config.SMTP_PASSWORD and Config.SMTP_FROM)


def parse_recipients(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,;\s]+", raw.strip())
    return [p for p in parts if p and "@" in p]


def get_report_recipients(empresa: dict[str, Any] | None = None) -> list[str]:
    empresa = empresa or {}
    raw = (
        str(empresa.get("reporte_email_destinatarios") or "").strip()
        or str(Config.REPORT_EMAIL_TO or "").strip()
    )
    return parse_recipients(raw)


def send_daily_report_email(report: dict[str, Any], recipients: list[str] | None = None) -> dict[str, Any]:
    if not smtp_configured():
        return {"ok": False, "error": "SMTP no configurado en el servidor (SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)"}

    dest = recipients or get_report_recipients(report.get("empresa"))
    if not dest:
        return {"ok": False, "error": "No hay destinatarios configurados para el informe diario"}

    emp = report.get("empresa") or {}
    nombre = emp.get("razon_social") or "Empresa"
    fecha = report.get("fecha_legible") or report.get("fecha") or ""
    subject = f"Informe diario {nombre} — {fecha}"

    html_body = render_daily_report_html(report)
    pdf_bytes = render_daily_report_pdf(report)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = Config.SMTP_FROM
    msg["To"] = ", ".join(dest)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(f"Informe ejecutivo diario de {nombre} ({fecha}). PDF adjunto.", "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=f"informe-diario-{fecha.replace('/', '-')}.pdf")
    msg.attach(attachment)

    with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=30) as server:
        if Config.SMTP_USE_TLS:
            server.starttls()
        server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        server.sendmail(Config.SMTP_FROM, dest, msg.as_string())

    return {"ok": True, "sent_to": dest, "subject": subject}


def whatsapp_summary_text(report: dict[str, Any]) -> str:
    r = report.get("resumen") or {}
    emp = (report.get("empresa") or {}).get("razon_social") or "Empresa"
    fecha = report.get("fecha_legible") or ""
    lines = [
        f"📊 *Informe diario — {emp}*",
        f"📅 {fecha}",
        "",
        f"💰 *A cobrar:* {_fmt_money(r.get('total_a_cobrar'))} ({r.get('clientes_con_saldo', 0)} clientes)",
        f"🚨 *En mora:* {_fmt_money(r.get('monto_en_mora'))} ({r.get('clientes_en_mora', 0)} clientes)",
        f"💳 *A pagar:* {_fmt_money(r.get('total_a_pagar_financiero'))}",
        f"⏰ *Vencido:* {_fmt_money(r.get('total_a_pagar_vencido'))}",
        f"🏦 *Caja real:* {_fmt_money(r.get('caja_real'))}",
        f"📈 *Patrimonio:* {_fmt_money(r.get('patrimonio_neto'))}",
        f"Salud: {r.get('estado_salud', '—')}",
        "",
        "PDF completo adjunto en el informe del panel del jefe.",
    ]
    return "\n".join(lines)
