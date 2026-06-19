"""Auditoría matemática: deuda neta vs interés neto."""
from app.database import get_db
from app.services.finanzas import (
    _desglose_deuda_financiera,
    _deuda_pendiente_total,
    _saldo_pendiente_operacion,
    panel_activo,
    sangria_diaria,
)
from app.services.pagos import calc_estado_vencimiento, calc_plan_cuotas


def cuota_fija_desglose(recibido, pagar, cuotas_total, cuotas_pagadas):
    """Método industrial cuota fija: capital e interés lineales por cuota."""
    if pagar <= recibido or cuotas_total <= 0:
        restantes = max(0, cuotas_total - cuotas_pagadas)
        saldo = round((pagar / cuotas_total) * restantes, 2) if cuotas_total else pagar
        return saldo, saldo, 0.0
    cap_cuota = recibido / cuotas_total
    int_cuota = (pagar - recibido) / cuotas_total
    restantes = max(0, cuotas_total - cuotas_pagadas)
    return (
        round((pagar / cuotas_total) * restantes, 2),
        round(cap_cuota * restantes, 2),
        round(int_cuota * restantes, 2),
    )


def main():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, alias, tipo, recibido, pagar, meses, cuotas,
                   COALESCE(cuotas_pagadas, 0) AS cuotas_pagadas, fecha_vencimiento
            FROM operaciones_financieras
            ORDER BY id
            """
        ).fetchall()

    print("=" * 72)
    print("  AUDITORÍA DEUDA — operación por operación")
    print("=" * 72)

    sum_saldo = sum_cap_prop = sum_int_prop = 0.0
    sum_cap_cuota = sum_int_cuota = 0.0

    for row in rows:
        r = dict(row)
        tipo = (r["tipo"] or "").lower()
        if tipo == "proveedor":
            continue

        saldo = _saldo_pendiente_operacion(r)
        if saldo <= 0:
            continue

        recibido, pagar = float(r["recibido"]), float(r["pagar"])
        if tipo == "cheque" or pagar <= recibido:
            cap_p, int_p = saldo, 0.0
        else:
            ratio = recibido / pagar
            cap_p = saldo * ratio
            int_p = saldo * (1 - ratio)

        cuotas = r["cuotas"] or (1 if tipo in ("cheque",) else r["meses"])
        _, cap_c, int_c = cuota_fija_desglose(recibido, pagar, int(cuotas or 1), int(r["cuotas_pagadas"]))

        sum_saldo += saldo
        sum_cap_prop += cap_p
        sum_int_prop += int_p
        sum_cap_cuota += cap_c
        sum_int_cuota += int_c

        diff_cap = cap_p - cap_c
        print(f"\n#{r['id']} {r['alias']} ({tipo})")
        print(f"  Recibido ${recibido:,.2f} | Pagar ${pagar:,.2f} | Cuotas {r['cuotas_pagadas']}/{cuotas}")
        print(f"  Saldo pendiente ........ ${saldo:,.2f}")
        print(f"  Proporcional (actual) .. Cap ${cap_p:,.2f} + Int ${int_p:,.2f} = ${cap_p+int_p:,.2f}")
        print(f"  Cuota fija (industrial)  Cap ${cap_c:,.2f} + Int ${int_c:,.2f} = ${cap_c+int_c:,.2f}")
        if abs(diff_cap) > 0.05:
            print(f"  ⚠ Diferencia capital .... ${diff_cap:,.2f}")

    with get_db() as conn:
        _, _, deuda_real = _deuda_pendiente_total(conn)
        _, deuda_neta, interes_neto = _desglose_deuda_financiera(conn)

    a = panel_activo()
    s = sangria_diaria()

    print("\n" + "=" * 72)
    print("  TOTALES")
    print("=" * 72)
    print(f"  Suma saldos ops ........ ${sum_saldo:,.2f}")
    print(f"  deuda_real (sistema) ... ${deuda_real:,.2f}")
    print(f"  Proporcional capital ... ${sum_cap_prop:,.2f}")
    print(f"  Proporcional interés ... ${sum_int_prop:,.2f}")
    print(f"  Suma cap+int ........... ${sum_cap_prop + sum_int_prop:,.2f}")
    print(f"  Cuota fija capital ..... ${sum_cap_cuota:,.2f}")
    print(f"  Cuota fija interés ..... ${sum_int_cuota:,.2f}")
    print(f"  panel_activo deuda_neta  ${a['deuda_neta']:,.2f}")
    print(f"  panel_activo interes_net ${a['interes_neto']:,.2f}")
    print(f"  Cuadra cap+int=total? ... {abs(a['deuda_neta'] + a['interes_neto'] - a['deuda_real']) < 0.02}")
    print(f"  Cuadra con ops? ........ {abs(sum_saldo - deuda_real) < 0.02}")

    print("\n" + "=" * 72)
    print("  SANGRÍA — consistencia interés")
    print("=" * 72)
    print(f"  intereses_totales sangría ${s['intereses_totales']:,.2f}")
    print(f"  interes_neto pendiente ... ${a['interes_neto']:,.2f}")
    if abs(s["intereses_totales"] - a["interes_neto"]) > 1:
        print("  ⚠ Sangría usa interés NOMINAL total, no interés PENDIENTE")

    # Banco sin cuotas
    for row in rows:
        r = dict(row)
        if (r["tipo"] or "").lower() == "banco":
            plan = calc_plan_cuotas(r, calc_estado_vencimiento(None))
            print(f"\n  Préstamo '{r['alias']}': tiene_cuotas={plan['tiene_cuotas']}, saldo={plan['saldo_pendiente']}")


if __name__ == "__main__":
    main()
