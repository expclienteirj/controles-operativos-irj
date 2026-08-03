/* Almacenamiento local (IndexedDB) — la app funciona sin conexión.
 *
 * Tres responsabilidades:
 *   1. `meta`     — token de sesión, usuario, catálogos cacheados.
 *   2. `local`    — estado del control en curso tal como lo ve el auditor.
 *   3. `cola`     — operaciones pendientes de enviar al servidor.
 *
 * Cada operación en cola lleva un uuid propio. El backend lo usa para
 * descartar reintentos: si la tablet pierde red al confirmar un sector y
 * reintenta, la operación no se aplica dos veces. */

const Store = (() => {
  const BASE = 'irj';
  const VERSION = 1;
  let _db = null;

  const TIMEOUT_APERTURA = 5000;

  /**
   * Abre la base local.
   *
   * `indexedDB.open` puede quedarse sin emitir ningún evento —ni success, ni
   * error, ni blocked— si otra pestaña mantiene abierta una versión distinta o
   * si quedó pendiente un borrado. Sin timeout, la promesa nunca resuelve y la
   * app se queda en la pantalla de carga sin explicar por qué. En una tablet
   * compartida eso se ve como "la app no abre", sin nada que hacer al respecto.
   */
  function abrir() {
    if (_db) return Promise.resolve(_db);
    return new Promise((resolve, reject) => {
      let resuelto = false;
      const listo = (fn, arg) => {
        if (resuelto) return;
        resuelto = true;
        clearTimeout(reloj);
        fn(arg);
      };

      const reloj = setTimeout(() => listo(reject, new Error(
        'La base local no responde. Cerrá las otras pestañas de la app y '
        + 'volvé a intentar.')), TIMEOUT_APERTURA);

      let req;
      try {
        req = indexedDB.open(BASE, VERSION);
      } catch (e) {
        return listo(reject, e);
      }

      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta');
        if (!db.objectStoreNames.contains('local')) db.createObjectStore('local');
        if (!db.objectStoreNames.contains('cola')) {
          db.createObjectStore('cola', { keyPath: 'uuid' });
        }
      };
      req.onsuccess = () => { _db = req.result; listo(resolve, _db); };
      req.onerror = () => listo(reject, req.error);
      req.onblocked = () => listo(reject, new Error(
        'Hay otra pestaña de la app abierta con una versión distinta. '
        + 'Cerrala y recargá.'));
    });
  }

  function tx(almacen, modo, fn) {
    return abrir().then((db) => new Promise((resolve, reject) => {
      const t = db.transaction(almacen, modo);
      const req = fn(t.objectStore(almacen));
      t.oncomplete = () => resolve(req && req.result);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    }));
  }

  const get = (almacen, clave) => tx(almacen, 'readonly', (s) => s.get(clave));
  const set = (almacen, clave, valor) =>
    tx(almacen, 'readwrite', (s) => s.put(valor, clave));
  const borrar = (almacen, clave) => tx(almacen, 'readwrite', (s) => s.delete(clave));
  const todos = (almacen) => tx(almacen, 'readonly', (s) => s.getAll());

  /* -------------------------------------------------------------- cola -- */

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return 'op-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
  }

  /** Encola una mutación para enviarla cuando haya red. */
  function encolar(metodo, ruta, body) {
    const op = { uuid: uuid(), metodo, ruta, body, creado: Date.now(), intentos: 0 };
    return tx('cola', 'readwrite', (s) => s.put(op)).then(() => op);
  }

  const pendientes = () => todos('cola');
  const quitarDeCola = (uuidOp) => borrar('cola', uuidOp);

  function marcarIntento(uuidOp, error) {
    return tx('cola', 'readwrite', (s) => {
      const req = s.get(uuidOp);
      req.onsuccess = () => {
        const op = req.result;
        if (op) {
          op.intentos = (op.intentos || 0) + 1;
          op.ultimoError = error;
          s.put(op);
        }
      };
      return req;
    });
  }

  /* ------------------------------------------------- estado del control -- */
  // Espejo local de lo que el auditor ya cargó, para que la pantalla siga
  // respondiendo sin red y sobreviva a un cierre de la app.

  const claveControl = (id) => `control:${id}`;

  function estadoControl(controlId) {
    return get('local', claveControl(controlId))
      .then((e) => e || { desvios: {}, confirmados: {} });
  }

  function guardarEstadoControl(controlId, estado) {
    return set('local', claveControl(controlId), estado);
  }

  function registrarDesvioLocal(controlId, itemId, datos) {
    return estadoControl(controlId).then((e) => {
      if (datos === null) delete e.desvios[itemId];
      else e.desvios[itemId] = datos;
      return guardarEstadoControl(controlId, e).then(() => e);
    });
  }

  function confirmarSectorLocal(controlId, sectorId) {
    return estadoControl(controlId).then((e) => {
      e.confirmados[sectorId] = new Date().toISOString();
      return guardarEstadoControl(controlId, e).then(() => e);
    });
  }

  /* ------------------------------------------------------------ sesión -- */

  const guardarSesion = (token, usuario) =>
    Promise.all([set('meta', 'token', token), set('meta', 'usuario', usuario)]);

  const leerSesion = () =>
    Promise.all([get('meta', 'token'), get('meta', 'usuario')])
      .then(([token, usuario]) => ({ token, usuario }));

  const limpiarSesion = () =>
    Promise.all([borrar('meta', 'token'), borrar('meta', 'usuario')]);

  /** Borra todo: se usa al cerrar sesión en una tablet compartida. */
  function limpiarTodo() {
    return Promise.all(['meta', 'local', 'cola'].map((a) =>
      tx(a, 'readwrite', (s) => s.clear())));
  }

  return {
    get, set, borrar, todos,
    encolar, pendientes, quitarDeCola, marcarIntento, uuid,
    estadoControl, guardarEstadoControl, registrarDesvioLocal, confirmarSectorLocal,
    guardarSesion, leerSesion, limpiarSesion, limpiarTodo,
  };
})();
