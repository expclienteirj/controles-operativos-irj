/* Informes y exports (sección 5 del pliego).
 *
 * Pantalla de descargas: los dos PDF (limpieza y LoS) y los CSV de datos
 * crudos. Todo lo genera el backend; acá solo se elige el período y se
 * dispara la bajada.
 *
 * Antes de ofrecer la descarga se muestra un resumen de lo que va a contener
 * el informe: firmar un PDF de un mes a medio auditar sin saberlo es
 * exactamente lo que el resto de la app trata de evitar. */

const Informes = (() => {
  let periodo = null;

  const $ = (sel) => document.querySelector(sel);

  const EXPORTS = [
    { clave: 'controles', titulo: 'Controles diarios',
      detalle: 'Una fila por sector y día, con el porcentaje que aportó al mes.' },
    { clave: 'desvios', titulo: 'Desvíos registrados',
      detalle: 'Cada hallazgo con su sector, ítem, severidad, observación y auditor.' },
    { clave: 'no-conformidades', titulo: 'No conformidades',
      detalle: 'Las NC del período con su prioridad, estado y fecha de resolución.' },
    { clave: 'los', titulo: 'Niveles de servicio',
      detalle: 'Estado de los 11 ítems del manual LoS.' },
  ];

  /* ==================================================== vista principal === */

  async function vista(layout, ir) {
    periodo = periodo || UI.periodoActual();
    layout('Informes', UI.nombrePeriodo(periodo),
           '<div class="vacio">Cargando…</div>', { volver: '/' });

    if (!navigator.onLine) {
      return ($('.contenido').innerHTML = `
        <div class="aviso advertencia">
          <strong>Sin conexión</strong>
          Los informes los genera el servidor, así que necesitan red. Podés
          seguir cargando controles y descargarlos cuando vuelva la conexión.
        </div>`);
    }

    let resumen, cert, dash;
    try {
      [resumen, cert, dash] = await Promise.all([
        API.get(`/api/periodos/${periodo}/limpieza`),
        API.get(`/api/periodos/${periodo}/certificacion`),
        API.get(`/api/los/dashboard?periodo=${periodo}`),
      ]);
    } catch (e) {
      // El aviso de arriba cubre el caso en que el navegador se sabe sin red.
      // Este cubre el otro, que es el que se ve en la terminal: se cree
      // conectado —wifi tomada, sin salida— y el pedido muere igual. Sin esto
      // el auditor leía el "Failed to fetch" crudo del navegador.
      return ($('.contenido').innerHTML = `<div class="aviso error">${e.codigo
        ? `No se pudo cargar el período: ${UI.esc(e.message)}`
        : 'No se pudo conectar con el servidor. Los informes los genera el '
          + 'servidor, así que necesitan red: probá de nuevo cuando vuelva la '
          + 'conexión.'}</div>`);
    }

    // Los controles del mes alimentan el selector de día: solo se ofrecen
    // los días que realmente tienen control, no un calendario vacío.
    let controles = [];
    try {
      controles = (await API.get(`/api/controles?periodo=${periodo}`)).controles;
    } catch (e) { /* el informe mensual sigue disponible */ }

    $('.contenido').innerHTML = `
      ${selectorPeriodo()}
      ${tarjetaLimpieza(resumen, cert)}
      ${tarjetaDia(controles)}
      ${tarjetaLoS(dash)}
      ${tarjetaExports()}`;

    $('#periodo').onchange = (e) => { periodo = e.target.value; vista(layout, ir); };

    const btnDia = $('#pdf-dia');
    if (btnDia) {
      btnDia.onclick = () => {
        const [fecha, turno] = $('#dia').value.split('|');
        bajar(`/api/controles/fecha/${fecha}/informe?turno=${turno}`,
              `control-${fecha}-${turno.toLowerCase()}.pdf`, btnDia);
      };
    }

    $('#pdf-limpieza').onclick = () => bajar(
      `/api/periodos/${periodo}/informe/limpieza`, `informe-limpieza-${periodo}.pdf`,
      $('#pdf-limpieza'));
    $('#pdf-limpieza-sf').onclick = () => bajar(
      `/api/periodos/${periodo}/informe/limpieza?fotos=0`,
      `informe-limpieza-${periodo}.pdf`, $('#pdf-limpieza-sf'));
    $('#pdf-los').onclick = () => bajar(
      `/api/periodos/${periodo}/informe/los`, `informe-los-${periodo}.pdf`,
      $('#pdf-los'));

    document.querySelectorAll('[data-export]').forEach((b) => {
      b.onclick = () => bajar(
        `/api/periodos/${periodo}/export/${b.dataset.export}`,
        `${b.dataset.export}-${periodo}.csv`, b);
    });
  }

  /** Últimos 12 meses: alcanza para cerrar un ejercicio y revisar atrás. */
  function selectorPeriodo() {
    const hoy = new Date();
    const opciones = [];
    for (let i = 0; i < 12; i++) {
      const d = new Date(hoy.getFullYear(), hoy.getMonth() - i, 1);
      const p = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
      opciones.push(`<option value="${p}" ${p === periodo ? 'selected' : ''}>
        ${UI.esc(UI.nombrePeriodo(p))}</option>`);
    }
    return `
      <div class="tarjeta">
        <div class="campo" style="margin:0">
          <label for="periodo">Período del informe</label>
          <select id="periodo">${opciones.join('')}</select>
        </div>
      </div>`;
  }

  /* -------------------------------------------------- informe de limpieza -- */

  function tarjetaLimpieza(resumen, cert) {
    const comp = resumen.completitud;
    const cob = Math.round((comp.cobertura || 0) * 100);
    const sinDatos = resumen.dias_considerados === 0;

    // El aviso va antes del botón, no después: el objetivo es que se lea
    // antes de descargar, no que explique un PDF ya emitido.
    const aviso = sinDatos
      ? `<div class="aviso advertencia">
           <strong>Sin controles cerrados en el período</strong>
           El informe se genera igual y deja constancia de que no hubo auditorías,
           pero no habrá porcentaje de cumplimiento que certificar.
         </div>`
      : !comp.cobertura_suficiente
        ? `<div class="aviso advertencia">
             <strong>Cobertura ${cob}% — por debajo del mínimo</strong>
             El informe mostrará ${comp.dias_cerrados.length} de
             ${comp.dias_esperados} días auditados. El porcentaje sale solo de
             esos días y es poco representativo del mes.
           </div>`
        : !comp.completo
          ? `<div class="aviso info">
               <strong>Cobertura ${cob}%</strong>
               ${comp.dias_cerrados.length} de ${comp.dias_esperados} días
               auditados. Los días sin auditar no penalizan al contratista.
             </div>`
          : `<div class="aviso info">
               <strong>Mes completo</strong>
               Los ${comp.dias_esperados} días tienen control cerrado.
             </div>`;

    const abiertos = comp.dias_abiertos.length
      ? `<div class="aviso advertencia">
           <strong>${comp.dias_abiertos.length} control(es) sin cerrar</strong>
           No entran en el informe. Conviene cerrarlos antes de emitirlo.
         </div>` : '';

    return `
      <div class="tarjeta">
        <h2>Informe mensual de limpieza</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          Cumplimiento por sector, certificación de los 6 ítems, no conformidades
          con su evidencia fotográfica y espacio de firmas.
        </p>
        ${aviso}
        ${abiertos}
        <div class="resumen-informe">
          <div><span>Cumplimiento</span><strong>${Calc.porcentaje(resumen.porcentaje_general)}</strong></div>
          <div><span>A certificar</span><strong>${Calc.porcentaje(cert.porcentaje)}</strong></div>
          <div><span>Días auditados</span><strong>${resumen.dias_considerados}/${resumen.dias_del_mes}</strong></div>
          <div><span>No conformidades</span><strong>${cert.no_conformidades_abiertas}</strong></div>
        </div>
        <button class="btn btn-primario btn-bloque btn-grande" id="pdf-limpieza">
          Descargar informe en PDF
        </button>
        <button class="btn btn-bloque" id="pdf-limpieza-sf" style="margin-top:8px">
          Versión liviana, sin fotos
        </button>
      </div>`;
  }

  /* ------------------------------------------------------ informe diario -- */

  /**
   * Informe de un día puntual. Es el documento del reclamo concreto al
   * contratista, así que lleva las fotos a tamaño grande — a diferencia del
   * mensual, donde son miniaturas de respaldo.
   */
  function tarjetaDia(controles) {
    if (!controles.length) {
      return `
        <div class="tarjeta">
          <h2>Informe de una recorrida</h2>
          <p style="margin:0;font-size:13px;color:var(--gris)">
            No hay controles registrados en ${UI.esc(UI.nombrePeriodo(periodo))}.
          </p>
        </div>`;
    }

    // Del más reciente al más viejo: casi siempre se busca el último.
    // El valor lleva fecha y turno: el informe es de UNA recorrida, y con dos
    // por día el selector tiene que dejar elegir cuál.
    const NOMBRE_TURNO = { MANANA: 'mañana', TARDE: 'tarde' };
    const opciones = [...controles].reverse().map((c) => `
      <option value="${c.fecha}|${c.turno}">
        ${UI.esc(UI.fechaCorta(c.fecha))} · ${NOMBRE_TURNO[c.turno] || c.turno} ·
        ${c.estado === 'CERRADO' ? 'cerrado' : 'en curso'}
      </option>`).join('');

    const abiertos = controles.filter((c) => c.estado !== 'CERRADO').length;

    return `
      <div class="tarjeta">
        <h2>Informe de una recorrida</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          Sectores de la recorrida, equipamiento y cada desvío con su
          evidencia fotográfica a tamaño completo. Cada turno tiene su informe.
        </p>
        ${abiertos ? `<div class="aviso info">
          Hay ${abiertos} control(es) sin cerrar. Se pueden descargar igual,
          pero el informe lo aclara y los sectores sin confirmar no computan.
        </div>` : ''}
        <div class="campo">
          <label for="dia">Día del control</label>
          <select id="dia">${opciones}</select>
        </div>
        <button class="btn btn-primario btn-bloque btn-grande" id="pdf-dia">
          Descargar informe del día
        </button>
      </div>`;
  }

  /* -------------------------------------------------------- informe LoS --- */

  function tarjetaLoS(dash) {
    const faltaConfig = dash.requieren_configuracion.length;
    const aviso = faltaConfig
      ? `<div class="aviso advertencia">
           <strong>${faltaConfig} ítem(s) sin inventario cargado</strong>
           Aparecerán como "Requiere configuración" en el informe. Se cargan
           desde Configuración del Aeropuerto.
         </div>`
      : dash.items_sin_datos.length
        ? `<div class="aviso info">
             <strong>${dash.items_sin_datos.length} ítem(s) sin relevar</strong>
             No se computan como incumplimiento, pero tampoco como cumplimiento.
           </div>`
        : '';

    return `
      <div class="tarjeta">
        <h2>Informe de Niveles de Servicio</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          Estado de los 11 ítems del manual con las mediciones del período y
          el detalle de cada desvío.
        </p>
        ${aviso}
        <div class="resumen-informe">
          <div><span>Ítems que cumplen</span><strong>${Calc.porcentaje(dash.porcentaje)}</strong></div>
          <div><span>Evaluados</span><strong>${dash.items_evaluados}/${dash.items_aplicables}</strong></div>
          <div><span>Sin datos</span><strong>${dash.items_sin_datos.length}</strong></div>
          <div><span>No aplica</span><strong>${dash.no_aplica.length}</strong></div>
        </div>
        <button class="btn btn-primario btn-bloque btn-grande" id="pdf-los">
          Descargar informe en PDF
        </button>
      </div>`;
  }

  /* ------------------------------------------------------------ exports --- */

  function tarjetaExports() {
    return `
      <div class="tarjeta">
        <h2>Datos crudos (CSV)</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--gris)">
          Para abrir en Excel o cruzar con otros sistemas. Separador punto y coma.
        </p>
        <div class="lista-items">
          ${EXPORTS.map((e) => `
            <div class="item">
              <span class="texto">
                <span class="nombre-item">${UI.esc(e.titulo)}</span>
                <span class="obs">${UI.esc(e.detalle)}</span>
              </span>
              <button class="btn-mini" data-export="${e.clave}">Descargar</button>
            </div>`).join('')}
        </div>
      </div>`;
  }

  /* ------------------------------------------------------------ descarga -- */

  async function bajar(ruta, nombre, boton) {
    const original = boton.textContent;
    boton.disabled = true;
    boton.textContent = 'Generando…';
    try {
      const archivo = await API.descargar(ruta, nombre);
      UI.toast(`Descargado: ${archivo}`, 'ok');
    } catch (e) {
      UI.toast('No se pudo generar: ' + e.message, 'error', 5000);
    } finally {
      boton.disabled = false;
      boton.textContent = original;
    }
  }

  return { vista };
})();
