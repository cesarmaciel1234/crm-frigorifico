const CACHE_NAME = 'crm-frigorifico-static-v5';
const API_CACHE_NAME = 'crm-frigorifico-api-v5';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) => {
      return Promise.all(names.map(name => caches.delete(name)));
    }).then(() => {
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', (event) => {
  // Siempre ir a la red primero para asegurar que vemos los cambios locales de inmediato
  event.respondWith(fetch(event.request).catch(() => {
      return caches.match(event.request);
  }));
});
