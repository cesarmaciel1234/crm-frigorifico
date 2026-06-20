/**
 * Utilidades compartidas: escape HTML y fetch con credenciales de sesión.
 */
(function (global) {
    function esc(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    async function apiFetch(url, opts) {
        opts = opts || {};
        opts.credentials = 'same-origin';
        opts.headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
        return fetch(url, opts);
    }

    global.CrmSafe = { esc, apiFetch };
})(window);
