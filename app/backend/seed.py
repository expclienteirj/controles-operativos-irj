"""
Seed de configuración — Aeropuerto IRJ (categoría G5).

Carga sectores, ítems, equipamiento, ítems LoS y todos los umbrales del manual.
NO carga inventario físico: esas tablas quedan vacías a propósito y las completa
el admin desde "Configuración del Aeropuerto" (onboarding, sección 4.2).

Idempotente: se puede reejecutar sin duplicar filas ni pisar valores que el
admin haya editado.
"""

from __future__ import annotations

import json
import sqlite3

import calc

# --------------------------------------------------------------------------
# 4.1 — Datos generales y umbrales (grupo, clave, valor, descripción)
# --------------------------------------------------------------------------

CONFIG = [
    # -- general ------------------------------------------------------------
    ("general", "aeropuerto_codigo", "IRJ", "Código IATA/OACI del aeropuerto"),
    ("general", "aeropuerto_nombre",
     "Aeropuerto Capitán Vicente Almandos Almonacid", "Nombre del aeropuerto"),
    ("general", "aeropuerto_categoria", "G5", "Categoría del aeropuerto"),
    ("general", "concesionario", "Aeropuertos Argentina S.A.", "Concesionario"),
    ("general", "horario_operativo_inicio", "07:00", "Inicio del horario operativo"),
    ("general", "horario_operativo_fin", "21:00", "Fin del horario operativo"),
    ("general", "horas_operativas_dia", 14,
     "Horas operativas por día. Base del 100% exigido a pista y rodajes (3.10)"),
    # Freno a la prueba de contraseñas. Con la app publicada en internet, el
    # login queda expuesto a intentos automáticos sin límite.
    ("general", "login_max_intentos", 10,
     "Intentos fallidos de login antes de bloquear. 0 desactiva el freno"),
    ("general", "login_ventana_minutos", 15,
     "Minutos que se miran hacia atrás para contar los intentos fallidos"),
    ("general", "inicio_verano", "10-01", "Fecha de cambio a temporada verano (MM-DD)"),
    ("general", "inicio_invierno", "04-01", "Fecha de cambio a temporada invierno (MM-DD)"),
    ("general", "foto_obligatoria_desvio", True,
     "Exigir foto al registrar un desvío"),
    ("general", "periodicidad_control", "DIARIA",
     "El control de limpieza es diario: se exige uno por cada día del mes."),
    ("general", "cobertura_minima_mes", calc.COBERTURA_MINIMA_DEFAULT,
     "Proporción mínima de días del mes que deben tener control cerrado para "
     "que el resultado se considere representativo. Por debajo de este valor "
     "la certificación se emite con una advertencia visible. Los días sin "
     "auditar nunca penalizan al contratista: quedan fuera del promedio."),

    # -- LoS 3.1 baños ------------------------------------------------------
    ("los", "banos_objetivo_nucleo", calc.OBJETIVO_NUCLEO,
     "3.1.a — Objetivo de artefactos en servicio por tipo de núcleo"),
    ("los", "banos_limpieza_objetivos", {
        "BACHAS": {"bachas": 0.80, "jabonera": 0.80, "toallero": 0.80,
                   "cestos": 0.80, "espejos": 1.00, "pisos": 1.00,
                   "cambiador_bebes": 1.00},
        "BOXES": {"papel_higienico": 0.80, "cestos": 0.80, "inodoro": 0.80},
        "MINGITORIOS": {"mingitorios": 0.80},
        "PMR": {"bachas": 1.00, "jabonera": 1.00, "toallero": 1.00,
                "papel_higienico": 1.00, "cestos": 1.00, "inodoro": 1.00},
        "RECINTO_BEBES": {"bachas": 1.00, "jabonera": 1.00, "toallero": 1.00,
                          "cestos": 1.00, "cambiador": 1.00},
     }, "3.1.b — Objetivo de limpieza por equipo dentro de cada sector de baño"),
    ("los", "banos_criterios_hallazgo", {
        "bachas": "Mojada o con papeles",
        "jabonera": "Nivel de jabón menor al 10%",
        "toallero": "Toallas por debajo del 10%",
        "papel_higienico": "Disponibilidad menor al 10%",
        "cestos": "Lleno por encima del 80% de su capacidad",
        "espejos": "Sucios o manchados",
        "pisos": "Derrames por falta de limpieza",
        "inodoro": "Sucio, con derrames u obstruido",
        "mingitorios": "Sucios, con papeles u obstruidos",
        "cambiador_bebes": "Derrames o suciedad",
     }, "3.1.b — Texto de ayuda: qué constituye un hallazgo por equipo"),

    # -- LoS 3.2 confort térmico -------------------------------------------
    ("los", "confort_termico", calc.CONFORT_IRJ,
     "3.2 — IRJ: verano categoría B, invierno categoría C"),
    ("los", "confort_zonas",
     ["Hall público", "Arribos", "Embarque", "Bar"],
     "3.2 — Zonas donde se mide la temperatura operativa. Editable: si se "
     "habilita una sala nueva, se agrega acá sin tocar la aplicación."),

    # -- LoS 3.3 iluminación ------------------------------------------------
    ("los", "iluminacion_objetivo", 0.90, "3.3 — Mínimo de luminarias encendidas"),
    ("los", "iluminacion_horario_verano", "22:00", "3.3 — Horario de medición en verano"),
    ("los", "iluminacion_horario_invierno", "20:00", "3.3 — Horario de medición en invierno"),

    # -- LoS 3.4 infraestructura -------------------------------------------
    ("los", "infraestructura_grados_cumplen", sorted(calc.GRADOS_QUE_CUMPLEN),
     "3.4 — Grados que se consideran cumplimiento (objetivo IRJ: B o mejor)"),
    ("los", "infraestructura_escala", {
        "A": "Satisfactorio",
        "B": "Mejoras (aceptable, sin acción inmediata)",
        "C": "Mejoras considerables (requiere acción correctiva)",
        "D": "Insatisfactorio (riesgo a las personas, acción inmediata)",
     }, "3.4 — Descripción de la escala A/B/C/D"),
    ("los", "infraestructura_subitems", {
        "demarcacion_vial": ["parking", "sendas_peatonales", "cordones"],
        "alfombras": ["desgaste_deshilachado", "despegado_separacion",
                      "manchas_decoloracion"],
        "cielorraso": ["estado_general"],
        "vidrios": ["estado_general"],
        "pisos_interiores": ["estado_general"],
        "puertas": ["cerraduras", "alineacion_marco", "frenos"],
        "veredas_vialidades": ["grietas", "superficies_danadas"],
        "paredes_pintura": ["estado_general"],
     }, "3.4 — Sub-ítems y criterios de infraestructura"),
    ("los", "veredas_umbrales", {
        "grieta_separacion_d_cm": 1.0,
        "superficie_b_max_cm2": 40, "superficie_b_max_diametro_cm": 15,
        "superficie_b_max_profundidad_cm": 2,
        "escalonamiento_c_min_cm": 0.5, "escalonamiento_c_max_cm": 1.0,
        "superficie_d_min_cm2": 10000, "superficie_d_min_profundidad_cm": 4,
     }, "3.4 — Umbrales cuantitativos de veredas y vialidades"),

    # -- LoS 3.5 asientos ---------------------------------------------------
    ("los", "asientos_minimo", calc.ASIENTOS_MINIMOS_IRJ,
     "3.5 — Mínimo de asientos utilizables en preembarque (IRJ)"),
    ("los", "asientos_parametros_irj", {
        "pico_operaciones_diarias": 2, "pico_simultaneas": 1,
        "pax_promedio_aeronave": 76, "porcentaje_minimo_sentados": 0.50,
        "porcentaje_optimo": [0.50, 0.70],
     }, "3.5 — Parámetros de dimensionamiento (regla IATA)"),

    # -- LoS 3.6 puntos de carga -------------------------------------------
    ("los", "tomas_por_100_pax", calc.TOMAS_POR_100_PAX,
     "3.6 — Tomas exigidas cada 100 pasajeros en hora pico, por puerta"),

    # -- LoS 3.7 medios de elevación ---------------------------------------
    ("los", "elevacion_umbrales", calc.ELEVACION_IRJ,
     "3.7 — Disponibilidad mínima y tope mensual de indisponibilidad"),
    ("los", "elevacion_indisp_max_evento_hs", calc.INDISP_MAX_POR_EVENTO_HS,
     "3.7 — Indisponibilidad máxima por evento"),
    ("los", "elevacion_horas_dia", calc.HORAS_DIA_ELEVACION,
     "3.7 — Base horaria diaria. 24 hs: es la única base con la que los topes "
     "de 60/48 hs y los mínimos de 91,66%/93% resultan coherentes entre sí"),

    # -- LoS 3.8 limpieza de terminal --------------------------------------
    ("los", "limpieza_terminal_umbrales_llenado", {
        "A": [0, 50], "B": [51, 65], "C": [66, 80], "D": [81, 100],
     }, "3.8 — Umbrales de llenado para contenedores y cestos"),
    ("los", "limpieza_terminal_subitems", {
        "contenedores_basura": "Nivel de llenado antes de la recolección",
        "cestos_interiores": "Nivel de llenado",
        "cestos_externos": "Nivel de llenado",
        "telaranias_polvo": "A sin acumulación · B mínima en bajo tránsito · "
                            "C moderada en áreas visibles · D visible en alto tránsito",
        "limpieza_vidrios": "A sin suciedad · B leve en baja visibilidad · "
                            "C visible en alto tránsito · D acumulación considerable",
        "corredores_peatonales": "A sin residuos · B mínimos en bajo tránsito · "
                                 "C moderados en áreas accesibles · D acumulación significativa",
     }, "3.8 — Sub-ítems de limpieza de terminal"),

    # -- LoS 3.9 GEL --------------------------------------------------------
    ("los", "gel_tiempos_conmutacion", calc.TIEMPOS_CONMUTACION,
     "3.9 — Tiempo máximo de conmutación por categoría de ayuda (RAAC 154)"),
    ("los", "gel_categoria_irj", "APROX_NO_PRECISION",
     "3.9 — Categoría aplicable a IRJ (aproximaciones que no son de precisión)"),

    # -- LoS 3.10 pista y rodajes ------------------------------------------
    ("los", "pci_pista", calc.PCI_PISTA, "3.10 — 85% de secciones de pista con PCI > 70"),
    ("los", "pci_rodaje", calc.PCI_RODAJE, "3.10 — 70% de secciones de rodaje con PCI > 60"),
    ("los", "pci_escala", [{"min": a, "max": b, "etiqueta": e}
                           for a, b, e in calc.ESCALA_PCI],
     "3.10 — Escala de referencia de PCI"),

    # -- LoS 3.11 pasarelas -------------------------------------------------
    ("los", "pasarelas_aplica", False,
     "3.11 — IRJ no figura en la Tabla 10 del manual: no posee mangas"),

    # -- linkeo checklist -> LoS -------------------------------------------
    # Los baños y la limpieza de terminal ya se relevan todos los días en el
    # check-list. En vez de pedir un segundo relevamiento LoS del mismo objeto,
    # el resultado mensual de estos ítems alimenta directamente el nivel de
    # servicio. La clave es el equipo LoS; el valor, los ítems del check-list
    # que lo miden (por slug, ver seed._slug).
    ("los", "banos_link_checklist", {
        "bachas": ["lavabos"],
        "jabonera": ["jabonera"],
        "toallero": ["toallero"],
        "papel_higienico": ["papel_higienico"],
        "cestos": ["tachos_de_residuos"],
        "espejos": ["espejos", "espejos_gral"],
        "pisos": ["pisos_gral", "piso", "pisos"],
        "inodoro": ["inodoros_mingitorios", "inodoro_mingitorio"],
        "mingitorios": ["inodoros_mingitorios", "inodoro_mingitorio"],
        "cambiador_bebes": ["cambiador_de_bebes", "cambiador"],
        "cambiador": ["cambiador_de_bebes", "cambiador"],
     }, "3.1.b — Ítems del check-list diario que alimentan cada equipo del LoS "
        "de baños. Se promedian sobre los días auditados del mes."),
    ("los", "banos_sectores_checklist", ["sala_arribos", "banos_hall", "sanidad"],
     "3.1.b — Sectores del check-list que corresponden a baños"),

    ("los", "limpieza_terminal_link_checklist", {
        "contenedores_basura": ["contenedores_de_basura"],
        "cestos_interiores": ["papeleros"],
        "cestos_externos": ["cestos_residuos", "residuos"],
        "telaranias_polvo": ["techo"],
        "limpieza_vidrios": ["vidriera", "vidrieria", "vidrios",
                             "vidrieria_carpinteria_metalica"],
        "corredores_peatonales": ["pisos", "piso", "pisos_gral", "superficie"],
     }, "3.8 — Ítems del check-list diario que alimentan cada sub-ítem de "
        "limpieza de terminal."),

    # -- certificación 2.3 --------------------------------------------------
    ("certificacion", "pesos", calc.PESOS_CERTIFICACION_DEFAULT,
     "2.3 — Ponderaciones de los 6 ítems (PCP 4.3). Editables de común acuerdo"),
    ("certificacion", "penalizacion_nc_activa", False,
     "¿Las no conformidades descuentan del importe a certificar? Viene "
     "DESACTIVADA y así debe quedar mientras no se negocie: el PET dice que la "
     "calidad se ajusta según las no conformidades pero NO fija ninguna "
     "fórmula, y descontar con un criterio propio es cobrarle al contratista "
     "sobre una regla que nadie acordó. Las NC se registran e informan igual."),
    ("certificacion", "penalizacion_por_nc", calc.PENALIZACION_NC_DEFAULT,
     "⚠ VALOR PROVISORIO, NO SURGE DEL PLIEGO — Descuento aplicado al ítem "
     "Calidad de servicio por cada no conformidad abierta. El PET indica que la "
     "calidad se ajusta por la cantidad de no conformidades pero no fija la "
     "fórmula. Acordar el criterio real con el contratista y confirmarlo."),
    ("certificacion", "penalizacion_nc_tope_activo", False,
     "¿Rige un tope para el descuento por no conformidades? Viene DESACTIVADO: "
     "el tope no surge del pliego y, como cada desvío genera una NC, cualquier "
     "mes real lo alcanza — a partir de ahí la penalización deja de distinguir "
     "un mes de otro. Activarlo solo si se acuerda un tope con el contratista."),
    ("certificacion", "penalizacion_nc_tope", calc.PENALIZACION_NC_TOPE_DEFAULT,
     "⚠ VALOR PROVISORIO, NO SURGE DEL PLIEGO — Tope del descuento acumulado "
     "por no conformidades. Solo se aplica si el tope está activado."),
    ("certificacion", "penalizacion_nc_confirmada", False,
     "Marcar en True una vez acordado el criterio de penalización por no "
     "conformidad con el contratista. Mientras sea False, la certificación se "
     "emite con una advertencia visible en pantalla y en el informe PDF."),
]

# --------------------------------------------------------------------------
# 2.2 — Sectores e ítems del check-list (tomados de la planilla mensual)
# --------------------------------------------------------------------------

SECTORES = [
    ("sala_embarque", "Sala de embarque", [
        "Vidriera", "Piso", "Carpintería metálica", "Mostrador",
        "Artefactos de iluminación", "Carteles - AIRCOM & Media", "Papeleros",
        "Cinta organizadora", "Techo"]),
    ("sala_arribos", "Sala de arribos - Baño arribos", [
        "Pisos - Gral", "Vidriería - Carpintería metálica", "Inodoro - Mingitorio",
        "Lavabos", "Grifería", "Mármol", "Espejos - Gral", "Rejillas - Piso - Techo",
        # Los dispensers se desdoblan porque el LoS mide jabonera, toallero y
        # papel higiénico por separado (3.1.b) y agrupados se perdía el detalle.
        "Jabonera", "Toallero", "Tachos de residuos", "Puertas",
        "Artefactos de iluminación", "Cinta equipajes", "Techo"]),
    ("check_in", "Sector check-in", [
        "Mostradores", "Papeleros", "Artefactos de iluminación",
        "Columnas de acero inoxidable", "Cinta transportadora de equipajes", "Pisos", "Techo"]),
    ("hall_central", "Hall central", [
        "Vidriería", "Carpintería metálica", "Artefactos de iluminación - Exterior",
        "Artefactos de iluminación - Interior", "Vereda exterior - Ingresos",
        "Alfombras", "Pisos", "Carteles - Aircom & Media", "Papeleros", "Ceniceros",
        "Columnas acero inoxidable", "Tándem", "Monitores",
        "Sector experiencia al cliente", "Rejillas pared (toma aire)", "Alero",
        "Bases para elementos electrónicos", "Techo"]),
    ("sanidad", "Sanidad", [
        "Piso", "Puertas", "Bajo mesada", "Cambiador", "Ante baño", "Techo"]),
    ("banos_hall", "Baños hall - Sector público", [
        "Pisos gral", "Azulejos", "Lavabos", "Grifería", "Mármol", "Espejos",
        "Tachos de residuos", "Puertas", "Artefactos de iluminación",
        "Jabonera", "Toallero", "Papel higiénico", "Cambiador de bebés",
        "Inodoros - Mingitorios", "Gabinete bomberos", "Techo"]),
    ("air_side", "Air side", [
        "Cestos FOD", "Cestos residuos", "FOD",
        "Plataforma auxiliar - Patio compañías"]),
    ("estacionamiento", "Estacionamiento", [
        "Cordones", "Residuos", "Superficie", "Contenedores de basura"]),
    ("oficinas_aa", "Oficinas Aeropuertos Argentina (Operaciones)", [
        "Pisos gral", "Mobiliario", "Vidrios", "Tachos de residuos",
        "Artefactos de iluminación", "Techo"]),
]

EQUIPAMIENTO = [
    "Lavadora automática (control a pie)",
    "Lustradora (rotativa)",
    "Aspiradora de polvo (industrial)",
    "Hidrolavadora",
    "Sopladora",
    "Andamio / Escalera",
]

# --------------------------------------------------------------------------
# 3 — Los 11 ítems del manual LoS
# --------------------------------------------------------------------------
# requiere_inventario: tabla que el admin debe cargar antes de poder relevar
# el ítem. Mientras esté vacía, la UI lo muestra como "Requiere configuración".

# periodicidad: ver el comentario de los_items en schema.sql. Solo se diarizan
# los ítems que se relevan mirando: los que exigen instrumental (confort, GEL),
# un índice de ingeniería (PCI de pista) o acumulación de eventos (elevación)
# se dejan como estaban, y los que ya salen del check-list diario (baños,
# limpieza de terminal) no se cargan a mano.
LOS_ITEMS = [
    ("banos", "Baños", 1, True, "DERIVADO", "nucleos_sanitarios"),
    ("confort_termico", "Confort térmico", 2, True, "MENSUAL", None),
    ("iluminacion", "Iluminación", 3, True, "DIARIO", "luminarias_sector"),
    ("infraestructura", "Estado de infraestructura", 4, True, "DIARIO", None),
    ("asientos_preembarque", "Asientos en preembarque", 5, True, "DIARIO",
     "asientos_preembarque"),
    ("puntos_carga", "Puntos de carga", 6, True, "DIARIO", "puertas_embarque"),
    # IRJ no tiene ascensores ni escaleras mecánicas en la terminal (confirmado
    # por operaciones, 2026-08-11). Mismo criterio que pasarelas: la estructura
    # queda implementada por si el aeropuerto suma equipos, pero el ítem no
    # rige, así que no exige inventario ni entra en el resultado LoS.
    ("medios_elevacion", "Medios de elevación", 7, False, "POR_EVENTO",
     "medios_elevacion"),
    ("limpieza_terminal", "Limpieza de terminal", 8, True, "DERIVADO", None),
    ("gel", "Grupos electrógenos (GEL)", 9, True, "MENSUAL", None),
    ("pista_rodajes", "Pista y rodajes", 10, True, "MENSUAL", "secciones_pavimento"),
    # IRJ no posee mangas (Tabla 10 del manual). Estructura implementada por si
    # se habilita a futuro.
    ("pasarelas", "Pasarelas telescópicas", 11, False, "MENSUAL", None),
]


def _slug(texto: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")


def aplicar_seed(conn: sqlite3.Connection) -> dict:
    """Carga la configuración maestra. Idempotente y respeta ediciones del admin."""
    cur = conn.cursor()
    resumen = {"config": 0, "sectores": 0, "items": 0,
               "equipamiento": 0, "los_items": 0}

    # Config: INSERT OR IGNORE para no pisar valores ya ajustados por el admin.
    for grupo, clave, valor, descripcion in CONFIG:
        cur.execute(
            "INSERT OR IGNORE INTO config (clave, valor, grupo, descripcion) "
            "VALUES (?,?,?,?)",
            (clave, json.dumps(valor, ensure_ascii=False), grupo, descripcion))
        resumen["config"] += cur.rowcount

    for orden, (clave, nombre, items) in enumerate(SECTORES, start=1):
        cur.execute(
            "INSERT OR IGNORE INTO sectores_limpieza (clave, nombre, orden) "
            "VALUES (?,?,?)", (clave, nombre, orden))
        resumen["sectores"] += cur.rowcount
        sector_id = cur.execute(
            "SELECT id FROM sectores_limpieza WHERE clave = ?", (clave,)).fetchone()[0]

        for i, item in enumerate(items, start=1):
            cur.execute(
                "INSERT OR IGNORE INTO items_limpieza (sector_id, clave, nombre, orden) "
                "VALUES (?,?,?,?)", (sector_id, _slug(item), item, i))
            resumen["items"] += cur.rowcount

    for orden, nombre in enumerate(EQUIPAMIENTO, start=1):
        cur.execute(
            "INSERT OR IGNORE INTO equipamiento_limpieza (clave, nombre, orden) "
            "VALUES (?,?,?)", (_slug(nombre), nombre, orden))
        resumen["equipamiento"] += cur.rowcount

    for clave, nombre, orden, aplica, periodicidad, inventario in LOS_ITEMS:
        cur.execute(
            "INSERT OR IGNORE INTO los_items "
            "(clave, nombre, orden, aplica, periodicidad, requiere_inventario) "
            "VALUES (?,?,?,?,?,?)",
            (clave, nombre, orden, int(aplica), periodicidad, inventario))
        resumen["los_items"] += cur.rowcount
        # La periodicidad sí se actualiza en bases ya sembradas: el INSERT OR
        # IGNORE de arriba no toca las filas existentes y quedarían todas en el
        # default 'MENSUAL'.
        cur.execute("UPDATE los_items SET periodicidad = ? WHERE clave = ?",
                    (periodicidad, clave))

    # Fila única de asientos (el total lo carga el admin; 0 = sin configurar).
    cur.execute("INSERT OR IGNORE INTO asientos_preembarque (id, instalados) VALUES (1, 0)")

    conn.commit()
    return resumen
