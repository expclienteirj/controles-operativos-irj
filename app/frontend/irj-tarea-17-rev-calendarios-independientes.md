# Tarea 17 (revisión) — Calendarios independientes

Corrige `irj-tarea-17-calendario-los.md`. **Usar este documento; el anterior queda
anulado en todo lo que se contradiga con éste.**

Requisito nuevo: el calendario de Niveles de Servicio y el calendario del
check-list de Limpieza son **independientes**. Cada uno pertenece a su propio
módulo, mide su propio trabajo y no se entera de lo que pasa en el otro.

---

## Qué significa "independientes"

Conviene separarlo por capas, porque la respuesta es distinta en cada una:

| Capa | ¿Compartida? | Motivo |
|---|---|---|
| **Datos** | **No** | Cada calendario se alimenta solo de su módulo. Ver el riesgo de la sección 2. |
| **Estado** | **No** | Cada módulo mantiene su propio `periodo` y su propio caché. |
| **Navegación** | **No** | Tocar un día en LoS no lleva nunca a un control de limpieza. |
| **Caché offline** | **No** | Claves separadas, sin invalidación cruzada. |
| **Función de dibujo** | **Sí, opcional** | Es presentación pura, sin estado. Ver sección 4. |

La independencia que importa es la de las tres primeras filas. Compartir la
función que dibuja una grilla de días no crea acoplamiento, del mismo modo que
`UI.fechaLarga()` la usan los dos módulos sin que eso los ate.

---

## 1. Regla general

> Cada calendario mide **únicamente el trabajo de su propio check-list**. Ningún
> día puede cambiar de color por algo cargado en el otro módulo.

De esa regla salen todos los criterios de aceptación del final.

---

## 2. El riesgo real: los ítems DERIVADO

Este es el único lugar donde el acoplamiento puede colarse sin que se vea, así que
va primero.

LoS clasifica sus ítems por periodicidad: `DIARIO`, `MENSUAL`, `POR_EVENTO` y
`DERIVADO`. Los `DERIVADO` **no se cargan en LoS**: se calculan con lo que el
auditor ya cargó en el check-list de limpieza. `seccionDerivada()` lo dice al
usuario con todas las letras:

> *Se calculan con lo que ya cargaste en el check-list diario de limpieza.*

Si el calendario de LoS agregara los ítems derivados, pasaría esto: el auditor hace
la recorrida de limpieza y **el calendario de LoS se pone verde solo**, sin que
nadie haya relevado nada de LoS. El indicador de progreso de un módulo mostraría el
avance del otro. Es exactamente lo que hay que evitar.

**Regla de implementación:**

```js
// SOLO periodicidad DIARIO. Los DERIVADO se calculan desde el check-list de
// limpieza y no representan trabajo de relevamiento de LoS: incluirlos haría
// que este calendario avanzara solo por hacer la recorrida del otro módulo.
const diarios = dash.items.filter((i) => i.periodicidad === 'DIARIO' && i.aplica);
```

Nunca `!== 'MENSUAL'`, nunca `i.diario != null` como criterio, nunca un filtro por
exclusión. **Lista blanca explícita de `DIARIO`.** Un filtro por exclusión deja
entrar cualquier periodicidad nueva que se agregue después.

Mismo criterio para el denominador: si LoS tiene 4 ítems diarios y 2 derivados, el
día completo son **4 de 4**, no 6.

---

## 3. Fuentes de datos separadas

Cada calendario deriva su estado de un único endpoint, del suyo:

| Calendario | Fuente | Campos |
|---|---|---|
| Limpieza | `/api/periodos/:periodo/completitud` (ya en uso) | `turnos_cerrados_por_dia`, `dias_abiertos`, `dias_vencidos_sin_control` |
| LoS | `/api/los/dashboard?periodo=…` (ya en uso) | por ítem `DIARIO`: `diario.dias_relevados`, `diario.dias_incumplen` |

**Ninguno de los dos llama al endpoint del otro.** Si el calendario de LoS
necesitara alguna vez un dato de completitud de limpieza, es señal de que el diseño
se torció.

Ninguno de los dos requiere cambios de backend: los campos ya vienen en los
payloads que ambos módulos piden hoy.

---

## 4. Sobre la función de dibujo compartida

La revisión anterior proponía extraer `UI.calendarioMes()` para que la usen los
dos. Eso **sigue siendo la recomendación**, con una condición que la vuelve
compatible con el requisito de independencia:

> `UI.calendarioMes()` debe ser una **función pura**: recibe el período y unos
> callbacks, devuelve un string de HTML. No lee `App.control`, no lee el `periodo`
> de LoS, no toca `Store`, no hace fetch, no guarda nada.

Toda la semántica —qué es verde, qué es rojo, qué leyenda va— la aporta el módulo
que llama. Dos módulos usando la misma función de dibujo con reglas distintas están
tan desacoplados como dos módulos usando `UI.fechaCorta()`.

**Firma con las restricciones explícitas:**

```js
/**
 * Dibuja un mes en grilla semanal (lunes a domingo). Presentación pura.
 *
 * NO conoce el dominio: no sabe qué es un turno, un sector ni un ítem de LoS.
 * Toda la semántica la aporta quien la llama. Está prohibido que esta función
 * lea estado de App, LoS o Store: los calendarios de Limpieza y de Niveles de
 * Servicio son independientes entre sí y sólo comparten cómo se dibujan.
 *
 * @param {string} periodo  'AAAA-MM'
 * @param {Object} opciones
 *   estado(fecha)  -> 'ok' | 'parcial' | 'abierto' | 'falta' | 'futuro'
 *   marca(fecha)   -> bool. Señal secundaria, independiente del estado.
 *   titulo(fecha)  -> texto accesible de la celda
 *   accion         -> nombre del data-attribute de la celda. Cada módulo usa el
 *                     suyo para que los handlers no se pisen.
 *   leyenda        -> [{ clase, texto }]
 *   compacto       -> bool
 *   soloLectura    -> bool. Celdas como <span>, no como <button>.
 */
```

El cuerpo es el de la revisión anterior, con dos cambios (ver 4.1 y 4.2).

### 4.1. Atributo de celda parametrizable

En la versión anterior el componente emitía siempre `data-dia`. Eso es un
acoplamiento accidental: si alguna vez conviven dos calendarios en una misma
pantalla, o un calendario y una lista que ya usa `data-dia`, un
`document.querySelectorAll('[data-dia]')` engancha los dos y los handlers se pisan.

```js
const attr = opciones.accion || 'dia';
// ...
`<button class="dia dia-${cl}" data-${attr}="${fecha}" ...>`
```

- Limpieza: `accion: 'dia'` (mantiene el handler actual de `vistaInicio()`, sin cambios).
- LoS dashboard: `accion: 'dia-los'`.
- LoS por ítem: `accion: 'dia-item-los'`.

Y cada módulo consulta **dentro de su propio contenedor**, nunca sobre
`document`:

```js
// LoS
caja.querySelectorAll('[data-dia-los]').forEach(...)
```

### 4.2. Alternativa: duplicar en vez de compartir

Si preferís cero código compartido entre los módulos, es una opción defendible:
son ~40 líneas y evita que un cambio pedido por un módulo rompa al otro.

**Costo de duplicar:** el bug de los glifos duplicados de la leyenda (17-A del
documento anterior) es exactamente lo que pasa cuando reglas de calendario andan
sueltas. Con dos copias, cada arreglo hay que hacerlo dos veces y con el tiempo
divergen.

**Recomendación:** compartir la función de dibujo con las restricciones de arriba,
y no compartir nada más. Pero si elegís duplicar, es una decisión razonable — en
ese caso poné el calendario de LoS en `los.js` y dejá el de Limpieza donde está.

---

## 5. Estado y caché separados

**Período.** Cada módulo ya mantiene su propia variable `periodo`: `los.js` tiene
`let periodo` a nivel de módulo, `informes.js` tiene la suya, y Limpieza lo deriva
de `UI.periodoActual()` en cada vista. **No unificarlas.** Que el auditor esté
mirando julio en Informes no debe mover el mes de LoS.

**Claves de caché.** Separadas y con prefijo por módulo. Las de limpieza ya lo
están (`cache:completitud:${periodo}`, `cache:controles:${periodo}`). Para LoS, si
se agrega caché del dashboard, usar `cache:los:dashboard:${periodo}`.

**Repintado.** Cuando se releva un ítem de LoS, se repinta el calendario de LoS y
nada más. `App.pintarControl()` no se llama nunca desde `los.js`, y `LoS.vista()`
no se llama nunca desde `app.js`. Hoy es así; conviene que siga siéndolo después
del refactor de la Tarea 1.

---

## 6. Navegación separada

Tocar un día en el calendario de LoS **no puede** llevar a `#/control/:id`. Los dos
calendarios muestran los mismos 31 números y el auditor no debería descubrir que
tocó el equivocado apareciendo en el otro módulo.

- Limpieza, día tocado → `abrirDia()` → hoja con los dos turnos → `#/control/:id`
- LoS, día tocado → hoja con los ítems diarios de esa fecha → formulario de LoS

Si en la primera versión el calendario de LoS va como solo lectura
(`soloLectura: true`), las celdas deben ser `<span>`, no `<button>` deshabilitados:
un botón que no hace nada invita al toque y no responde.

---

## 7. Que se distingan a simple vista

Consecuencia de la independencia: si son dos objetos distintos que miden cosas
distintas, no deberían verse idénticos. Hoy compartirían grilla, colores y forma.

Diferenciadores, sin inventar una paleta nueva:

1. **Leyendas distintas**, que ya lo son y es el diferenciador más fuerte:
   Limpieza dice `2 turnos / 1 turno / Sin cerrar / Sin auditar / En plazo`; LoS
   dice `4 ítems / Incompleto / Sin relevar / En plazo`.
2. **Títulos de tarjeta distintos:** `Mes de agosto 2026` en Limpieza (ya está) y
   `Avance del mes — recorrida diaria` en LoS.
3. **LoS en modo compacto** (celdas de 52px contra 64px), lo cual además ayuda con
   el presupuesto vertical.
4. **LoS no tiene el estado azul** `abierto`: el relevamiento es mensual, no hay
   "día sin cerrar". Cuatro estados contra cinco.

Con eso alcanza. No hace falta cambiar colores: el vocabulario cromático
compartido —verde completo, ámbar parcial, rojo falta, gris en plazo— es una
ventaja, siempre que signifique lo mismo en los dos lados (que es lo que garantiza
la regla de la sección 8).

---

## 8. Se mantiene: el color codifica cobertura

Sigue vigente la decisión de la revisión anterior, y la independencia la refuerza.

> En los dos calendarios, el **color de fondo indica cuánto se relevó**. El
> incumplimiento, cuando aplica, va como señal secundaria: un punto rojo en la
> esquina de la celda.

En LoS un día con los 4 ítems relevados y uno que no cumple se ve **verde con
punto**, no rojo. Si se pintara rojo, el rojo significaría "no se auditó" en un
calendario y "se auditó y el contratista falló" en el otro — dos cosas opuestas en
objetos que se ven parecidos. Eso sí sería una dependencia mental entre los dos
módulos, aunque el código estuviera separado.

Estados de cada uno:

| | Limpieza | LoS (dashboard) | LoS (por ítem) |
|---|---|---|---|
| Verde `✓` | 2 turnos cerrados | los 4 ítems diarios relevados | ítem relevado |
| Ámbar `½` | 1 de 2 turnos | 1 a 3 ítems relevados | — |
| Azul `…` | iniciado sin cerrar | — | — |
| Rojo `!` | día vencido sin control | día pasado sin relevar nada | día pasado sin relevar |
| Gris | aún en plazo | hoy o futuro | hoy o futuro |
| Punto rojo | — | algún ítem no cumple ese día | el ítem no cumple ese día |

---

## Criterios de aceptación

1. Cargar una recorrida completa de limpieza **no cambia ninguna celda** del
   calendario de LoS.
2. Relevar los 4 ítems diarios de LoS **no cambia ninguna celda** del calendario de
   Limpieza.
3. El cálculo del calendario de LoS filtra por `periodicidad === 'DIARIO'` con
   lista blanca explícita. Los ítems `DERIVADO`, `MENSUAL` y `POR_EVENTO` no entran
   ni en el numerador ni en el denominador.
4. `los.js` no llama a ningún endpoint de `/api/controles` ni de
   `/api/periodos/:p/completitud`.
5. `app.js` no llama a ningún endpoint de `/api/los/`.
6. Tocar un día en el calendario de LoS nunca navega a `#/control/…`.
7. Los handlers de un calendario no enganchan celdas del otro: cada módulo consulta
   su propio atributo, dentro de su propio contenedor.
8. Cambiar el período en un módulo no cambia el del otro.
9. Si se comparte `UI.calendarioMes()`: la función no referencia `App`, `LoS`,
   `Store` ni `API`, y no tiene estado propio entre llamadas.
10. Un día completo con un ítem que no cumple se ve verde con punto, no rojo.
11. El día de hoy nunca aparece en rojo en ninguno de los dos.
12. Ninguna celda tocable mide menos de 48px de alto.

---

## Prueba manual

En la tablet, en un mismo día:

1. Abrir Limpieza y cerrar los dos turnos del día. Anotar el estado de esa celda.
2. Ir a LoS **sin relevar nada**. La celda de ese día en el calendario de LoS debe
   estar en gris (en plazo) o rojo (si es un día pasado), **nunca verde**. Si está
   verde, están entrando los ítems derivados: revisar el filtro.
3. Relevar los 4 ítems diarios de LoS con uno que no cumpla. La celda de LoS pasa a
   verde con punto rojo.
4. Volver a Limpieza. Su calendario debe estar exactamente como en el paso 1.
5. Cambiar el período en Informes a un mes anterior, volver a LoS y a Limpieza:
   ambos siguen en el mes corriente.
