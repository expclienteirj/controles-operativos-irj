"""
Validación de la configuración editable por el administrador.

La pantalla de Configuración deja cambiar los umbrales que alimentan el motor
de cálculo, y ese motor termina produciendo un porcentaje a pagar. Un valor
absurdo cargado por error (una ponderación negativa, un rango térmico invertido,
pesos que suman 1,3) no rompería la app: produciría números plausibles pero
incorrectos, que es el peor modo de falla posible acá.

Estas reglas rechazan el valor en el borde, antes de que llegue a la base.
"""

from __future__ import annotations

import calc


class ErrorValidacion(ValueError):
    pass


def _num(valor, clave):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErrorValidacion(f"'{clave}' debe ser un número")
    return float(valor)


def _proporcion(valor, clave, minimo=0.0, maximo=1.0):
    v = _num(valor, clave)
    if not (minimo <= v <= maximo):
        raise ErrorValidacion(
            f"'{clave}' debe estar entre {minimo} y {maximo} (recibido: {v}). "
            "Se expresa como fracción: 0,90 es 90%.")
    return v


def _entero_positivo(valor, clave, minimo=1):
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ErrorValidacion(f"'{clave}' debe ser un número entero")
    if valor < minimo:
        raise ErrorValidacion(f"'{clave}' debe ser {minimo} o mayor (recibido: {valor})")
    return valor


def _booleano(valor, clave):
    if not isinstance(valor, bool):
        raise ErrorValidacion(f"'{clave}' debe ser verdadero o falso")
    return valor


def _texto(valor, clave):
    if not isinstance(valor, str) or not valor.strip():
        raise ErrorValidacion(f"'{clave}' no puede quedar vacío")
    return valor.strip()


def _hora(valor, clave):
    texto = _texto(valor, clave)
    partes = texto.split(":")
    if len(partes) != 2 or not all(p.isdigit() for p in partes):
        raise ErrorValidacion(f"'{clave}' debe tener formato HH:MM (ej. 07:00)")
    h, m = int(partes[0]), int(partes[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ErrorValidacion(f"'{clave}' no es una hora válida: {texto}")
    return f"{h:02d}:{m:02d}"


def _mmdd(valor, clave):
    texto = _texto(valor, clave)
    partes = texto.split("-")
    if len(partes) != 2 or not all(p.isdigit() for p in partes):
        raise ErrorValidacion(f"'{clave}' debe tener formato MM-DD (ej. 10-01)")
    mes, dia = int(partes[0]), int(partes[1])
    if not (1 <= mes <= 12 and 1 <= dia <= 31):
        raise ErrorValidacion(f"'{clave}' no es una fecha válida: {texto}")
    return f"{mes:02d}-{dia:02d}"


# --------------------------------------------------------------------------
# Validadores compuestos
# --------------------------------------------------------------------------

def _pesos_certificacion(valor, clave):
    if not isinstance(valor, dict):
        raise ErrorValidacion("Las ponderaciones deben ser un objeto por ítem")

    esperados = set(calc.PESOS_CERTIFICACION_DEFAULT)
    recibidos = set(valor)
    if recibidos != esperados:
        faltan = esperados - recibidos
        sobran = recibidos - esperados
        detalle = []
        if faltan:
            detalle.append("faltan: " + ", ".join(sorted(faltan)))
        if sobran:
            detalle.append("no reconocidos: " + ", ".join(sorted(sobran)))
        raise ErrorValidacion(
            "Las ponderaciones deben incluir exactamente los 6 ítems de la "
            "certificación (" + "; ".join(detalle) + ")")

    for k, v in valor.items():
        _proporcion(v, f"peso de {k}")

    total = sum(valor.values())
    if abs(total - 1.0) > 0.001:
        raise ErrorValidacion(
            f"Las ponderaciones deben sumar 100% (suman {total:.1%}). "
            "Ajustá los pesos para que el total cierre.")
    return valor


def _lista_de_textos(valor, clave, minimo=0):
    """Lista de nombres, sin vacíos ni repetidos."""
    if not isinstance(valor, list):
        raise ErrorValidacion(f"'{clave}' debe ser una lista")
    if len(valor) < minimo:
        raise ErrorValidacion(f"'{clave}' necesita al menos {minimo} elemento(s)")

    limpios = []
    for v in valor:
        if not isinstance(v, str) or not v.strip():
            raise ErrorValidacion(f"'{clave}' no puede tener elementos vacíos")
        limpios.append(v.strip())

    if len(set(limpios)) != len(limpios):
        raise ErrorValidacion(f"'{clave}' tiene elementos repetidos")
    return limpios


def _confort_termico(valor, clave):
    if not isinstance(valor, dict) or set(valor) != {"VERANO", "INVIERNO"}:
        raise ErrorValidacion(
            "El confort térmico debe definir las estaciones VERANO e INVIERNO")

    for estacion, p in valor.items():
        if not isinstance(p, dict):
            raise ErrorValidacion(f"Parámetros inválidos para {estacion}")
        for campo in ("min", "max", "vel_aire_max"):
            if campo not in p:
                raise ErrorValidacion(f"Falta '{campo}' en {estacion}")
        minimo = _num(p["min"], f"{estacion}.min")
        maximo = _num(p["max"], f"{estacion}.max")
        if minimo >= maximo:
            raise ErrorValidacion(
                f"En {estacion}, la temperatura mínima ({minimo}) debe ser menor "
                f"que la máxima ({maximo})")
        if not (-10 <= minimo <= 50 and -10 <= maximo <= 50):
            raise ErrorValidacion(
                f"Las temperaturas de {estacion} están fuera de un rango "
                "razonable para una terminal (-10 a 50 °C)")
        _proporcion(p["vel_aire_max"], f"{estacion}.vel_aire_max", 0, 5)
    return valor


def _elevacion_umbrales(valor, clave):
    if not isinstance(valor, dict) or set(valor) != {"CON_REDUNDANCIA",
                                                     "SIN_REDUNDANCIA"}:
        raise ErrorValidacion(
            "Los umbrales de elevación deben definir CON_REDUNDANCIA y "
            "SIN_REDUNDANCIA")
    for grupo, p in valor.items():
        _proporcion(p.get("disponibilidad_min"), f"{grupo}.disponibilidad_min")
        horas = _num(p.get("indisp_max_mensual_hs"), f"{grupo}.indisp_max_mensual_hs")
        if not (0 <= horas <= 744):        # 31 días × 24 hs
            raise ErrorValidacion(
                f"'{grupo}.indisp_max_mensual_hs' debe estar entre 0 y 744 horas")
    return valor


def _pci(valor, clave):
    if not isinstance(valor, dict):
        raise ErrorValidacion(f"'{clave}' debe ser un objeto")
    umbral = _num(valor.get("umbral"), f"{clave}.umbral")
    if not (0 <= umbral <= 100):
        raise ErrorValidacion(f"El umbral de PCI debe estar entre 0 y 100")
    _proporcion(valor.get("proporcion_min"), f"{clave}.proporcion_min")
    return valor


def _objetivos_nucleo(valor, clave):
    if not isinstance(valor, dict):
        raise ErrorValidacion(f"'{clave}' debe ser un objeto por tipo de núcleo")
    for tipo, v in valor.items():
        _proporcion(v, f"objetivo de {tipo}")
    return valor


def _objetivos_limpieza_banos(valor, clave):
    if not isinstance(valor, dict):
        raise ErrorValidacion(f"'{clave}' debe ser un objeto por sector de baño")
    for sector, equipos in valor.items():
        if not isinstance(equipos, dict):
            raise ErrorValidacion(f"Los equipos de '{sector}' deben ser un objeto")
        for equipo, v in equipos.items():
            _proporcion(v, f"{sector}.{equipo}")
    return valor


# --------------------------------------------------------------------------
# Registro: clave de configuración -> validador
# --------------------------------------------------------------------------

VALIDADORES = {
    # generales
    "aeropuerto_codigo": _texto,
    "aeropuerto_nombre": _texto,
    "aeropuerto_categoria": _texto,
    "concesionario": _texto,
    "horario_operativo_inicio": _hora,
    "horario_operativo_fin": _hora,
    "horas_operativas_dia": lambda v, k: _proporcion(v, k, 1, 24),
    "inicio_verano": _mmdd,
    "inicio_invierno": _mmdd,
    "foto_obligatoria_desvio": _booleano,
    "periodicidad_control": _texto,
    "cobertura_minima_mes": _proporcion,

    # LoS
    "banos_objetivo_nucleo": _objetivos_nucleo,
    "banos_limpieza_objetivos": _objetivos_limpieza_banos,
    "confort_termico": _confort_termico,
    "confort_zonas": lambda v, k: _lista_de_textos(v, k, minimo=1),
    "iluminacion_objetivo": _proporcion,
    "iluminacion_horario_verano": _hora,
    "iluminacion_horario_invierno": _hora,
    "asientos_minimo": _entero_positivo,
    "tomas_por_100_pax": _entero_positivo,
    "elevacion_umbrales": _elevacion_umbrales,
    "elevacion_indisp_max_evento_hs": lambda v, k: _proporcion(v, k, 0, 744),
    "elevacion_horas_dia": lambda v, k: _proporcion(v, k, 1, 24),
    "pci_pista": _pci,
    "pci_rodaje": _pci,
    "pasarelas_aplica": _booleano,

    # certificación
    "pesos": _pesos_certificacion,
    "penalizacion_nc_activa": _booleano,
    "penalizacion_por_nc": _proporcion,
    "penalizacion_nc_tope_activo": _booleano,
    "penalizacion_nc_tope": _proporcion,
    "penalizacion_nc_confirmada": _booleano,
}


def validar(clave: str, valor):
    """Valida un valor de configuración. Devuelve el valor normalizado.

    Las claves sin validador registrado (textos de ayuda, escalas de referencia)
    pasan sin control: no alimentan cálculos.
    """
    validador = VALIDADORES.get(clave)
    if validador is None:
        return valor
    return validador(valor, clave)


# --------------------------------------------------------------------------
# Inventario
# --------------------------------------------------------------------------

TIPOS_NUCLEO = {"DAMAS", "CABALLEROS", "PMR", "RECINTO_BEBES"}
EQUIPOS_NUCLEO = {"inodoros", "mingitorios", "bachas", "jaboneras", "toalleros",
                  "cestos", "espejos", "cambiadores"}
TIPOS_SECCION = {"PISTA", "RODAJE"}


def validar_nucleo(datos: dict) -> dict:
    _texto(datos.get("nombre"), "nombre")
    tipo = datos.get("tipo")
    if tipo not in TIPOS_NUCLEO:
        raise ErrorValidacion(
            f"Tipo de núcleo inválido. Opciones: {', '.join(sorted(TIPOS_NUCLEO))}")

    equipos = datos.get("equipos") or {}
    if not isinstance(equipos, dict):
        raise ErrorValidacion("Los equipos deben ser un objeto {equipo: cantidad}")

    desconocidos = set(equipos) - EQUIPOS_NUCLEO
    if desconocidos:
        raise ErrorValidacion(
            "Equipos no reconocidos: " + ", ".join(sorted(desconocidos))
            + ". Válidos: " + ", ".join(sorted(EQUIPOS_NUCLEO)))

    for equipo, cantidad in equipos.items():
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad < 0:
            raise ErrorValidacion(
                f"La cantidad de '{equipo}' debe ser un entero de 0 o más")

    if not any(equipos.values()):
        raise ErrorValidacion(
            "El núcleo tiene que declarar al menos un artefacto: sin cantidades "
            "no se puede calcular su nivel de servicio")

    # Un núcleo de mingitorios en Damas es casi siempre un error de carga y
    # distorsionaría el % de artefactos en servicio de ese núcleo.
    if tipo == "DAMAS" and equipos.get("mingitorios"):
        raise ErrorValidacion(
            "Un núcleo de Damas no debería tener mingitorios. Revisá el tipo "
            "o la cantidad cargada.")
    return datos


def validar_luminarias(datos: dict) -> dict:
    _texto(datos.get("sector"), "sector")
    _entero_positivo(datos.get("cantidad"), "cantidad")
    return datos


def validar_puerta(datos: dict) -> dict:
    _texto(datos.get("nombre"), "nombre")
    _entero_positivo(datos.get("php"), "php")
    _entero_positivo(datos.get("instaladas"), "instaladas", minimo=0)
    return datos


def validar_elevacion(datos: dict) -> dict:
    _texto(datos.get("nombre"), "nombre")
    if "redundancia" in datos and not isinstance(datos["redundancia"], (bool, int)):
        raise ErrorValidacion("'redundancia' debe ser verdadero o falso")
    return datos


def validar_seccion(datos: dict) -> dict:
    _texto(datos.get("identificador"), "identificador")
    if datos.get("tipo") not in TIPOS_SECCION:
        raise ErrorValidacion(
            f"Tipo de sección inválido. Opciones: {', '.join(sorted(TIPOS_SECCION))}")
    return datos


VALIDADORES_INVENTARIO = {
    "nucleos": validar_nucleo,
    "luminarias": validar_luminarias,
    "puertas": validar_puerta,
    "elevacion": validar_elevacion,
    "secciones": validar_seccion,
}


def validar_inventario(recurso: str, datos: dict) -> dict:
    validador = VALIDADORES_INVENTARIO.get(recurso)
    return validador(datos) if validador else datos
