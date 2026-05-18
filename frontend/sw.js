/**
 * Restless Sales Toolkit — Service Worker
 * Cache-first for static assets, network-first for HTML navigation.
 * Offline fallback served from cache when network unavailable.
 */
const CACHE_VERSION = 'v1';
const STATIC_CACHE  = `static-${CACHE_VERSION}`;
const DYNAMIC_CACHE = `dynamic-${CACHE_VERSION}`;

const APP_SHELL = [
  '/',
  '/index.html',
  '/manifest.json',
  '/assets/css/starwatcher.css',
  '/assets/js/app.js',
  '/assets/js/router.js',
  '/assets/js/state.js',
  '/assets/js/api.js',
  '/assets/js/constellation.js',
  '/assets/js/views/dashboard.js',
  '/assets/js/views/companies.js',
  '/assets/js/views/inbox.js',
  '/assets/js/views/health.js',
  '/assets/js/views/login.js',
  '/assets/js/views/manual.js',
  '/assets/fonts/DMSans-Regular.woff2',
  '/assets/fonts/DMSans-Medium.woff2',
  '/assets/fonts/DMSans-SemiBold.woff2',
  '/assets/fonts/DMSans-Bold.woff2',
  '/assets/fonts/InstrumentSerif-Regular.woff2',
  '/assets/fonts/JetBrainsMono-Regular.woff2',
  '/assets/fonts/JetBrainsMono-Medium.woff2',
  '/assets/icons/icon-192.png',
  '/assets/icons/icon-512.png',
  '/offline.html',
];

// ─── Install ────────────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      return cache.addAll(APP_SHELL);
    }).catch((err) => {
      console.warn('[SW] Pre-cache partial failure (non-fatal):', err);
    })
  );
  self.skipWaiting();
});

// ─── Activate ───────────────────────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames
          .filter((name) => name !== STATIC_CACHE && name !== DYNAMIC_CACHE)
          .map((name) => caches.delete(name))
      )
    )
  );
  self.clients.claim();
});

// ─── Fetch ──────────────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle GET from same origin
  if (request.method !== 'GET' || url.origin !== location.origin) return;

  // Field manual — cache-first (large static asset)
  if (url.pathname.startsWith('/manual/')) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Static assets — cache-first
  if (url.pathname.match(/\.(css|js|woff2?|png|jpg|svg|ico|webp)$/)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // HTML navigation — network-first with offline fallback
  if (request.headers.get('Accept')?.includes('text/html')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // API calls — network-only (do not cache auth-gated responses)
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // Everything else — stale-while-revalidate
  event.respondWith(staleWhileRevalidate(request));
});

// ─── Strategies ─────────────────────────────────────────────────────────────
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('Asset unavailable offline', { status: 503 });
  }
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(DYNAMIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached || (await caches.match('/offline.html')) || new Response('Offline', { status: 503 });
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(DYNAMIC_CACHE);
  const cached = await cache.match(request);
  const fetchPromise = fetch(request).then((response) => {
    if (response.ok) cache.put(request, response.clone());
    return response;
  }).catch(() => null);
  return cached || fetchPromise;
}
