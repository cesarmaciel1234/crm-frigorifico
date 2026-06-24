"""Genera crm-backup.js desde app.js."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8").splitlines()
body = "\n".join(lines[2110:2536])
dedented = "\n".join(ln[8:] if ln.startswith("        ") else ln for ln in body.splitlines())
dedented = dedented.replace("appCache || data", "appCache || getData()")
dedented = dedented.replace("appEntry?.data || data", "appEntry?.data || getData()")
dedented = dedented.replace(
    "convertirAppDataABackup(appCache || data)",
    "convertirAppDataABackup(appCache || getData())",
)
dedented = dedented.replace("data = nodo.snapshot", "setData(nodo.snapshot)")
dedented = dedented.replace("                cliente: r.cliente || '',\n", "")

header = """/**
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

"""

footer = r"""
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
"""

(ROOT / "app/static/js/crm-backup.js").write_text(header + dedented + footer, encoding="utf-8")
print("ok", len(header + dedented + footer))
