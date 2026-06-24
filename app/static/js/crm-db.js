/**
 * Dexie + caché local por tenant.
 */
(function (global) {
    const db = new Dexie('CarniceriaContableDB');
    db.version(3).stores({
        transacciones: '++id, uuid, tipo, monto, fecha, status, updated_at',
        cache: 'key, updated_at',
        solicitudes_pendientes: '++id, url, method, body, created_at',
    });
    db.version(4).stores({
        transacciones: '++id, uuid, tipo, monto, fecha, status, updated_at',
        cache: 'key, updated_at',
        solicitudes_pendientes: '++id, url, method, body, created_at',
        pending_sync: '++local_id, op_id, entity, entity_uuid, status, updated_at_utc, device_id',
        sync_meta: 'key',
    });

    let empresaIdResolver = () => 1;

    function setEmpresaIdResolver(fn) {
        empresaIdResolver = fn;
    }

    function tenantCacheKey(name) {
        const eid = empresaIdResolver() || 1;
        return name + ':' + eid;
    }

    function emptyAppData() {
        return {
            enemigos: [],
            remitos: [],
            estrategia: {},
            bancos: [],
            historial: [],
            historialPagos: [],
            bulk: [],
            clientes: [],
            auditoria: [],
            usuarios: [],
            metricas_flotantes: {},
            totales: {},
        };
    }

    function ensureDataShape(d) {
        const src = d && typeof d === 'object' ? d : {};
        return {
            ...src,
            enemigos: src.enemigos || [],
            remitos: src.remitos || [],
            estrategia: src.estrategia || {},
            bancos: src.bancos || [],
            historial: src.historial || [],
            historialPagos: src.historialPagos || [],
            bulk: src.bulk || [],
            clientes: src.clientes || [],
            auditoria: src.auditoria || [],
            usuarios: src.usuarios || [],
            metricas_flotantes: src.metricas_flotantes || {},
            totales: src.totales || {},
        };
    }

    async function tenantCacheGet(name) {
        const row = await db.cache.get(tenantCacheKey(name));
        return row?.data ?? null;
    }

    async function tenantCacheGetEntry(name) {
        return db.cache.get(tenantCacheKey(name));
    }

    async function tenantCachePut(name, payload) {
        await db.cache.put({ key: tenantCacheKey(name), data: payload, updated_at: Date.now() });
    }

    async function clearTenantCaches() {
        await Promise.all([
            db.cache.clear(),
            db.transacciones.clear(),
            db.solicitudes_pendientes.clear(),
        ]);
        try { await db.pending_sync.clear(); } catch (_) {}
    }

    global.CrmDb = {
        db,
        setEmpresaIdResolver,
        tenantCacheKey,
        emptyAppData,
        ensureDataShape,
        tenantCacheGet,
        tenantCacheGetEntry,
        tenantCachePut,
        clearTenantCaches,
    };
})(window);
