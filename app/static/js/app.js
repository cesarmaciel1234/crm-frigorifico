const $ = id => document.getElementById(id);
        const esc = s => (window.CrmSafe && window.CrmSafe.esc(s)) || String(s ?? '');

        window.onerror = function(msg, url, lineNo, columnNo, error) {
            if (String(msg).includes('api.open-meteo.com')) return false;
            console.error("Error global de JS:", msg, "Línea:", lineNo, "Error:", error);
            // alert solo si estamos en móvil para que el usuario pueda ver qué falló.
            if(window.innerWidth < 1000) {
                alert("Error JS: " + msg + "nLínea: " + lineNo + ":" + columnNo + "n" + (error ? error.stack : ""));
            }
            return false;
        };

        // --- OFFLINE SYNC ENGINE (Dexie) ---
        const MODO_PRUEBA = false; // Cambiar a false para enviar datos a la nube
        const db = new Dexie('CarniceriaContableDB');
        db.version(3).stores({
            transacciones: '++id, uuid, tipo, monto, fecha, status, updated_at',
            cache: 'key, updated_at',
            solicitudes_pendientes: '++id, url, method, body, created_at'
        });
        db.version(4).stores({
            transacciones: '++id, uuid, tipo, monto, fecha, status, updated_at',
            cache: 'key, updated_at',
            solicitudes_pendientes: '++id, url, method, body, created_at',
            pending_sync: '++local_id, op_id, entity, entity_uuid, status, updated_at_utc, device_id',
            sync_meta: 'key',
        });

        async function registrarTransaccion(datos) {
            const registro = {
                uuid: crypto.randomUUID(),
                ...datos,
                status: 0,
                updated_at: Date.now()
            };
            try {
                await db.transacciones.add(registro);
                if (window.CrmSync?.enqueueChange) {
                    await CrmSync.enqueueChange({
                        entity: 'operacion',
                        entity_uuid: registro.uuid,
                        action: 'CREATE',
                        payload: { ...registro, updated_at_utc: new Date().toISOString() },
                    });
                }
                console.log("Guardado localmente:", registro.uuid);
                await intentarSincronizar();
                return true;
            } catch (e) {
                console.error("Error al guardar localmente:", e);
                return false;
            }
        }

        async function intentarSincronizar(opts) {
            opts = opts || {};
            if (!navigator.onLine) {
                console.log("Sin conexión. Los datos están seguros en el dispositivo.");
                return { syncCount: 0 };
            }
            const pendientes = await db.transacciones.where('status').equals(0).toArray();
            let syncCount = 0;
            for (const item of pendientes) {
                try {
                    let responseOk = false;
                    let responseStatus = 0;

                    if (MODO_PRUEBA) {
                        console.log("[MODO PRUEBA] Simulación de envío a la nube:", item.uuid);
                        await new Promise(r => setTimeout(r, 300));
                        responseOk = true;
                    } else {
                        const response = await (window.CrmSafe?.apiFetch || fetch)('/api/operaciones', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            credentials: 'same-origin',
                            body: JSON.stringify(item)
                        });
                        responseOk = response.ok;
                        responseStatus = response.status;
                    }

                    if (responseOk) {
                        await db.transacciones.update(item.id, { status: 1 });
                        console.log("Sincronizado con éxito:", item.uuid);
                        syncCount++;
                    } else if (responseStatus >= 500 || responseStatus === 408) {
                        break;
                    }
                } catch (error) {
                    console.warn("Error de conexión al sincronizar:", error);
                    break;
                }
            }
            if (syncCount > 0 && !opts.skipReload) {
                toast(`Sincronizados ${syncCount} registros pendientes`);
                loadAll();
            }
            return { syncCount };
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
            return null;
        }

        async function drainOutboxAll() {
            if (window.CrmSync?.drainPendingSync) {
                try {
                    await CrmSync.drainPendingSync();
                } catch (e) {
                    console.warn('pending_sync drain:', e);
                }
            }
            await intentarSincronizar({ skipReload: true });
            return drainSolicitudesPendientes();
        }

        async function drainSolicitudesPendientes() {
            if (!navigator.onLine) return { exito: 0, fallo: 0 };
            const pendientes = await db.solicitudes_pendientes.orderBy('id').toArray();
            let exitoCount = 0;
            let falloCount = 0;

            for (const item of pendientes) {
                try {
                    const response = await (window.CrmSafe?.apiFetch || fetch)(item.url, {
                        method: item.method,
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'same-origin',
                        body: item.body
                    });

                    if (response.ok) {
                        await db.solicitudes_pendientes.delete(item.id);
                        exitoCount++;
                    } else if (response.status >= 500 || response.status === 408) {
                        console.warn('Reintentar solicitud pendiente más tarde:', item.url, response.status);
                        break;
                    } else {
                        const d = await response.json().catch(() => ({}));
                        console.error('Error al sincronizar operación:', d.error || response.statusText);
                        await db.solicitudes_pendientes.update(item.id, {
                            failed: true,
                            last_status: response.status,
                            last_error: d.error || response.statusText,
                        });
                        falloCount++;
                    }
                } catch (error) {
                    console.warn('Fallo de red en sincronización, deteniendo cola:', error);
                    break;
                }
            }
            return { exito: exitoCount, fallo: falloCount };
        }

        async function publishNodeBackupAfterSync() {
            if (window.CrmSync && data && navigator.onLine) {
                await CrmSync.pushNode(data).catch(() => {});
            }
        }

        window.addEventListener('online', async () => {
            await drainOutboxAll();
            if ((await countPendingOutbox()) === 0) {
                await loadAll();
            } else {
                actualizarUIOffline();
            }
        });
        // -----------------------------------
        const fmt = n => Number(n).toLocaleString('es-AR', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        const fmtFecha = (iso, time=false) => {
            if (!iso) return '-';
            const d = new Date(iso);
            if (isNaN(d)) return iso;
            return d.toLocaleString('es-AR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: time ? '2-digit' : undefined, minute: time ? '2-digit' : undefined });
        };
        const fmtCompact = n => {
            if (n >= 1000000) return (n / 1000000).toLocaleString('es-AR', { minimumFractionDigits: 0, maximumFractionDigits: 1 }) + 'M';
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
        const fmtPct = n => n != null ? n.toFixed(2) + '%' : '—';

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
            const isCobrado = (r.pagado ?? r.estado_cobro) === 'cobrado' || Number(r.pagado) === 1 || (r.estado_cobro === 'incobrable' || Number(r.pagado) === 2);
            if (isCobrado) return 0;
            return Math.max(0, total - pagado);
        }

        let remitoPagoActual = null;
        let cobranzaClienteActual = null;
        let pagoCentralDeudaActual = null;

        let data = { enemigos: [], remitos: [], estrategia: {}, bancos: [], historial: [], historialPagos: [], bulk: [], clientes: [], auditoria: [], usuarios: [] };
        let isProMode = true;
        let selectedDeuda = null;
        let selectedAuditId = null;
        let sessionUser = { role: 'admin', username: 'jefe', empresa_id: 1, empresa_nombre: '' };

        function tenantCacheKey(name) {
            const eid = sessionUser?.empresa_id || 1;
            return name + ':' + eid;
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

        let histPagosFiltro = '';
        let activeRowIndex = -1;

        const titles = {
            home: ['Inicio', 'Resumen rápido', 'Inicio'],
            dashboard: ['', 'Análisis general del negocio', 'Panel del jefe'],
            deudas: ['Prioridad de pagos', 'Obligaciones ordenadas por impacto financiero', 'Obligaciones'],
            remitos: ['Historial de venta', 'Historial de ventas y márgenes', 'Ventas'],
            clientes: ['CRM de Clientes', 'Gestión de cuentas corrientes y créditos', 'Clientes'],
            registro: ['Nueva deuda', 'Alta de deudas, remitos y entidades', 'Deuda'],
            'compra-bulk': ['Compra Bulk', 'Registrar nuevo lote mayorista de carne', 'Bulk'],
            'ventas-express': ['Ventas Express', 'Registro rápido de ventas de mostrador', 'Express'],
            'historial-pagos': ['Historial de pagos', 'Ledger de movimientos por cuota', 'Pagos'],
            cobranzas: ['Central de Cobranzas', 'Clientes con saldo pendiente', 'Cobranzas'],
            'pago-central': ['Pago Centralizado Empresarial', 'Obligaciones pendientes de pago', 'Pagos'],
            auditoria: ['Auditoría', 'Historial completo de acciones', 'Historial'],
            usuarios: ['Usuarios', 'Gestión de accesos y roles', 'Usuarios'],
            'cliente-detalle': ['Perfil de Cliente', 'Detalle corporativo y facturación', 'Perfil'],
            'nueva-venta': ['Registrar Venta', 'Emitir remito o factura a cuenta corriente', 'Ventas'],
            'finanzas-aging': ['Antigüedad de deuda', 'Análisis de vencimiento de facturas', 'Deuda'],
            'finanzas-margenes': ['Márgenes de venta', 'Rentabilidad y costos de remitos', 'Márgenes']
        };

        function setLoading(on) {
            $('appLoader')?.classList.toggle('active', !!on);
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

        async function api(url, opts) {
            opts = opts || {};
            opts.credentials = 'same-origin';
            opts.headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
            
            const method = (opts.method || 'GET').toUpperCase();
            const isGet = method === 'GET';
            
            try {
                const r = await (window.CrmSafe?.apiFetch || fetch)(url, opts);
                const d = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(d.error || 'Error en la operación');
                return d;
            } catch (error) {
                if (isGet) throw error;
                
                const isNetworkError = !navigator.onLine || error.message.includes('Failed to fetch') || error.message.includes('NetworkError') || error.message.includes('network error');
                if (isNetworkError) {
                    const syncOp = buildSyncOpFromRequest(url, method, opts.body);
                    if (syncOp && window.CrmSync?.enqueueChange) {
                        await CrmSync.enqueueChange(syncOp);
                        if (opts.body) {
                            try {
                                const parsed = JSON.parse(opts.body);
                                if (!parsed.uuid) {
                                    opts.body = JSON.stringify({ ...parsed, uuid: syncOp.entity_uuid });
                                }
                            } catch (_) {}
                        }
                    } else {
                        await db.solicitudes_pendientes.add({
                            url,
                            method,
                            body: opts.body || null,
                            created_at: new Date().toISOString()
                        });
                    }
                    
                    toast('⚠️ Sin conexión. Registrado localmente.', false);
                    aplicarCambioOptimista(url, method, opts.body);
                    actualizarUIOffline();
                    
                    return { ok: true, offline: true, message: 'Operación guardada localmente' };
                } else {
                    throw error;
                }
            }
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
            const r = await drainSolicitudesPendientes();
            await intentarSincronizar({ skipReload: true });
            const pending = await countPendingOutbox();

            if (r.exito > 0 || r.fallo > 0) {
                toast(`🔄 Sincronización: ${r.exito} éxito, ${r.fallo} pendiente de revisión.`);
                if (!pending) await loadAll();
            }
            actualizarUIOffline();
        }

        async function aplicarCambioOptimista(url, method, body) {
            try {
                if (!data) return;
                const parsedBody = body ? JSON.parse(body) : {};
                
                // 1. Crear cliente
                if (url.includes('/api/clientes') && method === 'POST' && !url.includes('/cobrar') && !url.includes('/saldo-inicial') && !url.includes('/incobrable')) {
                    const clientUuid = parsedBody.uuid || crypto.randomUUID();
                    const newCli = {
                        id: 'temp_' + clientUuid,
                        uuid: clientUuid,
                        nombre: parsedBody.nombre || 'Nuevo Cliente (Offline)',
                        techo_deuda: parseFloat(parsedBody.techo_deuda || 0),
                        scoring: parseFloat(parsedBody.scoring || 5),
                        telefono: parsedBody.telefono || '',
                        cuit: parsedBody.cuit || '',
                        direccion: parsedBody.direccion || '',
                        email: parsedBody.email || '',
                        saldo_inicial: parseFloat(parsedBody.saldo_inicial || 0),
                        saldo_actual: parseFloat(parsedBody.saldo_inicial || 0),
                        remitos: [],
                        pagos: []
                    };
                    data.clientes = data.clientes || [];
                    data.clientes.push(newCli);
                }
                
                // 2. Crear Remito
                if (url.includes('/api/remitos') && method === 'POST' && !url.includes('/reset-pago')) {
                    const clientVal = parsedBody.cliente;
                    const c = data.clientes.find(cli => cli.nombre === clientVal || String(cli.id) === String(clientVal));
                    const precio = parseFloat(parsedBody.precio_por_kg || 0);
                    const kg = parseFloat(parsedBody.kg || 0);
                    const total = precio * kg;
                    
                    const remitoUuid = parsedBody.uuid || crypto.randomUUID();
                    const newRemito = {
                        id: 'temp_' + remitoUuid,
                        uuid: remitoUuid,
                        cliente: c ? c.nombre : clientVal,
                        tipo_corte: parsedBody.tipo_corte || '',
                        cantidad: parseInt(parsedBody.cantidad || 1, 10),
                        kg: kg,
                        precio_por_kg: precio,
                        precio_venta_total: total,
                        pagado: 0,
                        monto_pagado: 0,
                        fecha: new Date().toISOString().split('T')[0]
                    };
                    
                    if (c) {
                        c.remitos = c.remitos || [];
                        c.remitos.push(newRemito);
                        c.saldo_actual = (c.saldo_actual || 0) + total;
                    }
                }
                
                // 3. Cobro a cliente
                if (url.includes('/api/clientes/') && url.includes('/cobrar') && method === 'POST') {
                    const match = url.match(/\/api\/clientes\/([^\/]+)\/cobrar/);
                    if (match) {
                        const clientId = match[1];
                        const c = data.clientes.find(cli => String(cli.id) === String(clientId));
                        const monto = parseFloat(parsedBody.monto_pagado || 0);
                        if (c) {
                            c.saldo_actual = (c.saldo_actual || 0) - monto;
                            c.pagos = c.pagos || [];
                            c.pagos.push({
                                id: 'temp_' + crypto.randomUUID(),
                                monto: monto,
                                fecha: new Date().toISOString().split('T')[0],
                                tipo: 'COBRO'
                            });
                        }
                    }
                }
                
                // 4. Cobro a Remito
                if (url.includes('/api/remitos/') && url.includes('/cobrar') && method === 'POST') {
                    const match = url.match(/\/api\/remitos\/([^\/]+)\/cobrar/);
                    if (match) {
                        const remitoId = match[1];
                        const monto = parseFloat(parsedBody.monto_pagado || 0);
                        for (const c of (data.clientes || [])) {
                            const r = (c.remitos || []).find(rem => String(rem.id) === String(remitoId));
                            if (r) {
                                r.monto_pagado = (r.monto_pagado || 0) + monto;
                                if (r.monto_pagado >= r.precio_venta_total - 0.01) {
                                    r.pagado = 1;
                                }
                                c.saldo_actual = (c.saldo_actual || 0) - monto;
                                c.pagos = c.pagos || [];
                                c.pagos.push({
                                    id: 'temp_' + Date.now(),
                                    monto: monto,
                                    fecha: new Date().toISOString().split('T')[0],
                                    tipo: 'COBRO'
                                });
                                break;
                            }
                        }
                    }
                }

                // 5. Eliminar Remito
                if (url.includes('/api/remitos/') && method === 'DELETE') {
                    const match = url.match(/\/api\/remitos\/([^\/]+)/);
                    if (match) {
                        const remitoId = match[1];
                        for (const c of (data.clientes || [])) {
                            const rIndex = (c.remitos || []).findIndex(rem => String(rem.id) === String(remitoId));
                            if (rIndex !== -1) {
                                const r = c.remitos[rIndex];
                                c.saldo_actual = (c.saldo_actual || 0) - (r.precio_venta_total - (r.monto_pagado || 0));
                                c.remitos.splice(rIndex, 1);
                                break;
                            }
                        }
                    }
                }

                // 6. Eliminar Pago
                if (url.includes('/api/pagos/') && method === 'DELETE') {
                    const match = url.match(/\/api\/pagos\/([^\/]+)/);
                    if (match) {
                        const pagoId = match[1];
                        for (const c of (data.clientes || [])) {
                            const pIndex = (c.pagos || []).findIndex(p => String(p.id) === String(pagoId));
                            if (pIndex !== -1) {
                                const p = c.pagos[pIndex];
                                c.saldo_actual = (c.saldo_actual || 0) + p.monto;
                                c.pagos.splice(pIndex, 1);
                                break;
                            }
                        }
                    }
                }

                // 7. Eliminar Cliente
                if (url.includes('/api/clientes/') && method === 'DELETE') {
                    const match = url.match(/\/api\/clientes\/([^\/]+)/);
                    if (match) {
                        const clientId = match[1];
                        const cIndex = (data.clientes || []).findIndex(cli => String(cli.id) === String(clientId));
                        if (cIndex !== -1) {
                            data.clientes.splice(cIndex, 1);
                        }
                    }
                }

                await tenantCachePut('appData', data);
                renderAll();
            } catch (err) {
                console.error("Error al aplicar cambio optimista:", err);
            }
        }

        function plazoTexto(e) {
            if (e.es_proveedor) {
                let t = e.plazo_texto || (e.plazo_dias != null ? e.plazo_dias + ' días' : '—');
                if (e.kg && e.precio_kg) t = fmt(e.kg) + ' kg × $' + fmt(e.precio_kg) + ' · ' + t;
                if (e.tiene_cuotas && e.cuotas_total) t += ' · Pago ' + (e.cuotas_pagadas || 0) + '/' + e.cuotas_total;
                return t;
            }
            if (e.tiene_cuotas && e.cuotas_total) {
                let t = `Cuota ${e.cuotas_pagadas}/${e.cuotas_total}`;
                if (e.es_cheque && e.fecha_vencimiento) t += ' · vto ' + e.fecha_vencimiento;
                else if (e.es_tarjeta && e.fecha_vencimiento) t += ' · vto ' + e.fecha_vencimiento;
                return t;
            }
            if (e.es_cheque) {
                return e.fecha_vencimiento ? 'Cheque · vto ' + e.fecha_vencimiento : 'Cheque';
            }
            if (e.es_tarjeta && e.cuotas) {
                let t = e.cuotas + ' cuota' + (e.cuotas > 1 ? 's' : '');
                if (e.fecha_vencimiento) t += ' · vto ' + e.fecha_vencimiento;
                return t;
            }
            return e.meses + ' meses';
        }

        let planPagoActual = null;

        function calcDiffUi(esperado, pagado) {
            const diff = pagado - esperado;
            const box = $('modalDiff');
            if (!pagado || pagado <= 0) {
                box.className = 'diff-box';
                box.textContent = 'Ingresá el monto a pagar';
                return;
            }
            if (diff > 0.005) {
                box.className = 'diff-box punitorio';
                box.innerHTML = `<strong>Interés punitorio:</strong> $${fmt(diff)}<br><span style="font-size:0.75rem;opacity:0.85">Pago mayor a la cuota esperada</span>`;
            } else if (diff < -0.005) {
                box.className = 'diff-box descuento';
                box.innerHTML = `<strong>Descuento:</strong> $${fmt(Math.abs(diff))}<br><span style="font-size:0.75rem;opacity:0.85">Pago menor a la cuota esperada</span>`;
            } else {
                box.className = 'diff-box';
                box.textContent = 'Pago exacto de la cuota';
            }
        }

        async function abrirModalPago() {
            if (!selectedDeuda) return;
            if (!selectedDeuda.tiene_cuotas) {
                toast('Esta obligación no usa plan de cuotas', true);
                return;
            }
            if (selectedDeuda.completa) {
                toast('Todas las cuotas ya están pagadas', true);
                return;
            }
            planPagoActual = await api('/api/operaciones/' + selectedDeuda.id + '/plan-pago');
            $('modalPagoSub').textContent = planPagoActual.alias + ' · Total $' + fmt(planPagoActual.total_pagar);
            $('inpCuotaTotal').textContent = planPagoActual.cuotas_total;
            $('inpCuotaNum').min = planPagoActual.cuotas_pagadas + 1;
            $('inpCuotaNum').max = planPagoActual.cuotas_total;
            $('inpCuotaNum').value = planPagoActual.cuota_en_curso;
            $('inpMontoEsperado').value = fmt(planPagoActual.monto_cuota);
            $('inpMontoPago').value = planPagoActual.monto_cuota;

            const alert = $('modalVencidasAlert');
            if (planPagoActual.cuotas_vencidas > 1) {
                alert.classList.remove('field-hidden');
                alert.textContent = 'Cuotas vencidas: ' + planPagoActual.cuotas_vencidas_lista.join(', ') + ' de ' + planPagoActual.cuotas_total;
            } else if (planPagoActual.vencido) {
                alert.classList.remove('field-hidden');
                alert.textContent = 'Cuota ' + planPagoActual.cuota_en_curso + ' vencida · ' + (planPagoActual.mensaje_vencimiento || '');
            } else {
                alert.classList.add('field-hidden');
            }
            calcDiffUi(planPagoActual.monto_cuota, planPagoActual.monto_cuota);
            $('modalPago').classList.add('open');
        }

        function cerrarModalPago() {
            $('modalPago').classList.remove('open');
            planPagoActual = null;
        }

        async function confirmarPago() {
            if (!planPagoActual) return;
            const numero = parseInt($('inpCuotaNum').value, 10);
            const monto = parseFloat($('inpMontoPago').value);
            try {
                const res = await api('/api/operaciones/' + planPagoActual.id + '/pagar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ numero_cuota: numero, monto_pagado: monto })
                });
                let msg = `Cuota ${res.numero_cuota}/${res.cuotas_total} registrada`;
                if (res.interes_punitorio > 0) msg += ` · Punitorio $${fmt(res.interes_punitorio)}`;
                if (res.descuento > 0) msg += ` · Descuento $${fmt(res.descuento)}`;
                toast(msg);
                cerrarModalPago();
                closeDrawer();
                await loadAll();
            } catch (e) { toast(e.message, true); }
        }

        function abrirModalPagoRemito(remito) {
            if (!remito) return;
            const saldo = remitoSaldoPendiente(remito);
            if (saldo <= 0) {
                toast('Este remito no tiene saldo pendiente', true);
                return;
            }
            remitoPagoActual = remito;
            const cliente = currentClientData?.nombre || remito.cliente || '';
            $('modalPagoRemitoSub').textContent = `Remito #${remito.id}${cliente ? ' · ' + cliente : ''}`;
            $('inpRemitoSaldo').value = fmt(saldo);
            $('inpMontoRemitoPago').value = saldo;
            $('inpMontoRemitoPago').max = saldo;
            $('modalPagoRemito').classList.add('open');
            setTimeout(() => $('inpMontoRemitoPago')?.focus(), 100);
        }

        function cerrarModalPagoRemito() {
            $('modalPagoRemito')?.classList.remove('open');
            remitoPagoActual = null;
        }

        async function confirmarPagoRemito() {
            if (!remitoPagoActual) return;
            const saldo = remitoSaldoPendiente(remitoPagoActual);
            const monto = parseFloat($('inpMontoRemitoPago').value);
            if (!monto || monto <= 0) {
                toast('Ingresá un monto válido', true);
                return;
            }
            if (monto > saldo + 0.009) {
                toast('El monto supera el saldo pendiente', true);
                return;
            }
            try {
                const res = await api('/api/remitos/' + remitoPagoActual.id + '/cobrar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ monto_pagado: monto })
                });
                toast(res.message || 'Pago registrado');
                cerrarModalPagoRemito();
                await loadAll();
                if (currentClientData && $('view-cliente-detalle')?.classList.contains('active')) {
                    const c = await api('/api/clientes/' + currentClientData.id);
                    currentClientData = c;
                    if ($('viewClientTitle')) $('viewClientTitle').textContent = c.nombre;
                    renderClientDashboard();
                } else {
                    await renderRemitosFull();
                }
            } catch (e) { toast(e.message, true); }
        }

        function abrirAccionCobranza(clientId) {
            const c = (data.clientes || []).find(x => x.id === clientId);
            if (!c) return;
            cobranzaClienteActual = c;
            if ($('modalCobranzaAccionSub')) {
                $('modalCobranzaAccionSub').textContent = `${c.nombre} · Deuda $${fmt(c.saldo_actual)}`;
            }
            $('modalCobranzaAccion')?.classList.add('open');
        }
        window.abrirAccionCobranza = abrirAccionCobranza;

        function cerrarModalCobranzaAccion() {
            $('modalCobranzaAccion')?.classList.remove('open');
        }

        function cobranzaElegirVer() {
            const c = cobranzaClienteActual;
            cerrarModalCobranzaAccion();
            cobranzaClienteActual = null;
            if (c) openClientDrawer(c.id);
        }

        function cobranzaElegirCobrar() {
            if (!cobranzaClienteActual) return;
            const c = cobranzaClienteActual;
            cerrarModalCobranzaAccion();
            abrirModalPagoGlobal(c);
        }

        function abrirModalPagoGlobal(cliente) {
            if (!cliente) return;
            cobranzaClienteActual = cliente;
            const saldo = Number(cliente.saldo_actual || 0);
            if ($('modalPagoGlobalSub')) $('modalPagoGlobalSub').textContent = cliente.nombre;
            if ($('inpPagoGlobalSaldo')) $('inpPagoGlobalSaldo').value = fmt(saldo);
            if ($('inpMontoPagoGlobal')) {
                $('inpMontoPagoGlobal').value = saldo;
                $('inpMontoPagoGlobal').max = saldo;
            }
            $('modalPagoGlobal')?.classList.add('open');
            setTimeout(() => $('inpMontoPagoGlobal')?.focus(), 100);
        }

        function cerrarModalPagoGlobal() {
            $('modalPagoGlobal')?.classList.remove('open');
            cobranzaClienteActual = null;
        }

        async function confirmarPagoGlobal() {
            if (!cobranzaClienteActual) return;
            const saldo = Number(cobranzaClienteActual.saldo_actual || 0);
            const monto = parseFloat($('inpMontoPagoGlobal').value);
            if (!monto || monto <= 0) {
                toast('Ingresá un monto válido', true);
                return;
            }
            if (monto > saldo + 0.009) {
                toast('El monto supera la deuda pendiente', true);
                return;
            }
            try {
                const res = await api('/api/clientes/' + cobranzaClienteActual.id + '/cobrar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ monto_pagado: monto })
                });
                toast(res.message || 'Cobro registrado');
                cerrarModalPagoGlobal();
                await loadAll();
                renderCobranzas();
            } catch (e) { toast(e.message, true); }
        }

        function toggleFormTipo() {
            const tipo = $('selTipo').value;
            const esTarjeta = tipo === 'tarjeta';
            const esCheque = tipo === 'cheque';
            const esProveedor = tipo === 'proveedor';
            const esPrestamo = tipo === 'prestamo';
            const form = $('formDeuda');

            ['fldCierre', 'fldCuotas', 'hintTarjeta'].forEach(id => {
                $(id).classList.toggle('field-hidden', !esTarjeta);
            });
            ['fldMonto', 'hintCheque'].forEach(id => {
                $(id).classList.toggle('field-hidden', !esCheque);
            });
            ['fldKg', 'fldPrecioKg', 'fldProvTotal', 'hintProveedor'].forEach(id => {
                $(id).classList.toggle('field-hidden', !esProveedor);
            });
            ['hintPrestamo', 'fldInicioFecha'].forEach(id => {
                $(id).classList.toggle('field-hidden', !esPrestamo);
            });
            $('fldPlazoDias').classList.toggle('field-hidden', !(esProveedor || esPrestamo));
            $('fldVencimiento').classList.toggle('field-hidden', !(esTarjeta || esCheque));
            $('fldRecibido').classList.toggle('field-hidden', esCheque || esProveedor);
            $('fldPagar').classList.toggle('field-hidden', esCheque);
            $('fldMeses').classList.toggle('field-hidden', esTarjeta || esCheque || esProveedor || esPrestamo);

            if (!esCheque) {
                $('lblRecibido').textContent = esTarjeta ? 'Resumen / consumo ($)' : 'Monto recibido ($)';
                $('lblPagar').textContent = esTarjeta ? 'Total a pagar ($)' : (esProveedor ? 'Total a pagar ($) — opcional si hay interés' : 'Monto a devolver ($)');
            }

            form.monto.required = esCheque;
            form.recibido.required = !esCheque && !esProveedor;
            form.pagar.required = !esCheque && !esTarjeta && !esProveedor;
            form.fecha_cierre.required = esTarjeta;
            form.fecha_vencimiento.required = esTarjeta || esCheque;
            form.cuotas.required = esTarjeta;
            form.meses.required = !esTarjeta && !esCheque && !esProveedor && !esPrestamo;
            if (form.kg) form.kg.required = esProveedor;
            if (form.precio_kg) form.precio_kg.required = esProveedor;
            if (form.plazo_dias) form.plazo_dias.required = esProveedor || esPrestamo;

            if (esTarjeta) form.meses.value = '';
            if (esCheque) { form.recibido.value = ''; form.pagar.value = ''; form.meses.value = ''; }
            if (esProveedor) { form.recibido.value = ''; form.meses.value = ''; updateProvTotal(); }
            if (esPrestamo) { form.meses.value = ''; }
        }

        function updateProvTotal() {
            const kg = parseFloat(document.querySelector('[name=kg]')?.value) || 0;
            const pk = parseFloat(document.querySelector('[name=precio_kg]')?.value) || 0;
            const box = $('inpTotalProveedor');
            if (!box) return;
            if (kg > 0 && pk > 0) box.textContent = '$' + fmt(kg * pk);
            else box.textContent = '—';
        }

        function badgeVencimiento(e) {
            if ((!e.es_tarjeta && !e.es_cheque && !e.es_proveedor) || !e.fecha_vencimiento) return '<span class="badge-home neutro">—</span>';
            if (e.vencido) return `<span class="badge-home vencido" style="line-height:1.2;text-align:center;display:inline-block">${e.dias_retraso} días<br>retraso</span>`;
            if (e.estado_vencimiento === 'hoy') return '<span class="badge-home vencido">Hoy</span>';
            if (e.estado_vencimiento === 'proximo') return `<span class="badge-home restantes" style="line-height:1.2;text-align:center;display:inline-block">${e.dias_faltantes} días<br>restantes</span>`;
            return `<span class="badge-home verde" style="line-height:1.2;text-align:center;display:inline-block">${e.dias_faltantes} días<br>restantes</span>`;
        }

        function badgeEstado(e) {
            if (e.vencido) return '<span class="badge badge-danger">Vencido</span>';
            if (e.prioridad) return '<span class="badge badge-danger">Pagar primero</span>';
            if (e.urgente) return '<span class="badge badge-warning">Urgente CFR</span>';
            if (e.estado_vencimiento === 'proximo' || e.estado_vencimiento === 'hoy') return '<span class="badge badge-warning">Vence pronto</span>';
            return '<span class="badge badge-neutral">Normal</span>';
        }

        function histMsgClass(e) {
            if (e.vencido) return 'vencido';
            if (e.estado_vencimiento === 'proximo' || e.estado_vencimiento === 'hoy') return 'proximo';
            return 'ok';
        }

        function renderHistorialSidebar() {
            const box = $('historialSidebar');
            const items = data.historial || [];
            if (!items.length) {
                box.innerHTML = '<div class="hist-empty">Sin vencimientos próximos</div>';
                return;
            }
            box.innerHTML = items.map(e => {
                const cls = ['hist-item', e.vencido ? 'vencido' : '', (e.estado_vencimiento === 'proximo' || e.estado_vencimiento === 'hoy') ? 'proximo' : ''].filter(Boolean).join(' ');
                const sub = e.es_proveedor && e.plazo_texto
                    ? `Total: $${fmt(e.total_pagar)} · ${e.plazo_texto}`
                    : `Total: $${fmt(e.total_pagar)} · Cuota ${e.cuotas_pagadas || 0}/${e.cuotas_total || '—'}`;
                return `<div class="${cls}" data-id="${e.id}">
                    <div class="hist-top">
                        <span class="hist-alias">${esc(e.alias)}</span>
                        ${badgeVencimiento(e)}
                    </div>
                    <div class="hist-pagar">${sub}</div>
                    <div class="hist-msg ${histMsgClass(e)}">${esc(e.mensaje_vencimiento || '')} · vto ${esc(e.fecha_vencimiento)}</div>
                </div>`;
            }).join('');
            box.querySelectorAll('.hist-item').forEach(el => {
                el.addEventListener('click', () => {
                    const id = parseInt(el.dataset.id, 10);
                    const e = data.enemigos.find(x => x.id === id) || data.historial.find(x => x.id === id);
                    if (e) { openDrawer(e); switchView('home'); $('sidebar').classList.remove('open'); }
                });
            });
        }

        function vencBannerHtml(e) {
            if ((!e.es_tarjeta && !e.es_cheque && !e.es_proveedor) || !e.fecha_vencimiento) return '';
            let cls = 'ok';
            if (e.vencido) cls = 'vencido';
            else if (e.estado_vencimiento === 'hoy') cls = 'hoy';
            else if (e.estado_vencimiento === 'proximo') cls = 'proximo';
            return `<div class="venc-banner ${cls}">${esc(e.mensaje_vencimiento)}<br><span style="font-size:0.8rem;font-weight:500">Total a pagar: $${fmt(e.total_pagar)} · Vto ${esc(e.fecha_vencimiento)}</span></div>`;
        }

        function renderKpis() {
            const s = data.estrategia.sangria || {};
            const a = data.estrategia.activo || data.estrategia.flujo || data.estrategia.respiracion || {};
            const p = data.estrategia.proyeccion || {};
            
            const mf = data.metricas_flotantes;
            if (mf) {
                const isMobile = window.innerWidth < 768;
                const formatVal = (val) => isMobile ? fmtCompact(val) : fmt(val);
                const formatSubVal = (val) => isMobile ? fmtCompact(val) : fmt(val);

                if ($('barSangriaValue')) $('barSangriaValue').textContent = '$' + formatVal(mf.sangre);
                if ($('barSangriaSub')) $('barSangriaSub').textContent = `Int: $${formatSubVal(mf.int_diario)}`;
                
                if ($('barDeudaValue')) $('barDeudaValue').textContent = '$' + formatVal(mf.deuda);
                if ($('barDeudaSub')) $('barDeudaSub').textContent = `Int: $${formatSubVal(mf.int_acumulado || 0)}`;
                
                const absCapital = Math.abs(mf.capital);
                const signStr = mf.capital < 0 ? '-' : (mf.capital > 0 ? '+' : '');
                const isNeg = mf.capital < 0;
                const isZero = mf.capital === 0;
                if ($('barCapitalValue')) {
                    $('barCapitalValue').textContent = signStr + '$' + formatVal(absCapital);
                    $('barCapitalValue').className = 'value ' + (isNeg ? 'capital-neg' : isZero ? 'capital-zero' : 'capital-pos');
                }
                if ($('barCapitalTrend')) {
                    const trend = isNeg ? 'down' : (isZero ? 'down' : (mf.tendencia || 'up'));
                    $('barCapitalTrend').textContent = trend === 'up' ? '▲' : '▼';
                    $('barCapitalTrend').className = 'trend ' + trend;
                    $('barCapitalTrend').style.visibility = isZero ? 'hidden' : 'visible';
                }
                if ($('barCapitalSub')) {
                    $('barCapitalSub').textContent = isNeg ? 'En déficit' : isZero ? 'Sin margen' : 'Disponible hoy';
                }
            }

            $('kpiMeta').textContent = fmt(a.stock_kg || 0) + ' kg';
            $('kpiExcedente').textContent = '$' + fmt(a.caja_real || 0);


        }

        function renderHealth() {
            const a = data.estrategia.activo || data.estrategia.flujo || data.estrategia.respiracion || {};
            $('healthGrid').innerHTML = `
                <div class="health-item"><div class="lbl">Activo ventas (Por cobrar)</div><div class="val" style="color:var(--success)">$${fmt(a.activo_pendiente || 0)}</div></div>
                <div class="health-item"><div class="lbl">Caja real (Efectivo disponible)</div><div class="val" style="color:var(--success)">$${fmt(a.caja_real || 0)}</div></div>
                <div class="health-item"><div class="lbl">Stock valorizado</div><div class="val" style="color:var(--accent)">$${fmt(a.activo_mercaderia || 0)}</div></div>
                <div class="health-item"><div class="lbl">Stock disponible</div><div class="val" style="color:var(--accent)">${fmt(a.stock_kg || 0)} kg</div></div>
                <div class="health-item"><div class="lbl">Deuda comercial (Proveedores)</div><div class="val" style="color:var(--warning)">$${fmt(a.deuda_comercial ?? 0)}</div></div>
                <div class="health-item"><div class="lbl">Deuda financiera (Total pendiente)</div><div class="val" style="color:var(--danger)">$${fmt(a.deuda_real ?? 0)}</div></div>
                <div class="health-item"><div class="lbl">Deuda neta (Capital pendiente)</div><div class="val" style="color:var(--danger)">$${fmt(a.deuda_neta ?? 0)}</div></div>
                <div class="health-item"><div class="lbl">Interés neto pendiente</div><div class="val" style="color:var(--warning)">$${fmt(a.interes_neto ?? 0)}</div></div>
            `;

            if ($('balActivos')) $('balActivos').textContent = '$' + fmt(a.activo_total || 0);
            if ($('balPasivos')) $('balPasivos').textContent = '$' + fmt(a.pasivo_total || 0);
            if ($('balPatrimonio')) $('balPatrimonio').textContent = '$' + fmt(a.patrimonio_neto || 0);
        }

        function renderChartCfr() {
            const top = data.enemigos
                .filter(e => !e.es_proveedor && e.cfr != null)
                .slice(0, 5);
            const max = top.length ? Math.max(...top.map(e => e.cfr || 0), 1) : 1;
            if (!top.length) {
                $('chartCfr').innerHTML = '<div class="empty-state"><strong>Sin deuda financiera con CFR</strong><p>Tarjetas, bancos y préstamos con interés</p></div>';
                return;
            }
            $('chartCfr').innerHTML = top.map(e => {
                const pct = Math.min(100, ((e.cfr || 0) / max) * 100);
                const cls = (e.cfr || 0) > 10 ? 'critical' : '';
                return `<div class="chart-row">
                    <label>${esc(e.alias)}</label>
                    <div class="chart-track"><div class="chart-fill ${cls}" style="width:${pct}%">${fmtPct(e.cfr)}</div></div>
                    <span style="font-size:0.75rem;color:var(--text-muted)">${esc(e.tipo)}</span>
                </div>`;
            }).join('');
        }

        function sortEnemigosPrioridad(list) {
            return [...list].sort((a, b) => {
                const getScore = e => {
                    if (e.vencido) return -1000 - (e.dias_retraso || 0);
                    if (e.estado_vencimiento === 'hoy') return 0;
                    if (e.estado_vencimiento === 'proximo') return e.dias_faltantes;
                    if (e.fecha_vencimiento) return 1000 + e.dias_faltantes;
                    return 10000;
                };
                return getScore(a) - getScore(b);
            });
        }

        function deudaCardHtml(e, opts = {}) {
            const { clickable = true, actionBtnHtml = '' } = opts;
            const isVencido = e.vencido;
            let tipoClase = (e.tipo || 'neutro').toLowerCase();
            const cfrText = e.sin_interes ? '0%' : fmtPct(e.cfr);
            const intText = e.sin_interes ? '$0' : '$' + fmtCompact(e.interes);
            const capText = '$' + fmtCompact(e.recibido);
            let icon = '💳';
            if (tipoClase === 'proveedor') icon = '🏭';
            if (tipoClase === 'cheque') icon = '🧾';
            if (tipoClase === 'banco') icon = '🏦';
            if (tipoClase.includes('préstamo') || tipoClase.includes('prestamo')) icon = '🦈';
            const totalHighlight = isVencido ? ' highlight' : '';
            let dueHtml = plazoTexto(e);
            if (dueHtml.toLowerCase().includes('- vto')) {
                dueHtml = dueHtml.replace(/ - vto /i, '<br>Vto: <strong>') + '</strong>';
            } else if (dueHtml.toLowerCase().includes('- vto:')) {
                dueHtml = dueHtml.replace(/ - vto: /i, '<br>Vto: <strong>') + '</strong>';
            }
            const clickCls = clickable && !actionBtnHtml ? ' clickable' : '';
            const extraCls = actionBtnHtml ? ' cobranza-debt-card' : '';
            return `
            <div class="debt-card${clickCls}${extraCls}" data-id="${e.id}">
                <div class="card-header">
                    <div class="card-title-group">
                        <div class="icon-box">${icon}</div>
                        <div>
                            <h3 class="card-title">${esc(e.alias)}</h3>
                            <span class="pill pill-type">${esc(e.tipo)}</span>
                        </div>
                    </div>
                    ${badgeVencimiento(e)}
                </div>
                <div class="card-body">
                    <div class="data-col">
                        <span class="data-label">Capital</span>
                        <span class="data-value">${capText}</span>
                    </div>
                    <div class="data-col">
                        <span class="data-label">Interés</span>
                        <span class="data-value">${intText}</span>
                    </div>
                    <div class="data-col">
                        <span class="data-label">CFR</span>
                        <span class="data-value">${cfrText}</span>
                    </div>
                </div>
                <div class="card-footer">
                    <div class="amount-section">
                        <span class="amount-label">TOTAL A PAGAR</span>
                        <span class="amount-total${totalHighlight}">$${fmtCompact(e.total_pagar)}</span>
                    </div>
                    <div class="due-details">
                        ${dueHtml}
                    </div>
                </div>
                ${actionBtnHtml}
            </div>`;
        }

        function renderHomeTable() {
            if (!data.enemigos || !data.enemigos.length) {
                if (isProMode) {
                    $('tblHomeWrapperPro').innerHTML = '<div class="empty-state">No hay obligaciones cargadas</div>';
                } else {
                    $('tblHome').innerHTML = '<tr><td colspan="8"><div class="empty-state">No hay obligaciones cargadas</div></td></tr>';
                }
                return;
            }
            
            const sorted = sortEnemigosPrioridad(data.enemigos);

            if (isProMode) {
                $('tblHomeWrapperPro').innerHTML = sorted.map(e => deudaCardHtml(e)).join('');
                
                document.querySelectorAll('#tblHomeWrapperPro .clickable').forEach(card => {
                    card.addEventListener('click', () => {
                        const id = parseInt(card.dataset.id, 10);
                        const e = data.enemigos.find(x => x.id === id);
                        if (e) openDrawer(e);
                    });
                });
            } else {
                $('tblHome').innerHTML = sorted.map(e => {
                    const rowCls = e.vencido ? ' class="highlight-red clickable"' : ' class="clickable"';
                    let tipoClase = (e.tipo || 'neutro').toLowerCase();
                    if (!['tarjeta','banco','proveedor','cheque'].includes(tipoClase)) tipoClase = 'neutro';
                    
                    const cfrText = e.sin_interes ? '0%' : fmtPct(e.cfr);
                    const intText = e.sin_interes ? '$0' : '$' + fmtCompact(e.interes);
                    const capText = '$' + fmtCompact(e.recibido);

                    return `<tr${rowCls} data-id="${e.id}">
                        <td>
                            <div class="home-contraparte">${esc(e.alias)}</div>
                            <div style="margin-top:4px"><span class="badge-home ${tipoClase}">${esc(e.tipo)}</span></div>
                        </td>
                        <td style="line-height:1.4; font-size:10px;">
                            <div style="color:var(--text-muted)">Cap: <span style="color:#111827;font-weight:500">${capText}</span></div>
                            <div style="color:var(--text-muted)">Int: <span style="color:#111827;font-weight:500">${intText}</span></div>
                            <div style="color:var(--text-muted)">CFR: <span style="color:#111827;font-weight:500">${cfrText}</span></div>
                        </td>
                        <td>
                            <div class="home-amount" style="font-size:13px">$${fmtCompact(e.total_pagar)}</div>
                            <div class="home-subtext" style="font-size:9px;margin-top:4px">${plazoTexto(e)}</div>
                        </td>
                        <td>${badgeVencimiento(e)}</td>
                    </tr>`;
                }).join('');
                
                document.querySelectorAll('#tblHome .clickable').forEach(tr => {
                    tr.addEventListener('click', () => {
                        const id = parseInt(tr.dataset.id, 10);
                        const e = data.enemigos.find(x => x.id === id);
                        if (e) openDrawer(e);
                    });
                });
            }
        }

        function renderRemitosDash() {
            const rows = data.remitos.slice(0, 8);
            $('tblRemitosDash').innerHTML = rows.length ? rows.map(r => {
                const badge = r.pagado || (r.estado_cobro === 'cobrado') 
                    ? '<div style="margin-bottom:4px"><span class="badge badge-success" style="font-size:9px;padding:2px 4px">Cobrado</span></div>' 
                    : '<div style="margin-bottom:4px"><span class="badge badge-danger" style="font-size:9px;padding:2px 4px">Pendiente</span></div>';
                const ventaText = fmtDualCompact(r.precio_venta_total);
                const margenText = fmtDualCompact(r.margen);
                return `<tr style="cursor:pointer;" onclick="abrirModalFacturaOriginal(${r.id})">
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.cliente || '—')}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">#${r.id} · ${r.fecha.slice(5)}</div>
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div style="color:var(--text-muted)">Kg: <span style="color:#111827;font-weight:500">${fmt(r.kg)}</span></div>
                        <div style="color:var(--text-muted)">Log: <span style="color:#111827;font-weight:500">$${fmtCompact(r.costo_total_logistica)}</span></div>
                    </td>
                    <td>
                        <div class="home-amount" style="font-size:12px">${ventaText}</div>
                        <div class="home-subtext" style="font-size:9px;margin-top:2px;color:var(--success)">Mrg: ${margenText}</div>
                    </td>
                    <td style="text-align:center">${badge}</td>
                </tr>`;
            }).join('') : '<tr><td colspan="4"><div class="empty-state">Sin remitos de venta</div></td></tr>';
        }

        async function renderRemitosFull() {
            const all = await api('/api/remitos');
            $('remitoCount').textContent = all.length + ' registros';
            $('tblRemitosFull').innerHTML = all.length ? all.map(r => {
                const est = remitoEstado(r.estado_cobro ?? r.pagado);
                const badgeStatus = `<div style="margin-bottom:4px"><span class="badge ${est.badgeClass}" style="font-size:9px;padding:2px 4px">${est.label}</span></div>`;
                const actionBtn = est.cobrable
                    ? `<button type="button" class="btn btn-ghost btn-sm btn-cobrar-remito" data-rid="${r.id}" style="color:var(--success); border:1px solid var(--success); padding: 2px 4px; font-size:9px; width:100%">Cobrar</button>`
                    : '';
                
                const logisticaText = '$' + fmtCompact(r.costo_total_logistica);
                const ventaText = fmtDualCompact(r.precio_venta_total);
                const margenText = fmtDualCompact(r.margen);

                return `<tr style="cursor:pointer;" onclick="abrirModalFacturaOriginal(${r.id})">
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.cliente || '—')}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">#${r.id} · ${r.fecha.slice(5)}</div>
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div style="color:var(--text-muted)">Kg: <span style="color:#111827;font-weight:500">${fmt(r.kg)}</span></div>
                        <div style="color:var(--text-muted)">Log: <span style="color:#111827;font-weight:500">${logisticaText}</span></div>
                    </td>
                    <td>
                        <div class="home-amount" style="font-size:12px">${ventaText}</div>
                        <div class="home-subtext" style="font-size:9px;margin-top:2px;color:var(--success)">Mrg: ${margenText}</div>
                    </td>
                    <td style="text-align:center">
                        ${badgeStatus}
                        ${actionBtn}
                    </td>
                </tr>`;
            }).join('') : '<tr><td colspan="4"><div class="empty-state">Sin remitos de venta registrados</div></td></tr>';

            // Vincular acciones de cobro
            document.querySelectorAll('.btn-cobrar-remito').forEach(btn => {
                btn.addEventListener('click', ev => {
                    ev.stopPropagation();
                    const rid = parseInt(btn.dataset.rid, 10);
                    const remito = all.find(r => r.id === rid);
                    if (remito) abrirModalPagoRemito(remito);
                });
            });
        }

        function getHistorialPagosFiltrados() {
            const rows = data.historialPagos || [];
            if (!histPagosFiltro) return rows;
            return rows.filter(r => (r.tipo || '').toLowerCase() === histPagosFiltro);
        }

        function csvCell(val) {
            const s = val == null ? '' : String(val);
            return '"' + s.replace(/"/g, '""') + '"';
        }

        function exportHistorialCsv() {
            const rows = getHistorialPagosFiltrados();
            if (!rows.length) {
                toast('No hay movimientos para exportar', true);
                return;
            }
            const headers = ['Fecha', 'Contraparte', 'Tipo', 'Cuota', 'Plazo', 'Detalle', 'Monto cuota', 'Pagado', 'Punitorio', 'Descuento', 'Progreso op.'];
            const lines = rows.map(r => {
                const progreso = r.cuotas_pagadas_op != null && r.cuota_label
                    ? `${r.cuota_label} · ${r.cuotas_pagadas_op} pag.`
                    : r.cuota_label || '';
                return [
                    (r.fecha_pago || '').replace('T', ' ').slice(0, 16),
                    r.alias,
                    r.tipo,
                    r.cuota_label || '',
                    r.plazo_texto || '',
                    r.detalle || '',
                    r.monto_cuota_esperado,
                    r.monto_pagado,
                    r.interes_punitorio || 0,
                    r.descuento || 0,
                    progreso
                ].map(csvCell).join(';');
            });
            const csv = 'ufeff' + [headers.map(csvCell).join(';'), ...lines].join('rn');
            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const suf = histPagosFiltro ? '-' + histPagosFiltro : '';
            a.href = url;
            a.download = `historial-pagos${suf}-${new Date().toISOString().slice(0, 10)}.csv`;
            a.click();
            URL.revokeObjectURL(url);
            toast('CSV exportado');
        }

        function renderAuditoria() {
            const arr = data.auditoria || [];
            $('auditoriaCount').textContent = arr.length + ' registros';
            const showDelete = sessionUser.role === 'admin';
            $('tblAuditoria').innerHTML = arr.length ? arr.map(a => {
                const ipText = a.ip_address ? ` · IP: ${esc(a.ip_address)}` : '';
                const agentText = a.user_agent ? `<div style="font-size: 8px; color: var(--text-muted); margin-top: 2px;" title="${esc(a.user_agent)}">${esc(a.user_agent.length > 55 ? a.user_agent.substring(0, 55) + '...' : a.user_agent)}</div>` : '';
                return `<tr>
                    <td>${fmtFecha(a.fecha, true)}</td>
                    <td><strong>${esc(a.alias || 'Registro')}</strong><br><small style="color:var(--text-light)">Op ID: ${a.operacion_id || '—'}${a.usuario ? ' · ' + esc(a.usuario) : ''}${ipText}</small>${agentText}</td>
                    <td><span class="badge ${a.accion === 'ELIMINADO' ? 'badge-danger' : 'badge-success'}">${esc(a.accion)}</span></td>
                    <td class="money">$${fmt(a.monto)}</td>
                    <td>${showDelete ? `<button class="btn btn-ghost btn-sm" onclick="promptDeleteAuditoria(${a.id})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>` : ''}</td>
                </tr>`;
            }).join('') : '<tr><td colspan="5" class="empty-state">No hay registros de auditoría</td></tr>';
        }

        async function renderUsuarios() {
            if (!$('tblUsuarios')) return;
            try {
                data.usuarios = await api('/api/usuarios');
            } catch (e) {
                toast(e.message || 'No se pudieron cargar usuarios', true);
                data.usuarios = [];
            }
            const arr = data.usuarios || [];
            $('usuariosCount').textContent = arr.length + ' usuario' + (arr.length === 1 ? '' : 's');
            const roleLabel = { admin: 'Administrador', operador: 'Operador', visor: 'Visor' };
            $('tblUsuarios').innerHTML = arr.length ? arr.map(u => `
                <tr>
                    <td><strong>${esc(u.username)}</strong></td>
                    <td>${esc(u.nombre || u.username)}</td>
                    <td>
                        <select class="inp-usuario-role" data-uid="${u.id}" ${u.username === sessionUser.username ? 'disabled' : ''}>
                            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Administrador</option>
                            <option value="operador" ${u.role === 'operador' ? 'selected' : ''}>Operador</option>
                            <option value="visor" ${u.role === 'visor' ? 'selected' : ''}>Visor</option>
                        </select>
                    </td>
                    <td><span class="badge ${u.activo ? 'badge-success' : 'badge-neutral'}">${u.activo ? 'Activo' : 'Inactivo'}</span></td>
                    <td>${fmtFecha(u.created_at)}</td>
                    <td>
                        ${u.username !== sessionUser.username ? `<button type="button" class="btn btn-ghost btn-sm btn-toggle-user" data-uid="${u.id}" data-activo="${u.activo ? '0' : '1'}">${u.activo ? 'Desactivar' : 'Activar'}</button>` : '<small style="color:var(--text-muted)">Tú</small>'}
                    </td>
                </tr>
            `).join('') : '<tr><td colspan="6" class="empty-state">Sin usuarios registrados</td></tr>';

            document.querySelectorAll('.inp-usuario-role').forEach(sel => {
                sel.addEventListener('change', async () => {
                    const uid = parseInt(sel.dataset.uid, 10);
                    try {
                        await api('/api/usuarios/' + uid, {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ role: sel.value })
                        });
                        toast('Rol actualizado');
                        await renderUsuarios();
                    } catch (e) {
                        toast(e.message, true);
                        await renderUsuarios();
                    }
                });
            });
            document.querySelectorAll('.btn-toggle-user').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const uid = parseInt(btn.dataset.uid, 10);
                    const activo = btn.dataset.activo === '1';
                    try {
                        await api('/api/usuarios/' + uid, {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ activo })
                        });
                        toast(activo ? 'Usuario activado' : 'Usuario desactivado');
                        await renderUsuarios();
                    } catch (e) {
                        toast(e.message, true);
                    }
                });
            });
        }

        function applyRoleUi() {
            const isAdmin = sessionUser.role === 'admin';
            const isVisor = sessionUser.role === 'visor';
            document.querySelectorAll('[data-admin-only]').forEach(el => {
                el.style.display = isAdmin ? '' : 'none';
            });
            document.querySelectorAll('[data-write-only]').forEach(el => {
                el.style.display = isVisor ? 'none' : '';
            });
        }

        window.promptDeleteAuditoria = (id) => {
            selectedAuditId = id;
            $('inpPasswordAuditoria').value = '';
            $('modalPasswordAuditoria').classList.add('open');
        };

        function renderHistorialPagos() {
            const rows = getHistorialPagosFiltrados();
            const total = (data.historialPagos || []).length;
            const label = rows.length === total
                ? rows.length + ' movimiento' + (rows.length === 1 ? '' : 's')
                : rows.length + ' de ' + total + ' movimientos';
            $('histPagosCount').textContent = label;
            if (!rows.length) {
                $('tblHistorialPagos').innerHTML = '<tr><td colspan="11"><div class="xls-empty"><strong>Sin pagos' + (histPagosFiltro ? ' para este filtro' : ' registrados') + '</strong><br>Usá el botón Pagar en una obligación para registrar cuotas</div></td></tr>';
                return;
            }
            $('tblHistorialPagos').innerHTML = rows.map(r => {
                const tipoCls = (r.tipo || 'neutro').toLowerCase();
                const punText = r.interes_punitorio > 0 ? `<div style="color:var(--danger);font-size:9px;margin-top:2px">+ Pun: $${fmtCompact(r.interes_punitorio)}</div>` : '';
                const descText = r.descuento > 0 ? `<div style="color:var(--success);font-size:9px;margin-top:2px">- Desc: $${fmtCompact(r.descuento)}</div>` : '';
                const progreso = r.cuotas_pagadas_op != null && r.cuota_label
                    ? `${r.cuota_label} · ${r.cuotas_pagadas_op} pag.`
                    : r.cuota_label || '—';
                return `<tr>
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.alias)}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">${(r.fecha_pago || '').slice(0, 10)}</div>
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div style="color:#111827;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:80px" title="${esc(r.detalle||'')}">${esc(r.detalle || '—')}</div>
                        <div style="color:var(--text-muted);font-size:9px;margin-top:2px">${esc(r.plazo_texto || '—')}</div>
                    </td>
                    <td>
                        <div class="home-amount" style="font-size:13px;color:var(--success)">$${fmtCompact(r.monto_pagado)}</div>
                        ${punText}${descText}
                    </td>
                    <td style="text-align:center">
                        <div style="margin-bottom:4px"><span class="badge-home ${tipoCls}">${esc(r.tipo)}</span></div>
                        <div style="font-size:9px;color:var(--text-muted)">${progreso}</div>
                    </td>
                </tr>`;
            }).join('');
        }

        function renderBancos() {
            $('tblBancos').innerHTML = data.bancos.length ? data.bancos.map(b => `
                <tr><td>${esc(b.nombre)}</td><td>$${fmt(b.limite)}</td></tr>
            `).join('') : '<tr><td colspan="2" style="color:var(--text-muted);padding:16px">Sin entidades bancarias</td></tr>';
        }

        function renderBulkLots() {
            const table = $('tblBulkLots');
            if (!table) return;
            const now = new Date();
            now.setHours(0,0,0,0);
            
            table.innerHTML = data.bulk && data.bulk.length ? data.bulk.map(b => {
                let badge = '';
                if (b.kg_remanentes <= 0) {
                    badge = '<span class="badge badge-neutral">Agotado</span>';
                } else {
                    if (b.fecha_vencimiento) {
                        const vto = new Date(b.fecha_vencimiento + 'T00:00:00');
                        const diffDays = Math.ceil((vto - now) / (1000 * 60 * 60 * 24));
                        if (diffDays < 0) {
                            badge = '<span class="badge badge-danger">Vencido</span>';
                        } else if (diffDays <= 7) {
                            badge = '<span class="badge badge-warning">Vence pronto</span>';
                        } else {
                            badge = '<span class="badge badge-success">Activo</span>';
                        }
                    } else {
                        badge = '<span class="badge badge-success">Activo</span>';
                    }
                }
                
                const provText = b.proveedor ? `<div style="font-size:10px;font-weight:600;color:var(--text-primary);margin-top:2px;">Prov: ${esc(b.proveedor)}</div>` : '';
                const loteNumText = b.numero_lote ? ` - ${esc(b.numero_lote)}` : '';
                const vtoText = b.fecha_vencimiento ? `<div style="font-size:9px;color:var(--text-muted);margin-top:2px;">Vto: ${b.fecha_vencimiento}</div>` : '';
                
                return `<tr>
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Lote #${b.id}${loteNumText}</div>
                        ${provText}
                    </td>
                    <td style="font-size:11px;line-height:1.3">
                        <div>Com: ${b.fecha}</div>
                        ${vtoText}
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div style="font-weight:600">${fmt(b.kg_remanentes)} kg</div>
                        <div style="color:var(--text-muted);font-size:9px;margin-top:2px">Orig: ${fmt(b.kg_totales)} kg</div>
                    </td>
                    <td>
                        <div class="home-amount" style="font-size:13px">$${fmtCompact(b.costo_total_bulk)}</div>
                        <div class="home-subtext" style="font-size:9px;margin-top:2px">x Kg: $${fmtCompact(b.costo_kg)}</div>
                    </td>
                    <td style="text-align:center">${badge}</td>
                </tr>`;
            }).join('') : '<tr><td colspan="5" style="color:var(--text-muted);padding:16px;text-align:center">Sin lotes de compra bulk registrados</td></tr>';
        }

        window.filtrarClientes = (term) => {
            const termLower = term.toLowerCase();
            document.querySelectorAll('#tblClientes .clickable').forEach(el => {
                const text = el.textContent.toLowerCase();
                el.style.display = text.includes(termLower) ? 'block' : 'none';
            });
        };

        window.filtrarInrecuperables = (term) => {
            const termLower = term.toLowerCase();
            document.querySelectorAll('#tblInrecuperables .clickable').forEach(el => {
                const text = el.textContent.toLowerCase();
                el.style.display = text.includes(termLower) ? 'block' : 'none';
            });
        };

        let selectedClienteId = null;

        function renderClientes() {
            const table = $('tblClientes');
            const tableInrec = $('tblInrecuperables');
            if (!table) return;
            
            const activos = (data.clientes || []).filter(c => !c.inrecuperable);
            const inrecuperables = (data.clientes || []).filter(c => c.inrecuperable);
            
            const countEl = $('clientCount');
            if (countEl) countEl.textContent = activos.length + ' clientes';
            const inrecCountEl = $('inrecCount');
            if (inrecCountEl) inrecCountEl.textContent = inrecuperables.length + ' clientes';
            
            const renderRow = c => '<div class="clickable" data-id="' + c.id + '" style="cursor:pointer; margin-bottom:15px; width:100%;">' + cobranzaCardHtml(c, true) + '</div>';

            table.innerHTML = activos.length ? activos.map(renderRow).join('') : '<div style="color:var(--text-muted);padding:20px;text-align:center">Sin clientes activos</div>';
            
            if (tableInrec) {
                tableInrec.innerHTML = inrecuperables.length ? inrecuperables.map(renderRow).join('') : '<div style="color:var(--text-muted);padding:20px;text-align:center">No hay clientes inrecuperables</div>';
            }
            
            document.querySelectorAll('#tblClientes .clickable, #tblInrecuperables .clickable').forEach((row, idx) => {
                row.addEventListener('click', () => {
                    const cid = parseInt(row.dataset.id, 10);
                    openClientDrawer(cid);
                });
            });

            // Populate datalist for Ventas Express
            const datalist = $('clientesExpressList');
            if (datalist) {
                datalist.innerHTML = (data.clientes || []).map(c => `<option value="${esc(c.nombre)}"></option>`).join('');
            }
        }

        function badgeCobranzaMora(c) {
            if (c.en_mora) return '<span class="badge-home vencido" style="line-height:1.2;text-align:center;display:inline-block">En<br>mora</span>';
            if (c.limite_superado) return '<span class="badge-home vencido" style="line-height:1.2;text-align:center;display:inline-block">Límite<br>superado</span>';
            return '<span class="badge-home verde" style="line-height:1.2;text-align:center;display:inline-block">Al<br>día</span>';
        }

        function cobranzaCardHtml(c, hideButton = false) {
            const scoreIcons = { A: '🅰️', B: '📊', C: '⚠️', D: '⛔' };
            const icon = scoreIcons[c.scoring] || '🏪';
            const pctUso = c.techo_deuda > 0 ? Math.round((c.saldo_actual / c.techo_deuda) * 100) + '%' : '—';
            const ultimoPago = c.fecha_ultimo_pago ? fmtFecha(c.fecha_ultimo_pago) : 'Nunca';
            const moraText = c.en_mora
                ? (c.oldest_unpaid ? `En mora · desde ${fmtFecha(c.oldest_unpaid)}` : 'En mora')
                : (c.oldest_unpaid ? `Al día · ${fmtFecha(c.oldest_unpaid)}` : 'Sin vencimientos');
            const totalCls = c.en_mora ? ' highlight' : '';

            return `
            <div class="debt-card cobranza-debt-card">
                <div class="card-header">
                    <div class="card-title-group">
                        <div class="icon-box">${icon}</div>
                        <div>
                            <h3 class="card-title">${esc(c.nombre)}</h3>
                            <span class="pill pill-type">Scoring ${esc(c.scoring)}</span>
                        </div>
                    </div>
                    ${badgeCobranzaMora(c)}
                </div>
                <div class="card-body">
                    <div class="data-col">
                        <span class="data-label">Scoring</span>
                        <span class="data-value">${esc(c.scoring)}</span>
                    </div>
                    <div class="data-col">
                        <span class="data-label">Límite</span>
                        <span class="data-value">$${fmtCompact(c.techo_deuda)}</span>
                    </div>
                    <div class="data-col">
                        <span class="data-label">Uso crédito</span>
                        <span class="data-value">${pctUso}</span>
                    </div>
                </div>
                <div class="card-footer">
                    <div class="amount-section">
                        <span class="amount-label">TOTAL A COBRAR</span>
                        <span class="amount-total${totalCls}">$${fmtCompact(c.saldo_actual)}</span>
                    </div>
                    <div class="due-details">
                        Último pago: <strong>${ultimoPago}</strong><br>${moraText}
                    </div>
                </div>
                ${hideButton ? '' : `<button type="button" class="btn btn-primary btn-cobranza-accion cobranza-card-btn" data-cid="${c.id}">Cobrar / Ver</button>`}
            </div>`;
        }

        function renderCobranzas() {
            const grid = $('cobranzasGrid');
            if (!grid) return;
            let conDeuda = data.clientes.filter(c => c.saldo_actual > 0);
            conDeuda.sort((a,b) => b.saldo_actual - a.saldo_actual);
            
            let totalDeuda = conDeuda.reduce((acc, c) => acc + c.saldo_actual, 0);
            const enMora = conDeuda.filter(c => c.en_mora);
            const montoMora = enMora.reduce((acc, c) => acc + c.saldo_actual, 0);

            if ($('cobranzasCount')) $('cobranzasCount').textContent = `${conDeuda.length} clientes con saldo pendiente`;

            const isMobile = window.innerWidth < 768;
            const formatVal = (val) => isMobile ? fmtCompact(val) : fmt(val);

            if ($('cobBarClientesMora')) $('cobBarClientesMora').textContent = enMora.length;
            if ($('cobBarClientesMoraSub')) $('cobBarClientesMoraSub').textContent = enMora.length === 1 ? 'Cliente con facturas vencidas' : 'Clientes con facturas vencidas';
            if ($('cobBarMontoMora')) $('cobBarMontoMora').textContent = '$' + formatVal(montoMora);
            if ($('cobBarCapitalTotal')) $('cobBarCapitalTotal').textContent = '$' + formatVal(totalDeuda);
            if ($('cobBarCapitalSub')) $('cobBarCapitalSub').textContent = `${conDeuda.length} clientes con saldo`;
            
            if (!conDeuda.length) {
                grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1;padding:40px;text-align:center;color:var(--text-secondary);">No hay clientes con saldo pendiente</div>';
            } else {
                grid.innerHTML = conDeuda.map(cobranzaCardHtml).join('');
            }

            grid.querySelectorAll('.btn-cobranza-accion').forEach(btn => {
                btn.addEventListener('click', ev => {
                    ev.stopPropagation();
                    abrirAccionCobranza(parseInt(btn.dataset.cid, 10));
                });
            });
        }

        function pagoCentralSaldo(e) {
            const s = Number(e.saldo_pendiente);
            if (!Number.isNaN(s) && s > 0) return s;
            return Number(e.total_pagar || 0);
        }

        function pagoCentralCardHtml(e) {
            return deudaCardHtml(e, {
                clickable: false,
                actionBtnHtml: `<button type="button" class="btn btn-primary btn-pago-central-accion cobranza-card-btn" data-deuda-id="${e.id}">Pagar / Ver</button>`
            });
        }

        function abrirAccionPagoCentral(deudaId) {
            const e = (data.enemigos || []).find(x => x.id === deudaId);
            if (!e) return;
            pagoCentralDeudaActual = e;
            const saldo = pagoCentralSaldo(e);
            if ($('modalPagoCentralAccionSub')) {
                $('modalPagoCentralAccionSub').textContent = `${e.alias} · Saldo $${fmt(saldo)}`;
            }
            const btnPagar = $('btnPagoCentralPagar');
            if (btnPagar) {
                const puedePagar = e.tiene_cuotas && !e.completa;
                btnPagar.disabled = !puedePagar;
                btnPagar.style.opacity = puedePagar ? '1' : '0.5';
                btnPagar.title = puedePagar ? '' : 'Esta obligación no usa plan de cuotas';
            }
            $('modalPagoCentralAccion')?.classList.add('open');
        }

        function cerrarModalPagoCentralAccion() {
            $('modalPagoCentralAccion')?.classList.remove('open');
            pagoCentralDeudaActual = null;
        }

        function pagoCentralElegirVer() {
            const e = pagoCentralDeudaActual;
            cerrarModalPagoCentralAccion();
            if (e) openDrawer(e);
        }

        function pagoCentralElegirPagar() {
            const e = pagoCentralDeudaActual;
            if (!e) return;
            cerrarModalPagoCentralAccion();
            selectedDeuda = e;
            if (e.tiene_cuotas && !e.completa) {
                abrirModalPago();
            } else {
                openDrawer(e);
                toast('Abrí el detalle para gestionar esta obligación', false);
            }
        }

        function renderPagosCentral() {
            const grid = $('pagoCentralGrid');
            if (!grid) return;
            const pendientes = (data.enemigos || []).filter(e => !e.completa && pagoCentralSaldo(e) > 0.009);
            const sorted = sortEnemigosPrioridad(pendientes);
            const vencidas = pendientes.filter(e => e.vencido);
            const totalPagar = pendientes.reduce((acc, e) => acc + pagoCentralSaldo(e), 0);
            const montoVencido = vencidas.reduce((acc, e) => acc + pagoCentralSaldo(e), 0);

            if ($('pagoCentralCount')) {
                $('pagoCentralCount').textContent = `${pendientes.length} obligacion${pendientes.length === 1 ? '' : 'es'} pendiente${pendientes.length === 1 ? '' : 's'}`;
            }

            const isMobile = window.innerWidth < 768;
            const formatVal = (val) => isMobile ? fmtCompact(val) : fmt(val);

            if ($('pcBarObligaciones')) $('pcBarObligaciones').textContent = pendientes.length;
            if ($('pcBarMontoVencido')) $('pcBarMontoVencido').textContent = '$' + formatVal(montoVencido);
            if ($('pcBarTotalPagar')) $('pcBarTotalPagar').textContent = '$' + formatVal(totalPagar);
            if ($('pcBarTotalSub')) $('pcBarTotalSub').textContent = `${pendientes.length} obligaciones activas`;

            if (!pendientes.length) {
                grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1;padding:40px;text-align:center;color:var(--text-secondary);">No hay obligaciones pendientes de pago</div>';
            } else {
                grid.innerHTML = sorted.map(pagoCentralCardHtml).join('');
            }

            grid.querySelectorAll('.btn-pago-central-accion').forEach(btn => {
                btn.addEventListener('click', ev => {
                    ev.stopPropagation();
                    abrirAccionPagoCentral(parseInt(btn.dataset.deudaId, 10));
                });
            });
        }

        let currentClientData = null;
        let currentClientFilter = 'all';
        let clientDetailReturnView = 'clientes';

        async function openClientDrawer(clientId) {
            if (!$('view-cliente-detalle')) {
                toast('Actualizando aplicación...', false);
                setTimeout(() => window.location.reload(), 500);
                return;
            }
            const currentView = document.querySelector('.view.active')?.id?.replace(/^view-/, '') || '';
            if (currentView && currentView !== 'cliente-detalle') {
                clientDetailReturnView = currentView;
            }
            selectedClienteId = clientId;
            switchView('cliente-detalle');
            $('viewClientBody').innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px; background:rgba(255,255,255,0.7); border-radius:10px; align-self:center; margin-top:20px;">Cargando historial de chat...</div>';
            
            try {
                const c = await api('/api/clientes/' + clientId);
                if (selectedClienteId !== clientId) return;
                
                currentClientData = c;
                currentClientFilter = 'all';
                $('viewClientTitle').textContent = c.nombre;
                
                const initials = c.nombre.substring(0, 2).toUpperCase();
                $('wspAvatar').textContent = initials;
                
                if (c.saldo_actual > 0) {
                    $('viewClientSubtitle').innerHTML = `Deuda: <span style="color:#ef4444; font-weight:bold;">$${fmt(c.saldo_actual)}</span>`;
                } else {
                    $('viewClientSubtitle').innerHTML = `En línea`;
                }
                
                $('wspSaldoAtrasado').textContent = '$' + fmt(c.saldo_actual);
                
                const unpaid = (c.remitos || []).filter(r => remitoSaldoPendiente(r) > 0);
                // Si hay facturas impagas, tomamos la más reciente como "Factura Actual"
                const saldoActual = unpaid.length > 0 ? remitoSaldoPendiente(unpaid[unpaid.length - 1]) : 0;
                $('wspSaldoActual').textContent = '$' + fmt(saldoActual);
                
                window.wspSelectedInvoices = [];
                $('btnDeleteSeleccionados').style.display = 'none';
                
                // Toggle Topbar WSP Mode
                if ($('topbarNormalMode')) $('topbarNormalMode').style.setProperty('display', 'none', 'important');
                if ($('topbarWspMode')) $('topbarWspMode').style.setProperty('display', 'flex', 'important');
                if ($('menuToggle')) $('menuToggle').style.setProperty('display', 'none', 'important');
                if ($('btnVolverTop')) $('btnVolverTop').style.setProperty('display', 'block', 'important');
                if ($('btnClientPrintWsp')) $('btnClientPrintWsp').style.setProperty('display', 'flex', 'important');
                if ($('btnTogglePro')) $('btnTogglePro').style.setProperty('display', 'none', 'important');

                renderClientDashboard();
                
            } catch (e) {
                $('viewClientBody').innerHTML = `<div style="color:var(--danger);text-align:center;padding:20px; background:#fff; border-radius:10px; align-self:center; margin-top:20px;">Error al cargar detalles: ${esc(e.message)}</div>`;
            }
        }
        window.openClientDrawer = openClientDrawer;

        function volverDesdeClienteDetalle() {
            // Restore Topbar Normal Mode
            if ($('topbarNormalMode')) $('topbarNormalMode').style.setProperty('display', '', '');
            if ($('topbarWspMode')) $('topbarWspMode').style.setProperty('display', 'none', 'important');
            if ($('menuToggle')) $('menuToggle').style.setProperty('display', '', '');
            if ($('btnVolverTop')) $('btnVolverTop').style.setProperty('display', 'none', 'important');
            if ($('btnClientPrintWsp')) $('btnClientPrintWsp').style.setProperty('display', 'none', 'important');
            if ($('btnTogglePro')) $('btnTogglePro').style.setProperty('display', '', '');

            switchView(clientDetailReturnView || 'clientes');
        }
        window.volverDesdeClienteDetalle = volverDesdeClienteDetalle;

        function abrirPerfilCliente() {
            $('modalWspProfile').classList.add('open');
        }
        window.abrirPerfilCliente = abrirPerfilCliente;

        function toggleSelectFactura(el, rid, saldoAmnt) {
            el.classList.toggle('checked');
            if (el.classList.contains('checked')) {
                window.wspSelectedInvoices.push(rid);
            } else {
                window.wspSelectedInvoices = window.wspSelectedInvoices.filter(id => id !== rid);
            }
            $('btnDeleteSeleccionados').style.display = window.wspSelectedInvoices.length > 0 ? 'inline-block' : 'none';
        }
        window.toggleSelectFactura = toggleSelectFactura;

        async function eliminarFacturasSeleccionadas() {
            if (!window.wspSelectedInvoices || window.wspSelectedInvoices.length === 0) return;
            const ids = window.wspSelectedInvoices;
            const pass = await promptMasterPasswordAsync("Eliminar " + ids.length + " factura(s)");
            if (!pass) return;
            
            try {
                for (const rid of ids) {
                    await api('/api/remitos/' + rid, {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json', 'X-Master-Password': pass }
                    });
                }
                toast('Facturas eliminadas');
                openClientDrawer(selectedClienteId);
                await loadAll();
            } catch (e) {
                toast(e.message, true);
            }
        }
        window.eliminarFacturasSeleccionadas = eliminarFacturasSeleccionadas;

        function abrirCobranzaWsp() {
            abrirCobranzaClienteGlobal(selectedClienteId);
        }
        window.abrirCobranzaWsp = abrirCobranzaWsp;

        function renderClientDashboard() {
            if (!currentClientData) return;
            const c = currentClientData;
            
            const limitStatus = c.limite_superado 
                ? '<span style="color:var(--danger)">Bloqueado (Límite superado)</span>' 
                : '<span style="color:var(--success)">Activo y Operativo</span>';
                
            const items = [];
            (c.remitos || []).forEach(r => items.push({ type: 'remito', date: r.fecha, data: r }));
            (c.pagos || []).forEach(p => items.push({ type: 'pago', date: p.fecha, data: p }));
            
            items.sort((a, b) => new Date(a.date) - new Date(b.date));
            
            let html = '<div style="font-weight:800; font-size:1.1rem; color:#0f172a; margin-bottom:16px; padding:0 4px;">Historial de Cuenta</div>';
            let lastDate = '';
            
            if (items.length === 0) {
                html += `<div style="color:#64748b;text-align:center;padding:32px 20px;background:#ffffff;border:1px dashed #cbd5e1; border-radius:12px; margin-top:12px;">No hay movimientos registrados en esta cuenta.</div>`;
            }
            
            items.forEach(item => {
                const dateStr = item.date || new Date().toISOString();
                const day = dateStr.slice(0, 10);
                if (day !== lastDate) {
                    html += `<div style="text-align:center; margin:24px 0 16px 0;"><span style="background:#e2e8f0; color:#475569; font-size:0.75rem; font-weight:700; padding:4px 12px; border-radius:12px; text-transform:uppercase; letter-spacing:0.5px;">${day}</span></div>`;
                    lastDate = day;
                }
                
                const time = dateStr.slice(11, 16) || '12:00';
                
                if (item.type === 'remito') {
                    const r = item.data;
                    const est = remitoEstado(r.estado_cobro ?? r.pagado);
                    const pagadoAmnt = Number(r.monto_pagado || 0);
                    const saldoAmnt = remitoSaldoPendiente(r);
                    const isPaid = (r.pagado ?? r.estado_cobro) === 'cobrado' || (r.pagado ?? r.estado_cobro) === 1 || (r.pagado ?? r.estado_cobro) === 2;
                    
                    const checkedState = (window.wspSelectedInvoices || []).includes(r.id) ? 'checked' : '';
                    const badgeBg = isPaid ? '#dcfce7' : '#f1f5f9';
                    const badgeColor = isPaid ? '#166534' : '#475569';
                    const leftBorder = (!isPaid && saldoAmnt > 0) ? '#ef4444' : '#10b981';
                    
                    html += `
                    <div style="background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:16px; margin-bottom:12px; box-shadow:0 1px 3px rgba(0,0,0,0.05); display:flex; gap:12px; align-items:flex-start; position:relative; overflow:hidden;">
                        <div class="wsp-checkbox ${checkedState}" data-rid="${r.id}" onclick="toggleSelectFactura(this, ${r.id}, ${saldoAmnt})" style="margin-top:2px;">✓</div>
                        <div style="flex:1; cursor:pointer;" onclick="abrirModalFacturaOriginal(${r.id})">
                            <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
                                <div>
                                    <div style="font-weight:800; color:#0f172a; font-size:1.05rem;">Factura #${r.id}</div>
                                    <div style="font-size:0.8rem; color:#64748b; margin-top:2px; font-weight:500;">${r.tipo_corte || 'Variedad'} · ${remitoCantidad(r)} u · ${fmt(r.kg)} kg</div>
                                </div>
                                <span style="font-size:0.7rem; font-weight:700; background:${badgeBg}; color:${badgeColor}; padding:4px 8px; border-radius:6px; text-transform:uppercase;">${est.label}</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; align-items:flex-end;">
                                <div>
                                    <div style="font-size:1.15rem; font-weight:800; color:#0f172a;">$${fmt(r.precio_venta_total)}</div>
                                    ${saldoAmnt > 0 && pagadoAmnt > 0 ? `<div style="font-size:0.8rem; color:#ea580c; font-weight:700; margin-top:2px;">Resta: $${fmt(saldoAmnt)}</div>` : ''}
                                </div>
                                <div style="font-size:0.75rem; color:#94a3b8; font-weight:600;">
                                    ${time}
                                </div>
                            </div>
                        </div>
                        <div style="position:absolute; left:0; top:0; bottom:0; width:4px; background:${leftBorder};"></div>
                    </div>`;
                } else if (item.type === 'pago') {
                    const p = item.data;
                    html += `
                    <div style="background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px; padding:16px; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center;">
                        <div style="display:flex; align-items:center; gap:12px;">
                            <div style="width:36px; height:36px; border-radius:50%; background:#dcfce7; color:#166534; display:flex; align-items:center; justify-content:center; font-size:1.1rem; font-weight:bold;">↓</div>
                            <div>
                                <div style="font-weight:700; color:#0f172a; font-size:0.95rem;">Pago Registrado #${p.id}</div>
                                <div style="font-size:0.75rem; color:#64748b; font-weight:500;">${time}</div>
                            </div>
                        </div>
                        <div style="display:flex; align-items:center; gap:12px;">
                            <div style="font-size:1.15rem; font-weight:800; color:#166534;">+$${fmt(p.monto)}</div>
                            <button onclick="eliminarPagoCliente(${p.id})" style="background:#fee2e2; border:none; color:#ef4444; cursor:pointer; font-size:0.9rem; width:28px; height:28px; border-radius:6px; display:flex; align-items:center; justify-content:center; transition:background 0.2s;" onmouseover="this.style.background='#fca5a5'" onmouseout="this.style.background='#fee2e2'">❌</button>
                        </div>
                    </div>`;
                }
            });
            
            let profileHtml = `
                <div style="background:linear-gradient(135deg, #1e293b, #0f172a); border-radius:16px; padding:24px 20px; color:#fff; display:flex; flex-direction:column; align-items:center; gap:16px; margin-bottom:24px; box-shadow:0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -2px rgba(0,0,0,0.05); position:relative; overflow:hidden;">
                    <div style="position:absolute; top:-20px; right:-20px; width:100px; height:100px; background:rgba(255,255,255,0.05); border-radius:50%;"></div>
                    <div style="width:80px; height:80px; border-radius:50%; background:linear-gradient(to bottom right, #3b82f6, #1d4ed8); display:flex; align-items:center; justify-content:center; font-size:2rem; font-weight:800; color:#fff; border:3px solid rgba(255,255,255,0.2); box-shadow:0 4px 10px rgba(0,0,0,0.2); z-index:1;">
                        ${c.nombre ? c.nombre.substring(0, 2).toUpperCase() : 'C'}
                    </div>
                    <div style="text-align:center; z-index:1;">
                        <div style="font-size:1.6rem; font-weight:800; margin-bottom:4px; letter-spacing:-0.5px;">${c.nombre || 'Cliente'}</div>
                        <div style="display:inline-flex; align-items:center; gap:6px; background:rgba(255,255,255,0.1); padding:4px 10px; border-radius:20px; font-size:0.85rem; font-weight:600; backdrop-filter:blur(4px);">
                            <div style="width:8px; height:8px; border-radius:50%; background:${c.limite_superado ? '#ef4444' : '#10b981'};"></div>
                            ${c.limite_superado ? 'Límite Superado' : 'Activo y Operativo'}
                        </div>
                    </div>
                </div>
                
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:24px;">
                    <div style="background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:16px; display:flex; flex-direction:column; gap:4px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                        <span style="font-size:0.75rem; color:#64748b; font-weight:700; text-transform:uppercase;">Deuda Actual</span>
                        <span style="font-size:1.3rem; font-weight:800; color:${c.saldo_actual > 0 ? '#ef4444' : '#10b981'};">$${fmt(c.saldo_actual || 0)}</span>
                    </div>
                    <div style="background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:16px; display:flex; flex-direction:column; gap:4px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                        <span style="font-size:0.75rem; color:#64748b; font-weight:700; text-transform:uppercase;">Límite Crédito</span>
                        <span style="font-size:1.3rem; font-weight:800; color:#0f172a;">$${fmt(c.techo_deuda || 0)}</span>
                    </div>
                </div>

                <div style="background:#fff; padding:20px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 1px 3px rgba(0,0,0,0.05); font-size:0.95rem; margin-bottom:24px;">
                    <h3 style="font-size:1rem; font-weight:800; color:#0f172a; margin-top:0; margin-bottom:16px; display:flex; align-items:center; gap:8px;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg> Datos del Cliente
                    </h3>
                    <div style="display:flex; flex-direction:column; gap:12px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="color:#64748b; font-weight:600;">Scoring</span>
                            <span style="background:#f1f5f9; padding:4px 10px; border-radius:8px; font-weight:700; color:#334155;">${c.scoring || 'A'}</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="color:#64748b; font-weight:600;">Fecha de Alta</span>
                            <span style="color:#0f172a; font-weight:600;">${(c.created_at || '').slice(0, 10)}</span>
                        </div>
                        ${c.cuit ? `<div style="display:flex; justify-content:space-between; align-items:center;"><span style="color:#64748b; font-weight:600;">CUIT</span> <span style="color:#0f172a; font-weight:600;">${esc(c.cuit)}</span></div>` : ''}
                        ${c.direccion ? `<div style="display:flex; justify-content:space-between; align-items:center;"><span style="color:#64748b; font-weight:600;">Dirección</span> <span style="color:#0f172a; font-weight:600; text-align:right;">${esc(c.direccion)}</span></div>` : ''}
                        ${c.telefono ? `<div style="display:flex; justify-content:space-between; align-items:center;"><span style="color:#64748b; font-weight:600;">Teléfono</span> <span style="color:#0f172a; font-weight:600;">${esc(c.telefono)}</span></div>` : ''}
                        ${c.email ? `<div style="display:flex; justify-content:space-between; align-items:center;"><span style="color:#64748b; font-weight:600;">Email</span> <span style="color:#0f172a; font-weight:600;">${esc(c.email)}</span></div>` : ''}
                    </div>
                </div>
            `;
            
            // Add Profile to the top of the chat body instead of a hidden modal
            $('viewClientBody').innerHTML = profileHtml + html;
            $('wspProfileContent').innerHTML = profileHtml;
            
            if (sessionUser.role === 'admin') {
                $('btnClientEliminarWsp').style.display = 'block';
                $('btnClientEliminarWsp').onclick = () => {
                    $('modalWspProfile').classList.remove('open');
                    eliminarCliente(c.id);
                };
            } else {
                $('btnClientEliminarWsp').style.display = 'none';
            }
            
            if (c.saldo_actual > 0 && c.scoring !== 'D') {
                $('btnClientIncobrableWsp').style.display = 'block';
                // Attach correct action if incobrable is supported
            } else {
                $('btnClientIncobrableWsp').style.display = 'none';
            }
        }

        function closeClientDrawer() {
            $('drawerClientOverlay').classList.remove('open');
            selectedClienteId = null;
        }

        function updateSelectedRow(rows) {
            rows.forEach((r, idx) => {
                r.classList.toggle('selected', idx === activeRowIndex);
                if (idx === activeRowIndex) {
                    r.scrollIntoView({ block: 'nearest' });
                }
            });
        }

        function openDrawer(e) {
            selectedDeuda = e;
            $('drawerTitle').textContent = e.alias + ' — ' + e.tipo;
            let extra = '';
            if (e.es_tarjeta) {
                extra = `
                <div class="drawer-row"><span class="lbl">Fecha cierre</span><span class="val">${e.fecha_cierre || '—'}</span></div>
                <div class="drawer-row"><span class="lbl">Fecha vencimiento</span><span class="val">${e.fecha_vencimiento || '—'}</span></div>
                <div class="drawer-row"><span class="lbl">Cuotas</span><span class="val">${e.cuotas || '—'}</span></div>
                <div class="drawer-row"><span class="lbl">Cuotas pagadas</span><span class="val">${e.cuotas_pagadas || 0} de ${e.cuotas || '—'}</span></div>
                `;
            } else if (e.es_proveedor) {
                extra = `
                <div class="drawer-row"><span class="lbl">Kilos</span><span class="val">${e.kg ? fmt(e.kg) + ' kg' : '—'}</span></div>
                <div class="drawer-row"><span class="lbl">Precio / kg</span><span class="val">${e.precio_kg ? '$' + fmt(e.precio_kg) : '—'}</span></div>
                <div class="drawer-row"><span class="lbl">Plazo de pago</span><span class="val" style="color:var(--accent)">${e.plazo_texto || (e.plazo_dias ? e.plazo_dias + ' días' : '—')}</span></div>
                <div class="drawer-row"><span class="lbl">Vencimiento</span><span class="val">${e.fecha_vencimiento || '—'}</span></div>
                <div class="drawer-row"><span class="lbl">Reserva diaria (caja)</span><span class="val" style="color:var(--warning)">$${fmt(e.reserva_diaria || 0)}</span></div>
                `;
            }
            let pagosHtml = '';
            if (e.tiene_cuotas) {
                pagosHtml = `
                <div class="drawer-row"><span class="lbl">Cuota en curso</span><span class="val">${e.completa ? 'Completa' : e.cuota_en_curso + ' de ' + e.cuotas_total}</span></div>
                <div class="drawer-row"><span class="lbl">Monto por cuota</span><span class="val">$${fmt(e.monto_cuota)}</span></div>
                <div class="drawer-row"><span class="lbl">Saldo pendiente</span><span class="val">$${fmt(e.saldo_pendiente)}</span></div>
                ${e.cuotas_vencidas > 1 ? `<div class="drawer-row"><span class="lbl">Cuotas vencidas</span><span class="val" style="color:var(--danger)">${e.cuotas_vencidas_lista.join(', ')}</span></div>` : ''}
                `;
            }
            $('drawerBody').innerHTML = `
                ${vencBannerHtml(e)}
                ${e.es_cheque ? `
                <div class="drawer-row"><span class="lbl">Monto del cheque</span><span class="val" style="color:var(--accent)">$${fmt(e.total_pagar)}</span></div>
                <div class="drawer-row"><span class="lbl">Interés financiero</span><span class="val">Sin interés</span></div>
                <div class="drawer-row"><span class="lbl">Reserva diaria (caja)</span><span class="val" style="color:var(--warning)">$${fmt(e.reserva_diaria || 0)}</span></div>
                <div class="drawer-row"><span class="lbl">Saldo pendiente</span><span class="val">$${fmt(e.saldo_pendiente || e.total_pagar)}</span></div>
                ` : e.es_proveedor ? `
                <div class="drawer-row"><span class="lbl">Compra</span><span class="val">${e.kg ? fmt(e.kg) + ' kg × $' + fmt(e.precio_kg) : '—'}</span></div>
                <div class="drawer-row"><span class="lbl">Total a pagar</span><span class="val" style="color:var(--accent)">$${fmt(e.total_pagar)}</span></div>
                <div class="drawer-row"><span class="lbl">Interés total</span><span class="val">${e.sin_interes ? 'Sin interés' : '$' + fmt(e.interes)}</span></div>
                <div class="drawer-row"><span class="lbl">Saldo pendiente</span><span class="val">$${fmt(e.saldo_pendiente || e.total_pagar)}</span></div>
                ` : `
                <div class="drawer-row"><span class="lbl">${e.es_tarjeta ? 'Resumen / consumo' : 'Monto recibido'}</span><span class="val">$${fmt(e.recibido)}</span></div>
                <div class="drawer-row"><span class="lbl">Total a pagar</span><span class="val" style="color:var(--accent)">$${fmt(e.total_pagar)}</span></div>
                <div class="drawer-row"><span class="lbl">Interés total</span><span class="val">${e.sin_interes ? 'Sin interés' : '$' + fmt(e.interes)}</span></div>
                `}
                ${extra}
                ${pagosHtml}
                ${(e.es_tarjeta || e.es_cheque || e.es_proveedor) && e.fecha_vencimiento ? `
                <div class="drawer-row"><span class="lbl">Estado vencimiento</span><span class="val">${badgeVencimiento(e)}</span></div>
                <div class="drawer-row"><span class="lbl">${e.vencido ? 'Días de retraso' : 'Días faltantes'}</span><span class="val" style="color:${e.vencido?'var(--danger)':'var(--success)'}">${e.vencido ? e.dias_retraso + ' días' : (e.dias_faltantes ?? '—') + (e.dias_faltantes != null ? ' días' : '')}</span></div>
                ` : ''}
                <div class="drawer-row"><span class="lbl">Plazo CFR</span><span class="val">${plazoTexto(e)}</span></div>
                <div class="drawer-row"><span class="lbl">CFR mensual</span><span class="val" style="color:${e.urgente?'var(--danger)':'var(--success)'}">${fmtPct(e.cfr)}</span></div>
                <div class="drawer-row"><span class="lbl">Prioridad</span><span class="val">${e.prioridad ? 'Máxima' : 'Normal'}</span></div>
                <div class="drawer-row"><span class="lbl">Registrado</span><span class="val">${e.created_at || '—'}</span></div>
            `;
            $('drawerPagar').style.display = (e.tiene_cuotas && !e.completa) ? 'inline-flex' : 'none';
            $('drawerOverlay').classList.add('open');
        }

        function closeDrawer() {
            $('drawerOverlay').classList.remove('open');
            selectedDeuda = null;
        }

        function bindDeudaEvents() {
            document.querySelectorAll('#tblDeudas tr.clickable').forEach(row => {
                row.addEventListener('click', ev => {
                    if (ev.target.closest('[data-del]')) return;
                    const e = data.enemigos.find(x => x.id === parseInt(row.dataset.id, 10));
                    if (e) openDrawer(e);
                });
            });
            document.querySelectorAll('[data-del]').forEach(btn => {
                btn.addEventListener('click', async ev => {
                    ev.stopPropagation();
                    if (!confirm('¿Eliminar esta obligación?')) return;
                    await api('/api/operaciones/' + btn.dataset.del, { method: 'DELETE' });
                    toast('Obligación eliminada');
                    closeDrawer();
                    await loadAll();
                });
            });
        }

        function updateGreeting() {
            const h = new Date().getHours();
            let icon = '☀️';
            let msg = 'Buenos días,';
            if (h >= 13 && h < 20) {
                icon = '🌤️';
                msg = 'Buenas tardes,';
            } else if (h >= 20 || h < 6) {
                icon = '🌙';
                msg = 'Buenas noches,';
            }
            if ($('topbarIcon')) $('topbarIcon').textContent = icon;
            if ($('topbarGreeting')) $('topbarGreeting').textContent = msg;
        }

        function renderAll() {
            updateGreeting();
            renderKpis();
            renderHealth();
            renderChartCfr();
            renderHomeTable();
            renderRemitosDash();
            renderBancos();
            renderBulkLots();
            renderClientes();
            renderCobranzas();
            renderPagosCentral();
            renderHistorialSidebar();
        }

        async function syncEmpresaFromServer() {
            try {
                const emp = await api('/api/empresa');
                if (emp && (emp.razon_social || emp.nombre)) {
                    localStorage.setItem('empresa_datos', JSON.stringify({
                        nombre: emp.razon_social || emp.nombre || 'Master Total',
                        cuit: emp.cuit || '',
                        direccion: emp.direccion || '',
                        telefono: emp.telefono || '',
                        email: emp.email || '',
                        cotizacion_usd: parseFloat(emp.cotizacion_usd) || 1000.0
                    }));
                }
            } catch (_) {}
        }

        function servidorTieneDatos(freshData) {
            if (!freshData) return false;
            const ops = freshData.enemigos?.length || 0;
            const cli = freshData.clientes?.length || 0;
            const rem = freshData.remitos?.length || 0;
            const bulk = freshData.bulk?.length || 0;
            return ops + cli + rem + bulk > 0;
        }
        async function loadSession() {
            try {
                const s = await api('/auth/session?_=' + Date.now());
                sessionUser = {
                    role: s.role || 'admin',
                    username: s.username || 'jefe',
                    empresa_id: s.empresa_id || 1,
                    empresa_nombre: s.empresa_nombre || '',
                };
                const nameEl = document.querySelector('.topbar-greeting-block div div:last-child');
                if (nameEl) {
                    const etiqueta = sessionUser.empresa_nombre || sessionUser.username;
                    nameEl.textContent = etiqueta;
                    nameEl.title = 'Cuenta: ' + sessionUser.username + ' (empresa #' + sessionUser.empresa_id + ')';
                }
            } catch (_) {
                sessionUser = { role: 'admin', username: 'jefe', empresa_id: 1, empresa_nombre: '' };
            }
            if (window.CrmSync) {
                CrmSync.init(db, sessionUser);
                CrmSync.setApi(api);
            }
            await syncEmpresaFromServer();
            applyRoleUi();
        }

        async function logout() {
            try {
                // Limpiar la caché local de IndexedDB para evitar que el siguiente usuario vea los datos del anterior
                await Promise.all([
                    db.transacciones.clear(),
                    db.cache.clear(),
                    db.solicitudes_pendientes.clear()
                ]);
                await api('/auth/logout', { method: 'POST' });
            } catch (_) {}
            window.location.href = '/login';
        }

        $('btnLogout')?.addEventListener('click', logout);
        $('btnLogoutSidebar')?.addEventListener('click', logout);

        $('btnNuevoUsuario')?.addEventListener('click', () => {
            $('formNuevoUsuario')?.reset();
            $('modalNuevoUsuario')?.classList.add('open');
        });
        $('btnCerrarModalUsuario')?.addEventListener('click', () => $('modalNuevoUsuario')?.classList.remove('open'));
        $('btnCancelarModalUsuario')?.addEventListener('click', () => $('modalNuevoUsuario')?.classList.remove('open'));
        $('modalNuevoUsuario')?.addEventListener('click', ev => { if (ev.target === $('modalNuevoUsuario')) $('modalNuevoUsuario').classList.remove('open'); });
        $('formNuevoUsuario')?.addEventListener('submit', async ev => {
            ev.preventDefault();
            try {
                await api('/api/usuarios', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: $('inpUsuarioUsername').value.trim(),
                        nombre: $('inpUsuarioNombre').value.trim(),
                        password: $('inpUsuarioPassword').value,
                        role: $('inpUsuarioRole').value
                    })
                });
                toast('Usuario creado');
                $('modalNuevoUsuario')?.classList.remove('open');
                await renderUsuarios();
            } catch (e) {
                toast(e.message, true);
            }
        });

        $('btnExportData')?.addEventListener('click', async () => {
            try {
                const payload = await api('/api/export');
                const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'master-total-export-' + new Date().toISOString().slice(0, 10) + '.json';
                a.click();
                URL.revokeObjectURL(url);
                toast('Exportación descargada');
            } catch (e) {
                toast(e.message || 'Error al exportar', true);
            }
        });

        function descargarArchivoJson(payload, filename) {
            const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        }
        function enemigoAOperacion(e) {
            if (!e || !e.alias) return null;
            return {
                id: e.id,
                uuid: e.uuid || null,
                alias: e.alias,
                tipo: e.tipo || 'otro',
                recibido: e.recibido,
                pagar: e.pagar ?? e.total_pagar,
                meses: e.meses ?? 1,
                fecha_cierre: e.fecha_cierre || null,
                fecha_vencimiento: e.fecha_vencimiento || null,
                cuotas: e.cuotas ?? e.cuotas_total ?? 1,
                cuotas_pagadas: e.cuotas_pagadas ?? 0,
                kg: e.kg ?? null,
                precio_kg: e.precio_kg ?? null,
                plazo_dias: e.plazo_dias ?? null,
                created_at: e.created_at || null,
            };
        }
        function empresaDatosParaBackup() {
            try {
                const raw = JSON.parse(localStorage.getItem('empresa_datos') || '{}');
                const razon = (raw.nombre || raw.razon_social || '').trim();
                if (!razon && !raw.cuit && !raw.direccion && !raw.telefono && !raw.email) {
                    return null;
                }
                return {
                    razon_social: razon || 'Master Total',
                    cuit: raw.cuit || '',
                    direccion: raw.direccion || '',
                    telefono: raw.telefono || '',
                    email: raw.email || '',
                    cotizacion_usd: parseFloat(raw.cotizacion_usd) || 1000.0,
                };
            } catch (_) {
                return null;
            }
        }
        function convertirAppDataABackup(appData) {
            if (!appData) return null;
            const clientes = (appData.clientes || []).map(c => ({
                id: c.id,
                nombre: c.nombre,
                scoring: c.scoring || 'A',
                techo_deuda: c.techo_deuda ?? 500000,
                saldo_actual: c.saldo_actual ?? 0,
                saldo_inicial: c.saldo_inicial ?? 0,
                telefono: c.telefono || null,
                cuit: c.cuit || null,
                direccion: c.direccion || null,
                email: c.email || null,
                created_at: c.created_at || null,
                fecha_ultimo_pago: c.fecha_ultimo_pago || null,
            }));
            const compras_bulk = (appData.bulk || []).map(b => ({
                id: b.id,
                fecha: b.fecha,
                kg_totales: b.kg_totales,
                kg_remanentes: b.kg_remanentes,
                costo_total_bulk: b.costo_total_bulk,
                costo_reparto: b.costo_reparto ?? 0,
                numero_lote: b.numero_lote || '',
                fecha_vencimiento: b.fecha_vencimiento || '',
                proveedor: b.proveedor || '',
                created_at: b.created_at || null,
            }));
            const entidades_bancarias = (appData.bancos || []).map(b => ({
                id: b.id,
                nombre: b.nombre,
                limite: b.limite ?? 0,
            }));
            const remitos_carga = (appData.remitos || []).map(r => ({
                id: r.id,
                fecha: r.fecha,
                cliente: r.cliente || '',
                cliente_id: r.cliente_id,
                tipo_corte: r.tipo_corte || '',
                cantidad: r.cantidad ?? 0,
                pesos_piezas: typeof r.pesos_piezas === 'string' ? r.pesos_piezas : JSON.stringify(r.pesos_piezas || []),
                kg: r.kg ?? 0,
                precio_por_kg: r.precio_por_kg ?? 0,
                costo_total_logistica: r.costo_total_logistica ?? 0,
                precio_venta_total: r.precio_venta_total ?? 0,
                plazo_cobro_dias: r.plazo_cobro_dias ?? 0,
                costo_carne: r.costo_carne ?? 0,
                pagado: r.pagado ?? 0,
                monto_pagado: r.monto_pagado ?? 0,
                created_at: r.created_at || null,
            }));
            const fuenteOps = appData.enemigos?.length ? appData.enemigos : (appData.historial || []);
            const operaciones_financieras = fuenteOps.map(enemigoAOperacion).filter(Boolean);
            const empresa = empresaDatosParaBackup();
            const payload = {
                version: 2,
                exported_at: new Date().toISOString(),
                source: 'appData_cache',
                clientes,
                compras_bulk,
                entidades_bancarias,
                remitos_carga,
                operaciones_financieras,
                pagos_cuotas: [],
                pagos_clientes: (appData.historialPagos || []).map(p => ({
                    id: p.id,
                    cliente_id: p.cliente_id,
                    monto: p.monto ?? p.monto_pagado ?? 0,
                    fecha: p.fecha || null,
                })).filter(p => p.cliente_id && p.monto > 0),
                aplicacion_pagos: [],
                remitos_fracciones: [],
                perdidas_acumuladas: appData.perdidas || [],
                ventas_mostrador: [],
                auditoria_operaciones: appData.auditoria || [],
            };
            if (empresa) payload.empresa = empresa;
            return payload;
        }
        function mergeAppDataEnBackup(base, app) {
            if (!base || !app) return base;
            const merged = { ...base };
            const tieneOps = (merged.operaciones_financieras?.length || merged.operaciones?.length || merged.enemigos?.length || 0) > 0;
            if (!tieneOps && app.enemigos?.length) merged.enemigos = app.enemigos;
            const pairs = [
                ['clientes', 'clientes'],
                ['remitos_carga', 'remitos'],
                ['remitos', 'remitos'],
                ['compras_bulk', 'bulk'],
                ['bulk', 'bulk'],
                ['entidades_bancarias', 'bancos'],
                ['bancos', 'bancos'],
                ['auditoria_operaciones', 'auditoria'],
                ['auditoria', 'auditoria'],
                ['perdidas_acumuladas', 'perdidas'],
                ['perdidas', 'perdidas'],
                ['pagos_clientes', 'historialPagos'],
                ['historialPagos', 'historialPagos'],
            ];
            for (const [target, source] of pairs) {
                if (!merged[target]?.length && app[source]?.length) merged[target] = app[source];
            }
            return merged;
        }
        function prepararBackupParaSubida(raw) {
            if (!raw || typeof raw !== 'object') return null;
            let payload = raw;

            if (raw.version === 'cache_snapshot_v1') {
                if (raw.fullBackup && typeof raw.fullBackup === 'object') {
                    payload = mergeAppDataEnBackup(raw.fullBackup, raw.appData);
                } else if (raw.appData) {
                    payload = raw.appData;
                } else {
                    return null;
                }
            }

            const versionNum = parseInt(payload.version, 10);
            const esExportCompleto = versionNum >= 2
                && (payload.operaciones_financieras?.length || payload.clientes?.length);
            if (esExportCompleto) {
                if (!payload.operaciones_financieras?.length && payload.enemigos?.length) {
                    payload = { ...payload, operaciones_financieras: payload.enemigos.map(enemigoAOperacion).filter(Boolean) };
                }
                if (!payload.empresa) {
                    const emp = empresaDatosParaBackup();
                    if (emp) payload = { ...payload, empresa: emp };
                }
                return payload;
            }

            const appShape = {
                enemigos: payload.enemigos || payload.historial || payload.operaciones_financieras,
                clientes: payload.clientes,
                bulk: payload.bulk || payload.compras_bulk,
                remitos: payload.remitos || payload.remitos_carga,
                bancos: payload.bancos || payload.entidades_bancarias,
                auditoria: payload.auditoria || payload.auditoria_operaciones,
                perdidas: payload.perdidas || payload.perdidas_acumuladas,
                historialPagos: payload.historialPagos || payload.pagos_clientes,
            };
            return convertirAppDataABackup(appShape);
        }
        function resumirBackup(payload) {
            if (!payload) return '';
            const c = payload.clientes?.length || 0;
            const o = payload.operaciones_financieras?.length || payload.operaciones?.length || payload.enemigos?.length || 0;
            const r = payload.remitos_carga?.length || payload.remitos?.length || 0;
            const b = payload.compras_bulk?.length || payload.bulk?.length || 0;
            return `${o} tarjetas/deudas, ${c} clientes, ${r} remitos, ${b} lotes`;
        }
        async function apiImportBackup(password, backupData) {
            const r = await fetch('/api/import', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ password, backup_data: backupData }),
            });
            const body = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(body.error || 'Error al subir el backup');
            return body;
        }
        function normalizarBackupParaImport(raw) {
            return prepararBackupParaSubida(raw);
        }
        // Backup / Modal Logic
        function backupTieneDatos(payload) {
            if (!payload) return false;
            const clientes = payload.clientes?.length || 0;
            const remitos = payload.remitos_carga?.length || payload.remitos?.length || 0;
            const operaciones = payload.operaciones_financieras?.length || payload.operaciones?.length || payload.enemigos?.length || 0;
            const bulk = payload.compras_bulk?.length || payload.bulk?.length || 0;
            if (payload.version === 'cache_snapshot_v1' && payload.appData) {
                const ad = payload.appData;
                return backupTieneDatos(ad) || (ad.enemigos?.length || 0) > 0;
            }
            return clientes + remitos + operaciones + bulk > 0;
        }
        async function obtenerPayloadBackup() {
            try {
                const server = await api('/api/export');
                if (backupTieneDatos(server)) return server;
            } catch (_) {}
            const cached = await tenantCacheGet('fullBackup');
            if (cached && backupTieneDatos(cached)) return cached;
            const appCache = await tenantCacheGet('appData');
            const fromApp = convertirAppDataABackup(appCache || data);
            if (backupTieneDatos(fromApp)) return fromApp;
            return null;
        }
        async function guardarCacheDispositivo() {
            const [fullEntry, appEntry, pendientes] = await Promise.all([
                tenantCacheGetEntry('fullBackup'),
                tenantCacheGetEntry('appData'),
                db.solicitudes_pendientes.toArray()
            ]);
            const fullBackup = fullEntry?.data || null;
            const appData = appEntry?.data || data || null;
            const fecha = new Date().toISOString().slice(0, 10);

            if (fullBackup && backupTieneDatos(fullBackup)) {
                descargarArchivoJson(fullBackup, 'Backup_MasterTotal_' + fecha + '.json');
                toast('Backup completo descargado. Guardalo en Drive, WhatsApp o email.');
                return;
            }

            if (!appData) {
                return toast('No hay datos en caché para guardar', true);
            }

            const snapshot = {
                version: 'cache_snapshot_v1',
                exported_at: new Date().toISOString(),
                fullBackup,
                appData,
                solicitudes_pendientes: pendientes,
            };
            descargarArchivoJson(snapshot, 'CacheDispositivo_MasterTotal_' + fecha + '.json');
            toast('Caché del celular guardado. No borres ese archivo.');
        }
        async function leerArchivoJson(file) {
            if (typeof file.text === 'function') {
                return JSON.parse(await file.text());
            }
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = e => {
                    try { resolve(JSON.parse(e.target.result)); }
                    catch (err) { reject(err); }
                };
                reader.onerror = () => reject(new Error('No se pudo leer el archivo'));
                reader.readAsText(file);
            });
        }
        async function subirBackupAlServidor(backupData) {
            if (!navigator.onLine) {
                toast('Necesitás conexión a internet para subir al servidor', true);
                return false;
            }
            let parsed = backupData;
            if (typeof backupData === 'string') {
                try {
                    parsed = JSON.parse(backupData);
                } catch (_) {
                    toast('El archivo no es un JSON válido', true);
                    return false;
                }
            }
            const normalizado = prepararBackupParaSubida(parsed);
            if (!normalizado || !backupTieneDatos(normalizado)) {
                toast('El backup no contiene datos para subir', true);
                return false;
            }
            const resumen = resumirBackup(normalizado);
            toast('Listo para subir: ' + resumen);
            const backupWasOpen = $('modalBackup')?.classList.contains('open');
            if (backupWasOpen) $('modalBackup')?.classList.remove('open');
            const password = await window.promptMasterPasswordAsync(
                'REEMPLAZO TOTAL de la nube (' + resumen + '). Se borrarán todos los datos actuales y se cargará el backup. Contraseña maestra:'
            );
            if (!password) {
                if (backupWasOpen) $('modalBackup')?.classList.add('open');
                return false;
            }
            setLoading(true);
            toast('Subiendo datos al servidor...');
            try {
                const res = await apiImportBackup(password, normalizado);
                const ops = res.summary?.tablas?.operaciones_financieras?.insertados;
                const cli = res.summary?.tablas?.clientes?.insertados;
                const msg = ops != null
                    ? `Subido: ${ops} deudas, ${cli ?? 0} clientes`
                    : 'Datos subidos al servidor correctamente';
                toast(msg);
                cerrarModalBackup();
                await tenantCachePut('fullBackup', normalizado);
                await syncEmpresaFromServer();
                await loadAll();
                return true;
            } catch (e) {
                toast('Error al subir: ' + (e.message || ''), true);
                if (backupWasOpen) $('modalBackup')?.classList.add('open');
                return false;
            } finally {
                setLoading(false);
            }
        }
        function cerrarModalBackup() {
            $('modalBackup')?.classList.remove('open');
        }
        async function actualizarNodosBackup() {
            const el = $('backupNodosLista');
            if (!el || !window.CrmSync) return;
            el.textContent = 'Cargando nodos...';
            try {
                const nodos = await CrmSync.listNodes();
                if (!nodos.length) {
                    el.innerHTML = '<span style="color:#64748b;">Este dispositivo publicará su caché como nodo al sincronizar (máx. '
                        + CrmSync.MAX_NODOS + ' por empresa).</span>';
                    return;
                }
                const mine = CrmSync.deviceId;
                el.innerHTML = nodos.map(n => {
                    const esEste = n.device_id === mine;
                    const btn = esEste
                        ? '<span style="color:#16a34a;font-size:0.8rem;">Este dispositivo</span>'
                        : '<button type="button" class="btn btn-ghost btn-sm btn-restaurar-nodo" data-device="'
                            + esc(n.device_id) + '" style="font-size:0.8rem;">Restaurar caché</button>';
                    return '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #e2e8f0;">'
                        + '<div><strong>' + esc(n.etiqueta || 'Dispositivo') + '</strong>'
                        + '<div style="font-size:0.75rem;color:#64748b;">' + esc(n.updated_at || '') + '</div></div>'
                        + btn + '</div>';
                }).join('');
                el.querySelectorAll('.btn-restaurar-nodo').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        try {
                            const nodo = await CrmSync.restoreFromNode(btn.dataset.device);
                            if (nodo?.snapshot) {
                                data = nodo.snapshot;
                                await tenantCachePut('appData', data);
                                renderAll();
                                toast('Caché restaurada desde otro dispositivo');
                            }
                        } catch (e) {
                            toast('No se pudo restaurar: ' + (e.message || ''), true);
                        }
                    });
                });
            } catch (e) {
                el.textContent = 'No se pudieron listar nodos: ' + (e.message || '');
            }
        }
        async function actualizarEstadoNubeBackup() {
            const el = $('backupNubeEstado');
            if (!el) return;
            el.style.display = 'block';
            el.textContent = 'Consultando la nube...';
            try {
                const r = await api('/api/nube/resumen?_=' + Date.now());
                const ops = r.conteos?.operaciones_financieras || 0;
                const cli = r.conteos?.clientes || 0;
                const rem = r.conteos?.remitos_carga || 0;
                const cuenta = r.empresa_nombre || r.username || 'tu cuenta';
                if (r.tiene_datos) {
                    el.style.background = '#f0fdf4';
                    el.style.borderColor = '#86efac';
                    el.style.color = '#166534';
                    el.innerHTML = '<strong>Nube con datos</strong> (' + cuenta + '): '
                        + ops + ' deudas, ' + cli + ' clientes, ' + rem + ' remitos. '
                        + 'Usuario: <strong>' + esc(r.username) + '</strong>';
                } else {
                    el.style.background = '#fef2f2';
                    el.style.borderColor = '#fecaca';
                    el.style.color = '#991b1b';
                    el.innerHTML = '<strong>Nube vacía</strong> para <strong>' + esc(cuenta) + '</strong>'
                        + ' (usuario ' + esc(r.username) + '). Cada empresa tiene datos separados: subí tu archivo .json aquí abajo.';
                }
            } catch (e) {
                el.style.background = '#fef2f2';
                el.style.borderColor = '#fecaca';
                el.style.color = '#991b1b';
                el.textContent = 'No se pudo consultar la nube: ' + (e.message || 'sin conexión');
            }
        }
        function abrirModalBackup() {
            setSidebarOpen(false);
            const modal = $('modalBackup');
            if (!modal) {
                toast('Actualizando aplicación...', false);
                return;
            }
            modal.classList.add('open');
            actualizarEstadoNubeBackup();
            actualizarNodosBackup();
        }
        $('btnBackup')?.addEventListener('click', abrirModalBackup);
        $('btnCerrarBackup')?.addEventListener('click', cerrarModalBackup);
        $('btnCerrarBackupFooter')?.addEventListener('click', cerrarModalBackup);
        $('modalBackup')?.addEventListener('click', ev => {
            if (ev.target === $('modalBackup')) cerrarModalBackup();
        });
        $('btnCrearBackup')?.addEventListener('click', async () => {
            try {
                toast('Generando backup...');
                const payload = await obtenerPayloadBackup();
                if (!payload) {
                    return toast('No hay datos en el servidor ni en caché. Usá "Guardar caché del celular".', true);
                }
                descargarArchivoJson(payload, 'Backup_MasterTotal_' + new Date().toISOString().slice(0, 10) + '.json');
                toast('Backup descargado correctamente');
                cerrarModalBackup();
            } catch (e) {
                toast('Error al crear backup: ' + (e.message || ''), true);
            }
        });
        $('btnGuardarCache')?.addEventListener('click', async () => {
            try {
                await guardarCacheDispositivo();
                cerrarModalBackup();
            } catch (e) {
                toast('Error al guardar caché: ' + (e.message || ''), true);
            }
        });
        $('btnDescargarNube')?.addEventListener('click', async () => {
            try {
                await descargarDatosDeLaNube();
                cerrarModalBackup();
            } catch (e) {
                toast('Error al descargar: ' + (e.message || ''), true);
            }
        });
        $('btnSubirServidor')?.addEventListener('click', async () => {
            const fileInput = $('inputBackupFile');
            if (fileInput?.files?.length) {
                try {
                    toast('Leyendo archivo...');
                    const json = await leerArchivoJson(fileInput.files[0]);
                    await subirBackupAlServidor(json);
                } catch (e) {
                    toast('Archivo JSON inválido', true);
                }
                return;
            }
            const payload = await obtenerPayloadBackup();
            if (!payload) {
                return toast('Seleccioná un archivo .json o guardá primero la caché del celular', true);
            }
            await subirBackupAlServidor(payload);
        });
        $('btnSubirCacheLocal')?.addEventListener('click', async () => {
            const cached = await tenantCacheGet('fullBackup');
            if (cached && backupTieneDatos(cached)) {
                return subirBackupAlServidor(cached);
            }
            const appCache = await tenantCacheGet('appData');
            const snapshot = {
                version: 'cache_snapshot_v1',
                exported_at: new Date().toISOString(),
                fullBackup: null,
                appData: appCache || data || null,
            };
            if (!backupTieneDatos(snapshot)) {
                return toast('No hay copia en este dispositivo. Usá "Guardar caché del celular" primero.', true);
            }
            await subirBackupAlServidor(snapshot);
        });
        $('btnRestaurarBackup')?.addEventListener('click', async () => {
            const fileInput = $('inputBackupFile');
            if (!fileInput?.files?.length) {
                return toast('Seleccioná un archivo JSON de backup primero', true);
            }
            try {
                toast('Leyendo archivo...');
                const jsonContent = await leerArchivoJson(fileInput.files[0]);
                await subirBackupAlServidor(jsonContent);
                fileInput.value = '';
            } catch (err) {
                toast('Error al restaurar: ' + (err.message || 'Archivo inválido'), true);
            }
        });

        async function loadAll(opts = {}) {
            const forzarServidor = !!opts.forzarServidor;
            const avisarSiVacio = opts.avisarSiVacio !== false;
            const bust = '_=' + Date.now();
            setLoading(true);
            try {
                if (!forzarServidor) {
                    const cached = await tenantCacheGet('appData');
                    if (cached) {
                        data = cached;
                        renderAll();
                        setLoading(false);
                    }
                }

                if (navigator.onLine) {
                    await drainOutboxAll();
                }

                const pending = await countPendingOutbox();
                if (pending > 0 && data) {
                    console.warn('Outbox con pendientes: no se pisa la caché local con pull del servidor.');
                    actualizarUIOffline();
                    return;
                }

                let freshData = null;
                if (navigator.onLine && window.CrmSync) {
                    try {
                        const bundle = await CrmSync.pullFromServer();
                        if (bundle?.blocked) {
                            console.warn('Pull bloqueado: outbox no vacío.');
                        } else if (bundle?.appData) {
                            freshData = bundle.appData;
                            if (bundle.fullBackup && backupTieneDatos(bundle.fullBackup)) {
                                await tenantCachePut('fullBackup', bundle.fullBackup);
                            }
                        }
                    } catch (syncErr) {
                        console.warn('sync/pull no disponible, usando APIs individuales', syncErr);
                    }
                }

                if (!freshData) {
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
                    freshData = {
                        ...dash,
                        historialPagos: pick(1, []),
                        bulk: pick(2, []),
                        clientes: pick(3, []),
                        auditoria: pick(4, []),
                    };
                    try {
                        const fullBackup = await api('/api/export?' + bust);
                        if (backupTieneDatos(fullBackup)) {
                            await tenantCachePut('fullBackup', fullBackup);
                        }
                    } catch (_) {
                        const fromApp = convertirAppDataABackup(freshData);
                        if (backupTieneDatos(fromApp)) {
                            await tenantCachePut('fullBackup', fromApp);
                        }
                    }
                }

                data = freshData;
                await tenantCachePut('appData', data);
                renderAll();
                await publishNodeBackupAfterSync();

                if (avisarSiVacio && !servidorTieneDatos(freshData)) {
                    try {
                        const nube = await api('/api/nube/resumen?_=' + Date.now());
                        const cuenta = nube.empresa_nombre || sessionUser.empresa_nombre || sessionUser.username || 'esta empresa';
                        toast(
                            'La nube está vacía para ' + cuenta + '. Subí tu backup .json en Backup / Nube.',
                            true
                        );
                    } catch (_) {
                        toast('La nube parece vacía para esta empresa. Subí el backup .json en Backup / Nube.', true);
                    }
                } else if (opts.mostrarExito) {
                    toast('Datos descargados de la nube correctamente');
                }
            } catch (e) {
                console.warn('Error al cargar del servidor.', e);
                if (forzarServidor || !data) {
                    toast('No se pudieron descargar los datos: ' + (e.message || 'revisá tu conexión'), true);
                }
            } finally {
                setLoading(false);
            }
        }
        async function descargarDatosDeLaNube() {
            toast('Descargando datos del servidor...');
            await loadAll({ forzarServidor: true, avisarSiVacio: true, mostrarExito: true });
        }

        window.abrirFormularioRegistro = abrirFormularioRegistro;
        function abrirFormularioRegistro(id, tipo = null) {
            switchView('registro');
            $('registroMenu').classList.add('field-hidden');
            document.querySelectorAll('.registro-subview').forEach(el => el.classList.add('field-hidden'));
            $(id).classList.remove('field-hidden');
            if (tipo) {
                const selectTipo = $(id).querySelector('select[name="tipo"]');
                if (selectTipo) {
                    selectTipo.value = tipo;
                    if (id === 'regDeuda') toggleFormTipo();
                }
            }
            // Small animation via CSS class
            $(id).classList.add('fade-in');
            setTimeout(() => $(id).classList.remove('fade-in'), 300);
        }

        window.abrirSubVistaClientes = (id) => {
            if ($('clientesMenu')) $('clientesMenu').classList.add('field-hidden');
            document.querySelectorAll('.clientes-subview').forEach(v => v.classList.add('field-hidden'));
            if ($(id)) $(id).classList.remove('field-hidden');
            window.scrollTo({top:0, behavior:'smooth'});
        };

        window.volverMenuClientes = () => {
            if ($('clientesMenu')) $('clientesMenu').classList.remove('field-hidden');
            document.querySelectorAll('.clientes-subview').forEach(v => v.classList.add('field-hidden'));
        };

        window.volverMenuRegistro = () => {
            document.querySelectorAll('.registro-subview').forEach(el => el.classList.add('field-hidden'));
            $('registroMenu').classList.remove('field-hidden');
            $('registroMenu').classList.add('fade-in');
            setTimeout(() => $('registroMenu').classList.remove('fade-in'), 300);
        }

        window.abrirSubVistaBulk = (id) => {
            $('bulkMenu').classList.add('field-hidden');
            document.querySelectorAll('.bulk-subview').forEach(el => el.classList.add('field-hidden'));
            $(id).classList.remove('field-hidden');
            $(id).classList.add('fade-in');
            setTimeout(() => $(id).classList.remove('fade-in'), 300);
        };

        window.volverMenuBulk = () => {
            document.querySelectorAll('.bulk-subview').forEach(el => el.classList.add('field-hidden'));
            $('bulkMenu').classList.remove('field-hidden');
            $('bulkMenu').classList.add('fade-in');
            setTimeout(() => $('bulkMenu').classList.remove('fade-in'), 300);
        };

        window.abrirSubVistaVentasExpress = (id) => {
            $('ventasExpressMenu').classList.add('field-hidden');
            document.querySelectorAll('.ventas-express-subview').forEach(el => el.classList.add('field-hidden'));
            $(id).classList.remove('field-hidden');
            $(id).classList.add('fade-in');
            setTimeout(() => $(id).classList.remove('fade-in'), 300);
        };

        window.volverMenuVentasExpress = () => {
            if (currentClientData) {
                // If we came from a client profile, going back means returning to the profile
                switchView('cliente-detalle');
                return;
            }
            document.querySelectorAll('.ventas-express-subview').forEach(el => el.classList.add('field-hidden'));
            $('ventasExpressMenu').classList.remove('field-hidden');
            $('ventasExpressMenu').classList.add('fade-in');
            setTimeout(() => $('ventasExpressMenu').classList.remove('fade-in'), 300);
        };

        function getGreeting() {
            const h = new Date().getHours();
            let emoji = '🌙'; let text = 'Buenas noches,';
            if (h >= 5 && h < 12) { emoji = '☀️'; text = 'Buenos días,'; }
            else if (h >= 12 && h < 20) { emoji = '🌤️'; text = 'Buenas tardes,'; }
            return `<div style="display: flex; align-items: center; gap: 6px;">
                        <span style="font-size: 28px; line-height: 1; filter: drop-shadow(0 2px 2px rgba(0,0,0,0.1)); margin-right: 4px;">${emoji}</span>
                        <div style="display: flex; flex-direction: column; line-height: 1.15; font-weight: 700; font-size: 16px; color: #111827;">
                            <span>${text}</span>
                            <span>jefe</span>
                        </div>
                    </div>`;
        }

        function switchView(name) {
            if (name === 'usuarios' && sessionUser.role !== 'admin') {
                toast('Solo administradores pueden gestionar usuarios', true);
                return;
            }
            if (activeRowIndex >= 0) {
                const trs = Array.from(document.querySelectorAll('#tblHome tr.clickable'));
                const el = trs[activeRowIndex];
                if (el && el.nextElementSibling && el.nextElementSibling.classList.contains('detail-row')) {
                    el.nextElementSibling.remove();
                }
                activeRowIndex = -1;
            }
            if (!titles[name]) return;
            document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === name));
            document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
            
            if ($('dashboardBar')) {
                if (name === 'home' || name === 'dashboard' || name === 'deudas') {
                    $('dashboardBar').classList.remove('field-hidden');
                } else {
                    $('dashboardBar').classList.add('field-hidden');
                }
            }
            if ($('cobranzasBar')) {
                if (name === 'cobranzas') {
                    $('cobranzasBar').classList.remove('field-hidden');
                } else {
                    $('cobranzasBar').classList.add('field-hidden');
                }
            }
            if ($('pagosCentralBar')) {
                if (name === 'pago-central') {
                    $('pagosCentralBar').classList.remove('field-hidden');
                } else {
                    $('pagosCentralBar').classList.add('field-hidden');
                }
            }

            if (name === 'dashboard' || name === 'home') {
                titles[name][0] = getGreeting();
                if ($('weatherWidget')) $('weatherWidget').classList.remove('field-hidden');
            } else {
                if ($('weatherWidget')) $('weatherWidget').classList.add('field-hidden');
            }

        if (name === 'home') {
            if ($('btnTogglePro')) $('btnTogglePro').style.setProperty('display', 'flex', 'important');
            renderHomeTable();
        } else {
            if ($('btnTogglePro')) $('btnTogglePro').style.setProperty('display', 'none', 'important');
        }

            const [t, s, bc] = titles[name] || ['', '', name];
            if ($('pageTitle')) $('pageTitle').innerHTML = t;
            if ($('pageSub')) $('pageSub').textContent = s;
            if ($('breadcrumbPage')) $('breadcrumbPage').textContent = bc || t;
            if (name === 'remitos') renderRemitosFull();
            if (name === 'clientes') {
                volverMenuClientes();
                renderClientes();
            }
            if (name === 'historial-pagos') renderHistorialPagos();
            if (name === 'cobranzas') renderCobranzas();
            if (name === 'pago-central') renderPagosCentral();
            if (name === 'auditoria') renderAuditoria();
            if (name === 'usuarios') renderUsuarios();
            if (name === 'registro') volverMenuRegistro();
            if (name === 'finanzas-aging') renderFinanzasAging();
            if (name === 'finanzas-margenes') renderFinanzasMargenes();
            setSidebarOpen(false);
        }

        document.querySelectorAll('.nav-item').forEach(btn => {
            btn.addEventListener('click', () => switchView(btn.dataset.view));
        });

        $('btnTogglePro').addEventListener('click', () => {
            isProMode = !isProMode;
            const btn = $('btnTogglePro');
            if (isProMode) {
                btn.style.background = '#1e293b';
                btn.style.color = '#fff';
                $('tblHomeWrapperNormal').classList.add('field-hidden');
                $('tblHomeWrapperPro').classList.remove('field-hidden');
            } else {
                btn.style.background = '#e2e8f0';
                btn.style.color = '#111827';
                $('tblHomeWrapperNormal').classList.remove('field-hidden');
                $('tblHomeWrapperPro').classList.add('field-hidden');
            }
            renderHomeTable();
        });
        
        $('btnRefresh').addEventListener('click', async () => {
            await loadAll();
            const v = document.querySelector('.nav-item.active')?.dataset.view;
            if (v === 'remitos') await renderRemitosFull();
            if (v === 'historial-pagos') renderHistorialPagos();
            if (v === 'auditoria') renderAuditoria();
            if (v === 'usuarios') renderUsuarios();
            if (v === 'finanzas-aging') renderFinanzasAging();
            if (v === 'finanzas-margenes') renderFinanzasMargenes();
            toast('Panel actualizado');
        });

        function setSidebarOpen(open) {
            const sidebar = $('sidebar');
            const backdrop = $('sidebarBackdrop');
            if (sidebar) sidebar.classList.toggle('open', open);
            if (backdrop) {
                backdrop.classList.toggle('active', open);
                backdrop.setAttribute('aria-hidden', open ? 'false' : 'true');
            }
            document.body.classList.toggle('sidebar-open', open);
            document.querySelectorAll('.dashboard-bar').forEach(el => {
                el.classList.toggle('bar-suppressed', open);
                if (open) {
                    el.dataset.prevDisplay = el.style.display || '';
                    el.style.display = 'none';
                } else {
                    el.style.display = el.dataset.prevDisplay || '';
                    delete el.dataset.prevDisplay;
                }
            });
        }

        $('menuToggle')?.addEventListener('click', () => setSidebarOpen(!$('sidebar').classList.contains('open')));
        if ($('sidebarCloseMobile')) $('sidebarCloseMobile').addEventListener('click', () => setSidebarOpen(false));
        $('sidebarBackdrop')?.addEventListener('click', () => setSidebarOpen(false));
        document.addEventListener('click', ev => {
            const sidebar = $('sidebar');
            const toggle = $('menuToggle');
            if (sidebar && toggle && sidebar.classList.contains('open')) {
                if (!sidebar.contains(ev.target) && !toggle.contains(ev.target)) {
                    setSidebarOpen(false);
                }
            }
        });
        $('drawerClose')?.addEventListener('click', closeDrawer);
        $('drawerOverlay')?.addEventListener('click', ev => { if (ev.target === $('drawerOverlay')) closeDrawer(); });

        document.body.addEventListener('blur', ev => {
            if (ev.target && ev.target.classList.contains('calc-input')) {
                let val = ev.target.value.replace(/,/g, '.');
                if (/^\d+(?:\.\d+)?(?:\s*\+\s*\d+(?:\.\d+)?)+$/.test(val.trim())) {
                    const parsed = parseKgInput(val);
                    ev.target.dataset.pesos = JSON.stringify(parsed.pesos_piezas);
                    ev.target.value = parsed.kg;
                    return;
                }
                delete ev.target.dataset.pesos;
                if (/^[0-9+\-.*\/()\s]+$/.test(val) && val.trim() !== '') {
                    try {
                        let res = Function('"use strict";return (' + val + ')')();
                        if (!isNaN(res) && isFinite(res)) {
                            ev.target.value = Math.round(res * 100) / 100;
                        }
                    } catch (e) {}
                }
            }
        }, true);

        $('drawerPagar')?.addEventListener('click', abrirModalPago);
        $('drawerDelete').addEventListener('click', async () => {
            if (!selectedDeuda) return;
            const pw = await window.promptMasterPasswordAsync('Ingrese la contraseña maestra para eliminar la deuda:');
            if (!pw) return;
            if (!confirm('¿Eliminar ' + selectedDeuda.alias + '?')) return;
            await api('/api/operaciones/' + selectedDeuda.id, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json', 'X-Master-Password': pw }
            });
            toast('Obligación eliminada');
            closeDrawer();
            await loadAll();
        });

        $('btnConfirmDeleteAuditoria')?.addEventListener('click', async () => {
            if (!selectedAuditId) return;
            const pw = $('inpPasswordAuditoria').value;
            if (!pw) {
                toast('Debe ingresar la contraseña maestra', true);
                return;
            }
            if (!confirm('¿Borrar definitivamente este registro de auditoría?')) return;
            try {
                await api('/api/auditoria/' + selectedAuditId, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json', 'X-Master-Password': pw }
                });
                toast('Registro de auditoría eliminado');
                $('modalPasswordAuditoria').classList.remove('open');
                await loadAll();
            } catch (e) {
                toast(e.message, true);
            }
        });

        $('btnCancelarPago').addEventListener('click', cerrarModalPago);
        $('btnConfirmarPago').addEventListener('click', confirmarPago);
        $('modalPago').addEventListener('click', ev => { if (ev.target === $('modalPago')) cerrarModalPago(); });
        $('btnCancelarPagoRemito')?.addEventListener('click', cerrarModalPagoRemito);
        $('btnConfirmarPagoRemito')?.addEventListener('click', confirmarPagoRemito);
        $('modalPagoRemito')?.addEventListener('click', ev => { if (ev.target === $('modalPagoRemito')) cerrarModalPagoRemito(); });
        $('inpMontoRemitoPago')?.addEventListener('keydown', ev => { if (ev.key === 'Enter') confirmarPagoRemito(); });
        $('btnCobranzaCobrar')?.addEventListener('click', cobranzaElegirCobrar);
        $('btnCobranzaVer')?.addEventListener('click', cobranzaElegirVer);
        $('btnCobranzaCancelar')?.addEventListener('click', cerrarModalCobranzaAccion);
        $('modalCobranzaAccion')?.addEventListener('click', ev => { if (ev.target === $('modalCobranzaAccion')) cerrarModalCobranzaAccion(); });
        $('btnPagoCentralPagar')?.addEventListener('click', pagoCentralElegirPagar);
        $('btnPagoCentralVer')?.addEventListener('click', pagoCentralElegirVer);
        $('btnPagoCentralCancelar')?.addEventListener('click', cerrarModalPagoCentralAccion);
        $('modalPagoCentralAccion')?.addEventListener('click', ev => { if (ev.target === $('modalPagoCentralAccion')) cerrarModalPagoCentralAccion(); });
        $('btnCancelarPagoGlobal')?.addEventListener('click', cerrarModalPagoGlobal);
        $('btnConfirmarPagoGlobal')?.addEventListener('click', confirmarPagoGlobal);
        $('modalPagoGlobal')?.addEventListener('click', ev => { if (ev.target === $('modalPagoGlobal')) cerrarModalPagoGlobal(); });
        $('inpMontoPagoGlobal')?.addEventListener('keydown', ev => { if (ev.key === 'Enter') confirmarPagoGlobal(); });
        $('modalEmpresa')?.addEventListener('click', ev => { if (ev.target === $('modalEmpresa')) cerrarModalEmpresa(); });
        $('modalFacturaOriginal')?.addEventListener('click', ev => { if (ev.target === $('modalFacturaOriginal')) cerrarModalFacturaOriginal(); });
        $('formEmpresa')?.addEventListener('submit', async ev => {
            ev.preventDefault();
            const data = {
                nombre: $('inpEmpresaNombre')?.value.trim() || "Master Total",
                cuit: $('inpEmpresaCuit')?.value.trim() || "",
                direccion: $('inpEmpresaDireccion')?.value.trim() || "",
                telefono: $('inpEmpresaTelefono')?.value.trim() || "",
                email: $('inpEmpresaEmail')?.value.trim() || "",
                cotizacion_usd: parseFloat($('inpEmpresaUsd')?.value || 1000) || 1000.0
            };
            localStorage.setItem('empresa_datos', JSON.stringify(data));
            if (sessionUser.role === 'admin') {
                try {
                    await api('/api/empresa', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            razon_social: data.nombre,
                            cuit: data.cuit,
                            direccion: data.direccion,
                            telefono: data.telefono,
                            email: data.email,
                            cotizacion_usd: data.cotizacion_usd
                        })
                    });
                } catch (e) {
                    toast(e.message || 'No se pudo guardar en servidor', true);
                    return;
                }
            }
            toast('Datos de la empresa guardados con éxito');
            cerrarModalEmpresa();
        });
        $('inpMontoPago').addEventListener('input', () => {
            const esp = planPagoActual ? planPagoActual.monto_cuota : 0;
            calcDiffUi(esp, parseFloat($('inpMontoPago').value) || 0);
        });
        $('inpCuotaNum').addEventListener('change', () => {
            if (!planPagoActual) return;
            const n = parseInt($('inpCuotaNum').value, 10);
            if (n < planPagoActual.cuotas_pagadas + 1) {
                $('inpCuotaNum').value = planPagoActual.cuota_en_curso;
            }
        });

        $('formDeuda').addEventListener('submit', async ev => {
            ev.preventDefault();
            const fd = new FormData(ev.target);
            const payload = Object.fromEntries(fd);
            if (payload.tipo === 'tarjeta') {
                delete payload.meses;
                delete payload.monto;
                delete payload.kg;
                delete payload.precio_kg;
                delete payload.plazo_dias;
            } else if (payload.tipo === 'cheque') {
                delete payload.recibido;
                delete payload.pagar;
                delete payload.meses;
                delete payload.fecha_cierre;
                delete payload.cuotas;
                delete payload.kg;
                delete payload.precio_kg;
                delete payload.plazo_dias;
            } else if (payload.tipo === 'proveedor') {
                delete payload.monto;
                delete payload.meses;
                delete payload.fecha_cierre;
                delete payload.fecha_vencimiento;
                delete payload.cuotas;
                delete payload.recibido;
                if (!payload.pagar) delete payload.pagar;
            } else if (payload.tipo === 'prestamo') {
                delete payload.monto;
                delete payload.meses;
                delete payload.fecha_cierre;
                delete payload.fecha_vencimiento;
                delete payload.cuotas;
                delete payload.kg;
                delete payload.precio_kg;
            } else {
                delete payload.monto;
                delete payload.fecha_cierre;
                delete payload.fecha_vencimiento;
                delete payload.cuotas;
                delete payload.kg;
                delete payload.precio_kg;
                delete payload.plazo_dias;
            }
            const guardado = await registrarTransaccion(payload);
            if (guardado) {
                toast('Datos cargados, en unos instantes lo verás reflejado. Gracias.', false, 4000);
                ev.target.reset();
                toggleFormTipo();
                switchView('home');
            } else {
                toast('Error al guardar localmente', true);
            }
        });

        $('selTipo').addEventListener('change', toggleFormTipo);
        document.querySelector('[name=kg]')?.addEventListener('input', updateProvTotal);
        document.querySelector('[name=precio_kg]')?.addEventListener('input', updateProvTotal);
        toggleFormTipo();

        $('selHistPagosTipo').addEventListener('change', ev => {
            histPagosFiltro = ev.target.value;
            renderHistorialPagos();
        });
        $('btnHistPagosCsv').addEventListener('click', exportHistorialCsv);

        $('formRemito').addEventListener('submit', async ev => {
            ev.preventDefault();
            const fd = new FormData(ev.target);
            const kgInput = ev.target.querySelector('[name=kg]');
            let pesos_piezas = [];
            if (kgInput?.dataset?.pesos) {
                try { pesos_piezas = JSON.parse(kgInput.dataset.pesos); } catch (e) { pesos_piezas = []; }
            }
            if (!pesos_piezas.length) {
                const parsed = parseKgInput(fd.get('kg'));
                pesos_piezas = parsed.pesos_piezas;
            }
            try {
                await api('/api/remitos', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cliente: fd.get('cliente'),
                        tipo_corte: fd.get('tipo_corte'),
                        cantidad: fd.get('cantidad') || (pesos_piezas.length || 0),
                        pesos_piezas: pesos_piezas.length ? pesos_piezas : undefined,
                        kg: fd.get('kg'),
                        precio_por_kg: fd.get('precio_por_kg'),
                        plazo_cobro_dias: fd.get('plazo')
                    })
                });
                toast('Remito de venta registrado');
                ev.target.reset();
                if (ev.target.plazo) ev.target.plazo.value = 30;
                await loadAll();
                if (currentClientData) {
                    await openClientDrawer(currentClientData.id);
                }
            } catch (e) { toast(e.message, true); }
        });

        $('formBanco').addEventListener('submit', async ev => {
            ev.preventDefault();
            const fd = new FormData(ev.target);
            try {
                await api('/api/bancos', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Object.fromEntries(fd))
                });
                toast('Entidad bancaria agregada');
                ev.target.reset();
                await loadAll();
            } catch (e) { toast(e.message, true); }
        });

        $('formBulk').addEventListener('submit', async ev => {
            ev.preventDefault();
            const fd = new FormData(ev.target);
            try {
                await api('/api/bulk', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Object.fromEntries(fd))
                });
                toast('Lote bulk registrado');
                ev.target.reset();
                $('inpBulkFecha').value = new Date().toISOString().split('T')[0];
                await loadAll();
            } catch (e) { toast(e.message, true); }
        });

        $('formCliente').addEventListener('submit', async ev => {
            ev.preventDefault();
            const fd = new FormData(ev.target);
            const nombre = fd.get('nombre') ? fd.get('nombre').trim() : "";
            
            // Buscar si ya existe por nombre
            const existe = (data.clientes || []).find(c => c.nombre.trim().toLowerCase() === nombre.toLowerCase());
            
            let isUpdating = false;
            let clientIdToUpdate = null;
            
            if (existe) {
                const conf = confirm(`El cliente "${existe.nombre}" ya existe. ¿Desea actualizar los datos del cliente existente?`);
                if (!conf) return;
                isUpdating = true;
                clientIdToUpdate = existe.id;
            }
            
            const payload = {
                nombre: nombre,
                techo_deuda: fd.get('techo_deuda'),
                scoring: fd.get('scoring'),
                telefono: fd.get('telefono') || undefined,
                cuit: fd.get('cuit') || undefined,
                direccion: fd.get('direccion') || undefined,
                email: fd.get('email') || undefined,
                saldo_inicial: fd.get('saldo_inicial') ? parseFloat(fd.get('saldo_inicial')) : undefined
            };
            
            try {
                if (isUpdating) {
                    await api('/api/clientes/' + clientIdToUpdate, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    toast('Cliente actualizado con éxito');
                } else {
                    await api('/api/clientes', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    toast('Cliente registrado con éxito');
                }
                ev.target.reset();
                await loadAll();
                abrirSubVistaClientes('cliVer');
            } catch (e) { toast(e.message, true); }
        });
        
        $('btnClientIncobrableWsp').addEventListener('click', async () => {
            if (!currentClientData) return;
            if (!confirm('⚠️ ¿ESTÁ SEGURO DE DECLARAR ESTE CLIENTE COMO INCOBRABLE?\nSu deuda de saldo pendiente se pasará a pérdidas y el crédito se bloqueará a $0 para siempre.')) return;
            try {
                await api('/api/clientes/' + currentClientData.id + '/incobrable', { method: 'POST' });
                toast('Cliente declarado como Incobrable');
                await loadAll();
                $('modalWspProfile').classList.remove('open');
                await openClientDrawer(currentClientData.id);
            } catch (e) { toast(e.message, true); }
        });

        const PESO_REF_CORTE = {
            'media res': 60,
            'cuartos': 30,
            'parrilleros': 25,
            'pechos': 20,
            'novillo': 280,
        };

        function remitoPesosPiezas(r) {
            if (Array.isArray(r?.pesos_piezas) && r.pesos_piezas.length) {
                return r.pesos_piezas.map(Number);
            }
            return [];
        }

        function remitoCantidad(r) {
            const piezas = remitoPesosPiezas(r);
            if (piezas.length) return piezas.length;
            const q = parseInt(r.cantidad, 10);
            if (q > 0) return q;
            const tipo = (r.tipo_corte || '').toLowerCase().trim();
            const peso = PESO_REF_CORTE[tipo];
            if (peso && r.kg > 0) {
                const est = r.kg / peso;
                if (Math.abs(est - Math.round(est)) < 0.12) return Math.round(est);
            }
            return 1;
        }

        function remitoKgPorUnidad(r) {
            const piezas = remitoPesosPiezas(r);
            if (piezas.length === 1) return piezas[0];
            const q = remitoCantidad(r);
            return q > 0 ? r.kg / q : r.kg;
        }

        function fmtFechaRemito(iso) {
            if (!iso) return '—';
            const p = String(iso).split('T')[0].split('-');
            if (p.length === 3) return `${p[2]}/${p[1]}/${p[0]}`;
            return iso;
        }

        function pesosPiezasHtml(piezas) {
            if (!piezas.length) return '';
            return `<div class="pesos-grid">${piezas.map(p => `<span class="peso-chip">${Number(p).toFixed(2)}</span>`).join('')}</div>`;
        }

        function reporteEstadoClass(label) {
            const l = (label || '').toLowerCase();
            if (l.includes('cobrado')) return 'st-paid';
            if (l.includes('parcial')) return 'st-partial';
            if (l.includes('incobrable')) return 'st-bad';
            return 'st-pending';
        }

        function getEmpresaDatos() {
            const defaults = {
                nombre: "Master Total",
                cuit: "30-12345678-9",
                direccion: "Av. Juan B. Justo 1234, CABA",
                telefono: "+54 11 4567-8901",
                email: "contacto@mastertotal.com",
                cotizacion_usd: 1000.0
            };
            try {
                const raw = localStorage.getItem('empresa_datos');
                if (raw) {
                    return Object.assign(defaults, JSON.parse(raw));
                }
            } catch (e) {}
            return defaults;
        }

        window.abrirModalEmpresa = function() {
            const data = getEmpresaDatos();
            if ($('inpEmpresaNombre')) $('inpEmpresaNombre').value = data.nombre || '';
            if ($('inpEmpresaCuit')) $('inpEmpresaCuit').value = data.cuit || '';
            if ($('inpEmpresaDireccion')) $('inpEmpresaDireccion').value = data.direccion || '';
            if ($('inpEmpresaTelefono')) $('inpEmpresaTelefono').value = data.telefono || '';
            if ($('inpEmpresaEmail')) $('inpEmpresaEmail').value = data.email || '';
            if ($('inpEmpresaUsd')) $('inpEmpresaUsd').value = data.cotizacion_usd || 1000.0;
            $('modalEmpresa')?.classList.add('open');
        };

        window.cerrarModalEmpresa = function() {
            $('modalEmpresa').classList.remove('open');
        };

        window.abrirModalFacturaOriginal = async function(remitoId) {
            $('facturaOriginalSub').textContent = `Cargando comprobante de remito #${remitoId}...`;
            $('facturaOriginalBody').innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:30px">Obteniendo detalles del servidor...</div>';
            $('modalFacturaOriginal').classList.add('open');
            
            try {
                const r = await api('/api/remitos/' + remitoId);
                $('facturaOriginalSub').textContent = `Detalles del remito #${String(r.id).padStart(3, '0')}`;
                
                const emp = getEmpresaDatos();
                const initials = emp.nombre
                    .split(' ')
                    .map(w => w[0])
                    .join('')
                    .toUpperCase()
                    .slice(0, 2) || 'MT';
                    
                const pagado = Number(r.monto_pagado || 0);
                const saldo = Math.max(0, r.precio_venta_total - pagado);
                const estado = remitoEstado(r.estado_cobro ?? r.pagado);
                
                // Generar lista de pesos piezas en cuadrícula si existen
                let piezasHtml = '';
                if (Array.isArray(r.pesos_piezas) && r.pesos_piezas.length) {
                    piezasHtml = `
                    <div style="margin-top: 15px;">
                        <h4 style="font-size: 11px; color: #64748b; font-weight: bold; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">Detalle de Pesos por Pieza</h4>
                        <div style="display: flex; flex-wrap: wrap; gap: 6px;">
                            ${r.pesos_piezas.map((p, idx) => `
                                <div style="font-family: monospace; font-size: 11px; background: #f1f5f9; border: 1px solid #cbd5e1; padding: 4px 8px; border-radius: 6px; color: #1e293b;">
                                    U#${idx+1}: <strong>${fmt(p)} kg</strong>
                                </div>
                            `).join('')}
                        </div>
                    </div>`;
                }

                $('facturaOriginalBody').innerHTML = `
                <div style="font-family:'Segoe UI',system-ui,sans-serif; display:flex; flex-direction:column; gap:16px;">
                    <!-- Cabecera de la empresa -->
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; padding-bottom:12px; border-bottom:2px solid var(--brand, #0d6efd);">
                        <div style="display:flex; gap:10px; align-items:center;">
                            <div style="width:40px; height:40px; background:var(--brand, #0d6efd); color:#fff; border-radius:8px; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:14px;">${esc(initials)}</div>
                            <div>
                                <div style="font-weight:700; font-size:14px; color:#0f172a;">${esc(emp.nombre)}</div>
                                <div style="font-size:9px; color:#64748b;">Distribuidora de Carne</div>
                            </div>
                        </div>
                        <div style="text-align:right; font-size:10px; color:#475569; line-height:1.3;">
                            ${emp.cuit ? `CUIT: ${esc(emp.cuit)}<br>` : ''}
                            ${emp.telefono ? `Tel: ${esc(emp.telefono)}<br>` : ''}
                            ${emp.email ? `Email: ${esc(emp.email)}` : ''}
                        </div>
                    </div>

                    <!-- Datos del cliente y factura -->
                    <div style="display:grid; grid-template-columns:1.2fr 1fr; gap:12px; font-size:11px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:12px;">
                        <div>
                            <h4 style="font-size:9px; color:#64748b; font-weight:bold; text-transform:uppercase; margin-bottom:4px; letter-spacing:0.04em;">Cliente Facturado</h4>
                            <div style="font-weight:700; color:#0f172a; font-size:12px; margin-bottom:4px;">${esc(r.cliente_nombre || r.cliente)}</div>
                            ${r.cliente_cuit ? `<div style="color:#475569;">CUIT: ${esc(r.cliente_cuit)}</div>` : ''}
                            ${r.cliente_direccion ? `<div style="color:#475569;">Dir: ${esc(r.cliente_direccion)}</div>` : ''}
                            ${r.cliente_telefono ? `<div style="color:#475569;">Tel: ${esc(r.cliente_telefono)}</div>` : ''}
                        </div>
                        <div style="text-align:right;">
                            <h4 style="font-size:9px; color:#64748b; font-weight:bold; text-transform:uppercase; margin-bottom:4px; letter-spacing:0.04em;">Detalle Remito</h4>
                            <div style="color:#475569;">Emisión: <span style="font-weight:600; color:#111827;">${r.fecha}</span></div>
                            <div style="color:#475569;">Plazo acordado: <span style="font-weight:600; color:#111827;">${r.plazo_cobro_dias} días</span></div>
                            <div style="margin-top:6px;"><span class="badge ${estado.badgeClass}" style="font-size:9px; padding:3px 8px;">${estado.label.toUpperCase()}</span></div>
                        </div>
                    </div>

                    <!-- Tabla de productos -->
                    <table style="width:100%; border-collapse:collapse; font-size:11px; margin-top:5px;">
                        <thead>
                            <tr style="background:#0f172a; color:#ffffff;">
                                <th style="text-align:left; padding:8px; border-radius:6px 0 0 6px;">Corte / Concepto</th>
                                <th style="text-align:right; padding:8px;">Cant.</th>
                                <th style="text-align:right; padding:8px;">Kilos</th>
                                <th style="text-align:right; padding:8px;">Precio Unit.</th>
                                <th style="text-align:right; padding:8px; border-radius:0 6px 6px 0;">Importe</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr style="border-bottom:1px solid #e2e8f0; background:#ffffff;">
                                <td style="padding:10px 8px; font-weight:700; color:#0f172a;">${esc((r.tipo_corte || 'Carne').toUpperCase())}</td>
                                <td style="padding:10px 8px; text-align:right;">${remitoCantidad(r)}</td>
                                <td style="padding:10px 8px; text-align:right;">${fmt(r.kg)} kg</td>
                                <td style="padding:10px 8px; text-align:right;">$${fmt(r.precio_por_kg || (r.kg > 0 ? (r.precio_venta_total - r.costo_total_logistica) / r.kg : 0))}</td>
                                <td style="padding:10px 8px; text-align:right; font-weight:600; color:#0f172a;">$${fmt(r.precio_venta_total - r.costo_total_logistica)}</td>
                            </tr>
                            ${r.costo_total_logistica > 0 ? `
                            <tr style="border-bottom:1px solid #e2e8f0; background:#f8fafc;">
                                <td style="padding:10px 8px; color:#475569;" colspan="4">Servicio de Reparto (Logística Distribuida FIFO)</td>
                                <td style="padding:10px 8px; text-align:right; font-weight:600; color:#0f172a;">$${fmt(r.costo_total_logistica)}</td>
                            </tr>
                            ` : ''}
                        </tbody>
                    </table>

                    <!-- Piezas en detalle -->
                    ${piezasHtml}

                    <!-- Cuadro de Totales -->
                    <div style="display:flex; justify-content:flex-end; margin-top:8px;">
                        <div style="width:250px; display:flex; flex-direction:column; gap:6px; font-size:11px; background:#f8fafc; border:1px solid #cbd5e1; border-radius:8px; padding:10px;">
                            <div style="display:flex; justify-content:space-between;"><span style="color:#64748b;">Subtotal Carne:</span><span style="font-weight:600; color:#0f172a;">$${fmt(r.precio_venta_total - r.costo_total_logistica)}</span></div>
                            <div style="display:flex; justify-content:space-between;"><span style="color:#64748b;">Costo Logística (Flete):</span><span style="font-weight:600; color:#0f172a;">$${fmt(r.costo_total_logistica)}</span></div>
                            <div style="display:flex; justify-content:space-between; border-top:1px solid #cbd5e1; padding-top:4px;"><span style="color:#475569; font-weight:bold;">TOTAL FACTURADO:</span><span style="font-weight:700; color:#0f172a;">$${fmt(r.precio_venta_total)}</span></div>
                            <div style="display:flex; justify-content:space-between;"><span style="color:#10b981; font-weight:600;">Monto Cobrado:</span><span style="font-weight:700; color:#10b981;">$${fmt(pagado)}</span></div>
                            <div style="display:flex; justify-content:space-between; border-top:1px solid #cbd5e1; padding-top:6px; font-size:12px;"><span style="font-weight:700; color:#0f172a;">SALDO PENDIENTE:</span><span style="font-weight:800; color:${saldo > 0 ? '#ef4444' : '#111827'};">$${fmt(saldo)}</span></div>
                        </div>
                    </div>
                </div>`;
                
                let actionsHtml = '';
                if (pagado > 0) {
                    actionsHtml += `<button type="button" class="btn btn-warning btn-sm" onclick="restablecerPagoFactura(${r.id})" style="font-size: 11px; padding: 6px 10px; font-weight:bold; cursor:pointer;">⚠️ Restablecer Pago</button>`;
                }
                actionsHtml += `<button type="button" class="btn btn-danger btn-sm" onclick="eliminarFactura(${r.id})" style="font-size: 11px; padding: 6px 10px; font-weight:bold; cursor:pointer;">❌ Eliminar Factura</button>`;
                actionsHtml += `<button type="button" class="btn btn-primary" onclick="cerrarModalFacturaOriginal()" style="font-size: 11px; padding: 6px 12px; cursor:pointer; margin-left: auto;">Cerrar</button>`;
                
                $('facturaOriginalActions').innerHTML = actionsHtml;
            } catch (e) {
                $('facturaOriginalBody').innerHTML = `<div style="color:var(--danger);text-align:center;padding:30px">Error al cargar comprobante: ${esc(e.message)}</div>`;
                $('facturaOriginalActions').innerHTML = `<button type="button" class="btn btn-primary" onclick="cerrarModalFacturaOriginal()">Cerrar</button>`;
            }
        };

        window.cerrarModalFacturaOriginal = function() {
            $('modalFacturaOriginal').classList.remove('open');
        };

        window.cargarSaldoViejo = async function(clientId) {
            const val = prompt("Ingrese el saldo viejo/anterior del cliente (ej: 15000):");
            if (val === null) return;
            const parsed = parseFloat(val);
            if (isNaN(parsed) || parsed < 0) {
                toast("Monto inválido", true);
                return;
            }
            const pass = await window.promptMasterPasswordAsync("Ingrese la contraseña maestra para actualizar el saldo inicial:");
            if (!pass) return;
            try {
                const res = await api('/api/clientes/' + clientId + '/saldo-inicial', {
                    method: 'POST',
                    body: JSON.stringify({ saldo_inicial: parsed, password: pass })
                });
                toast(res.message || "Saldo actualizado");
                await loadAll();
                await openClientDrawer(clientId);
            } catch(e) {
                toast(e.message, true);
            }
        };

        window.promptMasterPasswordAsync = function(text) {
            return new Promise(resolve => {
                const textEl = document.getElementById('masterPasswordText');
                const inpEl = document.getElementById('inputMasterPassword');
                const modalEl = document.getElementById('modalMasterPassword');
                const btnConfirm = document.getElementById('btnConfirmMasterPassword');
                const btnClose = modalEl.querySelector('.modal-close');
                const btnCancel = modalEl.querySelector('.btn-ghost');
                
                textEl.textContent = text;
                inpEl.value = '';
                modalEl.classList.add('open');
                inpEl.focus();
                
                const cleanup = () => {
                    btnConfirm.onclick = null;
                    if (btnClose) btnClose.onclick = null;
                    if (btnCancel) btnCancel.onclick = null;
                    modalEl.classList.remove('open');
                };
                
                btnConfirm.onclick = () => {
                    resolve(inpEl.value);
                    cleanup();
                };
                btnConfirm.textContent = 'Confirmar';
                
                if (btnClose) {
                    btnClose.onclick = () => {
                        resolve(null);
                        cleanup();
                    };
                }
                
                if (btnCancel) {
                    btnCancel.onclick = () => {
                        resolve(null);
                        cleanup();
                    };
                }
            });
        };

        window.eliminarCliente = async function(clientId) {
            const pass = await window.promptMasterPasswordAsync("⚠️ Se borrarán permanentemente este cliente, TODAS sus facturas/remitos (se devolverán los kilos al stock) y TODOS sus pagos.");
            if (!pass) return;
            try {
                const res = await api('/api/clientes/' + clientId, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json', 'X-Master-Password': pass }
                });
                toast(res.message || "Cliente eliminado permanentemente");
                
                // Eliminar optimísticamente del estado local antes de recargar
                if (data && data.clientes) {
                    const idx = data.clientes.findIndex(c => String(c.id) === String(clientId));
                    if (idx !== -1) {
                        data.clientes.splice(idx, 1);
                        await tenantCachePut('appData', data);
                    }
                }
                
                await loadAll();
                switchView('clientes');
            } catch(e) {
                toast(e.message, true);
            }
        };

        window.eliminarPagoCliente = async function(pagoId) {
            const pass = await window.promptMasterPasswordAsync("⚠️ Se eliminará este pago. La deuda de las facturas cobradas con este pago se restablecerá.");
            if (!pass) return;
            try {
                const res = await api('/api/pagos/' + pagoId, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json', 'X-Master-Password': pass }
                });
                toast(res.message || "Pago eliminado y deuda restablecida");
                
                // Eliminar optimísticamente del estado local antes de recargar
                if (data) {
                    if (data.historialPagos) {
                        const idx = data.historialPagos.findIndex(p => String(p.id) === String(pagoId));
                        if (idx !== -1) data.historialPagos.splice(idx, 1);
                    }
                    if (currentClientData && currentClientData.pagos) {
                        const idx = currentClientData.pagos.findIndex(p => String(p.id) === String(pagoId));
                        if (idx !== -1) {
                            const p = currentClientData.pagos[idx];
                            currentClientData.saldo_actual = (currentClientData.saldo_actual || 0) + p.monto;
                            currentClientData.pagos.splice(idx, 1);
                        }
                    }
                    if (data.clientes) {
                        const c = data.clientes.find(cli => String(cli.id) === String(selectedClienteId));
                        if (c && c.pagos) {
                            const idx = c.pagos.findIndex(p => String(p.id) === String(pagoId));
                            if (idx !== -1) {
                                const p = c.pagos[idx];
                                c.saldo_actual = (c.saldo_actual || 0) + p.monto;
                                c.pagos.splice(idx, 1);
                            }
                        }
                    }
                    await tenantCachePut('appData', data);
                }
                
                await loadAll();
                if (selectedClienteId) await openClientDrawer(selectedClienteId);
            } catch(e) {
                toast(e.message, true);
            }
        };

        window.restablecerPagoFactura = async function(remitoId) {
            if (!confirm("⚠️ ¿Está seguro de restablecer el pago de esta factura?\nEl monto pagado volverá a cero y se sumará a la deuda del cliente.")) return;
            const pass = await window.promptMasterPasswordAsync("⚠️ Ingrese la contraseña maestra para restablecer el pago de esta factura:");
            if (!pass) return;
            try {
                const res = await api('/api/remitos/' + remitoId + '/reset-pago', {
                    method: 'POST',
                    body: JSON.stringify({ password: pass })
                });
                toast(res.message || "Pago restablecido");
                cerrarModalFacturaOriginal();
                await loadAll();
                if (selectedClienteId) {
                    await openClientDrawer(selectedClienteId);
                }
            } catch(e) {
                toast(e.message, true);
            }
        };

        window.eliminarFactura = async function(remitoId) {
            const pass = await window.promptMasterPasswordAsync("⚠️ Se eliminará esta factura permanentemente. Los kilos volverán a estar disponibles en el stock.");
            if (!pass) return;
            try {
                const res = await api('/api/remitos/' + remitoId, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json', 'X-Master-Password': pass }
                });
                toast(res.message || "Factura eliminada. Stock repuesto.");
                cerrarModalFacturaOriginal();
                
                // Eliminar optimísticamente del estado local antes de recargar
                if (data) {
                    if (currentClientData && currentClientData.remitos) {
                        const idx = currentClientData.remitos.findIndex(r => String(r.id) === String(remitoId));
                        if (idx !== -1) {
                            const r = currentClientData.remitos[idx];
                            currentClientData.saldo_actual = (currentClientData.saldo_actual || 0) - (r.precio_venta_total - (r.monto_pagado || 0));
                            currentClientData.remitos.splice(idx, 1);
                        }
                    }
                    if (data.clientes) {
                        const c = data.clientes.find(cli => String(cli.id) === String(selectedClienteId));
                        if (c && c.remitos) {
                            const idx = c.remitos.findIndex(r => String(r.id) === String(remitoId));
                            if (idx !== -1) {
                                const r = c.remitos[idx];
                                c.saldo_actual = (c.saldo_actual || 0) - (r.precio_venta_total - (r.monto_pagado || 0));
                                c.remitos.splice(idx, 1);
                            }
                        }
                    }
                    await tenantCachePut('appData', data);
                }
                
                await loadAll();
                if (selectedClienteId) {
                    await openClientDrawer(selectedClienteId);
                }
            } catch(e) {
                toast(e.message, true);
            }
        };

        function buildReporteClienteHtml(c) {
            const remitos = c.remitos || [];
            const genAt = new Date().toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' });
            const emp = getEmpresaDatos();
            const initials = emp.nombre
                .split(' ')
                .map(w => w[0])
                .join('')
                .toUpperCase()
                .slice(0, 2) || 'MT';
            const totalKg = remitos.reduce((acc, r) => acc + r.kg, 0);
            const totalVendido = remitos.reduce((acc, r) => acc + r.precio_venta_total, 0);
            const totalCobrado = remitos.reduce((acc, r) => acc + (Number(r.monto_pagado || 0) || (Number(r.pagado) === 1 ? r.precio_venta_total : 0)), 0);
            const disponible = Math.max(0, c.techo_deuda - c.saldo_actual);
            const totalUnidades = remitos.reduce((acc, r) => acc + remitoCantidad(r), 0);

            const remitosRows = remitos.length
                ? remitos.map((r) => {
                    const est = remitoEstado(r.estado_cobro ?? r.pagado);
                    const piezas = remitoPesosPiezas(r);
                    const cant = remitoCantidad(r);
                    const corte = (r.tipo_corte || '—').toUpperCase();
                    const pxKg = r.precio_por_kg || (r.kg > 0 ? r.precio_venta_total / r.kg : 0);
                    const pagado = Number(r.monto_pagado || 0) || (Number(r.pagado) === 1 ? r.precio_venta_total : 0);
                    const saldo = Math.max(0, r.precio_venta_total - pagado);
                    const plazo = r.plazo_cobro_dias != null ? `${r.plazo_cobro_dias} días` : '—';
                    const importe = r.precio_venta_total;
                    const detalleCarneHtml = piezas.length
                        ? `<div class="detalle-cell"><span class="corte-nombre">${esc(corte)}</span>${pesosPiezasHtml(piezas)}</div>`
                        : `<div class="detalle-cell"><span class="corte-nombre">${esc(corte)}</span><span class="sin-piezas">${fmt(r.kg)} kg total</span></div>`;

                    if (r.costo_total_logistica > 0) {
                        const importeCarne = r.precio_venta_total - r.costo_total_logistica;
                        const logisticaPxKg = r.costo_total_logistica / (r.kg || 1);
                        return `
                            <tr style="border-bottom: none;">
                                <td rowspan="2" style="vertical-align: middle; border-bottom: 1px solid #e2e8f0;">${fmtFechaRemito(r.fecha)}</td>
                                <td rowspan="2" class="col-mono" style="vertical-align: middle; border-bottom: 1px solid #e2e8f0;">${String(r.id).padStart(3, '0')}</td>
                                <td class="col-qty" style="border-bottom: none; padding-bottom: 4px;">${cant}</td>
                                <td class="col-detalle" style="border-bottom: none; padding-bottom: 4px;">${detalleCarneHtml}</td>
                                <td class="col-num" style="border-bottom: none; padding-bottom: 4px;">${Number(r.kg).toFixed(2)}</td>
                                <td class="col-num" style="border-bottom: none; padding-bottom: 4px;">${Number(pxKg).toFixed(2)}</td>
                                <td class="col-num col-total" style="border-bottom: none; padding-bottom: 4px;">$${fmt(importeCarne)}</td>
                                <td rowspan="2" class="col-num" style="vertical-align: middle; border-bottom: 1px solid #e2e8f0;">$${fmt(pagado)}</td>
                                <td rowspan="2" class="col-num col-saldo" style="vertical-align: middle; border-bottom: 1px solid #e2e8f0;">$${fmt(saldo)}</td>
                                <td rowspan="2" class="col-plazo" style="vertical-align: middle; border-bottom: 1px solid #e2e8f0;">${plazo}</td>
                                <td rowspan="2" class="col-estado" style="vertical-align: middle; border-bottom: 1px solid #e2e8f0;"><span class="status ${reporteEstadoClass(est.label)}">${esc(est.label.toUpperCase())}</span></td>
                            </tr>
                            <tr>
                                <td class="col-qty" style="padding-top: 0; color: #94a3b8;">—</td>
                                <td class="col-detalle" style="padding-top: 0;"><div class="detalle-cell"><span class="corte-nombre" style="color:#64748b; font-size: 11px;">🚚 LOGÍSTICA</span></div></td>
                                <td class="col-num" style="color:#64748b; font-size: 11px; padding-top: 0;">${Number(r.kg).toFixed(2)}</td>
                                <td class="col-num" style="color:#64748b; font-size: 11px; padding-top: 0;">${Number(logisticaPxKg).toFixed(2)}</td>
                                <td class="col-num col-total" style="font-size: 12px; color: #475569; padding-top: 0;">$${fmt(r.costo_total_logistica)}</td>
                            </tr>
                        `;
                    } else {
                        return `
                            <tr>
                                <td>${fmtFechaRemito(r.fecha)}</td>
                                <td class="col-mono">${String(r.id).padStart(3, '0')}</td>
                                <td class="col-qty">${cant}</td>
                                <td class="col-detalle">${detalleCarneHtml}</td>
                                <td class="col-num">${Number(r.kg).toFixed(2)}</td>
                                <td class="col-num">${Number(pxKg).toFixed(2)}</td>
                                <td class="col-num col-total">$${fmt(importe)}</td>
                                <td class="col-num">$${fmt(pagado)}</td>
                                <td class="col-num col-saldo">$${fmt(saldo)}</td>
                                <td class="col-plazo">${plazo}</td>
                                <td class="col-estado"><span class="status ${reporteEstadoClass(est.label)}">${esc(est.label.toUpperCase())}</span></td>
                            </tr>`;
                    }
                }).join('')
                : '<tr><td colspan="11" class="empty-row">Sin movimientos registrados</td></tr>';

            const totalesRemitosRow = remitos.length ? `
                <tr class="totales-row">
                    <td colspan="2"><strong>TOTALES</strong></td>
                    <td class="col-qty"><strong>${totalUnidades}</strong></td>
                    <td></td>
                    <td class="col-num"><strong>${Number(totalKg).toFixed(2)}</strong></td>
                    <td></td>
                    <td class="col-num col-total"><strong>$${fmt(totalVendido)}</strong></td>
                    <td class="col-num"><strong>$${fmt(totalCobrado)}</strong></td>
                    <td class="col-num"><strong>$${fmt(c.saldo_actual)}</strong></td>
                    <td colspan="2"></td>
                </tr>` : '';

            return `<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <title>Estado de cuenta — ${esc(c.nombre)}</title>
    <style>
        @media print { * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; color-adjust: exact !important; } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
            color: #111827;
            font-size: 10pt;
            line-height: 1.45;
            background: #fff;
            padding: 28px 32px;
        }
        .doc-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 24px;
            padding-bottom: 20px;
            border-bottom: 3px solid #0d6efd;
            margin-bottom: 22px;
        }
        .brand-block { display: flex; gap: 14px; align-items: center; }
        .brand-logo {
            width: 52px; height: 52px; background: #0d6efd; color: #fff;
            border-radius: 10px; display: flex; align-items: center; justify-content: center;
            font-weight: 800; font-size: 17px; letter-spacing: -0.5px; flex-shrink: 0;
        }
        .brand-name { font-size: 17pt; font-weight: 700; color: #0f172a; letter-spacing: -0.3px; }
        .brand-tag { font-size: 9pt; color: #64748b; margin-top: 2px; }
        .doc-meta { text-align: right; font-size: 9pt; color: #475569; }
        .doc-meta .doc-type {
            display: inline-block; background: #eff6ff; color: #1d4ed8;
            font-weight: 700; font-size: 8.5pt; letter-spacing: 0.06em;
            text-transform: uppercase; padding: 4px 10px; border-radius: 6px; margin-bottom: 6px;
        }
        .doc-meta .doc-date { font-weight: 600; color: #334155; }
        .info-grid {
            display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 22px;
        }
        .info-card {
            border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 16px; background: #f8fafc;
        }
        .info-card h4 {
            font-size: 8pt; text-transform: uppercase; letter-spacing: 0.08em;
            color: #64748b; margin-bottom: 10px; font-weight: 700;
        }
        .info-row { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; font-size: 9.5pt; }
        .info-row .lbl { color: #64748b; }
        .info-row .val { font-weight: 600; text-align: right; }
        .info-row.highlight .val { color: #b45309; font-size: 11pt; }
        .info-row.balance .val { color: #dc2626; font-size: 12pt; font-weight: 800; }
        .section-title {
            font-size: 9pt; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.07em; color: #334155; margin-bottom: 10px;
            padding-bottom: 6px; border-bottom: 1px solid #e2e8f0;
        }
        .items-table { width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 9pt; }
        .items-table thead th {
            background: #0f172a; color: #fff; font-weight: 600; font-size: 8pt;
            text-transform: uppercase; letter-spacing: 0.04em;
            padding: 9px 7px; text-align: left; white-space: nowrap;
        }
        .items-table thead th.col-num, .items-table tbody td.col-num,
        .items-table thead th.col-qty, .items-table tbody td.col-qty { text-align: right; }
        .items-table tbody td {
            padding: 10px 8px; border-bottom: 1px solid #e2e8f0; vertical-align: top;
        }
        .items-table tbody tr:nth-child(even) { background: #f8fafc; }
        .items-table tbody tr:hover { background: #eff6ff; }
        .col-detalle { min-width: 180px; max-width: 340px; }
        .detalle-cell .corte-nombre {
            display: block; font-weight: 700; font-size: 8.5pt; letter-spacing: 0.04em;
            color: #0f172a; margin-bottom: 6px;
        }
        .pesos-grid { display: flex; flex-wrap: wrap; gap: 4px; }
        .peso-chip {
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 8pt; font-weight: 600; color: #1e40af;
            background: #eff6ff; border: 1px solid #bfdbfe;
            padding: 2px 6px; border-radius: 4px; min-width: 42px; text-align: center;
        }
        .sin-piezas { font-size: 8pt; color: #64748b; }
        .totales-row { background: #0f172a !important; color: #fff; }
        .totales-row td { border-bottom: none; padding: 10px 8px; font-size: 9pt; }
        .totales-row .col-total { color: #fff; }
        .col-mono { font-family: 'Consolas', 'Courier New', monospace; font-size: 8.5pt; color: #475569; }
        .col-total { font-weight: 700; color: #0f172a; }
        .col-saldo { font-weight: 700; color: #dc2626; }
        .col-plazo { font-size: 8.5pt; color: #64748b; white-space: nowrap; text-align: center; }
        .col-estado { text-align: center; white-space: nowrap; }
        .empty-row { text-align: center; padding: 24px !important; color: #94a3b8; }
        .status {
            display: inline-block; font-size: 7.5pt; font-weight: 700;
            letter-spacing: 0.05em; padding: 4px 10px; border-radius: 6px; white-space: nowrap;
        }
        .st-paid { background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }
        .st-partial { background: #fef9c3; color: #854d0e; border: 1px solid #fde68a; }
        .st-pending { background: #ffffff; color: #dc2626; border: 1.5px solid #dc2626; }
        .st-bad { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; }
        .totals-grid {
            display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 4px;
        }
        .totals-card {
            border: 2px solid #0f172a; border-radius: 10px; padding: 16px 18px;
        }
        .totals-card.secondary { border-color: #cbd5e1; }
        .totals-card h4 {
            font-size: 8pt; text-transform: uppercase; letter-spacing: 0.08em;
            color: #64748b; margin-bottom: 12px; font-weight: 700;
        }
        .total-row {
            display: flex; justify-content: space-between; align-items: baseline;
            padding: 5px 0; font-size: 9.5pt; border-bottom: 1px dashed #e2e8f0;
        }
        .total-row:last-child { border-bottom: none; }
        .total-row.grand {
            margin-top: 8px; padding-top: 10px; border-top: 2px solid #0f172a;
            border-bottom: none; font-size: 11pt;
        }
        .total-row.grand .lbl { font-weight: 700; color: #0f172a; }
        .total-row.grand .val { font-weight: 800; color: #dc2626; font-size: 14pt; }
        .total-row .lbl { color: #475569; }
        .total-row .val { font-weight: 700; color: #0f172a; }
        .total-row.credit .val { color: #059669; }
        .doc-footer {
            margin-top: 28px; padding-top: 14px; border-top: 1px solid #e2e8f0;
            text-align: center; font-size: 8pt; color: #94a3b8;
        }
        .doc-footer p + p { margin-top: 3px; }
        .legal-note {
            margin-top: 16px; font-size: 8pt; color: #64748b;
            background: #f1f5f9; border-radius: 8px; padding: 10px 14px;
            border-left: 3px solid #0d6efd;
        }
        @media print {
            body { padding: 12px 16px; }
            .items-table tbody tr:hover { background: inherit; }
            @page { margin: 12mm; }
        }
    </style>
</head>
<body>
    <header class="doc-header">
        <div class="brand-block">
            <div class="brand-logo">${esc(initials)}</div>
            <div>
                <div class="brand-name">${esc(emp.nombre)}</div>
                <div class="brand-tag">Distribuidora de Carne · Cuenta Corriente</div>
                <div style="font-size: 8.5pt; color: #475569; margin-top: 4px; line-height: 1.3;">
                    ${emp.cuit ? `CUIT: ${esc(emp.cuit)}<br>` : ''}
                    ${emp.direccion ? `Dirección: ${esc(emp.direccion)}<br>` : ''}
                    ${emp.telefono || emp.email ? `${emp.telefono ? `Tel: ${esc(emp.telefono)}` : ''}${emp.telefono && emp.email ? ' | ' : ''}${emp.email ? `Email: ${esc(emp.email)}` : ''}` : ''}
                </div>
            </div>
        </div>
        <div class="doc-meta">
            <div class="doc-type">Estado de Cuenta</div>
            <div class="doc-date">Generado: ${genAt}</div>
        </div>
    </header>

    <div class="info-grid">
        <div class="info-card">
            <h4>Datos del cliente</h4>
            <div class="info-row"><span class="lbl">Razón social</span><span class="val">${esc(c.nombre)}</span></div>
            ${c.cuit ? `<div class="info-row"><span class="lbl">CUIT</span><span class="val">${esc(c.cuit)}</span></div>` : ''}
            ${c.direccion ? `<div class="info-row"><span class="lbl">Dirección</span><span class="val">${esc(c.direccion)}</span></div>` : ''}
            ${c.telefono ? `<div class="info-row"><span class="lbl">Teléfono</span><span class="val">${esc(c.telefono)}</span></div>` : ''}
            ${c.email ? `<div class="info-row"><span class="lbl">Email</span><span class="val">${esc(c.email)}</span></div>` : ''}
            <div class="info-row"><span class="lbl">Scoring de crédito</span><span class="val">${esc(c.scoring)}</span></div>
            <div class="info-row"><span class="lbl">Techo de deuda</span><span class="val">$${fmt(c.techo_deuda)}</span></div>
        </div>
        <div class="info-card">
            <h4>Situación financiera</h4>
            <div class="info-row highlight"><span class="lbl">Saldo deudor</span><span class="val">$${fmt(c.saldo_actual)}</span></div>
            <div class="info-row credit"><span class="lbl">Crédito disponible</span><span class="val">$${fmt(disponible)}</span></div>
            <div class="info-row"><span class="lbl">Remitos en cuenta</span><span class="val">${remitos.length}</span></div>
        </div>
    </div>

    <div class="section-title">Detalle de remitos — facturación por pieza</div>
    <table class="items-table">
        <thead>
            <tr>
                <th>Fecha</th>
                <th>Cód.</th>
                <th class="col-qty">Cant.</th>
                <th>Detalle (pesos por pieza)</th>
                <th class="col-num">Kilos</th>
                <th class="col-num">Precio</th>
                <th class="col-num">Importe</th>
                <th class="col-num">Pagado</th>
                <th class="col-num">Saldo</th>
                <th>Plazo</th>
                <th>Estado</th>
            </tr>
        </thead>
        <tbody>${remitosRows}${totalesRemitosRow}</tbody>
    </table>

    <div class="totals-grid">
        <div class="totals-card secondary">
            <h4>Resumen operativo</h4>
            <div class="total-row"><span class="lbl">Unidades totales</span><span class="val">${totalUnidades}</span></div>
            <div class="total-row"><span class="lbl">Kilos facturados</span><span class="val">${fmt(totalKg)} kg</span></div>
            <div class="total-row"><span class="lbl">Total facturado</span><span class="val">$${fmt(totalVendido)}</span></div>
            <div class="total-row"><span class="lbl">Total cobrado</span><span class="val">$${fmt(totalCobrado)}</span></div>
        </div>
        <div class="totals-card">
            <h4>Balance de cuenta</h4>
            <div class="total-row"><span class="lbl">Facturación acumulada</span><span class="val">$${fmt(totalVendido)}</span></div>
            <div class="total-row"><span class="lbl">Pagos recibidos</span><span class="val">$${fmt(totalCobrado)}</span></div>
            <div class="total-row grand"><span class="lbl">SALDO IMPAGO</span><span class="val">$${fmt(c.saldo_actual)}</span></div>
            <div class="total-row credit"><span class="lbl">Crédito libre</span><span class="val">$${fmt(disponible)}</span></div>
        </div>
    </div>

    <div class="legal-note">
        Documento informativo de cuenta corriente. Los montos en pesos argentinos ($). 
        El plazo de cobro indicado por remito corresponde al acuerdo comercial vigente al momento de la entrega.
    </div>

    <footer class="doc-footer">
        <p>${esc(emp.nombre)} Terminal · Distribuidoras de Carne</p>
        <p>— Fin del estado de cuenta —</p>
    </footer>
    <script>
        window.onload = function() { window.print(); setTimeout(function() { window.close(); }, 600); };
    <\/script>
</body>
</html>`;
        }

        $('btnClientPrint').addEventListener('click', async () => {
            if (!currentClientData) return;
            try {
                const c = await api('/api/clientes/' + currentClientData.id);
                
                const printWindow = window.open('', '_blank', 'width=900,height=700');
                if (!printWindow) {
                    toast('El navegador bloqueó la ventana emergente de impresión', true);
                    return;
                }
                
                printWindow.document.write(buildReporteClienteHtml(c));
                printWindow.document.close();
            } catch (e) {
                toast('Error al generar reporte impreso: ' + e.message, true);
            }
        });

        document.addEventListener('keydown', ev => {
            const activeView = document.querySelector('.view.active');
            if (!activeView) return;

            // Evitar interferir con la escritura en campos de formulario
            if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT' || ev.target.tagName === 'TEXTAREA') {
                if (ev.key === 'Escape') {
                    ev.target.blur();
                }
                return;
            }

            // Cambiar de vista con las teclas 1 a 6
            if (ev.key === '1') { switchView('dashboard'); return; }
            if (ev.key === '2') { switchView('home'); return; }
            if (ev.key === '3') { switchView('remitos'); return; }
            if (ev.key === '4') { switchView('clientes'); return; }
            if (ev.key === '5') { switchView('registro'); return; }
            if (ev.key === '6') { switchView('historial-pagos'); return; }

            // Buscar filas en el cuerpo de la tabla activa de la pestaña
            const tbody = activeView.querySelector('tbody');
            if (!tbody) return;
            const rows = Array.from(tbody.querySelectorAll('tr'));
            if (!rows.length) return;

            if (ev.key === 'ArrowDown') {
                ev.preventDefault();
                activeRowIndex = (activeRowIndex + 1) % rows.length;
                updateSelectedRow(rows);
            } else if (ev.key === 'ArrowUp') {
                ev.preventDefault();
                activeRowIndex = (activeRowIndex - 1 + rows.length) % rows.length;
                updateSelectedRow(rows);
            } else if (ev.key === 'Enter') {
                if (activeRowIndex >= 0 && activeRowIndex < rows.length) {
                    ev.preventDefault();
                    rows[activeRowIndex].click();
                }
            } else if (ev.key === 'Escape') {
                closeClientDrawer();
                closeDrawer();
                if (typeof closeLossDrawer === 'function') closeLossDrawer();
            }
        });

        if ($('inpBulkFecha')) {
            $('inpBulkFecha').value = new Date().toISOString().split('T')[0];
        }

        function tickClock() {
            if ($('clock')) {
                $('clock').textContent = new Date().toLocaleString('es-AR', {
                    weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'
                });
            }
        }
        tickClock();
        setInterval(tickClock, 30000);

        // Connection status monitoring
        function updateConnectionStatus() {
            const online = navigator.onLine;
            const pill = $('statusPill');
            const mobilePill = $('mobileConnPill');
            const mobileText = $('mobileConnText');
            if (pill) {
                if (online) {
                    pill.innerHTML = '<span class="pulse"></span> En línea';
                    pill.style.background = 'var(--success-muted)';
                    pill.style.color = 'var(--success)';
                    pill.style.border = '1px solid rgba(16, 185, 129, 0.2)';
                } else {
                    pill.innerHTML = '<span class="pulse" style="background:var(--danger)"></span> Fuera de línea';
                    pill.style.background = 'var(--danger-muted)';
                    pill.style.color = 'var(--danger)';
                    pill.style.border = '1px solid rgba(239, 68, 68, 0.2)';
                    toast('Sin conexión: mostrando datos del panel local', true);
                }
            }
            if (mobilePill) {
                mobilePill.classList.toggle('online', online);
                mobilePill.classList.toggle('offline', !online);
                if (mobileText) mobileText.textContent = online ? 'En línea' : 'Sin red';
            }
        }
        window.addEventListener('online', () => {
            updateConnectionStatus();
            sincronizarSolicitudesPendientes();
        });
        window.addEventListener('offline', updateConnectionStatus);
        
        document.getElementById('offlineBadge')?.addEventListener('click', sincronizarSolicitudesPendientes);
        actualizarUIOffline();
        window.abrirNuevaVenta = function() {
            $('formRemito').reset();
            switchView('ventas-express');
            if (currentClientData) {
                $('inpVentaCliente').value = currentClientData.nombre;
                $('btnVolverVentasExpress').textContent = '← Volver al Perfil de ' + currentClientData.nombre;
            } else {
                $('btnVolverVentasExpress').textContent = '← Volver a opciones';
            }
            switchView('ventas-express');
            abrirSubVistaVentasExpress('ventasExpressNueva');
        }
        updateConnectionStatus();

        // PWA Installation handling
        let deferredPrompt;
        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
            const btn = $('btnInstallApp');
            const sect = $('installSection');
            if (btn) btn.style.display = 'flex';
            if (sect) sect.style.display = 'block';
        });

        $('btnInstallApp')?.addEventListener('click', async () => {
            if (!deferredPrompt) return;
            deferredPrompt.prompt();
            const { outcome } = await deferredPrompt.userChoice;
            console.log(`PWA install prompt outcome: ${outcome}`);
            deferredPrompt = null;
            const btn = $('btnInstallApp');
            const sect = $('installSection');
            if (btn) btn.style.display = 'none';
            if (sect) sect.style.display = 'none';
        });

        window.addEventListener('appinstalled', () => {
            toast('¡Aplicación instalada con éxito!');
            const btn = $('btnInstallApp');
            const sect = $('installSection');
            if (btn) btn.style.display = 'none';
            if (sect) sect.style.display = 'none';
            localStorage.setItem('pwa_ios_hint_dismissed', '1');
            $('iosInstallBanner')?.classList.add('field-hidden');
        });

        async function renderFinanzasAging() {
            setLoading(true);
            try {
                const res = await api('/api/finanzas/aging');
                
                // Set total cards
                const t0_30 = $('ageTotal0_30');
                const t31_60 = $('ageTotal31_60');
                const t61_90 = $('ageTotal61_90');
                const t90_plus = $('ageTotal90_plus');
                
                if (t0_30) t0_30.innerHTML = fmtDual(res.totales['0_30'] || 0);
                if (t31_60) t31_60.innerHTML = fmtDual(res.totales['31_60'] || 0);
                if (t61_90) t61_90.innerHTML = fmtDual(res.totales['61_90'] || 0);
                if (t90_plus) t90_plus.innerHTML = fmtDual(res.totales['90_plus'] || 0);
                
                const tbody = $('tblFinanzasAging');
                if (tbody) {
                    tbody.innerHTML = '';
                    if (!res.clientes || res.clientes.length === 0) {
                        tbody.innerHTML = `<tr><td colspan="6" style="color:var(--text-muted);padding:16px;text-align:center">No hay clientes con saldo pendiente de pago.</td></tr>`;
                        return;
                    }
                    
                    const withBalance = res.clientes.filter(c => c.saldo_actual > 0);
                    if (withBalance.length === 0) {
                        tbody.innerHTML = `<tr><td colspan="6" style="color:var(--text-muted);padding:16px;text-align:center">No hay clientes con saldo pendiente de pago.</td></tr>`;
                        return;
                    }

                    withBalance.forEach(c => {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td><strong>${esc(c.nombre)}</strong></td>
                            <td>${fmtDual(c.saldo_actual)}</td>
                            <td>${c.buckets['0_30'] > 0 ? fmtDual(c.buckets['0_30']) : '—'}</td>
                            <td>${c.buckets['31_60'] > 0 ? fmtDual(c.buckets['31_60']) : '—'}</td>
                            <td>${c.buckets['61_90'] > 0 ? fmtDual(c.buckets['61_90']) : '—'}</td>
                            <td>${c.buckets['90_plus'] > 0 ? fmtDual(c.buckets['90_plus']) : '—'}</td>
                        `;
                        tbody.appendChild(tr);
                    });
                }
            } catch (e) {
                console.error("Error al cargar la antigüedad de deuda:", e);
                toast("Error al cargar reporte de antigüedad de deuda", true);
            } finally {
                setLoading(false);
            }
        }

        async function renderFinanzasMargenes() {
            setLoading(true);
            try {
                const res = await api('/api/finanzas/margenes?limit=200');
                const tbody = $('tblFinanzasMargenes');
                if (tbody) {
                    tbody.innerHTML = '';
                    if (!res || res.length === 0) {
                        tbody.innerHTML = `<tr><td colspan="8" style="color:var(--text-muted);padding:16px;text-align:center">No hay remitos de carga para analizar.</td></tr>`;
                        return;
                    }
                    
                    res.forEach(r => {
                        const tr = document.createElement('tr');
                        const pctColor = r.porcentaje_margen >= 15 ? 'var(--success)' : (r.porcentaje_margen >= 5 ? 'var(--warning)' : 'var(--danger)');
                        
                        tr.innerHTML = `
                            <td><strong>#${esc(r.id)}</strong></td>
                            <td>${esc(r.fecha)}</td>
                            <td>${fmt(r.kg)} kg</td>
                            <td>${fmtDual(r.precio_venta_total)}</td>
                            <td>${fmtDual(r.costo_carne)}</td>
                            <td>${fmtDual(r.costo_logistica)}</td>
                            <td><strong style="color: ${r.margen_neto >= 0 ? 'inherit' : 'var(--danger)'}">${fmtDual(r.margen_neto)}</strong></td>
                            <td><span class="badge" style="background-color: ${pctColor}22; color: ${pctColor}; font-weight: 700;">${r.porcentaje_margen.toFixed(1)}%</span></td>
                        `;
                        tbody.appendChild(tr);
                    });
                }
            } catch (e) {
                console.error("Error al cargar márgenes de venta:", e);
                toast("Error al cargar reporte de márgenes de venta", true);
            } finally {
                setLoading(false);
            }
        }

        function registerServiceWorker() {
            if (!('serviceWorker' in navigator)) return;
            let swReloading = false;
            navigator.serviceWorker.addEventListener('controllerchange', () => {
                if (swReloading) return;
                swReloading = true;
                window.location.reload();
            });
            navigator.serviceWorker.register('/sw.js').then((reg) => {
                if (reg.waiting) showPwaUpdateBanner(reg);
                reg.addEventListener('updatefound', () => {
                    const nw = reg.installing;
                    nw?.addEventListener('statechange', () => {
                        if (nw.state === 'installed' && navigator.serviceWorker.controller) {
                            showPwaUpdateBanner(reg);
                        }
                    });
                });
            }).catch(e => console.warn('PWA SW err:', e));
        }

        function showPwaUpdateBanner(reg) {
            if (document.getElementById('pwaUpdateBanner')) return;
            const el = document.createElement('div');
            el.id = 'pwaUpdateBanner';
            el.className = 'pwa-update-toast';
            el.innerHTML = '<span>Nueva versión disponible</span><button type="button">Actualizar</button>';
            el.querySelector('button')?.addEventListener('click', () => {
                reg.waiting?.postMessage('SKIP_WAITING');
                window.location.reload();
            });
            document.body.appendChild(el);
        }

        function initIosInstallHint() {
            const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
            const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;
            const dismissed = localStorage.getItem('pwa_ios_hint_dismissed');
            const banner = $('iosInstallBanner');
            if (isIos && !standalone && !dismissed && banner) {
                banner.classList.remove('field-hidden');
            }
            $('iosInstallDismiss')?.addEventListener('click', () => {
                localStorage.setItem('pwa_ios_hint_dismissed', '1');
                banner?.classList.add('field-hidden');
            });
        }

        function applyPwaDeepLink() {
            const params = new URLSearchParams(window.location.search);
            const view = params.get('view');
            if (view && titles[view]) {
                switchView(view);
            }
        }

        registerServiceWorker();
        initIosInstallHint();

        async function boot() {
            await loadSession();
            const prevUser = localStorage.getItem('sync_user') || '';
            const curUser = sessionUser.username || '';
            const usuarioCambio = prevUser && prevUser !== curUser;
            if (usuarioCambio) {
                await db.cache.clear();
            }
            if (curUser) localStorage.setItem('sync_user', curUser);
            const sinCacheLocal = !(await tenantCacheGet('appData'));
            await loadAll({
                forzarServidor: sinCacheLocal || usuarioCambio,
                avisarSiVacio: true,
            });
            if (new URLSearchParams(window.location.search).get('view')) {
                applyPwaDeepLink();
            } else {
                switchView('home');
            }
        }

        async function updateWeather() {
            function fetchW(lat, lon) {
                fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current_weather=true`)
                    .then(r => r.json())
                    .then(data => {
                        const ww = $('weatherWidget');
                        if (ww) ww.textContent = Math.round(data.current_weather.temperature) + '°';
                    }).catch(e => console.warn('Clima offline', e));
            }
            try {
                if ("geolocation" in navigator) {
                    navigator.geolocation.getCurrentPosition(
                        pos => fetchW(pos.coords.latitude, pos.coords.longitude),
                        err => fetchW(-34.61, -58.38)
                    );
                } else {
                    fetchW(-34.61, -58.38);
                }
            } catch (e) {
                console.warn('Geolocation failed', e);
                fetchW(-34.61, -58.38);
            }
        }
        updateWeather();
        setInterval(updateWeather, 1800000); // 30 mins

        boot();
        setInterval(loadAll, 60000);