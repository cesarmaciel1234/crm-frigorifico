/**
 * POS offline — IndexedDB (Dexie) + cola de sincronización.
 * Funciona sin red; al volver online envía ventas al servidor.
 */
const posDb = new Dexie('CrmFrigoPOS');
posDb.version(1).stores({
    ventas: '++id, producto, monto, tipo_pago, synced, created_at',
    dashboard_cache: 'key',
});

async function guardarVentaOffline({ producto, monto, tipo_pago }) {
    const id = await posDb.ventas.add({
        producto: producto.trim(),
        monto: Number(monto),
        tipo_pago,
        synced: 0,
        created_at: new Date().toISOString(),
    });
    if (navigator.onLine) {
        await sincronizarVentas();
    }
    return id;
}

async function listarVentasLocales() {
    return posDb.ventas.orderBy('id').reverse().toArray();
}

async function contarPendientesSync() {
    return posDb.ventas.where('synced').equals(0).count();
}

async function sincronizarVentas() {
    if (!navigator.onLine) return { ok: false, reason: 'offline' };

    const pendientes = await posDb.ventas.where('synced').equals(0).toArray();
    if (!pendientes.length) return { ok: true, synced: 0 };

    const apiBase = (window.CRM_CONFIG && window.CRM_CONFIG.apiBase) || '';
    const res = await (window.CrmSafe?.apiFetch || fetch)(`${apiBase}/api/ventas_mostrador/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
            ventas: pendientes.map((v) => ({
                offline_id: v.id,
                producto: v.producto,
                monto: v.monto,
                tipo_pago: v.tipo_pago,
                fecha: v.created_at.slice(0, 10),
            })),
        }),
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Error al sincronizar');
    }

    const data = await res.json();
    for (const id of data.synced_ids || []) {
        await posDb.ventas.update(id, { synced: 1 });
    }
    return { ok: true, synced: (data.synced_ids || []).length };
}

async function cachearDashboard(data) {
    await posDb.dashboard_cache.put({ key: 'last', data, saved_at: new Date().toISOString() });
}

async function leerDashboardCache() {
    return posDb.dashboard_cache.get('last');
}

window.PosOffline = {
    guardarVentaOffline,
    listarVentasLocales,
    contarPendientesSync,
    sincronizarVentas,
    cachearDashboard,
    leerDashboardCache,
};
