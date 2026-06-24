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
from datetime import datetime
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Config

REPORT_WEEKDAY_MONDAY = 0
DEFAULT_REPORT_HOUR = "05:00"
DEFAULT_REPORT_TIMEZONE = "America/Argentina/Buenos_Aires"


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
        .replace("·", "-").replace("–", "-").replace("—", "-")
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


def _vencimientos_rows(items: list[dict], limit: int = 15) -> str:
    if not items:
        return '<tr><td colspan="4" class="empty">Sin vencimientos en este tramo</td></tr>'
    rows = []
    for v in items[:limit]:
        estado = "VENCIDO" if v.get("vencido") else str(v.get("estado_vencimiento") or "—").upper()
        cls = " row-mora" if v.get("vencido") else (" row-urgente" if v.get("estado_vencimiento") == "hoy" else "")
        rows.append(
            f"<tr class{cls}>"
            f"<td><span class='concepto'>{_esc(v.get('alias') or v.get('tipo'))}</span></td>"
            f"<td>{_tipo_badge(v.get('tipo'))}</td>"
            f"<td class='num strong'>{_fmt_money(v.get('saldo_pendiente') or v.get('total_pagar'))}</td>"
            f"<td>{_esc(v.get('fecha_vencimiento') or '—')} <span class='muted'>· {estado}</span></td>"
            f"</tr>"
        )
    return "".join(rows)


def _cfr_rows(items: list[dict], limit: int = 10) -> str:
    if not items:
        return '<tr><td colspan="4" class="empty">Sin deuda financiera con CFR</td></tr>'
    rows = []
    for e in items[:limit]:
        rows.append(
            f"<tr>"
            f"<td><span class='concepto'>{_esc(e.get('alias'))}</span></td>"
            f"<td>{_tipo_badge(e.get('tipo'))}</td>"
            f"<td class='num strong' style='color:#dc2626'>{float(e.get('cfr') or 0):.1f}%</td>"
            f"<td class='num'>{_fmt_money(e.get('saldo_pendiente') or e.get('pagar'))}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _remitos_rows(items: list[dict], limit: int = 12) -> str:
    if not items:
        return '<tr><td colspan="5" class="empty">Sin remitos recientes</td></tr>'
    rows = []
    for rem in items[:limit]:
        estado = str(rem.get("estado_cobro") or "—")
        est_cls = "pill-ok" if estado == "cobrado" else ("pill-danger" if estado == "incobrable" else "")
        rows.append(
            f"<tr>"
            f"<td>{_esc(rem.get('fecha'))}</td>"
            f"<td><span class='concepto'>{_esc(rem.get('cliente'))}</span></td>"
            f"<td class='num'>{_fmt_kg(rem.get('kg'))}</td>"
            f"<td class='num strong'>{_fmt_money(rem.get('precio_venta_total'))}</td>"
            f"<td><span class='badge-pill {est_cls}'>{_esc(estado.upper())}</span></td>"
            f"</tr>"
        )
    return "".join(rows)


def _aging_bars_html(totales: dict[str, Any]) -> str:
    labels = [
        ("0_30", "0–30 días", "#10b981"),
        ("31_60", "31–60 días", "#f59e0b"),
        ("61_90", "61–90 días", "#f97316"),
        ("90_plus", "+90 días", "#ef4444"),
    ]
    vals = [float(totales.get(k) or 0) for k, _, _ in labels]
    max_v = max(vals) if vals and max(vals) > 0 else 1.0
    bars = []
    for (key, label, color), val in zip(labels, vals):
        pct = max(4, int(val / max_v * 100)) if val > 0 else 0
        bars.append(
            f'<div class="aging-row">'
            f'<div class="aging-lbl">{label}</div>'
            f'<div class="aging-track"><div class="aging-fill" style="width:{pct}%;background:{color}"></div></div>'
            f'<div class="aging-val">{_fmt_money(val)}</div>'
            f'</div>'
        )
    return "".join(bars)


def _balance_sheet_html(balance: dict[str, Any]) -> str:
    act = balance.get("activos") or {}
    pas = balance.get("pasivos") or {}
    return f"""
    <div class="balance-sheet">
      <div class="bs-col">
        <div class="bs-head">Activos</div>
        <div class="bs-row"><span>Caja real</span><span class="num">{_fmt_money(act.get('caja_real'))}</span></div>
        <div class="bs-row"><span>Cuentas por cobrar</span><span class="num">{_fmt_money(act.get('cuentas_por_cobrar'))}</span></div>
        <div class="bs-row"><span>Inventario valorizado</span><span class="num">{_fmt_money(act.get('inventario'))}</span></div>
        <div class="bs-row bs-total"><span>Total activos</span><span class="num">{_fmt_money(act.get('total'))}</span></div>
      </div>
      <div class="bs-col">
        <div class="bs-head">Pasivos</div>
        <div class="bs-row"><span>Deuda financiera</span><span class="num">{_fmt_money(pas.get('deuda_financiera'))}</span></div>
        <div class="bs-row"><span>Deuda comercial</span><span class="num">{_fmt_money(pas.get('deuda_comercial'))}</span></div>
        <div class="bs-row bs-total"><span>Total pasivos</span><span class="num">{_fmt_money(pas.get('total'))}</span></div>
        <div class="bs-row bs-pat"><span>Patrimonio neto</span><span class="num">{_fmt_money(balance.get('patrimonio'))}</span></div>
      </div>
    </div>"""


def _executive_html(bullets: list[str]) -> str:
    if not bullets:
        return ""
    items = "".join(f"<li>{_esc(b)}</li>" for b in bullets)
    return f'<div class="exec-box"><div class="exec-title">Resumen ejecutivo</div><ul class="exec-list">{items}</ul></div>'


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
    op = report.get("operacional") or {}
    proy = report.get("proyeccion") or {}
    nombre_emp = emp.get("razon_social") or "Empresa"
    fecha = report.get("fecha_legible") or report.get("fecha") or ""
    salud = _esc(r.get("estado_salud"))
    salud_cls = _salud_class(r.get("estado_salud"))
    cuit = _esc(emp.get("cuit") or "")
    contacto = " · ".join(
        x for x in [
            emp.get("telefono"),
            emp.get("email"),
            emp.get("direccion"),
        ] if x
    )

    alert_html = ""
    if r.get("clientes_en_mora") or r.get("total_a_pagar_vencido"):
        alert_html = (
            f'<div class="alert">'
            f'<div class="alert-icon">!</div>'
            f'<div><strong>Alertas del día</strong>'
            f'<p>{r.get("clientes_en_mora", 0)} clientes en mora '
            f'({_fmt_money(r.get("monto_en_mora"))}) · '
            f'{_fmt_money(r.get("total_a_pagar_vencido"))} vencidos a pagar · '
            f'{r.get("deudas_urgentes", 0)} obligaciones urgentes.</p></div>'
            f"</div>"
        )

    cobranza = (
        _kpi_card("A cobrar", _fmt_money(r.get("total_a_cobrar")), f"{r.get('clientes_con_saldo', 0)} cuentas", "green", "↗")
        + _kpi_card("En mora", _fmt_money(r.get("monto_en_mora")), f"{r.get('clientes_en_mora', 0)} clientes", "red", "⚠")
        + _kpi_card("Incobrables", str(r.get("clientes_inrecuperables", 0)), "Marcados +60d", "amber", "✕")
    )
    pagos = (
        _kpi_card("Deuda financiera", _fmt_money(r.get("total_a_pagar_financiero")), f"{r.get('obligaciones_activas', 0)} ops", "red", "↘")
        + _kpi_card("Vencido", _fmt_money(r.get("total_a_pagar_vencido")), f"{r.get('deudas_urgentes', 0)} urgentes", "red", "⏱")
        + _kpi_card("Deuda comercial", _fmt_money(r.get("total_a_pagar_comercial")), "Proveedores", "amber", "◎")
    )
    posicion = (
        _kpi_card("Caja real", _fmt_money(r.get("caja_real")), "Disponible", "blue", "◆")
        + _kpi_card("Patrimonio", _fmt_money(r.get("patrimonio_neto")), salud, "blue", "★")
        + _kpi_card("Stock", _fmt_kg(r.get("stock_kg")), _fmt_money(r.get("stock_valorizado")), "", "▣")
        + _kpi_card("Sangría/día", _fmt_money(r.get("sangria_diaria")), f"Fin {_fmt_money(r.get('sangria_financiera'))}", "", "∿")
    )
    operacion = (
        _kpi_card("Ventas hoy", _fmt_money(op.get("ventas_hoy")), f"{op.get('remitos_hoy', 0)} remitos · {_fmt_kg(op.get('kg_hoy'))}", "green", "₿")
        + _kpi_card("Ventas del mes", _fmt_money(op.get("ventas_mes")), f"Margen {op.get('margen_pct_mes', 0)}%", "blue", "◈")
        + _kpi_card("Cobros hoy", _fmt_money(op.get("cobros_hoy")), f"{op.get('pagos_registrados_hoy', 0)} pagos", "green", "✓")
    )
    aging_html = _aging_bars_html((report.get("antiguedad") or {}).get("totales") or {})
    balance_html = _balance_sheet_html(report.get("balance") or {})
    exec_html = _executive_html(report.get("resumen_ejecutivo") or [])
    meta_proy = proy.get("meta_texto") or "—"
    proy_html = (
        f'<div class="proy-box">'
        f'<div><span class="proy-lbl">Liberación de deuda</span><strong>{_esc(meta_proy)}</strong> meses</div>'
        f'<div><span class="proy-lbl">Excedente mensual</span><strong>{_fmt_money(proy.get("excedente_mensual"))}</strong></div>'
        f'<div><span class="proy-lbl">Carga financiera</span><strong>{_fmt_money(proy.get("carga_financiera_mensual"))}</strong>/mes</div>'
        f'</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Informe Empresarial — {_esc(nombre_emp)}</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{
  font-family:'Segoe UI',system-ui,sans-serif;
  color:#0f172a; font-size:10pt; line-height:1.55;
  background:#e8edf5; min-height:100vh; padding:28px 16px;
}}
@media print {{
  body {{ background:#fff; padding:0; }}
  * {{ -webkit-print-color-adjust:exact!important; print-color-adjust:exact!important; }}
  .section, .exec-box, .balance-sheet, .kpi {{ break-inside:avoid; }}
}}
.container {{
  max-width:980px; margin:0 auto; background:#fff;
  border-radius:16px; overflow:hidden;
  box-shadow:0 16px 48px rgba(15,23,42,.1);
  border:1px solid #e2e8f0;
}}
.hero {{
  background:linear-gradient(135deg,#0c1222 0%,#1a2744 40%,#1e40af 100%);
  color:#fff; padding:32px 36px 28px;
  display:flex; justify-content:space-between; gap:20px;
}}
.hero-tag {{
  font-size:7pt; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
  background:rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.2);
  padding:4px 12px; border-radius:999px; margin-bottom:12px; display:inline-block;
}}
.brand {{ font-size:22pt; font-weight:800; letter-spacing:-.02em; }}
.sub {{ color:rgba(255,255,255,.7); font-size:9pt; margin-top:6px; }}
.hero-right {{ text-align:right; }}
.fecha-box {{
  background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.15);
  border-radius:12px; padding:12px 16px;
}}
.fecha-label {{ font-size:7pt; text-transform:uppercase; opacity:.65; }}
.fecha-val {{ font-size:13pt; font-weight:800; margin-top:2px; }}
.salud-pill {{
  display:inline-block; margin-top:10px; padding:5px 12px; border-radius:999px;
  font-size:7.5pt; font-weight:700; text-transform:uppercase;
}}
.salud-good {{ background:#dcfce7; color:#166534; }}
.salud-warn {{ background:#fef3c7; color:#92400e; }}
.salud-bad {{ background:#fee2e2; color:#991b1b; }}
.salud-neutral {{ background:#e2e8f0; color:#475569; }}
.body-pad {{ padding:28px 36px 36px; }}
.exec-box {{
  background:linear-gradient(135deg,#f0f9ff,#f8fafc);
  border:1px solid #bae6fd; border-radius:14px; padding:18px 22px; margin-bottom:24px;
}}
.exec-title {{ font-size:8pt; font-weight:800; text-transform:uppercase; letter-spacing:.1em; color:#0369a1; margin-bottom:10px; }}
.exec-list {{ margin:0; padding-left:18px; color:#334155; font-size:9.5pt; }}
.exec-list li {{ margin-bottom:6px; }}
.balance-sheet {{
  display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px;
}}
.bs-col {{ border:1px solid #e2e8f0; border-radius:12px; overflow:hidden; }}
.bs-head {{ background:#f1f5f9; padding:10px 16px; font-size:8pt; font-weight:800; text-transform:uppercase; color:#475569; }}
.bs-row {{ display:flex; justify-content:space-between; padding:9px 16px; border-bottom:1px solid #f1f5f9; font-size:9.5pt; }}
.bs-total {{ background:#f8fafc; font-weight:700; }}
.bs-pat {{ background:#eff6ff; font-weight:800; color:#1d4ed8; }}
.two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:24px; }}
.aging-panel {{ border:1px solid #e2e8f0; border-radius:12px; padding:16px 18px; }}
.aging-title {{ font-size:8pt; font-weight:800; text-transform:uppercase; color:#64748b; margin-bottom:12px; }}
.aging-row {{ display:grid; grid-template-columns:72px 1fr 72px; gap:10px; align-items:center; margin-bottom:8px; }}
.aging-lbl {{ font-size:8pt; color:#64748b; font-weight:600; }}
.aging-track {{ height:8px; background:#f1f5f9; border-radius:999px; overflow:hidden; }}
.aging-fill {{ height:100%; border-radius:999px; }}
.aging-val {{ font-size:8.5pt; font-weight:700; text-align:right; }}
.proy-box {{
  border:1px solid #e2e8f0; border-radius:12px; padding:16px 18px;
  display:grid; grid-template-columns:repeat(3,1fr); gap:12px; text-align:center;
}}
.proy-lbl {{ display:block; font-size:7pt; text-transform:uppercase; color:#94a3b8; margin-bottom:4px; }}
.kpi-group {{ margin-bottom:22px; }}
.group-head {{
  display:flex; align-items:center; gap:10px; margin-bottom:12px;
  font-size:8pt; font-weight:800; text-transform:uppercase; letter-spacing:.08em; color:#64748b;
}}
.group-head::after {{ content:''; flex:1; height:1px; background:#e2e8f0; }}
.kpi-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }}
.kpi-grid-4 {{ grid-template-columns:repeat(4,1fr); }}
.kpi {{ border:1px solid #e2e8f0; border-radius:12px; padding:14px 16px; background:#fff; position:relative; }}
.kpi::after {{ content:''; position:absolute; top:0; left:0; right:0; height:3px; background:#cbd5e1; }}
.kpi-green::after {{ background:#10b981; }}
.kpi-red::after {{ background:#ef4444; }}
.kpi-blue::after {{ background:#2563eb; }}
.kpi-amber::after {{ background:#f59e0b; }}
.kpi-top {{ display:flex; align-items:center; gap:6px; margin-bottom:8px; }}
.kpi-icon {{ font-size:10pt; }}
.lbl {{ font-size:7pt; text-transform:uppercase; letter-spacing:.05em; color:#64748b; font-weight:700; }}
.val {{ font-size:15pt; font-weight:800; font-variant-numeric:tabular-nums; }}
.val-green {{ color:#059669; }} .val-red {{ color:#dc2626; }}
.val-blue {{ color:#2563eb; }} .val-amber {{ color:#d97706; }}
.sub-text {{ font-size:7.5pt; color:#94a3b8; margin-top:4px; }}
.alert {{
  display:flex; gap:12px; background:#fef2f2; border:1px solid #fecaca;
  border-left:4px solid #ef4444; border-radius:10px; padding:14px 16px; margin-bottom:22px;
}}
.alert-icon {{
  width:26px; height:26px; border-radius:50%; background:#ef4444; color:#fff;
  font-weight:800; display:flex; align-items:center; justify-content:center; flex-shrink:0;
}}
.section {{ margin-bottom:26px; }}
.section-title {{
  font-size:10.5pt; font-weight:800; margin-bottom:12px;
  display:flex; align-items:center; gap:8px;
}}
.section-title span {{ color:#94a3b8; font-weight:600; font-size:8.5pt; }}
.table-wrapper {{ border:1px solid #e2e8f0; border-radius:12px; overflow:hidden; }}
table {{ width:100%; border-collapse:collapse; font-size:9pt; }}
th {{
  background:#f8fafc; color:#475569; padding:10px 14px;
  font-size:7pt; font-weight:800; text-transform:uppercase; text-align:left;
  border-bottom:1px solid #e2e8f0;
}}
td {{ padding:10px 14px; border-bottom:1px solid #f1f5f9; }}
tr:nth-child(even) td {{ background:#fafbfc; }}
tr.row-mora td {{ background:#fff5f5!important; }}
tr.row-urgente td {{ background:#fffbeb!important; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.muted {{ color:#94a3b8; }}
.strong {{ font-weight:700; }}
.cliente-nombre, .concepto {{ font-weight:600; }}
.badge-sc {{ display:inline-block; padding:2px 7px; border-radius:5px; font-size:7.5pt; font-weight:800; }}
.sc-a {{ background:#dcfce7; color:#166534; }} .sc-b {{ background:#dbeafe; color:#1d4ed8; }}
.sc-c {{ background:#fef3c7; color:#92400e; }} .sc-d {{ background:#fee2e2; color:#991b1b; }}
.sc-x {{ background:#f1f5f9; color:#64748b; }}
.badge-pill {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:7pt; font-weight:700; }}
.pill-danger {{ background:#fee2e2; color:#b91c1c; }} .pill-ok {{ background:#ecfdf5; color:#047857; }}
.badge-tipo {{ display:inline-block; padding:2px 8px; border-radius:5px; font-size:6.5pt; font-weight:700; }}
.tipo-tarjeta {{ background:#ede9fe; color:#5b21b6; }} .tipo-cheque {{ background:#e0f2fe; color:#0369a1; }}
.tipo-banco {{ background:#fce7f3; color:#9d174d; }} .tipo-otro {{ background:#f1f5f9; color:#475569; }}
.empty {{ text-align:center; color:#94a3b8; padding:24px!important; font-style:italic; }}
.footer {{
  padding:16px 36px; background:#f8fafc; border-top:1px solid #e2e8f0;
  font-size:7.5pt; color:#94a3b8; display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px;
}}
@media (max-width:720px) {{
  .hero {{ flex-direction:column; }} .balance-sheet, .two-col, .kpi-grid, .kpi-grid-4, .proy-box {{ grid-template-columns:1fr; }}
}}
</style>
</head>
<body>
<div class="container">
  <header class="hero">
    <div>
      <div class="hero-tag">Informe Empresarial</div>
      <div class="brand">{_esc(nombre_emp)}</div>
      <div class="sub">Reporte de gestión · Master Total{f' · CUIT {cuit}' if cuit else ''}{f'<br>{_esc(contacto)}' if contacto else ''}</div>
    </div>
    <div class="hero-right">
      <div class="fecha-box">
        <div class="fecha-label">Fecha de corte</div>
        <div class="fecha-val">{_esc(fecha)}</div>
      </div>
      <span class="salud-pill {salud_cls}">{salud}</span>
    </div>
  </header>

  <div class="body-pad">
    {exec_html}
    {alert_html}
    {balance_html}

    <div class="kpi-group">
      <div class="group-head">Cobranza y cartera</div>
      <div class="kpi-grid">{cobranza}</div>
    </div>
    <div class="kpi-group">
      <div class="group-head">Pasivos y obligaciones</div>
      <div class="kpi-grid">{pagos}</div>
    </div>
    <div class="kpi-group">
      <div class="group-head">Posición financiera</div>
      <div class="kpi-grid kpi-grid-4">{posicion}</div>
    </div>
    <div class="kpi-group">
      <div class="group-head">Operación comercial</div>
      <div class="kpi-grid">{operacion}</div>
    </div>

    <div class="two-col">
      <div class="aging-panel">
        <div class="aging-title">Antigüedad de deuda (cartera)</div>
        {aging_html}
      </div>
      <div>{proy_html}</div>
    </div>

    <div class="section">
      <div class="section-title">Vencimientos vencidos <span>{len(report.get('vencimientos_vencidos') or [])}</span></div>
      <div class="table-wrapper">
        <table><thead><tr><th>Concepto</th><th>Tipo</th><th class="num">Saldo</th><th>Vencimiento</th></tr></thead>
        <tbody>{_vencimientos_rows(report.get('vencimientos_vencidos') or [])}</tbody></table>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Próximos vencimientos <span>{len(report.get('vencimientos_proximos') or [])}</span></div>
      <div class="table-wrapper">
        <table><thead><tr><th>Concepto</th><th>Tipo</th><th class="num">Saldo</th><th>Fecha</th></tr></thead>
        <tbody>{_vencimientos_rows(report.get('vencimientos_proximos') or [])}</tbody></table>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Mayor costo financiero (CFR) <span>Top deudas</span></div>
      <div class="table-wrapper">
        <table><thead><tr><th>Operación</th><th>Tipo</th><th class="num">CFR mensual</th><th class="num">Saldo</th></tr></thead>
        <tbody>{_cfr_rows(report.get('top_cfr') or [])}</tbody></table>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Clientes a cobrar <span>{len(report.get('clientes_a_cobrar') or [])}</span></div>
      <div class="table-wrapper">
        <table><thead><tr><th>Cliente</th><th class="num">Scoring</th><th class="num">Límite</th><th class="num">Saldo</th><th class="num">Estado</th></tr></thead>
        <tbody>{_clientes_rows(report.get('clientes_a_cobrar') or [])}</tbody></table>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Obligaciones a pagar <span>{len(report.get('obligaciones_a_pagar') or [])}</span></div>
      <div class="table-wrapper">
        <table><thead><tr><th>Concepto</th><th>Tipo</th><th class="num">Recibido</th><th class="num">Saldo</th><th>Vencimiento</th></tr></thead>
        <tbody>{_obligaciones_rows(report.get('obligaciones_a_pagar') or [])}</tbody></table>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Últimos remitos de venta <span>{len(report.get('remitos_recientes') or [])}</span></div>
      <div class="table-wrapper">
        <table><thead><tr><th>Fecha</th><th>Cliente</th><th class="num">Kg</th><th class="num">Venta</th><th>Estado</th></tr></thead>
        <tbody>{_remitos_rows(report.get('remitos_recientes') or [])}</tbody></table>
      </div>
    </div>
  </div>

  <footer class="footer">
    <span>Informe Empresarial · <strong>Master Total</strong> · Confidencial</span>
    <span>Generado {_esc(report.get('generado_at', '')[:19].replace('T', ' '))} UTC</span>
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
    op = report.get("operacional") or {}
    bal = report.get("balance") or {}
    act = bal.get("activos") or {}
    pas = bal.get("pasivos") or {}
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 7, _pdf_safe(emp.get("razon_social") or "Informe Empresarial"), ln=True)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(
        0, 4,
        _pdf_safe(
            f"Informe Empresarial · {report.get('fecha_legible', '')} · "
            f"Salud: {r.get('estado_salud', '')}"
        ),
        ln=True,
    )
    pdf.ln(2)

    for bullet in (report.get("resumen_ejecutivo") or [])[:4]:
        pdf.set_font("Helvetica", "", 7)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 3.5, _pdf_safe(f"- {str(bullet)[:140]}"))
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 5, _pdf_safe("Balance patrimonial"), ln=True)
    pdf.set_font("Helvetica", "", 7)
    balance_lines = [
        ("Caja real", act.get("caja_real")),
        ("Cuentas por cobrar", act.get("cuentas_por_cobrar")),
        ("Inventario", act.get("inventario")),
        ("Total activos", act.get("total")),
        ("Deuda financiera", pas.get("deuda_financiera")),
        ("Deuda comercial", pas.get("deuda_comercial")),
        ("Patrimonio neto", bal.get("patrimonio")),
    ]
    for label, val in balance_lines:
        pdf.cell(55, 4, _pdf_safe(label))
        pdf.cell(0, 4, _pdf_safe(_fmt_money(val)), ln=True)
    pdf.ln(2)

    kpis = [
        ("A cobrar", _fmt_money(r.get("total_a_cobrar")), f"{r.get('clientes_con_saldo', 0)} ctas"),
        ("En mora", _fmt_money(r.get("monto_en_mora")), f"{r.get('clientes_en_mora', 0)} cli"),
        ("A pagar fin.", _fmt_money(r.get("total_a_pagar_financiero")), f"{r.get('obligaciones_activas', 0)} ops"),
        ("Vencido", _fmt_money(r.get("total_a_pagar_vencido")), ""),
        ("Ventas mes", _fmt_money(op.get("ventas_mes")), f"Margen {op.get('margen_pct_mes', 0)}%"),
        ("Ventas hoy", _fmt_money(op.get("ventas_hoy")), f"{op.get('remitos_hoy', 0)} rem"),
        ("Caja real", _fmt_money(r.get("caja_real")), ""),
        ("Sangria/dia", _fmt_money(r.get("sangria_diaria")), ""),
    ]
    pdf.set_font("Helvetica", "B", 8)
    for label, val, sub in kpis:
        pdf.cell(38, 4, _pdf_safe(label))
        pdf.cell(45, 4, _pdf_safe(val))
        pdf.cell(0, 4, _pdf_safe(sub), ln=True)
    pdf.ln(2)

    aging = (report.get("antiguedad") or {}).get("totales") or {}
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 4, _pdf_safe("Antiguedad de deuda"), ln=True)
    pdf.set_font("Helvetica", "", 7)
    for key, lbl in [("0_30", "0-30d"), ("31_60", "31-60d"), ("61_90", "61-90d"), ("90_plus", "+90d")]:
        pdf.cell(25, 4, _pdf_safe(lbl))
        pdf.cell(0, 4, _pdf_safe(_fmt_money(aging.get(key))), ln=True)
    pdf.ln(2)

    def table_block(title: str, headers: list[str], rows: list[list[str]], widths: list[int]):
        if pdf.get_y() > 255:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, _pdf_safe(title), ln=True)
        pdf.set_font("Helvetica", "B", 7)
        for i, h in enumerate(headers):
            pdf.cell(widths[i], 4, _pdf_safe(h), border=1)
        pdf.ln()
        pdf.set_font("Helvetica", "", 7)
        if not rows:
            pdf.cell(sum(widths), 4, "Sin registros", border=1, ln=True)
            pdf.ln(2)
            return
        for row in rows[:_PDF_MAX_ROWS]:
            for i, cell in enumerate(row):
                pdf.cell(widths[i], 4, _pdf_safe(cell)[:36], border=1)
            pdf.ln()
        extra = len(rows) - _PDF_MAX_ROWS
        if extra > 0:
            pdf.set_font("Helvetica", "I", 7)
            pdf.cell(0, 4, _pdf_safe(f"... y {extra} mas (ver informe HTML)"), ln=True)
        pdf.ln(2)

    table_block(
        "Vencimientos vencidos",
        ["Concepto", "Saldo", "Vto."],
        [
            [
                str(v.get("alias") or v.get("tipo") or ""),
                _fmt_money(v.get("saldo_pendiente") or v.get("total_pagar")),
                str(v.get("fecha_vencimiento") or "-")[:10],
            ]
            for v in (report.get("vencimientos_vencidos") or [])
        ],
        [80, 40, 30],
    )
    table_block(
        "Top CFR",
        ["Operacion", "CFR%", "Saldo"],
        [
            [
                str(e.get("alias") or ""),
                f"{float(e.get('cfr') or 0):.1f}",
                _fmt_money(e.get("saldo_pendiente") or e.get("pagar")),
            ]
            for e in (report.get("top_cfr") or [])
        ],
        [70, 20, 40],
    )
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
    """True si hay Resend API key (env o empresa)."""
    emp = empresa or {}
    return bool(str(Config.RESEND_API_KEY or emp.get("resend_api_key") or "").strip())


def _resolve_email_settings(empresa: dict[str, Any] | None = None) -> dict[str, Any]:
    emp = empresa or {}
    from_addr = str(Config.SMTP_FROM or emp.get("smtp_from") or "").strip()
    resend_key = str(Config.RESEND_API_KEY or emp.get("resend_api_key") or "").strip()

    transport = "resend" if resend_key else ""

    return {
        "transport": transport,
        "from": from_addr,
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
    """Destinatarios del informe: email del perfil de empresa, con fallback legacy/env."""
    empresa = empresa or {}
    raw = (
        str(empresa.get("email") or "").strip()
        or str(empresa.get("reporte_email_destinatarios") or "").strip()
        or str(Config.REPORT_EMAIL_TO or "").strip()
    )
    return parse_recipients(raw)


def report_schedule_timezone() -> ZoneInfo:
    tz_name = str(Config.REPORT_TIMEZONE or DEFAULT_REPORT_TIMEZONE).strip()
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_REPORT_TIMEZONE)


def weekly_report_skip_reason(empresa: dict[str, Any] | None = None) -> str | None:
    """None si corresponde enviar hoy; texto si debe omitirse (cron semanal)."""
    empresa = empresa or {}
    tz = report_schedule_timezone()
    now = datetime.now(tz)
    if now.weekday() != REPORT_WEEKDAY_MONDAY:
        return f"hoy no es lunes en {tz.key} ({now.strftime('%A %d/%m/%Y')})"
    hora_cfg = str(empresa.get("reporte_email_hora") or DEFAULT_REPORT_HOUR).strip() or DEFAULT_REPORT_HOUR
    try:
        hour = int(hora_cfg.split(":", 1)[0])
    except (TypeError, ValueError):
        hour = 5
    if now.hour != hour:
        return f"fuera de hora programada ({hora_cfg} {tz.key}, ahora {now.strftime('%H:%M')})"
    return None


def _email_summary_html(report: dict[str, Any]) -> str:
    """HTML liviano para el cuerpo del email (el PDF lleva el detalle)."""
    emp = report.get("empresa") or {}
    r = report.get("resumen") or {}
    op = report.get("operacional") or {}
    nombre = _esc(emp.get("razon_social") or "Empresa")
    fecha = _esc(report.get("fecha_legible") or "")
    bullets = report.get("resumen_ejecutivo") or []
    bullets_html = "".join(f"<li style='margin-bottom:6px'>{_esc(b)}</li>" for b in bullets[:4])
    return f"""<!DOCTYPE html><html><body style="font-family:system-ui,sans-serif;color:#1e293b;padding:20px;max-width:600px">
<h2 style="margin:0 0 8px">Informe Empresarial — {nombre}</h2>
<p style="color:#64748b;margin:0 0 16px">{fecha} · Salud: <strong>{_esc(r.get('estado_salud'))}</strong></p>
<ul style="font-size:14px;color:#334155;padding-left:18px;margin:0 0 18px">{bullets_html}</ul>
<table style="border-collapse:collapse;font-size:14px;width:100%">
<tr><td style="padding:6px 0;color:#64748b">Patrimonio neto</td><td style="text-align:right"><strong>{_fmt_money(r.get('patrimonio_neto'))}</strong></td></tr>
<tr><td style="padding:6px 0;color:#64748b">A cobrar</td><td style="text-align:right"><strong>{_fmt_money(r.get('total_a_cobrar'))}</strong></td></tr>
<tr><td style="padding:6px 0;color:#64748b">En mora</td><td style="text-align:right"><strong style="color:#dc2626">{_fmt_money(r.get('monto_en_mora'))}</strong></td></tr>
<tr><td style="padding:6px 0;color:#64748b">A pagar</td><td style="text-align:right"><strong>{_fmt_money(r.get('total_a_pagar_financiero'))}</strong></td></tr>
<tr><td style="padding:6px 0;color:#64748b">Ventas del mes</td><td style="text-align:right"><strong>{_fmt_money(op.get('ventas_mes'))}</strong></td></tr>
<tr><td style="padding:6px 0;color:#64748b">Caja real</td><td style="text-align:right"><strong>{_fmt_money(r.get('caja_real'))}</strong></td></tr>
</table>
<p style="margin-top:20px;color:#64748b;font-size:13px">Informe empresarial completo en PDF adjunto.</p>
</body></html>"""


def send_daily_report_email(report: dict[str, Any], recipients: list[str] | None = None) -> dict[str, Any]:
    empresa = report.get("empresa") or {}
    settings = _resolve_email_settings(empresa)
    if not settings["transport"]:
        return {
            "ok": False,
            "error": (
                "Email no configurado. Agregá tu Resend API key en el modal de email "
                "o definí RESEND_API_KEY en el servidor."
            ),
        }

    dest = recipients or get_report_recipients(empresa)
    if not dest:
        return {
            "ok": False,
            "error": "No hay destinatario. Completá el email en Datos de la Empresa.",
        }

    nombre = empresa.get("razon_social") or "Empresa"
    fecha = report.get("fecha_legible") or report.get("fecha") or ""
    subject = f"Informe Empresarial {nombre} — {fecha}"
    plain = f"Informe empresarial de {nombre} ({fecha}). Resumen ejecutivo y PDF adjunto."
    pdf_name = f"informe-empresarial-{fecha.replace('/', '-')}.pdf"

    try:
        pdf_bytes = render_daily_report_pdf(report)
        email_html = _email_summary_html(report)
    except Exception as e:
        return {"ok": False, "error": f"Error al generar informe: {e}"}

    try:
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
    op = report.get("operacional") or {}
    emp = (report.get("empresa") or {}).get("razon_social") or "Empresa"
    fecha = report.get("fecha_legible") or ""
    lines = [
        f"📊 *Informe Empresarial — {emp}*",
        f"📅 {fecha} · Salud: {r.get('estado_salud', '—')}",
        "",
        f"📈 *Patrimonio:* {_fmt_money(r.get('patrimonio_neto'))}",
        f"💰 *A cobrar:* {_fmt_money(r.get('total_a_cobrar'))} ({r.get('clientes_con_saldo', 0)} ctas)",
        f"🚨 *En mora:* {_fmt_money(r.get('monto_en_mora'))}",
        f"💳 *Deuda fin.:* {_fmt_money(r.get('total_a_pagar_financiero'))}",
        f"⏰ *Vencido:* {_fmt_money(r.get('total_a_pagar_vencido'))}",
        f"🏦 *Caja:* {_fmt_money(r.get('caja_real'))}",
        f"🥩 *Ventas mes:* {_fmt_money(op.get('ventas_mes'))} (margen {op.get('margen_pct_mes', 0)}%)",
        f"📦 *Stock:* {_fmt_kg(r.get('stock_kg'))}",
        "",
        "PDF empresarial completo en el panel del jefe.",
    ]
    return "\n".join(lines)
