"""
Motor de cálculo — Controles Operativos IRJ.

Funciones puras (sin acceso a base de datos) que implementan las reglas
cuantitativas de los módulos LIMPIEZA y LoS. Es la única fuente de verdad
de los porcentajes: la API y los informes consumen estas funciones.

Convenciones transversales (lógica por excepción, sección 0 del pliego funcional):
  - Todo arranca en CUMPLE (1.0). Solo los desvíos registrados descuentan.
  - Un sector/ítem no confirmado devuelve None ("Sin datos"), NO 100%.
  - Los porcentajes se manejan como fracción 0..1 y se comparan con EPS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Tolerancia para comparaciones de punto flotante. Sin esto, 4/5 >= 0.80
# puede fallar por representación binaria y marcar incumplimiento un caso
# que el pliego admite explícitamente (ej.: 5 bachas, 1 con hallazgo).
EPS = 1e-9


def cumple(valor: float, objetivo: float) -> bool:
    """¿El valor medido alcanza el objetivo? Comparación tolerante a floats."""
    return valor >= objetivo - EPS


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def ratio_sin_hallazgo(total: int, con_hallazgo: int) -> float | None:
    """(total - con_hallazgo) / total. Devuelve None si no hay inventario cargado.

    Base de todos los ítems cuantitativos: el auditor nunca cuenta totales,
    solo informa cuántas unidades presentan hallazgo.
    """
    if total is None or total <= 0:
        return None
    return _clamp((total - (con_hallazgo or 0)) / total)


def promedio(valores) -> float | None:
    """Promedio ignorando los None (sectores/períodos sin datos)."""
    vals = [v for v in valores if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


# ---------------------------------------------------------------------------
# MÓDULO LIMPIEZA
# ---------------------------------------------------------------------------

# Valor de cada estado de ítem. CUMPLE es el default y no requiere carga.
VALOR_ESTADO = {
    "CUMPLE": 1.0,
    "DESVIO_PARCIAL": 0.5,   # "a medias" en la planilla original
    "DESVIO_TOTAL": 0.0,
}
NO_VERIFICABLE = "NO_VERIFICABLE"  # excluido del cálculo de ese control


def sector_limpieza(items_activos: list[str],
                    desvios: dict[str, str],
                    confirmado: bool) -> float | None:
    """% de un sector en un control (Qn).

    items_activos: ítems del sector no desactivados por el admin (NO PROCEDE).
    desvios:       {item_id: estado} — solo los ítems que el auditor tocó.
    confirmado:    el auditor cerró el sector explícitamente.

    Un sector sin desvíos pero confirmado = 100%. Sin confirmar = None.
    """
    if not confirmado:
        return None

    valores = []
    for item in items_activos:
        estado = desvios.get(item, "CUMPLE")
        if estado == NO_VERIFICABLE:
            continue  # excluido del cálculo de este control
        valores.append(VALOR_ESTADO[estado])

    if not valores:
        return None  # todos los ítems resultaron no verificables
    return sum(valores) / len(valores)


# El control de limpieza es DIARIO: se exige uno por cada día del mes.
# La cobertura mínima por debajo de la cual el mes se considera poco
# representativo y la certificación lo advierte (editable por el admin).
COBERTURA_MINIMA_DEFAULT = 0.80


def sector_mensual(scores_diarios: list[float | None]) -> float | None:
    """Estado mensual del sector = promedio de los días efectivamente auditados.

    Los días sin control se excluyen del promedio en lugar de contar como cero:
    no medir no es lo mismo que medir mal, y castigar al contratista por una
    auditoría que el operador no hizo sería imputarle un incumplimiento que
    nadie constató.

    El contrapeso es la cobertura: como el promedio sale solo de los días
    auditados, un mes con pocos días relevados es poco representativo y el
    resultado depende mucho de CUÁLES días se auditaron. Por eso
    `completitud_mes` informa siempre la cobertura y la certificación advierte
    cuando cae por debajo del mínimo. La decisión de certificar un mes con baja
    cobertura es del operador, no un efecto silencioso del cálculo.
    """
    return promedio(scores_diarios)


def dias_del_mes(periodo: str) -> list[str]:
    """Todos los días de un período 'YYYY-MM', en formato ISO."""
    import calendar as _cal
    anio, mes = (int(x) for x in periodo.split("-"))
    ultimo = _cal.monthrange(anio, mes)[1]
    return [f"{periodo}-{d:02d}" for d in range(1, ultimo + 1)]


TURNOS = ("MANANA", "TARDE")


def completitud_mes(dias_auditados: list[str], periodo: str,
                    hasta: str | None = None,
                    cobertura_minima: float = COBERTURA_MINIMA_DEFAULT) -> dict:
    """Qué días del mes se auditaron y cuáles faltan.

    Un día se cuenta como auditado cuando tiene al menos un turno cerrado; la
    exigencia de los dos turnos se mide aparte, en `completitud_turnos`.

    dias_auditados: fechas ISO con al menos un control cerrado.
    hasta:          fecha ISO hasta la cual el plazo ya venció (normalmente hoy).
                    Los días posteriores todavía están en plazo y no son un
                    faltante: mezclarlos generaría alarma todos los días del mes.
    """
    esperados = dias_del_mes(periodo)
    auditados = sorted(set(d for d in dias_auditados if d in esperados))
    faltantes = [d for d in esperados if d not in auditados]

    vencidos = [d for d in faltantes if hasta is None or d < hasta]
    cobertura = len(auditados) / len(esperados) if esperados else None

    return {
        "periodo": periodo,
        "dias_esperados": len(esperados),
        "dias_auditados": auditados,
        "dias_faltantes": faltantes,
        # Días cuyo plazo ya pasó sin control: esto sí es un faltante real.
        "dias_vencidos_sin_control": vencidos,
        "completo": not faltantes,
        "cobertura": cobertura,
        "cobertura_minima": cobertura_minima,
        "cobertura_suficiente": (cobertura is not None
                                 and cobertura >= cobertura_minima - EPS),
    }


def completitud_turnos(turnos_cerrados: dict[str, list[str]], periodo: str,
                       hasta: str | None = None) -> dict:
    """Cobertura de las dos recorridas diarias exigidas (mañana y tarde).

    Se mide aparte del promedio de cumplimiento a propósito: un turno que no se
    hizo NO entra al promedio como 0% —no hay recorrida de la cual afirmar que
    el servicio estuvo mal—, pero sí es un incumplimiento del plan de auditoría
    y tiene que verse como tal.

    turnos_cerrados: {fecha ISO: [turnos con control cerrado]}.
    """
    esperados = dias_del_mes(periodo)
    vencidos = [d for d in esperados if hasta is None or d < hasta]

    completos, parciales, sin_control = [], [], []
    for d in esperados:
        hechos = set(turnos_cerrados.get(d, []))
        if len(hechos) >= len(TURNOS):
            completos.append(d)
        elif hechos:
            parciales.append({"fecha": d, "hechos": sorted(hechos),
                              "faltan": sorted(set(TURNOS) - hechos)})
        elif d in vencidos:
            sin_control.append(d)

    esperados_turnos = len(vencidos) * len(TURNOS)
    hechos_turnos = sum(len(set(turnos_cerrados.get(d, []))) for d in vencidos)

    return {
        "turnos_por_dia": len(TURNOS),
        "turnos_esperados": esperados_turnos,
        "turnos_cerrados": hechos_turnos,
        "dias_completos": completos,
        "dias_parciales": parciales,
        "dias_vencidos_sin_turno": sin_control,
        "cobertura_turnos": (hechos_turnos / esperados_turnos
                             if esperados_turnos else None),
    }


def estado_general_limpieza(scores_sectores: list[float | None]) -> float | None:
    """Estado general del mes = promedio de todos los sectores con datos."""
    return promedio(scores_sectores)


def cumplimiento_equipamiento(exigidos: int, faltantes: int) -> float | None:
    """(equipos exigidos - faltantes) / equipos exigidos.

    Por excepción: todo equipo se asume disponible; solo se cargan los
    faltantes o fuera de servicio.
    """
    return ratio_sin_hallazgo(exigidos, faltantes)


# ---------------------------------------------------------------------------
# CERTIFICACIÓN MENSUAL (PET cláusula 7 / PCP 4.3)
# ---------------------------------------------------------------------------

PESOS_CERTIFICACION_DEFAULT = {
    "documentacion": 0.10,
    "ley_19587": 0.10,
    "programacion_trabajos": 0.40,
    "maquinarias": 0.10,
    "insumos": 0.10,
    "calidad_servicio": 0.20,
}

# Ítems cuyo dato cargado es obligatorio (no permite redistribución de peso).
# Requisito contractual: la certificación es nula si falta cualquiera de estos.
ITEMS_OBLIGATORIOS = {"documentacion", "ley_19587", "programacion_trabajos"}

# ⚠ VALOR PROVISORIO — NO SURGE DEL PLIEGO ⚠
# El PET indica que la calidad de servicio se ajusta "por la cantidad de no
# conformidades registradas", pero NO fija la fórmula ni el porcentaje. Estos
# números son un supuesto de implementación, no un dato contractual, y afectan
# directamente el importe a certificar al contratista.
#
# Son solo el default inicial: el valor efectivo se lee de la configuración
# (claves 'penalizacion_por_nc' y 'penalizacion_nc_tope') y el admin puede
# cambiarlo sin tocar código. Mientras la clave 'penalizacion_nc_confirmada'
# siga en False, la API y los informes emiten una advertencia visible.
PENALIZACION_NC_DEFAULT = 0.01        # 1% por cada NC abierta
PENALIZACION_NC_TOPE_DEFAULT = 0.20   # tope acumulado del 20%


def item_binario(hallazgos: int) -> float:
    """Ítems 1 y 2: un solo hallazgo evidenciado penaliza el ítem completo."""
    return 0.0 if (hallazgos or 0) > 0 else 1.0


def item_programacion(horas_programadas: float, horas_perdidas: float) -> float | None:
    """Ítem 3: horas hombre cumplidas / programadas."""
    if not horas_programadas or horas_programadas <= 0:
        return None
    return _clamp((horas_programadas - (horas_perdidas or 0)) / horas_programadas)


def item_maquinarias(dias_periodo: int,
                     dias_baja_por_equipo: list[int]) -> float | None:
    """Ítem 4: disponibilidad de las máquinas exigidas por el pliego.

    Se mide sobre el inventario de equipos, no sobre horas: el pliego exige
    tener X máquinas y lo verificable es si están. Las "horas máquina" del
    diseño original no eran exigibles ni medibles en el aeropuerto.

    Cada equipo aporta su propia disponibilidad —días en servicio sobre días
    del período— y recién después se promedia entre equipos. Contar en cambio
    "cuántos faltaron cada día" hacía que una máquina rota todo el mes valiera
    lo mismo que treinta máquinas rotas un día cada una, que no es lo que se
    quiere descontar del pago.

    dias_periodo:         días del período sobre los que se mide.
    dias_baja_por_equipo: días de baja de cada equipo exigido.
    """
    if not dias_periodo or dias_periodo <= 0:
        return None
    if not dias_baja_por_equipo:
        return None          # sin equipos exigidos no hay nada que promediar
    return promedio([_clamp((dias_periodo - min(d, dias_periodo)) / dias_periodo)
                     for d in dias_baja_por_equipo])


def item_insumos(insumos: list[dict]) -> float | None:
    """Ítem 5: insumos con stock >= punto de pedido / insumos controlados.

    insumos: [{"nombre":..., "stock": n, "punto_pedido": n}, ...]
    """
    if not insumos:
        return None
    ok = sum(1 for i in insumos if (i.get("stock") or 0) >= (i.get("punto_pedido") or 0))
    return ok / len(insumos)


def item_calidad_servicio(estado_general: float | None,
                          nc_abiertas: int = 0,
                          penalizacion_nc: float = PENALIZACION_NC_DEFAULT,
                          tope: float = PENALIZACION_NC_TOPE_DEFAULT) -> float | None:
    """Ítem 6: se alimenta del estado general del check-list, ajustado por NC."""
    if estado_general is None:
        return None
    descuento = min((nc_abiertas or 0) * penalizacion_nc, tope)
    return _clamp(estado_general - descuento)


def certificacion_mensual(items: dict[str, float | None],
                          pesos: dict[str, float] | None = None) -> dict:
    """% Certificación = Σ (%ítem × peso).

    Requisito contractual: los ítems obligatorios (ITEMS_OBLIGATORIOS) deben
    estar cargados obligatoriamente. Su ausencia bloquea la certificación (→ None).

    Los demás ítems sin datos se excluyen y el peso se redistribuye
    proporcionalmente entre los evaluados.
    """
    pesos = pesos or PESOS_CERTIFICACION_DEFAULT
    faltantes = sorted(k for k in pesos if items.get(k) is None)

    # Verificar que todos los ítems obligatorios estén presentes.
    faltantes_obligatorios = [k for k in ITEMS_OBLIGATORIOS if items.get(k) is None]
    if faltantes_obligatorios:
        return {"porcentaje": None, "peso_evaluado": 0.0,
                "items_sin_datos": faltantes,
                "items_obligatorios_faltantes": faltantes_obligatorios,
                "detalle": {}}

    evaluados = {k: v for k, v in items.items() if v is not None}
    peso_evaluado = sum(pesos[k] for k in evaluados if k in pesos)
    if peso_evaluado <= 0:
        return {"porcentaje": None, "peso_evaluado": 0.0,
                "items_sin_datos": faltantes, "detalle": {}}

    detalle, total = {}, 0.0
    for k, v in evaluados.items():
        if k not in pesos:
            continue
        peso_norm = pesos[k] / peso_evaluado
        aporte = v * peso_norm
        total += aporte
        detalle[k] = {"valor": v, "peso": pesos[k],
                      "peso_normalizado": peso_norm, "aporte": aporte}

    return {"porcentaje": _clamp(total), "peso_evaluado": peso_evaluado,
            "items_sin_datos": faltantes, "detalle": detalle}


def importe_a_certificar(porcentaje: float | None, monto_adjudicado: float | None):
    if porcentaje is None or monto_adjudicado is None:
        return None
    return round(porcentaje * monto_adjudicado, 2)


# ---------------------------------------------------------------------------
# LoS 3.1 — BAÑOS
# ---------------------------------------------------------------------------

# Objetivo por tipo de núcleo sanitario (ítem 3.1.a, artefactos en servicio).
OBJETIVO_NUCLEO = {
    "DAMAS": 0.80,
    "CABALLEROS": 0.80,
    "PMR": 1.00,
    "RECINTO_BEBES": 1.00,
}


def banos_en_servicio(nucleo: dict) -> dict:
    """3.1.a — LoS por grupo de artefacto dentro de un núcleo sanitario.

    nucleo: {"nombre":..., "tipo": "DAMAS"|"CABALLEROS"|"PMR",
             "artefactos": {"inodoros": {"instalados": n, "fuera_servicio": n}, ...}}
    """
    objetivo = OBJETIVO_NUCLEO[nucleo["tipo"]]
    grupos, todos_ok = {}, True

    for grupo, datos in (nucleo.get("artefactos") or {}).items():
        pct = ratio_sin_hallazgo(datos.get("instalados"), datos.get("fuera_servicio"))
        ok = None if pct is None else cumple(pct, objetivo)
        if ok is False:
            todos_ok = False
        grupos[grupo] = {"porcentaje": pct, "objetivo": objetivo, "cumple": ok}

    evaluados = [g for g in grupos.values() if g["cumple"] is not None]
    return {"nucleo": nucleo.get("nombre"), "tipo": nucleo["tipo"],
            "objetivo": objetivo, "grupos": grupos,
            "cumple": todos_ok if evaluados else None}


def limpieza_banos(sector: dict) -> dict:
    """3.1.b — Limpieza por sector de baño, equipo por equipo.

    sector: {"sector": "BACHAS"|"BOXES"|"MINGITORIOS"|"PMR"|"RECINTO_BEBES",
             "equipos": {"bachas": {"total": n, "con_hallazgo": n, "objetivo": 0.80}, ...}}

    El objetivo viaja por equipo porque dentro de un mismo sector conviven
    exigencias distintas (ej.: en Bachas, las bachas son 80% pero espejos,
    pisos y cambiador son 100%).
    """
    equipos, todos_ok = {}, True
    for nombre, datos in (sector.get("equipos") or {}).items():
        objetivo = datos.get("objetivo", 0.80)
        pct = ratio_sin_hallazgo(datos.get("total"), datos.get("con_hallazgo"))
        ok = None if pct is None else cumple(pct, objetivo)
        if ok is False:
            todos_ok = False
        equipos[nombre] = {"porcentaje": pct, "objetivo": objetivo, "cumple": ok}

    evaluados = [e for e in equipos.values() if e["cumple"] is not None]
    return {"sector": sector.get("sector"), "equipos": equipos,
            "cumple": todos_ok if evaluados else None}


# ---------------------------------------------------------------------------
# LoS 3.2 — CONFORT TÉRMICO  (IRJ: verano categoría B, invierno categoría C)
# ---------------------------------------------------------------------------

CONFORT_IRJ = {
    "VERANO": {"categoria": "B", "objetivo": 24.5, "tolerancia": 1.5,
               "min": 23.0, "max": 26.0, "vel_aire_max": 0.19, "ppd_max": 0.10},
    "INVIERNO": {"categoria": "C", "objetivo": 22.0, "tolerancia": 3.0,
                 "min": 19.0, "max": 25.0, "vel_aire_max": 0.21, "ppd_max": 0.15},
}


def confort_termico(temperatura: float, estacion: str,
                    velocidad_aire: float | None = None,
                    params: dict | None = None) -> dict:
    """Valor medido (no hallazgo): cumple si cae dentro del rango estacional."""
    p = (params or CONFORT_IRJ)[estacion]
    if temperatura is None:
        return {"cumple": None, "motivo": "Sin medición"}

    temp_ok = p["min"] - EPS <= temperatura <= p["max"] + EPS
    aire_ok = True if velocidad_aire is None else velocidad_aire <= p["vel_aire_max"] + EPS

    return {"estacion": estacion, "categoria": p["categoria"],
            "temperatura": temperatura, "rango": [p["min"], p["max"]],
            "temperatura_ok": temp_ok,
            "velocidad_aire": velocidad_aire,
            "velocidad_aire_max": p["vel_aire_max"], "velocidad_aire_ok": aire_ok,
            "cumple": temp_ok and aire_ok}


# ---------------------------------------------------------------------------
# LoS 3.3 — ILUMINACIÓN (objetivo 90%)
# ---------------------------------------------------------------------------

def iluminacion_sector(total_luminarias: int, quemadas: int,
                       consecutivas_mismo_cono: bool = False,
                       objetivo: float = 0.90) -> dict:
    """% encendidas >= 90%, pero dos o más quemadas consecutivas en el mismo
    cono de luz hacen incumplir el sector aunque supere el umbral."""
    pct = ratio_sin_hallazgo(total_luminarias, quemadas)
    if pct is None:
        return {"porcentaje": None, "cumple": None, "motivo": "Inventario sin cargar"}

    umbral_ok = cumple(pct, objetivo)
    ok = umbral_ok and not consecutivas_mismo_cono
    motivo = None
    if umbral_ok and consecutivas_mismo_cono:
        motivo = "Dos o más luminarias consecutivas en el mismo cono de luz"

    return {"porcentaje": pct, "objetivo": objetivo, "umbral_ok": umbral_ok,
            "consecutivas_mismo_cono": consecutivas_mismo_cono,
            "cumple": ok, "motivo": motivo}


# ---------------------------------------------------------------------------
# LoS 3.4 — ESTADO DE INFRAESTRUCTURA (escala A/B/C/D, objetivo B o mejor)
# ---------------------------------------------------------------------------

ESCALA_ABCD = ["A", "B", "C", "D"]
GRADOS_QUE_CUMPLEN = {"A", "B"}
PRIORIDAD_NC = {"C": "PROGRAMADA", "D": "INMEDIATA"}


def evaluar_grado(grado: str | None) -> dict:
    """A o B = cumple. C o D = no cumple y genera no conformidad."""
    if grado is None:
        return {"grado": None, "cumple": None, "genera_nc": False, "prioridad_nc": None}
    grado = grado.upper()
    if grado not in ESCALA_ABCD:
        raise ValueError(f"Grado inválido: {grado}")
    ok = grado in GRADOS_QUE_CUMPLEN
    return {"grado": grado, "cumple": ok, "genera_nc": not ok,
            "prioridad_nc": PRIORIDAD_NC.get(grado)}


def infraestructura(subitems: dict[str, str | None]) -> dict:
    """subitems: {"alfombras_manchas": "A", "veredas_grietas": "D", ...}

    Por excepción los sub-ítems no tocados llegan como "A"/"B" (cumplen);
    el auditor solo degrada a C o D.
    """
    detalle = {k: evaluar_grado(v) for k, v in subitems.items()}
    evaluados = [d for d in detalle.values() if d["cumple"] is not None]
    ncs = [{"subitem": k, "grado": d["grado"], "prioridad": d["prioridad_nc"]}
           for k, d in detalle.items() if d["genera_nc"]]
    return {"subitems": detalle,
            "no_conformidades": ncs,
            "cumple": all(d["cumple"] for d in evaluados) if evaluados else None}


def grado_veredas_grietas(separacion_cm: float) -> str:
    """Grietas en veredas y vialidades: D si la separación supera 1 cm."""
    return "D" if separacion_cm > 1.0 else "B"


def grado_veredas_superficie(superficie_cm2: float, diametro_cm: float,
                             profundidad_cm: float,
                             escalonamiento_cm: float = 0.0) -> str:
    """Superficies dañadas / baldosas sueltas, según umbrales del manual.

    D: daño > 1 m² (10.000 cm²) y profundidad > 4 cm
    C: escalonamiento 0,5–1 cm, o daño > 15 cm de diámetro y prof. > 2 cm
    B: superficie < 40 cm², diámetro < 15 cm y profundidad < 2 cm
    """
    if superficie_cm2 > 10000 and profundidad_cm > 4:
        return "D"
    if 0.5 <= escalonamiento_cm <= 1.0:
        return "C"
    if diametro_cm > 15 and profundidad_cm > 2:
        return "C"
    if superficie_cm2 < 40 and diametro_cm < 15 and profundidad_cm < 2:
        return "B"
    return "C"


# ---------------------------------------------------------------------------
# LoS 3.5 — ASIENTOS EN PREEMBARQUE
# ---------------------------------------------------------------------------

ASIENTOS_MINIMOS_IRJ = 38  # 50% de 76 PAX promedio por aeronave (regla IATA)


def asientos_preembarque(instalados: int, inutilizables: int = 0,
                         minimo: int = ASIENTOS_MINIMOS_IRJ) -> dict:
    """Cumple si los asientos utilizables alcanzan el mínimo requerido."""
    if not instalados:
        return {"utilizables": None, "cumple": None, "motivo": "Inventario sin cargar"}
    utilizables = max(0, instalados - (inutilizables or 0))
    return {"instalados": instalados, "inutilizables": inutilizables or 0,
            "utilizables": utilizables, "minimo": minimo,
            "cumple": utilizables >= minimo}


# ---------------------------------------------------------------------------
# LoS 3.6 — PUNTOS DE CARGA (25 tomas cada 100 PAX en hora pico, por puerta)
# ---------------------------------------------------------------------------

TOMAS_POR_100_PAX = 25


def tomas_requeridas(php: int, tomas_por_100: int = TOMAS_POR_100_PAX) -> int:
    return math.ceil((php or 0) * tomas_por_100 / 100)


def puntos_de_carga(puerta: dict, tomas_por_100: int = TOMAS_POR_100_PAX) -> dict:
    """puerta: {"nombre":..., "php": n, "instaladas": n, "fuera_servicio": n}"""
    instaladas = puerta.get("instaladas")
    if not instaladas:
        return {"puerta": puerta.get("nombre"), "cumple": None,
                "motivo": "Inventario sin cargar"}
    req = tomas_requeridas(puerta.get("php"), tomas_por_100)
    operativas = max(0, instaladas - (puerta.get("fuera_servicio") or 0))
    return {"puerta": puerta.get("nombre"), "php": puerta.get("php"),
            "requeridas": req, "instaladas": instaladas,
            "operativas": operativas, "cumple": operativas >= req}


# ---------------------------------------------------------------------------
# LoS 3.7 — MEDIOS DE ELEVACIÓN
# ---------------------------------------------------------------------------

ELEVACION_IRJ = {
    "CON_REDUNDANCIA": {"disponibilidad_min": 0.9166, "indisp_max_mensual_hs": 60},
    "SIN_REDUNDANCIA": {"disponibilidad_min": 0.93, "indisp_max_mensual_hs": 48},
}
INDISP_MAX_POR_EVENTO_HS = 48

# Base horaria mensual para disponibilidad de medios de elevación.
#
# ⚠ Los topes del manual (60 hs con redundancia / 48 hs sin) son coherentes con
# los mínimos de disponibilidad (91,66% / 93%) SOLO si la base es calendario:
#     60 / (30 × 24) = 8,33%  ⇒  91,66% de disponibilidad  (coincidencia exacta)
# Con la base del horario operativo (14 hs/día = 420 hs), 60 hs equivaldrían a
# 85,7% de disponibilidad y el tope horario nunca podría alcanzarse sin violar
# antes el mínimo de 91,66%: los dos criterios se contradicen.
# Por eso este ítem usa 24 hs/día. El horario operativo 07:00–21:00 se sigue
# usando para la disponibilidad de pista y rodajes (3.10). Es un parámetro
# editable por el admin si el operador define otro criterio.
HORAS_DIA_ELEVACION = 24.0


def horas_base_mes(dias_del_mes: int,
                   horas_por_dia: float = HORAS_DIA_ELEVACION) -> float:
    """Horas base del mes para el cálculo de disponibilidad."""
    return dias_del_mes * horas_por_dia


def medio_elevacion(equipo: dict, dias_del_mes: int,
                    horas_operativas_dia: float = HORAS_DIA_ELEVACION,
                    params: dict | None = None) -> dict:
    """equipo: {"nombre":..., "redundancia": True/False,
                "eventos": [{"horas": h}, ...]}

    Incumple si baja del mínimo de disponibilidad, si supera las horas de
    indisponibilidad mensual, o si algún evento individual excede 48 hs.
    """
    p = (params or ELEVACION_IRJ)["CON_REDUNDANCIA" if equipo.get("redundancia")
                                  else "SIN_REDUNDANCIA"]
    base = horas_base_mes(dias_del_mes, horas_operativas_dia)
    eventos = equipo.get("eventos") or []
    hs_indisp = sum(e.get("horas", 0) for e in eventos)
    peor_evento = max((e.get("horas", 0) for e in eventos), default=0)

    disponibilidad = _clamp((base - hs_indisp) / base) if base > 0 else None
    disp_ok = disponibilidad is not None and cumple(disponibilidad, p["disponibilidad_min"])
    mensual_ok = hs_indisp <= p["indisp_max_mensual_hs"] + EPS
    evento_ok = peor_evento <= INDISP_MAX_POR_EVENTO_HS + EPS

    return {"equipo": equipo.get("nombre"),
            "redundancia": bool(equipo.get("redundancia")),
            "horas_base": base, "horas_indisponible": hs_indisp,
            "peor_evento_hs": peor_evento,
            "disponibilidad": disponibilidad,
            "disponibilidad_min": p["disponibilidad_min"],
            "indisp_max_mensual_hs": p["indisp_max_mensual_hs"],
            "disponibilidad_ok": disp_ok, "mensual_ok": mensual_ok,
            "evento_ok": evento_ok,
            "cumple": disp_ok and mensual_ok and evento_ok}


# ---------------------------------------------------------------------------
# LoS 3.8 — LIMPIEZA DE TERMINAL (escala A/B/C/D por umbral de llenado)
# ---------------------------------------------------------------------------

def grado_por_llenado(porcentaje_llenado: float) -> str:
    """Contenedores y cestos: A <=50% · B 51-65% · C 66-80% · D >80%."""
    if porcentaje_llenado <= 50:
        return "A"
    if porcentaje_llenado <= 65:
        return "B"
    if porcentaje_llenado <= 80:
        return "C"
    return "D"


def limpieza_terminal(subitems: dict[str, str | None]) -> dict:
    """Mismo criterio A/B cumple que infraestructura; los grados cualitativos
    (telarañas, vidrios, corredores) los elige el auditor solo si observa C o D."""
    return infraestructura(subitems)


# ---------------------------------------------------------------------------
# LoS 3.9 — GEL / GRUPOS ELECTRÓGENOS (RAAC 154)
# ---------------------------------------------------------------------------

# Tiempo máximo de conmutación por categoría de ayuda luminosa (segundos).
TIEMPOS_CONMUTACION = {
    "APROX_NO_PRECISION": 15,   # caso típico de IRJ
    "APROX_PRECISION_CAT_I": 15,
    "APROX_PRECISION_CAT_II_III": 1,
}


def prueba_gel(tiempo_medido_s: float,
               categoria: str = "APROX_NO_PRECISION",
               tabla: dict | None = None) -> dict:
    """Valor medido (no hallazgo): cumple si el tiempo medido no supera el máximo."""
    maximo = (tabla or TIEMPOS_CONMUTACION)[categoria]
    if tiempo_medido_s is None:
        return {"cumple": None, "motivo": "Sin medición"}
    return {"categoria": categoria, "tiempo_medido_s": tiempo_medido_s,
            "tiempo_maximo_s": maximo,
            "cumple": tiempo_medido_s <= maximo + EPS}


# ---------------------------------------------------------------------------
# LoS 3.10 — PISTA Y RODAJES
# ---------------------------------------------------------------------------

PCI_PISTA = {"umbral": 70, "proporcion_min": 0.85}
PCI_RODAJE = {"umbral": 60, "proporcion_min": 0.70}

ESCALA_PCI = [
    (85, 100, "Bueno"), (70, 85, "Satisfactorio"), (55, 70, "Razonable"),
    (40, 55, "Malo"), (25, 40, "Muy malo"), (10, 25, "Grave"), (0, 10, "Nefasto"),
]


def clasificar_pci(pci: float) -> str:
    for minimo, maximo, etiqueta in ESCALA_PCI:
        if minimo <= pci <= maximo:
            return etiqueta
    raise ValueError(f"PCI fuera de rango 0-100: {pci}")


def disponibilidad_pista(indisponibilidades_no_programadas: int) -> dict:
    """100% del horario operativo salvo cierres programados: cualquier
    indisponibilidad no programada hace incumplir."""
    n = indisponibilidades_no_programadas or 0
    return {"eventos_no_programados": n, "cumple": n == 0}


def pci_secciones(secciones: list[dict], tipo: str) -> dict:
    """secciones: [{"id": "PISTA-01", "pci": 78}, ...]
    tipo: "PISTA" (85% con PCI>70) o "RODAJE" (70% con PCI>60)."""
    p = PCI_PISTA if tipo == "PISTA" else PCI_RODAJE
    if not secciones:
        return {"tipo": tipo, "cumple": None, "motivo": "Sin secciones cargadas"}

    sobre_umbral = [s for s in secciones if s.get("pci", 0) > p["umbral"]]
    proporcion = len(sobre_umbral) / len(secciones)
    return {"tipo": tipo, "umbral_pci": p["umbral"],
            "proporcion_min": p["proporcion_min"],
            "secciones_totales": len(secciones),
            "secciones_sobre_umbral": len(sobre_umbral),
            "proporcion": proporcion,
            "cumple": cumple(proporcion, p["proporcion_min"])}


# ---------------------------------------------------------------------------
# LoS 3.12 — RESULTADO GLOBAL
# ---------------------------------------------------------------------------

@dataclass
class ItemLoS:
    """Estado consolidado de uno de los 11 ítems del manual."""
    clave: str
    nombre: str
    aplica: bool = True                 # pasarelas: NO APLICA en IRJ
    cumple: bool | None = None          # None = sin datos del período
    valor: float | None = None
    objetivo: float | None = None
    nc_abiertas: int = 0
    detalle: dict = field(default_factory=dict)

    @property
    def estado(self) -> str:
        if not self.aplica:
            return "NO_APLICA"
        if self.cumple is None:
            return "SIN_DATOS"
        return "CUMPLE" if self.cumple else "NO_CUMPLE"

    @property
    def semaforo(self) -> str:
        """Amarillo = cumple justo en el límite o con no conformidades abiertas."""
        if self.estado in ("NO_APLICA", "SIN_DATOS"):
            return "GRIS"
        if not self.cumple:
            return "ROJO"
        en_el_limite = (self.valor is not None and self.objetivo is not None
                        and abs(self.valor - self.objetivo) <= EPS)
        return "AMARILLO" if (en_el_limite or self.nc_abiertas > 0) else "VERDE"


def resultado_los(items: list[ItemLoS]) -> dict:
    """% global LoS = ítems que cumplen / ítems aplicables evaluados.

    Los NO APLICA se excluyen; los SIN_DATOS también (no se cuentan como
    incumplimiento, pero se informan aparte para que no pasen inadvertidos).
    """
    aplicables = [i for i in items if i.aplica]
    evaluados = [i for i in aplicables if i.cumple is not None]
    sin_datos = [i.clave for i in aplicables if i.cumple is None]

    porcentaje = (sum(1 for i in evaluados if i.cumple) / len(evaluados)
                  if evaluados else None)

    return {"porcentaje": porcentaje,
            "items_aplicables": len(aplicables),
            "items_evaluados": len(evaluados),
            "items_cumplen": sum(1 for i in evaluados if i.cumple),
            "items_sin_datos": sin_datos,
            "no_aplica": [i.clave for i in items if not i.aplica],
            "items": [{"clave": i.clave, "nombre": i.nombre, "aplica": i.aplica,
                       "estado": i.estado, "semaforo": i.semaforo, "valor": i.valor,
                       "objetivo": i.objetivo, "nc_abiertas": i.nc_abiertas}
                      for i in items]}
