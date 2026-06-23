/**
 * Motor de sincronización: caché local (IndexedDB) ↔ motor central (API) ↔ nodos de dispositivo.
 * Cada empresa tiene caché aislada; hasta 10 dispositivos sirven como nodos de backup.
 */
(function (global) {
    const MAX_NODOS = 10;

    function deviceStorageKey(empresaId) {
        return 'crm_device_id_' + empresaId;
    }

    const CrmSync = {
        _db: null,
        _api: null,
        empresaId: 1,
        deviceId: '',
        etiqueta: '',

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

        /** Servidor → caché local */
        async pullFromServer() {
            if (!this._api || !navigator.onLine) return null;
            const bundle = await this._api('/api/sync/pull?_=' + Date.now());
            if (bundle?.appData) {
                await this.putAppData(bundle.appData);
            }
            if (bundle?.fullBackup) {
                await this.putFullBackup(bundle.fullBackup);
            }
            return bundle;
        },

        /** Caché local → nodo de backup en el motor central */
        async pushNode(snapshot) {
            if (!this._api || !navigator.onLine || !snapshot) return null;
            return this._api('/api/sync/nodo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: this.deviceId,
                    etiqueta: this.etiqueta,
                    snapshot,
                }),
            });
        },

        /** Bidireccional: bajar del motor y publicar caché como nodo */
        async syncBidirectional(getSnapshot) {
            if (!navigator.onLine) return { offline: true };
            const pulled = await this.pullFromServer();
            const snap = typeof getSnapshot === 'function' ? getSnapshot() : await this.getAppData();
            if (snap) {
                await this.pushNode(snap);
            }
            return { pulled: !!pulled, published: !!snap };
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
