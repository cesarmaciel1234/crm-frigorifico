"""Informe ejecutivo diario para el panel del jefe."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.services.clientes import list_clientes
from app.services.finanzas import historial_vencimientos, panel_estrategia, ranking_enemigos
from app.services.remitos import list_remitos
from app.services.users import get_empresa_config


def _money(n: float | int | None) -> float:
    try:
        return round(float(n or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def build_daily_report(*, include_details: bool = True) -> dict[str, Any]:
    """Arma el briefing del día: cobrar, pagar, clientes, deudas y salud financiera."""
    estrategia = panel_estrategia()
    activo = estrategia.get("activo") or {}
    sangria = estrategia.get("sangria") or {}
    enemigos = ranking_enemigos()
    clientes = list_clientes()
    historial = historial_vencimientos()
    empresa = get_empresa_config()

    clientes_con_saldo = [c for c in clientes if _money(c.get("saldo_actual")) > 0]
    clientes_mora = [c for c in clientes if c.get("en_mora") and _money(c.get("saldo_actual")) > 0]
    inrecuperables = [c for c in clientes if c.get("inrecuperable")]

    total_cobrar = sum(_money(c.get("saldo_actual")) for c in clientes_con_saldo)
    total_mora = sum(_money(c.get("saldo_actual")) for c in clientes_mora)

    vencidos = [h for h in historial if h.get("vencido")]
    proximos = [h for h in historial if not h.get("vencido") and not h.get("completa")]

    obligaciones = [
        e for e in enemigos
        if not e.get("completa") and _money(e.get("saldo_pendiente") or e.get("pagar")) > 0
    ]

    total_pagar_fin = _money(activo.get("deuda_real"))
    total_pagar_comercial = _money(activo.get("deuda_comercial"))
    total_pagar_vencido = sum(_money(h.get("total_pagar")) for h in vencidos)

    hoy = date.today()
    result: dict[str, Any] = {
        "version": "informe_diario_v1",
        "fecha": hoy.isoformat(),
        "fecha_legible": hoy.strftime("%d/%m/%Y"),
        "generado_at": datetime.now(timezone.utc).isoformat(),
        "empresa": empresa,
        "resumen": {
            "total_a_cobrar": _money(total_cobrar),
            "clientes_con_saldo": len(clientes_con_saldo),
            "clientes_en_mora": len(clientes_mora),
            "monto_en_mora": _money(total_mora),
            "clientes_inrecuperables": len(inrecuperables),
            "total_a_pagar_financiero": total_pagar_fin,
            "total_a_pagar_comercial": total_pagar_comercial,
            "total_a_pagar_vencido": _money(total_pagar_vencido),
            "obligaciones_activas": len(obligaciones),
            "caja_real": _money(activo.get("caja_real")),
            "activo_total": _money(activo.get("activo_total")),
            "pasivo_total": _money(activo.get("pasivo_total")),
            "patrimonio_neto": _money(activo.get("patrimonio_neto")),
            "capital_neto": _money(activo.get("capital_neto")),
            "cuentas_por_cobrar": _money(activo.get("activo_pendiente")),
            "stock_kg": _money(activo.get("stock_kg")),
            "sangria_diaria": _money(sangria.get("sangria_diaria")),
            "estado_salud": activo.get("estado") or "—",
            "deudas_urgentes": sum(1 for e in obligaciones if e.get("urgente")),
        },
        "clientes_a_cobrar": sorted(
            clientes_con_saldo,
            key=lambda x: -_money(x.get("saldo_actual")),
        )[:40 if include_details else 20],
        "clientes_en_mora": sorted(
            clientes_mora,
            key=lambda x: -_money(x.get("saldo_actual")),
        )[:25],
        "obligaciones_a_pagar": sorted(
            obligaciones,
            key=lambda x: -_money(x.get("saldo_pendiente") or x.get("pagar")),
        )[:30 if include_details else 15],
    }
    if not include_details:
        return result

    result.update({
        "vencimientos_vencidos": sorted(
            vencidos,
            key=lambda x: -_money(x.get("total_pagar")),
        )[:25],
        "vencimientos_proximos": proximos[:15],
        "top_cfr": sorted(
            [e for e in enemigos if e.get("cfr") is not None],
            key=lambda x: -(x.get("cfr") or 0),
        )[:10],
        "remitos_recientes": list_remitos(12),
    })
    return result
