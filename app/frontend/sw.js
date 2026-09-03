/* Service worker — la app tiene que abrir sin conexión.
 *
 * Estrategia:
 *   - Shell (HTML, CSS, JS): stale-while-revalidate. Se responde con la copia
 *     guardada, así que la app abre sin esperar a la red, y en paralelo se baja
 *     la versión publicada para dejarla lista. Antes esto era network-first, y
 *     el motivo sigue valiendo: en una app que calcula porcentajes de
 *     certificación, un bug corregido tiene que llegar al dispositivo sin
 *     depender de que alguien se acuerde de subir el número de VERSION. Lo que
 *     no valía era el precio — con la señal de plataforma, abrir la app
 *     costaba esperar once descargas antes de ver nada.
 *
 *     La garantía se conserva porque la revalidación igual ocurre: la versión
 *     nueva queda en caché para la próxima apertura, y si se detecta que el
 *     archivo cambió se avisa a la pantalla para que ofrezca recargar. No se
 *     recarga sola: hacerlo en medio de una recorrida le borraría al auditor
 *     el desvío que está escribiendo.
 *   - Estáticos inmutables (íconos): cache-first, no cambian.
 *   - API: siempre a la red. Nunca se cachean respuestas ni se sirven datos
 *     viejos como si fueran actuales; de eso se ocupa IndexedDB, que sí sabe
 *     distinguir "guardado local" de "confirmado por el servidor". Las fotos
 *     son la excepción, pero tampoco se cachean acá: las guarda `API.imagen`
 *     en una caché propia ('irj-fotos'), porque esa se puede borrar entera al
 *     cerrar sesión y estas tablets las comparten varios auditores.
 */

const VERSION = 'irj-v25';

// Caché de evidencia fotográfica, propiedad de `API.imagen`. No la maneja el
// worker, pero sí tiene que sobrevivir a sus actualizaciones: el barrido de
// `activate` borra toda caché que no sea la del shell, y sin esta excepción
// cada versión nueva de la app dejaría al auditor sin las fotos que ya tenía
// bajadas, justo en el momento en que puede no haber señal para recuperarlas.
const CACHE_FOTOS = 'irj-fotos';
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
        claves.filter((k) => k !== VERSION && k !== CACHE_FOTOS)
              .map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

/**
 * ¿La respuesta que acaba de llegar es distinta de la que había guardada?
 *
 * Se comparan las marcas del servidor, de la más confiable a la menos: Vercel
 * manda ETag para los estáticos y el servidor de la Mac manda Content-Length.
 * Sin ninguna marca comparable se responde que no cambió: avisar de una
 * actualización que no ocurrió entrena al auditor a ignorar el aviso.
 */
function cambio(vieja, nueva) {
  for (const marca of ['ETag', 'Last-Modified', 'Content-Length']) {
    const a = vieja.headers.get(marca);
    const b = nueva.headers.get(marca);
    if (a && b) return a !== b;
  }
  return false;
}

// Un solo aviso por vida del worker: el shell son once archivos y once toasts
// seguidos diciendo lo mismo son ruido, no información.
let avisado = false;

function avisarActualizacion() {
  if (avisado) return;
  avisado = true;
  self.clients.matchAll({ type: 'window' }).then((clientes) => {
    clientes.forEach((c) => c.postMessage({ tipo: 'shell-actualizado' }));
  });
}

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  if (url.origin !== location.origin) return;      // nada externo
  if (e.request.method !== 'GET') return;          // las escrituras van por la cola

  // API: se deja pasar sin tocar. Salir sin llamar a `respondWith` hace que el
  // navegador resuelva el pedido por su cuenta, sin pasar por el worker.
  //
  // Antes acá había un `respondWith(fetch(e.request))`, que da exactamente el
  // mismo resultado —ir a la red— pero metiendo al worker en el camino de las
  // ~30 llamadas que hace cada pantalla. Eso no aporta nada y sí agrega modos
  // de falla: si el worker está reinstalándose, fue terminado por el sistema o
  // su fetch falla por lo que sea, el pedido muere y la pantalla recibe un
  // "Failed to fetch" que no distingue de un corte de red real. Con `return`
  // el worker no puede romper una llamada a la API aunque quiera.
  if (url.pathname.startsWith('/api/')) return;

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

  // Shell: stale-while-revalidate.
  e.respondWith(
    caches.open(VERSION).then((cache) => cache.match(e.request).then((guardada) => {
      const red = fetch(e.request).then((r) => {
        if (r.ok) {
          if (guardada && cambio(guardada, r)) avisarActualizacion();
          cache.put(e.request, r.clone());
        }
        return r;
      });

      if (guardada) {
        // La revalidación sigue después de haber respondido. Que falle no
        // puede tumbar una respuesta que ya se entregó bien.
        e.waitUntil(red.catch(() => {}));
        return guardada;
      }
      // Primera visita, o archivo fuera del shell: no hay más remedio que
      // esperar a la red. Sin red, el index deja abrir la app igual.
      return red.catch(() => caches.match('/index.html'));
    }))
  );
});
