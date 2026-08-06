/* Sincronización de la cola de operaciones.
 *
 * Envía en lote a /api/sync, que descarta duplicados por uuid. Se dispara al
 * recuperar la red, cada 60 s y a pedido del usuario desde el chip de estado. */

const Sync = (() => {
  let sincronizando = false;
  let sesionVencida = false;
  const oyentes = [];

  const alCambiar = (fn) => oyentes.push(fn);
  const notificar = (estado) => oyentes.forEach((fn) => fn(estado));

  async function estado() {
    const pendientes = await Store.pendientes();
    return {
      online: navigator.onLine,
      pendientes: pendientes.length,
      sincronizando,
      sesionVencida,
      estado: sincronizando ? 'sincronizando'
            : sesionVencida ? 'offline'
            : !navigator.onLine ? 'offline'
            : pendientes.length ? 'pendiente' : 'ok',
    };
  }

  /** La llama el login exitoso: la cola vuelve a ser enviable. */
  function sesionRenovada() {
    sesionVencida = false;
    return sincronizar({ silencioso: false });
  }

  // Tope de tamaño por envío. El borde de Vercel rechaza con 413 cualquier
  // petición de más de 4,5 MB, y ese límite es de infraestructura: no se puede
  // subir por configuración. Se deja margen para las cabeceras y el JSON.
  //
  // Con señal permanente la cola nunca se acerca a esto —sincroniza cada 60 s—,
  // pero si un access point se cae a mitad de recorrida las fotos se acumulan:
  // a ~135 KB cada una en base64, treinta y pico alcanzan el techo. Sin partir
  // el envío, el auditor perdería la recorrida entera por un 413.
  const MAX_TANDA = 3.5 * 1024 * 1024;

  /** Parte las operaciones en tandas que no superen el tope. */
  function enTandas(operaciones, tope = MAX_TANDA) {
    const tandas = [];
    let actual = [], peso = 0;

    for (const op of operaciones) {
      const suyo = JSON.stringify(op).length;
      // Una operación sola más grande que el tope viaja igual: partirla no es
      // posible y el servidor dirá qué pasa. Es preferible a descartarla en
      // silencio.
      if (actual.length && peso + suyo > tope) {
        tandas.push(actual);
        actual = []; peso = 0;
      }
      actual.push(op);
      peso += suyo;
    }
    if (actual.length) tandas.push(actual);
    return tandas;
  }

  async function sincronizar({ silencioso = true } = {}) {
    if (sincronizando) return estado();

    const pendientes = await Store.pendientes();
    if (!pendientes.length) { notificar(await estado()); return estado(); }

    if (!navigator.onLine) {
      if (!silencioso) UI.toast('Sin conexión. Los cambios quedan guardados.', '');
      notificar(await estado());
      return estado();
    }

    sincronizando = true;
    notificar(await estado());

    try {
      const operaciones = pendientes.map((op) => ({
        uuid: op.uuid, metodo: op.metodo, ruta: op.ruta, body: op.body,
      }));

      let ok = 0, fallidas = 0;
      for (const tanda of enTandas(operaciones)) {
        const r = await API.post('/api/sync', { operaciones: tanda });
        for (const res of r.resultados) {
          if (res.estado === 'OK' || res.estado === 'DUPLICADA') {
            await Store.quitarDeCola(res.uuid);
            ok++;
          } else {
            // Un error del servidor no se reintenta indefinidamente: se marca
            // para que el auditor lo vea en el detalle de sincronización.
            await Store.marcarIntento(res.uuid, res.error);
            fallidas++;
          }
        }
      }

      if (!silencioso || fallidas) {
        if (fallidas) {
          UI.toast(`${ok} operación(es) sincronizada(s), ${fallidas} con error`, 'error');
        } else if (ok) {
          UI.toast(`${ok} operación(es) sincronizada(s)`, 'ok');
        }
      }
    } catch (e) {
      // Un 401 deja la cola intacta pero sin forma de subirla: hay que avisar,
      // porque si no la tablet muestra "pendiente" indefinidamente sin motivo.
      if (e.codigo === 401) {
        sesionVencida = true;
        notificar(await estado());
        UI.toast('La sesión venció. Volvé a iniciar sesión para enviar lo pendiente.',
                 'error', 6000);
      } else if (!silencioso) {
        UI.toast('No se pudo sincronizar: ' + e.message, 'error');
      }
    } finally {
      sincronizando = false;
      notificar(await estado());
    }
    return estado();
  }

  /** Detalle de lo pendiente, para la hoja del chip de sincronización. */
  async function detalle() {
    const pendientes = await Store.pendientes();
    return pendientes.map((op) => ({
      uuid: op.uuid, ruta: op.ruta, metodo: op.metodo,
      creado: op.creado, intentos: op.intentos || 0, error: op.ultimoError,
    }));
  }

  function iniciar() {
    window.addEventListener('online', () => {
      UI.toast('Conexión restablecida', 'ok');
      sincronizar();
    });
    window.addEventListener('offline', async () => {
      UI.toast('Sin conexión. Podés seguir trabajando.', '');
      notificar(await estado());
    });
    setInterval(() => { if (navigator.onLine) sincronizar(); }, 60000);
    sincronizar();
  }

  return { sincronizar, sesionRenovada, estado, detalle, alCambiar, iniciar,
           enTandas, MAX_TANDA };
})();
