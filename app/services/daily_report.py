"""Informe ejecutivo empresarial para el panel del jefe."""
from __future__ import annotations

import copy
import time
from datetime import date, datetime, timezone
from typing import Any

from app.database import get_db
from app.services.clientes import list_clientes
from app.services.finanzas import (
    calcular_antiguedad_deuda,
    historial_vencimientos,
    panel_estrategia,
    ranking_enemigos,
)
from app.services.remitos import list_remitos
from app.services.users import get_empresa_config

_REPORT_CACHE: dict[tuple[int, bool], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 45.0


def _money(n: float | int | None) -> float:
    try:
        return round(float(n or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _report_cache_key(include_details: bool) -> tuple[int, bool]:
    empresa_id = 1
    try:
        from flask import has_request_context, session

        if has_request_context():
            empresa_id = int(session.get("empresa_id") or 1)
    except ImportError:
        pass
    return (empresa_id, include_details)


def invalidate_daily_report_cache() -> None:
    _REPORT_CACHE.clear()


def _metricas_operacionales() -> dict[str, Any]:
    hoy = date.today()
    mes_ini = hoy.replace(day=1).isoformat()
    hoy_s = hoy.isoformat()
    with get_db() as conn:
        dia = conn.execute(
            """
            SELECT COALESCE(SUM(precio_venta_total), 0), COALESCE(SUM(kg), 0), COUNT(*)
            FROM remitos_carga WHERE fecha = ?
            """,
            (hoy_s,),
        ).fetchone()
        mes = conn.execute(
            """
            SELECT COALESCE(SUM(precio_venta_total), 0), COALESCE(SUM(kg), 0), COUNT(*),
                   COALESCE(SUM(precio_venta_total - costo_carne - costo_total_logistica), 0)
            FROM remitos_carga WHERE fecha >= ?
            """,
            (mes_ini,),
        ).fetchone()
        cobros = conn.execute(
            """
            SELECT COALESCE(SUM(monto_pagado), 0), COUNT(*)
            FROM pagos_cuotas WHERE fecha_pago = ?
            """,
            (hoy_s,),
        ).fetchone()
        stock = conn.execute(
            """
            SELECT COALESCE(SUM(kg_remanentes), 0), COUNT(*)
            FROM compras_bulk WHERE kg_remanentes > 0
            """
        ).fetchone()

    ventas_mes = float(mes[0])
    margen_mes = float(mes[3])
    return {
        "ventas_hoy": _money(dia[0]),
        "kg_hoy": _money(dia[1]),
        "remitos_hoy": int(dia[2] or 0),
        "ventas_mes": _money(ventas_mes),
        "kg_mes": _money(mes[1]),
        "remitos_mes": int(mes[2] or 0),
        "margen_neto_mes": _money(margen_mes),
        "margen_pct_mes": round(margen_mes / ventas_mes * 100, 1) if ventas_mes > 0 else 0.0,
        "cobros_hoy": _money(cobros[0]),
        "pagos_registrados_hoy": int(cobros[1] or 0),
        "lotes_stock_activos": int(stock[1] or 0),
        "kg_stock_disponible": _money(stock[0]),
    }


def _distribucion_scoring(clientes: list[dict]) -> dict[str, int]:
    out = {"A": 0, "B": 0, "C": 0, "D": 0, "otros": 0}
    for c in clientes:
        s = str(c.get("scoring") or "").upper()
        if s in out:
            out[s] += 1
        else:
            out["otros"] += 1
    return out


def _build_resumen_ejecutivo(
    resumen: dict[str, Any],
    operacional: dict[str, Any],
    proyeccion: dict[str, Any],
    antiguedad: dict[str, Any],
) -> list[str]:
    bullets: list[str] = []
    estado = resumen.get("estado_salud") or "—"
    bullets.append(
        f"Salud financiera: {estado}. Patrimonio neto "
        f"${resumen.get('patrimonio_neto', 0):,.0f} "
        f"(activos ${resumen.get('activo_total', 0):,.0f} · pasivos ${resumen.get('pasivo_total', 0):,.0f})."
        .replace(",", ".")
    )
    bullets.append(
        f"Cartera comercial: ${resumen.get('total_a_cobrar', 0):,.0f} pendientes en "
        f"{resumen.get('clientes_con_saldo', 0)} cuentas; "
        f"${resumen.get('monto_en_mora', 0):,.0f} en mora ({resumen.get('clientes_en_mora', 0)} clientes)."
        .replace(",", ".")
    )
    aging = antiguedad.get("totales") or {}
    bullets.append(
        "Antigüedad de deuda: "
        f"0-30d ${aging.get('0_30', 0):,.0f} · "
        f"31-60d ${aging.get('31_60', 0):,.0f} · "
        f"61-90d ${aging.get('61_90', 0):,.0f} · "
        f"+90d ${aging.get('90_plus', 0):,.0f}."
        .replace(",", ".")
    )
    bullets.append(
        f"Obligaciones: ${resumen.get('total_a_pagar_financiero', 0):,.0f} financieras · "
        f"${resumen.get('total_a_pagar_comercial', 0):,.0f} comerciales · "
        f"${resumen.get('total_a_pagar_vencido', 0):,.0f} vencidas hoy."
        .replace(",", ".")
    )
    bullets.append(
        f"Operación del mes: ${operacional.get('ventas_mes', 0):,.0f} en ventas "
        f"({operacional.get('remitos_mes', 0)} remitos, {operacional.get('kg_mes', 0):,.0f} kg) · "
        f"margen neto {operacional.get('margen_pct_mes', 0)}%."
        .replace(",", ".")
    )
    meta = proyeccion.get("meta_texto") or "—"
    if meta not in ("SIN DEUDA", "—"):
        bullets.append(
            f"Proyección de liberación de deuda: {meta} meses "
            f"(excedente mensual estimado ${proyeccion.get('excedente_mensual', 0):,.0f})."
            .replace(",", ".")
        )
    elif meta == "SIN DEUDA":
        bullets.append("Sin deuda financiera activa registrada.")
    return bullets


def build_daily_report(*, include_details: bool = True) -> dict[str, Any]:
    """Arma el informe empresarial: balance, cobranza, operaciones y riesgos."""
    cache_key = _report_cache_key(include_details)
    now = time.monotonic()
    cached = _REPORT_CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return copy.deepcopy(cached[1])

    estrategia = panel_estrategia()
    activo = estrategia.get("activo") or {}
    sangria = estrategia.get("sangria") or {}
    proyeccion = estrategia.get("proyeccion") or {}
    enemigos = ranking_enemigos()
    clientes = list_clientes(solo_con_saldo=True)
    historial = historial_vencimientos(enemigos)
    antiguedad = calcular_antiguedad_deuda()
    operacional = _metricas_operacionales()
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
    resumen: dict[str, Any] = {
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
        "stock_valorizado": _money(activo.get("activo_mercaderia")),
        "sangria_diaria": _money(sangria.get("sangria_diaria")),
        "sangria_financiera": _money(sangria.get("sangria_financiera_diaria")),
        "sangria_cheques": _money(sangria.get("sangria_cheques_diaria")),
        "intereses_financieros": _money(sangria.get("intereses_totales")),
        "estado_salud": activo.get("estado") or "—",
        "deudas_urgentes": sum(1 for e in obligaciones if e.get("urgente")),
        "ciclo_cobro_dias": activo.get("ciclo_cobro_dias"),
        "ciclo_pago_dias": activo.get("ciclo_pago_dias"),
        "ganancia_acumulada": _money(activo.get("ganancia_acumulada")),
        "ganancia_pendiente": _money(activo.get("ganancia_pendiente")),
    }

    balance = {
        "activos": {
            "caja_real": resumen["caja_real"],
            "cuentas_por_cobrar": resumen["cuentas_por_cobrar"],
            "inventario": resumen["stock_valorizado"],
            "total": resumen["activo_total"],
        },
        "pasivos": {
            "deuda_financiera": total_pagar_fin,
            "deuda_comercial": total_pagar_comercial,
            "total": resumen["pasivo_total"],
        },
        "patrimonio": resumen["patrimonio_neto"],
    }

    lim_cli = 40 if include_details else 20
    lim_obl = 30 if include_details else 15

    result: dict[str, Any] = {
        "version": "informe_empresarial_v2",
        "fecha": hoy.isoformat(),
        "fecha_legible": hoy.strftime("%d/%m/%Y"),
        "generado_at": datetime.now(timezone.utc).isoformat(),
        "empresa": empresa,
        "resumen": resumen,
        "balance": balance,
        "proyeccion": {
            "deuda_total": proyeccion.get("deuda_total"),
            "margen_mensual_est": proyeccion.get("margen_mensual_est"),
            "carga_financiera_mensual": proyeccion.get("carga_financiera_mensual"),
            "excedente_mensual": proyeccion.get("excedente_mensual"),
            "meses_liberacion": proyeccion.get("meses_liberacion"),
            "meta_texto": proyeccion.get("meta_texto"),
        },
        "operacional": operacional,
        "antiguedad": {
            "totales": antiguedad.get("totales") or {},
            "top_mora": sorted(
                [c for c in (antiguedad.get("clientes") or []) if _money(c.get("saldo_actual")) > 0],
                key=lambda x: -_money((x.get("buckets") or {}).get("90_plus", 0)),
            )[:10 if include_details else 5],
        },
        "scoring_distribucion": _distribucion_scoring(clientes_con_saldo),
        "resumen_ejecutivo": _build_resumen_ejecutivo(resumen, operacional, proyeccion, antiguedad),
        "clientes_a_cobrar": sorted(
            clientes_con_saldo,
            key=lambda x: -_money(x.get("saldo_actual")),
        )[:lim_cli],
        "clientes_en_mora": sorted(
            clientes_mora,
            key=lambda x: -_money(x.get("saldo_actual")),
        )[:25],
        "obligaciones_a_pagar": sorted(
            obligaciones,
            key=lambda x: -_money(x.get("saldo_pendiente") or x.get("pagar")),
        )[:lim_obl],
        "vencimientos_vencidos": sorted(
            vencidos,
            key=lambda x: -_money(x.get("total_pagar")),
        )[:15 if include_details else 8],
        "vencimientos_proximos": proximos[:12 if include_details else 6],
        "top_cfr": sorted(
            [e for e in enemigos if e.get("cfr") is not None],
            key=lambda x: -(x.get("cfr") or 0),
        )[:10 if include_details else 5],
        "remitos_recientes": list_remitos(15 if include_details else 8),
    }

    _REPORT_CACHE[cache_key] = (now, copy.deepcopy(result))
    return result
