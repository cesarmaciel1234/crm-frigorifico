/**
 * Eliminaciones + mutaciones optimistas offline.
 */
(function (global) {
    const { tenantCachePut, ensureDataShape } = global.CrmDb;
    const { api } = global.CrmApi;
    const bus = () => global.CrmBus;

    function parseNumLocal(v) {
        const n = parseFloat(v);
        return Number.isFinite(n) ? n : 0;
    }

    function getData() {
        return ensureDataShape(bus().emit('getData'));
    }

    function setData(d) {
        bus().emit('setData', ensureDataShape(d));
    }

    async function persistLocal() {
        const data = getData();
        await tenantCachePut('appData', data);
        bus().emit('recalcularMetricasLocales');
        bus().emit('renderAll');
        void global.CrmApi.actualizarUIOffline();
    }

    function deleteHeaders(pw) {
        const headers = {
            'Content-Type': 'application/json',
            'X-Master-Password': pw,
        };
        const deviceId = global.CrmSync?.deviceId || '';
        if (deviceId) headers['X-Device-Id'] = deviceId;
        return headers;
    }

    function aplicarPagoCuotaOptimista(data, opId, numero, monto) {
        const e = data.enemigos.find(x => String(x.id) === String(opId));
        if (!e || !numero) return;
        e.cuotas_pagadas = Math.max(e.cuotas_pagadas || 0, numero);
        const total = parseInt(e.cuotas || e.cuotas_total || e.meses, 10) || 0;
        if (total && e.cuotas_pagadas >= total) e.completa = true;
        e.monto_pagado = (e.monto_pagado || 0) + (monto || 0);
    }

    function aplicarCobroRemitoOptimista(data, remitoId, monto) {
        const m = parseFloat(monto) || 0;
        for (const c of data.clientes) {
            const r = (c.remitos || []).find(rem => String(rem.id) === String(remitoId));
            if (!r) continue;
            r.monto_pagado = (r.monto_pagado || 0) + m;
            if (r.monto_pagado >= (r.precio_venta_total || 0) - 0.01) r.pagado = 1;
            c.saldo_actual = (c.saldo_actual || 0) - m;
            c.pagos = c.pagos || [];
            c.pagos.push({
                id: 'temp_' + crypto.randomUUID(),
                monto: m,
                fecha: new Date().toISOString().split('T')[0],
                tipo: 'COBRO',
            });
            break;
        }
    }

    function aplicarCobroClienteOptimista(data, clientId, monto) {
        const m = parseFloat(monto) || 0;
        const c = data.clientes.find(cli => String(cli.id) === String(clientId));
        if (!c) return;
        c.saldo_actual = (c.saldo_actual || 0) - m;
        c.pagos = c.pagos || [];
        c.pagos.push({
            id: 'temp_' + crypto.randomUUID(),
            monto: m,
            fecha: new Date().toISOString().split('T')[0],
            tipo: 'COBRO',
        });
        if (!data.historialPagos) data.historialPagos = [];
        data.historialPagos.unshift({
            id: 'temp_' + Date.now(),
            cliente: c.nombre,
            monto: m,
            fecha: new Date().toISOString().split('T')[0],
            tipo: 'COBRO',
        });
    }

    async function aplicarCambioOptimista(url, method, body) {
        try {
            let data = getData();
            const parsedBody = body ? JSON.parse(body) : {};

            if (url.includes('/api/operaciones') && method === 'POST' && !url.includes('/pagar')) {
                const uuid = parsedBody.uuid || crypto.randomUUID();
                await bus().emit('aplicarOperacionOptimista', parsedBody, uuid);
                return;
            }

            if (url.includes('/api/bulk') && method === 'POST') {
                const loteUuid = parsedBody.uuid || crypto.randomUUID();
                const kg = parseNumLocal(parsedBody.kg_totales);
                const costo = parseNumLocal(parsedBody.costo_total_bulk);
                data.bulk.unshift({
                    id: 'temp_' + loteUuid.slice(0, 8),
                    uuid: loteUuid,
                    fecha: parsedBody.fecha || new Date().toISOString().slice(0, 10),
                    kg_totales: kg,
                    kg_remanentes: kg,
                    costo_total_bulk: costo,
                    costo_reparto: parseNumLocal(parsedBody.costo_reparto),
                    costo_kg: kg > 0 ? Math.round((costo / kg) * 100) / 100 : 0,
                    activo: true,
                    numero_lote: parsedBody.numero_lote || '',
                    fecha_vencimiento: parsedBody.fecha_vencimiento || '',
                    proveedor: parsedBody.proveedor || '',
                    _optimistic: true,
                });
                setData(data);
                await bus().emit('persistirYRefrescarUI');
                return;
            }

            if (url.includes('/api/clientes') && method === 'POST'
                && !url.includes('/cobrar') && !url.includes('/saldo-inicial') && !url.includes('/incobrable')) {
                const clientUuid = parsedBody.uuid || crypto.randomUUID();
                data.clientes.push({
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
                    pagos: [],
                });
            }

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
                    kg,
                    precio_por_kg: precio,
                    precio_venta_total: total,
                    pagado: 0,
                    monto_pagado: 0,
                    fecha: new Date().toISOString().split('T')[0],
                };
                if (c) {
                    c.remitos = c.remitos || [];
                    c.remitos.push(newRemito);
                    c.saldo_actual = (c.saldo_actual || 0) + total;
                }
            }

            if (url.includes('/api/clientes/') && url.includes('/cobrar') && method === 'POST') {
                const match = url.match(/\/api\/clientes\/([^/]+)\/cobrar/);
                if (match) {
                    const clientId = match[1];
                    const c = data.clientes.find(cli => String(cli.id) === String(clientId));
                    const monto = parseFloat(parsedBody.monto_pagado || 0);
                    if (c) {
                        c.saldo_actual = (c.saldo_actual || 0) - monto;
                        c.pagos = c.pagos || [];
                        c.pagos.push({
                            id: 'temp_' + crypto.randomUUID(),
                            monto,
                            fecha: new Date().toISOString().split('T')[0],
                            tipo: 'COBRO',
                        });
                    }
                }
            }

            if (url.includes('/api/remitos/') && url.includes('/cobrar') && method === 'POST') {
                const match = url.match(/\/api\/remitos\/([^/]+)\/cobrar/);
                if (match) {
                    const remitoId = match[1];
                    const monto = parseFloat(parsedBody.monto_pagado || 0);
                    for (const c of data.clientes) {
                        const r = (c.remitos || []).find(rem => String(rem.id) === String(remitoId));
                        if (r) {
                            r.monto_pagado = (r.monto_pagado || 0) + monto;
                            if (r.monto_pagado >= r.precio_venta_total - 0.01) r.pagado = 1;
                            c.saldo_actual = (c.saldo_actual || 0) - monto;
                            c.pagos = c.pagos || [];
                            c.pagos.push({
                                id: 'temp_' + Date.now(),
                                monto,
                                fecha: new Date().toISOString().split('T')[0],
                                tipo: 'COBRO',
                            });
                            break;
                        }
                    }
                }
            }

            if (url.includes('/api/operaciones/') && method === 'DELETE') {
                const match = url.match(/\/api\/operaciones\/([^/?]+)/);
                if (match) {
                    const opId = match[1];
                    data.enemigos = data.enemigos.filter(e => String(e.id) !== String(opId) && String(e.uuid) !== String(opId));
                }
            }

            if (url.includes('/api/operaciones/') && url.includes('/pagar') && method === 'POST') {
                const match = url.match(/\/api\/operaciones\/([^/]+)\/pagar/);
                if (match) {
                    aplicarPagoCuotaOptimista(
                        data,
                        match[1],
                        parseInt(parsedBody.numero_cuota, 10),
                        parseFloat(parsedBody.monto_pagado || 0),
                    );
                }
            }

            if (url.includes('/api/bulk/') && method === 'DELETE') {
                const match = url.match(/\/api\/bulk\/([^/?]+)/);
                if (match) {
                    const lid = match[1];
                    data.bulk = data.bulk.filter(b => String(b.id) !== String(lid));
                }
            }

            if (url.includes('/api/auditoria/') && method === 'DELETE') {
                const match = url.match(/\/api\/auditoria\/([^/?]+)/);
                if (match) {
                    const aid = match[1];
                    data.auditoria = (data.auditoria || []).filter(a => String(a.id) !== String(aid));
                }
            }

            if (url.includes('/api/remitos/') && method === 'DELETE') {
                const match = url.match(/\/api\/remitos\/([^/?]+)/);
                if (match) {
                    const remitoId = match[1];
                    for (const c of data.clientes) {
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

            if (url.includes('/api/pagos/') && method === 'DELETE') {
                const match = url.match(/\/api\/pagos\/([^/?]+)/);
                if (match) {
                    const pagoId = match[1];
                    for (const c of data.clientes) {
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

            if (url.includes('/api/clientes/') && method === 'DELETE') {
                const match = url.match(/\/api\/clientes\/([^/?]+)/);
                if (match) {
                    const clientId = match[1];
                    data.clientes = data.clientes.filter(cli => String(cli.id) !== String(clientId));
                }
            }

            setData(data);
            await persistLocal();
        } catch (err) {
            console.error('Error al aplicar cambio optimista:', err);
        }
    }

    async function deleteWithMasterPassword(opts) {
        const {
            url,
            confirmMessage,
            passwordPrompt,
            successMessage,
            optimisticApply,
            afterOptimistic,
            refresh = false,
            onSynced,
        } = opts;

        if (confirmMessage && !confirm(confirmMessage)) return false;
        const pw = await global.promptMasterPasswordAsync?.(passwordPrompt || 'Contraseña maestra:');
        if (!pw) return false;

        const data = getData();
        if (typeof optimisticApply === 'function') optimisticApply(data);
        setData(data);
        void persistLocal();

        if (typeof afterOptimistic === 'function') afterOptimistic(data);

        const msg = typeof successMessage === 'function'
            ? successMessage({ ok: true })
            : (successMessage || 'Eliminado');
        if (msg) bus().emit('toast', msg);

        void api(url, { method: 'DELETE', headers: deleteHeaders(pw) })
            .then((res) => {
                if (onSynced) onSynced(res);
                if (refresh) {
                    void global.CrmLoader.loadAll({
                        enSegundoPlano: true,
                        bloquearUI: false,
                        avisarSiVacio: false,
                    });
                }
            })
            .catch((e) => {
                bus().emit('toast', e.message || 'No se pudo sincronizar la eliminación', true);
            });

        return true;
    }

    async function eliminarOperacion(opId, alias) {
        return deleteWithMasterPassword({
            url: '/api/operaciones/' + opId,
            confirmMessage: '¿Eliminar ' + (alias || 'esta obligación') + '?',
            passwordPrompt: 'Ingrese la contraseña maestra para eliminar la deuda:',
            successMessage: 'Obligación eliminada',
            optimisticApply: (data) => {
                data.enemigos = data.enemigos.filter(
                    e => String(e.id) !== String(opId) && String(e.uuid) !== String(opId),
                );
            },
            afterOptimistic: () => bus().emit('closeDrawer'),
        });
    }

    async function eliminarAuditoria(auditId) {
        const pw = bus().emit('getAuditDeletePassword');
        if (!pw) {
            bus().emit('toast', 'Debe ingresar la contraseña maestra', true);
            return false;
        }
        if (!confirm('¿Borrar definitivamente este registro de auditoría?')) return false;

        const data = getData();
        data.auditoria = (data.auditoria || []).filter(a => String(a.id) !== String(auditId));
        setData(data);
        void persistLocal();
        bus().emit('toast', 'Registro de auditoría eliminado');
        bus().emit('closeAuditModal');

        void api('/api/auditoria/' + auditId, {
            method: 'DELETE',
            headers: deleteHeaders(pw),
        }).catch((e) => bus().emit('toast', e.message, true));

        return true;
    }

    global.CrmDelete = {
        aplicarCambioOptimista,
        aplicarPagoCuotaOptimista,
        aplicarCobroRemitoOptimista,
        aplicarCobroClienteOptimista,
        deleteWithMasterPassword,
        eliminarOperacion,
        eliminarAuditoria,
    };

    global.eliminarLoteBulk = async function (loteId) {
        const sessionUser = bus().emit('getSessionUser') || {};
        if (sessionUser.role !== 'admin') {
            bus().emit('toast', 'Solo administradores pueden eliminar lotes', true);
            return;
        }
        const data = getData();
        const lote = data.bulk.find(b => b.id === loteId);
        const label = lote ? `Lote #${loteId}` : `lote #${loteId}`;
        await deleteWithMasterPassword({
            url: '/api/bulk/' + loteId,
            confirmMessage: '¿Eliminar ' + label + '? Esta acción no se puede deshacer.',
            passwordPrompt: 'Contraseña maestra para eliminar ' + label + ' (stock mal cargado):',
            successMessage: 'Lote eliminado',
            optimisticApply: (d) => {
                d.bulk = d.bulk.filter(b => String(b.id) !== String(loteId));
            },
        });
    };

    global.eliminarCliente = async function (clientId) {
        await deleteWithMasterPassword({
            url: '/api/clientes/' + clientId,
            passwordPrompt: '⚠️ Se borrarán permanentemente este cliente, TODAS sus facturas/remitos (se devolverán los kilos al stock) y TODOS sus pagos.',
            successMessage: 'Cliente eliminado permanentemente',
            optimisticApply: (d) => {
                d.clientes = d.clientes.filter(c => String(c.id) !== String(clientId));
            },
            afterOptimistic: () => bus().emit('switchView', 'clientes'),
        });
    };

    global.eliminarPagoCliente = async function (pagoId) {
        await deleteWithMasterPassword({
            url: '/api/pagos/' + pagoId,
            passwordPrompt: '⚠️ Se eliminará este pago. La deuda de las facturas cobradas con este pago se restablecerá.',
            successMessage: 'Pago eliminado y deuda restablecida',
            optimisticApply: (d) => {
                if (d.historialPagos) {
                    d.historialPagos = d.historialPagos.filter(p => String(p.id) !== String(pagoId));
                }
                const selectedClienteId = bus().emit('getSelectedClienteId');
                const currentClientData = bus().emit('getCurrentClientData');
                if (currentClientData?.pagos) {
                    const idx = currentClientData.pagos.findIndex(p => String(p.id) === String(pagoId));
                    if (idx !== -1) {
                        const p = currentClientData.pagos[idx];
                        currentClientData.saldo_actual = (currentClientData.saldo_actual || 0) + p.monto;
                        currentClientData.pagos.splice(idx, 1);
                    }
                }
                if (selectedClienteId && d.clientes) {
                    const c = d.clientes.find(cli => String(cli.id) === String(selectedClienteId));
                    if (c?.pagos) {
                        const idx = c.pagos.findIndex(p => String(p.id) === String(pagoId));
                        if (idx !== -1) {
                            const p = c.pagos[idx];
                            c.saldo_actual = (c.saldo_actual || 0) + p.monto;
                            c.pagos.splice(idx, 1);
                        }
                    }
                }
            },
            afterOptimistic: () => {
                const selectedClienteId = bus().emit('getSelectedClienteId');
                if (selectedClienteId) void bus().emit('openClientDrawer', selectedClienteId);
            },
        });
    };

    global.eliminarFactura = async function (remitoId) {
        await deleteWithMasterPassword({
            url: '/api/remitos/' + remitoId,
            passwordPrompt: '⚠️ Se eliminará esta factura permanentemente. Los kilos volverán a estar disponibles en el stock.',
            successMessage: 'Factura eliminada. Stock repuesto.',
            optimisticApply: (d) => {
                const selectedClienteId = bus().emit('getSelectedClienteId');
                const currentClientData = bus().emit('getCurrentClientData');
                if (currentClientData?.remitos) {
                    const idx = currentClientData.remitos.findIndex(r => String(r.id) === String(remitoId));
                    if (idx !== -1) {
                        const r = currentClientData.remitos[idx];
                        currentClientData.saldo_actual = (currentClientData.saldo_actual || 0)
                            - (r.precio_venta_total - (r.monto_pagado || 0));
                        currentClientData.remitos.splice(idx, 1);
                    }
                }
                if (selectedClienteId && d.clientes) {
                    const c = d.clientes.find(cli => String(cli.id) === String(selectedClienteId));
                    if (c?.remitos) {
                        const idx = c.remitos.findIndex(r => String(r.id) === String(remitoId));
                        if (idx !== -1) {
                            const r = c.remitos[idx];
                            c.saldo_actual = (c.saldo_actual || 0) - (r.precio_venta_total - (r.monto_pagado || 0));
                            c.remitos.splice(idx, 1);
                        }
                    }
                }
            },
            afterOptimistic: () => {
                bus().emit('invalidateRemitosCache');
                bus().emit('cerrarModalFacturaOriginal');
            },
        });
    };

    global.eliminarFacturasSeleccionadas = async function () {
        const ids = global.wspSelectedInvoices || [];
        if (!ids.length) return;
        const pass = await global.promptMasterPasswordAsync('Eliminar ' + ids.length + ' factura(s)');
        if (!pass) return;

        const data = getData();
        const selectedClienteId = bus().emit('getSelectedClienteId');
        const idSet = new Set(ids.map(String));
        for (const c of data.clientes) {
            if (!c.remitos) continue;
            for (const r of c.remitos) {
                if (!idSet.has(String(r.id))) continue;
                c.saldo_actual = (c.saldo_actual || 0) - (r.precio_venta_total - (r.monto_pagado || 0));
            }
            c.remitos = c.remitos.filter(r => !idSet.has(String(r.id)));
        }
        setData(data);
        void persistLocal();
        bus().emit('toast', 'Facturas eliminadas');
        bus().emit('invalidateRemitosCache');

        void (async () => {
            try {
                const resultados = await Promise.allSettled(
                    ids.map(rid => api('/api/remitos/' + rid, {
                        method: 'DELETE',
                        headers: deleteHeaders(pass),
                    }))
                );
                const fallos = resultados.filter(r => r.status === 'rejected');
                if (fallos.length) {
                    bus().emit('toast', `${fallos.length} factura(s) no se pudieron eliminar del servidor`, true);
                }
                if (selectedClienteId) await bus().emit('openClientDrawer', selectedClienteId);
            } catch (e) {
                bus().emit('toast', e.message, true);
            }
        })();
    };
})(window);
