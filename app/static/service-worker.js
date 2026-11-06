const CACHE = 'apartment-agent-v49';
const ASSETS = ['/', '/static/styles.css?v=143', '/static/app.js?v=158', '/static/icon.svg', '/manifest.webmanifest'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS.map(url => new Request(url, {cache:'reload'})))));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('apartment-agent-') && key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') {
    // A failed API request must stay a failed API request, never cached HTML.
    event.respondWith(fetch(event.request, {cache:'no-store'}));
    return;
  }
  const navigation = event.request.mode === 'navigate';
  if (!navigation && !url.pathname.startsWith('/static/') && url.pathname !== '/manifest.webmanifest') return;
  const network = fetch(event.request, navigation ? {cache:'no-store'} : undefined);
  event.waitUntil(network.then(response => {
    if (response.ok) {
      const copy = response.clone();
      return caches.open(CACHE).then(cache => cache.put(event.request, copy));
    }
  }).catch(() => {}));
  event.respondWith(network.catch(async () => await caches.match(event.request)
    || (navigation && await caches.match('/')) || Response.error()));
});
