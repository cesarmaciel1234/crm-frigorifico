/**
 * Cliente API + colas offline (outbox legacy + pending_sync).
 */
(function (global) {
    const { db } = global.CrmDb;
    const bus = () => global.CrmBus;
    const MODO_PRUEBA = false;

    let _syncFlushPromise = null;

    function fetchApi(url, opts) {
        return (global.CrmSafe?.apiFetch || fetch)(url, opts);
    }

    async function countPendingOutbox() {
        let pendingSync = 0;
        try {
            pendingSync = await db.pending_sync
                .where('status').anyOf(['pending', 'pushing']).count();
        } catch (_) {}
        const solicitudesRows = await db.solicitudes_pendientes.toArray();
        const solicitudes = solicitudesRows.filter(s => !s.failed).length;
        const transacciones = await db.transacciones.where('status').equals(0).count();
        return pendingSync + solicitudes + transacciones;
    }

    function buildSyncOpFromRequest(url, method, body) {
        if (!body || method === 'GET') return null;
        let parsed = {};
        try { parsed = JSON.parse(body); } catch (_) { return null; }
        const ts = new Date().toISOString();
        const uuid = parsed.uuid || crypto.randomUUID();

        if (url.includes('/api/operaciones') && method === 'POST') {
            return {
                entity: 'operacion',
                entity_uuid: uuid,
                action: 'CREATE',
                payload: { ...parsed, uuid, updated_at_utc: ts },
            };
        }
        if (url.includes('/api/clientes') && method === 'POST'
            && !url.includes('/cobrar') && !url.includes('/saldo-inicial') && !url.includes('/incobrable')) {
            return {
                entity: 'cliente',
                entity_uuid: uuid,
                action: 'CREATE',
                payload: { ...parsed, uuid, updated_at_utc: ts },
            };
        }
        if (url.includes('/api/clientes/') && method === 'PUT') {
            return {
                entity: 'cliente',
                entity_uuid: parsed.uuid || uuid,
                action: 'UPDATE',
                payload: { ...parsed, uuid: parsed.uuid || uuid, updated_at_utc: ts },
            };
        }
        if (url.includes('/api/operaciones/') && method === 'DELETE') {
            const m = url.match(/\/api\/operaciones\/([^/?]+)/);
            const entity_uuid = parsed.uuid || m?.[1] || uuid;
            return {
                entity: 'operacion',
                entity_uuid: String(entity_uuid),
                action: 'DELETE',
                payload: { uuid: entity_uuid, updated_at_utc: ts },
            };
        }
        return null;
    }

    async function intentarSincronizar(opts) {
        opts = opts || {};
        if (!navigator.onLine) return { syncCount: 0 };

        const pendientes = await db.transacciones.where('status').equals(0).toArray();
        let syncCount = 0;
        for (const item of pendientes) {
            try {
                let responseOk = false;
                let responseStatus = 0;
                let response = null;

                if (MODO_PRUEBA) {
                    await new Promise(r => setTimeout(r, 300));
                    responseOk = true;
                } else {
                    const { id, status, updated_at, last_error, ...datos } = item;
                    response = await fetchApi('/api/operaciones', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'same-origin',
                        body: JSON.stringify({ ...datos, uuid: item.uuid }),
                    });
                    responseOk = response.ok;
                    responseStatus = response.status;
                }

                if (responseOk) {
                    await db.transacciones.update(item.id, { status: 1 });
                    syncCount++;
                } else if (response) {
                    const d = await response.json().catch(() => ({}));
                    if (responseStatus >= 400 && responseStatus < 500) {
                        await db.transacciones.update(item.id, {
                            status: 2,
                            last_error: d.error || response.statusText,
                        });
                        continue;
                    }
                    break;
                }
            } catch (_) {
                break;
            }
        }
        if (syncCount > 0 && !opts.skipReload) {
            bus().emit('toast', `Sincronizados ${syncCount} registros pendientes`);
            bus().emit('loadAll', { forzarServidor: true, enSegundoPlano: true, bloquearUI: false, avisarSiVacio: false });
        }
        void actualizarUIOffline();
        return { syncCount };
    }

    async function drainSolicitudesPendientes() {
        if (!navigator.onLine) return { exito: 0, fallo: 0 };
        const pendientes = await db.solicitudes_pendientes.orderBy('id').toArray();
        let exitoCount = 0;
        let falloCount = 0;

        for (const item of pendientes) {
            try {
                const response = await fetchApi(item.url, {
                    method: item.method,
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: item.body,
                });

                if (response.ok) {
                    await db.solicitudes_pendientes.delete(item.id);
                    exitoCount++;
                } else if (response.status >= 500 || response.status === 408) {
                    break;
                } else {
                    const d = await response.json().catch(() => ({}));
                    await db.solicitudes_pendientes.update(item.id, {
                        failed: true,
                        last_status: response.status,
                        last_error: d.error || response.statusText,
                    });
                    falloCount++;
                }
            } catch (_) {
                break;
            }
        }
        return { exito: exitoCount, fallo: falloCount };
    }

    async function drainOutboxAll() {
        if (global.CrmSync?.drainPendingSync) {
            try {
                await global.CrmSync.drainPendingSync();
            } catch (e) {
                console.warn('pending_sync drain:', e);
            }
        }
        await intentarSincronizar({ skipReload: true });
        return drainSolicitudesPendientes();
    }

    async function dispararSyncInmediato(refreshAfter = true) {
        if (!navigator.onLine) {
            void actualizarUIOffline();
            return;
        }
        if (_syncFlushPromise) return _syncFlushPromise;
        _syncFlushPromise = (async () => {
            try {
                await Promise.race([
                    drainOutboxAll(),
                    new Promise(resolve => setTimeout(resolve, 12000)),
                ]);
                await intentarSincronizar({ skipReload: true });
                const pending = await countPendingOutbox();
                if (pending === 0 && refreshAfter) {
                    await bus().emit('loadAll', {
                        forzarServidor: true,
                        enSegundoPlano: true,
                        bloquearUI: false,
                        avisarSiVacio: false,
                    });
                }
            } catch (e) {
                console.warn('sync inmediato:', e);
            } finally {
                void actualizarUIOffline();
                _syncFlushPromise = null;
            }
        })();
        return _syncFlushPromise;
    }

    async function actualizarUIOffline() {
        const count = await countPendingOutbox();
        const badge = document.getElementById('offlineBadge');
        const cntSpan = document.getElementById('offlinePendingCount');
        const greeting = document.querySelector('.topbar-greeting-block');
        const weather = document.getElementById('weatherWidget');
        const isMobile = window.innerWidth <= 768;

        if (badge && cntSpan) {
            if (count > 0 || !navigator.onLine) {
                badge.style.display = 'inline-flex';
                cntSpan.textContent = count;
                if (!navigator.onLine) {
                    badge.style.backgroundColor = '#fee2e2';
                    badge.style.color = '#991b1b';
                    badge.title = `Sin conexión a Internet. ${count} acciones pendientes de subir.`;
                } else {
                    badge.style.backgroundColor = '#fef3c7';
                    badge.style.color = '#92400e';
                    badge.title = `${count} acciones pendientes de subir al servidor. Haz clic para sincronizar ahora.`;
                }
                if (isMobile) {
                    if (greeting) greeting.style.display = 'none';
                    if (weather) weather.style.display = 'none';
                }
            } else {
                badge.style.display = 'none';
                if (greeting) greeting.style.display = '';
                if (weather) weather.style.display = 'inline-flex';
            }
        }
    }

    async function sincronizarSolicitudesPendientes() {
        if (!navigator.onLine) return;
        const antes = await countPendingOutbox();
        try {
            await drainOutboxAll();
        } catch (e) {
            console.warn('sync manual:', e);
        }
        const pending = await countPendingOutbox();
        if (pending === 0) {
            await bus().emit('loadAll', { forzarServidor: true, enSegundoPlano: true, bloquearUI: false });
            if (antes > 0) bus().emit('toast', 'Datos sincronizados con la nube');
        } else {
            actualizarUIOffline();
            if (antes > pending) {
                bus().emit('toast', `Quedan ${pending} cambios por sincronizar`, true);
            }
        }
    }

    async function api(url, opts) {
        opts = opts || {};
        opts.credentials = 'same-origin';
        opts.headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});

        const method = (opts.method || 'GET').toUpperCase();
        const isGet = method === 'GET';

        try {
            const r = await fetchApi(url, opts);
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.error || d.message || 'Error en la operación');
            if (!isGet && navigator.onLine) {
                void dispararSyncInmediato();
            }
            return d;
        } catch (error) {
            if (isGet) throw error;

            const isNetworkError = !navigator.onLine
                || error.message.includes('Failed to fetch')
                || error.message.includes('NetworkError')
                || error.message.includes('network error');
            if (isNetworkError) {
                const syncOp = buildSyncOpFromRequest(url, method, opts.body);
                if (syncOp && global.CrmSync?.enqueueChange) {
                    await global.CrmSync.enqueueChange(syncOp);
                } else {
                    await db.solicitudes_pendientes.add({
                        url,
                        method,
                        body: opts.body || null,
                        created_at: new Date().toISOString(),
                    });
                }

                bus().emit('toast', '⚠️ Sin conexión. Guardado localmente — se sincroniza al volver internet.', false);
                await bus().emit('aplicarCambioOptimista', url, method, opts.body);
                void actualizarUIOffline();
                void dispararSyncInmediato(false);
                return { ok: true, offline: true, message: 'Operación guardada localmente' };
            }
            throw error;
        }
    }

    function publishNodeBackupAfterSync() {
        const data = bus().emit('getData');
        if (global.CrmSync && data && navigator.onLine) {
            void global.CrmSync.pushNode(data).catch(() => {});
        }
    }

    global.CrmApi = {
        api,
        countPendingOutbox,
        buildSyncOpFromRequest,
        intentarSincronizar,
        drainSolicitudesPendientes,
        drainOutboxAll,
        dispararSyncInmediato,
        actualizarUIOffline,
        sincronizarSolicitudesPendientes,
        publishNodeBackupAfterSync,
    };
})(window);
