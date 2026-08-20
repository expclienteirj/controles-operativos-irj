/* Módulo LoS — Niveles de Servicio (manual REV.02, parámetros IRJ).
 *
 * Dashboard con los 11 ítems y un formulario por ítem. Igual que en Limpieza,
 * el cálculo definitivo lo hace el servidor: acá solo se arma el payload y se
 * muestra el resultado que devuelve.
 *
 * Dos ítems escapan a la lógica por excepción y piden medición siempre
 * (confort térmico y GEL), porque son valores medidos, no hallazgos. */

const LoS = (() => {
  let periodo = null;
  let relevamiento = null;      // { id, estado, ... } o null
  let mediciones = {};          // { item_clave: { datos, resultado, cumple } }
  let items = [];
  // Día que se está relevando en un ítem diario. null en los mensuales: el
  // servidor rechaza una fecha en un ítem que no la admite.
  let fechaItem = null;
  let fotosItem = [];           // evidencia ya guardada del ítem que se abre

  const $ = (sel) => document.querySelector(sel);

  /* ======================================================== dashboard === */

  /**
   * `itemClave` (opcional) es el ítem al que hay que entrar directamente, y
   * llega por la ruta (#/los/pista_rodajes). Lo usa el centro de novedades
   * cuando hay un único ítem incumpliendo: dejar al auditor en el tablero lo
   * obligaba a buscar a ojo cuál de los once era el que lo trajo hasta acá.
   */
  /**
   * Lectura tolerante a la red, como en Inicio y Limpieza.
   *
   * Esta era la única pantalla que pedía sus datos con `API.get` pelado, que
   * es un `fetch` sin red de contención: cualquier corte —lo habitual en una
   * tablet recorriendo la terminal— la dejaba en blanco con el mensaje crudo
   * del navegador ("Failed to fetch"). La incoherencia era doble, porque la
   * carga de mediciones sí sabe encolarse y seguir sin conexión: la pantalla
   * podía escribir offline pero no podía abrirse.
   *
   * `estado.desdeCache` se marca para poder decirlo en pantalla. Mostrar un
   * "no cumple" de hace tres días como si fuera de ahora sería peor que no
   * mostrar nada: de estos estados sale la certificación del mes.
   */
  async function leerConCache(ruta, clave, estado) {
    try {
      const datos = await API.get(ruta);
      await Store.set('meta', clave, datos);
      return datos;
    } catch (e) {
      const guardado = await Store.get('meta', clave);
      if (!guardado) throw e;
      estado.desdeCache = true;
      return guardado;
    }
  }

  function avisarSinRed() {
    const caja = $('.contenido');
    if (!caja) return;
    caja.insertAdjacentHTML('afterbegin', `
      <div class="aviso advertencia">
        <strong>Sin conexión — datos de la última sincronización</strong>
        Lo que cargues se guarda y se envía al recuperar la red, pero los
        estados de cumplimiento pueden no reflejar lo último relevado.
      </div>`);
  }

  async function vista(layout, ir, itemClave) {
    periodo = UI.periodoActual();
    layout('Niveles de Servicio', UI.nombrePeriodo(periodo),
           '<div class="vacio">Cargando…</div>', { volver: '/' });

    const estado = { desdeCache: false };
    try {
      const [dash, actual, listaItems] = await Promise.all([
        leerConCache(`/api/los/dashboard?periodo=${periodo}`,
                     `cache:los-dashboard:${periodo}`, estado),
        leerConCache(`/api/los/relevamientos/actual?periodo=${periodo}`,
                     `cache:los-relevamiento:${periodo}`, estado),
        leerConCache('/api/los/items', 'cache:los-items', estado),
      ]);
      relevamiento = actual.relevamiento;
      mediciones = actual.mediciones || {};
      items = listaItems.items;
      pintarDashboard(dash, layout, ir);
      if (estado.desdeCache) avisarSinRed();
      // El tablero ya pintó sus botones, así que alcanza con accionar el del
      // ítem pedido: hereda tal cual su comportamiento (solo lectura, inerte,
      // hoja de carga) sin duplicar acá ninguna de esas reglas.
      if (itemClave) {
        const boton = Array.from(document.querySelectorAll('[data-item]'))
          .find((b) => b.dataset.item === itemClave);
        if (boton) boton.click();
      }
    } catch (e) {
      // Sin red y sin copia local. Un error del servidor trae `codigo` y su
      // mensaje sirve; una caída de red no lo trae, y "Failed to fetch" no le
      // dice nada a un auditor parado en la terminal.
      $('.contenido').innerHTML = `<div class="aviso error">${e.codigo
        ? `No se pudo cargar: ${UI.esc(e.message)}`
        : 'Sin conexión y sin datos guardados de este mes. Se necesita red '
          + 'para abrir Niveles de Servicio la primera vez; después queda '
          + 'disponible sin conexión.'}</div>`;
    }
  }

  /**
   * Dashboard organizado por ritmo de trabajo, no por el orden del manual.
   *
   * Los 11 ítems tienen tres cadencias distintas y mezclarlos en una grilla
   * obligaba al auditor a traducir "ítem 3.3" a "¿esto me toca hoy?". Ahora la
   * pantalla responde esa pregunta primero: arriba la recorrida del día, abajo
   * las mediciones del mes, y al pie lo que no se carga a mano.
   *
   * No hay un botón que releve los cuatro ítems diarios de una: están en
   * lugares físicamente distintos de la terminal y resolverlos juntos sería
   * declarar sin haber caminado.
   */
  function pintarDashboard(dash, layout, ir) {
    const cerrado = relevamiento && relevamiento.estado === 'CERRADO';
    const porPeriodicidad = (p) => dash.items.filter(
      (i) => i.periodicidad === p && i.aplica);

    const diarios = porPeriodicidad('DIARIO');
    const mensuales = porPeriodicidad('MENSUAL');
    const porEvento = porPeriodicidad('POR_EVENTO');
    const derivados = porPeriodicidad('DERIVADO');
    const noAplican = dash.items.filter((i) => !i.aplica);

    const relevadosHoy = diarios.filter((i) => i.diario && i.diario.relevado_hoy);
    const pct = dash.porcentaje === null ? '—'
              : Math.round(dash.porcentaje * 100) + '%';

    layout('Niveles de Servicio', UI.nombrePeriodo(periodo), `
      ${cerrado ? `<div class="aviso info">
        <strong>Relevamiento cerrado</strong>
        Cerrado el ${UI.esc(UI.fecha(relevamiento.cerrado_en))}. No admite cambios.
        ${API.esAdmin()
          ? '<button class="btn" id="btn-reabrir-los" style="margin-top:10px">Reabrir relevamiento</button>'
          : ''}
      </div>` : ''}

      ${dash.requieren_configuracion.length ? `
        <div class="aviso advertencia">
          <strong>${dash.requieren_configuracion.length} ítem(s) sin inventario cargado</strong>
          No se pueden relevar y quedan como Sin datos — nunca como 100%.
          Se cargan desde Configuración → Inventario.
        </div>` : ''}

      ${seccionDiaria(diarios, relevadosHoy, cerrado)}
      ${calendarioDiario(dash)}
      ${seccionMensual(mensuales)}
      ${seccionPorEvento(porEvento)}
      ${seccionDerivada(derivados)}

      <div class="tarjeta">
        <h2>Cumplimiento del período</h2>
        <p style="margin:0 0 6px;font-size:32px;font-weight:700">${pct}</p>
        <p style="margin:0;font-size:14px;color:var(--gris)">
          ${dash.items_cumplen} de ${dash.items_evaluados} ítem(s) evaluado(s)
          cumplen${dash.items_sin_datos.length
            ? ` · ${dash.items_sin_datos.length} todavía sin datos` : ''}.
          Se calcula solo sobre lo relevado${noAplican.length
            ? `; ${noAplican.length} ítem(s) no aplican en IRJ` : ''}.
        </p>
      </div>`,
      { volver: '/' });

    conectarDashboard(dash, layout, ir, cerrado);
  }

  /* ------------------------------------------------ calendario diario --- */

  /**
   * Los ítems que este calendario mide: SOLO periodicidad DIARIO.
   *
   * Los DERIVADO se calculan con lo que el auditor ya cargó en el check-list de
   * limpieza y no representan trabajo de relevamiento de LoS: incluirlos haría
   * que este calendario se pusiera verde solo por hacer la recorrida del otro
   * módulo, y el indicador de progreso de LoS mostraría el avance de Limpieza.
   *
   * Lista blanca explícita, nunca un filtro por exclusión (`!== 'MENSUAL'`):
   * por exclusión entra cualquier periodicidad que se agregue después.
   */
  const itemsDiarios = (dash) => dash.items.filter(
    (i) => i.periodicidad === 'DIARIO' && i.aplica);

  /**
   * Calendario de la recorrida diaria de Niveles de Servicio.
   *
   * Es independiente del calendario del check-list de limpieza: se alimenta
   * solo de `/api/los/dashboard`, mantiene su propio período y ningún día puede
   * cambiar de color por algo cargado en el otro módulo.
   *
   * El fondo codifica CUÁNTO se relevó, no si el contratista cumplió: un día
   * completo con un ítem que falla se ve verde con punto rojo. Si se pintara
   * rojo, el mismo color significaría "no se auditó" en un calendario y "se
   * auditó y falló" en el otro.
   */
  function calendarioDiario(dash) {
    const diarios = itemsDiarios(dash);
    if (!diarios.length) return '';

    const total = diarios.length;
    const relevados = {};      // fecha -> cuántos ítems diarios se relevaron
    const incumplen = {};      // fecha -> algún ítem diario no cumplió
    diarios.forEach((i) => {
      const d = i.diario || {};
      (d.dias_relevados || []).forEach((f) => {
        relevados[f] = (relevados[f] || 0) + 1;
      });
      (d.dias_incumplen || []).forEach((f) => { incumplen[f] = true; });
    });

    const hoy = UI.hoyISO();
    // Sin estado "abierto": el relevamiento de LoS es mensual, no hay día a
    // medio cerrar. Cuatro estados contra los cinco de Limpieza.
    const estado = (fecha) => {
      const n = relevados[fecha] || 0;
      if (n >= total) return 'ok';
      if (n) return 'parcial';
      // Hoy todavía está en plazo: nunca se pinta como faltante.
      return fecha < hoy ? 'falta' : 'futuro';
    };
    const titulo = (fecha) => {
      const n = relevados[fecha] || 0;
      const base = n >= total ? `Los ${total} ítems diarios relevados`
                 : n ? `${n} de ${total} ítems relevados`
                 : fecha < hoy ? 'Día pasado sin relevar' : 'Aún en plazo';
      return base + (incumplen[fecha] ? ' · algún ítem no cumple' : '');
    };

    const completos = Object.keys(relevados).filter((f) => relevados[f] >= total).length;
    const conAlgo = Object.keys(relevados).length;

    return `
      <div class="tarjeta">
        <h2>Avance del mes — recorrida diaria</h2>
        <p style="margin:0;font-size:14px;color:var(--gris)">
          ${completos} día(s) con los ${total} ítems relevados${
            conAlgo > completos ? ` · ${conAlgo - completos} incompleto(s)` : ''}.
          El color indica cuánto se relevó; el punto rojo, que algún ítem no cumple.
        </p>
        <div id="cal-los">${UI.calendarioMes(periodo, {
          estado,
          titulo,
          marca: (fecha) => !!incumplen[fecha],
          accion: 'dia-los',
          compacto: true,
          leyenda: [
            { clase: 'dia-ok', texto: `${total} ítems` },
            { clase: 'dia-parcial', texto: 'Incompleto' },
            { clase: 'dia-falta', texto: 'Sin relevar' },
            { clase: 'dia-futuro', texto: 'En plazo' },
          ],
        })}</div>
      </div>`;
  }

  /**
   * Los ítems diarios de una fecha concreta.
   *
   * Tocar un día de este calendario nunca lleva a un control de limpieza: los
   * dos calendarios muestran los mismos 31 números y el auditor no debería
   * descubrir que tocó el equivocado apareciendo en el otro módulo.
   */
  function hojaDiaLoS(fecha, dash, layout, ir, cerrado) {
    const diarios = itemsDiarios(dash);
    const volver = () => vista(layout, ir);

    const fila = (i) => {
      const d = i.diario || {};
      const hecho = (d.dias_relevados || []).includes(fecha);
      const falla = (d.dias_incumplen || []).includes(fecha);
      const bloqueado = i.requiere_configuracion;
      return `
        <div class="item-pendiente">
          <div class="texto">
            <span class="nombre-item">${UI.esc(i.nombre)}</span>
            <span class="obs">${bloqueado ? 'Requiere cargar inventario'
                              : hecho ? (falla ? 'No cumple' : 'Cumple')
                              : 'Sin relevar'}</span>
          </div>
          ${bloqueado ? '' : `
            <button class="btn-texto" data-abrir-item="${UI.esc(i.clave)}">
              ${hecho ? 'Editar' : 'Cargar'}</button>
            ${hecho ? '' : `<button class="btn-texto"
                                    data-sin-novedad-dia="${UI.esc(i.clave)}">
                              Sin novedades</button>`}`}
        </div>`;
    };

    UI.abrirHoja(`
      <h3>${UI.esc(UI.fechaLarga(fecha))}</h3>
      <p class="sub">Recorrida diaria de Niveles de Servicio</p>
      <div class="lista-pendientes">${diarios.map(fila).join('')}</div>
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrar;

      hoja.querySelectorAll('[data-abrir-item]').forEach((b) => {
        b.onclick = async () => {
          if (cerrado) return UI.toast('El relevamiento está cerrado', 'error');
          cerrar();
          fechaItem = fecha;
          const previo = await medicionDelDia(b.dataset.abrirItem, fecha);
          abrirFormulario(b.dataset.abrirItem, previo, volver);
        };
      });

      hoja.querySelectorAll('[data-sin-novedad-dia]').forEach((b) => {
        b.onclick = () => {
          if (cerrado) return UI.toast('El relevamiento está cerrado', 'error');
          cerrar();
          marcarSinNovedad(b.dataset.sinNovedadDia, fecha, volver);
        };
      });
    });
  }

  /* -------------------------------------------------- sección diaria --- */

  function seccionDiaria(items, relevadosHoy, cerrado) {
    if (!items.length) return '';

    const fila = (i) => {
      const d = i.diario || {};
      const bloqueado = i.requiere_configuracion;
      const hecho = !!d.relevado_hoy;
      // El estado que importa en la recorrida es el de HOY. El acumulado del
      // mes va como dato secundario, no como titular.
      const estadoHoy = bloqueado ? 'Requiere cargar inventario'
                      : hecho ? (d.cumple_hoy === false ? 'Hoy: no cumple'
                                                        : 'Hoy: sin novedades')
                      : 'Hoy: pendiente';
      const mes = d.dias_incumplen && d.dias_incumplen.length
        ? `${d.dias_relevados.length} día(s) en el mes · falla ${d.dias_incumplen.length}`
        : `${(d.dias_relevados || []).length} día(s) relevado(s) en el mes`;

      return `<div class="fila-dia ${bloqueado ? 'bloqueado'
                                    : hecho ? (d.cumple_hoy === false ? 'total' : '')
                                    : 'parcial'}">
        <button class="fila-dia-abrir" data-item-diario="${UI.esc(i.clave)}">
          <span class="texto">
            <span class="nombre-item">${UI.esc(i.nombre)}</span>
            <span class="obs">${UI.esc(estadoHoy)} · ${UI.esc(mes)}</span>
          </span>
          <span class="estado-item">${hecho ? 'Editar' : 'Cargar novedad'}</span>
        </button>
        ${cerrado || bloqueado || hecho ? '' : `
          <button class="btn-sector-ok" data-hoy-ok="${UI.esc(i.clave)}">
            SIN NOVEDADES</button>`}
      </div>`;
    };

    return `
      <div class="tarjeta">
        <h2>Recorrida de hoy</h2>
        <p style="margin:0 0 12px;font-size:14px;color:var(--gris)">
          ${relevadosHoy.length} de ${items.length} relevados ·
          se recorren todos los días
        </p>
        <div class="lista-items">${items.map(fila).join('')}</div>
        <button class="btn" id="ver-otros-dias" style="margin-top:12px">
          Ver otros días del mes</button>
      </div>`;
  }

  /* ------------------------------------------------- sección mensual --- */

  function seccionMensual(items) {
    if (!items.length) return '';
    const faltan = items.filter((i) => i.estado === 'SIN_DATOS'
                                       && !i.requiere_configuracion);

    return `
      <div class="tarjeta">
        <h2>Mediciones del mes</h2>
        <p style="margin:0 0 12px;font-size:14px;color:var(--gris)">
          ${faltan.length
            ? `Faltan ${faltan.length} de ${items.length}: ` +
              faltan.map((i) => i.nombre.toLowerCase()).join(', ')
            : `Las ${items.length} mediciones del mes están cargadas`}
          · se hacen una vez al mes
        </p>
        <div class="grilla">${items.map((i) => tarjetaItem(i)).join('')}</div>
      </div>`;
  }

  function seccionPorEvento(items) {
    if (!items.length) return '';
    return `
      <div class="tarjeta">
        <h2>Registro por evento</h2>
        <p style="margin:0 0 12px;font-size:14px;color:var(--gris)">
          Se cargan cuando ocurren y el cumplimiento sale del acumulado del mes
        </p>
        <div class="grilla">${items.map((i) => tarjetaItem(i)).join('')}</div>
      </div>`;
  }

  /* ------------------------------------------------ sección derivada --- */

  /**
   * Ítems que salen del check-list diario de limpieza. No se cargan acá y por
   * eso no se comportan como los demás: se abren en modo lectura, para ver de
   * dónde sale el resultado. Antes eran tarjetas idénticas a las editables y
   * el auditor terminaba cargando dos veces lo mismo.
   */
  function seccionDerivada(items) {
    if (!items.length) return '';
    return `
      <div class="tarjeta">
        <h2>Automáticos del check-list</h2>
        <p style="margin:0 0 12px;font-size:14px;color:var(--gris)">
          Se calculan con lo que ya cargaste en el check-list diario de
          limpieza. No hay nada que relevar acá.
        </p>
        <div class="grilla">${items.map((i) => tarjetaItem(i, true)).join('')}</div>
      </div>`;
  }

  /* ---------------------------------------------------------- tarjeta --- */

  function tarjetaItem(i, soloLectura = false) {
    const bloqueado = i.requiere_configuracion;
    const clase = !i.aplica ? 'no-aplica'
                : bloqueado ? 'pendiente'
                : i.estado === 'CUMPLE' ? 'sin-novedades'
                : i.estado === 'NO_CUMPLE' ? 'con-desvios' : 'pendiente';
    const marca = !i.aplica ? '—'
                : bloqueado ? '⚙'
                : i.estado === 'CUMPLE' ? '✓'
                : i.estado === 'NO_CUMPLE' ? '✕' : '·';
    const detalle = !i.aplica ? 'No aplica en IRJ'
                  : bloqueado ? 'Requiere cargar inventario'
                  : i.estado === 'CUMPLE' ? 'Cumple'
                  : i.estado === 'NO_CUMPLE' ? 'No cumple'
                  : 'Sin datos del período';

    return `<button class="sector ${clase}" data-item="${UI.esc(i.clave)}"
                    ${soloLectura ? 'data-solo-lectura="1"' : ''}
                    ${!i.aplica || bloqueado ? 'data-inerte="1"' : ''}>
              <span class="marca">${marca}</span>
              <span class="nombre">${UI.esc(i.nombre)}</span>
              <span class="detalle">${UI.esc(detalle)}${
                soloLectura ? ' · ver cómo se calcula' : ''}</span>
            </button>`;
  }

  /* ------------------------------------------------------- conexiones --- */

  function conectarDashboard(dash, layout, ir, cerrado) {
    const btnReabrir = $('#btn-reabrir-los');
    if (btnReabrir) btnReabrir.onclick = () => reabrirRelevamiento(layout, ir);

    const buscar = (clave) => dash.items.find((x) => x.clave === clave);

    // Ítem diario: se releva el día de hoy sin pasar por la lista de días.
    document.querySelectorAll('[data-item-diario]').forEach((b) => {
      b.onclick = async () => {
        const i = buscar(b.dataset.itemDiario);
        if (i.requiere_configuracion) {
          return UI.toast('Cargá el inventario de este ítem desde Configuración', '');
        }
        if (cerrado) return UI.toast('El relevamiento está cerrado', 'error');
        fechaItem = dash.fecha;
        const previo = await medicionDelDia(i.clave, fechaItem);
        abrirFormulario(i.clave, previo, () => vista(layout, ir));
      };
    });

    document.querySelectorAll('[data-hoy-ok]').forEach((b) => {
      b.onclick = () => {
        if (cerrado) return UI.toast('El relevamiento está cerrado', 'error');
        marcarSinNovedad(b.dataset.hoyOk, dash.fecha, () => vista(layout, ir));
      };
    });

    const otrosDias = $('#ver-otros-dias');
    if (otrosDias) otrosDias.onclick = () => hojaElegirItemDiario(dash, layout, ir);

    // Dentro de su propio contenedor y con su propio atributo: el calendario de
    // Limpieza usa `data-dia` y consultar sobre `document` los engancharía a
    // los dos.
    const cal = $('#cal-los');
    if (cal) {
      cal.querySelectorAll('[data-dia-los]').forEach((b) => {
        b.onclick = () => hojaDiaLoS(b.dataset.diaLos, dash, layout, ir, cerrado);
      });
    }

    document.querySelectorAll('[data-item]').forEach((b) => {
      b.onclick = () => {
        const i = buscar(b.dataset.item);
        if (b.dataset.soloLectura) {
          return hojaDerivado(i);
        }
        if (b.dataset.inerte) {
          return UI.toast(i.requiere_configuracion
            ? 'Cargá el inventario de este ítem desde Configuración'
            : 'Este ítem está marcado como No aplica', '');
        }
        if (cerrado) return UI.toast('El relevamiento está cerrado', 'error');
        abrirItem(b.dataset.item, layout, ir, dash);
      };
    });
  }

  /** Elegir qué ítem diario cargar en un día distinto de hoy. */
  function hojaElegirItemDiario(dash, layout, ir) {
    const diarios = dash.items.filter((i) => i.periodicidad === 'DIARIO'
                                             && i.aplica
                                             && !i.requiere_configuracion);
    UI.abrirHoja(`
      <h3>Otros días del mes</h3>
      <p class="sub">Para cargar un día que quedó pendiente</p>
      <div class="lista-pendientes">
        ${diarios.map((i) => `
          <div class="item-pendiente">
            <div class="texto">
              <span class="nombre-item">${UI.esc(i.nombre)}</span>
              <span class="obs">${(i.diario && i.diario.dias_relevados.length) || 0}
                día(s) relevado(s)</span>
            </div>
            <button class="btn-texto" data-dias="${UI.esc(i.clave)}">Ver días</button>
          </div>`).join('')}
      </div>
      <div class="acciones">
        <button class="btn" data-cerrar>Cerrar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrar;
      hoja.querySelectorAll('[data-dias]').forEach((b) => {
        b.onclick = () => {
          cerrar();
          const i = dash.items.find((x) => x.clave === b.dataset.dias);
          pantallaDiasItem(i.clave, i, layout, ir);
        };
      });
    });
  }

  /**
   * Explica de dónde sale un ítem derivado del check-list.
   *
   * Baños es el único con una parte que el check-list no puede cubrir: si un
   * inodoro está clausurado, el check-list lo mide como limpio igual. Esa
   * carga sigue disponible desde acá, separada de lo automático.
   */
  function hojaDerivado(i) {
    const estado = i.estado === 'CUMPLE' ? 'Cumple'
                 : i.estado === 'NO_CUMPLE' ? 'No cumple'
                 : 'Sin datos del período';
    const origen = i.clave === 'banos'
      ? `La limpieza sale de los ítems de baños del check-list diario,
         promediados sobre los días auditados del mes. Los artefactos en
         servicio salen de las clausuras cargadas en el control diario.`
      : `Se calcula con los ítems del check-list diario que miden cestos,
         contenedores, vidrios, techos y corredores.`;

    UI.abrirHoja(`
      <h3>${UI.esc(i.nombre)}</h3>
      <p class="sub">Automático — ${UI.esc(estado)}</p>
      <div class="aviso info">${origen}</div>
      <p style="font-size:14px;color:var(--gris)">
        Para cambiar este resultado hay que corregir el check-list diario de
        limpieza del día que corresponda, no cargarlo de nuevo acá.
      </p>
      ${i.clave === 'banos' ? `
        <div class="aviso info">
          <strong>Artefactos fuera de servicio (3.1.a)</strong>
          Un inodoro clausurado el check-list lo mide como limpio igual, así
          que se registra aparte — en la tarjeta "Artefactos de baño" del
          control diario, junto al resto de la recorrida.
        </div>` : ''}
      <div class="acciones">
        <button class="btn" data-cerrar>Entendido</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cerrar]').onclick = cerrar;
    });
  }

  /**
   * Reabre el relevamiento del período. Solo admin.
   *
   * Mismo criterio que en limpieza: el historial es inmutable salvo por esta
   * vía, el motivo es obligatorio y queda en auditoria_log.
   */
  function reabrirRelevamiento(layout, ir) {
    if (!navigator.onLine) {
      return UI.toast('Reabrir un relevamiento requiere conexión', 'error');
    }
    UI.abrirHoja(`
      <h3>Reabrir relevamiento</h3>
      <p class="sub">Niveles de Servicio — ${UI.esc(UI.nombrePeriodo(periodo))}</p>
      <div class="aviso advertencia">
        El relevamiento vuelve a admitir cambios y el cumplimiento del período
        puede variar. Queda registrado quién lo reabrió, cuándo y por qué.
      </div>
      <div class="campo">
        <label for="motivo-reabrir-los">Motivo
          <span class="ayuda">(obligatorio)</span></label>
        <textarea id="motivo-reabrir-los" rows="3"
                  placeholder="Ej.: falta cargar el relevamiento de pista"></textarea>
      </div>
      ${accionesHoja('Reabrir')}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const motivo = hoja.querySelector('#motivo-reabrir-los').value.trim();
        if (!motivo) return UI.toast('Indicá el motivo', 'error');
        try {
          await API.post(`/api/los/relevamientos/${relevamiento.id}/reabrir`,
                         { motivo });
        } catch (e) {
          return UI.toast(e.message, 'error', 6000);
        }
        cerrar();
        UI.toast('Relevamiento reabierto', 'ok');
        vista(layout, ir);
      };
    });
  }

  /* ================================================= router por ítem === */

  function abrirItem(clave, layout, ir, dash) {
    const volver = () => vista(layout, ir);

    // Elevación no se carga con un formulario: se registran eventos a lo largo
    // del mes y el cumplimiento sale del acumulado.
    if (clave === 'medios_elevacion') return pantallaElevacion(layout, ir);

    const meta = (dash && dash.items.find((x) => x.clave === clave)) || {};

    // Los derivados no se cargan a mano: salen del check-list diario. Si algo
    // los abriera, el auditor cargaría dos veces el mismo relevamiento.
    if (meta.periodicidad === 'DERIVADO') {
      return hojaDerivado(meta);
    }

    // Los ítems diarios se relevan día por día, igual que el check-list de
    // limpieza: primero se elige la fecha y después se abre el formulario.
    if (meta.periodicidad === 'DIARIO') {
      return pantallaDiasItem(clave, meta, layout, ir);
    }

    fechaItem = null;
    const guardado = mediciones[clave];
    const previo = guardado ? guardado.datos : {};
    return abrirFormulario(clave, previo, volver);
  }

  /**
   * Días del mes para un ítem diario. Muestra qué días ya se relevaron y con
   * qué resultado, para que se vea de un vistazo lo que falta — el mes no
   * cumple si falla un solo día, así que un día sin relevar no es lo mismo
   * que un día en orden.
   */
  async function pantallaDiasItem(clave, meta, layout, ir) {
    const volverAlDash = () => vista(layout, ir);
    const diario = meta.diario || { dias_relevados: [], dias_incumplen: [] };
    const relevados = new Set(diario.dias_relevados);
    const fallan = new Set(diario.dias_incumplen);
    const hoy = UI.hoyISO();

    // Mismo principio que el check-list de limpieza: lo frecuente —el día sin
    // novedades— se resuelve en un toque, y el formulario largo queda para
    // cuando efectivamente hay algo que cargar. Los cuatro ítems diarios
    // trabajan por excepción, así que un relevamiento vacío ya significa
    // "todo en orden": no hay nada que tipear para decirlo.
    const filas = Calc.diasDelMes(periodo).reverse()
      .filter((f) => f <= hoy)
      .map((fecha) => {
        const hecho = relevados.has(fecha);
        const falla = fallan.has(fecha);
        const etiqueta = UI.fechaCorta(fecha) + (fecha === hoy ? ' · hoy' : '');
        return `<div class="fila-dia ${falla ? 'total' : hecho ? '' : 'parcial'}">
                  <button class="fila-dia-abrir" data-dia-fila-los="${fecha}">
                    <span class="texto">
                      <span class="nombre-item">${UI.esc(etiqueta)}</span>
                      <span class="obs">${hecho ? (falla ? 'No cumple' : 'Cumple')
                                                : 'Sin relevar'}</span>
                    </span>
                    <span class="estado-item">${hecho ? 'Editar' : 'Cargar novedad'}</span>
                  </button>
                  ${hecho ? '' : `<button class="btn-sector-ok"
                                          data-sin-novedad="${fecha}">
                                    SIN NOVEDADES</button>`}
                </div>`;
      }).join('');

    // Mismo calendario que el dashboard, pero de un solo ítem: acá el verde es
    // "relevado" y no hay estado parcial —un ítem se relevó o no—, así que son
    // tres estados. El punto rojo sigue siendo la señal de incumplimiento.
    const estadoDia = (fecha) => (relevados.has(fecha) ? 'ok'
                                : fecha < hoy ? 'falta' : 'futuro');
    const calendario = UI.calendarioMes(periodo, {
      estado: estadoDia,
      titulo: (fecha) => (relevados.has(fecha)
        ? (fallan.has(fecha) ? 'Relevado — no cumple' : 'Relevado — cumple')
        : fecha < hoy ? 'Día pasado sin relevar' : 'Aún en plazo'),
      marca: (fecha) => fallan.has(fecha),
      accion: 'dia-item-los',
      compacto: true,
      leyenda: [
        { clase: 'dia-ok', texto: 'Relevado' },
        { clase: 'dia-falta', texto: 'Sin relevar' },
        { clase: 'dia-futuro', texto: 'En plazo' },
      ],
    });

    layout(meta.nombre || clave, `${UI.nombrePeriodo(periodo)} · relevamiento diario`, `
      <div class="aviso info">
        Este ítem se releva todos los días. El mes cumple solo si cumplen todos
        los días relevados; los días sin relevar no cuentan como cumplidos.
      </div>
      <div class="tarjeta">
        <h2>Avance del mes</h2>
        <p style="margin:0 0 4px;font-size:14px;color:var(--gris)">
          ${diario.dias_relevados.length} día(s) relevado(s)${
            diario.dias_incumplen.length
              ? ` · ${diario.dias_incumplen.length} no cumple(n)` : ''}
        </p>
        <div id="cal-item-los">${calendario}</div>
      </div>
      <div class="lista-items" id="lista-dias-los">${filas}</div>`, { volver: '/los' });

    const abrirDia = async (fecha) => {
      fechaItem = fecha;
      const previo = await medicionDelDia(clave, fechaItem);
      abrirFormulario(clave, previo, volverAlDash);
    };

    // Cada control consulta dentro de su propio contenedor: el calendario y la
    // lista conviven en esta pantalla y sobre `document` se engancharían entre sí.
    const cal = $('#cal-item-los');
    if (cal) {
      cal.querySelectorAll('[data-dia-item-los]').forEach((b) => {
        b.onclick = () => abrirDia(b.dataset.diaItemLos);
      });
    }

    const lista = $('#lista-dias-los');
    if (lista) {
      lista.querySelectorAll('[data-dia-fila-los]').forEach((b) => {
        b.onclick = () => abrirDia(b.dataset.diaFilaLos);
      });
      lista.querySelectorAll('[data-sin-novedad]').forEach((b) => {
        b.onclick = () => marcarSinNovedad(clave, b.dataset.sinNovedad, volverAlDash);
      });
    }
  }

  /**
   * Registra un día sin novedades de un ítem diario, sin abrir el formulario.
   *
   * Guarda un relevamiento vacío, que en estos ítems significa "nada que
   * reportar" — no es un atajo que asuma datos: es la carga que el auditor
   * haría campo por campo, con el mismo resultado y la misma trazabilidad.
   *
   * Al terminar vuelve al dashboard, igual que cargar una novedad: el ítem del
   * día quedó resuelto y lo que sigue es el ítem siguiente, no la lista de
   * días. Quedarse en la lista dejaba al auditor sin salida evidente.
   */
  async function marcarSinNovedad(clave, fecha, volver) {
    fechaItem = fecha;
    try {
      if (!relevamiento) {
        const r = await API.post('/api/los/relevamientos', { periodo });
        relevamiento = { id: r.relevamiento_id, estado: 'ABIERTO' };
      }
      await API.mutar('POST', `/api/los/relevamientos/${relevamiento.id}/mediciones`,
                      { item: clave, datos: {}, fecha });
      UI.toast(`${UI.fechaCorta(fecha)}: sin novedades`, 'ok');
    } catch (e) {
      return UI.toast(e.message, 'error', 6000);
    }
    volver();
  }

  /** Datos ya cargados de un ítem diario en un día, para poder editarlos. */
  async function medicionDelDia(clave, fecha) {
    fotosItem = [];
    if (!relevamiento) return {};
    try {
      const r = await API.get(
        `/api/los/relevamientos/${relevamiento.id}/mediciones`
        + `?item=${encodeURIComponent(clave)}&fecha=${fecha}`);
      fotosItem = (r.medicion && r.medicion.fotos) || [];
      return (r.medicion && r.medicion.datos) || {};
    } catch (e) {
      return {};
    }
  }

  function abrirFormulario(clave, previo, volver) {
    const formularios = {
      confort_termico: formConfort,
      iluminacion: formIluminacion,
      infraestructura: formInfraestructura,
      asientos_preembarque: formAsientos,
      puntos_carga: formPuntosCarga,
      gel: formGel,
      pista_rodajes: formPista,
    };
    const fn = formularios[clave];
    if (!fn) return UI.toast('Ítem sin formulario disponible', 'error');
    // Las diarias ya dejaron sus fotos en `fotosItem` al pasar por
    // `medicionDelDia`; las mensuales viven en el dashboard, indexadas por
    // ítem. `fechaItem` es lo que distingue un camino del otro.
    if (!fechaItem) {
      fotosItem = (mediciones[clave] && mediciones[clave].fotos) || [];
    }
    fn(previo || {}, volver);
  }

  /**
   * Fotos ya guardadas del sub-ítem que se está por editar.
   *
   * El servidor viene mandando la evidencia de cada medición desde siempre y
   * la pantalla la descartaba: al reabrir un ítem el auditor no tenía forma de
   * ver qué había fotografiado, ni de saber que ya lo había hecho.
   */
  function fotosGuardadas(subitem) {
    return fotosItem.filter((f) => (f.subitem || null) === (subitem || null));
  }

  /* ============================================ evidencia fotográfica === */

  /**
   * Bloque de evidencia. Va pegado al ítem que la exige, no al final de la
   * hoja: con varios sub-ítems en grado C o D, una foto suelta al pie no dice
   * a cuál corresponde.
   *
   * Exige la foto pero deja una salida explícita: sin ella, un auditor con la
   * cámara fallada tendría como única alternativa no registrar el hallazgo,
   * que es exactamente lo que no queremos. El motivo queda asentado y el
   * informe lo marca como evidencia pendiente.
   */
  function bloqueEvidencia(id, textoExige) {
    return `
      <div class="evidencia" id="ev-${id}" hidden>
        <p class="evidencia-titulo">Evidencia fotográfica
          <span class="ayuda">${UI.esc(textoExige)}</span></p>
        <div class="fotos" id="ev-guardadas-${id}"></div>
        <div class="fotos" id="ev-fotos-${id}">
          <button class="btn-foto" type="button" data-ev-tomar="${id}"
                  aria-label="Tomar foto">📷</button>
        </div>
        <label class="campo-linea" style="margin:8px 0 0">
          <span style="font-size:13.5px">No puedo sacar la foto ahora</span>
          <input type="checkbox" data-ev-omitir="${id}"
                 style="width:24px;height:24px;flex:none">
        </label>
        <input type="text" data-ev-motivo="${id}" hidden
               placeholder="Motivo (ej.: batería agotada)">
      </div>`;
  }

  /**
   * Conecta un bloque de evidencia. Devuelve un lector de su estado.
   * `subitem` etiqueta las fotos para que el informe sepa qué retratan.
   */
  function conectarEvidencia(hoja, id, subitem = null) {
    const caja = hoja.querySelector(`#ev-${id}`);
    const cont = hoja.querySelector(`#ev-fotos-${id}`);
    const omitir = hoja.querySelector(`[data-ev-omitir="${id}"]`);
    const motivo = hoja.querySelector(`[data-ev-motivo="${id}"]`);
    let fotos = [];

    UI.galeria(hoja.querySelector(`#ev-guardadas-${id}`), fotosGuardadas(subitem),
               { titulo: subitem || '' });

    hoja.querySelector(`[data-ev-tomar="${id}"]`).onclick = async () => {
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
      cont.insertBefore(prev, cont.querySelector('.btn-foto'));
    };

    omitir.onchange = () => { motivo.hidden = !omitir.checked; };

    return {
      subitem,
      /** Muestra u oculta el bloque según si el ítem tiene hallazgo. */
      exigir(hace_falta) {
        caja.hidden = !hace_falta;
        if (!hace_falta) { omitir.checked = false; motivo.hidden = true; }
      },
      get exigida() { return !caja.hidden; },
      /** null si falta la evidencia y no se justificó. */
      leer() {
        if (caja.hidden) return { fotos: [], observaciones: null };
        if (fotos.length) {
          return { fotos: fotos.map((f) => ({ data: f, subitem })),
                   observaciones: null };
        }
        if (omitir.checked && motivo.value.trim()) {
          return { fotos: [],
                   observaciones: `${subitem ? subitem + ': ' : ''}`
                                  + `sin evidencia fotográfica (${motivo.value.trim()})` };
        }
        return null;
      },
    };
  }

  /**
   * Junta varios bloques de evidencia en un resultado único.
   * Devuelve null si alguno de los exigidos quedó sin foto ni justificación,
   * junto con el nombre del que falta para poder señalarlo.
   */
  function leerEvidencias(bloques) {
    const fotos = [];
    const observaciones = [];
    for (const b of bloques) {
      const r = b.leer();
      if (!r) return { falta: b.subitem || 'el ítem' };
      fotos.push(...r.fotos);
      if (r.observaciones) observaciones.push(r.observaciones);
    }
    return { fotos, observaciones: observaciones.join(' · ') || null };
  }

  /** Guarda una medición y vuelve al dashboard mostrando el resultado. */
  async function guardar(clave, datos, volver, observaciones, fotos) {
    try {
      if (!relevamiento) {
        const r = await API.post('/api/los/relevamientos', { periodo });
        relevamiento = { id: r.relevamiento_id, estado: 'ABIERTO' };
      }
      // fechaItem solo viaja en los ítems diarios; en los mensuales queda null
      // y el servidor rechaza cualquier fecha, que es lo que corresponde.
      const r = await API.post(
        `/api/los/relevamientos/${relevamiento.id}/mediciones`,
        { item: clave, datos, observaciones, fotos, fecha: fechaItem });

      UI.cerrarHoja();
      const res = r.resultado;
      UI.toast(res.cumple === true ? 'Cumple'
             : res.cumple === false ? 'Registrado: no cumple'
             : 'Guardado sin datos suficientes',
             res.cumple === false ? 'error' : 'ok');
      volver();
    } catch (e) {
      UI.toast(e.message, 'error', 6000);
    }
  }

  /* ===================================================== 3.1 — Baños === */

  /* ===================================================== 3.1 — Baños === */
  /* No tiene formulario. Sus dos mitades son derivadas:
       3.1.b limpieza  → del check-list diario (evaluar_banos_desde_checklist)
       3.1.a servicio  → de artefacto_baja, que se carga desde el control
                         diario, en la tarjeta "Artefactos de baño"
     Cargarlo desde acá significaba pedirle al auditor el mismo dato dos veces,
     y en el caso de la limpieza directamente se ignoraba. */

  const TIPO_NUCLEO = {
    DAMAS: 'Damas', CABALLEROS: 'Caballeros',
    PMR: 'PMR (movilidad reducida)', RECINTO_BEBES: 'Recinto de bebés',
  };

  /* ============================================ 3.2 — Confort térmico === */

  async function formConfort(previo, volver) {
    const [params, zonas] = await Promise.all([
      ayuda('confort_termico'), ayuda('confort_zonas'),
    ]);
    const estacion = previo.estacion || (await estacionActual());
    const p = params[estacion];
    const previas = previo.mediciones || [];

    // Las zonas salen de la configuración: habilitar una sala nueva no exige
    // tocar la aplicación. Solo se releva lo que está configurado hoy; una
    // medición de una zona dada de baja no se arrastra.
    const ZONAS = zonas;

    const filas = ZONAS.map((z, i) => {
      const m = previas.find((x) => x.zona === z) || {};
      return `<div class="tarjeta" style="margin-bottom:10px">
        <h3 style="font-size:15px;margin:0 0 10px">${UI.esc(z)}</h3>
        <div class="campo-linea">
          <label>Temperatura (°C)</label>
          <div class="con-sufijo">
            <input type="number" step="0.1" inputmode="decimal" data-temp="${i}"
                   value="${m.temperatura ?? ''}" placeholder="—">
            <span>°C</span>
          </div>
        </div>
        ${bloqueEvidencia(`cf-${i}`, 'Obligatoria: temperatura fuera de rango')}
      </div>`;
    }).join('');

    UI.abrirHoja(`
      <h3>Confort térmico</h3>
      <p class="sub">Ítem 3.2 — medición durante operación de vuelos</p>
      <div class="aviso info">
        <strong>${estacion === 'VERANO' ? 'Verano' : 'Invierno'} — categoría
        ${p.categoria}</strong>
        Rango admitido: ${p.min} a ${p.max} °C.
        Este ítem exige medición: no se puede completar por excepción.
      </div>
      <div class="campo">
        <label for="cf-estacion">Estación</label>
        <select id="cf-estacion">
          <option value="VERANO" ${estacion === 'VERANO' ? 'selected' : ''}>Verano</option>
          <option value="INVIERNO" ${estacion === 'INVIERNO' ? 'selected' : ''}>Invierno</option>
        </select>
      </div>
      ${filas}
      ${accionesHoja()}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;

      // Una evidencia por zona: la foto tiene que decir qué zona estaba fuera
      // de rango, no que "alguna" lo estaba.
      const evidencias = ZONAS.map((z, i) => conectarEvidencia(hoja, `cf-${i}`, z));

      /** Los rangos dependen de la estación elegida en la misma hoja. */
      const rango = () => params[hoja.querySelector('#cf-estacion').value] || p;
      const revisar = () => {
        const r = rango();
        ZONAS.forEach((z, i) => {
          const t = hoja.querySelector(`[data-temp="${i}"]`).value;
          evidencias[i].exigir(
            t !== '' && (parseFloat(t) < r.min || parseFloat(t) > r.max));
        });
      };
      hoja.querySelectorAll('[data-temp]').forEach((el) => { el.oninput = revisar; });
      hoja.querySelector('#cf-estacion').onchange = revisar;
      revisar();

      hoja.querySelector('[data-guardar]').onclick = () => {
        const meds = [];
        ZONAS.forEach((z, i) => {
          const t = hoja.querySelector(`[data-temp="${i}"]`).value;
          if (t === '') return;                    // zona no medida hoy
          meds.push({ zona: z, temperatura: parseFloat(t) });
        });
        if (!meds.length) {
          return UI.toast('Cargá al menos una temperatura', 'error');
        }
        const ev = leerEvidencias(evidencias);
        if (ev.falta) {
          return UI.toast(`Falta la evidencia de ${ev.falta}: sacá una foto o `
                          + 'indicá por qué no podés', 'error', 5000);
        }
        guardar('confort_termico',
                { estacion: hoja.querySelector('#cf-estacion').value,
                  mediciones: meds }, volver, ev.observaciones, ev.fotos);
      };
    });
  }

  /* ================================================ 3.3 — Iluminación === */

  async function formIluminacion(previo, volver) {
    const [sectores, objetivo, horarios] = await Promise.all([
      API.get('/api/inventario/luminarias').then((r) => r.luminarias),
      ayuda('iluminacion_objetivo'),
      Promise.all([ayuda('iluminacion_horario_verano'),
                   ayuda('iluminacion_horario_invierno')]),
    ]);

    const quemadas = previo.quemadas || {};
    const consecutivas = previo.consecutivas_mismo_cono || {};

    const filas = sectores.map((s) => `
      <div class="tarjeta" style="margin-bottom:10px">
        <h3 style="font-size:15px;margin:0 0 4px">${UI.esc(s.sector)}</h3>
        <p style="margin:0 0 10px;font-size:13px;color:var(--gris)">
          ${s.cantidad} luminarias instaladas
        </p>
        <div class="campo-linea">
          <label>Quemadas</label>
          <input type="number" min="0" max="${s.cantidad}" inputmode="numeric"
                 data-q="${UI.esc(s.sector)}" value="${quemadas[s.sector] || 0}">
        </div>
        <div class="campo-linea">
          <label>¿Dos o más consecutivas en el mismo cono de luz?
            <span class="ayuda">Si es así, el sector incumple aunque supere el
              ${Math.round(objetivo * 100)}%.</span></label>
          <select data-c="${UI.esc(s.sector)}">
            <option value="0" ${!consecutivas[s.sector] ? 'selected' : ''}>No</option>
            <option value="1" ${consecutivas[s.sector] ? 'selected' : ''}>Sí</option>
          </select>
        </div>
      </div>`).join('');

    UI.abrirHoja(`
      <h3>Iluminación</h3>
      <p class="sub">Ítem 3.3 — objetivo ${Math.round(objetivo * 100)}% encendidas</p>
      <div class="aviso info">
        Horario de medición: ${UI.esc(horarios[0])} en verano ·
        ${UI.esc(horarios[1])} en invierno.
        Cargá solo las luminarias quemadas; el resto se asume encendido.
      </div>
      ${filas}
      ${accionesHoja()}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = () => {
        const q = {}, c = {};
        hoja.querySelectorAll('[data-q]').forEach((el) => {
          const v = parseInt(el.value, 10) || 0;
          if (v > 0) q[el.dataset.q] = v;
        });
        hoja.querySelectorAll('[data-c]').forEach((el) => {
          if (el.value === '1') c[el.dataset.c] = true;
        });
        guardar('iluminacion',
                { quemadas: q, consecutivas_mismo_cono: c }, volver);
      };
    });
  }

  /* ========================================== 3.4 — Infraestructura === */

  async function formInfraestructura(previo, volver) {
    const [subitems, escala] = await Promise.all([
      ayuda('infraestructura_subitems'), ayuda('infraestructura_escala'),
    ]);
    const previos = previo.subitems || {};

    // Por excepción: todo arranca en A/B (cumple). El auditor solo degrada a
    // C o D, que son los grados que generan no conformidad. La evidencia va
    // debajo del criterio degradado, no al pie de la hoja: con varios en C o D
    // una foto suelta no diría a cuál corresponde.
    const claves = [];
    const bloques = Object.entries(subitems).map(([grupo, criterios]) => `
      <div class="tarjeta" style="margin-bottom:10px">
        <h3 style="font-size:15px;margin:0 0 10px">${UI.esc(etiqueta(grupo))}</h3>
        ${criterios.map((c) => {
          const clave = criterios.length === 1 && c === 'estado_general'
                      ? grupo : `${grupo}_${c}`;
          const v = previos[clave] || 'A';
          claves.push(clave);
          return `<div class="campo-linea">
            <label>${UI.esc(etiqueta(c))}</label>
            <select data-sub="${UI.esc(clave)}">
              ${['A', 'B', 'C', 'D'].map((g) =>
                `<option value="${g}" ${v === g ? 'selected' : ''}>${g}</option>`
              ).join('')}
            </select>
          </div>
          ${bloqueEvidencia(clave, 'Obligatoria en grado C o D')}`;
        }).join('')}
      </div>`).join('');

    UI.abrirHoja(`
      <h3>Estado de infraestructura</h3>
      <p class="sub">Ítem 3.4 — objetivo IRJ: grado B o mejor</p>
      <div class="aviso info">
        <strong>A</strong> ${UI.esc(escala.A)}<br>
        <strong>B</strong> ${UI.esc(escala.B)}<br>
        <strong>C</strong> ${UI.esc(escala.C)}<br>
        <strong>D</strong> ${UI.esc(escala.D)}<br>
        A y B cumplen. C y D generan una no conformidad automáticamente
        (C programada, D inmediata) y exigen foto del ítem dañado.
      </div>
      ${bloques}
      ${accionesHoja()}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;

      // Un bloque de evidencia por criterio, atado a su propio select.
      const evidencias = claves.map((clave) => {
        const ev = conectarEvidencia(hoja, clave, clave);
        const select = hoja.querySelector(`[data-sub="${clave}"]`);
        const revisar = () => ev.exigir('CD'.includes(select.value));
        select.onchange = revisar;
        revisar();
        return ev;
      });

      hoja.querySelector('[data-guardar]').onclick = () => {
        const subs = {};
        hoja.querySelectorAll('[data-sub]').forEach((el) => {
          subs[el.dataset.sub] = el.value;
        });
        const ev = leerEvidencias(evidencias);
        if (ev.falta) {
          return UI.toast(`Falta la evidencia de "${etiqueta(ev.falta)}": sacá una `
                          + 'foto o indicá por qué no podés', 'error', 5000);
        }
        guardar('infraestructura', { subitems: subs }, volver,
                ev.observaciones, ev.fotos);
      };
    });
  }

  /* ================================================== 3.5 — Asientos === */

  async function formAsientos(previo, volver) {
    const info = await API.get('/api/inventario/asientos');
    UI.abrirHoja(`
      <h3>Asientos en preembarque</h3>
      <p class="sub">Ítem 3.5 — mínimo ${info.minimo} utilizables</p>
      <div class="aviso info">
        Hay ${info.instalados} asientos instalados. Cargá solo los que están
        rotos, faltantes o inutilizables.
      </div>
      <div class="campo">
        <label for="as-inut">Asientos inutilizables</label>
        <input id="as-inut" type="number" min="0" max="${info.instalados}"
               inputmode="numeric" value="${previo.inutilizables || 0}" autofocus>
      </div>
      ${bloqueEvidencia('as', 'Obligatoria: hay asientos inutilizables')}
      ${accionesHoja()}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      const evidencia = conectarEvidencia(hoja, 'as', 'asientos');
      const campo = hoja.querySelector('#as-inut');
      const revisar = () => evidencia.exigir((parseInt(campo.value, 10) || 0) > 0);
      campo.oninput = revisar;
      revisar();

      hoja.querySelector('[data-guardar]').onclick = () => {
        const ev = leerEvidencias([evidencia]);
        if (ev.falta) {
          return UI.toast('Sacá una foto de los asientos inutilizables o indicá '
                          + 'por qué no podés', 'error', 5000);
        }
        guardar('asientos_preembarque',
                { inutilizables: parseInt(campo.value, 10) || 0 },
                volver, ev.observaciones, ev.fotos);
      };
    });
  }

  /* ============================================ 3.6 — Puntos de carga === */

  async function formPuntosCarga(previo, volver) {
    const puertas = (await API.get('/api/inventario/puertas')).puertas;
    const tomas100 = await ayuda('tomas_por_100_pax');
    const fuera = previo.fuera_servicio || {};

    const filas = puertas.map((p) => {
      const req = Math.ceil(p.php * tomas100 / 100);
      return `<div class="tarjeta" style="margin-bottom:10px">
        <h3 style="font-size:15px;margin:0 0 4px">${UI.esc(p.nombre)}</h3>
        <p style="margin:0 0 10px;font-size:13px;color:var(--gris)">
          ${p.instaladas} tomas instaladas · PHP ${p.php} · requiere ${req}
        </p>
        <div class="campo-linea">
          <label>Tomas fuera de servicio</label>
          <input type="number" min="0" max="${p.instaladas}" inputmode="numeric"
                 data-fs="${p.id}" value="${fuera[p.id] || 0}">
        </div>
        ${bloqueEvidencia(`pc-${p.id}`, 'Obligatoria: hay tomas fuera de servicio')}
      </div>`;
    }).join('');

    UI.abrirHoja(`
      <h3>Puntos de carga</h3>
      <p class="sub">Ítem 3.6 — ${tomas100} tomas cada 100 pasajeros hora pico</p>
      <div class="aviso info">
        Cargá solo las tomas fuera de servicio; el resto se asume operativo.
      </div>
      ${filas}
      ${accionesHoja()}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;

      // Una evidencia por puerta: la foto tiene que mostrar qué toma falla.
      const evidencias = puertas.map((p) => {
        const ev = conectarEvidencia(hoja, `pc-${p.id}`, p.nombre);
        const campo = hoja.querySelector(`[data-fs="${p.id}"]`);
        const revisar = () => ev.exigir((parseInt(campo.value, 10) || 0) > 0);
        campo.oninput = revisar;
        revisar();
        return ev;
      });

      hoja.querySelector('[data-guardar]').onclick = () => {
        const fs = {};
        hoja.querySelectorAll('[data-fs]').forEach((el) => {
          const v = parseInt(el.value, 10) || 0;
          if (v > 0) fs[el.dataset.fs] = v;
        });
        const ev = leerEvidencias(evidencias);
        if (ev.falta) {
          return UI.toast(`Falta la evidencia de ${ev.falta}: sacá una foto o `
                          + 'indicá por qué no podés', 'error', 5000);
        }
        guardar('puntos_carga', { fuera_servicio: fs }, volver,
                ev.observaciones, ev.fotos);
      };
    });
  }

  /* ======================================= 3.8 — Limpieza de terminal === */
  /* No tiene formulario. Sus seis sub-ítems se calculan íntegramente con el
     check-list diario (services.limpieza_terminal_desde_checklist): volver a
     pedirlos acá duplicaba la carga y, como lo manual tenía precedencia,
     terminaba anulando el cálculo automático. Se consulta desde la sección
     "Automáticos del check-list" del dashboard. */

  /* ======================================================= 3.9 — GEL === */

  async function formGel(previo, volver) {
    const [tabla, categoriaIRJ] = await Promise.all([
      ayuda('gel_tiempos_conmutacion'), ayuda('gel_categoria_irj'),
    ]);
    const previas = previo.pruebas || [{}];

    const fila = (p, i) => `
      <div class="tarjeta" style="margin-bottom:10px" data-prueba>
        <div class="campo-linea">
          <label>Fecha de la prueba</label>
          <input type="date" data-fecha value="${UI.esc(p.fecha || '')}">
        </div>
        <div class="campo-linea">
          <label>Categoría</label>
          <select data-cat>
            ${Object.keys(tabla).map((c) =>
              `<option value="${c}" ${(p.categoria || categoriaIRJ) === c ? 'selected' : ''}
                >${UI.esc(etiqueta(c))} (máx ${tabla[c]} s)</option>`).join('')}
          </select>
        </div>
        <div class="campo-linea">
          <label>Tiempo medido</label>
          <div class="con-sufijo">
            <input type="number" step="0.1" min="0" inputmode="decimal" data-tiempo
                   value="${p.tiempo_s ?? ''}" placeholder="—">
            <span>s</span>
          </div>
        </div>
      </div>`;

    UI.abrirHoja(`
      <h3>Grupos electrógenos (GEL)</h3>
      <p class="sub">Ítem 3.9 — prueba de conmutación, RAAC 154</p>
      <div class="aviso info">
        Este ítem exige medición: se registra el tiempo real de conmutación de
        cada ayuda luminosa y se compara con el máximo de su categoría.
      </div>
      <div id="gel-pruebas">${previas.map(fila).join('')}</div>
      <button class="btn btn-bloque" id="gel-agregar" style="margin-bottom:12px">
        + Agregar otra prueba
      </button>
      ${accionesHoja()}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('#gel-agregar').onclick = () => {
        hoja.querySelector('#gel-pruebas').insertAdjacentHTML('beforeend', fila({}, 0));
      };
      hoja.querySelector('[data-guardar]').onclick = () => {
        const pruebas = [];
        hoja.querySelectorAll('[data-prueba]').forEach((el) => {
          const t = el.querySelector('[data-tiempo]').value;
          if (t === '') return;
          pruebas.push({
            fecha: el.querySelector('[data-fecha]').value || null,
            categoria: el.querySelector('[data-cat]').value,
            tiempo_s: parseFloat(t),
          });
        });
        if (!pruebas.length) {
          return UI.toast('Cargá al menos un tiempo de conmutación', 'error');
        }
        guardar('gel', { pruebas }, volver);
      };
    });
  }

  /* ========================================= 3.10 — Pista y rodajes === */

  async function formPista(previo, volver) {
    const [secciones, pPista, pRodaje, escala] = await Promise.all([
      API.get('/api/inventario/secciones').then((r) => r.secciones),
      ayuda('pci_pista'), ayuda('pci_rodaje'), ayuda('pci_escala'),
    ]);
    const pci = previo.pci || {};

    const grupo = (tipo, p) => {
      const filtradas = secciones.filter((s) => s.tipo === tipo);
      if (!filtradas.length) return '';
      return `<div class="tarjeta" style="margin-bottom:10px">
        <h3 style="font-size:15px;margin:0 0 4px">
          ${tipo === 'PISTA' ? 'Pista' : 'Rodaje'}
        </h3>
        <p style="margin:0 0 10px;font-size:13px;color:var(--gris)">
          Exige que el ${Math.round(p.proporcion_min * 100)}% de las secciones
          tenga PCI mayor a ${p.umbral}
        </p>
        ${filtradas.map((s) => `
          <div class="campo-linea">
            <label>${UI.esc(s.identificador)}</label>
            <input type="number" min="0" max="100" inputmode="numeric"
                   data-pci="${s.id}" value="${pci[s.id] ?? ''}" placeholder="PCI">
          </div>`).join('')}
      </div>`;
    };

    UI.abrirHoja(`
      <h3>Pista y rodajes</h3>
      <p class="sub">Ítem 3.10 — disponibilidad y estado del pavimento</p>
      <div class="aviso info">
        Escala PCI: ${escala.map((e) => `${e.min}-${e.max} ${e.etiqueta}`).join(' · ')}
      </div>
      <div class="campo">
        <label for="pi-indisp">Indisponibilidades no programadas en el período
          <span class="ayuda">Se exige 100% de disponibilidad en horario
            operativo: cualquier evento no programado hace incumplir.</span></label>
        <input id="pi-indisp" type="number" min="0" inputmode="numeric"
               value="${previo.indisponibilidades_no_programadas || 0}">
      </div>
      ${grupo('PISTA', pPista)}
      ${grupo('RODAJE', pRodaje)}
      ${accionesHoja()}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = () => {
        const vals = {};
        hoja.querySelectorAll('[data-pci]').forEach((el) => {
          if (el.value !== '') vals[el.dataset.pci] = parseInt(el.value, 10);
        });
        guardar('pista_rodajes', {
          pci: vals,
          indisponibilidades_no_programadas:
            parseInt(hoja.querySelector('#pi-indisp').value, 10) || 0,
        }, volver);
      };
    });
  }

  /* ==================================== 3.7 — Medios de elevación === */

  /**
   * Pantalla propia, no formulario: la disponibilidad sale del acumulado de
   * eventos del mes, que se van registrando cuando ocurren. Un formulario de
   * carga única no reflejaría cómo se usa en la realidad.
   */
  async function pantallaElevacion(layout, ir) {
    layout('Medios de elevación', UI.nombrePeriodo(periodo),
           '<div class="vacio">Cargando…</div>', { volver: '/los' });

    let equipos, eventos, resultado;
    try {
      [equipos, eventos] = await Promise.all([
        API.get('/api/inventario/elevacion').then((r) => r.elevacion),
        API.get(`/api/los/elevacion/eventos?periodo=${periodo}`).then((r) => r.eventos),
      ]);
      resultado = (await API.get(`/api/los/dashboard?periodo=${periodo}`))
        .items.find((i) => i.clave === 'medios_elevacion');
    } catch (e) {
      return ($('.contenido').innerHTML =
        `<div class="aviso error">${UI.esc(e.message)}</div>`);
    }

    const porEquipo = {};
    eventos.forEach((ev) => {
      porEquipo[ev.equipo_id] = porEquipo[ev.equipo_id] || [];
      porEquipo[ev.equipo_id].push(ev);
    });

    const tarjetas = equipos.map((eq) => {
      const evs = porEquipo[eq.id] || [];
      const horas = evs.reduce((a, e) => a + (e.horas || 0), 0);
      const filas = evs.length ? evs.map((e) => `
        <div class="item">
          <span class="texto">
            <span class="nombre-item">${e.horas} h fuera de servicio</span>
            <span class="obs">${UI.esc(e.inicio || '')}${
              e.motivo ? ' · ' + UI.esc(e.motivo) : ''}</span>
          </span>
          <button class="btn-mini peligro" data-borrar-ev="${e.id}">Borrar</button>
        </div>`).join('')
        : '<p style="margin:0;font-size:13px;color:var(--gris)">Sin eventos este mes</p>';

      return `<div class="tarjeta">
        <h2>${UI.esc(eq.nombre)}</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          ${eq.tipo || 'Equipo'} · ${eq.redundancia ? 'con' : 'sin'} redundancia ·
          <strong>${horas} h</strong> acumuladas en el mes
        </p>
        <div class="lista-items">${filas}</div>
        <button class="btn btn-bloque" style="margin-top:12px"
                data-nuevo-ev="${eq.id}" data-nombre="${UI.esc(eq.nombre)}">
          + Registrar evento
        </button>
      </div>`;
    }).join('');

    $('.contenido').innerHTML = `
      <div class="aviso info">
        <strong>${resultado.estado === 'CUMPLE' ? 'Cumple'
                : resultado.estado === 'NO_CUMPLE' ? 'No cumple' : 'Sin datos'}</strong>
        Con redundancia se exige 91,66% de disponibilidad (máx. 60 h de
        indisponibilidad mensual); sin redundancia, 93% (máx. 48 h).
        Ningún evento individual puede superar las 48 h.
      </div>
      ${equipos.length ? tarjetas : `<div class="aviso advertencia">
        <strong>Sin equipos cargados</strong>
        Cargá los medios de elevación desde Configuración → Inventario.
      </div>`}`;

    document.querySelectorAll('[data-nuevo-ev]').forEach((b) => {
      b.onclick = () => hojaEventoElevacion(b.dataset.nuevoEv, b.dataset.nombre,
                                            () => pantallaElevacion(layout, ir));
    });
    document.querySelectorAll('[data-borrar-ev]').forEach((b) => {
      b.onclick = async () => {
        const ok = await UI.confirmar(
          'Borrar evento',
          'Se elimina del acumulado del mes y la disponibilidad se recalcula.',
          'Borrar', 'btn-rojo');
        if (!ok) return;
        try {
          await API.del(`/api/los/elevacion/eventos/${b.dataset.borrarEv}`);
          UI.toast('Evento eliminado', 'ok');
          pantallaElevacion(layout, ir);
        } catch (e) { UI.toast(e.message, 'error'); }
      };
    });
  }

  function hojaEventoElevacion(equipoId, nombre, volver) {
    const ahora = UI.ahoraISO();
    UI.abrirHoja(`
      <h3>Registrar evento</h3>
      <p class="sub">${UI.esc(nombre)}</p>
      <div id="error-ev"></div>
      <div class="campo">
        <label for="ev-inicio">Inicio de la indisponibilidad</label>
        <input id="ev-inicio" type="datetime-local" value="${ahora}">
      </div>
      <div class="campo">
        <label for="ev-horas">Horas fuera de servicio
          <span class="ayuda">Un solo evento no puede superar las 48 h sin
            incumplir.</span></label>
        <input id="ev-horas" type="number" min="0" step="0.5" inputmode="decimal"
               placeholder="0">
      </div>
      <div class="campo">
        <label for="ev-motivo">Motivo <span class="ayuda">opcional</span></label>
        <input id="ev-motivo" type="text" placeholder="Mantenimiento, falla…">
      </div>
      ${accionesHoja('Registrar')}`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const horas = parseFloat(hoja.querySelector('#ev-horas').value);
        if (isNaN(horas) || horas < 0) {
          return UI.toast('Ingresá las horas fuera de servicio', 'error');
        }
        try {
          await API.post('/api/los/elevacion/eventos', {
            equipo_id: parseInt(equipoId, 10),
            periodo,
            inicio: hoja.querySelector('#ev-inicio').value.replace('T', ' '),
            horas,
            motivo: hoja.querySelector('#ev-motivo').value.trim() || null,
          });
          cerrar();
          UI.toast('Evento registrado', 'ok');
          volver();
        } catch (e) {
          hoja.querySelector('#error-ev').innerHTML =
            `<div class="aviso error">${UI.esc(e.message)}</div>`;
        }
      };
    });
  }

  /* ====================================================== utilidades === */

  function accionesHoja(textoOk = 'Guardar') {
    return `<div class="acciones">
      <button class="btn" data-cancelar>Cancelar</button>
      <button class="btn btn-primario" data-guardar>${UI.esc(textoOk)}</button>
    </div>`;
  }

  /** Lee un valor de la configuración del servidor (umbrales, textos de ayuda). */
  let _config = null;
  async function ayuda(clave) {
    if (!_config) {
      const r = await API.get('/api/config');
      _config = {};
      r.config.forEach((c) => { _config[c.clave] = c.valor; });
    }
    return _config[clave];
  }

  async function estacionActual() {
    const hoy = new Date();
    const mmdd = String(hoy.getMonth() + 1).padStart(2, '0') + '-'
               + String(hoy.getDate()).padStart(2, '0');
    const verano = await ayuda('inicio_verano');
    const invierno = await ayuda('inicio_invierno');
    // El verano cruza el fin de año (hemisferio sur).
    if (verano > invierno) {
      return (mmdd >= verano || mmdd < invierno) ? 'VERANO' : 'INVIERNO';
    }
    return (mmdd >= verano && mmdd < invierno) ? 'VERANO' : 'INVIERNO';
  }

  /** Convierte claves internas en texto legible: 'pisos_interiores' → 'Pisos interiores'. */
  function etiqueta(clave) {
    const t = String(clave).replace(/_/g, ' ');
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  return { vista };
})();
