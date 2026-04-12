// Service Worker — network-first with cache fallback.
// On 3G connections, stale data beats a blank screen.

const CACHE = 'kayak-v2';
const TIMEOUT = 3000;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (!req.url.startsWith(self.location.origin)) return;

  e.respondWith(
    Promise.race([
      fetchAndCache(req),
      new Promise((_, reject) => setTimeout(reject, TIMEOUT))
    ]).catch(() => caches.match(req))
    .then(r => r || fetch(req))
  );
});

async function fetchAndCache(req) {
  const res = await fetch(req);
  if (res.ok) {
    const cache = await caches.open(CACHE);
    cache.put(req, res.clone());
  }
  return res;
}
