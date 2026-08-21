/* Aplicación: router por hash, login y módulo Limpieza.
 *
 * Toda la operación sigue la lógica por excepción: los sectores arrancan
 * pendientes, el auditor confirma "sin novedades" o carga los desvíos que
 * encontró, y recién ahí el sector suma al porcentaje. */

const App = (() => {
  let usuario = null;
  let sectores = [];          // catálogo cacheado
  let control = null;         // control en curso {id, periodo, q, estado}
  let local = { desvios: {}, confirmados: {} };

  const $ = (sel) => document.querySelector(sel);
  const app = () => document.getElementById('app');

  /* ======================================================== arranque === */

  async function iniciar() {
    // Sin almacenamiento local no hay trabajo offline ni cola de sincronización:
    // el auditor perdería los hallazgos al quedarse sin señal. Es una falla
    // que hay que mostrar, no una que convenga disimular arrancando igual.
    try {
      await Store.leerSesion();
    } catch (e) {
      return pantallaSinAlmacenamiento(e);
    }

    // La validación de la sesión y el catálogo de sectores viajan juntas. El
    // token sale del almacenamiento local, así que pedir los sectores no
    // depende de que el servidor haya contestado lo otro: en serie, abrir la
    // app costaba dos esperas completas de red antes de la primera pantalla.
    let catalogosListos = false;
    try {
      const guardada = await API.prepararSesion();
      if (guardada.token) {
        [usuario] = await Promise.all([
          API.validarSesion(guardada),
          cargarCatalogos(),
        ]);
        catalogosListos = true;
      } else {
        usuario = null;
      }
    } catch (e) { usuario = null; }

    Sync.iniciar();
    Sync.alCambiar(pintarChipSync);
    window.addEventListener('hashchange', enrutar);

    if (!usuario) return vistaLogin();
    if (!catalogosListos) await cargarCatalogos();
    enrutar();
  }

  function pantallaSinAlmacenamiento(error) {
    app().className = '';
    app().innerHTML = `
      <div class="login">
        <div class="marca"><div class="splash-logo">IRJ</div></div>
        <div class="aviso error">
          <strong>No se puede usar el almacenamiento de la tablet</strong>
          ${UI.esc(error.message)}
        </div>
        <p style="color:var(--gris);font-size:14px">
          La app necesita guardar los relevamientos en el dispositivo para poder
          trabajar sin conexión. Hasta resolverlo no conviene cargar un control:
          los datos podrían perderse.
        </p>
        <button class="btn btn-primario btn-bloque btn-grande" id="reintentar">
          Reintentar
        </button>
      </div>`;
    $('#reintentar').onclick = () => location.reload();
  }

  async function cargarCatalogos() {
    try {
      const r = await API.getCacheado('/api/sectores', 'cache:sectores');
      sectores = r.sectores;
    } catch (e) {
      UI.toast('No se pudieron cargar los sectores', 'error');
      sectores = [];
    }
  }

  function enrutar() {
    const ruta = location.hash.slice(1) || '/';
    const [, seccion, arg] = ruta.split('/');

    if (!usuario) return vistaLogin();
    if (seccion === 'control' && arg) {
      // #/control/:id/sector/:clave — el sector lleva su control en la ruta.
      const [, , id, sub, clave] = ruta.split('/');
      if (sub === 'sector' && clave) return vistaSector(parseInt(id, 10), clave);
      return vistaControl(parseInt(arg, 10));
    }
    // Ruta vieja (#/sector/:clave), sin el control en el hash: se resuelve con
    // el que haya en memoria. Queda por compatibilidad con hashes guardados.
    if (seccion === 'sector' && arg) {
      return vistaSector(control ? control.id : null, arg);
    }
    if (seccion === 'limpieza') return vistaLimpieza();
    // El argumento es el destino puntual dentro de la sección: el ítem de LoS
    // (#/los/pista_rodajes) o la pestaña de configuración (#/config/inventario).
    // Sin esto, una novedad solo podía dejar al auditor en la puerta de la
    // sección y hacerle buscar a ojo el caso que la generó.
    if (seccion === 'los') return LoS.vista(layout, ir, arg);
    if (seccion === 'informes') return Informes.vista(layout, ir);
    if (seccion === 'config') {
      if (usuario.rol !== 'admin') {
        UI.toast('La configuración es solo para administradores', 'error');
        return ir('/');
      }
      return Config.vista(layout, ir, arg);
    }
    return vistaInicio();
  }

  /**
   * Navega a una ruta. Si el hash ya es ese, re-enruta a mano: asignar el
   * mismo valor no dispara `hashchange`, y una subpantalla que comparte hash
   * con su padre (elevación dentro de /los) quedaría trabada sin volver.
   */
  const ir = (ruta) => {
    if (location.hash === '#' + ruta) return enrutar();
    location.hash = ruta;
  };

  /* =========================================================== login === */

  function vistaLogin() {
    app().className = '';
    app().innerHTML = `
      <div class="login">
        <div class="marca">
          <div class="splash-logo">IRJ</div>
          <h1>Controles Operativos</h1>
          <p class="sub">Aeropuerto Cap. V. A. Almonacid</p>
        </div>
        <form id="form-login">
          <div class="campo">
            <label for="usuario">Usuario</label>
            <input id="usuario" name="usuario" type="text" autocomplete="username"
                   autocapitalize="none" autocorrect="off" required autofocus>
          </div>
          <div class="campo">
            <label for="password">Contraseña</label>
            <input id="password" name="password" type="password"
                   autocomplete="current-password" required>
          </div>
          <div id="error-login"></div>
          <button class="btn btn-primario btn-bloque btn-grande" type="submit">
            Ingresar
          </button>
        </form>
      </div>`;

    $('#form-login').onsubmit = async (e) => {
      e.preventDefault();
      const boton = e.target.querySelector('button');
      boton.disabled = true;
      boton.textContent = 'Ingresando…';
      try {
        const r = await API.login($('#usuario').value.trim(), $('#password').value);
        usuario = r.usuario;
        await cargarCatalogos();
        ir('/');
        enrutar();
        // Si la sesión anterior venció con trabajo sin subir, se envía ahora.
        Sync.sesionRenovada();
      } catch (err) {
        const mensaje = navigator.onLine
          ? err.message
          : 'Sin conexión. El primer ingreso requiere red.';
        $('#error-login').innerHTML =
          `<div class="aviso error">${UI.esc(mensaje)}</div>`;
        boton.disabled = false;
        boton.textContent = 'Ingresar';
      }
    };
  }

  /* ========================================================== layout === */

  /**
   * Ruta de "volver" de la pantalla actual.
   *
   * La guarda el layout para que la barra inferior pueda reconstruirse
   * (`repintarBarraInferior`) sin perder el botón de volver, que ahora vive
   * ahí adentro.
   */
  let volverActual = null;

  function layout(titulo, subtitulo, contenido, { volver = null, inferior = '' } = {}) {
    volverActual = volver;
    app().className = 'app';
    app().innerHTML = `
      <header class="barra">
        ${volver ? `<button class="btn-volver" id="btn-volver"
                      aria-label="Volver">←</button>` : ''}
        <h1>${UI.esc(titulo)}<span class="sub">${UI.esc(subtitulo || '')}</span></h1>
        <button class="btn-novedades" id="btn-novedades" hidden
                aria-label="Novedades">
          <span class="campana" aria-hidden="true">!</span>
          <span class="cuenta"></span>
        </button>
        <button class="chip-sync" id="chip-sync">
          <span class="punto"></span><span class="txt">…</span>
        </button>
      </header>
      <main class="contenido">${contenido}</main>
      ${volver || inferior
        ? `<div class="barra-inferior">${barraInferiorHTML(inferior)}</div>`
        : ''}`;

    if (volver) $('#btn-volver').onclick = () => ir(volver);
    $('#chip-sync').onclick = hojaSync;
    $('#btn-novedades').onclick = hojaNovedades;
    enlazarVolverInferior();
    Sync.estado().then(pintarChipSync);
    pintarNovedades();
  }

  /**
   * Contenido de la barra inferior: volver + las acciones de la pantalla.
   *
   * En vertical la barra superior queda a ~1.200px del pulgar y ahí vivía el
   * único botón de volver: con la tablet en una mano había que recolocarla o
   * usar la segunda. La flecha de arriba se mantiene —es la convención— pero
   * ya no es la única salida.
   */
  function barraInferiorHTML(inferior = '') {
    return `${volverActual
      ? `<button class="btn-volver-inf" id="btn-volver-inf"
                 aria-label="Volver">←</button>`
      : ''}${inferior}`;
  }

  function enlazarVolverInferior() {
    const btn = $('#btn-volver-inf');
    if (btn) btn.onclick = () => ir(volverActual);
  }

  /* ==================================================== novedades === */

  /**
   * Centro de novedades: lo que hay que saber al abrir la app.
   *
   * Reemplaza a las notificaciones push, que en esta instalación no son
   * posibles: la app se sirve por http en la red local y el navegador no
   * habilita service workers fuera de un contexto seguro. Esto funciona hoy y
   * sin infraestructura nueva.
   *
   * Se recalcula en cada pantalla porque el servidor lo deriva de los datos:
   * no hay estado de "leído" que mantener ni que pueda quedar desfasado.
   */
  let _novedades = null;

  async function pintarNovedades() {
    const btn = $('#btn-novedades');
    if (!btn) return;
    try {
      _novedades = await API.getCacheado('/api/novedades', 'cache:novedades');
    } catch (e) {
      return;                    // sin red y sin caché no se inventa nada
    }
    if (!_novedades.total) return (btn.hidden = true);

    btn.hidden = false;
    btn.classList.toggle('critico', _novedades.criticas > 0);
    btn.querySelector('.cuenta').textContent = _novedades.total;
    btn.setAttribute('aria-label',
      `${_novedades.total} novedad(es), ${_novedades.criticas} crítica(s)`);
  }

  /**
   * Qué dice el botón de cada novedad, según la acción que el servidor declaró.
   *
   * Una novedad sin acción no lleva botón. Es a propósito: un día pasado sin
   * control no se puede recuperar ni siendo admin, así que ofrecer "Ir" era
   * prometer una consecuencia inexistente — y en el peor caso terminaba en la
   * pantalla de limpieza apretando "Iniciar" para comerse un error del
   * servidor. Sin botón, la novedad dice lo que es: un dato del mes.
   */
  const ACCION_NOVEDAD = {
    RESOLVER_NC: 'Resolver',
    ALTA_MAQUINA: 'Dar de alta',
    INICIAR_TURNO: 'Iniciar',
    IR: 'Ir',
  };

  function hojaNovedades() {
    const n = _novedades;
    if (!n || !n.total) return UI.toast('No hay novedades', '');

    const fila = (x, i) => `
      <div class="item-pendiente${x.criticidad === 'ALTA' ? ' demorada' : ''}">
        <div class="texto">
          <span class="nombre-item">${UI.esc(x.titulo)}</span>
          <span class="obs">${UI.esc(x.detalle)}</span>
        </div>
        ${ACCION_NOVEDAD[x.accion]
          ? `<button class="btn-texto" data-novedad="${i}">
               ${ACCION_NOVEDAD[x.accion]}</button>`
          : ''}
      </div>`;

    UI.abrirHoja(`
      <h3>Novedades</h3>
      <p class="sub">${n.total} en total${n.criticas ? ` · ${n.criticas} crítica(s)`
                                                    : ''}</p>
      <div class="lista-pendientes">${n.novedades.map(fila).join('')}</div>
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrar;
      hoja.querySelectorAll('[data-novedad]').forEach((b) => {
        b.onclick = () => accionarNovedad(n.novedades[Number(b.dataset.novedad)]);
      });
    });
  }

  /**
   * Ejecuta lo que la novedad declara que se puede hacer.
   *
   * Las dos primeras resuelven en el lugar, apilando una hoja sobre el centro
   * de novedades: el caso concreto ya viajó en la novedad, así que no hay nada
   * que ir a buscar a otra pantalla. Navegar queda para cuando el trabajo
   * realmente vive en otro lado.
   */
  function accionarNovedad(x) {
    const d = x.datos || {};
    switch (x.accion) {
      case 'RESOLVER_NC':
        // Sin control: la no conformidad se cierra por su propio id y no
        // depende de ninguna recorrida abierta.
        return hojaPendientes(d.no_conformidades, null, false);
      case 'ALTA_MAQUINA':
        return hojaMaquinasDeBaja(d.bajas || []);
      case 'INICIAR_TURNO':
        UI.cerrarParaNavegar();
        return crearControl(d.fecha, d.turno);
      case 'IR':
        UI.cerrarParaNavegar();
        return ir(x.ruta);
      default:
        return undefined;
    }
  }

  /**
   * Máquinas sin reposición, para darlas de alta sin salir de novedades.
   *
   * Las marcadas por el modelo viejo llegan sin id de baja: se listan igual
   * —están realmente fuera de servicio— pero sin botón, porque no hay tramo
   * que cerrar. Mostrarlas con un botón que no puede funcionar sería repetir
   * el problema que este cambio corrige.
   */
  function hojaMaquinasDeBaja(bajas) {
    const fila = (b, i) => `
      <div class="item-pendiente">
        <div class="texto">
          <span class="nombre-item">${UI.esc(b.equipo)}</span>
          <span class="obs">${b.id ? `De baja desde ${UI.esc(b.desde)}`
                                   : `Marcada el ${UI.esc(b.desde)} — se repone `
                                     + 'desde el control del día'}</span>
        </div>
        ${b.id ? `<button class="btn-texto" data-alta="${i}">Dar de alta</button>`
               : ''}
      </div>`;

    UI.abrirHoja(`
      <h3>Máquinas fuera de servicio</h3>
      <p class="sub">${bajas.length} sin reposición registrada</p>
      <div class="aviso info">
        Cada día de baja descuenta del ítem 4 de la certificación. Registrala el
        día en que la máquina volvió a estar disponible, no el de hoy si ya
        estaba funcionando antes.
      </div>
      <div class="lista-pendientes">${bajas.map(fila).join('')}</div>
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrar;
      hoja.querySelectorAll('[data-alta]').forEach((b) => {
        b.onclick = () => hojaAltaMaquina(bajas[Number(b.dataset.alta)]);
      });
    });
  }

  /** Reposición de una máquina desde novedades, sin control abierto. */
  function hojaAltaMaquina(baja) {
    UI.abrirHoja(`
      <h3>Dar de alta ${UI.esc(baja.equipo)}</h3>
      <p class="sub">De baja desde ${UI.esc(baja.desde)}</p>
      <div class="campo">
        <label for="alta-hasta">Último día fuera de servicio
          <span class="ayuda">El día siguiente ya cuenta como disponible</span></label>
        <input type="date" id="alta-hasta" value="${UI.esc(UI.hoyISO())}">
      </div>
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-verde" data-guardar>Marcar disponible</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const hasta = hoja.querySelector('#alta-hasta').value;
        if (!hasta) return UI.toast('Indicá el último día fuera de servicio', 'error');
        if (hasta < baja.desde) {
          return UI.toast('La reposición no puede ser anterior a la baja', 'error');
        }
        let r;
        try {
          r = await API.mutar('PUT', `/api/equipamiento/bajas/${baja.id}`, { hasta });
        } catch (e) {
          return UI.toast(e.message, 'error');
        }
        UI.cerrarTodas();
        UI.toast(r.encolada ? 'Guardado (se enviará al recuperar la red)'
                            : 'Equipo disponible', 'ok');
        Sync.estado().then(pintarChipSync);
        pintarNovedades();
      };
    });
  }

  function pintarChipSync(est) {
    const chip = $('#chip-sync');
    if (!chip) return;
    chip.className = `chip-sync ${est.estado}`;
    const texto = est.sesionVencida
      ? `Sesión vencida · ${est.pendientes}`
      : {
          ok: 'Sincronizado',
          pendiente: `${est.pendientes} pendiente${est.pendientes === 1 ? '' : 's'}`,
          offline: est.pendientes ? `Offline · ${est.pendientes}` : 'Sin conexión',
          sincronizando: 'Sincronizando…',
        }[est.estado];
    chip.querySelector('.txt').textContent = texto;
  }

  async function hojaSync() {
    const est = await Sync.estado();
    const detalle = await Sync.detalle();
    const conError = detalle.filter((d) => d.error);

    UI.abrirHoja(`
      <h3>Estado de sincronización</h3>
      <p class="sub">${est.online ? 'Conectado' : 'Sin conexión'} ·
        ${est.pendientes} operación(es) pendiente(s)</p>
      ${est.pendientes === 0
        ? '<div class="aviso info">Todo sincronizado con el servidor.</div>'
        : `<div class="aviso ${est.online ? 'info' : 'advertencia'}">
             Los cambios están guardados en la tablet y se envían solos
             ${est.online ? 'en unos segundos' : 'cuando vuelva la conexión'}.
           </div>`}
      ${conError.length ? `
        <div class="aviso error">
          <strong>${conError.length} operación(es) rechazada(s)</strong>
          ${UI.esc(conError[0].error || '')}
        </div>` : ''}
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
        <button class="btn btn-primario" data-sync ${est.online ? '' : 'disabled'}>
          Sincronizar ahora
        </button>
      </div>
      <button class="btn btn-bloque" data-password style="margin-top:12px">
        Cambiar contraseña
      </button>
      <button class="btn btn-bloque" data-salir style="margin-top:8px">
        Cerrar sesión
      </button>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrar;
      hoja.querySelector('[data-password]').onclick = () => hojaPassword();
      hoja.querySelector('[data-sync]').onclick = async () => {
        cerrar();
        await Sync.sincronizar({ silencioso: false });
        enrutar();
      };
      hoja.querySelector('[data-salir]').onclick = async () => {
        const pend = (await Sync.estado()).pendientes;
        const ok = await UI.confirmar(
          'Cerrar sesión',
          pend ? `Hay ${pend} operación(es) sin sincronizar. Si cerrás sesión ahora `
               + 'se pierden. Conviene sincronizar primero.'
               : 'Se borrarán los datos locales de esta tablet.',
          'Cerrar sesión', 'btn-rojo');
        if (!ok) return;
        cerrar();
        await API.logout();
        usuario = null;
        vistaLogin();
      };
    });
  }

  /**
   * Cambio de contraseña del usuario en sesión.
   *
   * El endpoint existía desde el principio y no había pantalla que lo llamara:
   * la única forma de cambiar una contraseña era que un administrador la
   * reescribiera, con lo que eso implica —alguien más elige y conoce la clave
   * con la que un auditor firma sus recorridas—.
   *
   * No se encola: pide la contraseña actual, así que sin red no hay forma de
   * verificarla, y una operación que espera en la cola dejaría al auditor
   * creyendo que ya cambió cuando todavía no.
   */
  function hojaPassword() {
    UI.abrirHoja(`
      <h3>Cambiar contraseña</h3>
      <p class="sub">Se cierran las sesiones abiertas en otras tablets</p>
      <div class="campo">
        <label for="pw-actual">Contraseña actual</label>
        <input type="password" id="pw-actual" autocomplete="current-password">
      </div>
      <div class="campo">
        <label for="pw-nueva">Nueva contraseña
          <span class="ayuda">Mínimo 8 caracteres</span></label>
        <input type="password" id="pw-nueva" autocomplete="new-password">
      </div>
      <div class="campo">
        <label for="pw-repetir">Repetir la nueva</label>
        <input type="password" id="pw-repetir" autocomplete="new-password">
      </div>
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-primario" data-guardar>Cambiar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const actual = hoja.querySelector('#pw-actual').value;
        const nueva = hoja.querySelector('#pw-nueva').value;
        const repetir = hoja.querySelector('#pw-repetir').value;

        if (!actual) return UI.toast('Escribí tu contraseña actual', 'error');
        if (nueva.length < 8) {
          return UI.toast('La nueva tiene que tener al menos 8 caracteres', 'error');
        }
        // Se compara acá y no en el servidor: el error de tipeo se detecta sin
        // gastar un viaje, y sobre todo sin dejar puesta una contraseña que el
        // auditor cree que es otra.
        if (nueva !== repetir) {
          return UI.toast('Las dos contraseñas nuevas no coinciden', 'error');
        }
        if (!navigator.onLine) {
          return UI.toast('Necesitás conexión para cambiar la contraseña', 'error');
        }

        try {
          await API.post('/api/password', { actual, nueva });
        } catch (e) {
          return UI.toast(e.message, 'error');
        }
        UI.cerrarTodas();
        UI.toast('Contraseña cambiada', 'ok');
      };
    });
  }

  /* =========================================================== inicio === */

  /**
   * Color de cada tarjeta de inicio, según si el mes está listo para liquidar.
   *
   * Verde o rojo, sin punto medio: la liquidación es binaria. Falta cualquier
   * dato y la certificación no sale más baja, no sale —o su peso se
   * redistribuye y mueve el importe—. Un amarillo intermedio sugeriría un
   * margen que no existe.
   *
   * Gris hasta el día en que arranca la liquidación (26 por defecto,
   * configurable): antes de esa fecha la pregunta no aplica, y un semáforo
   * encendido veinticinco días seguidos se vuelve paisaje.
   *
   * Los cuatro colores estaban escritos a mano en el HTML y no miraban nada:
   * Limpieza y LoS salían verdes aunque el mes tuviera dieciséis días sin
   * auditar. Un semáforo que siempre da verde no es información, es adorno.
   *
   * Sin el dato (respuesta vieja en caché de una versión anterior) también cae
   * a gris, que es lo honesto: no sabemos.
   */
  const CLASE_MODULO = {
    AL_DIA: 'sin-novedades',
    FALTANTE: 'total',
    SIN_VENTANA: 'pendiente',
  };
  const claseModulo = (modulos, clave) =>
    CLASE_MODULO[modulos && modulos[clave]] || 'pendiente';

  /**
   * Qué decir debajo del nombre del módulo.
   *
   * En rojo, el motivo pisa la descripción fija. La descripción explica qué es
   * el módulo —algo que el auditor ya sabe— y el color solo avisaba que había
   * un problema sin decir cuál, así que había que entrar a buscarlo. Fuera de
   * la ventana de liquidación y en verde no hay motivo y vuelve la descripción.
   */
  const detalleModulo = (modulos, clave, descripcion) => {
    const motivo = modulos && modulos.motivos && modulos.motivos[clave];
    return motivo
      ? `<span class="detalle falta">${UI.esc(motivo)}</span>`
      : `<span class="detalle">${descripcion}</span>`;
  };

  async function vistaInicio() {
    layout('Controles Operativos IRJ', usuario.nombre,
           '<div class="vacio">Cargando…</div>');

    // Las dos llamadas de esta pantalla salen juntas: el onboarding no depende
    // del control del día, y encadenarlas hacía esperar dos veces a la red en
    // la primera pantalla que ve el auditor.
    const [hoy, onboarding] = await Promise.all([
      API.get('/api/controles/hoy')
        .then((r) => Store.set('meta', 'cache:hoy', r).then(() => r))
        .catch(() => Store.get('meta', 'cache:hoy')),
      // Estado del inventario, para avisarle al admin qué falta configurar.
      usuario.rol === 'admin'
        ? API.get('/api/onboarding').catch(() => null)   // opcional
        : Promise.resolve(null),
    ]);

    if (!hoy) {
      return $('.contenido').innerHTML =
        '<div class="aviso error">No se pudo cargar el control del día.</div>';
    }

    $('.contenido').innerHTML = `
      ${tarjetaControlHoy(hoy)}
      ${bannerMes(hoy.mes, hoy.periodo)}
      <div class="grilla" style="margin-top:16px">
        <button class="sector ${claseModulo(hoy.modulos, 'limpieza')}"
                data-ir="/limpieza">
          <span class="marca">🧹</span>
          <span class="nombre">Limpieza</span>
          ${detalleModulo(hoy.modulos, 'limpieza',
                          'Ver el historial diario del mes')}
        </button>
        <button class="sector ${claseModulo(hoy.modulos, 'los')}" data-ir="/los">
          <span class="marca">📋</span>
          <span class="nombre">Niveles de Servicio</span>
          ${detalleModulo(hoy.modulos, 'los', '11 ítems del manual LoS')}
        </button>
        <button class="sector ${claseModulo(hoy.modulos, 'informes')}"
                data-ir="/informes">
          <span class="marca">📄</span>
          <span class="nombre">Informes</span>
          ${detalleModulo(hoy.modulos, 'informes',
                          'PDF mensual y export de datos en CSV')}
        </button>
        ${usuario.rol === 'admin' ? `
          <button class="sector ${claseModulo(hoy.modulos, 'config')}"
                  data-ir="/config">
            <span class="marca">⚙</span>
            <span class="nombre">Configuración del Aeropuerto</span>
            ${detalleModulo(hoy.modulos, 'config',
              onboarding && !onboarding.terminado
                ? `Falta cargar ${onboarding.total - onboarding.completos} bloque(s) de inventario`
                : 'Inventario, parámetros y datos del período')}
          </button>` : ''}
      </div>`;

    document.querySelectorAll('[data-ir]').forEach((b) => {
      if (!b.disabled) b.onclick = () => ir(b.dataset.ir);
    });
    document.querySelectorAll('[data-abrir-control]').forEach((b) => {
      b.onclick = () => ir(`/control/${b.dataset.abrirControl}`);
    });
    document.querySelectorAll('[data-iniciar-turno]').forEach((b) => {
      b.onclick = () => crearControl(hoy.fecha, b.dataset.iniciarTurno);
    });
    // Acotado a su propio contenedor: el calendario de LoS usa su propio
    // atributo, pero consultar sobre `document` es justamente lo que hace que
    // dos calendarios en pantalla se pisen los handlers.
    const calLimpieza = $('#cal-limpieza');
    if (calLimpieza) {
      calLimpieza.querySelectorAll('[data-dia]').forEach((b) => {
        b.onclick = () => abrirDia(hoy.periodo, b.dataset.dia);
      });
    }

    const btnParciales = $('#ver-parciales');
    if (btnParciales) {
      btnParciales.onclick = () => hojaDiasParciales(hoy.mes, hoy.periodo);
    }
  }

  /** Qué días quedaron con una sola de las dos recorridas exigidas. */
  function hojaDiasParciales(mes, periodo) {
    const dias = (mes.turnos && mes.turnos.dias_parciales) || [];
    const filas = dias.map((d) => `
      <div class="item-pendiente">
        <div class="texto">
          <span class="nombre-item">${UI.esc(UI.fechaCorta(d.fecha))}</span>
          <span class="obs">Falta ${d.faltan.map((x) =>
            (NOMBRE_TURNO[x] || x).toLowerCase()).join(' y ')}</span>
        </div>
        <button class="btn-texto" data-ir-dia="${UI.esc(d.fecha)}">Abrir</button>
      </div>`).join('');

    UI.abrirHoja(`
      <h3>Días con una sola recorrida</h3>
      <p class="sub">${UI.esc(UI.nombrePeriodo(periodo))} — ${dias.length} día(s)</p>
      <div class="aviso info">
        Se exigen dos controles diarios. El turno que no se hizo no baja el
        porcentaje —no hay recorrida de la cual afirmar nada—, pero queda como
        incumplimiento del plan de auditoría.
      </div>
      <div class="lista-pendientes">${filas}</div>
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrar;
      hoja.querySelectorAll('[data-ir-dia]').forEach((b) => {
        b.onclick = () => { UI.cerrarParaNavegar(); abrirDia(periodo, b.dataset.irDia); };
      });
    });
  }

  /**
   * Abre un día del calendario. Con dos turnos por día ya no hay un único
   * control que abrir: se muestran los dos y el auditor elige, salvo que el
   * día esté vacío, donde se ofrece iniciar cualquiera de los dos.
   *
   * "Iniciar" solo se ofrece si `fecha` es hoy: el servidor rechaza abrir un
   * control de un día pasado o futuro (el recorrido que no se hizo en su
   * momento queda como no hecho, ni siquiera un administrador lo reconstruye
   * después), así que para cualquier otro día ni se muestra el botón.
   */
  async function abrirDia(periodo, fecha) {
    let controles = [];
    try {
      controles = (await API.get(`/api/controles?periodo=${periodo}`)).controles;
    } catch (e) {
      controles = (await Store.get('meta', `cache:controles:${periodo}`)) || [];
    }
    const delDia = controles.filter((x) => x.fecha === fecha);
    const esHoy = fecha === UI.hoyISO();

    UI.abrirHoja(`
      <h3>${UI.esc(UI.fechaLarga(fecha))}</h3>
      <p class="sub">Dos recorridas diarias exigidas</p>
      ${['MANANA', 'TARDE'].map((turno) => {
        const c = delDia.find((x) => x.turno === turno);
        const nombre = NOMBRE_TURNO[turno];
        const puedeIniciar = !c && esHoy;
        return `<div class="sector ${c ? (c.estado === 'CERRADO' ? 'sin-novedades'
                                                                : 'con-desvios')
                                       : 'pendiente'}" style="margin-bottom:8px">
          <div class="sector-abrir" style="cursor:default">
            <span class="marca">${c ? (c.estado === 'CERRADO' ? '✓' : '·') : '·'}</span>
            <span class="nombre">${UI.esc(nombre)}</span>
            <span class="detalle">${c ? (c.estado === 'CERRADO' ? 'Cerrado'
                                                                : 'En curso')
                                      : 'No se hizo'}</span>
          </div>
          ${c || puedeIniciar
            ? `<button class="btn-sector-ok"
                       ${c ? `data-ver="${c.id}"` : `data-nuevo="${turno}"`}>
                 ${c ? 'Abrir' : 'Iniciar'}</button>`
            : ''}
        </div>`;
      }).join('')}
      <div class="acciones">
        <button class="btn" data-cancelar>Cerrar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelectorAll('[data-ver]').forEach((b) => {
        b.onclick = () => { UI.cerrarParaNavegar(); ir(`/control/${b.dataset.ver}`); };
      });
      hoja.querySelectorAll('[data-nuevo]').forEach((b) => {
        b.onclick = () => { UI.cerrarParaNavegar(); crearControl(fecha, b.dataset.nuevo); };
      });
    });
  }

  const NOMBRE_TURNO = { MANANA: 'Turno mañana', TARDE: 'Turno tarde' };
  // La marca circular mostraba un punto que no comunicaba nada. Con dos
  // recorridas idénticas por día, lo que distingue a una tarjeta de la otra
  // es el turno, así que la marca lo dice.
  const MARCA_TURNO = { MANANA: 'AM', TARDE: 'PM' };

  /**
   * Tarjetas del día: una por turno. Se exigen las dos recorridas, así que la
   * tarde se muestra desde temprano aunque todavía no toque — que falte tiene
   * que ser visible, no algo que aparezca recién al final del día.
   *
   * Uno de los dos es la acción principal de la pantalla: el que corresponde
   * por horario y todavía no está cerrado. Sin esa jerarquía, la pantalla de
   * entrada quedaba con dos bloques equivalentes y ninguna acción evidente.
   */
  function tarjetaControlHoy(hoy) {
    const dia = UI.fechaLarga(hoy.fecha);
    const sugerido = turnoSugerido(hoy.turnos);
    return `
      <div class="tarjeta">
        <h2>Controles de hoy</h2>
        <p style="margin:0 0 12px;color:var(--gris)">${UI.esc(dia)} —
          se exigen las dos recorridas diarias</p>
        ${hoy.turnos.map((t) => tarjetaTurno(t, t.turno === sugerido)).join('')}
      </div>`;
  }

  // A partir de esta hora la recorrida que toca es la de la tarde.
  const HORA_CORTE_TARDE = 13;

  /**
   * Qué turno proponer como acción principal: el que corresponde por horario
   * si está pendiente, y si ya se cerró, el otro que falte. Si están los dos
   * cerrados no se destaca ninguno — no queda nada por hacer.
   */
  function turnoSugerido(turnos) {
    const pendiente = (t) => !t.control || t.control.estado !== 'CERRADO';
    const porHorario = new Date().getHours() < HORA_CORTE_TARDE ? 'MANANA' : 'TARDE';

    const preferido = turnos.find((t) => t.turno === porHorario);
    if (preferido && pendiente(preferido)) return porHorario;

    const otro = turnos.find((t) => t.turno !== porHorario && pendiente(t));
    return otro ? otro.turno : null;
  }

  function tarjetaTurno(t, principal) {
    const c = t.control;
    const nombre = NOMBRE_TURNO[t.turno] || t.turno;

    if (!c) {
      return `
        <div class="sector pendiente" style="margin-bottom:8px">
          <div class="sector-abrir" style="cursor:default">
            <span class="marca marca-turno">${MARCA_TURNO[t.turno] || '·'}</span>
            <span class="nombre">${UI.esc(nombre)}</span>
            <span class="detalle">Todavía no se inició</span>
          </div>
          <button class="${principal ? 'btn btn-primario btn-bloque btn-grande'
                                     : 'btn-sector-ok'}"
                  data-iniciar-turno="${t.turno}">
            ${principal ? `Iniciar ${nombre.toLowerCase()}` : 'Iniciar'}
          </button>
        </div>`;
    }

    if (c.estado === 'CERRADO') {
      return `
        <div class="sector sin-novedades" style="margin-bottom:8px">
          <button class="sector-abrir" data-abrir-control="${c.id}">
            <span class="marca">✓</span>
            <span class="nombre">${UI.esc(nombre)}</span>
            <span class="detalle">Cerrado ·
              ${Calc.porcentaje(c.porcentaje)} de cumplimiento</span>
          </button>
        </div>`;
    }

    // En curso no es un hallazgo: va en azul/neutro. El ámbar y el rojo quedan
    // reservados para lo que efectivamente está mal.
    const avance = Math.round((c.sectores_confirmados / c.sectores_totales) * 100);
    return `
      <div class="sector en-curso" style="margin-bottom:8px">
        <div class="sector-abrir" style="cursor:default">
          <span class="marca marca-turno">${MARCA_TURNO[t.turno] || '·'}</span>
          <span class="nombre">${UI.esc(nombre)}</span>
          <span class="detalle">En curso — ${c.sectores_confirmados} de
            ${c.sectores_totales} sectores confirmados</span>
          <div class="barra-progreso" style="margin-top:8px">
            <div style="width:${avance}%"></div>
          </div>
        </div>
        <button class="${principal ? 'btn btn-primario btn-bloque btn-grande'
                                   : 'btn-sector-ok'}"
                data-abrir-control="${c.id}">
          ${principal ? `Continuar ${nombre.toLowerCase()}` : 'Abrir'}
        </button>
      </div>`;
  }

  /**
   * Calendario del mes. Distingue el día sin auditar cuyo plazo ya venció del
   * que todavía no llegó: si se marcaran todos, habría alarma todos los días
   * del mes y el aviso dejaría de significar algo.
   */
  function bannerMes(mes, periodo) {
    const abiertos = new Set(mes.dias_abiertos);
    const vencidos = new Set(mes.dias_vencidos_sin_control);

    // Con dos recorridas por día, "auditado" ya no es binario: un día con solo
    // un turno cerrado no es lo mismo que uno completo, y el calendario tiene
    // que dejarlo ver sin abrir el día.
    const porDia = mes.turnos_cerrados_por_dia || {};
    const TURNOS = 2;

    // Este calendario mide únicamente el check-list de limpieza. Nada de lo
    // que se cargue en Niveles de Servicio puede cambiarle una celda.
    const estadoDia = (fecha) => {
      const hechos = (porDia[fecha] || []).length;
      if (hechos >= TURNOS) return 'ok';
      if (hechos) return 'parcial';
      if (abiertos.has(fecha)) return 'abierto';
      return vencidos.has(fecha) ? 'falta' : 'futuro';
    };
    const TITULOS = {
      ok: 'Los dos turnos cerrados', parcial: 'Solo 1 de 2 turnos',
      abierto: 'Iniciado sin cerrar', falta: 'Sin auditar', futuro: 'Aún en plazo',
    };

    const calendario = UI.calendarioMes(periodo, {
      estado: estadoDia,
      titulo: (fecha) => TITULOS[estadoDia(fecha)],
      accion: 'dia',
      leyenda: [
        { clase: 'dia-ok', texto: '2 turnos' },
        { clase: 'dia-parcial', texto: '1 turno' },
        { clase: 'dia-abierto', texto: 'Sin cerrar' },
        { clase: 'dia-falta', texto: 'Sin auditar' },
        { clase: 'dia-futuro', texto: 'En plazo' },
      ],
    });

    // Aviso de una línea: es una condición estable del mes, no una novedad, y
    // repetir la explicación completa todos los días la vuelve invisible.
    const t = mes.turnos || {};
    const avisoTurnos = t.dias_parciales && t.dias_parciales.length
      ? `<div class="aviso advertencia">
           <div class="linea-aviso">
             <strong>${t.dias_parciales.length} día(s) con una sola recorrida</strong>
             <button class="btn" id="ver-parciales">Ver</button>
           </div>
         </div>`
      : '';

    const cob = Math.round((mes.cobertura || 0) * 100);
    const aviso = mes.completo
      ? `<div class="aviso info"><strong>Mes completo</strong>
           Los ${mes.dias_esperados} días de ${UI.esc(UI.nombrePeriodo(periodo))}
           tienen control cerrado.</div>`
      : !mes.cobertura_suficiente
        ? `<div class="aviso advertencia">
             <strong>Cobertura ${cob}% — ${mes.dias_cerrados.length} de
             ${mes.dias_esperados} días auditados</strong>
             Por debajo del mínimo esperado
             (${Math.round(mes.cobertura_minima * 100)}%). Los días sin auditar no
             penalizan al contratista, pero con pocos días el resultado del mes
             es poco representativo.
           </div>`
        : `<div class="aviso info">
             <strong>${mes.dias_cerrados.length} de ${mes.dias_esperados} días
             auditados (${cob}%)</strong>
             ${mes.dias_vencidos_sin_control.length} día(s) quedaron sin control.
             No computan ni penalizan.
           </div>`;

    return `
      <div class="tarjeta">
        <h2>Mes de ${UI.esc(UI.nombrePeriodo(periodo))}</h2>
        ${aviso}
        ${avisoTurnos}
        <div id="cal-limpieza">${calendario}</div>
      </div>`;
  }


  /* ========================================================= limpieza === */

  async function vistaLimpieza() {
    const periodo = UI.periodoActual();
    layout('Limpieza', UI.nombrePeriodo(periodo),
           '<div class="vacio">Cargando controles…</div>', { volver: '/' });

    // Las tres son del mismo período y no se necesitan entre sí: van juntas.
    const [controles, mes, liquidacion] = await Promise.all([
      API.get(`/api/controles?periodo=${periodo}`)
        .then((r) => Store.set('meta', `cache:controles:${periodo}`, r.controles)
          .then(() => r.controles))
        .catch(() => Store.get('meta', `cache:controles:${periodo}`)
          .then((c) => c || [])),
      API.get(`/api/periodos/${periodo}/completitud`)
        .then((r) => Store.set('meta', `cache:completitud:${periodo}`, r)
          .then(() => r))
        .catch(() => Store.get('meta', `cache:completitud:${periodo}`)),
      API.getCacheado('/api/liquidacion', 'cache:liquidacion').catch(() => null),
    ]);

    // Lista de días, del más reciente al más viejo: el auditor casi siempre
    // busca hoy o ayer, no el día 1.
    // Una fila por turno: el día ya no es la unidad de trabajo, la recorrida sí.
    const porClave = Object.fromEntries(
      controles.map((c) => [`${c.fecha}·${c.turno}`, c]));
    const hoy = UI.hoyISO();

    const filas = Calc.diasDelMes(periodo).reverse()
      .filter((f) => f <= hoy)          // los días futuros no se pueden auditar
      .flatMap((fecha) => ['MANANA', 'TARDE'].map((turno) => {
        const c = porClave[`${fecha}·${turno}`];
        const etiqueta = `${UI.fechaCorta(fecha)}${fecha === hoy ? ' · hoy' : ''} — `
                       + (turno === 'MANANA' ? 'mañana' : 'tarde');

        if (!c) {
          // Solo hoy admite iniciar una recorrida. Un día anterior se muestra
          // como lo que es —no se hizo y ya no se puede hacer— y sin botón:
          // ofrecía "Iniciar" y el servidor lo rechazaba, así que el auditor
          // llegaba hasta el diálogo de confirmación para comerse un error.
          if (fecha !== hoy) {
            return `<div class="item total">
                      <span class="texto">
                        <span class="nombre-item">${UI.esc(etiqueta)}</span>
                        <span class="obs">No se hizo — ya no se puede cargar</span>
                      </span>
                      <span class="estado-item">Sin auditar</span>
                    </div>`;
          }
          return `<button class="item" data-crear="${fecha}" data-turno="${turno}">
                    <span class="texto">
                      <span class="nombre-item">${UI.esc(etiqueta)}</span>
                      <span class="obs">Sin iniciar</span>
                    </span>
                    <span class="estado-item">Iniciar</span>
                  </button>`;
        }
        const cerrado = c.estado === 'CERRADO';
        return `<button class="item ${cerrado ? '' : 'parcial'}" data-abrir="${c.id}">
                  <span class="texto">
                    <span class="nombre-item">${UI.esc(etiqueta)}</span>
                    <span class="obs">${UI.esc(c.auditor)}</span>
                  </span>
                  <span class="estado-item">${cerrado ? 'Cerrado' : 'En curso'}</span>
                </button>`;
      })).join('');

    const cob = mes ? Math.round((mes.cobertura || 0) * 100) : 0;
    $('.contenido').innerHTML = `
      ${UI.avisoPendientes(
        liquidacion && liquidacion.pendientes && liquidacion.pendientes.limpieza,
        (ruta) => ir(ruta))}
      ${!navigator.onLine ? `<div class="aviso advertencia">
        <strong>Sin conexión</strong>
        Podés seguir un control ya iniciado. Crear uno nuevo requiere red.
      </div>` : ''}
      ${mes ? (mes.completo
        ? `<div class="aviso info"><strong>Mes completo</strong>
             Los ${mes.dias_esperados} días tienen control cerrado.</div>`
        : `<div class="aviso ${mes.cobertura_suficiente ? 'info' : 'advertencia'}">
             <strong>${mes.dias_cerrados.length} de ${mes.dias_esperados} días
             auditados (${cob}%)</strong>
             ${mes.dias_vencidos_sin_control.length} día(s) sin control.
             No penalizan al contratista, pero bajan la representatividad del mes.
           </div>`) : ''}
      <h2 style="font-size:16px;margin:0 0 12px">Controles diarios</h2>
      <div class="lista-items">${filas}</div>
      ${usuario.rol === 'admin' ? `
        <button class="btn btn-bloque" id="btn-certificacion" style="margin-top:16px">
          Ver certificación del mes
        </button>` : ''}`;

    const btnCert = $('#btn-certificacion');
    if (btnCert) btnCert.onclick = () => hojaCertificacion(periodo);

    document.querySelectorAll('[data-abrir]').forEach((b) => {
      b.onclick = () => ir(`/control/${b.dataset.abrir}`);
    });
    document.querySelectorAll('[data-crear]').forEach((b) => {
      b.onclick = () => crearControl(b.dataset.crear, b.dataset.turno);
    });
  }

  async function crearControl(fecha, turno = 'MANANA') {
    if (!navigator.onLine) {
      return UI.toast('Se necesita conexión para iniciar un control nuevo', 'error');
    }
    // Ya no existe el caso "control atrasado": el servidor solo abre el de hoy,
    // así que el diálogo que avisaba "estás cargando un día anterior" describía
    // un camino imposible.
    const nombre = (NOMBRE_TURNO[turno] || turno).toLowerCase();
    const ok = await UI.confirmar(
      `Iniciar el ${nombre} de hoy`,
      `Se abre el ${nombre} del ${UI.fechaLarga(fecha)}. `
      + 'Todos los sectores arrancan sin verificar.',
      'Iniciar');
    if (!ok) return;

    try {
      const r = await API.post('/api/controles', { fecha, turno });
      ir(`/control/${r.control_id}`);
    } catch (e) {
      UI.toast(e.message, 'error');
    }
  }


  /* ================================================== certificación === */

  const NOMBRE_ITEM_CERT = {
    documentacion: 'Documentación obligatoria',
    ley_19587: 'Ley 19587 (seguridad e higiene)',
    programacion_trabajos: 'Programación de trabajos',
    maquinarias: 'Maquinarias en programación',
    insumos: 'Disponibilidad de insumos',
    calidad_servicio: 'Calidad de servicio',
  };

  async function hojaCertificacion(periodo) {
    let cert;
    try {
      cert = await API.get(`/api/periodos/${periodo}/certificacion`);
    } catch (e) {
      return UI.toast('No se pudo calcular la certificación: ' + e.message, 'error');
    }

    const filas = Object.keys(NOMBRE_ITEM_CERT).map((clave) => {
      const d = cert.detalle[clave];
      const sinDatos = cert.items_sin_datos.includes(clave);
      return `<tr>
        <td style="padding:8px 0">${UI.esc(NOMBRE_ITEM_CERT[clave])}</td>
        <td style="text-align:right;color:var(--gris)">
          ${d ? (d.peso * 100).toFixed(0) + '%' : '—'}</td>
        <td style="text-align:right;font-weight:600;
                   color:${sinDatos ? 'var(--gris)' : 'inherit'}">
          ${sinDatos ? 'Sin datos' : Calc.porcentaje(d.valor)}</td>
      </tr>`;
    }).join('');

    // Antes se apilaban hasta 3 avisos ámbar con el mismo peso visual, y el
    // de cobertura (el que más mueve el número) quedaba mezclado con los
    // demás. Ahora se destaca el más severo y el resto queda plegado: se ve
    // que hay más para revisar sin que todos compitan por la misma atención.
    const lista = cert.advertencias || [];
    const ordenados = [...lista].sort((a, b) => {
      const prioridad = {BLOQUEANTE: 3, ADVERTENCIA: 2, INFO: 1};
      return (prioridad[b.nivel] || 1) - (prioridad[a.nivel] || 1);
    });
    const [principal, ...resto] = ordenados;

    const clasificarAviso = (nivel) => {
      if (nivel === 'BLOQUEANTE') return 'error';
      if (nivel === 'ADVERTENCIA') return 'advertencia';
      return 'info';
    };

    const avisos = !principal ? '' : `
      <div class="aviso ${clasificarAviso(principal.nivel)}">
        ${UI.esc(principal.mensaje)}
      </div>
      ${resto.length ? `
        <details class="detalle-avisos">
          <summary>Ver ${resto.length} aviso(s) más</summary>
          ${resto.map((a) => `
            <div class="aviso ${clasificarAviso(a.nivel)}">
              ${UI.esc(a.mensaje)}
            </div>`).join('')}
        </details>` : ''}`;

    UI.abrirHoja(`
      <h3>Certificación · ${UI.esc(UI.nombrePeriodo(periodo))}</h3>
      <p class="sub">Calculada por el servidor sobre los controles cerrados</p>

      <div class="tarjeta" style="text-align:center;margin-bottom:16px">
        <div style="font-size:38px;font-weight:700;line-height:1.1">
          ${Calc.porcentaje(cert.porcentaje)}
        </div>
        <div style="color:var(--gris);font-size:14px">
          Porcentaje a certificar del mes
        </div>
      </div>

      ${avisos}

      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <thead><tr style="border-bottom:1px solid var(--borde);color:var(--gris)">
          <th style="text-align:left;padding-bottom:6px">Ítem</th>
          <th style="text-align:right">Peso</th>
          <th style="text-align:right">Resultado</th>
        </tr></thead>
        <tbody>${filas}</tbody>
      </table>

      <div class="acciones">
        <button class="btn btn-primario btn-bloque" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrar;
    });
  }

  /* ================================================ control: sectores === */

  /**
   * Deja `control` y `local` cargados para ese id. Devuelve false si no se
   * pudo ni por red ni por caché.
   *
   * Está separado de `vistaControl` porque la vista de sector también lo
   * necesita: con el control en la ruta, entrar directo a un sector tiene que
   * poder recuperarlo (ver `vistaSector`).
   */
  async function cargarControl(controlId) {
    try {
      const datos = await API.get(`/api/controles/${controlId}`);
      control = datos.control;
      await Store.set('meta', `cache:control:${controlId}`, datos);
      // El servidor es la verdad: se re-espeja el estado local.
      local = { desvios: {}, confirmados: {} };
      datos.desvios.forEach((d) => {
        local.desvios[d.item_id] = { estado: d.estado, observacion: d.observacion,
                                     fotos: d.fotos || [] };
      });
      datos.sectores.forEach((s) => {
        if (s.confirmado) local.confirmados[s.sector_id] = true;
      });
      await Store.guardarEstadoControl(controlId, local);
      return true;
    } catch (e) {
      const cache = await Store.get('meta', `cache:control:${controlId}`);
      if (!cache) return false;
      control = cache.control;
      local = await Store.estadoControl(controlId);
      return true;
    }
  }

  function errorControlNoCargado() {
    layout('Control', '', `<div class="aviso error">
      No se pudo cargar el control y no hay copia local.</div>`,
      { volver: '/limpieza' });
  }

  async function vistaControl(controlId) {
    layout('Control', 'Cargando…', '<div class="vacio">Cargando…</div>',
           { volver: '/limpieza' });

    if (!await cargarControl(controlId)) return errorControlNoCargado();
    pintarControl();
  }

  function estadoSector(sector) {
    const ids = sector.items.map((i) => i.id);
    const confirmado = !!local.confirmados[sector.id];
    const desviosSector = {};
    ids.forEach((id) => { if (local.desvios[id]) desviosSector[id] = local.desvios[id]; });
    return {
      confirmado,
      desvios: Object.keys(desviosSector).length,
      porcentaje: Calc.sector(ids, desviosSector, confirmado),
    };
  }

  /**
   * HTML de una tarjeta de sector.
   *
   * Única fuente del markup de la tarjeta: la usan `pintarControl()` para la
   * grilla completa y `repintarSector()` para reemplazar una sola. El
   * `data-sector-id` es lo que permite encontrar el nodo después.
   */
  function tarjetaSector(sector, estado, cerrado) {
    const { confirmado, desvios, porcentaje } = estado;
    const clase = !confirmado ? 'pendiente' : desvios ? 'con-desvios' : 'sin-novedades';
    const marca = !confirmado ? (desvios || '·') : desvios ? desvios : '✓';
    // Un sector con desvíos cargados pero sin confirmar tiene que verse
    // distinto de uno intacto: es trabajo empezado que falta cerrar.
    const detalle = !confirmado
      ? (desvios
          ? `${desvios} desvío(s) · falta confirmar`
          : `${sector.items.length} ítems · sin verificar`)
      : desvios
        ? `${desvios} desvío(s) · ${Calc.porcentaje(porcentaje)}`
        : 'Sin novedades · 100%';
    // Atajo de un toque para el caso frecuente: el sector está bien.
    // Solo aparece si el sector no tiene desvíos cargados; con desvíos el
    // botón diría "TODO OK" sobre un sector que justamente no lo está, así
    // que ahí se ofrece "Confirmar" (confirma el sector con sus desvíos).
    const accion = cerrado || confirmado ? ''
      : desvios
        ? `<button class="btn-sector-ok con-desvios"
                   data-confirmar="${sector.id}">Confirmar</button>`
        : `<button class="btn-sector-ok" data-ok="${sector.id}">TODO OK</button>`;

    return `<div class="sector ${clase}" data-sector-id="${sector.id}">
              <button class="sector-abrir" data-sector="${UI.esc(sector.clave)}">
                <span class="marca">${UI.esc(String(marca))}</span>
                <span class="nombre">${UI.esc(sector.nombre)}</span>
                <span class="detalle">${UI.esc(detalle)}</span>
              </button>
              ${accion}
            </div>`;
  }

  /** Enlaza las dos zonas táctiles de una tarjeta ya montada en el DOM. */
  function enlazarSector(nodo, sectorId) {
    const abrir = nodo.querySelector('[data-sector]');
    if (abrir) {
      abrir.onclick = () => ir(`/control/${control.id}/sector/${abrir.dataset.sector}`);
    }
    const confirmar = nodo.querySelector('[data-ok], [data-confirmar]');
    if (confirmar) confirmar.onclick = () => confirmarSector(sectorId);
  }

  /** Contenido propio de la barra inferior del control: preview + cerrar. */
  function inferiorControlHTML() {
    if (control.estado === 'CERRADO') return '';
    const estados = sectores.map((s) => estadoSector(s));
    const pendientes = estados.filter((e) => !e.confirmado).length;
    const general = Calc.promedio(estados.map((e) => e.porcentaje));
    return `
      <div class="resumen">
        <strong>${Calc.porcentaje(general)}</strong>
        <span style="color:var(--gris)">preview · calcula el servidor</span>
      </div>
      <button class="btn btn-verde" id="btn-cerrar" ${pendientes ? 'disabled' : ''}>
        ${pendientes ? `Faltan ${pendientes} sector(es)` : 'Cerrar control'}
      </button>`;
  }

  /**
   * Reemplaza la tarjeta de un solo sector.
   *
   * Antes, confirmar un sector llamaba a `pintarControl()`, que reconstruye
   * todo el `#app` vía `layout()`: por recorrida de 9 sectores eran 9
   * reconstrucciones completas del DOM, las peticiones de equipamiento,
   * artefactos y pendientes repetidas cada vez, y 9 saltos al tope de la
   * página. El auditor confirmaba un sector de la fila de abajo y la pantalla
   * lo devolvía arriba.
   */
  function repintarSector(sectorId) {
    const nodo = document.querySelector(`.sector[data-sector-id="${sectorId}"]`);
    const sector = sectores.find((s) => s.id === sectorId);
    if (!nodo || !sector) return;

    const molde = document.createElement('div');
    molde.innerHTML = tarjetaSector(sector, estadoSector(sector),
                                    control.estado === 'CERRADO');
    const nuevo = molde.firstElementChild;
    nodo.replaceWith(nuevo);
    enlazarSector(nuevo, sectorId);
  }

  /** Avance de verificación: es lo otro que cambia al confirmar un sector. */
  function repintarAvance() {
    const tarjeta = $('#tarjeta-avance');
    if (!tarjeta) return;
    const pendientes = sectores.filter((s) => !local.confirmados[s.id]).length;
    const avance = Math.round(
      ((sectores.length - pendientes) / sectores.length) * 100);
    tarjeta.querySelector('.avance-texto').textContent =
      `${sectores.length - pendientes} de ${sectores.length} sectores confirmados`
      + (pendientes ? ` · faltan ${pendientes}` : '');
    tarjeta.querySelector('.barra-progreso div').style.width = `${avance}%`;
  }

  /** Repinta solo la barra inferior, conservando el botón de volver. */
  function repintarBarraInferior() {
    // Confirmar un sector también se puede hacer desde la vista de sector, que
    // tiene su propia barra inferior. El guard evita pisarla con la del
    // control: `#tarjeta-avance` solo existe en la grilla.
    if (!$('#tarjeta-avance')) return;
    const barra = document.querySelector('.barra-inferior');
    if (!barra) return;
    barra.innerHTML = barraInferiorHTML(inferiorControlHTML());
    enlazarVolverInferior();
    const btnCerrar = $('#btn-cerrar');
    if (btnCerrar) btnCerrar.onclick = cerrarControl;
  }

  function pintarControl() {
    const cerrado = control.estado === 'CERRADO';
    const estados = sectores.map((s) => ({ sector: s, ...estadoSector(s) }));
    const pendientes = estados.filter((e) => !e.confirmado);
    const avance = Math.round(((sectores.length - pendientes.length) / sectores.length) * 100);

    const tarjetas = estados
      .map(({ sector, ...estado }) => tarjetaSector(sector, estado, cerrado))
      .join('');

    // El turno va en el subtítulo: con dos recorridas idénticas por día, la
    // fecha sola no alcanza para saber en cuál se está cargando.
    layout(NOMBRE_TURNO[control.turno] || 'Control diario',
           UI.fechaLarga(control.fecha), `
      ${cerrado ? `<div class="aviso info">
        <strong>Control cerrado</strong>
        Cerrado el ${UI.esc(UI.fecha(control.cerrado_en))}. No admite cambios.
        ${usuario.rol === 'admin'
          ? '<button class="btn" id="btn-reabrir" style="margin-top:10px">Reabrir control</button>'
          : ''}
      </div>` : ''}
      <div class="tarjeta" id="tarjeta-avance">
        <h2>Avance de verificación</h2>
        <p class="avance-texto" style="margin:0;font-size:14px;color:var(--gris)">
          ${sectores.length - pendientes.length} de ${sectores.length} sectores
          confirmados${pendientes.length ? ` · faltan ${pendientes.length}` : ''}
        </p>
        <div class="barra-progreso"><div style="width:${avance}%"></div></div>
      </div>
      <div class="tarjeta" id="tarjeta-pendientes" hidden></div>
      <div class="grilla">${tarjetas}</div>
      <div class="tarjeta" id="tarjeta-equipamiento" style="margin-top:16px">
        <h2>Maquinarias y equipos</h2>
        <p style="margin:0;font-size:14px;color:var(--gris)">Cargando…</p>
      </div>
      <div class="tarjeta" id="tarjeta-artefactos" hidden></div>`,
      { volver: '/limpieza', inferior: inferiorControlHTML() });

    document.querySelectorAll('.sector[data-sector-id]').forEach((nodo) => {
      enlazarSector(nodo, parseInt(nodo.dataset.sectorId, 10));
    });
    const btnCerrar = $('#btn-cerrar');
    if (btnCerrar) btnCerrar.onclick = cerrarControl;

    const btnReabrir = $('#btn-reabrir');
    if (btnReabrir) btnReabrir.onclick = reabrirControl;

    pintarEquipamiento(cerrado);
    pintarArtefactos(cerrado);
    pintarPendientesAnteriores(control, cerrado);
  }

  /* ================================ no conformidades de auditorías previas === */

  /**
   * Lo que dejó pendiente el auditor anterior, sin importar de qué día venga.
   *
   * Va como un aviso de una línea, no como una lista desplegada: con 19 NC
   * abiertas la lista medía tres pantallas y media y empujaba el check-list
   * —que es a lo que el auditor entró— fuera de la vista. El aviso avisa; el
   * detalle se abre en una hoja cuando el auditor decide ocuparse.
   *
   * Cerrar una NC acá no cambia el porcentaje del mes: la penalización se
   * aplicó cuando se relevó el desvío. Lo que queda registrado es cuánto tardó
   * el contratista en resolverla y quién lo verificó.
   */
  // Días a partir de los cuales una no conformidad sin resolver se destaca.
  // La prioridad del pliego no sirve para ordenar la recorrida: casi todo
  // desvío total entra como INMEDIATA y termina siendo la etiqueta de todas.
  // Lo que sí distingue es cuánto lleva sin resolverse.
  const DIAS_NC_DEMORADA = 7;

  async function pintarPendientesAnteriores(control, cerrado) {
    const caja = $('#tarjeta-pendientes');
    // Sin control no hay tarjeta que repintar: pasa cuando la no conformidad se
    // resolvió desde el centro de novedades, que no cuelga de ninguna recorrida.
    if (!caja || !control) return;

    let pendientes = [];
    try {
      const r = await API.getCacheado(
        `/api/no-conformidades/pendientes?fecha=${control.fecha}`,
        `cache:nc-pendientes:${control.fecha}`);
      pendientes = r.pendientes;
    } catch (e) {
      pendientes = [];      // sin red y sin caché: no se inventa nada
    }
    // Se limpia siempre: al repintar tras resolver una, la tarjeta tiene que
    // poder quedar vacía y desaparecer.
    caja.hidden = true;
    caja.innerHTML = '';
    if (!pendientes.length) return;

    // La más vieja es la que importa: es la que lleva más tiempo sin resolver.
    const vieja = pendientes[pendientes.length - 1];
    const dias = Math.max(...pendientes.map((p) => p.dias_pendiente));

    caja.hidden = false;
    caja.className = 'tarjeta aviso-pendientes';
    caja.innerHTML = `
      <div class="linea-aviso">
        <div>
          <strong>${pendientes.length} pendiente(s) de auditorías anteriores</strong>
          <span class="detalle-aviso">La más antigua lleva ${dias} día(s) —
            ${UI.esc([vieja.sector, vieja.item].filter(Boolean).join(' · '))}</span>
        </div>
        <button class="btn" id="ver-pendientes">Ver</button>
      </div>`;

    $('#ver-pendientes').onclick = () => hojaPendientes(pendientes, control, cerrado);
  }

  /** Lista completa de pendientes, ya fuera del camino del check-list. */
  function hojaPendientes(pendientes, control, cerrado) {
    const fila = (p) => {
      const dias = p.dias_pendiente === 1 ? 'hace 1 día' : `hace ${p.dias_pendiente} días`;
      const demorada = p.dias_pendiente >= DIAS_NC_DEMORADA;
      return `<div class="item-pendiente${demorada ? ' demorada' : ''}">
        <div class="texto">
          <span class="nombre-item">${UI.esc([p.sector, p.item].filter(Boolean).join(' · ')
                                             || p.origen)}</span>
          <span class="obs">${UI.esc(p.descripcion)}</span>
          <span class="obs">Relevado ${UI.esc(p.fecha_origen)} — ${dias}${
            demorada ? ' · demorada' : ''}</span>
        </div>
        ${cerrado ? '' : `<button class="btn-texto" data-resolver="${p.id}">
          Resolver</button>`}
      </div>`;
    };

    UI.abrirHoja(`
      <h3>Pendiente de auditorías anteriores</h3>
      <p class="sub">${pendientes.length} no conformidad(es) sin resolver —
        de la más antigua a la más reciente</p>
      <div class="aviso info">
        Verificalas durante la recorrida. Cerrarlas no modifica el porcentaje
        del mes: solo registra cuánto tardó en resolverse.
      </div>
      <div class="lista-pendientes">${pendientes.map(fila).join('')}</div>
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrarHoja) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrarHoja;
      hoja.querySelectorAll('[data-resolver]').forEach((b) => {
        const p = pendientes.find((x) => String(x.id) === b.dataset.resolver);
        // Se apila sobre esta hoja en vez de reemplazarla: así el botón atrás
        // devuelve la lista de pendientes en lugar de cerrar todo.
        b.onclick = () => hojaResolverNC(p, control, cerrado);
      });
    });
  }

  /** Cierre de una NC ajena: exige constatación escrita, que queda en el log. */
  function hojaResolverNC(p, control, cerrado) {
    UI.abrirHoja(`
      <h3>Resolver no conformidad</h3>
      <p class="sub">${UI.esc([p.sector, p.item].filter(Boolean).join(' · '))} —
        relevada el ${UI.esc(p.fecha_origen)}</p>
      <div class="aviso info">${UI.esc(p.descripcion)}</div>
      ${(p.fotos || []).length ? `
        <div class="campo">
          <label>Cómo se relevó <span class="ayuda">Tocá para ampliar</span></label>
          <div class="fotos" id="nc-fotos"></div>
        </div>` : ''}
      <div class="campo">
        <label>Qué constataste
          <span class="ayuda">Queda registrado con tu usuario y la fecha</span></label>
        <textarea id="nc-resolucion" rows="3"
                  placeholder="Ej.: sector relimpiado, verificado en recorrida"></textarea>
      </div>
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-verde" data-guardar>Marcar resuelta</button>
      </div>`, (hoja, cerrar) => {
      // La foto del día que se relevó. Dar por resuelta una no conformidad
      // leyendo solo la descripción escrita obliga a confiar en el recuerdo de
      // otro auditor, y muchas veces de otro mes.
      UI.galeria(hoja.querySelector('#nc-fotos'), p.fotos || [],
                 { titulo: [p.sector, p.item].filter(Boolean).join(' · ') });
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const texto = hoja.querySelector('#nc-resolucion').value.trim();
        if (!texto) return UI.toast('Indicá qué constataste', 'error');
        try {
          await API.mutar('PUT', `/api/no-conformidades/${p.id}`,
                          { estado: 'RESUELTA', resolucion: texto });
        } catch (e) {
          return UI.toast(e.message, 'error');
        }
        // Cierra también la lista que quedó debajo: sus datos ya son viejos y
        // la tarjeta de fondo se repinta sola.
        UI.cerrarTodas();
        UI.toast('No conformidad resuelta', '');
        pintarPendientesAnteriores(control, cerrado);
        pintarNovedades();
      };
    });
  }

  /* ======================================== artefactos sanitarios (3.1.a) === */

  /**
   * Artefactos de baño fuera de servicio.
   *
   * Se carga acá y no en el módulo LoS porque es una observación de recorrida:
   * el auditor lo ve cuando entra al baño a controlar la limpieza, y el
   * check-list no puede deducirlo — un inodoro clausurado se ve igual de
   * limpio que uno en uso. Alimenta el ítem 3.1.a del manual.
   *
   * Por tramos y no día por día, igual que las maquinarias: un artefacto
   * clausurado dos semanas no se puede estar marcando catorce veces.
   */
  async function pintarArtefactos(cerrado) {
    const caja = $('#tarjeta-artefactos');
    if (!caja) return;

    let datos;
    try {
      datos = await API.getCacheado(`/api/controles/${control.id}/artefactos`,
                                    `cache:artefactos:${control.id}`);
    } catch (e) {
      return;                      // el resto del control sigue disponible
    }
    caja.hidden = true;
    caja.innerHTML = '';
    // Sin núcleos sanitarios cargados no hay nada que ofrecer: el aviso de
    // inventario faltante ya lo da la pantalla de LoS.
    if (!datos.nucleos.length) return;

    const clausurados = datos.nucleos.flatMap((n) =>
      n.equipos.filter((e) => e.fuera_servicio > 0)
        .map((e) => ({ nucleo: n, ...e })));

    caja.hidden = false;
    caja.innerHTML = `
      <h2>Artefactos de baño</h2>
      <p style="margin:0 0 12px;font-size:14px;color:var(--gris)">
        ${clausurados.length
          ? `${clausurados.reduce((t, c) => t + c.fuera_servicio, 0)} artefacto(s)
             fuera de servicio hoy`
          : 'Todos en servicio'}
        — alimenta el ítem 3.1 de Niveles de Servicio
      </p>
      <div class="lista-items">
        ${datos.nucleos.map((n) => `
          <button class="item ${n.equipos.some((e) => e.fuera_servicio) ? 'total' : ''}"
                  data-nucleo="${n.id}" ${cerrado ? 'disabled' : ''}>
            <span class="texto">
              <span class="nombre-item">${UI.esc(n.nombre)}</span>
              <span class="obs">${n.equipos.map((e) =>
                `${UI.esc(etiquetaArtefacto(e.equipo))} ${
                  e.instalados - e.fuera_servicio}/${e.instalados}`).join(' · ')}</span>
            </span>
            <span class="estado-item">${
              n.equipos.some((e) => e.fuera_servicio) ? 'Con clausuras' : 'En servicio'}
            </span>
          </button>`).join('')}
      </div>
      ${datos.bajas.length ? `
        <button class="btn" id="ver-clausuras" style="margin-top:12px">
          Ver clausuras del mes</button>` : ''}`;

    caja.querySelectorAll('[data-nucleo]').forEach((b) => {
      b.onclick = () => {
        if (cerrado) return UI.toast('El control está cerrado', 'error');
        const n = datos.nucleos.find((x) => x.id === parseInt(b.dataset.nucleo, 10));
        hojaClausurarArtefacto(n, datos.fecha, cerrado);
      };
    });
    const ver = $('#ver-clausuras');
    if (ver) ver.onclick = () => hojaClausuras(datos.bajas, cerrado);
  }

  const ETIQUETA_ARTEFACTO = {
    inodoros: 'Inodoros', mingitorios: 'Mingitorios', bachas: 'Bachas',
  };
  const etiquetaArtefacto = (e) => ETIQUETA_ARTEFACTO[e] || e;

  /** Registra artefactos clausurados de un núcleo por un tramo de días. */
  function hojaClausurarArtefacto(n, fechaControl, cerrado) {
    const opciones = n.equipos.map((e) =>
      `<option value="${e.equipo}">${UI.esc(etiquetaArtefacto(e.equipo))} —
        ${e.instalados} instalado(s)</option>`).join('');

    UI.abrirHoja(`
      <h3>${UI.esc(n.nombre)}</h3>
      <p class="sub">Artefactos fuera de servicio</p>
      <div class="aviso info">
        Registrá solo el artefacto que no se puede usar. La limpieza ya se
        controla en el check-list; esto mide disponibilidad.
      </div>
      <div class="campo">
        <label for="art-equipo">Artefacto</label>
        <select id="art-equipo">${opciones}</select>
      </div>
      <div class="campo">
        <label for="art-cantidad">Cuántos</label>
        <input type="number" id="art-cantidad" min="1" value="1" inputmode="numeric">
      </div>
      <div class="campo">
        <label for="art-desde">Desde</label>
        <input type="date" id="art-desde" value="${UI.esc(fechaControl)}">
      </div>
      <div class="campo">
        <label for="art-hasta">Último día fuera de servicio
          <span class="ayuda">Vacío = sigue clausurado</span></label>
        <input type="date" id="art-hasta">
      </div>
      <div class="campo">
        <label for="art-motivo">Motivo <span class="ayuda">(obligatorio)</span></label>
        <textarea id="art-motivo" rows="2"
                  placeholder="Ej.: pérdida de agua, clausurado"></textarea>
      </div>
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-rojo" data-guardar>Registrar</button>
      </div>`, (hoja, cerrarHoja) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrarHoja;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const equipo = hoja.querySelector('#art-equipo').value;
        const cantidad = parseInt(hoja.querySelector('#art-cantidad').value, 10);
        const desde = hoja.querySelector('#art-desde').value;
        const hasta = hoja.querySelector('#art-hasta').value || null;
        const motivo = hoja.querySelector('#art-motivo').value.trim();
        if (!motivo) return UI.toast('Indicá el motivo', 'error');
        if (!desde) return UI.toast('Indicá desde qué día', 'error');
        if (!(cantidad > 0)) return UI.toast('La cantidad debe ser mayor a cero', 'error');
        if (hasta && hasta < desde) {
          return UI.toast('La reposición no puede ser anterior', 'error');
        }
        try {
          await API.mutar('POST', `/api/controles/${control.id}/artefactos/baja`,
                          { nucleo_id: n.id, equipo, cantidad, desde, hasta, motivo });
        } catch (e) {
          return UI.toast(e.message, 'error', 6000);
        }
        cerrarHoja();
        UI.toast('Artefacto registrado fuera de servicio', 'ok');
        pintarArtefactos(cerrado);
        pintarNovedades();
      };
    });
  }

  /** Clausuras del mes, para reponer o corregir. */
  function hojaClausuras(bajas, cerrado) {
    const filas = bajas.map((b) => `
      <div class="item-pendiente">
        <div class="texto">
          <span class="nombre-item">${UI.esc(b.nucleo)} ·
            ${UI.esc(etiquetaArtefacto(b.equipo))} (${b.cantidad})</span>
          <span class="obs">${UI.esc(b.desde)} →
            ${b.hasta ? UI.esc(b.hasta) : 'sigue clausurado'}</span>
          <span class="obs">${UI.esc(b.motivo || '')}</span>
        </div>
        ${cerrado ? '' : `<button class="btn-texto" data-reponer="${b.id}">
          ${b.hasta ? 'Corregir' : 'Reponer'}</button>`}
      </div>`).join('');

    UI.abrirHoja(`
      <h3>Clausuras del mes</h3>
      <p class="sub">${bajas.length} registrada(s)</p>
      <div class="lista-pendientes">${filas}</div>
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrarHoja) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrarHoja;
      hoja.querySelectorAll('[data-reponer]').forEach((b) => {
        const baja = bajas.find((x) => String(x.id) === b.dataset.reponer);
        b.onclick = () => hojaReponerArtefacto(baja, cerrado);
      });
    });
  }

  function hojaReponerArtefacto(b, cerrado) {
    UI.abrirHoja(`
      <h3>${UI.esc(b.nucleo)} · ${UI.esc(etiquetaArtefacto(b.equipo))}</h3>
      <p class="sub">Clausurado desde ${UI.esc(b.desde)} — ${UI.esc(b.motivo || '')}</p>
      <div class="campo">
        <label for="art-rep">Último día fuera de servicio
          <span class="ayuda">Vacío = sigue clausurado · el día siguiente ya
            cuenta como disponible</span></label>
        <input type="date" id="art-rep" value="${UI.esc(b.hasta || control.fecha)}">
      </div>
      ${usuario.rol === 'admin'
        ? `<button class="btn btn-rojo btn-bloque" data-borrar
                   style="margin-bottom:12px">Eliminar esta clausura</button>` : ''}
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-verde" data-guardar>Guardar</button>
      </div>`, (hoja, cerrarHoja) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrarHoja;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const hasta = hoja.querySelector('#art-rep').value || null;
        if (hasta && hasta < b.desde) {
          return UI.toast('La reposición no puede ser anterior', 'error');
        }
        try {
          await API.put(`/api/artefactos/bajas/${b.id}`,
                        { hasta, reabrir: !hasta });
        } catch (e) {
          return UI.toast(e.message, 'error', 6000);
        }
        UI.cerrarTodas();
        UI.toast('Clausura actualizada', 'ok');
        pintarArtefactos(cerrado);
        pintarNovedades();
      };
      const borrar = hoja.querySelector('[data-borrar]');
      if (borrar) {
        borrar.onclick = async () => {
          const ok = await UI.confirmar(
            'Eliminar la clausura',
            'Los días dejan de contar como fuera de servicio en el ítem 3.1.',
            'Eliminar', 'btn-rojo');
          if (!ok) return;
          try { await API.del(`/api/artefactos/bajas/${b.id}`); }
          catch (e) { return UI.toast(e.message, 'error', 6000); }
          UI.cerrarTodas();
          UI.toast('Clausura eliminada', 'ok');
          pintarArtefactos(cerrado);
          pintarNovedades();
        };
      }
    });
  }

  /* ============================================ equipamiento del control === */

  /**
   * Maquinarias exigidas por el pliego. Va aparte de la grilla de sectores
   * porque no promedia con ellos: alimenta el ítem 4 de la certificación.
   * Como todo el control, funciona por excepción — se asume que están.
   */
  async function pintarEquipamiento(cerrado) {
    const caja = $('#tarjeta-equipamiento');
    if (!caja) return;

    let datos;
    try {
      datos = await API.get(`/api/controles/${control.id}/equipamiento`);
      await Store.set('meta', `cache:equip:${control.id}`, datos);
    } catch (e) {
      datos = await Store.get('meta', `cache:equip:${control.id}`);
      if (!datos) {
        caja.innerHTML = `<h2>Maquinarias y equipos</h2>
          <p style="margin:0;font-size:14px;color:var(--gris)">
            No se pudo cargar. Se puede seguir con el resto del control.</p>`;
        return;
      }
    }

    // "De baja" es un tramo declarado (equipamiento_baja); "fuera de servicio"
    // es la marca suelta del día. Se muestran juntos porque para el auditor
    // son lo mismo, pero solo el tramo sobrevive a los días sin control.
    const inactivo = (e) => e.baja || e.fuera_servicio;
    const fuera = datos.equipos.filter(inactivo);
    const m = datos.mensual || {};
    const disp = m.porcentaje === null || m.porcentaje === undefined
      ? null : Math.round(m.porcentaje * 1000) / 10;

    caja.innerHTML = `
      <h2>Maquinarias y equipos</h2>
      <p style="margin:0 0 4px;font-size:14px;color:var(--gris)">
        ${datos.equipos.length} exigidos por pliego ·
        ${fuera.length ? `${fuera.length} fuera de servicio`
                       : 'todos disponibles'}
      </p>
      <p style="margin:0 0 8px;font-size:14px;color:var(--gris)">
        Disponibilidad del mes: <strong>${disp === null ? 'Sin datos' : disp + '%'}</strong>
        ${m.dias_considerados ? ` · ${m.dias_considerados} día(s) medidos` : ''}
        — incide en el ítem 4 de la certificación
      </p>
      <button class="btn" id="ver-bajas" style="margin-bottom:12px">
        Ver bajas del mes</button>
      <div class="lista-items">
        ${datos.equipos.map((e) => {
          const b = e.baja;
          const detalle = b
            ? `De baja desde ${UI.esc(b.desde)}${
                b.hasta ? ` hasta el ${UI.esc(b.hasta)} inclusive`
                        : ' — sin reposición'}`
            : e.fuera_servicio ? 'Fuera de servicio (solo este día)' : 'Disponible';
          return `
          <button class="item ${inactivo(e) ? 'total' : ''}"
                  data-equipo="${e.id}" ${cerrado ? 'disabled' : ''}>
            <span class="texto">
              <span class="nombre-item">${UI.esc(e.nombre)}</span>
              ${(b && b.motivo) || e.observacion
                ? `<span class="obs">${UI.esc((b && b.motivo) || e.observacion)}</span>`
                : ''}
            </span>
            <span class="estado-item">${detalle}</span>
          </button>`;
        }).join('')}
      </div>`;

    const btnBajas = $('#ver-bajas');
    if (btnBajas) btnBajas.onclick = () => hojaBajasDelMes(cerrado);

    caja.querySelectorAll('[data-equipo]').forEach((b) => {
      b.onclick = () => {
        if (cerrado) return UI.toast('El control está cerrado', 'error');
        const equipo = datos.equipos.find(
          (e) => e.id === parseInt(b.dataset.equipo, 10));
        equipo.baja ? hojaReponerEquipo(equipo, cerrado)
                    : hojaEquipoFueraServicio(equipo, datos.fecha, cerrado);
      };
    });
  }

  /**
   * Baja de una máquina por un tramo de días.
   *
   * Se pide el tramo y no solo "hoy" porque una máquina rota dos semanas
   * obligaba a marcarla catorce veces, y cualquier día sin control cerrado
   * la hacía desaparecer del cálculo mensual. La fecha de reposición puede
   * quedar vacía: la baja sigue vigente y descuenta hasta que se reponga.
   */
  function hojaEquipoFueraServicio(equipo, fechaControl, cerrado) {
    UI.abrirHoja(`
      <h3>${UI.esc(equipo.nombre)}</h3>
      <p class="sub">Registrar días de baja</p>
      <div class="aviso advertencia">
        Los días de baja descuentan del ítem 4 de la certificación, que es
        parte del porcentaje a certificar del contratista.
      </div>
      <div class="campo">
        <label for="eq-desde">Desde</label>
        <input type="date" id="eq-desde" value="${UI.esc(fechaControl)}">
      </div>
      <div class="campo">
        <label for="eq-hasta">Último día fuera de servicio
          <span class="ayuda">Vacío = sigue fuera de servicio</span></label>
        <input type="date" id="eq-hasta">
      </div>
      <div class="campo">
        <label for="eq-obs">Motivo <span class="ayuda">(obligatorio)</span></label>
        <textarea id="eq-obs"
                  placeholder="Ej.: motor quemado, en reparación"></textarea>
      </div>
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-rojo" data-guardar>Registrar baja</button>
      </div>`, (hoja, cerrarHoja) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrarHoja;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const obs = hoja.querySelector('#eq-obs').value.trim();
        const desde = hoja.querySelector('#eq-desde').value;
        const hasta = hoja.querySelector('#eq-hasta').value || null;
        if (!obs) return UI.toast('Indicá el motivo', 'error');
        if (!desde) return UI.toast('Indicá desde qué día está de baja', 'error');
        if (hasta && hasta < desde) {
          return UI.toast('La reposición no puede ser anterior a la baja', 'error');
        }
        cerrarHoja();
        const r = await API.mutar(
          'POST', `/api/controles/${control.id}/equipamiento/${equipo.id}/baja`,
          { desde, hasta, motivo: obs });
        UI.toast(r.encolada ? 'Guardado (se enviará al recuperar la red)'
                            : 'Baja registrada', 'ok');
        Sync.estado().then(pintarChipSync);
        pintarEquipamiento(cerrado);
        pintarNovedades();
      };
    });
  }

  /**
   * Bajas de maquinaria que tocan el mes, para revisarlas y corregirlas.
   *
   * Estos días descuentan del ítem 4 de la certificación, o sea del importe a
   * pagar. Hasta ahora solo se podían cargar: una fecha mal tipeada quedaba
   * incidiendo sobre el pago sin ningún lugar donde encontrarla.
   */
  async function hojaBajasDelMes(cerrado) {
    let datos;
    try {
      datos = await API.get(
        `/api/periodos/${control.periodo}/equipamiento/bajas`);
    } catch (e) {
      return UI.toast(e.message, 'error');
    }

    const filas = datos.bajas.map((b) => `
      <div class="item-pendiente">
        <div class="texto">
          <span class="nombre-item">${UI.esc(b.equipo)}</span>
          <span class="obs">${UI.esc(b.desde)} →
            ${b.hasta ? UI.esc(b.hasta) : 'sin reposición (sigue de baja)'}</span>
          <span class="obs">${UI.esc(b.motivo || '')}</span>
        </div>
        ${cerrado ? '' : `<button class="btn-texto" data-editar-baja="${b.id}">
          Corregir</button>`}
      </div>`).join('');

    UI.abrirHoja(`
      <h3>Bajas de maquinaria</h3>
      <p class="sub">${UI.esc(UI.nombrePeriodo(control.periodo))} —
        ${datos.bajas.length} registrada(s)</p>
      ${datos.bajas.length ? `
        <div class="aviso advertencia">
          Estos días descuentan del ítem 4 de la certificación. Corregir una
          baja cambia el porcentaje a certificar y queda registrado.
        </div>
        <div class="lista-pendientes">${filas}</div>`
        : '<div class="aviso info">No hay bajas registradas en el período.</div>'}
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrarHoja) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrarHoja;
      hoja.querySelectorAll('[data-editar-baja]').forEach((b) => {
        const baja = datos.bajas.find((x) => String(x.id) === b.dataset.editarBaja);
        b.onclick = () => hojaCorregirBaja(baja, cerrado);
      });
    });
  }

  /** Corrige fechas o motivo de una baja ya cargada, o la elimina (admin). */
  function hojaCorregirBaja(b, cerrado) {
    UI.abrirHoja(`
      <h3>Corregir baja</h3>
      <p class="sub">${UI.esc(b.equipo)}</p>
      <div class="campo">
        <label for="baja-desde">De baja desde</label>
        <input type="date" id="baja-desde" value="${UI.esc(b.desde)}">
      </div>
      <div class="campo">
        <label for="baja-hasta">Último día fuera de servicio
          <span class="ayuda">Vacío = sigue fuera de servicio</span></label>
        <input type="date" id="baja-hasta" value="${UI.esc(b.hasta || '')}">
      </div>
      <div class="campo">
        <label for="baja-motivo">Motivo</label>
        <textarea id="baja-motivo" rows="2">${UI.esc(b.motivo || '')}</textarea>
      </div>
      ${usuario.rol === 'admin'
        ? `<button class="btn btn-rojo btn-bloque" data-borrar
                   style="margin-bottom:12px">Eliminar esta baja</button>`
        : `<p style="font-size:13px;color:var(--gris);margin:0 0 12px">
             Eliminar una baja mal cargada requiere un administrador.</p>`}
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-primario" data-guardar>Guardar</button>
      </div>`, (hoja, cerrarHoja) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrarHoja;

      hoja.querySelector('[data-guardar]').onclick = async () => {
        const desde = hoja.querySelector('#baja-desde').value;
        const hasta = hoja.querySelector('#baja-hasta').value || null;
        const motivo = hoja.querySelector('#baja-motivo').value.trim();
        if (!desde) return UI.toast('Indicá desde qué día está de baja', 'error');
        if (!motivo) return UI.toast('Indicá el motivo', 'error');
        if (hasta && hasta < desde) {
          return UI.toast('La reposición no puede ser anterior a la baja', 'error');
        }
        try {
          // `reabrir` vacía la reposición cuando el auditor borró esa fecha.
          await API.put(`/api/equipamiento/bajas/${b.id}`,
                        { desde, hasta, motivo, reabrir: !hasta });
        } catch (e) {
          return UI.toast(e.message, 'error', 6000);
        }
        UI.cerrarTodas();
        UI.toast('Baja corregida', 'ok');
        pintarEquipamiento(cerrado);
        pintarNovedades();
      };

      const borrar = hoja.querySelector('[data-borrar]');
      if (borrar) {
        borrar.onclick = async () => {
          const ok = await UI.confirmar(
            'Eliminar la baja',
            `Se borra la baja de ${b.equipo} del ${b.desde}. Los días dejan de `
            + 'descontar y el porcentaje a certificar sube.',
            'Eliminar', 'btn-rojo');
          if (!ok) return;
          try {
            await API.del(`/api/equipamiento/bajas/${b.id}`);
          } catch (e) {
            return UI.toast(e.message, 'error', 6000);
          }
          UI.cerrarTodas();
          UI.toast('Baja eliminada', 'ok');
          pintarEquipamiento(cerrado);
          pintarNovedades();
        };
      }
    });
  }

  /** Repone la máquina: cierra el tramo de baja el día en que volvió. */
  function hojaReponerEquipo(equipo, cerrado) {
    const b = equipo.baja;
    UI.abrirHoja(`
      <h3>Reponer ${UI.esc(equipo.nombre)}</h3>
      <p class="sub">De baja desde ${UI.esc(b.desde)} — ${UI.esc(b.motivo || '')}</p>
      <div class="campo">
        <label for="eq-rep">Último día fuera de servicio
          <span class="ayuda">El día siguiente ya cuenta como disponible</span></label>
        <input type="date" id="eq-rep" value="${UI.esc(control.fecha)}">
      </div>
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-verde" data-guardar>Marcar disponible</button>
      </div>`, (hoja, cerrarHoja) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrarHoja;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const hasta = hoja.querySelector('#eq-rep').value;
        if (!hasta) return UI.toast('Indicá desde cuándo está disponible', 'error');
        if (hasta < b.desde) {
          return UI.toast('La reposición no puede ser anterior a la baja', 'error');
        }
        cerrarHoja();
        const r = await API.mutar('PUT', `/api/equipamiento/bajas/${b.id}`, { hasta });
        UI.toast(r.encolada ? 'Guardado (se enviará al recuperar la red)'
                            : 'Equipo disponible', 'ok');
        Sync.estado().then(pintarChipSync);
        pintarEquipamiento(cerrado);
        pintarNovedades();
      };
    });
  }

  /**
   * Confirma un sector desde la grilla, sin abrirlo.
   *
   * A diferencia del botón que está dentro del sector, acá no se pide
   * confirmación en un modal: con 9 sectores por día, un diálogo por cada uno
   * anularía el ahorro y terminaría respondiéndose sin leer. La declaración
   * queda igual de registrada (usuario, fecha y hora) y se ofrece deshacer,
   * que es la protección que sirve cuando el error es un toque accidental.
   */
  async function confirmarSector(sectorId) {
    const sector = sectores.find((s) => s.id === sectorId);
    if (!sector) return;

    await Store.confirmarSectorLocal(control.id, sectorId);
    local = await Store.estadoControl(control.id);
    repintarSector(sectorId);
    repintarAvance();
    repintarBarraInferior();

    const r = await API.mutar(
      'POST', `/api/controles/${control.id}/sectores/${sectorId}/confirmar`);
    Sync.estado().then(pintarChipSync);
    await contarConfirmacion();

    UI.toastDeshacer(
      `${sector.nombre}: confirmado${r.encolada ? ' (se enviará al recuperar la red)' : ''}`,
      () => deshacerConfirmacion(sectorId));
  }

  // A partir de cuántas confirmaciones se asume que el auditor ya conoce la
  // regla "sin confirmar no cuenta como 100%" y el aviso puede acortarse.
  const UMBRAL_AVISO_BREVE = 15;

  async function contarConfirmacion() {
    const n = (await Store.get('meta', 'contador_confirmaciones')) || 0;
    await Store.set('meta', 'contador_confirmaciones', n + 1);
  }

  async function yaAprendioLaRegla() {
    const n = (await Store.get('meta', 'contador_confirmaciones')) || 0;
    return n >= UMBRAL_AVISO_BREVE;
  }

  async function deshacerConfirmacion(sectorId) {
    if (!navigator.onLine) {
      return UI.toast('Deshacer requiere conexión. Abrí el sector para revisarlo.',
                      'error');
    }
    try {
      await Sync.sincronizar();     // que no quede la confirmación en cola
      await API.del(`/api/controles/${control.id}/sectores/${sectorId}/confirmar`);
      const est = await Store.estadoControl(control.id);
      delete est.confirmados[sectorId];
      await Store.guardarEstadoControl(control.id, est);
      local = est;
      repintarSector(sectorId);
      repintarAvance();
      repintarBarraInferior();
      UI.toast('Confirmación deshecha', 'ok');
    } catch (e) {
      UI.toast('No se pudo deshacer: ' + e.message, 'error');
    }
  }

  async function cerrarControl() {
    const ok = await UI.confirmar(
      'Cerrar control',
      'Una vez cerrado no se puede editar (solo un administrador puede reabrirlo). '
      + 'Verificá que todos los sectores estén bien cargados.',
      'Cerrar control', 'btn-verde');
    if (!ok) return;

    if (!navigator.onLine) {
      return UI.toast('El cierre del control requiere conexión', 'error');
    }
    try {
      await Sync.sincronizar({ silencioso: false });   // primero, lo pendiente
      await API.post(`/api/controles/${control.id}/cerrar`);
      UI.toast('Control cerrado', 'ok');
      ir('/limpieza');
    } catch (e) {
      UI.toast(e.message, 'error');
    }
  }

  /**
   * Reabre un control cerrado. Solo admin.
   *
   * El historial es inmutable salvo por esta vía: el motivo es obligatorio y
   * queda en auditoria_log con el usuario y la fecha. La acción existía en el
   * servidor desde el principio, pero no había ningún lugar en la app para
   * ejecutarla — el aviso de "cerrado" mencionaba que un admin podía reabrirlo
   * y no ofrecía cómo.
   */
  async function reabrirControl() {
    if (!navigator.onLine) {
      return UI.toast('Reabrir un control requiere conexión', 'error');
    }
    UI.abrirHoja(`
      <h3>Reabrir control</h3>
      <p class="sub">${UI.esc(NOMBRE_TURNO[control.turno] || 'Control')} del
        ${UI.esc(UI.fechaLarga(control.fecha))}</p>
      <div class="aviso advertencia">
        El control vuelve a admitir cambios y su resultado puede variar. Queda
        registrado quién lo reabrió, cuándo y por qué.
      </div>
      <div class="campo">
        <label for="motivo-reabrir">Motivo <span class="ayuda">(obligatorio)</span></label>
        <textarea id="motivo-reabrir" rows="3"
                  placeholder="Ej.: se cargó un desvío en el sector equivocado"></textarea>
      </div>
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-primario" data-guardar>Reabrir</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const motivo = hoja.querySelector('#motivo-reabrir').value.trim();
        if (!motivo) return UI.toast('Indicá el motivo', 'error');
        try {
          await API.post(`/api/controles/${control.id}/reabrir`, { motivo });
        } catch (e) {
          return UI.toast(e.message, 'error', 6000);
        }
        cerrar();
        UI.toast('Control reabierto', 'ok');
        vistaControl(control.id);
      };
    });
  }

  /* ================================================== sector: detalle === */

  async function vistaSector(controlId, sectorClave) {
    const sector = sectores.find((s) => s.clave === sectorClave);
    if (!sector || !controlId) return ir('/limpieza');

    // `control` es una variable en memoria: cualquier reinicio del JS —Android
    // matando la pestaña en segundo plano, el auditor cambiando de app unos
    // minutos, un reload, el service worker activando una versión nueva— la
    // deja vacía. Antes eso expulsaba al auditor a /limpieza sin explicación;
    // con el id en la ruta el control se puede recuperar.
    if (!control || control.id !== controlId) {
      layout(sector.nombre, 'Cargando…', '<div class="vacio">Cargando…</div>',
             { volver: '/limpieza' });
      if (!await cargarControl(controlId)) return errorControlNoCargado();
    }

    const cerrado = control.estado === 'CERRADO';
    const est = estadoSector(sector);
    // El aviso instructivo completo tiene sentido las primeras veces; repetido
    // en los 9 sectores todos los días se vuelve ruido que se deja de leer.
    const breve = await yaAprendioLaRegla();

    const items = sector.items.map((item) => {
      const d = local.desvios[item.id];
      const clase = !d ? '' : d.estado === 'DESVIO_PARCIAL' ? 'parcial'
                  : d.estado === 'DESVIO_TOTAL' ? 'total' : 'no-verificable';
      const etiqueta = !d ? 'Cumple'
                     : d.estado === 'DESVIO_PARCIAL' ? 'Parcial'
                     : d.estado === 'DESVIO_TOTAL' ? 'No cumple' : 'No verif.';
      return `<button class="item ${clase}" data-item="${item.id}">
                <span class="texto">
                  <span class="nombre-item">${UI.esc(item.nombre)}</span>
                  ${d ? `<span class="obs">${UI.esc(d.observacion)}</span>` : ''}
                </span>
                <span class="estado-item">${etiqueta}</span>
              </button>`;
    }).join('');

    layout(sector.nombre, UI.fechaLarga(control.fecha), `
      ${est.confirmado
        ? (breve
          ? `<div class="aviso ${est.desvios ? 'advertencia' : 'info'}">
              ${est.desvios ? `${est.desvios} desvío(s)` : 'Sin novedades'} ·
              ${Calc.porcentaje(est.porcentaje)}
            </div>`
          : `<div class="aviso ${est.desvios ? 'advertencia' : 'info'}">
              <strong>${est.desvios
                ? `Sector confirmado con ${est.desvios} desvío(s)`
                : 'Sector confirmado sin novedades'}</strong>
              ${Calc.porcentaje(est.porcentaje)} de cumplimiento.
              Podés seguir cargando desvíos si encontrás algo más.
            </div>`)
        : (breve
          ? `<div class="aviso info">Sin verificar — confirmá al terminar.</div>`
          : `<div class="aviso info">
              <strong>Sector sin verificar</strong>
              Cargá acá los desvíos que encuentres y confirmá el sector cuando
              termines de recorrerlo, con el botón de abajo. Un sector sin
              confirmar no cuenta como 100%: queda sin datos.
            </div>`)}

      <h2 style="font-size:15px;margin:0 0 10px;color:var(--gris)">
        Tocá un ítem solo si presenta un desvío
      </h2>
      <div class="lista-items">${items}</div>`,
      {
        volver: `/control/${control.id}`,
        // Antes este botón decía "Volver y confirmar sector" y solo navegaba:
        // el auditor creía haber confirmado, volvía a la grilla y el sector
        // seguía gris. Confirma de verdad, y vuelve a la grilla — nunca salta
        // solo al siguiente sector. El ← de la barra queda para salir sin
        // confirmar.
        inferior: cerrado ? '' : `
          <button class="btn btn-primario" style="flex:1"
                  id="btn-confirmar-volver">
            ${UI.esc(etiquetaConfirmar(est))}
          </button>`,
      });

    document.querySelectorAll('[data-item]').forEach((b) => {
      b.onclick = () => {
        if (cerrado) return UI.toast('El control está cerrado', 'error');
        const item = sector.items.find((i) => i.id === parseInt(b.dataset.item, 10));
        hojaDesvio(sector, item);
      };
    });

    const btnVolver = $('#btn-confirmar-volver');
    if (btnVolver) btnVolver.onclick = () => confirmarYVolver(sector, est);
  }

  /**
   * Qué dice el botón del pie del sector.
   *
   * Siempre termina en la grilla. Antes encadenaba al siguiente sector del
   * catálogo —"Confirmar y seguir →"— y eso decidía por el auditor: la
   * terminal se camina en orden físico, no en el orden en que están cargados
   * los sectores. La grilla es la parada intermedia desde la que él elige.
   *
   * La etiqueta dice qué se está confirmando y no solo "Confirmar sector":
   * confirmar es una declaración explícita que queda logueada, y no es lo
   * mismo declarar que no había nada que declarar que había tres hallazgos.
   */
  function etiquetaConfirmar(est) {
    if (est.confirmado) return 'Volver a la grilla';
    return est.desvios
      ? `Confirmar con ${est.desvios} desvío(s) y volver`
      : 'Confirmar sin novedades y volver';
  }

  async function confirmarYVolver(sector, est) {
    // Un sector ya confirmado no se reconfirma: el botón solo vuelve.
    if (!est.confirmado) await confirmarSector(sector.id);
    ir(`/control/${control.id}`);

    // Avisar recién cuando no queda ninguno: es el momento en que la recorrida
    // dejó de tener pasos y lo único pendiente es cerrar el control.
    if (sectores.every((s) => local.confirmados[s.id])) {
      UI.toast('Todos los sectores confirmados — falta cerrar el control', 'ok');
    }
  }

  /* ==================================================== hoja de desvío === */

  /**
   * Carga de un desvío.
   *
   * El orden es severidad → foto → observación, no el original
   * severidad → observación → foto. Dos razones: el auditor está parado frente
   * al desvío y lo natural es fotografiarlo mientras lo tiene delante; y en
   * vertical el teclado se lleva ~45% de la pantalla, así que el campo que lo
   * abre tiene que ser el último — con el orden viejo, entre el textarea y
   * Guardar quedaba todo el bloque de fotos, fuera del área visible.
   */
  function hojaDesvio(sector, item) {
    const actual = local.desvios[item.id];
    let estado = actual ? actual.estado : null;
    let fotos = [];
    // La evidencia que ya tenía el desvío. Mostrarla no es un adorno: sin esto
    // la tira aparecía vacía al reabrir, el auditor volvía a fotografiar lo
    // mismo y el backend guardaba las dos, así que el hallazgo terminaba
    // duplicado en el informe del mes.
    const guardadas = (actual && actual.fotos) || [];

    /** ¿Se puede abandonar la hoja sin guardar? */
    const puedeDescartarse = () => {
      const campo = document.querySelector('#modal #obs');
      const escrito = campo ? campo.value.trim() : '';
      const original = actual ? (actual.observacion || '') : '';
      if (escrito === original && !fotos.length) return true;
      return UI.confirmar(
        'Descartar el desvío',
        'Se pierde lo cargado en esta hoja'
        + (fotos.length ? `, incluidas ${fotos.length} foto(s)` : '') + '.',
        'Descartar', 'btn-rojo');
    };

    UI.abrirHoja(`
      <h3>${UI.esc(item.nombre)}</h3>
      <p class="sub">${UI.esc(sector.nombre)}</p>

      <div class="campo">
        <label>¿Qué encontraste? <span class="ayuda">(elegí una opción)</span></label>
        <div class="opciones" id="opciones" role="radiogroup" aria-required="true">
          <button class="opcion" data-estado="DESVIO_PARCIAL" role="radio"
                  aria-checked="false">
            <span class="icono">◑</span>
            <span><strong>Desvío parcial</strong>
              <small>Cumple a medias — descuenta 50%</small></span>
          </button>
          <button class="opcion" data-estado="DESVIO_TOTAL" role="radio"
                  aria-checked="false">
            <span class="icono">✕</span>
            <span><strong>No cumple</strong>
              <small>Desvío total — descuenta 100%</small></span>
          </button>
          <button class="opcion" data-estado="NO_VERIFICABLE" role="radio"
                  aria-checked="false">
            <span class="icono">⊘</span>
            <span><strong>No verificable hoy</strong>
              <small>Se excluye del cálculo — requiere motivo</small></span>
          </button>
        </div>
      </div>

      <div class="campo" id="campo-fotos">
        <label>Evidencia fotográfica</label>
        ${guardadas.length ? `<span class="ayuda">${guardadas.length} ya
          cargada(s) — tocá para ampliar</span>
          <div class="fotos" id="fotos-guardadas"></div>` : ''}
        <div class="fotos" id="fotos">
          <button class="btn-foto" id="btn-foto" type="button">
            <span class="icono" aria-hidden="true">📷</span>
            <span class="txt">Tomar foto</span>
          </button>
        </div>
      </div>

      <div class="campo">
        <label for="obs">Observación <span class="ayuda">(obligatoria)</span></label>
        <textarea id="obs" placeholder="Describí el hallazgo…"
                  >${UI.esc(actual ? actual.observacion : '')}</textarea>
      </div>

      <div class="acciones">
        ${actual ? '<button class="btn btn-rojo" data-quitar>Quitar desvío</button>'
                 : '<button class="btn" data-cancelar>Cancelar</button>'}
        <button class="btn btn-primario" data-guardar disabled>Guardar</button>
      </div>`, (hoja, cerrar) => {

      const btnGuardar = hoja.querySelector('[data-guardar]');
      const obs = hoja.querySelector('#obs');
      UI.galeria(hoja.querySelector('#fotos-guardadas'), guardadas,
                 { titulo: item.nombre });
      // Deshabilitado hasta elegir severidad y describir el hallazgo: antes no
      // había ninguna señal de que fueran obligatorios hasta apretar Guardar y
      // recibir un toast de error.
      const actualizarGuardar = () => {
        btnGuardar.disabled = !estado || !obs.value.trim();
      };
      obs.addEventListener('input', actualizarGuardar);

      const pintarSeleccion = () => {
        hoja.querySelectorAll('.opcion').forEach((b) => {
          const sel = b.dataset.estado === estado;
          b.setAttribute('aria-checked', String(sel));
          b.className = 'opcion' + (sel
            ? b.dataset.estado === 'DESVIO_PARCIAL' ? ' sel-parcial'
            : b.dataset.estado === 'DESVIO_TOTAL' ? ' sel-total' : ' sel-nv'
            : '');
        });
        // "No verificable" no es un incumplimiento: no exige foto.
        hoja.querySelector('#campo-fotos').style.display =
          estado === 'NO_VERIFICABLE' ? 'none' : '';
        actualizarGuardar();
      };
      pintarSeleccion();

      hoja.querySelectorAll('.opcion').forEach((b) => {
        b.onclick = () => { estado = b.dataset.estado; pintarSeleccion(); };
      });

      hoja.querySelector('#btn-foto').onclick = async () => {
        const dataUrl = await UI.tomarFoto();
        if (!dataUrl) return;
        fotos.push(dataUrl);
        const prev = document.createElement('div');
        prev.className = 'foto-prev';
        prev.innerHTML = `<img alt="Evidencia"><button type="button"
                          aria-label="Quitar foto">×</button>`;
        prev.querySelector('img').src = dataUrl;
        prev.querySelector('button').onclick = () => {
          fotos = fotos.filter((f) => f !== dataUrl);
          prev.remove();
        };
        hoja.querySelector('#fotos').insertBefore(prev, hoja.querySelector('#btn-foto'));
      };

      const cancelar = hoja.querySelector('[data-cancelar]');
      if (cancelar) cancelar.onclick = cerrar;

      const quitar = hoja.querySelector('[data-quitar]');
      if (quitar) quitar.onclick = async () => {
        UI.cerrarHoja({ forzar: true });
        await Store.registrarDesvioLocal(control.id, item.id, null);
        local = await Store.estadoControl(control.id);
        vistaSector(control.id, sector.clave);
        UI.toast('Desvío quitado', 'ok');
      };

      hoja.querySelector('[data-guardar]').onclick = async () => {
        const texto = obs.value.trim();
        // El botón ya está disabled en estos casos; la guarda queda por si el
        // estado se toca por otra vía.
        if (!estado || !texto) return;
        if (estado !== 'NO_VERIFICABLE' && !fotos.length) {
          const seguir = await UI.confirmar(
            'Desvío sin foto',
            'El hallazgo no tiene evidencia fotográfica. Se puede guardar igual, '
            + 'pero el informe lo va a mostrar como pendiente de evidencia.',
            'Guardar sin foto');
          if (!seguir) return;
        }

        // Guardar no es abandonar: se salta la guarda de descarte.
        UI.cerrarHoja({ forzar: true });
        await Store.registrarDesvioLocal(control.id, item.id,
                                         { estado, observacion: texto });
        local = await Store.estadoControl(control.id);
        // Se vuelve a la lista de ítems del sector, no a la grilla.
        //
        // Se probó devolver a la grilla para que el auditor eligiera el
        // siguiente sector, y es peor: el sector con varios hallazgos es lo
        // habitual, y expulsarlo después de cada uno lo obliga a volver a
        // entrar por cada ítem. La unidad de trabajo es el sector completo,
        // no el desvío suelto: se sale de acá cuando se termina de recorrerlo.
        vistaSector(control.id, sector.clave);

        const r = await API.mutar('POST', `/api/controles/${control.id}/desvios`, {
          item_id: item.id, estado, observacion: texto, fotos,
        });
        UI.toast(r.encolada ? 'Guardado (se enviará al recuperar la red)'
                            : 'Desvío registrado', 'ok');
        Sync.estado().then(pintarChipSync);
      };
    }, { alIntentarCerrar: puedeDescartarse });
  }

  return { iniciar };
})();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((e) => {
      // Sin service worker la app funciona online, así que no vale interrumpir
      // al usuario. Pero sí queda registrado en consola: si esto falla, el
      // dispositivo pierde el modo sin conexión entero —la razón por la que
      // esta app es una PWA— y hasta ahora ocurría en silencio absoluto, sin
      // manera de enterarse ni de diagnosticarlo sobre una tablet en la
      // terminal. Diagnosticar un "no me abre sin señal" empieza por acá.
      console.warn('No se pudo registrar el service worker:', (e && e.message) || e);
    });
  });

  /* Aviso de versión nueva.
   *
   * El worker sirve el shell desde la caché para que la app abra al instante y
   * revalida por detrás. Cuando esa revalidación encuentra que el código
   * cambió, avisa acá. No se recarga sola a propósito: la recarga en medio de
   * una recorrida se lleva puesto el desvío que el auditor está escribiendo.
   * El toast no vence, así que el aviso lo espera hasta que pueda atenderlo, y
   * si nunca lo toca la versión nueva entra igual en la próxima apertura.
   */
  let avisoVersion = null;
  navigator.serviceWorker.addEventListener('message', (e) => {
    if (!e.data || e.data.tipo !== 'shell-actualizado' || avisoVersion) return;
    avisoVersion = UI.toastAccion(
      'Hay una versión nueva de la app.', 'Actualizar', () => location.reload());
  });
}

/* Red de contención del arranque.
 *
 * Sin esto, cualquier excepción en iniciar() deja la pantalla de "Cargando…"
 * para siempre: el auditor no sabe si esperar, y en plataforma no tiene a
 * quién preguntar. Se muestra el error y una salida que no destruya la cola
 * de operaciones pendientes. */
App.iniciar().catch((e) => {
  document.getElementById('app').className = '';
  document.getElementById('app').innerHTML = `
    <div class="login">
      <div class="marca"><div class="splash-logo">IRJ</div></div>
      <div class="aviso error">
        <strong>No se pudo iniciar la aplicación</strong>
        ${UI.esc(e && e.message ? e.message : String(e))}
      </div>
      <p style="color:var(--gris);font-size:14px">
        Los datos cargados en la tablet no se perdieron: siguen guardados y se
        envían cuando la app vuelva a abrir correctamente.
      </p>
      <button class="btn btn-primario btn-bloque btn-grande" id="reintentar">
        Reintentar
      </button>
    </div>`;
  document.getElementById('reintentar').onclick = () => location.reload();
});
