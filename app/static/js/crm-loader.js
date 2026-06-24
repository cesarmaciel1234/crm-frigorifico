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
    let _signalSource = null;
    let _signalDebounceTimer = null;

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

    async function fetchDashboardBundle(bust, timeoutMs) {
        const requests = Promise.allSettled([
            api('/api/dashboard?' + bust),
            api('/api/historial-pagos?' + bust),
            api('/api/bulk?' + bust),
            api('/api/clientes?' + bust),
            api('/api/auditoria?' + bust),
        ]);
        if (!timeoutMs) return requests;
        return Promise.race([
            requests,
            new Promise((_, rej) => setTimeout(
                () => rej(new Error('Tiempo de espera agotado al contactar el servidor')),
                timeoutMs,
            )),
        ]);
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
                if (opts.manualRefresh) {
                    bus().emit('toast', 'Sincronizando cambios locales antes de actualizar…');
                }
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

            const settled = await fetchDashboardBundle(
                bust,
                forzarServidor ? 25000 : 0,
            );
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
            if (forzarServidor || opts.manualRefresh || !bus().emit('getData')) {
                bus().emit('toast', 'No se pudieron descargar los datos: ' + (e.message || 'revisá tu conexión'), true);
            }
            throw e;
        } finally {
            if (loaderActivo) bus().emit('setLoading', false);
        }
    }

    async function refrescarManual() {
        bus().emit('toast', 'Actualizando datos…');
        let loaderActivo = false;
        const loaderTimer = setTimeout(() => {
            bus().emit('setLoading', true, 'Actualizando datos…');
            loaderActivo = true;
        }, 350);
        try {
            await loadAll({
                forzarServidor: true,
                sincronizarOutbox: true,
                bloquearUI: false,
                avisarSiVacio: false,
                manualRefresh: true,
            });
            bus().emit('toast', 'Panel actualizado');
            return true;
        } catch (_) {
            return false;
        } finally {
            clearTimeout(loaderTimer);
            if (loaderActivo) bus().emit('setLoading', false);
        }
    }

    async function descargarDatosDeLaNube() {
        bus().emit('toast', 'Descargando datos del servidor…');
        let loaderActivo = false;
        const loaderTimer = setTimeout(() => {
            bus().emit('setLoading', true, 'Descargando datos de la nube…');
            loaderActivo = true;
        }, 350);
        try {
            await loadAll({
                forzarServidor: true,
                sincronizarOutbox: true,
                bloquearUI: false,
                avisarSiVacio: true,
                mostrarExito: true,
                manualRefresh: true,
            });
        } finally {
            clearTimeout(loaderTimer);
            if (loaderActivo) bus().emit('setLoading', false);
        }
    }

    function teardownSignalListener() {
        if (_signalDebounceTimer) {
            clearTimeout(_signalDebounceTimer);
            _signalDebounceTimer = null;
        }
        if (_signalSource) {
            try { _signalSource.close(); } catch (_) {}
            _signalSource = null;
        }
    }

    function initSignalListener() {
        if (!global.EventSource || !navigator.onLine) return;
        teardownSignalListener();

        const deviceId = global.CrmSync?.deviceId || '';
        _signalSource = new EventSource('/api/stream');

        _signalSource.addEventListener('refrescar', (ev) => {
            try {
                const payload = JSON.parse(ev.data || '{}');
                if (payload.source_device_id && payload.source_device_id === deviceId) return;
            } catch (_) {}
            if (_signalDebounceTimer) clearTimeout(_signalDebounceTimer);
            _signalDebounceTimer = setTimeout(() => {
                void loadAll({
                    enSegundoPlano: true,
                    bloquearUI: false,
                    avisarSiVacio: false,
                });
            }, 300);
        });

        _signalSource.onerror = () => {
            // EventSource reconecta automáticamente; no bloquear la UI.
        };
    }

    async function finishBootInBackground(hadCacheOnStart) {
        const { clearTenantCaches, tenantCacheGet } = global.CrmDb;
        const prevUser = localStorage.getItem('sync_user') || '';
        const prevEmpresa = localStorage.getItem('sync_empresa') || '';

        const sessionFromServer = await bus().emit('loadSession');
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

        if (cuentaNueva && !hadCacheOnStart) {
            bus().emit('setLoading', true, 'Cargando datos…');
        }

        try {
            await loadAll({
                forzarServidor: cuentaNueva,
                bloquearUI: cuentaNueva && !hadCacheOnStart,
                avisarSiVacio: cuentaNueva,
                enSegundoPlano: !cuentaNueva,
            });
        } finally {
            bus().emit('setLoading', false);
            document.documentElement.dataset.boot = 'ready';
        }

        if (navigator.onLine) {
            void dispararSyncInmediato();
            initSignalListener();
        }
    }

    async function runBoot() {
        const { tenantCacheGet } = global.CrmDb;

        // Lazy boot: pintar caché local al instante (sin esperar /auth/session)
        bus().emit('hydrateSessionLocal');

        const cached = await tenantCacheGet('appData');
        const hadCacheOnStart = !!cached;

        if (hadCacheOnStart) {
            bus().emit('setData', ensureDataShape(cached));
            bus().emit('safeRenderAll');
            global.CrmApi.actualizarUIOffline();
            document.documentElement.dataset.boot = 'cached';
        }

        if (new URLSearchParams(window.location.search).get('view')) {
            bus().emit('applyPwaDeepLink');
        } else {
            bus().emit('switchView', 'home');
        }

        void finishBootInBackground(hadCacheOnStart);
    }

    global.CrmLoader = {
        loadAll,
        loadAllCore,
        descargarDatosDeLaNube,
        refrescarManual,
        servidorTieneDatos,
        runBoot,
        initSignalListener,
        teardownSignalListener,
    };
})(window);
