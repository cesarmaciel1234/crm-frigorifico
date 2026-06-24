/**
 * Toast, loading y utilidades de estado de remitos en UI.
 */
(function (global) {
    const $ = id => document.getElementById(id);

    let _remitosFullCache = null;
    let _remitosFullCacheAt = 0;
    const REMITOS_FULL_CACHE_MS = 60_000;

    function invalidateRemitosFullCache() {
        _remitosFullCache = null;
        _remitosFullCacheAt = 0;
    }

    function remitoEstado(pagado) {
        if (typeof pagado === 'string') {
            const s = pagado.toLowerCase();
            if (s === 'cobrado') return { label: 'Cobrado', badgeClass: 'badge-success', cobrable: false };
            if (s === 'incobrable') return { label: 'Incobrable', badgeClass: 'badge-neutral', cobrable: false };
            if (s === 'parcial') return { label: 'Parcial', badgeClass: 'badge-warning', cobrable: true };
            return { label: 'Pendiente', badgeClass: 'badge-danger', cobrable: true };
        }
        const p = Number(pagado ?? 0);
        if (p === 1) return { label: 'Cobrado', badgeClass: 'badge-success', cobrable: false };
        if (p === 2) return { label: 'Incobrable', badgeClass: 'badge-neutral', cobrable: false };
        return { label: 'Pendiente', badgeClass: 'badge-danger', cobrable: true };
    }

    function remitoSaldoPendiente(r) {
        const total = Number(r.precio_venta_total || 0);
        const pagado = Number(r.monto_pagado || 0);
        const isCobrado = (r.pagado ?? r.estado_cobro) === 'cobrado' || Number(r.pagado) === 1
            || (r.estado_cobro === 'incobrable' || Number(r.pagado) === 2);
        if (isCobrado) return 0;
        return Math.max(0, total - pagado);
    }

    function setLoading(on, texto) {
        const el = $('appLoader');
        el?.classList.toggle('active', !!on);
        if (el && texto) {
            const t = el.querySelector('.loader-text');
            if (t) t.textContent = texto;
        }
    }

    function toast(msg, err) {
        let t = $('toast');
        if (!t) {
            t = document.createElement('div');
            t.id = 'toast';
            t.className = 'toast';
            document.body.appendChild(t);
        }
        t.textContent = msg;
        t.className = 'toast show' + (err ? ' error' : '');
        clearTimeout(toast._t);
        toast._t = setTimeout(() => t.classList.remove('show'), 3500);
    }

    global.CrmUi = {
        remitoEstado,
        remitoSaldoPendiente,
        invalidateRemitosFullCache,
        toast,
        setLoading,
        REMITOS_FULL_CACHE_MS,
        getRemitosFullCache: () => _remitosFullCache,
        getRemitosFullCacheAt: () => _remitosFullCacheAt,
        setRemitosFullCache: (v, at) => {
            _remitosFullCache = v;
            _remitosFullCacheAt = at;
        },
    };
})(window);
