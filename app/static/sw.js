/* Master Total PWA — network-first for shell updates, offline fallback */
const CACHE_VERSION = 'crm-frigorifico-v42';
const SHELL_ASSETS = [
  '/',
  '/login',
  '/pos',
  '/manifest.json',
  '/static/enterprise.css',
  '/static/js/crm-safe.js',
  '/static/js/sync-engine.js',
  '/static/js/app.js',
  '/static/vendor/dexie.min.js',
  '/static/js/pos-offline.js',
  '/static/icons/icon-180.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

const NETWORK_FIRST_PATHS = [
  '/',
  '/login',
  '/pos',
  '/static/js/app.js',
  '/static/enterprise.css',
  '/static/js/crm-safe.js',
];

function cacheKey(request) {
  const url = new URL(request.url);
  if (url.pathname.startsWith('/static/')) {
    return url.origin + url.pathname;
  }
  return request.url;
}

function isNetworkFirst(url) {
  return NETWORK_FIRST_PATHS.some((p) => url.pathname === p || url.pathname.startsWith(p));
}

function isApiRequest(pathname) {
  return pathname.startsWith('/api/') || pathname.startsWith('/auth/');
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
      .catch((err) => console.warn('SW precache partial:', err))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((n) => n !== CACHE_VERSION).map((n) => caches.delete(n))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  // API y auth: siempre red (nunca cachear — cada usuario/empresa tiene datos distintos)
  if (isApiRequest(url.pathname)) {
    event.respondWith(
      fetch(event.request).catch(() => new Response(
        JSON.stringify({ error: 'Sin conexión', offline: true }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
      ))
    );
    return;
  }

  const isNavigate = event.request.mode === 'navigate';
  const isStatic = url.pathname.startsWith('/static/');

  if (isNetworkFirst(url) || isNavigate) {
    event.respondWith(networkFirst(event.request));
    return;
  }

  if (isStatic) {
    event.respondWith(staleWhileRevalidate(event.request));
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_VERSION).then((cache) => {
            cache.put(cacheKey(event.request), clone);
          });
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(cacheKey(event.request));
        if (cached) return cached;
        if (isNavigate) {
          const shell = await caches.match('/');
          if (shell) return shell;
        }
        return new Response(
          JSON.stringify({ error: 'Sin conexión', offline: true }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        );
      })
  );
});

async function networkFirst(request) {
  const key = cacheKey(request);
  const cache = await caches.open(CACHE_VERSION);
  try {
    const response = await fetch(request);
    if (response.ok) {
      await cache.put(key, response.clone());
    }
    return response;
  } catch (_) {
    const cached = await cache.match(key);
    if (cached) return cached;
    if (request.mode === 'navigate') {
      const shell = await cache.match('/');
      if (shell) return shell;
    }
    throw _;
  }
}

async function staleWhileRevalidate(request) {
  const key = cacheKey(request);
  const cache = await caches.open(CACHE_VERSION);
  const cached = await cache.match(key);

  const networkPromise = fetch(request).then((response) => {
    if (response.ok) cache.put(key, response.clone());
    return response;
  }).catch(() => null);

  return cached || networkPromise || new Response('', { status: 504 });
}

self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
