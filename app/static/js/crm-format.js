/**
 * Formateo numérico, fechas y parseo de kg.
 */
(function (global) {
    function getEmpresaDatos() {
        const defaults = {
            nombre: 'Master Total',
            cuit: '30-12345678-9',
            direccion: 'Av. Juan B. Justo 1234, CABA',
            telefono: '+54 11 4567-8901',
            email: 'contacto@mastertotal.com',
            cotizacion_usd: 1000.0,
        };
        try {
            const raw = localStorage.getItem('empresa_datos');
            if (raw) return Object.assign(defaults, JSON.parse(raw));
        } catch (_) {}
        return defaults;
    }

    function parseNumLocal(v) {
        const n = parseFloat(v);
        return Number.isFinite(n) ? n : 0;
    }

    const fmt = n => Number(n).toLocaleString('es-AR', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    const fmtFecha = (iso, time = false) => {
        if (!iso) return '-';
        const d = new Date(iso);
        if (isNaN(d)) return iso;
        return d.toLocaleString('es-AR', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: time ? '2-digit' : undefined,
            minute: time ? '2-digit' : undefined,
        });
    };
    const fmtCompact = n => {
        if (n >= 1000000) {
            return (n / 1000000).toLocaleString('es-AR', { minimumFractionDigits: 0, maximumFractionDigits: 1 }) + 'M';
        }
        return fmt(n);
    };
    const fmtDual = n => {
        const emp = getEmpresaDatos();
        const cotiz = emp.cotizacion_usd || 1000.0;
        const ars = fmt(n);
        const usd = fmt(Math.round(Number(n) / cotiz));
        return `$${ars} <span class="dual-usd" title="Equivalente en USD (Cotización: $${cotiz})">(u$s ${usd})</span>`;
    };
    const fmtDualCompact = n => {
        const emp = getEmpresaDatos();
        const cotiz = emp.cotizacion_usd || 1000.0;
        const ars = fmtCompact(n);
        const usd = fmtCompact(Math.round(Number(n) / cotiz));
        return `$${ars} <span class="dual-usd">(u$s ${usd})</span>`;
    };
    const fmtPct = n => (n != null ? n.toFixed(2) + '%' : '—');

    function parseKgInput(val) {
        const s = String(val || '').replace(/,/g, '.').trim();
        if (/^\d+(?:\.\d+)?(?:\s*\+\s*\d+(?:\.\d+)?)+$/.test(s)) {
            const pesos_piezas = s.split('+').map(p => parseFloat(p.trim())).filter(n => !isNaN(n) && n > 0);
            const kg = Math.round(pesos_piezas.reduce((a, b) => a + b, 0) * 100) / 100;
            return { kg, pesos_piezas };
        }
        const kg = parseFloat(s);
        if (!isNaN(kg) && kg > 0) return { kg, pesos_piezas: [] };
        return { kg: 0, pesos_piezas: [] };
    }

    function csvCell(val) {
        const s = val == null ? '' : String(val);
        return '"' + s.replace(/"/g, '""') + '"';
    }

    global.CrmFormat = {
        parseNumLocal,
        fmt,
        fmtFecha,
        fmtCompact,
        fmtDual,
        fmtDualCompact,
        fmtPct,
        parseKgInput,
        csvCell,
        getEmpresaDatos,
    };
})(window);
