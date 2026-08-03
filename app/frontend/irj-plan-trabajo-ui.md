# Controles Operativos IRJ — plan de trabajo UI

Documento único de trabajo. Reemplaza a `auditoria-ui-controles-irj.md` y a
`auditoria-ui-irj-addendum-vertical.md`: las correcciones del addendum ya están
aplicadas y los hallazgos descartados fueron eliminados. **No usar los otros dos
como referencia** — contienen recomendaciones que este documento anula.

---

## Contexto del proyecto

Aplicación PWA de auditoría operativa del Aeropuerto Cap. V. A. Almonacid (IRJ).
Un auditor recorre el aeropuerto y releva dos módulos: Limpieza (check-list por
sector) y Niveles de Servicio.

**Dispositivo:** Samsung Galaxy Tab 10", Android, **bloqueada en orientación
vertical**. Viewport CSS ~800 × 1280 px. Se usa sostenida con una mano o apoyada
en el antebrazo, muchas veces sin conexión y a plena luz en plataforma.

**Arquitectura:** vanilla JS sin framework ni build. Módulos IIFE cargados por
`<script>` en orden desde `index.html`. Estado offline en IndexedDB (`store.js`),
cola de mutaciones sincronizada por lote (`sync.js`), service worker network-first
(`sw.js`).

**Archivos:** `index.html`, `css/app.css`, `js/{store,api,calc,ui,sync,config,los,informes,app}.js`

---

## Restricciones — leer antes de tocar nada

Estas decisiones están tomadas y documentadas en los comentarios del código. **No
revertirlas** salvo que una tarea lo pida explícitamente:

1. **No introducir frameworks, bundlers ni dependencias.** Vanilla JS, un archivo
   por módulo, patrón IIFE. Se mantiene.
2. **No usar `toISOString()` para fechas locales.** Existe `UI.hoyISO()` /
   `UI.ahoraISO()` justamente porque UTC adelantaba un día después de las 21:00 en
   UTC-3. Ver comentario en `ui.js`.
3. **No cachear respuestas de `/api/` en el service worker.** La distinción entre
   "guardado local" y "confirmado por el servidor" la maneja IndexedDB.
4. **`calc.js` no es fuente de verdad.** Es un espejo de `app/backend/calc.py` para
   mostrar preview offline. Si se toca, mantener sincronizado y seguir rotulando
   los valores como "preview".
5. **Escapar siempre con `UI.esc()`** todo lo que venga del usuario o del servidor
   antes de inyectarlo en HTML.
6. **Objetivos táctiles mínimos de 48px** (`var(--tap)`). No bajar de ahí.
7. **El estado nunca se comunica solo por color.** Cada estado lleva además un
   símbolo o texto. Es requisito de uso a plena luz.
8. **Contraste WCAG AA verificado** en la paleta actual. Si se agregan colores,
   verificar 4.5:1 para texto normal.
9. **Toda escritura pasa por `API.mutar()`**, que encola si no hay red. No usar
   `API.post/put/del` directo para mutaciones que el auditor haga en recorrida.
10. **Lógica por excepción:** los sectores arrancan sin verificar; un sector sin
    confirmar es "sin datos", nunca 100%.

---

## Tarea 1 — Repintado quirúrgico de la tarjeta de sector

**Prioridad: máxima.** Es el cambio con más impacto de toda la lista.

**Problema.** `confirmarDesdeGrilla()` (`app.js`, ~línea 1665) llama a
`pintarControl()`, que hace `app().innerHTML = ...` vía `layout()`. Eso reconstruye
el header, el `<main>` y la barra inferior, y vuelve a disparar
`pintarEquipamiento()`, `pintarArtefactos()`, `pintarPendientesAnteriores()` y
`pintarNovedades()`.

Por recorrida de 9 sectores: 9 reconstrucciones completas del DOM, ~36 peticiones
innecesarias y **9 saltos al tope de la página**. El auditor confirma un sector de
la fila de abajo y la pantalla lo devuelve arriba.

**Qué hacer.**

1. Extraer el cuerpo del `.map()` de `pintarControl()` (~línea 910) a una función
   suelta `tarjetaSector(sector, estado)` que devuelva el HTML de una tarjeta.
   `pintarControl()` pasa a usarla; el HTML resultante debe ser idéntico al actual.
2. Extraer el `onclick` de las tarjetas a `enlazarSector(nodo, sectorId)`.
3. Extraer el contenido de la barra inferior (resumen de % preview + botón "Cerrar
   control") a `repintarBarraInferior()`, que actualice solo ese nodo.
4. Agregar `repintarSector(sectorId)`, que reemplace únicamente el nodo `.sector`
   correspondiente y vuelva a enlazarlo.
5. `confirmarDesdeGrilla()` y `deshacerConfirmacion()` pasan a llamar a
   `repintarSector()` + `repintarBarraInferior()` en lugar de `pintarControl()`.

**Criterio de aceptación.** Confirmar el último sector de la grilla no mueve el
scroll ni un píxel, y no dispara ninguna petición fuera de la del propio
`/confirmar`.

---

## Tarea 2 — Hojas modales ancladas abajo, no centradas

**Problema.** `app.css` ~línea 270:

```css
@media (min-width: 720px) {
  .modal { align-items: center; }
  .hoja { border-radius: 18px; }
}
```

La tablet vertical mide 800px de ancho: **entra en ese media query**. Las hojas se
están centrando en un viewport de 1280px de alto, con los botones de acción
flotando a media pantalla, en la zona menos alcanzable con el pulgar. El bottom
sheet (comportamiento por defecto que este query anula) es lo correcto en vertical.

**Qué hacer.**

```css
/* Solo se centra si la pantalla es más ancha que alta (escritorio, apaisado).
   En la tablet vertical la hoja se ancla abajo: los botones quedan al alcance. */
@media (min-aspect-ratio: 1/1) {
  .modal { align-items: center; }
  .hoja { border-radius: 18px; }
}
```

Además, subir `.hoja { max-width: 640px }` a `max-width: min(720px, 100%)`.

---

## Tarea 3 — "Volver y confirmar sector" tiene que confirmar

**Problema.** En `vistaSector()` (`app.js` ~1845) el botón inferior dice
`← Volver y confirmar sector` y su handler solo navega:

```js
if (btnVolver) btnVolver.onclick = () => ir(`/control/${control.id}`);
```

El auditor cree que confirmó, vuelve a la grilla y el sector sigue gris. Además
obliga al recorrido más caro de la app: abrir sector → cargar desvío → volver →
buscar la tarjeta → tocar Confirmar.

**Qué hacer.** Que confirme y vuelva, reusando la lógica de
`confirmarDesdeGrilla()`. Etiqueta según estado:

- Sin desvíos: `Confirmar sin novedades y volver`
- Con desvíos: `Confirmar con N desvío(s) y volver`
- Sector ya confirmado: `Volver a la grilla` (sin reconfirmar)

Mostrar el mismo `UI.toastDeshacer()` que usa la grilla.

---

## Tarea 4 — El botón atrás de Android debe cerrar la hoja

**Problema.** `UI.abrirHoja()` (`ui.js` ~60) cierra con Escape —irrelevante, no hay
teclado físico— o tocando el fondo. El botón atrás del sistema navega en el
historial de hash y, si la hoja se abrió sin cambiar de ruta, **sale de la
aplicación**. En `hojaDesvio()` eso significa perder observación y fotos ya
cargadas.

**Qué hacer.**

1. `abrirHoja()` hace `history.pushState({ hoja: true }, '')` al abrir.
2. Escucha `popstate` para cerrar la hoja.
3. El cierre normal (botón, fondo, Escape) hace `history.back()` para no dejar la
   entrada colgada. Cuidado con el bucle: la función de cierre necesita saber si
   viene del historial o no.
4. En `hojaDesvio()`, si hay texto en la observación o fotos cargadas, pedir
   confirmación antes de descartar.

**Criterio de aceptación.** Con una hoja abierta, el gesto/botón atrás cierra la
hoja. Con dos hojas encadenadas (ej.: `hojaClausuras` → `hojaReponerArtefacto`),
vuelve a la anterior. Sin hojas abiertas, navega normalmente.

---

## Tarea 5 — Botón de volver en la barra inferior

**Problema.** En vertical, la barra superior está a ~1.200px del pulgar. Ahí vive el
único botón de volver. Con la tablet en una mano, alcanzarlo exige recolocarla o
usar la segunda mano.

**Qué hacer.** En `layout()`, cuando se pasa `volver`, agregar también un botón en
la barra inferior, a la izquierda del contenido de `inferior`. La flecha superior
se mantiene (es la convención y no molesta).

```css
.barra-inferior .btn-volver-inf {
  min-height: var(--tap); min-width: var(--tap);
  flex: none; border-radius: 10px;
  border: 1px solid var(--borde); background: var(--blanco);
  font-size: 20px;
}
```

Nota: hoy la barra inferior solo se renderiza si se pasa `inferior`. Con este
cambio debe renderizarse también cuando hay `volver` aunque no haya acción
principal. Verificar que `.contenido { padding-bottom: 96px }` siga alcanzando.

---

## Tarea 6 — Deep-link de sector

**Problema.** `vistaSector()` (`app.js` ~1785):

```js
if (!sector || !control) return ir('/limpieza');
```

`control` es una variable de módulo en memoria y la ruta `#/sector/banos` no dice a
qué control pertenece. Cualquier reinicio del JS —Android matando la pestaña en
segundo plano, el auditor cambiando de app unos minutos, un `reload`, el service
worker activando una versión nueva— devuelve al auditor a `/limpieza` sin
explicación. En una tablet compartida pasa varias veces por turno.

**Qué hacer.** Ruta nueva: `#/control/:id/sector/:clave`.

```js
if (seccion === 'control' && arg) {
  const [, , id, sub, clave] = ruta.split('/');
  if (sub === 'sector' && clave) return vistaSector(parseInt(id, 10), clave);
  return vistaControl(parseInt(id, 10));
}
```

`vistaSector(controlId, clave)` recarga el control si `control` está vacío, usando
la misma lógica de caché de `vistaControl()`. Actualizar los `ir()` que apuntan a
`/sector/...` y el `volver` de la vista.

---

## Tarea 7 — "Confirmar y seguir"

**Problema.** Una recorrida física es secuencial: hall → check-in → baños → sala de
embarque. La app obliga a volver a la grilla y elegir de nuevo cada vez, aunque el
orden sea siempre el mismo.

**Qué hacer.** En la barra inferior de `vistaSector()`, dos acciones:

```
[ ← Volver ]   [ Confirmar y seguir → ]
```

"Confirmar y seguir" confirma el sector actual y navega al siguiente **sin
confirmar** del catálogo. Si no queda ninguno, vuelve a la grilla con un toast del
tipo "Último sector — falta cerrar el control".

Usar el orden del catálogo de sectores. Con esto, una recorrida sin desvíos son 9
toques del mismo botón en el mismo píxel.

Depende de las tareas 1 y 3.

---

## Tarea 8 — Hoja de desvío: orden y acciones fijas

**Problema A — orden.** La hoja pide **severidad → observación → foto**. El auditor
está parado frente al desvío: lo natural es fotografiarlo mientras lo tiene
delante y describirlo después.

**Problema B — teclado.** En vertical el teclado se lleva ~45% de la pantalla
(quedan ~700px). Al enfocar el `<textarea>`, el navegador scrollea el campo a la
vista y los botones de acción quedan fuera del área visible. Con el orden actual,
entre el textarea y Guardar está todo el bloque de fotos.

**Qué hacer.**

1. Reordenar a **severidad → foto → observación**. El teclado aparece al final,
   cuando no queda nada debajo salvo los botones.
2. Acciones fijas al fondo del sheet:

```css
.hoja .acciones {
  position: sticky; bottom: 0;
  background: var(--blanco);
  padding-top: 12px;
  margin-bottom: -4px;
  box-shadow: 0 -8px 12px -8px rgba(16,24,40,.12);
}
```

3. La observación es obligatoria pero solo se valida al tocar Guardar (toast de
   error). Sumarla a `actualizarGuardar()`, igual que ya se hizo con la severidad:

```js
const obs = hoja.querySelector('#obs');
const actualizarGuardar = () => { btnGuardar.disabled = !estado || !obs.value.trim(); };
obs.addEventListener('input', actualizarGuardar);
```

Probar con el teclado de Samsung, que es más alto que el de Google.

---

## Tarea 9 — Deshacer sin conexión

**Problema.** `confirmarDesdeGrilla()` muestra el toast con Deshacer siempre,
incluso cuando la mutación quedó encolada. Pero `deshacerConfirmacion()` arranca
rechazando si no hay red. Toda la app está construida sobre la premisa de que el
auditor no nota la diferencia entre online y offline; este es el único lugar donde
se le ofrece una salida y después se le niega — justo en la protección contra el
toque accidental, que es más probable caminando por plataforma sin señal.

**Qué hacer.** Si la confirmación todavía está en la cola, deshacer es puramente
local: sacarla de la cola y limpiar el estado. No hace falta red porque la
operación nunca salió.

```js
const enCola = (await Store.pendientes()).find(
  (op) => op.ruta.endsWith(`/sectores/${sectorId}/confirmar`));

if (enCola) {
  await Store.quitarDeCola(enCola.uuid);
} else if (!navigator.onLine) {
  return UI.toast('Deshacer requiere conexión.', 'error');
} else {
  await API.del(`/api/controles/${control.id}/sectores/${sectorId}/confirmar`);
}
```

---

## Tarea 10 — Calendario en grilla semanal

**Problema.** `app.css` ~335: `grid-template-columns: repeat(auto-fill, minmax(44px, 1fr))`.
A 768px salen ~15 columnas: los 31 días quedan en dos filas corridas sin alineación
por día de la semana. El comentario del código dice que el objetivo es "un mes
entero de un vistazo", pero así no se puede ver el patrón que importa: si los
huecos de auditoría caen sistemáticamente en fin de semana.

**Qué hacer.** Grilla de 7 columnas con encabezado L-M-M-J-V-S-D y offset del día 1
(semana que empieza en lunes). A 768px las celdas pasan a ~103px, con lugar de
sobra para el número y el estado sin depender del pseudo-elemento de 9px.

Mantener los cinco estados actuales (`dia-ok`, `dia-parcial`, `dia-abierto`,
`dia-falta`, `dia-futuro`) y su leyenda. El `title=""` de cada celda no llega nunca
en táctil —no hay hover—, así que la información debe estar en la celda o en la
leyenda.

---

## Tarea 11 — Consistencia de "en curso"

**Problema.** `app.css` ~195 documenta la decisión correcta: un control en curso no
es un hallazgo, es trabajo empezado, y va en azul (`.sector.en-curso`). Pero:

- `abrirDia()` (~línea 436) pinta el control en curso con `con-desvios` → ámbar.
- `vistaLimpieza()` (~línea 698) pinta la fila en curso con `parcial` → ámbar.

La misma cosa es azul en una pantalla y ámbar en otras dos.

**Qué hacer.** Unificar en azul. En `vistaLimpieza()` la fila usa clases de `.item`,
así que puede hacer falta una clase `.item.en-curso` equivalente.

---

## Tarea 12 — Marca de la tarjeta: símbolo primero

**Problema.** `app.js` ~912:

```js
const marca = !confirmado ? (desvios || '·') : desvios ? desvios : '✓';
```

Un `3` en círculo gris significa "3 desvíos, sin confirmar". Un `3` en círculo ámbar
significa "3 desvíos, confirmado". Lo único que los separa es el tono — el fallo que
el resto del CSS se esfuerza en evitar, y en el peor escenario posible: tablet a
plena luz.

**Qué hacer.**

| Estado | Marca | Color |
|---|---|---|
| Sin verificar | `·` | gris |
| Con desvíos, sin confirmar | `…3` | gris |
| Confirmado sin novedades | `✓` | verde |
| Confirmado con desvíos | `✓3` | ámbar |

El `✓` presente o ausente pasa a ser la señal primaria de "confirmado", legible sin
distinguir tonos. Puede requerir bajar el `font-size` de `.sector .marca` o
ensanchar el círculo para que entren dos caracteres.

---

## Tarea 13 — Iniciar el turno de hoy sin modal

**Problema.** `crearControl()` (~línea 740) pasa por `UI.confirmar()` siempre. Un
control recién creado, con todos los sectores pendientes, no destruye nada: el
modal no protege de ningún error real y está en la acción más frecuente de la app.

**Qué hacer.** Si `fecha === hoy` y el turno coincide con el sugerido por horario,
crear directo. Mantener la confirmación —con su advertencia "estás cargando un día
anterior a hoy"— para fechas atrasadas, que es donde el error sí importa.

---

## Tarea 14 — Orientación bloqueada

En `manifest.webmanifest`:

```json
"orientation": "portrait"
```

Una rotación accidental en plataforma reflowea todo y desorienta a mitad de
recorrida. Con esto se puede además simplificar el CSS que existe solo para el caso
apaisado.

---

## Tarea 15 — Holgura vertical en la grilla

En vertical la grilla queda en 3×3 y el presupuesto de pantalla es:

```
barra superior            ~64 px
tarjeta "Avance"         ~110 px
grilla 3×3               ~380 px
                        ───────
                         ~554 px   de 1280 disponibles
```

**Los 9 sectores entran completos arriba del pliegue.** Es la mejor propiedad del
layout vertical y hay que protegerla: nada nuevo se monta *arriba* de la grilla.

Con ese margen sobra lugar para dar holgura:

- `.sector { min-height: 132px }` (hoy 108px)
- `.btn-sector-ok { margin-top: 16px }` (hoy 10px) — separa mejor el área de
  apertura del atajo de confirmación, que hoy están a 10px y se tocan por error.

---

## Tarea 16 — Auditor visible y cambio de turno

**Problema.** La tablet es compartida entre turnos y el auditor queda registrado en
cada confirmación. Cerrar sesión hoy exige tocar el chip que dice "Sincronizado" y
bajar hasta el fondo de esa hoja. Nadie lo encuentra sin que se lo enseñen, y el
costo del error es trazabilidad incorrecta en un documento que se firma.

**Qué hacer.** Nombre del auditor visible en el header de todas las pantallas —hoy
solo aparece como subtítulo en inicio—, tocable, abriendo una hoja con "Cambiar de
auditor" y "Cerrar sesión". Mantener la advertencia actual sobre operaciones sin
sincronizar antes de cerrar sesión.

---

## Orden de ejecución

Las tareas 1-8 son el núcleo y conviene hacerlas en ese orden: la 7 depende de la
1 y la 3, y la 8 se aprovecha mejor con la 2 ya aplicada.

| # | Tarea | Archivos | Esfuerzo |
|---|---|---|---|
| 1 | Repintado quirúrgico | `app.js` | 2-3 h |
| 2 | Hoja anclada abajo | `app.css` | 10 min |
| 3 | "Volver y confirmar" confirma | `app.js` | 20 min |
| 4 | Atrás cierra la hoja | `ui.js` | 40 min |
| 5 | Volver en barra inferior | `app.js` + `app.css` | 40 min |
| 6 | Deep-link de sector | `app.js` | 1-2 h |
| 7 | "Confirmar y seguir" | `app.js` | 1 h |
| 8 | Hoja de desvío: orden y acciones fijas | `app.js` + `app.css` | 40 min |
| 9 | Deshacer sin conexión | `app.js` | 40 min |
| 10 | Calendario semanal | `app.js` + `app.css` | 2 h |
| 11 | Consistencia "en curso" | `app.js` | 15 min |
| 12 | Marca símbolo + conteo | `app.js` + `app.css` | 20 min |
| 13 | Iniciar turno sin modal | `app.js` | 20 min |
| 14 | Orientación bloqueada | `manifest.webmanifest` | 2 min |
| 15 | Holgura vertical | `app.css` | 10 min |
| 16 | Auditor visible | `app.js` | 1 h |

Sugerencia de corte: **tareas 1 a 8 en una tanda**, probar en la tablet real, y
recién después seguir con el resto.

---

## Verificación en el dispositivo

Con la tablet **bloqueada en vertical** y sostenida con una sola mano, sin apoyarla:

1. Recorrida completa de 9 sectores sin desvíos. Contar toques, anotar cada salto
   al tope de página y cada vez que hace falta la segunda mano.
2. Llegar al botón de volver desde el fondo de la pantalla del control sin
   recolocar la mano.
3. Abrir la hoja de desvío y verificar que nace del borde inferior, no centrada.
4. Enfocar el textarea de observación con el teclado de Samsung: Guardar debe
   seguir visible o a un scroll corto.
5. Cargar un desvío con dos fotos y tocar atrás. Debe cerrarse la hoja, no la app,
   y pedir confirmación antes de descartar.
6. Al abrir un control, los 9 sectores deben entrar en pantalla sin scroll.
7. Modo avión durante una recorrida: confirmar 3 sectores, tocar Deshacer en el
   último, reconectar. Verificar estado final y cola vacía.
8. Matar la pestaña desde el multitarea con un sector abierto y reabrir. El auditor
   debe quedar donde estaba.
9. A plena luz en plataforma: distinguir "confirmado con desvíos" de "sin confirmar
   con desvíos" sin acercar la vista.
