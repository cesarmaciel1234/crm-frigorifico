/**
 * Motor de sincronización: caché local (IndexedDB) ↔ motor central (API) ↔ nodos de dispositivo.
 * Fase 1: push outbox → pull servidor → publicar nodo backup (nunca al revés).
 */
(function (global) {
    const MAX_NODOS = 10;
    const SYNC_STATES = { IDLE: 'idle', PUSHING: 'pushing', PULLING: 'pulling' };

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

        /** Cola legacy: solicitudes_pendientes + transacciones sin ack */
        async hasPendingOutbox() {
            if (!this._db) return false;
            const solicitudes = await this._db.solicitudes_pendientes.count();
            let transacciones = 0;
            try {
                transacciones = await this._db.transacciones.where('status').equals(0).count();
            } catch (_) {}
            return solicitudes > 0 || transacciones > 0;
        },

        /** Servidor → caché local (solo si outbox vacío) */
        async pullFromServer() {
            if (!this._api || !navigator.onLine) return null;
            if (await this.hasPendingOutbox()) {
                return { blocked: true, reason: 'outbox_not_empty' };
            }
            if (this._state !== SYNC_STATES.IDLE) return null;

            this._state = SYNC_STATES.PULLING;
            try {
                const bundle = await this._api('/api/sync/pull?_=' + Date.now());
                if (bundle?.appData) {
                    await this.putAppData(bundle.appData);
                }
                if (bundle?.fullBackup) {
                    await this.putFullBackup(bundle.fullBackup);
                }
                return bundle;
            } finally {
                this._state = SYNC_STATES.IDLE;
            }
        },

        /** Caché local → nodo de backup (después de sync exitoso, no en cada escritura) */
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

        /**
         * Flujo seguro offline-first:
         * 1) drenar outbox (callback)
         * 2) pull solo si outbox vacío
         * 3) publicar nodo backup
         */
        async syncSafe(options) {
            options = options || {};
            if (!navigator.onLine) return { offline: true };

            if (typeof options.drainOutbox === 'function') {
                await options.drainOutbox();
            }

            const pending = await this.hasPendingOutbox();
            if (pending) {
                return { blocked: true, reason: 'outbox_not_empty' };
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

            return { pulled: !!pulled && !pulled?.blocked, published: !!snap, blocked: !!pulled?.blocked };
        },

        /** @deprecated Usar syncSafe */
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
