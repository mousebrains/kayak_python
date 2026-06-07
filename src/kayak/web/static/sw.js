// Service Worker — network-first with cache fallback.
// On 3G connections, stale data beats a blank screen.
'use strict';

const CACHE = 'kayak-v2';
const TIMEOUT = 3000;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (!req.url.startsWith(self.location.origin)) return;

  e.respondWith(
    Promise.race([
      fetchAndCache(req),
      new Promise((_, reject) => setTimeout(reject, TIMEOUT)),
    ])
      .catch(() => caches.match(req))
      .then((r) => r || fetch(req)),
  );
});

async function fetchAndCache(req) {
  const res = await fetch(req);
  if (res.ok) {
    // Don't cache responses the server marked no-store (editor / _internal
    // pages): CacheStorage.put() ignores HTTP cache semantics, so a stale
    // authenticated page could otherwise be served after the session is gone.
    if (!(res.headers.get('Cache-Control') || '').includes('no-store')) {
      const cache = await caches.open(CACHE);
      cache.put(req, res.clone());
    }
    return res;
  }
  // 5xx: prefer stale cache over a blank server-error page. 4xx passes
  // through unchanged so real 404s still render.
  if (res.status >= 500) {
    const cached = await caches.match(req);
    if (cached) return cached;
  }
  return res;
}
