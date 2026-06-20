const $ = id => document.getElementById(id);

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
                intentarSincronizar();
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
                        const response = await fetch('/api/operaciones', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
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
        const fmtCompact = n => {
            if (n >= 1000000) return (n / 1000000).toLocaleString('es-AR', { minimumFractionDigits: 0, maximumFractionDigits: 1 }) + 'M';
            return fmt(n);
        };
        const fmtPct = n => n != null ? n.toFixed(2) + '%' : '—';

        function remitoEstado(pagado) {
            if (typeof pagado === 'string') {
                const s = pagado.toLowerCase();
                if (s === 'cobrado') return { label: 'Cobrado', badgeClass: 'badge-success', cobrable: false };
                if (s === 'incobrable') return { label: 'Incobrable', badgeClass: 'badge-neutral', cobrable: false };
                return { label: 'Pendiente', badgeClass: 'badge-danger', cobrable: true };
            }
            const p = Number(pagado ?? 0);
            if (p === 1) return { label: 'Cobrado', badgeClass: 'badge-success', cobrable: false };
            if (p === 2) return { label: 'Incobrable', badgeClass: 'badge-neutral', cobrable: false };
            return { label: 'Pendiente', badgeClass: 'badge-danger', cobrable: true };
        }

        let data = { enemigos: [], remitos: [], estrategia: {}, bancos: [], historial: [], historialPagos: [], bulk: [], clientes: [], auditoria: [] };
        let selectedDeuda = null;
        let selectedAuditId = null;
        let histPagosFiltro = '';
        let activeRowIndex = -1;

        const titles = {
            dashboard: ['Dashboard ejecutivo', 'Visión consolidada del negocio', 'Executive Overview'],
            deudas: ['Prioridad de pagos', 'Obligaciones ordenadas por impacto financiero', 'Obligaciones'],
            remitos: ['Remitos de venta', 'Historial de ventas y márgenes', 'Ventas'],
            clientes: ['CRM de Clientes', 'Gestión de cuentas corrientes y créditos', 'Clientes'],
            registro: ['Nuevo registro', 'Alta de deudas, remitos y entidades', 'Registro'],
            'historial-pagos': ['Historial de pagos', 'Ledger de movimientos por cuota', 'Pagos'],
            auditoria: ['Auditoría', 'Historial completo de acciones', 'Historial']
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
            const r = await fetch(url, opts);
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
            $('hintPrestamo').classList.toggle('field-hidden', !esPrestamo);
            $('fldPlazoDias').classList.toggle('field-hidden', !(esProveedor || esPrestamo));
            $('fldVencimiento').classList.toggle('field-hidden', !(esTarjeta || esCheque));
            $('fldRecibido').classList.toggle('field-hidden', esCheque || esProveedor || esPrestamo);
            $('fldPagar').classList.toggle('field-hidden', esCheque || esPrestamo);
            $('fldMeses').classList.toggle('field-hidden', esTarjeta || esCheque || esProveedor || esPrestamo);

            if (!esCheque) {
                $('lblRecibido').textContent = esTarjeta ? 'Resumen / consumo ($)' : 'Monto recibido ($)';
                $('lblPagar').textContent = esTarjeta ? 'Total a pagar ($)' : (esProveedor ? 'Total a pagar ($) — opcional si hay interés' : 'Monto a devolver ($)');
            }

            form.monto.required = esCheque;
            form.recibido.required = !esCheque && !esProveedor && !esPrestamo;
            form.pagar.required = !esCheque && !esTarjeta && !esProveedor && !esPrestamo;
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
                        <span class="hist-alias">${e.alias}</span>
                        ${badgeVencimiento(e)}
                    </div>
                    <div class="hist-pagar">${sub}</div>
                    <div class="hist-msg ${histMsgClass(e)}">${e.mensaje_vencimiento || ''} · vto ${e.fecha_vencimiento}</div>
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
            return `<div class="venc-banner ${cls}">${e.mensaje_vencimiento}<br><span style="font-size:0.8rem;font-weight:500">Total a pagar: $${fmt(e.total_pagar)} · Vto ${e.fecha_vencimiento}</span></div>`;
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
                const isPos = mf.capital >= 0;
                const signStr = isPos ? '+' : '-';
                const signClass = isPos ? 'sign-pos' : 'sign-neg';
                const signSpan = `<span class="${signClass}">${signStr}</span>`;
                if ($('barCapitalValue')) $('barCapitalValue').innerHTML = '$' + signSpan + fmt(absCapital);
                if ($('barCapitalTrend')) {
                    $('barCapitalTrend').textContent = mf.tendencia === 'up' ? '▲' : '▼';
                    $('barCapitalTrend').className = 'trend ' + mf.tendencia;
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
                    <label>${e.alias}</label>
                    <div class="chart-track"><div class="chart-fill ${cls}" style="width:${pct}%">${fmtPct(e.cfr)}</div></div>
                    <span style="font-size:0.75rem;color:var(--text-muted)">${e.tipo}</span>
                </div>`;
            }).join('');
        }

        function renderHomeTable() {
            if (!data.enemigos || !data.enemigos.length) {
                $('tblHome').innerHTML = '<tr><td colspan="8"><div class="empty-state">No hay obligaciones cargadas</div></td></tr>';
                return;
            }
            
            const sorted = [...data.enemigos].sort((a, b) => {
                const getScore = e => {
                    if (e.vencido) return -1000 - (e.dias_retraso || 0);
                    if (e.estado_vencimiento === 'hoy') return 0;
                    if (e.estado_vencimiento === 'proximo') return e.dias_faltantes;
                    if (e.fecha_vencimiento) return 1000 + e.dias_faltantes;
                    return 10000;
                };
                return getScore(a) - getScore(b);
            });

            $('tblHome').innerHTML = sorted.map(e => {
                const rowCls = e.vencido ? ' class="highlight-red clickable"' : ' class="clickable"';
                let tipoClase = (e.tipo || 'neutro').toLowerCase();
                if (!['tarjeta','banco','proveedor','cheque'].includes(tipoClase)) tipoClase = 'neutro';
                
                const cfrText = e.sin_interes ? '0%' : fmtPct(e.cfr);
                const intText = e.sin_interes ? '$0' : '$' + fmtCompact(e.interes);
                const capText = '$' + fmtCompact(e.recibido);

                return `<tr${rowCls} data-id="${e.id}">
                    <td>
                        <div class="home-contraparte">${e.alias}</div>
                        <div style="margin-top:4px"><span class="badge-home ${tipoClase}">${e.tipo}</span></div>
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

        function renderRemitosDash() {
            const rows = data.remitos.slice(0, 8);
            $('tblRemitosDash').innerHTML = rows.length ? rows.map(r => {
                const badge = r.pagado || (r.estado_cobro === 'cobrado') 
                    ? '<div style="margin-bottom:4px"><span class="badge badge-success" style="font-size:9px;padding:2px 4px">Cobrado</span></div>' 
                    : '<div style="margin-bottom:4px"><span class="badge badge-danger" style="font-size:9px;padding:2px 4px">Pendiente</span></div>';
                return `<tr>
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${r.cliente || '—'}</div>
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
                const est = remitoEstado(r.pagado ?? r.estado_cobro);
                const badgeStatus = `<div style="margin-bottom:4px"><span class="badge ${est.badgeClass}" style="font-size:9px;padding:2px 4px">${est.label}</span></div>`;
                const actionBtn = est.cobrable
                    ? `<button type="button" class="btn btn-ghost btn-sm btn-cobrar-remito" data-rid="${r.id}" style="color:var(--success); border:1px solid var(--success); padding: 2px 4px; font-size:9px; width:100%">Cobrar</button>`
                    : '';
                
                const logisticaText = '$' + fmtCompact(r.costo_total_logistica);
                const ventaText = '$' + fmtCompact(r.precio_venta_total);
                const margenText = '$' + fmtCompact(r.margen);

                return `<tr>
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${r.cliente || '—'}</div>
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
                btn.addEventListener('click', async ev => {
                    ev.stopPropagation();
                    if (!confirm('¿Confirmar cobro de este remito?')) return;
                    try {
                        await api('/api/remitos/' + btn.dataset.rid + '/cobrar', { method: 'POST' });
                        toast('Remito cobrado correctamente');
                        await loadAll();
                        await renderRemitosFull();
                    } catch (e) { toast(e.message, true); }
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
            const csv = '\ufeff' + [headers.map(csvCell).join(';'), ...lines].join('\r\n');
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
                    <td><strong>${escapeHTML(a.alias)}</strong><br><small style="color:var(--text-light)">Op ID: ${a.operacion_id}</small></td>
                    <td><span class="badge ${a.accion === 'ELIMINADO' ? 'badge-danger' : 'badge-success'}">${a.accion}</span></td>
                    <td class="money">$${fmt(a.monto)}</td>
                    <td><button class="btn btn-ghost btn-sm" onclick="promptDeleteAuditoria(${a.id})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button></td>
                </tr>
            `).join('') : '<tr><td colspan="5" class="empty-state">No hay registros de auditoría</td></tr>';
        }

        window.promptDeleteAuditoria = (id) => {
            selectedAuditId = id;
            $('inpPasswordAuditoria').value = '';
            $('modalPasswordAuditoria').classList.add('active');
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
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${r.alias}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">${(r.fecha_pago || '').slice(0, 10)}</div>
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div style="color:#111827;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:80px" title="${r.detalle||''}">${r.detalle || '—'}</div>
                        <div style="color:var(--text-muted);font-size:9px;margin-top:2px">${r.plazo_texto || '—'}</div>
                    </td>
                    <td>
                        <div class="home-amount" style="font-size:13px;color:var(--success)">$${fmtCompact(r.monto_pagado)}</div>
                        ${punText}${descText}
                    </td>
                    <td style="text-align:center">
                        <div style="margin-bottom:4px"><span class="badge-home ${tipoCls}">${r.tipo}</span></div>
                        <div style="font-size:9px;color:var(--text-muted)">${progreso}</div>
                    </td>
                </tr>`;
            }).join('');
        }

        function renderBancos() {
            $('tblBancos').innerHTML = data.bancos.length ? data.bancos.map(b => `
                <tr><td>${b.nombre}</td><td>$${fmt(b.limite)}</td></tr>
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
            if (!table) return;
            
            const countEl = $('clientCount');
            if (countEl) {
                countEl.textContent = (data.clientes || []).length + ' clientes';
            }
            
            table.innerHTML = data.clientes && data.clientes.length ? data.clientes.map(c => {
                const statusBadge = c.limite_superado 
                    ? '<span class="badge badge-danger">Superado</span>' 
                    : '<span class="badge badge-success">Límite OK</span>';
                
                const style = c.limite_superado ? 'color: var(--danger)' : '';
                
                const techoText = '$' + fmtCompact(c.techo_deuda);
                const saldoText = '$' + fmtCompact(c.saldo_actual);

                return `<tr class="clickable" data-id="${c.id}">
                    <td>
                        <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;${style}">${c.nombre}</div>
                        <div style="font-size:9px;color:var(--text-muted);margin-top:2px">Alta: ${(c.created_at || '').slice(0, 10)}</div>
                    </td>
                    <td style="line-height:1.4; font-size:10px;">
                        <div class="home-amount" style="font-size:13px">${saldoText}</div>
                        <div style="color:var(--text-muted);font-size:9px;margin-top:2px">Techo: <span style="color:#111827">${techoText}</span></div>
                    </td>
                    <td style="text-align:center"><span class="badge badge-neutral" style="text-align:center; display:inline-block; width:20px;">${c.scoring}</span></td>
                    <td style="text-align:center">${statusBadge}</td>
                </tr>`;
            }).join('') : '<tr><td colspan="4" style="color:var(--text-muted);padding:16px;text-align:center">Sin clientes registrados</td></tr>';
            
            table.querySelectorAll('tr.clickable').forEach((row, idx) => {
                row.addEventListener('click', () => {
                    activeRowIndex = idx;
                    updateSelectedRow(Array.from(table.querySelectorAll('tr')));
                    const cid = parseInt(row.dataset.id, 10);
                    openClientDrawer(cid);
                });
            });
        }

        async function openClientDrawer(clientId) {
            selectedClienteId = clientId;
            $('drawerClientOverlay').classList.add('open');
            $('drawerClientBody').innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px">Cargando detalles...</div>';
            
            try {
                const c = await api('/api/clientes/' + clientId);
                if (selectedClienteId !== clientId) return;
                
                $('drawerClientTitle').textContent = c.nombre;
                
                const limitStatus = c.limite_superado 
                    ? '<span class="badge badge-danger">Límite de crédito superado</span>' 
                    : '<span class="badge badge-success">Crédito OK</span>';
                    
                let remitosHtml = '';
                if (c.remitos && c.remitos.length) {
                    remitosHtml = `
                    <h4 style="margin:20px 0 10px 0;border-bottom:1px solid var(--border);padding-bottom:5px">Remitos de Venta</h4>
                    <div class="table-wrap">
                        <table class="home-table" style="font-size:0.85rem">
                            <thead>
                                <tr>
                                    <th>Info</th>
                                    <th>Venta</th>
                                    <th>Estado</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${c.remitos.map(r => {
                                    const est = remitoEstado(r.pagado ?? r.estado_cobro);
                                    const btn = r.estado_cobro !== 'cobrado' 
                                        ? `<div style="margin-top:4px"><button class="btn btn-ghost btn-sm btn-cobrar-remito-drawer" data-rid="${r.id}" style="color:var(--success); border:1px solid var(--success); padding:2px 6px; font-size:9px">Cobrar</button></div>` 
                                        : '';
                                        
                                    return `<tr>
                                        <td>
                                            <div class="home-contraparte" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Remito #${r.id}</div>
                                            <div style="font-size:9px;color:var(--text-muted);margin-top:2px">${r.fecha}</div>
                                        </td>
                                        <td style="line-height:1.4; font-size:10px;">
                                            <div class="home-amount" style="font-size:13px">$${fmtCompact(r.precio_venta_total)}</div>
                                            <div style="color:var(--text-muted);font-size:9px;margin-top:2px">${fmt(r.kg)} kg</div>
                                        </td>
                                        <td style="text-align:center">
                                            <span class="badge ${est.badgeClass}" style="padding:2px 4px;font-size:0.75rem">${est.label}</span>
                                            ${btn}
                                        </td>
                                    </tr>`;
                                }).join('')}
                            </tbody>
                        </table>
                    </div>
                    `;
                } else {
                    remitosHtml = `
                    <h4 style="margin:20px 0 10px 0;border-bottom:1px solid var(--border);padding-bottom:5px">Remitos de Venta</h4>
                    <div style="color:var(--text-muted);text-align:center;padding:10px;font-size:0.85rem">Sin remitos registrados para este cliente</div>
                    `;
                }
                
                $('drawerClientBody').innerHTML = `
                    <div class="drawer-row"><span class="lbl">ID Cliente</span><span class="val">#${c.id}</span></div>
                    <div class="drawer-row"><span class="lbl">Nombre</span><span class="val" style="font-weight:bold;color:var(--text-primary)">${c.nombre}</span></div>
                    <div class="drawer-row"><span class="lbl">Scoring de Crédito</span><span class="val"><span class="badge badge-neutral" style="width:20px;display:inline-block;text-align:center">${c.scoring}</span></span></div>
                    <div class="drawer-row"><span class="lbl">Techo de Deuda</span><span class="val" style="color:var(--warning)">$${fmt(c.techo_deuda)}</span></div>
                    <div class="drawer-row"><span class="lbl">Saldo de Deuda Actual</span><span class="val" style="color:${c.saldo_actual > 0 ? 'var(--danger)' : 'var(--success)'};font-weight:bold">$${fmt(c.saldo_actual)}</span></div>
                    <div class="drawer-row"><span class="lbl">Estado de Deuda</span><span class="val">${limitStatus}</span></div>
                    <div class="drawer-row"><span class="lbl">Registrado</span><span class="val">${(c.created_at || '').slice(0, 19).replace('T', ' ')}</span></div>
                    ${remitosHtml}
                `;
                
                if (c.saldo_actual > 0 && c.scoring !== 'D') {
                    $('drawerClientIncobrable').style.display = 'inline-block';
                } else {
                    $('drawerClientIncobrable').style.display = 'none';
                }
                
                $('drawerClientBody').querySelectorAll('.btn-cobrar-remito-drawer').forEach(btn => {
                    btn.addEventListener('click', async ev => {
                        ev.stopPropagation();
                        if (!confirm('¿Confirmar cobro de este remito?')) return;
                        try {
                            await api('/api/remitos/' + btn.dataset.rid + '/cobrar', { method: 'POST' });
                            toast('Remito cobrado correctamente');
                            await loadAll();
                            await openClientDrawer(clientId);
                        } catch (e) { toast(e.message, true); }
                    });
                });
                
            } catch (e) {
                $('drawerClientBody').innerHTML = `<div style="color:var(--danger);text-align:center;padding:20px">Error al cargar detalles: ${e.message}</div>`;
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

        function renderAll() {
            renderKpis();
            renderHealth();
            renderChartCfr();
            renderHomeTable();
            renderRemitosDash();
            renderBancos();
            renderBulkLots();
            renderClientes();
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

        function abrirFormularioRegistro(id) {
            $('registroMenu').classList.add('field-hidden');
            document.querySelectorAll('.registro-subview').forEach(el => el.classList.add('field-hidden'));
            $(id).classList.remove('field-hidden');
            // Small animation via CSS class
            $(id).classList.add('fade-in');
            setTimeout(() => $(id).classList.remove('fade-in'), 300);
        }

        function volverMenuRegistro() {
            document.querySelectorAll('.registro-subview').forEach(el => el.classList.add('field-hidden'));
            $('registroMenu').classList.remove('field-hidden');
            $('registroMenu').classList.add('fade-in');
            setTimeout(() => $('registroMenu').classList.remove('fade-in'), 300);
        }

        function switchView(name) {
            activeRowIndex = -1;
            document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === name));
            document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
            const [t, s, bc] = titles[name] || ['', '', name];
            $('pageTitle').textContent = t;
            $('pageSub').textContent = s;
            if ($('breadcrumbPage')) $('breadcrumbPage').textContent = bc || t;
            if (name === 'remitos') renderRemitosFull();
            if (name === 'clientes') renderClientes();
            if (name === 'historial-pagos') renderHistorialPagos();
            if (name === 'auditoria') renderAuditoria();
            if (name === 'registro') volverMenuRegistro();
            $('sidebar').classList.remove('open');
        }

        document.querySelectorAll('.nav-item').forEach(btn => {
            btn.addEventListener('click', () => switchView(btn.dataset.view));
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
        $('drawerClose').addEventListener('click', closeDrawer);
        $('drawerOverlay').addEventListener('click', ev => { if (ev.target === $('drawerOverlay')) closeDrawer(); });
        
        $('drawerClientClose').addEventListener('click', closeClientDrawer);
        $('drawerClientCloseBtn').addEventListener('click', closeClientDrawer);
        $('drawerClientOverlay').addEventListener('click', ev => { if (ev.target === $('drawerClientOverlay')) closeClientDrawer(); });

        $('drawerPagar').addEventListener('click', abrirModalPago);
        $('drawerDelete').addEventListener('click', async () => {
            if (!selectedDeuda || !confirm('¿Eliminar ' + selectedDeuda.alias + '?')) return;
            await api('/api/operaciones/' + selectedDeuda.id, { method: 'DELETE' });
            toast('Obligación eliminada');
            closeDrawer();
            await loadAll();
        });

        $('btnConfirmDeleteAuditoria')?.addEventListener('click', async () => {
            const pwd = $('inpPasswordAuditoria').value;
            if (!pwd) return;
            try {
                await api('/api/auditoria/' + selectedAuditId, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: pwd })
                });
                toast('Registro eliminado permanentemente');
                $('modalPasswordAuditoria').classList.remove('active');
                await loadAll();
            } catch (err) {
                toast('Contraseña incorrecta o error', true);
            }
        });

        $('btnCancelarPago').addEventListener('click', cerrarModalPago);
        $('btnConfirmarPago').addEventListener('click', confirmarPago);
        $('modalPago').addEventListener('click', ev => { if (ev.target === $('modalPago')) cerrarModalPago(); });
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
            try {
                await api('/api/remitos', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cliente: fd.get('cliente'),
                        kg: fd.get('kg'), costo_total_logistica: fd.get('costo'),
                        precio_venta_total: fd.get('venta'), plazo_cobro_dias: fd.get('plazo')
                    })
                });
                toast('Remito de venta registrado');
                ev.target.reset();
                ev.target.plazo.value = 30;
                await loadAll();
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
            } catch (e) { toast(e.message, true); }
        });
        
        $('drawerClientIncobrable').addEventListener('click', async () => {
            if (!selectedClienteId) return;
            if (!confirm('⚠️ ¿ESTÁ SEGURO DE DECLARAR ESTE CLIENTE COMO INCOBRABLE?\nSu deuda de saldo pendiente se pasará a pérdidas y el crédito se bloqueará a $0 para siempre.')) return;
            try {
                await api('/api/clientes/' + selectedClienteId + '/incobrable', { method: 'POST' });
                toast('Cliente declarado como Incobrable');
                closeClientDrawer();
                await loadAll();
            } catch (e) { toast(e.message, true); }
        });

        $('drawerClientPrint').addEventListener('click', async () => {
            if (!selectedClienteId) return;
            try {
                const c = await api('/api/clientes/' + selectedClienteId);
                
                const printWindow = window.open('', '_blank', 'width=800,height=600');
                if (!printWindow) {
                    toast('El navegador bloqueó la ventana emergente de impresión', true);
                    return;
                }
                
                const remitosRows = c.remitos && c.remitos.length 
                    ? c.remitos.map(r => {
                        const est = remitoEstado(r.pagado ?? r.estado_cobro);
                        return `
                        <tr>
                            <td>${r.fecha}</td>
                            <td>#${r.id}</td>
                            <td style="text-align:right">${fmt(r.kg)} kg</td>
                            <td style="text-align:right">$${fmt(r.kg > 0 ? r.precio_venta_total / r.kg : 0)}</td>
                            <td style="text-align:right">$${fmt(r.precio_venta_total)}</td>
                            <td style="text-align:center">${est.label.toUpperCase()}</td>
                        </tr>
                    `;
                    }).join('')
                    : '<tr><td colspan="6" style="text-align:center;padding:10px">Sin movimientos registrados</td></tr>';
                    
                const totalKg = c.remitos ? c.remitos.reduce((acc, r) => acc + r.kg, 0) : 0;
                const totalVendido = c.remitos ? c.remitos.reduce((acc, r) => acc + r.precio_venta_total, 0) : 0;
                const totalCobrado = c.remitos ? c.remitos.reduce((acc, r) => acc + (Number(r.pagado) === 1 ? r.precio_venta_total : 0), 0) : 0;
                const disponible = Math.max(0, c.techo_deuda - c.saldo_actual);
                
                printWindow.document.write(`
                    <html>
                    <head>
                        <title>Reporte - ${c.nombre}</title>
                        <style>
                            body {
                                font-family: 'Courier New', Courier, monospace;
                                color: #000;
                                background: #fff;
                                padding: 20px;
                                font-size: 12px;
                                line-height: 1.4;
                            }
                            .header {
                                text-align: center;
                                border-bottom: 2px dashed #000;
                                padding-bottom: 10px;
                                margin-bottom: 20px;
                            }
                            .header h1 {
                                margin: 0;
                                font-size: 20px;
                                text-transform: uppercase;
                            }
                            .header p {
                                margin: 5px 0 0 0;
                                font-size: 11px;
                            }
                            .details-table, .data-table {
                                width: 100%;
                                border-collapse: collapse;
                                margin-bottom: 20px;
                            }
                            .details-table td {
                                padding: 4px 8px;
                                vertical-align: top;
                            }
                            .details-table td.label {
                                font-weight: bold;
                                width: 180px;
                                text-transform: uppercase;
                            }
                            .data-table th, .data-table td {
                                border: 1px solid #000;
                                padding: 6px 8px;
                            }
                            .data-table th {
                                background-color: #f2f2f2;
                                text-transform: uppercase;
                                font-weight: bold;
                            }
                            .summary-box {
                                border: 2px solid #000;
                                padding: 10px;
                                margin-top: 20px;
                                display: flex;
                                justify-content: space-between;
                            }
                            .summary-col {
                                flex: 1;
                            }
                            .summary-row {
                                margin-bottom: 5px;
                            }
                            .summary-row span.label {
                                font-weight: bold;
                                text-transform: uppercase;
                            }
                            .footer {
                                margin-top: 40px;
                                text-align: center;
                                font-size: 10px;
                                border-top: 1px dashed #000;
                                padding-top: 10px;
                            }
                            @media print {
                                body { padding: 0; }
                                button { display: none; }
                            }
                        </style>
                    </head>
                    <body>
                        <div class="header">
                            <h1>Master Total — Distribuidora de Carne</h1>
                            <p>Reporte de Cuenta de Cliente · Generado el: ${new Date().toLocaleString('es-AR')}</p>
                        </div>
                        
                        <table class="details-table">
                            <tr>
                                <td class="label">Cliente:</td>
                                <td><strong>${c.nombre}</strong></td>
                                <td class="label">Scoring de Crédito:</td>
                                <td>${c.scoring}</td>
                            </tr>
                            <tr>
                                <td class="label">Techo de Deuda:</td>
                                <td>$${fmt(c.techo_deuda)}</td>
                                <td class="label">Saldo de Deuda Actual:</td>
                                <td><strong>$${fmt(c.saldo_actual)}</strong></td>
                            </tr>
                        </table>
                        
                        <h3 style="text-transform:uppercase;border-bottom:1px solid #000;padding-bottom:3px">Historial de Remitos de Venta</h3>
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>Fecha</th>
                                    <th>Nro Remito</th>
                                    <th style="text-align:right">Kilos</th>
                                    <th style="text-align:right">Precio Unit.</th>
                                    <th style="text-align:right">Total Remito</th>
                                    <th style="text-align:center">Estado Pago</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${remitosRows}
                            </tbody>
                        </table>
                        
                        <div class="summary-box">
                            <div class="summary-col">
                                <div class="summary-row"><span class="label">Total Comprado (Kg):</span> ${fmt(totalKg)} kg</div>
                                <div class="summary-row"><span class="label">Total Compras Facturado:</span> $${fmt(totalVendido)}</div>
                            </div>
                            <div class="summary-col" style="text-align:right">
                                <div class="summary-row"><span class="label">Total Cobrado (Pagado):</span> $${fmt(totalCobrado)}</div>
                                <div class="summary-row"><span class="label">Saldo Impago Pendiente:</span> <strong style="font-size:14px">$${fmt(c.saldo_actual)}</strong></div>
                                <div class="summary-row"><span class="label">Crédito Disponible:</span> $${fmt(disponible)}</div>
                            </div>
                        </div>
                        
                        <div class="footer">
                            <p>— Fin de Reporte de Cuenta —</p>
                            <p>Master Total Terminal - Desarrollado para Distribuidoras de Carne</p>
                        </div>
                        
                        <script>
                            window.onload = function() {
                                window.print();
                                setTimeout(function() { window.close(); }, 500);
                            };
                        <\/script>
                    </body>
                    </html>
                `);
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
            $('clock').textContent = new Date().toLocaleString('es-AR', {
                weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'
            });
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
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js')
                    .then((reg) => console.log('Service Worker registrado en el ámbito:', reg.scope))
                    .catch((err) => console.error('Error al registrar Service Worker:', err));
            });
        }

        loadAll();
        setInterval(loadAll, 60000);