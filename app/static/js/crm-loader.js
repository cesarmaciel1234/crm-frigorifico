/**
 * Carga de datos: caché local → API → render.
 */
(function (global) {
    const { ensureDataShape, tenantCacheGet, tenantCachePut } = global.CrmDb;
    const {
        api,
        countPendingOutbox,
        drainOutboxAll,
        dispararSyncInmediato,
        publishNodeBackupAfterSync,
    } = global.CrmApi;
    const bus = () => global.CrmBus;

    let _bgLoadAllPromise = null;

    function servidorTieneDatos(freshData) {
        if (!freshData) return false;
        const ops = freshData.enemigos?.length || 0;
        const cli = freshData.clientes?.length || 0;
        const rem = freshData.remitos?.length || 0;
        const bulk = freshData.bulk?.length || 0;
        return ops + cli + rem + bulk > 0;
    }

    async function loadAll(opts = {}) {
        if (opts.enSegundoPlano && _bgLoadAllPromise) {
            return _bgLoadAllPromise;
        }
        const run = () => loadAllCore(opts);
        if (opts.enSegundoPlano) {
            _bgLoadAllPromise = run().finally(() => { _bgLoadAllPromise = null; });
            return _bgLoadAllPromise;
        }
        return run();
    }

    async function loadAllCore(opts = {}) {
        const forzarServidor = !!opts.forzarServidor;
        const sincronizarOutbox = !!opts.sincronizarOutbox;
        const enSegundoPlano = !!opts.enSegundoPlano;
        const avisarSiVacio = opts.avisarSiVacio !== false;
        const bust = '_=' + Date.now();

        let loaderActivo = false;
        if (opts.bloquearUI === true) {
            bus().emit('setLoading', true, opts.textoCarga || 'Descargando datos…');
            loaderActivo = true;
        } else if (opts.bloquearUI !== false && !enSegundoPlano && (opts.mostrarExito || (forzarServidor && !bus().emit('getData')))) {
            bus().emit('setLoading', true, 'Cargando datos…');
            loaderActivo = true;
        }

        try {
            if (!forzarServidor || enSegundoPlano) {
                const cached = await tenantCacheGet('appData');
                if (cached) {
                    bus().emit('setData', ensureDataShape(cached));
                    bus().emit('safeRenderAll');
                    if (loaderActivo) {
                        bus().emit('setLoading', false);
                        loaderActivo = false;
                    }
                }
            }

            if (navigator.onLine && sincronizarOutbox) {
                try {
                    await Promise.race([
                        drainOutboxAll(),
                        new Promise((_, rej) => setTimeout(() => rej(new Error('outbox timeout')), 12000)),
                    ]);
                } catch (e) {
                    console.warn('Outbox drain omitido o lento:', e?.message || e);
                }
            }

            const data = bus().emit('getData');
            const pending = await countPendingOutbox();
            if (pending > 0 && data && !forzarServidor) {
                bus().emit('safeRenderAll');
                void global.CrmApi.actualizarUIOffline();
                if (navigator.onLine) void dispararSyncInmediato();
                return;
            }

            if (!navigator.onLine) {
                if (!data) {
                    const cached = await tenantCacheGet('appData');
                    if (cached) {
                        bus().emit('setData', ensureDataShape(cached));
                        bus().emit('safeRenderAll');
                    } else {
                        bus().emit('toast', 'Sin conexión y sin datos en este dispositivo', true);
                    }
                }
                global.CrmApi.actualizarUIOffline();
                return;
            }

            const settled = await Promise.allSettled([
                api('/api/dashboard?' + bust),
                api('/api/historial-pagos?' + bust),
                api('/api/bulk?' + bust),
                api('/api/clientes?' + bust),
                api('/api/auditoria?' + bust),
            ]);
            const labels = ['dashboard', 'historial-pagos', 'bulk', 'clientes', 'auditoria'];
            const pick = (i, fallback) => settled[i].status === 'fulfilled' ? settled[i].value : fallback;
            const dash = pick(0, null);
            if (!dash) {
                const err = settled[0].status === 'rejected' ? settled[0].reason : null;
                throw new Error((err && err.message) || 'Error al cargar el panel principal');
            }
            settled.forEach((res, i) => {
                if (res.status === 'rejected') console.warn('API ' + labels[i] + ' falló:', res.reason);
            });

            const cur = bus().emit('getData') || {};
            const freshData = {
                ...dash,
                historialPagos: pick(1, cur.historialPagos || []),
                bulk: pick(2, cur.bulk || []),
                clientes: pick(3, cur.clientes || []),
                auditoria: pick(4, cur.auditoria || []),
            };

            const shaped = ensureDataShape(freshData);
            bus().emit('setData', shaped);
            await tenantCachePut('appData', shaped);
            bus().emit('safeRenderAll');
            publishNodeBackupAfterSync();

            void (async () => {
                try {
                    const delta = await global.CrmSync?.pullDeltaLight?.();
                    if (delta?.changes?.length) {
                        const refreshed = await tenantCacheGet('appData');
                        if (refreshed) {
                            bus().emit('setData', ensureDataShape(refreshed));
                            bus().emit('renderAll');
                        }
                    }
                } catch (_) {}
            })();

            if (avisarSiVacio && !servidorTieneDatos(freshData)) {
                try {
                    const nube = await api('/api/nube/resumen?_=' + Date.now());
                    const sessionUser = bus().emit('getSessionUser') || {};
                    const cuenta = nube.empresa_nombre || sessionUser.empresa_nombre || sessionUser.username || 'esta empresa';
                    bus().emit('toast', 'La nube está vacía para ' + cuenta + '. Subí tu backup .json en Backup / Nube.', true);
                } catch (_) {
                    bus().emit('toast', 'La nube parece vacía para esta empresa. Subí el backup .json en Backup / Nube.', true);
                }
            } else if (opts.mostrarExito) {
                bus().emit('toast', 'Datos descargados de la nube correctamente');
            }
        } catch (e) {
            console.warn('Error al cargar del servidor.', e);
            if (forzarServidor || !bus().emit('getData')) {
                bus().emit('toast', 'No se pudieron descargar los datos: ' + (e.message || 'revisá tu conexión'), true);
            }
        } finally {
            if (loaderActivo) bus().emit('setLoading', false);
        }
    }

    async function descargarDatosDeLaNube() {
        bus().emit('toast', 'Descargando datos del servidor...');
        await loadAll({
            forzarServidor: true,
            sincronizarOutbox: true,
            bloquearUI: true,
            textoCarga: 'Descargando datos de la nube…',
            avisarSiVacio: true,
            mostrarExito: true,
        });
    }

    async function runBoot() {
        const { clearTenantCaches, tenantCacheGet } = global.CrmDb;
        const sessionFromServer = await bus().emit('loadSession');
        const prevUser = localStorage.getItem('sync_user') || '';
        const prevEmpresa = localStorage.getItem('sync_empresa') || '';
        const sessionUser = bus().emit('getSessionUser') || {};
        const curUser = sessionUser.username || '';
        const curEmpresa = String(sessionUser.empresa_id || '1');
        const usuarioCambio = sessionFromServer && prevUser && prevUser !== curUser;
        const empresaCambio = sessionFromServer && prevEmpresa && prevEmpresa !== curEmpresa;

        if (usuarioCambio || empresaCambio) {
            await clearTenantCaches();
        }
        if (curUser) localStorage.setItem('sync_user', curUser);
        if (curEmpresa) localStorage.setItem('sync_empresa', curEmpresa);

        const cached = await tenantCacheGet('appData');
        const cuentaNueva = !cached || usuarioCambio || empresaCambio;

        if (cached && !cuentaNueva) {
            bus().emit('setData', ensureDataShape(cached));
            bus().emit('safeRenderAll');
            global.CrmApi.actualizarUIOffline();
        }

        if (new URLSearchParams(window.location.search).get('view')) {
            bus().emit('applyPwaDeepLink');
        } else {
            bus().emit('switchView', 'home');
        }

        void loadAll({
            forzarServidor: cuentaNueva,
            bloquearUI: cuentaNueva,
            avisarSiVacio: cuentaNueva,
            enSegundoPlano: !cuentaNueva,
        });

        if (navigator.onLine) {
            void dispararSyncInmediato();
        }
    }

    global.CrmLoader = {
        loadAll,
        loadAllCore,
        descargarDatosDeLaNube,
        servidorTieneDatos,
        runBoot,
    };
})(window);
