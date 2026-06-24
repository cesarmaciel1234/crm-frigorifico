/**
 * Export/import de backups y estado de nube.
 */
(function (global) {
    const bus = () => global.CrmBus;
    const $ = (id) => document.getElementById(id);
    const esc = (s) => (global.CrmSafe && global.CrmSafe.esc(s)) || String(s ?? '');
    const { toast, setLoading } = global.CrmUi;
    const api = (...a) => global.CrmApi.api(...a);
    const { tenantCacheGet, tenantCacheGetEntry, tenantCachePut } = global.CrmDb;
    const db = global.CrmDb.db;
    const loadAll = (opts) => bus().emit('loadAll', opts);
    const getData = () => bus().emit('getData');
    const setData = (d) => bus().emit('setData', d);
    const renderAll = () => bus().emit('renderAll');
    const syncEmpresaFromServer = () => bus().emit('syncEmpresaFromServer');
    const setSidebarOpen = (open) => bus().emit('setSidebarOpen', open);

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
    const fromApp = convertirAppDataABackup(appCache || getData());
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
    const appData = appEntry?.data || getData() || null;
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
                        setData(nodo.snapshot);
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
    function initBackupUi() {
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
        $('btnDescargarNube')?.addEventListener('click', () => {
            void global.CrmLoader.descargarDatosDeLaNube()
                .then(() => cerrarModalBackup())
                .catch((e) => toast('Error al descargar: ' + (e?.message || ''), true));
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
                appData: appCache || getData() || null,
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
    }

    global.CrmBackup = {
        descargarArchivoJson,
        enemigoAOperacion,
        empresaDatosParaBackup,
        convertirAppDataABackup,
        mergeAppDataEnBackup,
        prepararBackupParaSubida,
        resumirBackup,
        apiImportBackup,
        normalizarBackupParaImport,
        backupTieneDatos,
        obtenerPayloadBackup,
        guardarCacheDispositivo,
        leerArchivoJson,
        subirBackupAlServidor,
        cerrarModalBackup,
        actualizarNodosBackup,
        actualizarEstadoNubeBackup,
        abrirModalBackup,
        initBackupUi,
    };
})(window);
