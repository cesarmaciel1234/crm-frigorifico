/**
 * Motor de sincronización v2: outbox (pending_sync) + changelog LWW + nodos backup.
 */
(function (global) {
    const MAX_NODOS = 10;
    const SYNC_STATES = { IDLE: 'idle', PUSHING: 'pushing', PULLING: 'pulling' };

    const ENTITY_CACHE_KEYS = {
        operacion: 'enemigos',
        cliente: 'clientes',
    };

    function deviceStorageKey(empresaId) {
        return 'crm_device_id_' + empresaId;
    }

    const CrmSync = {
        _db: null,
        _api: null,
        empresaId: 1,
        deviceId: '',
        etiqueta: '',
        _state: SYNC_STATES.IDLE,

        init(db, sessionUser) {
            this._db = db;
            this.empresaId = sessionUser?.empresa_id || 1;
            const key = deviceStorageKey(this.empresaId);
            let id = localStorage.getItem(key);
            if (!id) {
                id = (global.crypto && crypto.randomUUID) ? crypto.randomUUID() : ('dev-' + Date.now());
                localStorage.setItem(key, id);
            }
            this.deviceId = id;
            const labelKey = 'crm_device_label_' + this.empresaId;
            this.etiqueta = localStorage.getItem(labelKey)
                || (navigator.userAgent || 'Dispositivo').slice(0, 60);
            localStorage.setItem(labelKey, this.etiqueta);
        },

        setApi(fn) {
            this._api = fn;
        },

        utcNow() {
            return new Date().toISOString();
        },

        _metaKey() {
            return 'meta:' + this.empresaId;
        },

        async getSyncMeta() {
            if (!this._db?.sync_meta) return { cursor: 0, last_sync_utc: null };
            const row = await this._db.sync_meta.get(this._metaKey());
            return row || { key: this._metaKey(), cursor: 0, last_sync_utc: null };
        },

        async setSyncMeta(patch) {
            if (!this._db?.sync_meta) return;
            const cur = await this.getSyncMeta();
            await this._db.sync_meta.put({ ...cur, key: this._metaKey(), ...patch });
        },

        cacheKey(name) {
            return name + ':' + this.empresaId;
        },

        async getCache(name) {
            if (!this._db) return null;
            return this._db.cache.get(this.cacheKey(name));
        },

        async putCache(name, data) {
            if (!this._db) return;
            await this._db.cache.put({
                key: this.cacheKey(name),
                data,
                updated_at: Date.now(),
            });
        },

        async getAppData() {
            const row = await this.getCache('appData');
            return row?.data || null;
        },

        async putAppData(data) {
            await this.putCache('appData', data);
        },

        async getFullBackup() {
            const row = await this.getCache('fullBackup');
            return row?.data || null;
        },

        async putFullBackup(data) {
            await this.putCache('fullBackup', data);
        },

        /** OUTBOX v2: encolar cambio estructurado antes de sync */
        async enqueueChange({ entity, entity_uuid, action, payload }) {
            if (!this._db?.pending_sync) return null;
            const op_id = crypto.randomUUID();
            await this._db.pending_sync.add({
                op_id,
                entity,
                entity_uuid,
                action: action || 'CREATE',
                payload: { ...payload, uuid: entity_uuid, updated_at_utc: this.utcNow() },
                status: 'pending',
                updated_at_utc: this.utcNow(),
                device_id: this.deviceId,
                attempts: 0,
            });
            return op_id;
        },

        async hasPendingOutbox() {
            if (!this._db) return false;
            let pendingSync = 0;
            try {
                pendingSync = await this._db.pending_sync
                    .where('status').anyOf(['pending', 'pushing', 'failed']).count();
            } catch (_) {}
            const solicitudes = await this._db.solicitudes_pendientes.count();
            let transacciones = 0;
            try {
                transacciones = await this._db.transacciones.where('status').equals(0).count();
            } catch (_) {}
            return pendingSync > 0 || solicitudes > 0 || transacciones > 0;
        },

        /** Drenar pending_sync hacia POST /api/sync/push */
        async drainPendingSync() {
            if (!this._api || !navigator.onLine || !this._db?.pending_sync) {
                return { pushed: 0 };
            }
            if (this._state !== SYNC_STATES.IDLE) return { skipped: true };

            this._state = SYNC_STATES.PUSHING;
            try {
                const batch = await this._db.pending_sync
                    .where('status').anyOf(['pending', 'failed'])
                    .filter(r => (r.attempts || 0) < 8)
                    .sortBy('local_id');

                if (!batch.length) return { pushed: 0 };

                await this._db.transaction('rw', this._db.pending_sync, async () => {
                    for (const row of batch) {
                        await this._db.pending_sync.update(row.local_id, {
                            status: 'pushing',
                            attempts: (row.attempts || 0) + 1,
                        });
                    }
                });

                const response = await this._api('/api/sync/push', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        device_id: this.deviceId,
                        operations: batch.map(r => ({
                            op_id: r.op_id,
                            entity: r.entity,
                            entity_uuid: r.entity_uuid,
                            action: r.action,
                            payload: r.payload,
                            updated_at_utc: r.updated_at_utc,
                        })),
                    }),
                });

                const acked = new Set(response?.acked || []);
                const rejected = response?.rejected || [];

                await this._db.transaction('rw', this._db.pending_sync, async () => {
                    for (const row of batch) {
                        if (acked.has(row.op_id)) {
                            await this._db.pending_sync.update(row.local_id, { status: 'acked' });
                        } else {
                            const rej = rejected.find(x => x.op_id === row.op_id);
                            await this._db.pending_sync.update(row.local_id, {
                                status: rej?.fatal ? 'failed' : 'pending',
                                last_error: rej?.reason || 'rejected',
                            });
                        }
                    }
                });

                return { pushed: acked.size, rejected: rejected.length };
            } catch (err) {
                const pushing = await this._db.pending_sync.where('status').equals('pushing').toArray();
                await this._db.transaction('rw', this._db.pending_sync, async () => {
                    for (const row of pushing) {
                        await this._db.pending_sync.update(row.local_id, { status: 'pending' });
                    }
                });
                throw err;
            } finally {
                this._state = SYNC_STATES.IDLE;
            }
        },

        _applyChangeToList(list, change) {
            list = list || [];
            const uuid = change.entity_uuid;
            const idx = list.findIndex(x => (x.uuid || x.entity_uuid) === uuid);
            const payload = { ...change.payload, uuid };

            if (change.action === 'DELETE') {
                if (idx >= 0) list.splice(idx, 1);
                return list;
            }

            if (idx >= 0) {
                const local = list[idx];
                const localTs = Date.parse(local.updated_at_utc || local.updated_at || 0);
                const remoteTs = Date.parse(change.updated_at_utc || 0);
                if (remoteTs >= localTs) list[idx] = { ...local, ...payload };
            } else {
                list.push(payload);
            }
            return list;
        },

        async applyChangesToCache(changes) {
            if (!changes?.length) return;
            const appData = (await this.getAppData()) || {};
            for (const ch of changes) {
                const cacheKey = ENTITY_CACHE_KEYS[ch.entity];
                if (!cacheKey) continue;
                appData[cacheKey] = this._applyChangeToList(appData[cacheKey], ch);
            }
            await this.putAppData(appData);
            return appData;
        },

        /** Servidor → caché (delta + snapshot inicial) */
        async pullFromServer() {
            if (!this._api || !navigator.onLine) return null;
            if (await this.hasPendingOutbox()) {
                return { blocked: true, reason: 'outbox_not_empty' };
            }
            if (this._state !== SYNC_STATES.IDLE) return null;

            this._state = SYNC_STATES.PULLING;
            try {
                const meta = await this.getSyncMeta();
                const since = meta.cursor || 0;
                const bundle = await this._api(
                    '/api/sync/pull?since=' + encodeURIComponent(since) + '&_=' + Date.now()
                );

                if (bundle?.changes?.length) {
                    await this.applyChangesToCache(bundle.changes);
                }
                if (bundle?.appData) {
                    await this.putAppData(bundle.appData);
                }
                if (bundle?.fullBackup) {
                    await this.putFullBackup(bundle.fullBackup);
                }
                if (bundle?.cursor != null) {
                    await this.setSyncMeta({
                        cursor: bundle.cursor,
                        last_sync_utc: bundle.updated_at || this.utcNow(),
                    });
                }
                return bundle;
            } finally {
                this._state = SYNC_STATES.IDLE;
            }
        },

        async pushNode(snapshot) {
            if (!this._api || !navigator.onLine || !snapshot) return null;
            return this._api('/api/sync/nodo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: this.deviceId,
                    etiqueta: this.etiqueta,
                    snapshot,
                    updated_at_utc: this.utcNow(),
                }),
            });
        },

        async syncSafe(options) {
            options = options || {};
            if (!navigator.onLine) return { offline: true };

            let pushResult = { pushed: 0 };
            try {
                pushResult = await this.drainPendingSync();
            } catch (err) {
                console.warn('drainPendingSync falló:', err);
            }

            if (typeof options.drainOutbox === 'function') {
                await options.drainOutbox();
            }

            const pending = await this.hasPendingOutbox();
            if (pending) {
                return { blocked: true, reason: 'outbox_not_empty', push: pushResult };
            }

            let pulled = null;
            try {
                pulled = await this.pullFromServer();
            } catch (err) {
                console.warn('sync/pull falló:', err);
            }

            const snap = typeof options.getSnapshot === 'function'
                ? options.getSnapshot()
                : await this.getAppData();
            if (snap && !pulled?.blocked) {
                await this.pushNode(snap).catch(() => {});
            }

            return {
                push: pushResult,
                pulled: !!pulled && !pulled?.blocked,
                published: !!snap,
                blocked: !!pulled?.blocked,
            };
        },

        async syncBidirectional(getSnapshot) {
            return this.syncSafe({ getSnapshot });
        },

        async listNodes() {
            if (!this._api || !navigator.onLine) return [];
            const r = await this._api('/api/sync/nodos?_=' + Date.now());
            return r?.nodos || [];
        },

        async restoreFromNode(deviceId) {
            if (!this._api || !navigator.onLine) return null;
            const nodo = await this._api('/api/sync/nodo/' + encodeURIComponent(deviceId) + '?_=' + Date.now());
            if (nodo?.snapshot) {
                await this.putAppData(nodo.snapshot);
            }
            return nodo;
        },

        MAX_NODOS,
    };

    global.CrmSync = CrmSync;
})(window);
