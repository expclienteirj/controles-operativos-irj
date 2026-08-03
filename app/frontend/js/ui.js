/* Utilidades de interfaz: escape, toasts, hojas modales y captura de fotos. */

const UI = (() => {

  /** Escapa texto que viene del usuario o del servidor antes de inyectarlo. */
  function esc(texto) {
    if (texto === null || texto === undefined) return '';
    return String(texto)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(mensaje, tipo = '', ms = 3200) {
    const cont = document.getElementById('toasts');
    const el = document.createElement('div');
    el.className = `toast ${tipo}`;
    el.textContent = mensaje;
    cont.appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  /**
   * Toast con acción de deshacer.
   *
   * Es la red de seguridad de las acciones de un toque: en vez de frenar al
   * auditor con un diálogo de confirmación, se aplica el cambio y se le da una
   * ventana para revertirlo. Protege contra el toque accidental, que es el
   * error real, sin volver tediosa la operación repetitiva.
   */
  function toastDeshacer(mensaje, alDeshacer, ms = 6000) {
    const cont = document.getElementById('toasts');
    const el = document.createElement('div');
    el.className = 'toast ok con-accion';
    el.innerHTML = `<span class="txt"></span>
                    <button type="button" class="deshacer">Deshacer</button>`;
    el.querySelector('.txt').textContent = mensaje;

    const quitar = () => el.remove();
    const temporizador = setTimeout(quitar, ms);
    el.querySelector('.deshacer').onclick = () => {
      clearTimeout(temporizador);
      quitar();
      alDeshacer();
    };
    cont.appendChild(el);
  }

  /* --------------------------------------------------------------- modal -- */

  /**
   * Pila de hojas abiertas.
   *
   * Solo una está montada en el DOM a la vez, pero de cada una se guarda con
   * qué volver a renderizarla. Eso es lo que permite que el botón atrás de
   * Android cierre la hoja de arriba y devuelva la anterior.
   *
   * Antes la hoja solo se cerraba con Escape —irrelevante: no hay teclado
   * físico— o tocando el fondo. El botón atrás del sistema navegaba en el
   * historial de hash y, si la hoja se había abierto sin cambiar de ruta,
   * SALÍA DE LA APLICACIÓN. En la hoja de desvío eso significaba perder la
   * observación y las fotos ya cargadas.
   */
  let pila = [];

  // Entradas de historial que consumimos nosotros al cerrar a mano y que el
  // manejador de popstate no debe volver a procesar.
  let atrasPropios = 0;

  const modalEl = () => document.getElementById('modal');

  function ocultarModal() {
    const modal = modalEl();
    modal.classList.add('oculto');
    modal.innerHTML = '';
    modal.removeAttribute('aria-labelledby');
  }

  function montarHojaActual() {
    const { html, alMontar } = pila[pila.length - 1];
    const modal = modalEl();
    modal.innerHTML = `<div class="hoja">${html}</div>`;
    modal.classList.remove('oculto');

    // El diálogo tenía role="dialog" y aria-modal pero ningún nombre asociado:
    // un lector de pantalla lo anunciaba como "diálogo" sin decir de qué se
    // trata. Cada hoja empieza con un <h3> de título; se lo usa como nombre.
    const titulo = modal.querySelector('.hoja h1, .hoja h2, .hoja h3');
    if (titulo) {
      if (!titulo.id) titulo.id = 'hoja-titulo-' + Date.now();
      modal.setAttribute('aria-labelledby', titulo.id);
    } else {
      modal.removeAttribute('aria-labelledby');
    }

    // Cerrar tocando el fondo, pero no al tocar dentro de la hoja.
    modal.onclick = (e) => { if (e.target === modal) cerrarHoja(); };

    if (alMontar) alMontar(modal.querySelector('.hoja'), cerrarHoja);

    const foco = modal.querySelector('[autofocus], input, textarea, button');
    if (foco) foco.focus();
  }

  /** Saca la hoja de arriba y muestra la anterior (o ninguna). */
  function bajarHoja() {
    pila.pop();
    if (pila.length) montarHojaActual(); else ocultarModal();
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && pila.length) cerrarHoja();
  });

  window.addEventListener('popstate', () => {
    // Cierre nuestro: ya bajamos la hoja, esto es solo el eco de history.back().
    if (atrasPropios > 0) { atrasPropios -= 1; return; }
    if (!pila.length) return;

    const { alIntentarCerrar } = pila[pila.length - 1];
    if (!alIntentarCerrar) return bajarHoja();

    // El pop ya ocurrió: para poder preguntar hay que devolver la entrada al
    // historial y cerrar recién si el auditor confirma que descarta lo cargado.
    history.pushState({ hoja: pila.length }, '');
    Promise.resolve(alIntentarCerrar()).then((puede) => {
      if (puede) cerrarHoja({ forzar: true });
    });
  });

  /**
   * Abre una hoja.
   *
   * opciones.alIntentarCerrar: función que decide si se puede abandonar la
   * hoja (puede devolver una promesa). Se consulta al tocar el fondo, Escape o
   * el botón atrás — no en los caminos que guardan, que cierran con
   * `cerrarHoja({ forzar: true })`.
   */
  function abrirHoja(html, alMontar, { alIntentarCerrar = null } = {}) {
    pila.push({ html, alMontar, alIntentarCerrar });
    history.pushState({ hoja: pila.length }, '');
    montarHojaActual();
    return cerrarHoja;
  }

  /**
   * Cierra la hoja de arriba y consume su entrada de historial, para no dejarla
   * colgada y que el próximo atrás navegue de verdad.
   *
   * Se usa como handler de onclick, así que el primer argumento puede ser un
   * Event: de ahí `forzar` sale undefined y la hoja consulta su guarda, que es
   * lo correcto para Cancelar y para el toque en el fondo.
   */
  async function cerrarHoja({ forzar = false } = {}) {
    if (!pila.length) return;
    const { alIntentarCerrar } = pila[pila.length - 1];
    if (!forzar && alIntentarCerrar && !await alIntentarCerrar()) return;
    bajarHoja();
    atrasPropios += 1;
    history.back();
  }

  /** Cierra todas las hojas abiertas. Para los caminos que ya guardaron y
   *  vuelven a la pantalla de fondo, que se repinta sola. */
  async function cerrarTodas() {
    const n = pila.length;
    if (!n) return;
    pila = [];
    ocultarModal();
    // history.go(-n) es UNA navegación y dispara UN solo popstate, por muchas
    // entradas que retroceda. Sumar n acá dejaba el contador desfasado y los
    // siguientes "atrás" del auditor se perdían sin hacer nada.
    atrasPropios += 1;
    history.go(-n);
  }

  function confirmar(titulo, texto, textoOk = 'Confirmar', claseOk = 'btn-primario') {
    return new Promise((resolve) => {
      abrirHoja(`
        <h3>${esc(titulo)}</h3>
        <p class="sub">${esc(texto)}</p>
        <div class="acciones">
          <button class="btn" data-no>Cancelar</button>
          <button class="btn ${claseOk}" data-si>${esc(textoOk)}</button>
        </div>`, (hoja) => {
        hoja.querySelector('[data-no]').onclick = () => {
          cerrarHoja({ forzar: true }); resolve(false);
        };
        hoja.querySelector('[data-si]').onclick = () => {
          cerrarHoja({ forzar: true }); resolve(true);
        };
      });
    });
  }

  /* --------------------------------------------------------------- fotos -- */

  /**
   * Comprime la foto antes de guardarla: la cámara de la tablet produce
   * archivos de varios MB y la evidencia viaja en base64 dentro del JSON.
   */
  function comprimirImagen(archivo, maxLado = 1280, calidad = 0.72) {
    return new Promise((resolve, reject) => {
      const lector = new FileReader();
      lector.onerror = () => reject(new Error('No se pudo leer la imagen'));
      lector.onload = () => {
        const img = new Image();
        img.onerror = () => reject(new Error('Archivo de imagen inválido'));
        img.onload = () => {
          let { width: w, height: h } = img;
          if (w > maxLado || h > maxLado) {
            const escala = maxLado / Math.max(w, h);
            w = Math.round(w * escala);
            h = Math.round(h * escala);
          }
          const canvas = document.createElement('canvas');
          canvas.width = w; canvas.height = h;
          canvas.getContext('2d').drawImage(img, 0, 0, w, h);
          resolve(canvas.toDataURL('image/jpeg', calidad));
        };
        img.src = lector.result;
      };
      lector.readAsDataURL(archivo);
    });
  }

  /** Abre la cámara trasera de la tablet (o el selector de archivos). */
  function tomarFoto() {
    return new Promise((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.capture = 'environment';
      input.onchange = async () => {
        const archivo = input.files && input.files[0];
        if (!archivo) return resolve(null);
        try {
          resolve(await comprimirImagen(archivo));
        } catch (e) {
          toast(e.message, 'error');
          resolve(null);
        }
      };
      input.click();
    });
  }

  /* -------------------------------------------------------------- varios -- */

  function fecha(iso) {
    if (!iso) return '—';
    const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T'));
    if (isNaN(d)) return iso;
    return d.toLocaleString('es-AR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  /**
   * Fecha de HOY según el reloj de la tablet, en 'AAAA-MM-DD'.
   *
   * No se usa toISOString(): devuelve UTC, y en Argentina (UTC-3) a partir de
   * las 21:00 adelanta un día. El backend trabaja con la fecha local, así que
   * el frontend quedaba un día adelantado toda la franja de la tarde-noche —
   * justo cuando trabaja el turno tarde. En un cambio de mes, además, la app
   * mostraba un período y el servidor otro.
   */
  function hoyISO(d = new Date()) {
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  /** Marca de tiempo local 'AAAA-MM-DDTHH:MM', para inputs datetime-local. */
  function ahoraISO(d = new Date()) {
    const p = (n) => String(n).padStart(2, '0');
    return `${hoyISO(d)}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  const periodoActual = () => hoyISO().slice(0, 7);

  const DIAS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes',
                'sábado'];
  const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
                 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];

  /** 'lunes 21 de julio de 2026' — se arma a mano para no depender de la
   *  configuración regional de la tablet. */
  function fechaLarga(iso) {
    if (!iso) return '—';
    const [a, m, d] = iso.split('-').map(Number);
    const dow = new Date(Date.UTC(a, m - 1, d)).getUTCDay();
    return `${DIAS[dow]} ${d} de ${MESES[m - 1]} de ${a}`;
  }

  /** 'lun 21/07' — para listas densas. */
  function fechaCorta(iso) {
    if (!iso) return '—';
    const [a, m, d] = iso.split('-').map(Number);
    const dow = new Date(Date.UTC(a, m - 1, d)).getUTCDay();
    return `${DIAS[dow].slice(0, 3)} ${String(d).padStart(2, '0')}/`
         + `${String(m).padStart(2, '0')}`;
  }

  function hora(iso) {
    if (!iso) return '—';
    const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T'));
    return isNaN(d) ? '—'
      : d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
  }

  /* ---------------------------------------------------------- calendario -- */

  /**
   * Dibuja el mes como una corrida de días. Presentación pura.
   *
   * NO conoce el dominio: no sabe qué es un turno, un sector ni un ítem de LoS.
   * Toda la semántica la aporta quien la llama. Está prohibido que esta función
   * lea estado de App, LoS o Store: los calendarios de Limpieza y de Niveles de
   * Servicio son independientes entre sí y sólo comparten cómo se dibujan.
   *
   * Los días los da `Calc.diasDelMes()`. Que sea la única excepción a lo de
   * arriba no la ensucia: es una función pura y sin estado, igual que `esc()`,
   * y lo que la independencia entre módulos prohíbe es leer estado ajeno, no
   * reusar un helper. Tener acá una segunda implementación del mismo cálculo
   * era peor: dejaba dos lugares donde arreglar el mismo error, y una de las
   * dos copias armaba la fecha en UTC y la otra en hora local.
   *
   * @param {string} periodo  'AAAA-MM'
   * @param {Object} opciones
   *   estado(fecha)  -> 'ok' | 'parcial' | 'abierto' | 'falta' | 'futuro'
   *   marca(fecha)   -> bool. Señal secundaria, independiente del estado.
   *   titulo(fecha)  -> texto accesible de la celda
   *   accion         -> nombre del data-attribute de la celda. Cada módulo usa
   *                     el suyo para que los handlers no se pisen.
   *   leyenda        -> [{ clase, texto }]
   *   compacto       -> bool. Densidad, no tamaño de celda.
   *   soloLectura    -> bool. Celdas como <span>, no como <button>.
   */
  function calendarioMes(periodo, opciones = {}) {
    const {
      estado = () => 'futuro',
      marca = null,
      titulo = null,
      accion = 'dia',
      leyenda = [],
      compacto = false,
      soloLectura = false,
    } = opciones;

    // El atributo termina en el HTML sin comillas de por medio: se limita al
    // vocabulario de un data-attribute válido.
    const attr = String(accion).replace(/[^a-z0-9-]/gi, '') || 'dia';

    const celdas = Calc.diasDelMes(periodo).map((fecha) => {
      const clase = estado(fecha) || 'futuro';
      const texto = titulo ? titulo(fecha) : '';
      const etiqueta = esc(fecha + (texto ? ' — ' + texto : ''));
      const punto = marca && marca(fecha)
        ? '<span class="dia-punto" aria-hidden="true"></span>' : '';
      const cuerpo = `<span class="dia-num">${parseInt(fecha.slice(-2), 10)}</span>${punto}`;

      return soloLectura
        ? `<span class="dia dia-${esc(clase)}" title="${etiqueta}"
                 aria-label="${etiqueta}">${cuerpo}</span>`
        : `<button class="dia dia-${esc(clase)}" data-${attr}="${fecha}"
                   title="${etiqueta}" aria-label="${etiqueta}">${cuerpo}</button>`;
    }).join('');

    const pie = leyenda.length
      ? `<div class="leyenda">${leyenda.map((l) =>
          `<span><i class="${esc(l.clase)}"></i> ${esc(l.texto)}</span>`).join('')}</div>`
      : '';

    return `<div class="calendario${compacto ? ' compacto' : ''}">
              ${celdas}
            </div>${pie}`;
  }

  function nombrePeriodo(periodo) {
    const meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
                   'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
    const [anio, mes] = periodo.split('-');
    return `${meses[parseInt(mes, 10) - 1]} ${anio}`;
  }

  return { esc, toast, toastDeshacer, abrirHoja, cerrarHoja, cerrarTodas,
           confirmar, tomarFoto, calendarioMes,
           comprimirImagen, fecha, fechaLarga, fechaCorta, hora,
           hoyISO, ahoraISO, periodoActual, nombrePeriodo };
})();
