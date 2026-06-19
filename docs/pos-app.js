const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString('es-AR', { minimumFractionDigits: 2 });
const BASE = (window.CRM_CONFIG && window.CRM_CONFIG.base) || '';

function setNetBadge() {
    const on = navigator.onLine;
    $('netBadge').textContent = on ? 'En linea' : 'Sin red';
    $('netBadge').className = 'badge ' + (on ? 'on' : 'off');
}

async function renderVentas() {
    const ventas = await PosOffline.listarVentasLocales();
    const pend = await PosOffline.contarPendientesSync();
    const pill = $('syncPill');
    pill.style.display = pend > 0 ? 'block' : 'none';
    if (pend > 0) pill.textContent = pend + ' venta(s) pendiente(s) de subir';
    $('listaVentas').innerHTML = ventas.length
        ? ventas.map((v) => `<li><span>${v.producto}<br><small>${v.tipo_pago}</small></span><strong>$${fmt(v.monto)}</strong></li>`).join('')
        : '<li><small>Sin ventas</small></li>';
}

$('btnGuardar').addEventListener('click', async () => {
    const producto = $('producto').value.trim();
    const monto = $('monto').value;
    if (!producto || !monto || Number(monto) <= 0) return alert('Producto y monto obligatorios');
    await PosOffline.guardarVentaOffline({ producto, monto, tipo_pago: $('tipo_pago').value });
    $('producto').value = '';
    $('monto').value = '';
    await renderVentas();
});

$('btnSync').addEventListener('click', async () => {
    try {
        const r = await PosOffline.sincronizarVentas();
        if (r.reason === 'offline') return alert('Sin conexion.');
        if (!(window.CRM_CONFIG && window.CRM_CONFIG.apiBase)) return alert('Configura apiBase en config.js');
        alert('Sincronizado: ' + (r.synced || 0));
        await renderVentas();
    } catch (e) { alert(e.message); }
});

window.addEventListener('online', async () => { setNetBadge(); try { await PosOffline.sincronizarVentas(); await renderVentas(); } catch (_) {} });
window.addEventListener('offline', setNetBadge);
setNetBadge();
renderVentas();
if ('serviceWorker' in navigator) navigator.serviceWorker.register(BASE + '/sw.js', { scope: BASE + '/' });
