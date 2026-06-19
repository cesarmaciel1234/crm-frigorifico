// Eliminar PWA vieja del POS de prueba (caché offline en iPhone)
self.addEventListener('install', function (e) {
    self.skipWaiting();
});

self.addEventListener('activate', function (e) {
    e.waitUntil(
        caches.keys()
            .then(function (names) { return Promise.all(names.map(function (n) { return caches.delete(n); })); })
            .then(function () { return self.registration.unregister(); })
    );
});

self.addEventListener('fetch', function (e) {
    e.respondWith(fetch(e.request).catch(function () { return caches.match(e.request); }));
});
