/* Service worker — la app tiene que abrir sin conexión.
 *
 * Estrategia:
 *   - Shell (HTML, CSS, JS): network-first con fallback a caché. Con red se
 *     toma siempre la versión publicada; sin red, la copia guardada. Se
 *     descartó cache-first porque dejaba la tablet ejecutando código viejo
 *     hasta que alguien se acordara de subir el número de VERSION: en una app
 *     que calcula porcentajes de certificación, un bug corregido tiene que
 *     llegar al dispositivo sin depender de eso.
 *   - Estáticos inmutables (íconos): cache-first, no cambian.
 *   - API: siempre a la red. Nunca se cachean respuestas ni se sirven datos
 *     viejos como si fueran actuales; de eso se ocupa IndexedDB, que sí sabe
 *     distinguir "guardado local" de "confirmado por el servidor".
 */

const VERSION = 'irj-v22';
const SHELL = [
  '/',
  '/index.html',
  '/css/app.css',
  '/js/store.js',
  '/js/api.js',
  '/js/calc.js',
  '/js/ui.js',
  '/js/sync.js',
  '/js/config.js',
  '/js/los.js',
  '/js/informes.js',
  '/js/app.js',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(VERSION)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(
        claves.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  if (url.origin !== location.origin) return;      // nada externo
  if (e.request.method !== 'GET') return;          // las escrituras van por la cola

  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Íconos: cache-first, son inmutables.
  if (url.pathname.startsWith('/icons/')) {
    e.respondWith(
      caches.match(e.request).then((c) => c || fetch(e.request).then((r) => {
        if (r.ok) {
          const copia = r.clone();
          caches.open(VERSION).then((cache) => cache.put(e.request, copia));
        }
        return r;
      }))
    );
    return;
  }

  // Shell: network-first. La caché es el plan B para trabajar sin señal.
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r.ok) {
          const copia = r.clone();
          caches.open(VERSION).then((c) => c.put(e.request, copia));
        }
        return r;
      })
      .catch(() => caches.match(e.request)
        .then((c) => c || caches.match('/index.html')))
  );
});
