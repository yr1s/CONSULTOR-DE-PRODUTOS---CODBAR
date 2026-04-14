const CACHE = 'perez-v1';
const ASSETS = [
  '/',
  '/consulta.html',
  'https://cdn.jsdelivr.net/npm/jsbarcode@3.11.6/dist/JsBarcode.all.min.js'
];

self.addEventListener('install', ev => {
  ev.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', ev => {
  ev.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', ev => {
  // Requisições à API sempre vão para a rede (dados em tempo real)
  if (ev.request.url.includes('/api/')) {
    ev.respondWith(fetch(ev.request));
    return;
  }
  // Demais recursos: cache primeiro, rede como fallback
  ev.respondWith(
    caches.match(ev.request).then(cached => cached || fetch(ev.request))
  );
});
