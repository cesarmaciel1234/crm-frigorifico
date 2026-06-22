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
        db.version(2).stores({
            transacciones: '++id, uuid, tipo, monto, fecha, status, updated_at',
            cache: 'key, updated_at'
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
                console.log("Guardado localmente:", registro.uuid);
                await intentarSincronizar();
                return true;
            } catch (e) {
                console.error("Error al guardar localmente:", e);
                return false;
            }
        }

        async function intentarSincronizar() {
            if (!navigator.onLine) {
                console.log("Sin conexión. Los datos están seguros en el dispositivo.");
                return;
            }
            const pendientes = await db.transacciones.where('status').equals(0).toArray();
            let syncCount = 0;
            for (const item of pendientes) {
                try {
                    let responseOk = false;
                    
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
                    }

                    if (responseOk) {
                        await db.transacciones.update(item.id, { status: 1 });
                        console.log("Sincronizado con éxito:", item.uuid);
                        syncCount++;
                    }
                } catch (error) {
                    console.warn("Error de conexión al sincronizar:", error);
                    break;
                }
            }
            if (syncCount > 0) {
                toast(`Sincronizados ${syncCount} registros pendientes`);
                loadAll();
            }
        }

        window.addEventListener('online', intentarSincronizar);
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

        let data = { enemigos: [], remitos: [], estrategia: {}, bancos: [], historial: [], historialPagos: [], bulk: [], clientes: [], auditoria: [] };
        let isProMode = true;
        let selectedDeuda = null;
        let selectedAuditId = null;
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
            'cliente-detalle': ['Perfil de Cliente', 'Detalle corporativo y facturación', 'Perfil'],
            'nueva-venta': ['Registrar Venta', 'Emitir remito o factura a cuenta corriente', 'Ventas']
        };

        function setLoading(on) {
            $('appLoader')?.classList.toggle('active', !!on);
        }

        function toast(msg, err) {
            const t = $('toast');
            t.textContent = msg;
            t.className = 'toast show' + (err ? ' error' : '');
            clearTimeout(toast._t);
            toast._t = setTimeout(() => t.classList.remove('show'), 3500);
        }

        async function api(url, opts) {
            opts = opts || {};
            opts.credentials = 'same-origin';
            opts.headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
            const r = await (window.CrmSafe?.apiFetch || fetch)(url, opts);
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.error || 'Error en la operación');
            return d;
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
                if ($('barSangriaValue')) $('barSangriaValue').textContent = '$' + fmt(mf.sangre);
                if ($('barSangriaSub')) $('barSangriaSub').textContent = `Int: $${fmt(mf.int_diario)}`;
                
                if ($('barDeudaValue')) $('barDeudaValue').textContent = '$' + fmt(mf.deuda);
                if ($('barDeudaSub')) $('barDeudaSub').textContent = `Int: $${fmt(mf.int_acumulado || 0)}`;
                
                const absCapital = Math.abs(mf.capital);
                const signStr = mf.capital < 0 ? '-' : (mf.capital > 0 ? '+' : '');
                const isNeg = mf.capital < 0;
                const isZero = mf.capital === 0;
                if ($('barCapitalValue')) {
                    $('barCapitalValue').textContent = signStr + '$' + fmt(absCapital);
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
                return `<tr>
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.cliente || '—')}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">#${r.id} · ${r.fecha.slice(5)}</div>
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div style="color:var(--text-muted)">Kg: <span style="color:#111827;font-weight:500">${fmt(r.kg)}</span></div>
                        <div style="color:var(--text-muted)">Log: <span style="color:#111827;font-weight:500">$${fmtCompact(r.costo_total_logistica)}</span></div>
                    </td>
                    <td>
                        <div class="home-amount" style="font-size:13px">$${fmtCompact(r.precio_venta_total)}</div>
                        <div class="home-subtext" style="font-size:9px;margin-top:2px;color:var(--success)">Mrg: $${fmtCompact(r.margen)}</div>
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
                const ventaText = '$' + fmtCompact(r.precio_venta_total);
                const margenText = '$' + fmtCompact(r.margen);

                return `<tr>
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.cliente || '—')}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">#${r.id} · ${r.fecha.slice(5)}</div>
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div style="color:var(--text-muted)">Kg: <span style="color:#111827;font-weight:500">${fmt(r.kg)}</span></div>
                        <div style="color:var(--text-muted)">Log: <span style="color:#111827;font-weight:500">${logisticaText}</span></div>
                    </td>
                    <td>
                        <div class="home-amount" style="font-size:13px">${ventaText}</div>
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
            $('tblAuditoria').innerHTML = arr.length ? arr.map(a => `
                <tr>
                    <td>${fmtFecha(a.fecha, true)}</td>
                    <td><strong>${esc(a.alias || 'Registro')}</strong><br><small style="color:var(--text-light)">Op ID: ${a.operacion_id}</small></td>
                    <td><span class="badge ${a.accion === 'ELIMINADO' ? 'badge-danger' : 'badge-success'}">${esc(a.accion)}</span></td>
                    <td class="money">$${fmt(a.monto)}</td>
                    <td><button class="btn btn-ghost btn-sm" onclick="promptDeleteAuditoria(${a.id})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button></td>
                </tr>
            `).join('') : '<tr><td colspan="5" class="empty-state">No hay registros de auditoría</td></tr>';
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
            table.innerHTML = data.bulk && data.bulk.length ? data.bulk.map(b => {
                const badge = b.activo 
                    ? '<span class="badge badge-success">Activo</span>' 
                    : '<span class="badge badge-neutral">Agotado</span>';
                return `<tr>
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Lote #${b.id}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">${b.fecha}</div>
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
            }).join('') : '<tr><td colspan="4" style="color:var(--text-muted);padding:16px;text-align:center">Sin lotes de compra bulk registrados</td></tr>';
        }

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
            
            const renderRow = c => {
                const statusBadge = c.limite_superado 
                    ? '<span class="badge badge-danger">Superado</span>' 
                    : '<span class="badge badge-success">Límite OK</span>';
                const style = c.limite_superado ? 'color: var(--danger)' : '';
                const techoText = '$' + fmtCompact(c.techo_deuda);
                const saldoText = '$' + fmtCompact(c.saldo_actual);

                return `<tr class="clickable" data-id="${c.id}">
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;${style}">${esc(c.nombre)}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">Alta: ${(c.created_at || '').slice(0, 10)}</div>
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div class="home-amount" style="font-size:13px">${saldoText}</div>
                        <div style="color:var(--text-muted);font-size:9px;margin-top:2px">Techo: <span style="color:#111827">${techoText}</span></div>
                    </td>
                    <td style="text-align:center"><span class="badge badge-neutral" style="text-align:center; display:inline-block; width:20px;">${c.scoring}</span></td>
                    <td style="text-align:center">${statusBadge}</td>
                </tr>`;
            };

            table.innerHTML = activos.length ? activos.map(renderRow).join('') : '<tr><td colspan="4" style="color:var(--text-muted);padding:16px;text-align:center">Sin clientes activos</td></tr>';
            
            if (tableInrec) {
                tableInrec.innerHTML = inrecuperables.length ? inrecuperables.map(renderRow).join('') : '<tr><td colspan="4" style="color:var(--text-muted);padding:16px;text-align:center">No hay clientes inrecuperables</td></tr>';
            }
            
            document.querySelectorAll('#tblClientes tr.clickable, #tblInrecuperables tr.clickable').forEach((row, idx) => {
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

        function cobranzaCardHtml(c) {
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
                <button type="button" class="btn btn-primary btn-cobranza-accion cobranza-card-btn" data-cid="${c.id}">Cobrar / Ver</button>
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

            if ($('cobBarClientesMora')) $('cobBarClientesMora').textContent = enMora.length;
            if ($('cobBarClientesMoraSub')) $('cobBarClientesMoraSub').textContent = enMora.length === 1 ? 'Cliente con facturas vencidas' : 'Clientes con facturas vencidas';
            if ($('cobBarMontoMora')) $('cobBarMontoMora').textContent = '$' + fmt(montoMora);
            if ($('cobBarCapitalTotal')) $('cobBarCapitalTotal').textContent = '$' + fmt(totalDeuda);
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
            if ($('pcBarObligaciones')) $('pcBarObligaciones').textContent = pendientes.length;
            if ($('pcBarMontoVencido')) $('pcBarMontoVencido').textContent = '$' + fmt(montoVencido);
            if ($('pcBarTotalPagar')) $('pcBarTotalPagar').textContent = '$' + fmt(totalPagar);
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
            $('viewClientBody').innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px">Cargando detalles corporativos...</div>';
            
            try {
                const c = await api('/api/clientes/' + clientId);
                if (selectedClienteId !== clientId) return;
                
                currentClientData = c;
                currentClientFilter = 'all';
                const titleEl = $('viewClientTitle');
                if (titleEl) titleEl.textContent = c.nombre;
                
                renderClientDashboard();
                
                if (c.saldo_actual > 0 && c.scoring !== 'D') {
                    $('btnClientIncobrable').style.display = 'inline-block';
                } else {
                    $('btnClientIncobrable').style.display = 'none';
                }
                
            } catch (e) {
                $('viewClientBody').innerHTML = `<div style="color:var(--danger);text-align:center;padding:20px">Error al cargar detalles: ${esc(e.message)}</div>`;
            }
        }
        window.openClientDrawer = openClientDrawer;

        function volverDesdeClienteDetalle() {
            switchView(clientDetailReturnView || 'clientes');
        }
        window.volverDesdeClienteDetalle = volverDesdeClienteDetalle;

        function renderClientDashboard() {
            if (!currentClientData) return;
            const c = currentClientData;
            
            const limitStatus = c.limite_superado 
                ? '<span style="color:var(--danger)">Bloqueado (Límite superado)</span>' 
                : '<span style="color:var(--success)">Activo y Operativo</span>';
                
            let filteredRemitos = c.remitos || [];
            if (currentClientFilter === 'pending') {
                filteredRemitos = filteredRemitos.filter(r => (r.pagado ?? r.estado_cobro) !== 'cobrado' && (r.pagado ?? r.estado_cobro) !== 1 && (r.pagado ?? r.estado_cobro) !== 2);
            } else if (currentClientFilter === 'paid') {
                filteredRemitos = filteredRemitos.filter(r => (r.pagado ?? r.estado_cobro) === 'cobrado' || (r.pagado ?? r.estado_cobro) === 1 || (r.pagado ?? r.estado_cobro) === 2);
            }
            
            let remitosHtml = '';
            if (filteredRemitos.length > 0) {
                remitosHtml = `
                <div class="table-wrap" style="max-height: 40vh; overflow-y: auto;">
                    <table class="data-table" style="font-size:0.85rem; width:100%;">
                        <thead>
                            <tr style="background:var(--bg);position:sticky;top:0;z-index:10;">
                                <th style="text-align:left;padding:8px;">Remito</th>
                                <th style="text-align:left;padding:8px;">Detalle</th>
                                <th style="text-align:right;padding:8px;">Total</th>
                                <th style="text-align:right;padding:8px;">Pagado</th>
                                <th style="text-align:right;padding:8px;">Saldo</th>
                                <th style="text-align:center;padding:8px;">Estado</th>
                                <th style="text-align:center;padding:8px;">Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${filteredRemitos.map(r => {
                                const est = remitoEstado(r.estado_cobro ?? r.pagado);
                                const pagadoAmnt = Number(r.monto_pagado || 0);
                                const saldoAmnt = remitoSaldoPendiente(r);
                                const btn = est.cobrable
                                    ? `<button class="btn btn-ghost btn-sm btn-cobrar-remito-drawer" data-rid="${r.id}" style="color:var(--brand); border:1px solid var(--brand); padding:2px 8px; font-size:10px; border-radius:6px; cursor:pointer;">Registrar Pago</button>` 
                                    : '';
                                    
                                return `<tr style="border-bottom:1px solid #e5e7eb;">
                                    <td style="padding:8px;">
                                        <div style="font-weight:600;color:#111827;">#${r.id}</div>
                                        <div style="font-size:10px;color:#6b7280;margin-top:2px;">${r.fecha}</div>
                                    </td>
                                    <td style="padding:8px;">
                                        <div style="font-size:12px;color:#111827;font-weight:600;">${r.tipo_corte || '-'}</div>
                                        <div style="font-size:10px;color:#6b7280;margin-top:2px;">${remitoCantidad(r)} u · ${fmt(r.kg)} kg${remitoPesosPiezas(r).length ? ' · ' + remitoPesosPiezas(r).map(p => fmt(p)).join(' ') : ''}</div>
                                    </td>
                                    <td style="text-align:right;padding:8px;font-weight:600;color:#111827;">$${fmt(r.precio_venta_total)}</td>
                                    <td style="text-align:right;padding:8px;color:#10b981;">$${fmt(pagadoAmnt)}</td>
                                    <td style="text-align:right;padding:8px;color:${saldoAmnt > 0 ? '#ef4444' : '#111827'};font-weight:bold;">$${fmt(saldoAmnt)}</td>
                                    <td style="text-align:center;padding:8px;">
                                        <span class="badge ${est.badgeClass}" style="padding:4px 8px;font-size:10px;">${est.label}</span>
                                    </td>
                                    <td style="text-align:center;padding:8px;">${btn}</td>
                                </tr>`;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
                `;
            } else {
                remitosHtml = `<div style="color:#6b7280;text-align:center;padding:30px;font-size:0.9rem;background:#f9fafb;border-radius:12px;">No se encontraron facturas/remitos para este filtro.</div>`;
            }
            
            const btnStyle = (filter) => currentClientFilter === filter 
                ? 'background:var(--brand); color:white; border-color:var(--brand); padding:6px 12px; border-radius:20px; font-size:11px; font-weight:bold; cursor:pointer; border:1px solid transparent;'
                : 'background:transparent; color:var(--text-muted); border-color:var(--border); padding:6px 12px; border-radius:20px; font-size:11px; cursor:pointer; border:1px solid var(--border);';
                
            $('viewClientBody').innerHTML = `
                <div style="display:flex; flex-direction:column; gap:20px; font-family:'Segoe UI',sans-serif;">
                    <!-- Top KPI cards -->
                    <div style="display:flex; gap:15px; flex-wrap:wrap;">
                        <div style="flex:1; min-width:200px; padding:20px; border-radius:16px; background:#ffffff; border:1px solid #e5e7eb; box-shadow:0 4px 6px -1px rgba(0, 0, 0, 0.05);">
                            <div style="font-size:11px; color:#6b7280; font-weight:800; letter-spacing:0.5px;">DEUDA TOTAL ACUMULADA</div>
                            <div style="font-size:32px; font-weight:900; color:${c.saldo_actual > 0 ? '#ef4444' : '#10b981'}; margin-top:8px;">$${fmt(c.saldo_actual)}</div>
                            <div style="font-size:12px; color:#6b7280; margin-top:8px;">Límite Autorizado: <span style="font-weight:bold;color:#111827;">$${fmt(c.techo_deuda)}</span></div>
                        </div>
                        <div style="flex:1; min-width:200px; padding:20px; border-radius:16px; background:#ffffff; border:1px solid #e5e7eb; box-shadow:0 4px 6px -1px rgba(0, 0, 0, 0.05);">
                            <div style="font-size:11px; color:#6b7280; font-weight:800; letter-spacing:0.5px;">ESTADO DEL CRÉDITO</div>
                            <div style="font-size:18px; font-weight:bold; margin-top:12px;">${limitStatus}</div>
                            <div style="display:flex; gap:10px; margin-top:16px;">
                                <div style="background:#f3f4f6; padding:4px 10px; border-radius:6px; font-size:11px; font-weight:bold; color:#111827;">Scoring: ${c.scoring}</div>
                                <div style="background:#f3f4f6; padding:4px 10px; border-radius:6px; font-size:11px; color:#6b7280;">Alta: ${(c.created_at || '').slice(0, 10)}</div>
                            </div>
                        </div>
                    </div>

                    <!-- History and Filters -->
                    <div style="background:#ffffff; border:1px solid #e5e7eb; border-radius:16px; overflow:hidden;">
                        <div style="padding:15px 20px; border-bottom:1px solid #e5e7eb; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; background:#f9fafb;">
                            <h4 style="margin:0; font-size:14px; font-weight:800; color:#111827;">Historial de Facturación</h4>
                            <div style="display:flex; gap:8px;">
                                <button class="btn-filter-drawer" data-filter="all" style="${btnStyle('all')}">Todas</button>
                                <button class="btn-filter-drawer" data-filter="pending" style="${btnStyle('pending')}">Impagas / Parciales</button>
                                <button class="btn-filter-drawer" data-filter="paid" style="${btnStyle('paid')}">Pagadas</button>
                            </div>
                        </div>
                        <div style="padding:0;">
                            ${remitosHtml}
                        </div>
                    </div>
                </div>
            `;
            
            // Re-bind filter events
            $('viewClientBody').querySelectorAll('.btn-filter-drawer').forEach(btn => {
                btn.addEventListener('click', () => {
                    currentClientFilter = btn.dataset.filter;
                    renderClientDashboard();
                });
            });
            
            // Re-bind action events
            $('viewClientBody').querySelectorAll('.btn-cobrar-remito-drawer').forEach(btn => {
                btn.addEventListener('click', ev => {
                    ev.stopPropagation();
                    const rid = parseInt(btn.dataset.rid, 10);
                    const remito = currentClientData?.remitos?.find(r => r.id === rid);
                    if (remito) abrirModalPagoRemito(remito);
                });
            });
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

        async function loadAll() {
            setLoading(true);
            try {
                // 1. LAZY LOAD: Mostrar caché local instantáneamente
                const cached = await db.cache.get('appData');
                if (cached && cached.data) {
                    data = cached.data;
                    renderAll();
                    setLoading(false); // Ocultar loader rápido si hay caché
                }

                // 2. SYNC: Actualizar desde el servidor en segundo plano
                const [dash, pagos, bulk, clientes, auditoriaData] = await Promise.all([
                    api('/api/dashboard'),
                    api('/api/historial-pagos'),
                    api('/api/bulk'),
                    api('/api/clientes'),
                    api('/api/auditoria')
                ]);
                const freshData = { ...dash, historialPagos: pagos, bulk: bulk, clientes: clientes, auditoria: auditoriaData };
                
                data = freshData;
                await db.cache.put({ key: 'appData', data: data, updated_at: Date.now() });
                renderAll(); // Re-renderizar con los datos frescos
            } catch (e) {
                console.warn("Error al cargar del servidor. Mostrando datos locales (offline).", e);
            } finally {
                setLoading(false);
            }
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
                if ($('btnTogglePro')) $('btnTogglePro').style.display = '';
                renderHomeTable();
            } else {
                if ($('btnTogglePro')) $('btnTogglePro').style.display = 'none';
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
            if (name === 'registro') volverMenuRegistro();
            $('sidebar').classList.remove('open');
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
            toast('Panel actualizado');
        });

        $('menuToggle').addEventListener('click', () => $('sidebar').classList.toggle('open'));
        if($('sidebarCloseMobile')) $('sidebarCloseMobile').addEventListener('click', () => $('sidebar').classList.remove('open'));
        document.addEventListener('click', ev => {
            const sidebar = $('sidebar');
            const toggle = $('menuToggle');
            if (sidebar && toggle && sidebar.classList.contains('open')) {
                if (!sidebar.contains(ev.target) && !toggle.contains(ev.target)) {
                    sidebar.classList.remove('open');
                }
            }
        });
        $('drawerClose').addEventListener('click', closeDrawer);
        $('drawerOverlay').addEventListener('click', ev => { if (ev.target === $('drawerOverlay')) closeDrawer(); });

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

        $('drawerPagar').addEventListener('click', abrirModalPago);
        $('drawerDelete').addEventListener('click', async () => {
            if (!selectedDeuda) return;
            const pw = prompt('Ingrese la contraseña para eliminar la deuda:');
            if (pw !== '2094') {
                toast('Contraseña incorrecta', true);
                return;
            }
            if (!confirm('¿Eliminar ' + selectedDeuda.alias + '?')) return;
            await api('/api/operaciones/' + selectedDeuda.id, { method: 'DELETE' });
            toast('Obligación eliminada');
            closeDrawer();
            await loadAll();
        });

        $('btnConfirmDeleteAuditoria')?.addEventListener('click', async () => {
            if (!selectedAuditId) return;
            const pw = prompt('Ingrese la contraseña para borrar el registro del historial:');
            if (pw !== '2094') {
                toast('Contraseña incorrecta', true);
                return;
            }
            if (!confirm('¿Borrar definitivamente este registro de auditoría?')) return;
            await api('/api/auditoria/' + selectedAuditId, { method: 'DELETE' });
            toast('Registro eliminado');
            $('modalConfirmDeleteAuditoria').classList.remove('show');
            await loadAll();
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
        $('formEmpresa')?.addEventListener('submit', ev => {
            ev.preventDefault();
            const data = {
                nombre: $('inpEmpresaNombre').value.trim() || "Master Total",
                cuit: $('inpEmpresaCuit').value.trim(),
                direccion: $('inpEmpresaDireccion').value.trim(),
                telefono: $('inpEmpresaTelefono').value.trim(),
                email: $('inpEmpresaEmail').value.trim()
            };
            localStorage.setItem('empresa_datos', JSON.stringify(data));
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
            try {
                await api('/api/clientes', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        nombre: fd.get('nombre'),
                        techo_deuda: fd.get('techo_deuda'),
                        scoring: fd.get('scoring')
                    })
                });
                toast('Cliente registrado con éxito');
                ev.target.reset();
                await loadAll();
                abrirSubVistaClientes('cliVer');
            } catch (e) { toast(e.message, true); }
        });
        
        $('btnClientIncobrable').addEventListener('click', async () => {
            if (!currentClientData) return;
            if (!confirm('⚠️ ¿ESTÁ SEGURO DE DECLARAR ESTE CLIENTE COMO INCOBRABLE?\nSu deuda de saldo pendiente se pasará a pérdidas y el crédito se bloqueará a $0 para siempre.')) return;
            try {
                await api('/api/clientes/' + currentClientData.id + '/incobrable', { method: 'POST' });
                toast('Cliente declarado como Incobrable');
                await loadAll();
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
                email: "contacto@mastertotal.com"
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
            $('inpEmpresaNombre').value = data.nombre;
            $('inpEmpresaCuit').value = data.cuit;
            $('inpEmpresaDireccion').value = data.direccion;
            $('inpEmpresaTelefono').value = data.telefono;
            $('inpEmpresaEmail').value = data.email;
            $('modalEmpresa').classList.add('open');
        };

        window.cerrarModalEmpresa = function() {
            $('modalEmpresa').classList.remove('open');
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
                    const detalleHtml = piezas.length
                        ? `<div class="detalle-cell"><span class="corte-nombre">${esc(corte)}</span>${pesosPiezasHtml(piezas)}</div>`
                        : `<div class="detalle-cell"><span class="corte-nombre">${esc(corte)}</span><span class="sin-piezas">${fmt(r.kg)} kg total</span></div>`;
                    return `
                        <tr>
                            <td>${fmtFechaRemito(r.fecha)}</td>
                            <td class="col-mono">${String(r.id).padStart(3, '0')}</td>
                            <td class="col-qty">${cant}</td>
                            <td class="col-detalle">${detalleHtml}</td>
                            <td class="col-num">${Number(r.kg).toFixed(2)}</td>
                            <td class="col-num">${Number(pxKg).toFixed(2)}</td>
                            <td class="col-num col-total">$${fmt(importe)}</td>
                            <td class="col-num">$${fmt(pagado)}</td>
                            <td class="col-num col-saldo">$${fmt(saldo)}</td>
                            <td class="col-plazo">${plazo}</td>
                            <td class="col-estado"><span class="status ${reporteEstadoClass(est.label)}">${esc(est.label.toUpperCase())}</span></td>
                        </tr>`;
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
            const pill = $('statusPill');
            if (pill) {
                if (navigator.onLine) {
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
        }
        window.addEventListener('online', updateConnectionStatus);
        window.addEventListener('offline', updateConnectionStatus);
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
        });

        // Register Service Worker
        if ("serviceWorker" in navigator) {
            navigator.serviceWorker.register("/sw.js").catch(e => console.warn("PWA SW err:", e));
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

        loadAll();
        switchView('home');
        setInterval(loadAll, 60000);