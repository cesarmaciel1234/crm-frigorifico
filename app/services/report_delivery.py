"""Render PDF/HTML y envío por email del informe diario."""
from __future__ import annotations

import base64
import html
import json
import re
import smtplib
import socket
import ssl
import urllib.error
import urllib.request
from email.header import Header
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


def _fmt_kg(n: float | int | None) -> str:
    try:
        val = float(n or 0)
    except (TypeError, ValueError):
        val = 0.0
    return f"{val:,.0f} kg".replace(",", ".")


def _scoring_badge(scoring: Any) -> str:
    s = str(scoring or "—").upper()
    cls = {"A": "sc-a", "B": "sc-b", "C": "sc-c", "D": "sc-d"}.get(s, "sc-x")
    return f'<span class="badge-sc {cls}">{_esc(s)}</span>'


def _mora_badge(en_mora: bool) -> str:
    if en_mora:
        return '<span class="badge-pill pill-danger">En mora</span>'
    return '<span class="badge-pill pill-ok">Al día</span>'


def _tipo_badge(tipo: Any) -> str:
    t = str(tipo or "").lower()
    cls = {
        "tarjeta": "tipo-tarjeta",
        "cheque": "tipo-cheque",
        "banco": "tipo-banco",
        "otro": "tipo-otro",
    }.get(t, "tipo-otro")
    label = _esc(str(tipo or "—").upper())
    return f'<span class="badge-tipo {cls}">{label}</span>'


def _salud_class(estado: Any) -> str:
    e = str(estado or "").lower()
    if any(x in e for x in ("excelente", "buena", "ok", "sano")):
        return "salud-good"
    if any(x in e for x in ("alerta", "cuidado", "regular")):
        return "salud-warn"
    if any(x in e for x in ("critico", "crítico", "rojo", "grave")):
        return "salud-bad"
    return "salud-neutral"


def _clientes_rows(clientes: list[dict], limit: int = 40) -> str:
    if not clientes:
        return '<tr><td colspan="5" class="empty">Sin registros</td></tr>'
    rows = []
    for c in clientes[:limit]:
        mora_cls = " row-mora" if c.get("en_mora") else ""
        rows.append(
            f"<tr class{mora_cls}>"
            f"<td><span class='cliente-nombre'>{_esc(c.get('nombre'))}</span></td>"
            f"<td class='num'>{_scoring_badge(c.get('scoring'))}</td>"
            f"<td class='num muted'>{_fmt_money(c.get('techo_deuda'))}</td>"
            f"<td class='num strong'>{_fmt_money(c.get('saldo_actual'))}</td>"
            f"<td class='num'>{_mora_badge(bool(c.get('en_mora')))}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _obligaciones_rows(items: list[dict], limit: int = 30) -> str:
    if not items:
        return '<tr><td colspan="5" class="empty">Sin obligaciones pendientes</td></tr>'
    rows = []
    for e in items[:limit]:
        saldo = e.get("saldo_pendiente") or e.get("pagar") or 0
        urgente = e.get("urgente")
        row_cls = " row-urgente" if urgente else ""
        rows.append(
            f"<tr class{row_cls}>"
            f"<td><span class='concepto'>{_esc(e.get('alias') or e.get('tipo') or '—')}</span></td>"
            f"<td>{_tipo_badge(e.get('tipo'))}</td>"
            f"<td class='num muted'>{_fmt_money(e.get('recibido'))}</td>"
            f"<td class='num strong'>{_fmt_money(saldo)}</td>"
            f"<td>{_esc(e.get('fecha_vencimiento') or e.get('plazo_texto') or '—')}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _kpi_card(label: str, value: str, sub: str, tone: str = "", icon: str = "") -> str:
    tone_cls = f" kpi-{tone}" if tone else ""
    val_cls = f" val-{tone}" if tone in ("green", "red", "blue", "amber") else ""
    return (
        f'<div class="kpi{tone_cls}">'
        f'<div class="kpi-top"><span class="kpi-icon">{icon}</span><span class="lbl">{label}</span></div>'
        f'<div class="val{val_cls}">{value}</div>'
        f'<div class="sub-text">{sub}</div>'
        f"</div>"
    )


def render_daily_report_html(report: dict[str, Any]) -> str:
    emp = report.get("empresa") or {}
    r = report.get("resumen") or {}
    nombre_emp = emp.get("razon_social") or "Empresa"
    fecha = report.get("fecha_legible") or report.get("fecha") or ""
    salud = _esc(r.get("estado_salud"))
    salud_cls = _salud_class(r.get("estado_salud"))
    cuit = _esc(emp.get("cuit") or "")

    alert_html = ""
    if r.get("clientes_en_mora") or r.get("total_a_pagar_vencido"):
        alert_html = (
            f'<div class="alert">'
            f'<div class="alert-icon">!</div>'
            f'<div><strong>Atención requerida</strong>'
            f'<p>{r.get("clientes_en_mora", 0)} clientes en mora '
            f'({_fmt_money(r.get("monto_en_mora"))}) · '
            f'{_fmt_money(r.get("total_a_pagar_vencido"))} vencidos a pagar hoy.</p></div>'
            f"</div>"
        )

    cobranza = (
        _kpi_card("A cobrar", _fmt_money(r.get("total_a_cobrar")), f"{r.get('clientes_con_saldo', 0)} cuentas con saldo", "green", "↗")
        + _kpi_card("En mora", _fmt_money(r.get("monto_en_mora")), f"{r.get('clientes_en_mora', 0)} clientes atrasados", "red", "⚠")
        + _kpi_card("Incobrables", str(r.get("clientes_inrecuperables", 0)), "Clientes marcados", "amber", "✕")
    )
    pagos = (
        _kpi_card("A pagar", _fmt_money(r.get("total_a_pagar_financiero")), f"{r.get('obligaciones_activas', 0)} obligaciones", "red", "↘")
        + _kpi_card("Vencido hoy", _fmt_money(r.get("total_a_pagar_vencido")), f"{r.get('deudas_urgentes', 0)} urgentes", "red", "⏱")
        + _kpi_card("Deuda comercial", _fmt_money(r.get("total_a_pagar_comercial")), "Proveedores / operativo", "amber", "◎")
    )
    posicion = (
        _kpi_card("Caja real", _fmt_money(r.get("caja_real")), "Efectivo disponible", "blue", "◆")
        + _kpi_card("Patrimonio neto", _fmt_money(r.get("patrimonio_neto")), f"Salud: {salud}", "blue", "★")
        + _kpi_card("Stock físico", _fmt_kg(r.get("stock_kg")), "Inventario en kg", "", "▣")
        + _kpi_card("Sangría diaria", _fmt_money(r.get("sangria_diaria")), "Costo financiero estimado", "", "∿")
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Informe diario — {_esc(nombre_emp)}</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{
  font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
  color:#0f172a; font-size:10pt; line-height:1.55;
  background:linear-gradient(160deg,#eef2ff 0%,#f8fafc 45%,#f1f5f9 100%);
  min-height:100vh; padding:32px 20px;
}}
@media print {{
  body {{ background:#fff; padding:0; }}
  * {{ -webkit-print-color-adjust:exact!important; print-color-adjust:exact!important; }}
  .hero, .balance-strip, .kpi, .alert {{ break-inside:avoid; }}
  .container {{ box-shadow:none!important; border:none!important; }}
}}
.container {{
  max-width:920px; margin:0 auto; background:#fff;
  border-radius:20px; overflow:hidden;
  box-shadow:0 20px 50px rgba(15,23,42,.08), 0 1px 0 rgba(15,23,42,.04);
  border:1px solid rgba(226,232,240,.8);
}}
.hero {{
  background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 55%,#1d4ed8 100%);
  color:#fff; padding:36px 40px 32px;
  display:flex; justify-content:space-between; align-items:flex-start; gap:24px;
}}
.hero-left {{ flex:1; }}
.hero-tag {{
  display:inline-flex; align-items:center; gap:6px;
  font-size:7.5pt; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
  background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.2);
  padding:5px 12px; border-radius:999px; margin-bottom:14px;
}}
.brand {{ font-size:24pt; font-weight:800; letter-spacing:-.03em; line-height:1.15; }}
.sub {{ color:rgba(255,255,255,.72); font-size:9.5pt; margin-top:8px; font-weight:500; }}
.hero-right {{ text-align:right; }}
.fecha-box {{
  background:rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.18);
  border-radius:14px; padding:14px 18px; min-width:140px;
}}
.fecha-label {{ font-size:7pt; text-transform:uppercase; letter-spacing:.1em; opacity:.7; font-weight:600; }}
.fecha-val {{ font-size:14pt; font-weight:800; margin-top:4px; }}
.salud-pill {{
  display:inline-block; margin-top:12px; padding:6px 14px; border-radius:999px;
  font-size:8pt; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
}}
.salud-good {{ background:#dcfce7; color:#166534; }}
.salud-warn {{ background:#fef3c7; color:#92400e; }}
.salud-bad {{ background:#fee2e2; color:#991b1b; }}
.salud-neutral {{ background:#e2e8f0; color:#475569; }}
.body-pad {{ padding:32px 40px 40px; }}
.balance-strip {{
  display:grid; grid-template-columns:repeat(3,1fr); gap:12px;
  margin-bottom:28px; padding:16px 20px;
  background:linear-gradient(90deg,#f8fafc,#f1f5f9);
  border:1px solid #e2e8f0; border-radius:14px;
}}
.balance-item {{ text-align:center; }}
.balance-item .bl {{ font-size:7pt; text-transform:uppercase; letter-spacing:.08em; color:#64748b; font-weight:700; }}
.balance-item .bv {{ font-size:13pt; font-weight:800; color:#0f172a; margin-top:4px; font-variant-numeric:tabular-nums; }}
.balance-item .bv.pos {{ color:#059669; }}
.balance-item .bv.neg {{ color:#dc2626; }}
.kpi-group {{ margin-bottom:28px; }}
.group-head {{
  display:flex; align-items:center; gap:10px; margin-bottom:14px;
  font-size:8pt; font-weight:800; text-transform:uppercase; letter-spacing:.1em; color:#64748b;
}}
.group-head::after {{ content:''; flex:1; height:1px; background:#e2e8f0; }}
.kpi-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.kpi-grid-4 {{ grid-template-columns:repeat(4,1fr); }}
.kpi {{
  border:1px solid #e2e8f0; border-radius:14px; padding:16px 18px;
  background:#fff; position:relative; overflow:hidden;
  transition:box-shadow .15s;
}}
.kpi::after {{
  content:''; position:absolute; top:0; left:0; right:0; height:3px;
  background:#cbd5e1;
}}
.kpi-green::after {{ background:linear-gradient(90deg,#10b981,#34d399); }}
.kpi-red::after {{ background:linear-gradient(90deg,#ef4444,#f87171); }}
.kpi-blue::after {{ background:linear-gradient(90deg,#2563eb,#60a5fa); }}
.kpi-amber::after {{ background:linear-gradient(90deg,#f59e0b,#fbbf24); }}
.kpi-top {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; }}
.kpi-icon {{ font-size:11pt; opacity:.85; line-height:1; }}
.lbl {{ font-size:7.5pt; text-transform:uppercase; letter-spacing:.06em; color:#64748b; font-weight:700; }}
.val {{ font-size:17pt; font-weight:800; letter-spacing:-.02em; color:#0f172a; font-variant-numeric:tabular-nums; }}
.val-green {{ color:#059669; }}
.val-red {{ color:#dc2626; }}
.val-blue {{ color:#2563eb; }}
.val-amber {{ color:#d97706; }}
.sub-text {{ font-size:8pt; color:#94a3b8; font-weight:500; margin-top:6px; }}
.alert {{
  display:flex; gap:14px; align-items:flex-start;
  background:linear-gradient(90deg,#fef2f2,#fff7ed);
  border:1px solid #fecaca; border-left:4px solid #ef4444;
  border-radius:12px; padding:16px 18px; margin-bottom:28px;
}}
.alert-icon {{
  width:28px; height:28px; border-radius:50%; background:#ef4444; color:#fff;
  font-weight:800; font-size:14pt; display:flex; align-items:center; justify-content:center; flex-shrink:0;
}}
.alert strong {{ display:block; color:#991b1b; font-size:10pt; margin-bottom:4px; }}
.alert p {{ color:#7f1d1d; font-size:9pt; margin:0; }}
.section {{ margin-bottom:32px; }}
.section-title {{
  font-size:11pt; font-weight:800; color:#0f172a; margin-bottom:14px;
  display:flex; align-items:center; gap:10px;
}}
.section-title span {{ color:#64748b; font-weight:600; font-size:9pt; }}
.table-wrapper {{ border:1px solid #e2e8f0; border-radius:14px; overflow:hidden; }}
table {{ width:100%; border-collapse:collapse; font-size:9.5pt; }}
th {{
  background:#f8fafc; color:#475569; padding:11px 16px;
  font-size:7.5pt; font-weight:800; text-transform:uppercase; letter-spacing:.06em;
  border-bottom:1px solid #e2e8f0; text-align:left;
}}
td {{ padding:11px 16px; border-bottom:1px solid #f1f5f9; vertical-align:middle; color:#334155; }}
tr:last-child td {{ border-bottom:none; }}
tr:nth-child(even) td {{ background:#fafbfc; }}
tr.row-mora td {{ background:#fff5f5!important; }}
tr.row-urgente td {{ background:#fffbeb!important; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.muted {{ color:#94a3b8; font-weight:500; }}
.strong {{ font-weight:700; color:#0f172a; }}
.cliente-nombre, .concepto {{ font-weight:600; color:#1e293b; }}
.badge-sc {{
  display:inline-block; min-width:22px; text-align:center;
  padding:3px 8px; border-radius:6px; font-size:8pt; font-weight:800;
}}
.sc-a {{ background:#dcfce7; color:#166534; }}
.sc-b {{ background:#dbeafe; color:#1d4ed8; }}
.sc-c {{ background:#fef3c7; color:#92400e; }}
.sc-d {{ background:#fee2e2; color:#991b1b; }}
.sc-x {{ background:#f1f5f9; color:#64748b; }}
.badge-pill {{
  display:inline-block; padding:3px 10px; border-radius:999px;
  font-size:7.5pt; font-weight:700; letter-spacing:.03em;
}}
.pill-danger {{ background:#fee2e2; color:#b91c1c; }}
.pill-ok {{ background:#ecfdf5; color:#047857; }}
.badge-tipo {{
  display:inline-block; padding:3px 9px; border-radius:6px;
  font-size:7pt; font-weight:700; letter-spacing:.04em;
}}
.tipo-tarjeta {{ background:#ede9fe; color:#5b21b6; }}
.tipo-cheque {{ background:#e0f2fe; color:#0369a1; }}
.tipo-banco {{ background:#fce7f3; color:#9d174d; }}
.tipo-otro {{ background:#f1f5f9; color:#475569; }}
.empty {{ text-align:center; color:#94a3b8; padding:28px!important; font-style:italic; }}
.footer {{
  padding:20px 40px; background:#f8fafc; border-top:1px solid #e2e8f0;
  font-size:8pt; color:#94a3b8;
  display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;
}}
.footer strong {{ color:#64748b; }}
@media (max-width:720px) {{
  .hero {{ flex-direction:column; padding:28px 24px; }}
  .hero-right {{ text-align:left; width:100%; }}
  .body-pad {{ padding:24px; }}
  .balance-strip, .kpi-grid, .kpi-grid-4 {{ grid-template-columns:1fr 1fr; }}
}}
</style>
</head>
<body>
<div class="container">
  <header class="hero">
    <div class="hero-left">
      <div class="hero-tag">● Briefing del Jefe</div>
      <div class="brand">{_esc(nombre_emp)}</div>
      <div class="sub">Informe ejecutivo diario · Master Total{f' · CUIT {cuit}' if cuit else ''}</div>
    </div>
    <div class="hero-right">
      <div class="fecha-box">
        <div class="fecha-label">Fecha del informe</div>
        <div class="fecha-val">{_esc(fecha)}</div>
      </div>
      <span class="salud-pill {salud_cls}">{salud}</span>
    </div>
  </header>

  <div class="body-pad">
    <div class="balance-strip">
      <div class="balance-item">
        <div class="bl">Activo total</div>
        <div class="bv pos">{_fmt_money(r.get('activo_total'))}</div>
      </div>
      <div class="balance-item">
        <div class="bl">Pasivo total</div>
        <div class="bv neg">{_fmt_money(r.get('pasivo_total'))}</div>
      </div>
      <div class="balance-item">
        <div class="bl">Cuentas por cobrar</div>
        <div class="bv">{_fmt_money(r.get('cuentas_por_cobrar'))}</div>
      </div>
    </div>

    {alert_html}

    <div class="kpi-group">
      <div class="group-head">Cobranza</div>
      <div class="kpi-grid">{cobranza}</div>
    </div>
    <div class="kpi-group">
      <div class="group-head">Obligaciones de pago</div>
      <div class="kpi-grid">{pagos}</div>
    </div>
    <div class="kpi-group">
      <div class="group-head">Posición financiera</div>
      <div class="kpi-grid kpi-grid-4">{posicion}</div>
    </div>

    <div class="section">
      <div class="section-title">Clientes a cobrar <span>{len(report.get('clientes_a_cobrar') or [])} registros</span></div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Cliente</th><th class="num">Scoring</th><th class="num">Límite</th><th class="num">Saldo</th><th class="num">Estado</th></tr></thead>
          <tbody>{_clientes_rows(report.get('clientes_a_cobrar') or [])}</tbody>
        </table>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Obligaciones a pagar <span>{len(report.get('obligaciones_a_pagar') or [])} registros</span></div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Concepto</th><th>Tipo</th><th class="num">Recibido</th><th class="num">Saldo</th><th>Vencimiento</th></tr></thead>
          <tbody>{_obligaciones_rows(report.get('obligaciones_a_pagar') or [])}</tbody>
        </table>
      </div>
    </div>
  </div>

  <footer class="footer">
    <span>Generado automáticamente para la gerencia · <strong>Master Total</strong></span>
    <span>{_esc(report.get('generado_at', '')[:19].replace('T', ' '))} UTC</span>
  </footer>
</div>
</body>
</html>"""


_PDF_MAX_ROWS = 18


def render_daily_report_pdf(report: dict[str, Any]) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError as e:
        raise RuntimeError("fpdf2 no instalado") from e

    emp = report.get("empresa") or {}
    r = report.get("resumen") or {}
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, _pdf_safe(emp.get("razon_social") or "Informe diario"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(
        0, 5,
        _pdf_safe(f"Informe ejecutivo · {report.get('fecha_legible', '')} · Salud: {r.get('estado_salud', '')}"),
        ln=True,
    )
    pdf.ln(3)

    kpis = [
        ("A cobrar", _fmt_money(r.get("total_a_cobrar")), f"{r.get('clientes_con_saldo', 0)} ctas"),
        ("En mora", _fmt_money(r.get("monto_en_mora")), f"{r.get('clientes_en_mora', 0)} cli"),
        ("A pagar", _fmt_money(r.get("total_a_pagar_financiero")), f"{r.get('obligaciones_activas', 0)} ops"),
        ("Vencido", _fmt_money(r.get("total_a_pagar_vencido")), f"{r.get('deudas_urgentes', 0)} urg"),
        ("Caja real", _fmt_money(r.get("caja_real")), ""),
        ("Patrimonio", _fmt_money(r.get("patrimonio_neto")), ""),
        ("Stock", _fmt_kg(r.get("stock_kg")), ""),
        ("Sangria", _fmt_money(r.get("sangria_diaria")), ""),
    ]
    pdf.set_font("Helvetica", "B", 8)
    for label, val, sub in kpis:
        pdf.cell(42, 5, _pdf_safe(label))
        pdf.cell(48, 5, _pdf_safe(val))
        pdf.cell(0, 5, _pdf_safe(sub), ln=True)
    pdf.ln(3)

    def table_block(title: str, headers: list[str], rows: list[list[str]], widths: list[int]):
        if pdf.get_y() > 255:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, _pdf_safe(title), ln=True)
        pdf.set_font("Helvetica", "B", 7)
        for i, h in enumerate(headers):
            pdf.cell(widths[i], 5, _pdf_safe(h), border=1)
        pdf.ln()
        pdf.set_font("Helvetica", "", 7)
        if not rows:
            pdf.cell(sum(widths), 5, "Sin registros", border=1, ln=True)
            pdf.ln(2)
            return
        for row in rows[:_PDF_MAX_ROWS]:
            for i, cell in enumerate(row):
                pdf.cell(widths[i], 5, _pdf_safe(cell)[:36], border=1)
            pdf.ln()
        extra = len(rows) - _PDF_MAX_ROWS
        if extra > 0:
            pdf.set_font("Helvetica", "I", 7)
            pdf.cell(0, 4, _pdf_safe(f"... y {extra} mas (ver informe HTML)"), ln=True)
        pdf.ln(3)

    table_block(
        "Clientes a cobrar",
        ["Cliente", "Scor.", "Saldo", "Est."],
        [
            [
                str(c.get("nombre") or ""),
                str(c.get("scoring") or ""),
                _fmt_money(c.get("saldo_actual")),
                "MORA" if c.get("en_mora") else "OK",
            ]
            for c in (report.get("clientes_a_cobrar") or [])
        ],
        [78, 14, 38, 18],
    )
    table_block(
        "Obligaciones a pagar",
        ["Concepto", "Tipo", "Saldo", "Vto."],
        [
            [
                str(e.get("alias") or e.get("tipo") or ""),
                str((e.get("tipo") or "").upper())[:8],
                _fmt_money(e.get("saldo_pendiente") or e.get("pagar")),
                str(e.get("fecha_vencimiento") or "-")[:10],
            ]
            for e in (report.get("obligaciones_a_pagar") or [])
        ],
        [70, 22, 38, 28],
    )

    return pdf.output()


def smtp_configured(empresa: dict[str, Any] | None = None) -> bool:
    return bool(_resolve_email_settings(empresa).get("transport"))


def _resolve_email_settings(empresa: dict[str, Any] | None = None) -> dict[str, Any]:
    emp = empresa or {}
    host = str(Config.SMTP_HOST or emp.get("smtp_host") or "").strip()
    user = str(Config.SMTP_USER or emp.get("smtp_user") or "").strip()
    password = str(Config.SMTP_PASSWORD or emp.get("smtp_password") or "").replace(" ", "")
    from_addr = str(Config.SMTP_FROM or emp.get("smtp_from") or user or "").strip()
    port = int(emp.get("smtp_port") or Config.SMTP_PORT or 587)
    use_ssl = emp.get("smtp_use_ssl") if "smtp_use_ssl" in emp else Config.SMTP_USE_SSL
    resend_key = str(Config.RESEND_API_KEY or emp.get("resend_api_key") or "").strip()

    transport = ""
    if resend_key:
        transport = "resend"
    elif host and user and password and from_addr:
        transport = "smtp"

    return {
        "transport": transport,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from": from_addr,
        "use_ssl": bool(use_ssl),
        "use_tls": Config.SMTP_USE_TLS,
        "resend_key": resend_key,
        "on_render": Config.ON_RENDER,
    }


def _build_email_message(
    *,
    from_addr: str,
    dest: list[str],
    subject: str,
    plain: str,
    html_body: str,
    pdf_bytes: bytes,
    pdf_name: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = from_addr
    msg["To"] = ", ".join(dest)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=pdf_name)
    msg.attach(attachment)
    return msg


def _send_via_smtp(settings: dict[str, Any], msg: MIMEMultipart, dest: list[str]) -> None:
    context = ssl.create_default_context()
    try:
        if settings["use_ssl"]:
            with smtplib.SMTP_SSL(
                settings["host"], settings["port"], timeout=30, context=context
            ) as server:
                server.login(settings["user"], settings["password"])
                server.sendmail(settings["from"], dest, msg.as_string())
            return

        with smtplib.SMTP(settings["host"], settings["port"], timeout=30) as server:
            server.ehlo()
            if settings["use_tls"]:
                server.starttls(context=context)
                server.ehlo()
            server.login(settings["user"], settings["password"])
            server.sendmail(settings["from"], dest, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(
            "Gmail rechazó el usuario o la contraseña. "
            "Usá una contraseña de aplicación de Google (16 caracteres, sin espacios)."
        ) from e
    except (TimeoutError, socket.timeout, OSError, ssl.SSLError) as e:
        if settings.get("on_render"):
            raise RuntimeError(
                "No se pudo conectar al servidor SMTP desde Render. "
                "El plan free bloquea los puertos 465/587. "
                "Agregá RESEND_API_KEY (envío por HTTPS) o subí a un plan pago de Render."
            ) from e
        raise RuntimeError(f"No se pudo conectar a {settings['host']}:{settings['port']}: {e}") from e


def _send_via_resend(
    *,
    api_key: str,
    from_addr: str,
    dest: list[str],
    subject: str,
    html_body: str,
    plain: str,
    pdf_bytes: bytes,
    pdf_name: str,
) -> None:
    # Gmail como remitente no funciona en Resend sin dominio verificado
    if "@gmail." in from_addr.lower() or "@googlemail." in from_addr.lower():
        from_addr = "onboarding@resend.dev"
    payload = {
        "from": from_addr,
        "to": dest,
        "subject": subject,
        "html": html_body,
        "text": plain,
        "attachments": [
            {
                "filename": pdf_name,
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
            }
        ],
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "MasterTotal/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            if resp.status >= 300:
                body = resp.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Resend respondió {resp.status}: {body}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Resend error {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"No se pudo contactar a Resend: {e.reason}") from e


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


def _email_summary_html(report: dict[str, Any]) -> str:
    """HTML liviano para el cuerpo del email (el PDF lleva el detalle)."""
    emp = report.get("empresa") or {}
    r = report.get("resumen") or {}
    nombre = _esc(emp.get("razon_social") or "Empresa")
    fecha = _esc(report.get("fecha_legible") or "")
    return f"""<!DOCTYPE html><html><body style="font-family:system-ui,sans-serif;color:#1e293b;padding:20px">
<h2 style="margin:0 0 8px">Informe diario — {nombre}</h2>
<p style="color:#64748b;margin:0 0 16px">{fecha}</p>
<table style="border-collapse:collapse;font-size:14px">
<tr><td style="padding:6px 16px 6px 0;color:#64748b">A cobrar</td><td><strong>{_fmt_money(r.get('total_a_cobrar'))}</strong></td></tr>
<tr><td style="padding:6px 16px 6px 0;color:#64748b">En mora</td><td><strong style="color:#dc2626">{_fmt_money(r.get('monto_en_mora'))}</strong></td></tr>
<tr><td style="padding:6px 16px 6px 0;color:#64748b">A pagar</td><td><strong>{_fmt_money(r.get('total_a_pagar_financiero'))}</strong></td></tr>
<tr><td style="padding:6px 16px 6px 0;color:#64748b">Caja real</td><td><strong>{_fmt_money(r.get('caja_real'))}</strong></td></tr>
<tr><td style="padding:6px 16px 6px 0;color:#64748b">Patrimonio</td><td><strong>{_fmt_money(r.get('patrimonio_neto'))}</strong></td></tr>
</table>
<p style="margin-top:20px;color:#64748b;font-size:13px">PDF completo adjunto.</p>
</body></html>"""


def send_daily_report_email(report: dict[str, Any], recipients: list[str] | None = None) -> dict[str, Any]:
    empresa = report.get("empresa") or {}
    settings = _resolve_email_settings(empresa)
    if not settings["transport"]:
        return {
            "ok": False,
            "error": (
                "Email no configurado. Completá Gmail en el modal (usuario + contraseña de aplicación) "
                "o definí SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM en el servidor. "
                "En Render free usá RESEND_API_KEY."
            ),
        }

    dest = recipients or get_report_recipients(empresa)
    if not dest:
        return {
            "ok": False,
            "error": "No hay destinatarios. Agregá al menos un email en Destinatarios y guardá.",
        }

    nombre = empresa.get("razon_social") or "Empresa"
    fecha = report.get("fecha_legible") or report.get("fecha") or ""
    subject = f"Informe diario {nombre} — {fecha}"
    plain = f"Informe ejecutivo diario de {nombre} ({fecha}). PDF adjunto."
    pdf_name = f"informe-diario-{fecha.replace('/', '-')}.pdf"

    try:
        pdf_bytes = render_daily_report_pdf(report)
        email_html = _email_summary_html(report)
        full_html = render_daily_report_html(report) if settings["transport"] == "smtp" else email_html
    except Exception as e:
        return {"ok": False, "error": f"Error al generar informe: {e}"}

    try:
        if settings["transport"] == "resend":
            from_addr = settings["from"] or "onboarding@resend.dev"
            _send_via_resend(
                api_key=settings["resend_key"],
                from_addr=from_addr,
                dest=dest,
                subject=subject,
                html_body=email_html,
                plain=plain,
                pdf_bytes=pdf_bytes,
                pdf_name=pdf_name,
            )
        else:
            msg = _build_email_message(
                from_addr=settings["from"],
                dest=dest,
                subject=subject,
                plain=plain,
                html_body=full_html,
                pdf_bytes=pdf_bytes,
                pdf_name=pdf_name,
            )
            _send_via_smtp(settings, msg, dest)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "sent_to": dest,
        "subject": subject,
        "transport": settings["transport"],
    }


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
