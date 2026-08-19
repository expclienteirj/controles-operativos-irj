/* Configuración del Aeropuerto (sección 4) — solo rol admin.
 *
 * Cuatro pestañas: Generales, Inventario, Parámetros y Período. Todo lo que se
 * carga acá alimenta directamente el motor de cálculo, así que el servidor
 * valida cada valor y esta pantalla muestra el error tal cual lo devuelve, sin
 * reinterpretarlo. */

const Config = (() => {
  let pestana = 'general';

  // Las cuatro pestañas, en una sola lista: la pinta la vista y la valida el
  // ruteo por pestaña (#/config/inventario).
  const PESTANAS = [['general', 'Generales'], ['inventario', 'Inventario'],
                    ['parametros', 'Parámetros'], ['periodo', 'Período']];
  let onboarding = null;

  const $ = (sel) => document.querySelector(sel);

  /* Metadatos de cada recurso de inventario: qué campos pide el formulario y
     cómo se muestra cada fila en la lista. */
  const RECURSOS = {
    nucleos: {
      titulo: 'Núcleos sanitarios',
      singular: 'núcleo',
      ayuda: 'Cargá cada baño con la cantidad instalada de cada artefacto. '
           + 'Los baños PMR y el recinto de bebés exigen 100% de cumplimiento.',
      campos: [
        { id: 'nombre', etiqueta: 'Nombre', tipo: 'texto',
          ayuda: 'Ej.: Damas hall central' },
        { id: 'tipo', etiqueta: 'Tipo', tipo: 'opciones', opciones: [
            ['DAMAS', 'Damas'], ['CABALLEROS', 'Caballeros'],
            ['PMR', 'PMR (movilidad reducida)'], ['RECINTO_BEBES', 'Recinto de bebés']] },
        { id: 'ubicacion', etiqueta: 'Ubicación', tipo: 'texto', opcional: true },
      ],
      equipos: ['inodoros', 'mingitorios', 'bachas', 'jaboneras', 'toalleros',
                'cestos', 'espejos', 'cambiadores'],
      fila: (n) => {
        const eq = Object.entries(n.equipos || {})
          .map(([k, v]) => `${v} ${k}`).join(' · ');
        return { titulo: n.nombre, detalle: `${TIPO_NUCLEO[n.tipo] || n.tipo} — ${eq || 'sin artefactos'}` };
      },
    },
    luminarias: {
      titulo: 'Luminarias por sector',
      singular: 'sector',
      ayuda: 'Total de luminarias instaladas en cada sector. El auditor solo '
           + 'reporta las quemadas; el sistema calcula el porcentaje encendido.',
      campos: [
        { id: 'sector', etiqueta: 'Sector', tipo: 'texto' },
        { id: 'cantidad', etiqueta: 'Cantidad instalada', tipo: 'entero' },
      ],
      fila: (l) => ({ titulo: l.sector, detalle: `${l.cantidad} luminarias` }),
    },
    puertas: {
      titulo: 'Puntos de carga',
      singular: 'puerta',
      ayuda: 'Estándar: 25 tomas cada 100 pasajeros en hora pico, por puerta.',
      campos: [
        { id: 'nombre', etiqueta: 'Puerta de embarque', tipo: 'texto' },
        { id: 'php', etiqueta: 'Pasajeros en hora pico', tipo: 'entero' },
        { id: 'instaladas', etiqueta: 'Tomas instaladas', tipo: 'entero', min: 0 },
      ],
      fila: (p) => ({
        titulo: p.nombre,
        detalle: `${p.instaladas} tomas · PHP ${p.php} · requiere `
               + `${Math.ceil(p.php * 25 / 100)}`,
      }),
    },
    elevacion: {
      titulo: 'Medios de elevación',
      singular: 'equipo',
      ayuda: 'Con equipo redundante se exige 91,66% de disponibilidad; sin '
           + 'redundancia, 93%.',
      campos: [
        { id: 'nombre', etiqueta: 'Nombre', tipo: 'texto' },
        { id: 'tipo', etiqueta: 'Tipo', tipo: 'opciones', opcional: true, opciones: [
            ['Ascensor', 'Ascensor'], ['Escalera mecánica', 'Escalera mecánica'],
            ['Plataforma', 'Plataforma elevadora']] },
        { id: 'redundancia', etiqueta: '¿Tiene equipo redundante?', tipo: 'booleano' },
      ],
      fila: (e) => ({
        titulo: e.nombre,
        detalle: `${e.tipo || 'Sin tipo'} · ${e.redundancia ? 'con' : 'sin'} redundancia`,
      }),
    },
    secciones: {
      titulo: 'Secciones de pista y rodaje',
      singular: 'sección',
      ayuda: 'Se exige PCI > 70 en el 85% de las secciones de pista y PCI > 60 '
           + 'en el 70% de las de rodaje.',
      campos: [
        { id: 'identificador', etiqueta: 'Identificador', tipo: 'texto',
          ayuda: 'Ej.: RWY-01, TWY-A' },
        { id: 'tipo', etiqueta: 'Tipo', tipo: 'opciones', opciones: [
            ['PISTA', 'Pista'], ['RODAJE', 'Rodaje']] },
      ],
      fila: (s) => ({ titulo: s.identificador,
                      detalle: s.tipo === 'PISTA' ? 'Pista' : 'Rodaje' }),
    },
  };

  const TIPO_NUCLEO = {
    DAMAS: 'Damas', CABALLEROS: 'Caballeros',
    PMR: 'PMR', RECINTO_BEBES: 'Recinto de bebés',
  };

  /* ==================================================== vista principal === */

  /**
   * `tab` (opcional) llega por la ruta (#/config/inventario) y abre esa
   * pestaña en vez de la que quedó de la última visita. Lo usa la novedad de
   * inventario sin cargar, que antes dejaba al admin en "Generales" y con las
   * cuatro pestañas para revisar a mano.
   *
   * Se ignora un valor desconocido en lugar de fallar: la ruta la escribe el
   * usuario en la barra de direcciones tanto como la app.
   */
  async function vista(layout, ir, tab) {
    if (tab && PESTANAS.some(([id]) => id === tab)) pestana = tab;

    layout('Configuración del Aeropuerto', 'IRJ — categoría G5',
           '<div class="vacio">Cargando…</div>', { volver: '/' });

    // Acá no se cae al caché, a diferencia de Limpieza o LoS: las trece
    // acciones de guardado de esta pantalla van directo al servidor y ninguna
    // se encola. Mostrar la configuración desde una copia local dejaría al
    // admin editando contra una pantalla incapaz de guardar, y de este
    // inventario dependen los porcentajes de LoS y la certificación.
    if (!navigator.onLine) {
      return ($('.contenido').innerHTML = `
        <div class="aviso advertencia">
          <strong>Sin conexión</strong>
          La configuración se guarda en el servidor, así que necesita red.
          Volvé a entrar cuando haya conexión.
        </div>`);
    }

    try {
      onboarding = await API.get('/api/onboarding');
    } catch (e) {
      // Un error del servidor trae `codigo` y su mensaje sirve; una caída de
      // red no lo trae, y "Failed to fetch" no le dice nada a nadie.
      return ($('.contenido').innerHTML = `<div class="aviso error">${e.codigo
        ? `No se pudo cargar la configuración: ${UI.esc(e.message)}`
        : 'No se pudo conectar con el servidor. Revisá la conexión y volvé a '
          + 'entrar: la configuración no se puede editar sin red.'}</div>`);
    }

    $('.contenido').innerHTML = `
      ${bannerOnboarding()}
      <div class="pestanas" id="pestanas">
        ${PESTANAS
          .map(([id, txt]) => `<button class="pestana ${pestana === id ? 'activa' : ''}"
                                 data-tab="${id}">${txt}</button>`).join('')}
      </div>
      <div id="panel"></div>`;

    document.querySelectorAll('[data-tab]').forEach((b) => {
      b.onclick = () => { pestana = b.dataset.tab; vista(layout, ir); };
    });

    const panel = $('#panel');
    if (pestana === 'general') await panelGeneral(panel, layout, ir);
    else if (pestana === 'inventario') await panelInventario(panel, layout, ir);
    else if (pestana === 'parametros') await panelParametros(panel, layout, ir);
    else await panelPeriodo(panel);
  }

  /**
   * Estado del asistente de primera configuración. Se muestra hasta que el
   * inventario esté completo, porque hasta entonces los ítems LoS
   * cuantitativos no se pueden relevar y el dashboard los da por "Sin datos".
   */
  function bannerOnboarding() {
    if (!onboarding || onboarding.terminado) {
      // El número lo dice el servidor: escrito a mano dejaba de ser cierto en
      // cuanto se marcaba un ítem como no aplicable.
      const n = onboarding && onboarding.items_los_aplicables;
      return `<div class="aviso info">
        <strong>Configuración inicial completa</strong>
        ${n ? `Los ${n} ítems del módulo LoS que rigen en este aeropuerto están
               habilitados para relevamiento.`
            : 'Los ítems del módulo LoS están habilitados para relevamiento.'}
      </div>`;
    }
    // Los bloques que no aplican no son faltantes: no hay nada que cargar.
    const faltan = onboarding.pasos.filter((p) => !p.completo && p.aplica !== false);
    const pct = Math.round(onboarding.progreso * 100);
    return `
      <div class="tarjeta" style="border-left:4px solid var(--amarillo)">
        <h2>Primera configuración · ${onboarding.completos} de ${onboarding.total}</h2>
        <p style="margin:0 0 8px;font-size:14px;color:var(--gris)">
          Falta cargar el inventario físico del aeropuerto. Hasta que esté,
          ${faltan.length} ítem(s) del módulo LoS no se pueden relevar y quedan
          como <strong>Sin datos</strong> — nunca como 100%.
        </p>
        <div class="barra-progreso" style="margin-bottom:12px">
          <div style="width:${pct}%"></div>
        </div>
        <div class="chips">
          ${faltan.map((p) => `<span class="chip-falta">${UI.esc(p.titulo)}</span>`).join('')}
        </div>
      </div>`;
  }

  /* ======================================================= tab Generales === */

  const CAMPOS_GENERALES = [
    { clave: 'aeropuerto_nombre', etiqueta: 'Aeropuerto', tipo: 'texto' },
    { clave: 'aeropuerto_codigo', etiqueta: 'Código', tipo: 'texto' },
    { clave: 'aeropuerto_categoria', etiqueta: 'Categoría', tipo: 'texto' },
    { clave: 'concesionario', etiqueta: 'Concesionario', tipo: 'texto' },
    { clave: 'horario_operativo_inicio', etiqueta: 'Inicio del horario operativo',
      tipo: 'texto', ayuda: 'Formato HH:MM' },
    { clave: 'horario_operativo_fin', etiqueta: 'Fin del horario operativo',
      tipo: 'texto', ayuda: 'Formato HH:MM' },
    { clave: 'horas_operativas_dia', etiqueta: 'Horas operativas por día',
      tipo: 'numero',
      ayuda: 'Base del 100% exigido a la disponibilidad de pista y rodajes.' },
    { clave: 'inicio_verano', etiqueta: 'Inicio de temporada verano',
      tipo: 'texto', ayuda: 'Formato MM-DD' },
    { clave: 'inicio_invierno', etiqueta: 'Inicio de temporada invierno',
      tipo: 'texto', ayuda: 'Formato MM-DD' },
    { clave: 'foto_obligatoria_desvio', etiqueta: 'Exigir foto al registrar un desvío',
      tipo: 'booleano' },
    { clave: 'cobertura_minima_mes', etiqueta: 'Cobertura mínima del mes',
      tipo: 'porcentaje',
      ayuda: 'Proporción de días con control cerrado por debajo de la cual la '
           + 'certificación se emite con advertencia. Los días sin auditar nunca '
           + 'penalizan al contratista.' },
  ];

  async function panelGeneral(panel, layout, ir) {
    const cfg = await cargarConfig();
    panel.innerHTML = `<div class="tarjeta">
      <h2>Datos generales</h2>
      ${CAMPOS_GENERALES.map((c) => campoConfig(c, cfg[c.clave])).join('')}
    </div>`;
    conectarCampos(panel, layout, ir);
  }

  /* ====================================================== tab Inventario === */

  async function panelInventario(panel, layout, ir) {
    const [asientos, estado, ...listas] = await Promise.all([
      API.get('/api/inventario/asientos').catch(() => null),
      API.get('/api/onboarding').catch(() => null),
      ...Object.keys(RECURSOS).map((r) =>
        API.get(`/api/inventario/${r}`).then((x) => ({ recurso: r, datos: x[r] }))),
    ]);

    // Qué bloques rigen en este aeropuerto. El mapeo recurso → ítem LoS ya lo
    // resuelve el servidor para el onboarding: se reusa en lugar de mantener
    // una segunda copia acá, que es como se desincronizan estas cosas.
    const rige = {};
    for (const p of (estado && estado.pasos) || []) rige[p.clave] = p.aplica !== false;

    // Las zonas de medición térmica son inventario, no un parámetro: son los
    // lugares físicos donde el auditor toma la temperatura. Los umbrales de
    // confort (rangos por estación) siguen en Parámetros, que es donde vive
    // todo lo que se compara contra un objetivo.
    const cfg = await cargarConfig();

    panel.innerHTML = `
      ${listas.map((l) => bloqueRecurso(l.recurso, l.datos,
                                        rige[l.recurso] !== false)).join('')}
      ${bloqueAsientos()}
      <div class="tarjeta">
        <h2>Zonas de medición térmica</h2>
        ${formZonasConfort(cfg.confort_zonas || [])}
      </div>`;

    conectarZonas(panel, layout, ir);

    // Total de asientos: es un único número, no una lista.
    if (asientos) {
      $('#asientos-instalados').value = asientos.instalados || '';
      $('#asientos-minimo').textContent = asientos.minimo;
    }

    $('#guardar-asientos').onclick = async () => {
      const valor = parseInt($('#asientos-instalados').value, 10);
      if (isNaN(valor) || valor < 0) return UI.toast('Ingresá un número válido', 'error');
      try {
        await API.put('/api/inventario/asientos', { instalados: valor });
        UI.toast('Asientos actualizados', 'ok');
        onboarding = await API.get('/api/onboarding');
        vista(layout, ir);
      } catch (e) { UI.toast(e.message, 'error'); }
    };

    panel.querySelectorAll('[data-nuevo]').forEach((b) => {
      b.onclick = () => formulario(b.dataset.nuevo, null, layout, ir);
    });
    panel.querySelectorAll('[data-editar]').forEach((b) => {
      b.onclick = () => {
        const lista = listas.find((l) => l.recurso === b.dataset.recurso);
        const item = lista.datos.find((x) => x.id === parseInt(b.dataset.editar, 10));
        formulario(b.dataset.recurso, item, layout, ir);
      };
    });
    panel.querySelectorAll('[data-borrar]').forEach((b) => {
      b.onclick = () => borrar(b.dataset.recurso, parseInt(b.dataset.borrar, 10),
                              b.dataset.nombre, layout, ir);
    });
  }

  /**
   * Tarjeta de un recurso de inventario.
   *
   * `aplica`: si el ítem LoS que alimenta rige en este aeropuerto. Cuando no
   * rige, el bloque no reclama nada: exigir el inventario de algo que el
   * aeropuerto no tiene —IRJ no tiene mangas ni medios de elevación— es pedir
   * una carga imposible para satisfacer una condición que no existe. Se sigue
   * mostrando, y se puede cargar igual, porque el ítem puede volver a regir.
   */
  function bloqueRecurso(recurso, datos, aplica = true) {
    const meta = RECURSOS[recurso];
    if (!aplica) {
      return `<div class="tarjeta">
        <h2>${UI.esc(meta.titulo)}</h2>
        <div class="aviso info" style="margin:0 0 12px">
          <strong>No aplica en este aeropuerto</strong>
          El ítem LoS asociado está marcado como no aplicable, así que no exige
          inventario ni entra en el resultado. Se cambia en Parámetros.
        </div>
        ${datos.length ? `<div class="lista-items">${datos.map((d) => {
          const { titulo, detalle } = meta.fila(d);
          return `<div class="item">
            <span class="texto">
              <span class="nombre-item">${UI.esc(titulo)}</span>
              <span class="obs">${UI.esc(detalle)}</span>
            </span>
            <button class="btn-mini peligro" data-recurso="${recurso}"
                    data-borrar="${d.id}"
                    data-nombre="${UI.esc(titulo)}">Borrar</button>
          </div>`;
        }).join('')}</div>` : ''}
      </div>`;
    }
    const filas = datos.length
      ? datos.map((d) => {
          const { titulo, detalle } = meta.fila(d);
          return `<div class="item">
            <span class="texto">
              <span class="nombre-item">${UI.esc(titulo)}</span>
              <span class="obs">${UI.esc(detalle)}</span>
            </span>
            <button class="btn-mini" data-recurso="${recurso}"
                    data-editar="${d.id}">Editar</button>
            <button class="btn-mini peligro" data-recurso="${recurso}"
                    data-borrar="${d.id}"
                    data-nombre="${UI.esc(titulo)}">Borrar</button>
          </div>`;
        }).join('')
      : `<div class="aviso advertencia" style="margin:0">
           <strong>Sin cargar</strong>
           Mientras esté vacío, el ítem LoS asociado queda como
           "Requiere configuración".
         </div>`;

    return `<div class="tarjeta">
      <h2>${UI.esc(meta.titulo)}</h2>
      <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">${UI.esc(meta.ayuda)}</p>
      <div class="lista-items">${filas}</div>
      <button class="btn btn-bloque" style="margin-top:12px"
              data-nuevo="${recurso}">+ Agregar ${UI.esc(meta.singular)}</button>
    </div>`;
  }

  function bloqueAsientos() {
    return `<div class="tarjeta">
      <h2>Asientos de preembarque</h2>
      <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
        Total instalado en la sala. El mínimo exigido para IRJ es
        <strong id="asientos-minimo">38</strong> asientos utilizables.
      </p>
      <div class="campo">
        <label for="asientos-instalados">Asientos instalados</label>
        <input id="asientos-instalados" type="number" min="0" inputmode="numeric">
      </div>
      <button class="btn btn-primario btn-bloque" id="guardar-asientos">Guardar</button>
    </div>`;
  }

  /* -------------------------------------------------- formulario genérico -- */

  function formulario(recurso, item, layout, ir) {
    const meta = RECURSOS[recurso];
    const esNuevo = !item;

    const campos = meta.campos.map((c) => {
      const valor = item ? item[c.id] : '';
      if (c.tipo === 'opciones') {
        return `<div class="campo">
          <label for="f-${c.id}">${UI.esc(c.etiqueta)}</label>
          <select id="f-${c.id}">
            ${c.opcional ? '<option value="">—</option>' : ''}
            ${c.opciones.map(([v, t]) =>
              `<option value="${v}" ${valor === v ? 'selected' : ''}>${UI.esc(t)}</option>`
            ).join('')}
          </select>
        </div>`;
      }
      if (c.tipo === 'booleano') {
        return `<div class="campo">
          <label for="f-${c.id}">${UI.esc(c.etiqueta)}</label>
          <select id="f-${c.id}">
            <option value="0" ${!valor ? 'selected' : ''}>No</option>
            <option value="1" ${valor ? 'selected' : ''}>Sí</option>
          </select>
        </div>`;
      }
      const tipoInput = c.tipo === 'entero' ? 'number' : 'text';
      return `<div class="campo">
        <label for="f-${c.id}">${UI.esc(c.etiqueta)}
          ${c.ayuda ? `<span class="ayuda">${UI.esc(c.ayuda)}</span>` : ''}</label>
        <input id="f-${c.id}" type="${tipoInput}"
               ${c.tipo === 'entero' ? `min="${c.min !== undefined ? c.min : 1}" inputmode="numeric"` : ''}
               value="${UI.esc(valor === null || valor === undefined ? '' : valor)}">
      </div>`;
    }).join('');

    const equipos = meta.equipos ? `
      <div class="campo">
        <label>Artefactos instalados
          <span class="ayuda">Dejá en 0 los que no existan en este baño.</span>
        </label>
        <div class="grilla-equipos">
          ${meta.equipos.map((e) => `
            <div class="equipo-campo">
              <label for="eq-${e}">${e}</label>
              <input id="eq-${e}" type="number" min="0" inputmode="numeric"
                     value="${item && item.equipos ? (item.equipos[e] || 0) : 0}">
            </div>`).join('')}
        </div>
      </div>` : '';

    UI.abrirHoja(`
      <h3>${esNuevo ? 'Agregar' : 'Editar'} ${UI.esc(meta.singular)}</h3>
      <p class="sub">${UI.esc(meta.titulo)}</p>
      <div id="error-form"></div>
      ${campos}
      ${equipos}
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-primario" data-guardar>Guardar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        const cuerpo = {};
        for (const c of meta.campos) {
          const el = hoja.querySelector(`#f-${c.id}`);
          let v = el.value;
          if (c.tipo === 'entero') v = v === '' ? null : parseInt(v, 10);
          else if (c.tipo === 'booleano') v = v === '1';
          else v = v.trim();
          if (v !== '' && v !== null) cuerpo[c.id] = v;
        }
        if (meta.equipos) {
          cuerpo.equipos = {};
          for (const e of meta.equipos) {
            cuerpo.equipos[e] = parseInt(hoja.querySelector(`#eq-${e}`).value, 10) || 0;
          }
        }

        try {
          if (esNuevo) await API.post(`/api/inventario/${recurso}`, cuerpo);
          else await API.put(`/api/inventario/${recurso}/${item.id}`, cuerpo);
          cerrar();
          UI.toast(esNuevo ? 'Agregado' : 'Actualizado', 'ok');
          onboarding = await API.get('/api/onboarding');
          vista(layout, ir);
        } catch (e) {
          // El mensaje del servidor explica exactamente qué está mal; se muestra
          // tal cual en vez de un genérico "error al guardar".
          hoja.querySelector('#error-form').innerHTML =
            `<div class="aviso error">${UI.esc(e.message)}</div>`;
        }
      };
    });
  }

  async function borrar(recurso, id, nombre, layout, ir) {
    const meta = RECURSOS[recurso];
    const ok = await UI.confirmar(
      `Borrar ${meta.singular}`,
      `Se elimina "${nombre}" del inventario. Los relevamientos ya cargados que `
      + 'lo mencionan no se modifican, pero el ítem dejará de contarse en los '
      + 'cálculos futuros.', 'Borrar', 'btn-rojo');
    if (!ok) return;
    try {
      await API.del(`/api/inventario/${recurso}/${id}`);
      UI.toast('Eliminado', 'ok');
      onboarding = await API.get('/api/onboarding');
      vista(layout, ir);
    } catch (e) { UI.toast(e.message, 'error'); }
  }

  /* ====================================================== tab Parámetros === */

  const PARAMETROS_SIMPLES = [
    { clave: 'iluminacion_objetivo', etiqueta: 'Iluminación: mínimo encendidas',
      tipo: 'porcentaje' },
    { clave: 'iluminacion_horario_verano', etiqueta: 'Medición iluminación (verano)',
      tipo: 'texto' },
    { clave: 'iluminacion_horario_invierno', etiqueta: 'Medición iluminación (invierno)',
      tipo: 'texto' },
    { clave: 'asientos_minimo', etiqueta: 'Asientos mínimos en preembarque',
      tipo: 'entero' },
    { clave: 'tomas_por_100_pax', etiqueta: 'Tomas cada 100 pasajeros hora pico',
      tipo: 'entero' },
    { clave: 'elevacion_indisp_max_evento_hs',
      etiqueta: 'Elevación: indisponibilidad máxima por evento (hs)', tipo: 'numero' },
    { clave: 'elevacion_horas_dia', etiqueta: 'Elevación: horas por día para el cálculo',
      tipo: 'numero',
      ayuda: '24 hs es la única base con la que los topes de 60/48 hs y los '
           + 'mínimos de 91,66%/93% resultan coherentes entre sí.' },
  ];

  async function panelParametros(panel, layout, ir) {
    const [cfg, items] = await Promise.all([
      cargarConfig(), API.get('/api/los/items'),
    ]);

    panel.innerHTML = `
      <div class="tarjeta">
        <h2>Ponderaciones de la certificación</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          Deben sumar 100%. El pliego permite modificarlas de común acuerdo
          después del primer año.
        </p>
        ${formPesos(cfg.pesos)}
      </div>

      <div class="tarjeta">
        <h2>Penalización por no conformidad</h2>
        <div class="aviso advertencia">
          <strong>No surge del pliego</strong>
          El PET indica que la calidad se ajusta por la cantidad de no
          conformidades pero no fija la fórmula. Por eso el descuento viene
          <strong>desactivado</strong>: las NC se registran e informan, pero no
          bajan el importe. Activalo solo si se negocia un criterio con el
          contratista, y marcá la casilla de confirmado.
        </div>
        ${campoConfig({ clave: 'penalizacion_nc_activa', tipo: 'booleano',
                        etiqueta: 'Aplicar descuento por no conformidades',
                        ayuda: 'Desactivado, las NC se registran e informan '
                             + 'pero no descuentan del importe. Activar solo '
                             + 'si se acordó un criterio con el contratista: '
                             + 'el pliego no fija ninguna fórmula.' },
                      cfg.penalizacion_nc_activa)}
        ${campoConfig({ clave: 'penalizacion_por_nc', tipo: 'porcentaje',
                        etiqueta: 'Descuento por cada NC abierta',
                        ayuda: 'Solo se aplica si el descuento está activado.' },
                      cfg.penalizacion_por_nc)}
        ${campoConfig({ clave: 'penalizacion_nc_tope_activo', tipo: 'booleano',
                        etiqueta: 'Aplicar un tope al descuento acumulado',
                        ayuda: 'Desactivado, el descuento crece con cada NC. '
                             + 'Con tope, en cuanto se alcanza deja de '
                             + 'distinguir un mes de otro: como cada desvío '
                             + 'genera una NC, cualquier mes real lo alcanza.' },
                      cfg.penalizacion_nc_tope_activo)}
        ${campoConfig({ clave: 'penalizacion_nc_tope', tipo: 'porcentaje',
                        etiqueta: 'Tope acumulado del descuento',
                        ayuda: 'Solo se aplica si el tope está activado.' },
                      cfg.penalizacion_nc_tope)}
        ${campoConfig({ clave: 'penalizacion_nc_confirmada', tipo: 'booleano',
                        etiqueta: 'Criterio acordado con el contratista' },
                      cfg.penalizacion_nc_confirmada)}
      </div>

      <div class="tarjeta">
        <h2>Confort térmico</h2>
        ${formConfort(cfg.confort_termico)}
      </div>

      <div class="tarjeta">
        <h2>Umbrales LoS</h2>
        ${PARAMETROS_SIMPLES.map((c) => campoConfig(c, cfg[c.clave])).join('')}
      </div>

      <div class="tarjeta">
        <h2>Ítems del módulo LoS</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          Los ítems desactivados se excluyen de todos los cálculos y no bajan
          el porcentaje global.
        </p>
        <div class="lista-items">
          ${items.items.map((i) => `
            <div class="item">
              <span class="texto">
                <span class="nombre-item">${UI.esc(i.nombre)}</span>
                <span class="obs">${i.requiere_configuracion
                  ? 'Requiere carga de inventario' : 'Listo para relevar'}</span>
              </span>
              <button class="btn-mini ${i.aplica ? '' : 'peligro'}"
                      data-item="${i.clave}" data-aplica="${i.aplica ? 1 : 0}">
                ${i.aplica ? 'Aplica' : 'No aplica'}
              </button>
            </div>`).join('')}
        </div>
      </div>`;

    conectarCampos(panel, layout, ir);

    panel.querySelectorAll('[data-item]').forEach((b) => {
      b.onclick = async () => {
        try {
          await API.put(`/api/los/items/${b.dataset.item}`,
                        { aplica: b.dataset.aplica !== '1' });
          vista(layout, ir);
        } catch (e) { UI.toast(e.message, 'error'); }
      };
    });

    $('#guardar-pesos').onclick = () => guardarPesos(panel, layout, ir);
    $('#guardar-confort').onclick = () => guardarConfort(panel, layout, ir);

  }

  const NOMBRE_PESO = {
    documentacion: 'Documentación obligatoria',
    ley_19587: 'Ley 19587 (seguridad e higiene)',
    programacion_trabajos: 'Programación de trabajos',
    maquinarias: 'Maquinarias en programación',
    insumos: 'Disponibilidad de insumos',
    calidad_servicio: 'Calidad de servicio',
  };

  function formPesos(pesos) {
    return `
      ${Object.keys(NOMBRE_PESO).map((k) => `
        <div class="campo-linea">
          <label for="peso-${k}">${UI.esc(NOMBRE_PESO[k])}</label>
          <div class="con-sufijo">
            <input id="peso-${k}" type="number" min="0" max="100" step="1"
                   inputmode="numeric" data-peso="${k}"
                   value="${Math.round((pesos[k] || 0) * 100)}">
            <span>%</span>
          </div>
        </div>`).join('')}
      <p id="suma-pesos" class="suma"></p>
      <button class="btn btn-primario btn-bloque" id="guardar-pesos">
        Guardar ponderaciones
      </button>`;
  }

  async function guardarPesos(panel, layout, ir) {
    const pesos = {};
    let total = 0;
    panel.querySelectorAll('[data-peso]').forEach((el) => {
      const pct = parseFloat(el.value) || 0;
      total += pct;
      pesos[el.dataset.peso] = pct / 100;
    });
    // Se avisa en porcentajes, que es lo que el usuario está viendo.
    if (Math.abs(total - 100) > 0.1) {
      return UI.toast(
        `Las ponderaciones suman ${total.toFixed(0)}%. Tienen que sumar 100%.`,
        'error', 5000);
    }
    try {
      await API.put('/api/config/pesos', { valor: pesos });
      UI.toast('Ponderaciones guardadas', 'ok');
      vista(layout, ir);
    } catch (e) {
      UI.toast(e.message, 'error', 6000);
    }
  }

  function formConfort(c) {
    const fila = (est, p) => `
      <h3 style="font-size:14px;margin:14px 0 8px">
        ${est === 'VERANO' ? 'Verano' : 'Invierno'} — categoría ${p.categoria || '—'}
      </h3>
      <div class="campo-linea">
        <label for="ct-${est}-min">Temperatura mínima (°C)</label>
        <input id="ct-${est}-min" type="number" step="0.1" value="${p.min}">
      </div>
      <div class="campo-linea">
        <label for="ct-${est}-max">Temperatura máxima (°C)</label>
        <input id="ct-${est}-max" type="number" step="0.1" value="${p.max}">
      </div>
      `;

    return `${fila('VERANO', c.VERANO)}${fila('INVIERNO', c.INVIERNO)}
      <button class="btn btn-primario btn-bloque" id="guardar-confort"
              style="margin-top:14px">Guardar confort térmico</button>`;
  }

  /**
   * Zonas donde se mide la temperatura. Van en configuración y no en el código
   * para que habilitar una sala nueva no exija una actualización de la app.
   */
  /** Alta, baja y guardado de las zonas de medición térmica. */
  function conectarZonas(panel, layout, ir) {
    const leerZonas = () => [...panel.querySelectorAll('[data-zona]')]
      .map((el) => el.value.trim()).filter(Boolean);

    panel.querySelector('#agregar-zona').onclick = () => {
      const lista = panel.querySelector('#lista-zonas');
      const i = lista.querySelectorAll('[data-zona]').length;
      const fila = document.createElement('div');
      fila.className = 'item';
      fila.innerHTML = `<input type="text" data-zona="${i}" placeholder="Nombre de la zona"
                               style="flex:1;min-height:44px">
                        <button class="btn-mini peligro">Quitar</button>`;
      fila.querySelector('button').onclick = () => fila.remove();
      lista.appendChild(fila);
      fila.querySelector('input').focus();
    };

    panel.querySelectorAll('[data-quitar-zona]').forEach((b) => {
      b.onclick = () => b.closest('.item').remove();
    });

    panel.querySelector('#guardar-zonas').onclick = async () => {
      const zonas = leerZonas();
      if (!zonas.length) {
        return UI.toast('Tiene que haber al menos una zona', 'error');
      }
      try {
        await API.put('/api/config/confort_zonas', { valor: zonas });
        UI.toast('Zonas guardadas', 'ok');
        vista(layout, ir);
      } catch (e) { UI.toast(e.message, 'error', 5000); }
    };
  }

  function formZonasConfort(zonas) {
    return `
      <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
        Una fila por zona. El auditor carga la temperatura de cada una durante
        la operación de vuelos.
      </p>
      <div class="lista-items" id="lista-zonas">
        ${zonas.map((z, i) => `
          <div class="item">
            <input type="text" data-zona="${i}" value="${UI.esc(z)}"
                   style="flex:1;min-height:44px">
            <button class="btn-mini peligro" data-quitar-zona="${i}">Quitar</button>
          </div>`).join('')}
      </div>
      <div class="acciones" style="margin-top:12px">
        <button class="btn" id="agregar-zona">+ Agregar zona</button>
        <button class="btn btn-primario" id="guardar-zonas">Guardar zonas</button>
      </div>`;
  }

  async function guardarConfort(panel, layout, ir) {
    const cfg = await cargarConfig();
    const valor = JSON.parse(JSON.stringify(cfg.confort_termico));
    for (const est of ['VERANO', 'INVIERNO']) {
      valor[est].min = parseFloat(panel.querySelector(`#ct-${est}-min`).value);
      valor[est].max = parseFloat(panel.querySelector(`#ct-${est}-max`).value);

    }
    try {
      await API.put('/api/config/confort_termico', { valor });
      UI.toast('Confort térmico guardado', 'ok');
      vista(layout, ir);
    } catch (e) { UI.toast(e.message, 'error', 6000); }
  }

  /* ========================================================= tab Período === */

  async function panelPeriodo(panel) {
    const periodo = UI.periodoActual();
    const [datos, insumos, equipamiento] = await Promise.all([
      API.get(`/api/periodos/${periodo}/datos`),
      API.get(`/api/insumos?periodo=${periodo}`),
      API.get(`/api/periodos/${periodo}/equipamiento`),
    ]);
    const d = datos.datos || {};

    panel.innerHTML = `
      <div class="tarjeta">
        <h2>Datos del período · ${UI.esc(UI.nombrePeriodo(periodo))}</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          Alimentan los ítems 1 a 5 de la certificación mensual.
        </p>

        <div class="campo">
          <label for="pd-hh">Horas hombre programadas del mes</label>
          <input id="pd-hh" type="number" min="0" step="0.5"
                 value="${d.horas_hombre_programadas ?? ''}">
        </div>
        <div class="campo">
          <label for="pd-hhp">Horas hombre perdidas por ausencias</label>
          <input id="pd-hhp" type="number" min="0" step="0.5"
                 value="${d.horas_hombre_perdidas ?? 0}">
        </div>
        <div class="campo">
          <label for="pd-monto">Monto mensual adjudicado
            <span class="ayuda">Opcional. Si se carga, el informe muestra el
              importe a certificar.</span></label>
          <input id="pd-monto" type="number" min="0" step="0.01"
                 value="${d.monto_adjudicado ?? ''}">
        </div>

        <h3 style="font-size:14px;margin:18px 0 8px">Ítems binarios</h3>
        <div class="aviso info">
          Estos ítems dan 100% con cero hallazgos. Para que "nadie los revisó"
          no se confunda con "se revisaron y estaban bien", exigen que marques
          explícitamente que se verificaron.
        </div>
        <div class="campo-linea">
          <label for="pd-docv">Documentación verificada este mes</label>
          <select id="pd-docv">
            <option value="0" ${!d.documentacion_verificada ? 'selected' : ''}>No</option>
            <option value="1" ${d.documentacion_verificada ? 'selected' : ''}>Sí</option>
          </select>
        </div>
        <div class="campo-linea">
          <label for="pd-doch">Hallazgos de documentación</label>
          <input id="pd-doch" type="number" min="0"
                 value="${d.hallazgos_documentacion ?? 0}">
        </div>
        <div class="campo-linea">
          <label for="pd-leyv">Ley 19587 verificada este mes</label>
          <select id="pd-leyv">
            <option value="0" ${!d.ley_19587_verificada ? 'selected' : ''}>No</option>
            <option value="1" ${d.ley_19587_verificada ? 'selected' : ''}>Sí</option>
          </select>
        </div>
        <div class="campo-linea">
          <label for="pd-leyh">Hallazgos Ley 19587</label>
          <input id="pd-leyh" type="number" min="0"
                 value="${d.hallazgos_ley_19587 ?? 0}">
        </div>

        <button class="btn btn-primario btn-bloque" id="guardar-periodo"
                style="margin-top:14px">Guardar datos del período</button>
      </div>

      ${bloqueEquipamiento(equipamiento)}

      <div class="tarjeta">
        <h2>Insumos controlados</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          El ítem 5 mide qué proporción de insumos tiene stock por encima de su
          punto de pedido. La lista persiste de mes a mes.
        </p>
        <div class="lista-items" id="lista-insumos">
          ${insumos.insumos.length ? insumos.insumos.map((i) => `
            <div class="item ${i.stock !== null && i.stock < i.punto_pedido ? 'total' : ''}">
              <span class="texto">
                <span class="nombre-item">${UI.esc(i.nombre)}</span>
                <span class="obs">Punto de pedido: ${i.punto_pedido}
                  ${i.unidad ? UI.esc(i.unidad) : ''}</span>
              </span>
              <div class="con-sufijo" style="max-width:130px">
                <input type="number" min="0" step="0.01" data-insumo="${i.id}"
                       placeholder="stock" value="${i.stock ?? ''}">
              </div>
            </div>`).join('')
            : `<div class="aviso advertencia" style="margin:0">
                 <strong>Sin insumos cargados</strong>
                 El ítem "Disponibilidad de insumos" queda como Sin datos y su
                 peso se redistribuye entre los demás.
               </div>`}
        </div>
        <button class="btn btn-bloque" id="nuevo-insumo" style="margin-top:12px">
          + Agregar insumo
        </button>
      </div>`;

    $('#guardar-periodo').onclick = async () => {
      const num = (id) => {
        const v = panel.querySelector(id).value;
        return v === '' ? null : parseFloat(v);
      };
      const cuerpo = {
        horas_hombre_programadas: num('#pd-hh'),
        horas_hombre_perdidas: num('#pd-hhp') || 0,
        monto_adjudicado: num('#pd-monto'),
        documentacion_verificada: parseInt($('#pd-docv').value, 10),
        hallazgos_documentacion: num('#pd-doch') || 0,
        ley_19587_verificada: parseInt($('#pd-leyv').value, 10),
        hallazgos_ley_19587: num('#pd-leyh') || 0,
      };
      try {
        await API.put(`/api/periodos/${periodo}/datos`, cuerpo);
        UI.toast('Datos del período guardados', 'ok');
      } catch (e) { UI.toast(e.message, 'error'); }
    };

    // El stock se guarda al salir del campo: son muchos números y un botón
    // por fila sería peor.
    panel.querySelectorAll('[data-insumo]').forEach((el) => {
      el.onchange = async () => {
        if (el.value === '') return;
        try {
          await API.put(`/api/periodos/${periodo}/insumos/${el.dataset.insumo}`,
                        { stock: parseFloat(el.value) });
          UI.toast('Stock registrado', 'ok', 1500);
        } catch (e) { UI.toast(e.message, 'error'); }
      };
    });

    $('#nuevo-insumo').onclick = () => formularioInsumo(panel);

    $('#guardar-equipamiento').onclick = async () => {
      const exigidos = [...panel.querySelectorAll('[data-equipo-periodo]:checked')]
        .map((el) => parseInt(el.dataset.equipoPeriodo, 10));
      try {
        await API.put(`/api/periodos/${periodo}/equipamiento`, { exigidos });
        UI.toast('Equipos del período guardados', 'ok');
        panelPeriodo(panel);
      } catch (e) { UI.toast(e.message, 'error'); }
    };
  }

  /**
   * Ítem 4 de la certificación. Se declara una vez al inicio del mes qué
   * equipos rigen; los días fuera de servicio se registran después en cada
   * control diario.
   */
  function bloqueEquipamiento(equipamiento) {
    const r = equipamiento.resultado;
    const conFaltas = r.equipos_con_faltas || [];

    const estado = r.porcentaje === null
      ? `<div class="aviso info" style="margin:0 0 14px">
           <strong>Sin datos todavía</strong>
           ${UI.esc(r.motivo || 'Se calcula con los controles diarios cerrados.')}
         </div>`
      : `<div class="aviso ${r.porcentaje === 1 ? 'info' : 'advertencia'}"
              style="margin:0 0 14px">
           <strong>Disponibilidad ${Calc.porcentaje(r.porcentaje)}</strong>
           Promedio sobre ${r.dias_considerados} día(s) con control cerrado.
           ${conFaltas.length
             ? conFaltas.map((e) => `${UI.esc(e.nombre)}: ${e.dias_fuera_servicio} día(s)`)
                        .join(' · ')
             : 'Todos los equipos estuvieron disponibles.'}
         </div>`;

    return `
      <div class="tarjeta">
        <h2>Maquinarias y equipos exigidos</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          El ítem 4 mide qué proporción de los equipos exigidos estuvo
          disponible. Marcá cuáles rigen este período; los días fuera de
          servicio se cargan en el control diario.
        </p>
        ${estado}
        <div class="lista-items">
          ${equipamiento.equipos.map((e) => `
            <label class="item" style="cursor:pointer">
              <span class="texto">
                <span class="nombre-item">${UI.esc(e.nombre)}</span>
              </span>
              <input type="checkbox" data-equipo-periodo="${e.id}"
                     ${e.exigido ? 'checked' : ''}
                     style="width:24px;height:24px;flex:none">
            </label>`).join('')}
        </div>
        <button class="btn btn-primario btn-bloque" id="guardar-equipamiento"
                style="margin-top:12px">Guardar equipos del período</button>
      </div>`;
  }

  function formularioInsumo(panel) {
    UI.abrirHoja(`
      <h3>Agregar insumo</h3>
      <p class="sub">Se controla su stock todos los meses.</p>
      <div id="error-insumo"></div>
      <div class="campo">
        <label for="i-nombre">Nombre</label>
        <input id="i-nombre" type="text" autofocus>
      </div>
      <div class="campo">
        <label for="i-punto">Punto de pedido
          <span class="ayuda">Stock mínimo por debajo del cual se considera
            faltante.</span></label>
        <input id="i-punto" type="number" min="0" step="0.01" inputmode="numeric">
      </div>
      <div class="campo">
        <label for="i-unidad">Unidad <span class="ayuda">Opcional</span></label>
        <input id="i-unidad" type="text" placeholder="litros, rollos, kg…">
      </div>
      <div class="acciones">
        <button class="btn" data-cancelar>Cancelar</button>
        <button class="btn btn-primario" data-guardar>Agregar</button>
      </div>`, (hoja, cerrar) => {
      hoja.querySelector('[data-cancelar]').onclick = cerrar;
      hoja.querySelector('[data-guardar]').onclick = async () => {
        try {
          await API.post('/api/insumos', {
            nombre: hoja.querySelector('#i-nombre').value.trim(),
            punto_pedido: parseFloat(hoja.querySelector('#i-punto').value),
            unidad: hoja.querySelector('#i-unidad').value.trim() || null,
          });
          cerrar();
          UI.toast('Insumo agregado', 'ok');
          panelPeriodo(panel);
        } catch (e) {
          hoja.querySelector('#error-insumo').innerHTML =
            `<div class="aviso error">${UI.esc(e.message)}</div>`;
        }
      };
    });
  }

  /* ============================================== campos de configuración === */

  async function cargarConfig() {
    const r = await API.get('/api/config');
    const mapa = {};
    r.config.forEach((c) => { mapa[c.clave] = c.valor; });
    return mapa;
  }

  function campoConfig(c, valor) {
    if (c.tipo === 'booleano') {
      // La ayuda también acá: los otros dos tipos ya la mostraban y en un
      // interruptor —donde lo que hay que explicar es la consecuencia de
      // activarlo— hace más falta que en ninguno.
      return `<div class="campo-linea">
        <label for="cfg-${c.clave}">${UI.esc(c.etiqueta)}
          ${c.ayuda ? `<span class="ayuda">${UI.esc(c.ayuda)}</span>` : ''}</label>
        <select id="cfg-${c.clave}" data-config="${c.clave}" data-tipo="booleano">
          <option value="0" ${!valor ? 'selected' : ''}>No</option>
          <option value="1" ${valor ? 'selected' : ''}>Sí</option>
        </select>
      </div>`;
    }
    if (c.tipo === 'porcentaje') {
      return `<div class="campo">
        <label for="cfg-${c.clave}">${UI.esc(c.etiqueta)}
          ${c.ayuda ? `<span class="ayuda">${UI.esc(c.ayuda)}</span>` : ''}</label>
        <div class="con-sufijo">
          <input id="cfg-${c.clave}" type="number" min="0" max="100" step="0.1"
                 inputmode="decimal" data-config="${c.clave}" data-tipo="porcentaje"
                 value="${(valor * 100).toFixed(1).replace(/\.0$/, '')}">
          <span>%</span>
        </div>
      </div>`;
    }
    const tipoInput = (c.tipo === 'entero' || c.tipo === 'numero') ? 'number' : 'text';
    return `<div class="campo">
      <label for="cfg-${c.clave}">${UI.esc(c.etiqueta)}
        ${c.ayuda ? `<span class="ayuda">${UI.esc(c.ayuda)}</span>` : ''}</label>
      <input id="cfg-${c.clave}" type="${tipoInput}"
             ${c.tipo === 'entero' ? 'step="1" inputmode="numeric"' : ''}
             ${c.tipo === 'numero' ? 'step="0.1" inputmode="decimal"' : ''}
             data-config="${c.clave}" data-tipo="${c.tipo}"
             value="${UI.esc(valor)}">
    </div>`;
  }

  /** Guarda al salir del campo: evita un botón por cada parámetro. */
  function conectarCampos(panel, layout, ir) {
    panel.querySelectorAll('[data-config]').forEach((el) => {
      el.dataset.original = el.value;
      el.onchange = async () => {
        if (el.value === el.dataset.original) return;

        let valor = el.value;
        if (el.dataset.tipo === 'porcentaje') {
          // El servidor trabaja en fracciones (0,90) pero el campo muestra
          // porcentajes (90%). Si el rango se valida allá, el mensaje le habla
          // al usuario de "1.5" cuando escribió "150" — se valida acá para
          // poder responderle en las mismas unidades que ve.
          const pct = parseFloat(valor);
          if (isNaN(pct) || pct < 0 || pct > 100) {
            el.value = el.dataset.original;
            el.classList.add('invalido');
            return UI.toast('Ingresá un porcentaje entre 0 y 100', 'error');
          }
          valor = pct / 100;
        } else if (el.dataset.tipo === 'entero') valor = parseInt(valor, 10);
        else if (el.dataset.tipo === 'numero') valor = parseFloat(valor);
        else if (el.dataset.tipo === 'booleano') valor = el.value === '1';

        try {
          await API.put(`/api/config/${el.dataset.config}`, { valor });
          el.dataset.original = el.value;
          el.classList.remove('invalido');
          UI.toast('Guardado', 'ok', 1500);
        } catch (e) {
          // Se revierte el campo: dejarlo con el valor rechazado haría creer
          // que quedó guardado.
          el.value = el.dataset.original;
          el.classList.add('invalido');
          UI.toast(e.message, 'error', 6000);
        }
      };
    });

    const suma = panel.querySelector('#suma-pesos');
    if (suma) {
      const recalcular = () => {
        let total = 0;
        panel.querySelectorAll('[data-peso]').forEach((el) => {
          total += parseFloat(el.value) || 0;
        });
        const ok = Math.abs(total - 100) < 0.1;
        suma.textContent = `Suma: ${total.toFixed(0)}%`
                         + (ok ? '' : ' — debe sumar 100%');
        suma.className = 'suma' + (ok ? ' ok' : ' error');
      };
      panel.querySelectorAll('[data-peso]').forEach((el) => {
        el.oninput = recalcular;
      });
      recalcular();
    }
  }

  return { vista };
})();
