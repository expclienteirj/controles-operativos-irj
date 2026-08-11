"""
Capa de servicios: traduce entre la base de datos y el motor de cálculo.

calc.py no toca la base y no sabe de SQL; este módulo arma los payloads que
espera el motor a partir del inventario configurado y de los hallazgos que
cargó el auditor, y persiste el resultado.

Regla que atraviesa todo el módulo: si falta el inventario o falta la
confirmación del auditor, el resultado es None ("Sin datos") — nunca 100%.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, timedelta

import calc
import db


# ==========================================================================
# MÓDULO LIMPIEZA
# ==========================================================================

def _marcadores(n: int) -> str:
    """`?,?,?` para un IN de n elementos."""
    return ",".join("?" * n)


def estado_control(conn: sqlite3.Connection, control_id: int) -> dict:
    """Estado de un control (Qn): % por sector y % general.

    Los sectores sin confirmar quedan como Sin datos y no promedian.
    """
    return estados_controles(conn, [control_id])[control_id]


def estados_controles(conn: sqlite3.Connection,
                      control_ids: list[int]) -> dict[int, dict]:
    """`estado_control` para varios controles, con un número fijo de consultas.

    Existe porque el informe mensual y la certificación necesitan el estado de
    todos los controles cerrados del período. Resolviéndolos de a uno, cada
    control costaba tres consultas por sector más las de equipamiento: con
    nueve sectores y los dos turnos de un mes completo eran unas dos mil
    consultas por pantalla, y contra Postgres eso se siente como varios
    segundos de espera antes de la primera fila.

    Acá se traen los desvíos, las confirmaciones y los faltantes de todos los
    controles de una vez y se arman los mismos diccionarios en memoria: son
    seis consultas más dos por período involucrado, sin importar cuántos
    controles se pidan. El resultado por control es idéntico al que devolvía
    la versión anterior — los 571 tests siguen siendo la verificación.
    """
    ids = list(dict.fromkeys(control_ids))
    if not ids:
        return {}
    marcas = _marcadores(len(ids))
    args = tuple(ids)

    sectores = [dict(s) for s in conn.execute(
        "SELECT id, clave, nombre FROM sectores_limpieza "
        "WHERE activo = 1 ORDER BY orden")]

    items_por_sector: dict[int, list[str]] = {}
    for f in conn.execute(
            "SELECT sector_id, clave FROM items_limpieza WHERE activo = 1"):
        items_por_sector.setdefault(f["sector_id"], []).append(f["clave"])

    desvios: dict[tuple[int, int], dict[str, str]] = {}
    for f in conn.execute(
            "SELECT d.control_id, i.sector_id, i.clave, d.estado FROM desvios d "
            "JOIN items_limpieza i ON i.id = d.item_id "
            f"WHERE d.control_id IN ({marcas})", args):
        desvios.setdefault((f["control_id"], f["sector_id"]), {})[f["clave"]] = \
            f["estado"]

    confirmados = {
        (f["control_id"], f["sector_id"]) for f in conn.execute(
            "SELECT control_id, sector_id, confirmado FROM control_sectores "
            f"WHERE control_id IN ({marcas})", args)
        if f["confirmado"]}

    equipamiento = _equipamiento_controles(conn, ids)

    salida = {}
    for control_id in ids:
        filas = []
        for s in sectores:
            propios = desvios.get((control_id, s["id"]), {})
            confirmado = (control_id, s["id"]) in confirmados
            pct = calc.sector_limpieza(items_por_sector.get(s["id"], []),
                                       propios, confirmado)
            filas.append({
                "sector_id": s["id"], "clave": s["clave"], "nombre": s["nombre"],
                "porcentaje": pct, "confirmado": confirmado,
                "cantidad_desvios": len([e for e in propios.values()
                                         if e != calc.NO_VERIFICABLE]),
                "estado": _semaforo_sector(pct, confirmado, propios),
            })

        salida[control_id] = {
            "control_id": control_id, "sectores": filas,
            "equipamiento": equipamiento[control_id],
            "porcentaje_general": calc.estado_general_limpieza(
                [s["porcentaje"] for s in filas]),
            "sectores_pendientes": [s["clave"] for s in filas
                                    if not s["confirmado"]]}
    return salida


def _semaforo_sector(pct, confirmado, desvios) -> str:
    if not confirmado:
        return "PENDIENTE"          # gris
    return "CON_DESVIOS" if desvios else "SIN_NOVEDADES"


def equipos_exigidos(conn: sqlite3.Connection, periodo: str) -> list[dict]:
    """Equipos que rigen en el período.

    La lista base es la del pliego (equipamiento_limpieza). Si el admin
    confirmó el período, manda esa confirmación; si no, se usan los marcados
    como exigidos en la configuración.
    """
    confirmados = {f["equipamiento_id"]: bool(f["exigido"]) for f in conn.execute(
        "SELECT equipamiento_id, exigido FROM periodo_equipamiento WHERE periodo = ?",
        (periodo,))}

    equipos = []
    for e in conn.execute(
            "SELECT id, clave, nombre, exigido FROM equipamiento_limpieza "
            "ORDER BY orden, nombre"):
        rige = confirmados.get(e["id"], bool(e["exigido"]))
        equipos.append({"id": e["id"], "clave": e["clave"], "nombre": e["nombre"],
                        "exigido": rige})
    return equipos


def _equipamiento_control(conn: sqlite3.Connection, control_id: int,
                          periodo: str | None = None) -> dict:
    """Disponibilidad de equipos en un control diario."""
    return _equipamiento_controles(conn, [control_id], periodo)[control_id]


def _equipamiento_controles(conn: sqlite3.Connection, control_ids: list[int],
                            periodo: str | None = None) -> dict[int, dict]:
    """Lo mismo para varios controles: dos consultas más dos por período.

    `equipos_exigidos` depende solo del período, así que resolverla una vez por
    control repetía el mismo par de consultas treinta o sesenta veces para
    obtener siempre la misma lista.
    """
    ids = list(dict.fromkeys(control_ids))
    if not ids:
        return {}
    marcas = _marcadores(len(ids))
    args = tuple(ids)

    if periodo is None:
        periodos = {f["id"]: f["periodo"] for f in conn.execute(
            f"SELECT id, periodo FROM controles_limpieza WHERE id IN ({marcas})",
            args)}
        # Un control que no existe se evalúa contra el período en curso, que es
        # lo que hacía la versión de a uno.
        del_control = {cid: periodos.get(cid) or periodo_actual() for cid in ids}
    else:
        del_control = {cid: periodo for cid in ids}

    exigidos_por_periodo = {
        p: [e for e in equipos_exigidos(conn, p) if e["exigido"]]
        for p in sorted(set(del_control.values()))}

    faltantes: dict[int, list[dict]] = {}
    for f in conn.execute(
            "SELECT f.control_id, f.equipamiento_id, e.nombre, f.observacion "
            "FROM equipamiento_faltante f "
            "JOIN equipamiento_limpieza e ON e.id = f.equipamiento_id "
            f"WHERE f.control_id IN ({marcas})", args):
        faltantes.setdefault(f["control_id"], []).append(
            {"equipamiento_id": f["equipamiento_id"], "nombre": f["nombre"],
             "observacion": f["observacion"]})

    salida = {}
    for control_id in ids:
        exigidos = exigidos_por_periodo[del_control[control_id]]
        exigidos_ids = {e["id"] for e in exigidos}
        # Solo cuentan los faltantes de equipos que efectivamente se exigen.
        propios = [f for f in faltantes.get(control_id, [])
                   if f["equipamiento_id"] in exigidos_ids]
        salida[control_id] = {
            "exigidos": len(exigidos), "faltantes": len(propios),
            "detalle_faltantes": propios,
            "porcentaje": calc.cumplimiento_equipamiento(len(exigidos),
                                                         len(propios))}
    return salida


def _rango_medible(periodo: str, hoy: date | None = None) -> tuple[date, date]:
    """Tramo del mes sobre el que se mide la disponibilidad.

    Un mes en curso se mide hasta hoy: contar los días que todavía no llegaron
    como "disponibles" infla la disponibilidad del contratista a principios de
    mes y la corrige recién al cerrar.
    """
    hoy = hoy or date.today()
    anio, mes = int(periodo[:4]), int(periodo[5:7])
    primero = date(anio, mes, 1)
    ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1])
    return primero, min(ultimo, hoy) if hoy >= primero else primero


def equipamiento_mensual(conn: sqlite3.Connection, periodo: str,
                         hoy: date | None = None) -> dict:
    """Ítem 4 de la certificación: disponibilidad de equipos en el mes.

    Cada equipo aporta sus propios días de baja sobre los días transcurridos
    del período. Los días de baja salen de dos fuentes que se unifican:
    los tramos cargados en equipamiento_baja (lo que usa el auditor hoy) y las
    marcas día por día de equipamiento_faltante (el modelo anterior, que sigue
    valiendo para los períodos ya cargados así).
    """
    exigidos = [e for e in equipos_exigidos(conn, periodo) if e["exigido"]]
    if not exigidos:
        return {"exigidos": 0, "porcentaje": None, "dias_considerados": 0,
                "motivo": "No hay equipos exigidos configurados para el período",
                "por_equipo": []}

    desde, hasta = _rango_medible(periodo, hoy)
    dias_periodo = (hasta - desde).days + 1

    # Los días de baja valen aunque ese día no tenga control cerrado —para eso
    # se cargan por tramo—, pero un período sin ninguna auditoría y sin ninguna
    # baja no es "todo disponible": es que nadie miró. Sin evidencia, Sin datos.
    hay_auditoria = conn.execute(
        "SELECT 1 FROM controles_limpieza WHERE periodo = ? AND estado = 'CERRADO' "
        "LIMIT 1", (periodo,)).fetchone()
    hay_bajas = conn.execute(
        "SELECT 1 FROM equipamiento_baja WHERE desde <= ? AND (hasta IS NULL OR hasta >= ?) "
        "LIMIT 1", (hasta.isoformat(), desde.isoformat())).fetchone()
    if not hay_auditoria and not hay_bajas:
        return {"exigidos": len(exigidos), "porcentaje": None,
                "dias_considerados": 0, "por_equipo": [],
                "equipos_con_faltas": [],
                "motivo": "Sin controles cerrados ni bajas registradas en el período"}

    # Marcas diarias del modelo anterior, por equipo.
    marcas = {}
    for f in conn.execute(
            "SELECT f.equipamiento_id, c.fecha FROM equipamiento_faltante f "
            "JOIN controles_limpieza c ON c.id = f.control_id "
            "WHERE c.periodo = ?", (periodo,)):
        marcas.setdefault(f["equipamiento_id"], set()).add(f["fecha"])

    # Los tramos de baja de todos los equipos de una vez. Consultarlos por
    # equipo dentro del bucle era una consulta por máquina exigida, siempre
    # sobre la misma tabla. Se acotan al rango medible: un tramo que no lo toca
    # queda descartado igual por el recorte de abajo.
    tramos: dict[int, list[tuple[str, str | None]]] = {}
    for f in conn.execute(
            "SELECT equipamiento_id, desde, hasta FROM equipamiento_baja "
            "WHERE desde <= ? AND (hasta IS NULL OR hasta >= ?)",
            (hasta.isoformat(), desde.isoformat())):
        tramos.setdefault(f["equipamiento_id"], []).append((f["desde"], f["hasta"]))

    por_equipo = []
    for e in exigidos:
        # Unión de ambas fuentes: una baja marcada por los dos caminos cuenta
        # una sola vez.
        dias = set(marcas.get(e["id"], set()))
        for tramo_desde, tramo_hasta in tramos.get(e["id"], []):
            ini = max(date.fromisoformat(tramo_desde), desde)
            fin = min(date.fromisoformat(tramo_hasta) if tramo_hasta else hasta,
                      hasta)
            while ini <= fin:
                dias.add(ini.isoformat())
                ini += timedelta(days=1)
        dias = {d for d in dias if desde.isoformat() <= d <= hasta.isoformat()}
        por_equipo.append({"equipamiento_id": e["id"], "nombre": e["nombre"],
                           "dias_fuera_servicio": len(dias),
                           "disponibilidad": (None if not dias_periodo else
                                              (dias_periodo - len(dias)) / dias_periodo)})

    return {
        "exigidos": len(exigidos),
        "dias_considerados": dias_periodo,
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "porcentaje": calc.item_maquinarias(
            dias_periodo, [e["dias_fuera_servicio"] for e in por_equipo]),
        "por_equipo": por_equipo,
        "equipos_con_faltas": [e for e in por_equipo if e["dias_fuera_servicio"]],
        "motivo": None,
    }


def bajas_equipamiento(conn: sqlite3.Connection, periodo: str) -> list[dict]:
    """Bajas que tocan el período, con los equipos exigidos como referencia."""
    anio, mes = int(periodo[:4]), int(periodo[5:7])
    primero = date(anio, mes, 1).isoformat()
    ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1]).isoformat()

    return [dict(f) for f in conn.execute(
        "SELECT b.*, e.nombre equipo FROM equipamiento_baja b "
        "JOIN equipamiento_limpieza e ON e.id = b.equipamiento_id "
        "WHERE b.desde <= ? AND (b.hasta IS NULL OR b.hasta >= ?) "
        "ORDER BY b.desde DESC", (ultimo, primero))]


def registrar_baja_equipo(conn: sqlite3.Connection, equipamiento_id: int,
                          desde: str, hasta: str | None, motivo: str,
                          usuario_id: int, control_id: int | None = None) -> dict:
    """Da de baja una máquina por un tramo de días.

    `hasta` en None deja la baja abierta: la máquina sigue fuera de servicio y
    el cálculo la descuenta hasta el día en que se mide. Es el caso normal
    cuando el auditor todavía no sabe cuándo vuelve.
    """
    if not (motivo or "").strip():
        raise ValueError("El motivo de la baja es obligatorio")
    try:
        d = date.fromisoformat(desde)
        h = date.fromisoformat(hasta) if hasta else None
    except (TypeError, ValueError):
        raise ValueError("Fecha inválida: se espera AAAA-MM-DD")
    if h and h < d:
        raise ValueError("La fecha de reposición no puede ser anterior a la de baja")

    if not conn.execute("SELECT id FROM equipamiento_limpieza WHERE id = ?",
                        (equipamiento_id,)).fetchone():
        raise LookupError(f"No existe el equipo {equipamiento_id}")

    cur = conn.execute(
        "INSERT INTO equipamiento_baja (equipamiento_id, desde, hasta, motivo, "
        "control_id, registrado_por) VALUES (?,?,?,?,?,?)",
        (equipamiento_id, d.isoformat(), h.isoformat() if h else None,
         motivo.strip(), control_id, usuario_id))
    registrar_log(conn, usuario_id, "BAJA_EQUIPO", "equipamiento_baja", cur.lastrowid,
                  {"equipamiento_id": equipamiento_id, "desde": desde, "hasta": hasta})
    conn.commit()
    return {"baja_id": cur.lastrowid}


def editar_baja_equipo(conn: sqlite3.Connection, baja_id: int, usuario_id: int,
                       desde: str | None = None, hasta: str | None = None,
                       motivo: str | None = None,
                       reabrir: bool = False) -> dict:
    """Corrige una baja ya cargada, o la cierra al reponer la máquina.

    Los días de baja descuentan del pago, así que una fecha mal tipeada tiene
    que poder arreglarse sin borrar y volver a cargar. Todo cambio queda en
    auditoria_log con los valores anteriores.

    `reabrir` vacía la fecha de reposición: la máquina volvió a quedar fuera de
    servicio y sigue descontando.
    """
    fila = conn.execute(
        "SELECT desde, hasta, motivo FROM equipamiento_baja WHERE id = ?",
        (baja_id,)).fetchone()
    if not fila:
        raise LookupError(f"No existe la baja {baja_id}")

    def leer(valor, actual):
        if valor is None:
            return actual
        try:
            return date.fromisoformat(valor).isoformat()
        except (TypeError, ValueError):
            raise ValueError("Fecha inválida: se espera AAAA-MM-DD")

    nuevo_desde = leer(desde, fila["desde"])
    nuevo_hasta = None if reabrir else leer(hasta, fila["hasta"])
    nuevo_motivo = (motivo or fila["motivo"] or "").strip()

    if not nuevo_motivo:
        raise ValueError("El motivo de la baja es obligatorio")
    if nuevo_hasta and nuevo_hasta < nuevo_desde:
        raise ValueError("La fecha de reposición no puede ser anterior a la de baja")

    conn.execute(
        "UPDATE equipamiento_baja SET desde = ?, hasta = ?, motivo = ? WHERE id = ?",
        (nuevo_desde, nuevo_hasta, nuevo_motivo, baja_id))
    registrar_log(conn, usuario_id, "EDITAR_BAJA_EQUIPO", "equipamiento_baja", baja_id,
                  {"antes": {"desde": fila["desde"], "hasta": fila["hasta"],
                             "motivo": fila["motivo"]},
                   "ahora": {"desde": nuevo_desde, "hasta": nuevo_hasta,
                             "motivo": nuevo_motivo}})
    conn.commit()
    return {"ok": True}


def borrar_baja_equipo(conn: sqlite3.Connection, baja_id: int,
                       usuario_id: int) -> dict:
    """Elimina una baja mal cargada. Afecta el pago, así que queda en el log."""
    if not conn.execute("SELECT id FROM equipamiento_baja WHERE id = ?",
                        (baja_id,)).fetchone():
        raise LookupError(f"No existe la baja {baja_id}")
    conn.execute("DELETE FROM equipamiento_baja WHERE id = ?", (baja_id,))
    registrar_log(conn, usuario_id, "BORRAR_BAJA_EQUIPO", "equipamiento_baja", baja_id)
    conn.commit()
    return {"ok": True}


# Artefactos sanitarios que tienen medición de "en servicio" (3.1.a). El resto
# del equipamiento del núcleo (jaboneras, espejos, toalleros) solo se mide por
# limpieza, y eso ya sale del check-list.
ARTEFACTOS_CON_SERVICIO = ("inodoros", "mingitorios", "bachas")


def artefactos_baja(conn: sqlite3.Connection, periodo: str) -> list[dict]:
    """Clausuras de artefactos sanitarios que tocan el período."""
    anio, mes = int(periodo[:4]), int(periodo[5:7])
    primero = date(anio, mes, 1).isoformat()
    ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1]).isoformat()

    return [dict(f) for f in conn.execute(
        "SELECT a.*, n.nombre nucleo, n.tipo FROM artefacto_baja a "
        "JOIN nucleos_sanitarios n ON n.id = a.nucleo_id "
        "WHERE a.desde <= ? AND (a.hasta IS NULL OR a.hasta >= ?) "
        "ORDER BY a.desde DESC", (ultimo, primero))]


def clausuras_en_rango(conn: sqlite3.Connection, desde: str,
                       hasta: str) -> list[tuple]:
    """Clausuras que tocan el tramo, como (nucleo_id, equipo, cantidad, desde, hasta).

    Sirve para resolver un mes entero con una sola consulta: la evaluación de
    baños necesita el corte de cada día, y pedirlo día por día era una consulta
    por jornada del período.
    """
    return [(f["nucleo_id"], f["equipo"], f["cantidad"], f["desde"], f["hasta"])
            for f in conn.execute(
                "SELECT nucleo_id, equipo, cantidad, desde, hasta FROM artefacto_baja "
                "WHERE desde <= ? AND (hasta IS NULL OR hasta >= ?)", (hasta, desde))]


def fuera_servicio_el_dia(clausuras: list[tuple], fecha: str) -> dict:
    """Corte de `clausuras_en_rango` en una fecha. {nucleo_id: {equipo: cantidad}}."""
    fuera = {}
    for nucleo_id, equipo, cantidad, desde, hasta in clausuras:
        if desde <= fecha and (hasta is None or hasta >= fecha):
            n = fuera.setdefault(nucleo_id, {})
            n[equipo] = n.get(equipo, 0) + cantidad
    return fuera


def artefactos_fuera_servicio_en(conn: sqlite3.Connection, fecha: str) -> dict:
    """Cuántos artefactos hay clausurados en una fecha, por núcleo y equipo.

    Devuelve {nucleo_id: {equipo: cantidad}}. Las clausuras superpuestas del
    mismo equipo se suman: son artefactos distintos del mismo tipo.
    """
    return fuera_servicio_el_dia(clausuras_en_rango(conn, fecha, fecha), fecha)


def registrar_baja_artefacto(conn: sqlite3.Connection, nucleo_id: int, equipo: str,
                             cantidad: int, desde: str, hasta: str | None,
                             motivo: str, usuario_id: int,
                             control_id: int | None = None) -> dict:
    """Registra artefactos sanitarios clausurados por un tramo de días."""
    if equipo not in ARTEFACTOS_CON_SERVICIO:
        raise ValueError(
            f"Artefacto inválido: se espera uno de {', '.join(ARTEFACTOS_CON_SERVICIO)}")
    if not (motivo or "").strip():
        raise ValueError("El motivo de la clausura es obligatorio")
    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise ValueError("La cantidad debe ser un número")
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor a cero")

    fila = conn.execute(
        "SELECT instalados FROM nucleo_equipos WHERE nucleo_id = ? AND equipo = ?",
        (nucleo_id, equipo)).fetchone()
    if not fila:
        raise LookupError(f"El núcleo {nucleo_id} no tiene {equipo} cargados")
    if cantidad > fila["instalados"]:
        raise ValueError(
            f"No se pueden clausurar {cantidad}: hay {fila['instalados']} instalados")

    try:
        d = date.fromisoformat(desde)
        h = date.fromisoformat(hasta) if hasta else None
    except (TypeError, ValueError):
        raise ValueError("Fecha inválida: se espera AAAA-MM-DD")
    if h and h < d:
        raise ValueError("La reposición no puede ser anterior a la clausura")

    cur = conn.execute(
        "INSERT INTO artefacto_baja (nucleo_id, equipo, cantidad, desde, hasta, "
        "motivo, control_id, registrado_por) VALUES (?,?,?,?,?,?,?,?)",
        (nucleo_id, equipo, cantidad, d.isoformat(), h.isoformat() if h else None,
         motivo.strip(), control_id, usuario_id))
    registrar_log(conn, usuario_id, "BAJA_ARTEFACTO", "artefacto_baja", cur.lastrowid,
                  {"nucleo_id": nucleo_id, "equipo": equipo, "cantidad": cantidad,
                   "desde": desde, "hasta": hasta})
    conn.commit()
    return {"baja_id": cur.lastrowid}


def editar_baja_artefacto(conn: sqlite3.Connection, baja_id: int, usuario_id: int,
                          desde: str | None = None, hasta: str | None = None,
                          cantidad: int | None = None, motivo: str | None = None,
                          reabrir: bool = False) -> dict:
    """Corrige o cierra una clausura. Mismo criterio que en maquinarias."""
    fila = conn.execute(
        "SELECT nucleo_id, equipo, cantidad, desde, hasta, motivo "
        "FROM artefacto_baja WHERE id = ?", (baja_id,)).fetchone()
    if not fila:
        raise LookupError(f"No existe la clausura {baja_id}")

    def leer(valor, actual):
        if valor is None:
            return actual
        try:
            return date.fromisoformat(valor).isoformat()
        except (TypeError, ValueError):
            raise ValueError("Fecha inválida: se espera AAAA-MM-DD")

    nuevo_desde = leer(desde, fila["desde"])
    nuevo_hasta = None if reabrir else leer(hasta, fila["hasta"])
    nueva_cant = int(cantidad) if cantidad is not None else fila["cantidad"]
    nuevo_motivo = (motivo or fila["motivo"] or "").strip()

    if nueva_cant <= 0:
        raise ValueError("La cantidad debe ser mayor a cero")
    if not nuevo_motivo:
        raise ValueError("El motivo de la clausura es obligatorio")
    if nuevo_hasta and nuevo_hasta < nuevo_desde:
        raise ValueError("La reposición no puede ser anterior a la clausura")

    conn.execute(
        "UPDATE artefacto_baja SET desde = ?, hasta = ?, cantidad = ?, motivo = ? "
        "WHERE id = ?", (nuevo_desde, nuevo_hasta, nueva_cant, nuevo_motivo, baja_id))
    registrar_log(conn, usuario_id, "EDITAR_BAJA_ARTEFACTO", "artefacto_baja", baja_id,
                  {"antes": {"desde": fila["desde"], "hasta": fila["hasta"],
                             "cantidad": fila["cantidad"], "motivo": fila["motivo"]},
                   "ahora": {"desde": nuevo_desde, "hasta": nuevo_hasta,
                             "cantidad": nueva_cant, "motivo": nuevo_motivo}})
    conn.commit()
    return {"ok": True}


def borrar_baja_artefacto(conn: sqlite3.Connection, baja_id: int,
                          usuario_id: int) -> dict:
    """Elimina una clausura mal cargada. Queda en el log."""
    if not conn.execute("SELECT id FROM artefacto_baja WHERE id = ?",
                        (baja_id,)).fetchone():
        raise LookupError(f"No existe la clausura {baja_id}")
    conn.execute("DELETE FROM artefacto_baja WHERE id = ?", (baja_id,))
    registrar_log(conn, usuario_id, "BORRAR_BAJA_ARTEFACTO", "artefacto_baja", baja_id)
    conn.commit()
    return {"ok": True}


def confirmar_sector(conn: sqlite3.Connection, control_id: int, sector_id: int,
                     usuario_id: int) -> dict:
    """Declaración explícita del auditor. Queda logueada (trazabilidad, 0.6)."""
    _verificar_control_abierto(conn, control_id)
    conn.execute(
        "INSERT INTO control_sectores (control_id, sector_id, confirmado, "
        "confirmado_en, confirmado_por) VALUES (?,?,1,datetime('now'),?) "
        "ON CONFLICT (control_id, sector_id) DO UPDATE SET "
        "confirmado = 1, confirmado_en = datetime('now'), confirmado_por = ?",
        (control_id, sector_id, usuario_id, usuario_id))
    registrar_log(conn, usuario_id, "CONFIRMAR_SECTOR", "control_sectores", control_id,
                  {"sector_id": sector_id})
    conn.commit()
    return {"ok": True}


def desconfirmar_sector(conn: sqlite3.Connection, control_id: int, sector_id: int,
                        usuario_id: int) -> dict:
    """Revierte la confirmación de un sector (deshacer del atajo "TODO OK").

    El sector vuelve a "sin verificar", no a "verificado y mal": deshacer una
    declaración no es lo mismo que declarar un incumplimiento. Queda logueado
    para que el historial muestre que hubo una confirmación y una marcha atrás.
    """
    _verificar_control_abierto(conn, control_id)
    conn.execute(
        "UPDATE control_sectores SET confirmado = 0, confirmado_en = NULL, "
        "confirmado_por = NULL WHERE control_id = ? AND sector_id = ?",
        (control_id, sector_id))
    registrar_log(conn, usuario_id, "DESCONFIRMAR_SECTOR", "control_sectores",
                  control_id, {"sector_id": sector_id})
    conn.commit()
    return {"ok": True}


def registrar_desvio(conn: sqlite3.Connection, control_id: int, item_id: int,
                     estado: str, observacion: str, usuario_id: int) -> dict:
    """Única acción de carga del auditor. Genera la no conformidad asociada."""
    _verificar_control_abierto(conn, control_id)

    if not (observacion or "").strip():
        raise ValueError("La observación es obligatoria al registrar un desvío")
    if estado not in ("DESVIO_PARCIAL", "DESVIO_TOTAL", calc.NO_VERIFICABLE):
        raise ValueError(f"Estado inválido: {estado}")

    cur = conn.execute(
        "INSERT INTO desvios (control_id, item_id, estado, observacion, creado_por) "
        "VALUES (?,?,?,?,?) ON CONFLICT (control_id, item_id) DO UPDATE SET "
        "estado = excluded.estado, observacion = excluded.observacion",
        (control_id, item_id, estado, observacion.strip(), usuario_id))
    desvio_id = cur.lastrowid or conn.execute(
        "SELECT id FROM desvios WHERE control_id = ? AND item_id = ?",
        (control_id, item_id)).fetchone()["id"]

    # "No verificable hoy" no es un incumplimiento: se excluye del cálculo y
    # no genera no conformidad.
    if estado != calc.NO_VERIFICABLE:
        info = conn.execute(
            "SELECT s.nombre sector, i.nombre item, c.periodo, c.fecha "
            "FROM items_limpieza i "
            "JOIN sectores_limpieza s ON s.id = i.sector_id "
            "JOIN controles_limpieza c ON c.id = ? WHERE i.id = ?",
            (control_id, item_id)).fetchone()
        ya = conn.execute(
            "SELECT id FROM no_conformidades WHERE desvio_id = ?", (desvio_id,)).fetchone()
        if not ya:
            conn.execute(
                "INSERT INTO no_conformidades (periodo, fecha_origen, origen, sector, "
                "item, descripcion, prioridad, desvio_id) VALUES (?,?,'LIMPIEZA',?,?,?,?,?)",
                (info["periodo"], info["fecha"], info["sector"], info["item"],
                 observacion.strip(),
                 "INMEDIATA" if estado == "DESVIO_TOTAL" else "PROGRAMADA", desvio_id))

    conn.commit()
    return {"desvio_id": desvio_id}


def nc_pendientes_anteriores(conn: sqlite3.Connection, fecha: str,
                             limite: int = 50,
                             incluir_fecha: bool = False) -> list[dict]:
    """No conformidades abiertas relevadas hasta `fecha`, de cualquier mes.

    Por defecto excluye las del propio día: el hint de "pendiente de auditorías
    anteriores" no puede mostrarle al auditor el desvío que acaba de cargar él
    mismo hace un minuto.

    `incluir_fecha` las suma, que es lo que necesita el centro de novedades:
    ahí sí importa la no conformidad que cargó el turno mañana y el turno tarde
    todavía no vio. Deliberadamente no se filtra por período: un hallazgo del
    31 tiene que llegarle al auditor del 1.
    """
    comparador = "<=" if incluir_fecha else "<"
    filas = conn.execute(
        "SELECT id, periodo, fecha_origen, origen, sector, item, descripcion, "
        "       prioridad, creado_en "
        "  FROM no_conformidades "
        f" WHERE estado = 'ABIERTA' AND fecha_origen IS NOT NULL "
        f"   AND fecha_origen {comparador} ? "
        " ORDER BY prioridad = 'INMEDIATA' DESC, fecha_origen ASC LIMIT ?",
        (fecha, limite))

    hoy = date.fromisoformat(fecha)
    pendientes = []
    for f in filas:
        d = dict(f)
        d["dias_pendiente"] = (hoy - date.fromisoformat(d["fecha_origen"])).days
        pendientes.append(d)
    return pendientes


def resolver_nc(conn: sqlite3.Connection, nc_id: int, estado: str,
                usuario_id: int, resolucion: str | None = None) -> dict:
    """Cierra o reabre una NC dejando asentado quién y qué constató.

    Cerrarla NO modifica la certificación: la penalización se aplicó cuando se
    relevó el desvío. Lo que se mide acá es el tiempo de resolución.
    """
    if estado not in ("ABIERTA", "RESUELTA"):
        raise ValueError("Estado inválido: debe ser ABIERTA o RESUELTA")
    if estado == "RESUELTA" and not (resolucion or "").strip():
        raise ValueError("Indicá qué constataste para cerrar la no conformidad")

    if not conn.execute("SELECT id FROM no_conformidades WHERE id = ?",
                        (nc_id,)).fetchone():
        raise LookupError(f"No existe la no conformidad {nc_id}")

    if estado == "RESUELTA":
        conn.execute(
            "UPDATE no_conformidades SET estado = 'RESUELTA', "
            "resuelto_en = datetime('now'), resuelto_por = ?, resolucion = ? "
            "WHERE id = ?", (usuario_id, resolucion.strip(), nc_id))
    else:
        conn.execute(
            "UPDATE no_conformidades SET estado = 'ABIERTA', resuelto_en = NULL, "
            "resuelto_por = NULL, resolucion = NULL WHERE id = ?", (nc_id,))

    registrar_log(conn, usuario_id, f"NC_{estado}", "no_conformidades", nc_id,
                  {"resolucion": resolucion})
    conn.commit()
    return {"ok": True}


def cerrar_control(conn: sqlite3.Connection, control_id: int, usuario_id: int) -> dict:
    """No se puede cerrar sin confirmar todos los sectores activos (requisito 6)."""
    _verificar_control_abierto(conn, control_id)

    estado = estado_control(conn, control_id)
    if estado["sectores_pendientes"]:
        raise ValueError(
            "Faltan confirmar sectores: " + ", ".join(estado["sectores_pendientes"]))

    conn.execute(
        "UPDATE controles_limpieza SET estado = 'CERRADO', cerrado_en = datetime('now') "
        "WHERE id = ?", (control_id,))
    registrar_log(conn, usuario_id, "CERRAR_CONTROL", "controles_limpieza", control_id,
                  {"porcentaje": estado["porcentaje_general"]})
    conn.commit()
    return estado


def reabrir_control(conn: sqlite3.Connection, control_id: int, usuario_id: int,
                    motivo: str) -> dict:
    """Solo admin. El historial es inmutable salvo por esta vía, que queda logueada."""
    if not (motivo or "").strip():
        raise ValueError("Reabrir un control cerrado exige un motivo")
    # Reabrir saca al control del promedio mensual —que solo cuenta los
    # CERRADO—, así que mueve el ítem de calidad de servicio y con él el importe
    # a certificar. Sobre un id inexistente el UPDATE no hacía nada y la
    # operación quedaba igual registrada en el log: un cambio de importe
    # aparentemente autorizado que nunca sucedió.
    if not conn.execute("SELECT id FROM controles_limpieza WHERE id = ?",
                        (control_id,)).fetchone():
        raise LookupError(f"No existe el control {control_id}")
    conn.execute("UPDATE controles_limpieza SET estado = 'ABIERTO', cerrado_en = NULL "
                 "WHERE id = ?", (control_id,))
    registrar_log(conn, usuario_id, "REABRIR_CONTROL", "controles_limpieza", control_id,
                  {"motivo": motivo})
    conn.commit()
    return {"ok": True}


def _verificar_control_abierto(conn: sqlite3.Connection, control_id: int) -> None:
    fila = conn.execute(
        "SELECT estado FROM controles_limpieza WHERE id = ?", (control_id,)).fetchone()
    if not fila:
        raise LookupError(f"No existe el control {control_id}")
    if fila["estado"] == "CERRADO":
        raise PermissionError("El control está cerrado y no admite modificaciones")


def control_del_dia(conn: sqlite3.Connection, hoy: date | None = None) -> dict:
    """El control que corresponde a la fecha, su estado y el avance del mes.

    Es la pantalla de entrada del auditor: abre la app y ve el control de hoy.
    """
    hoy = hoy or date.today()
    fecha = hoy.isoformat()
    periodo = fecha[:7]

    filas = {f["turno"]: f for f in conn.execute(
        "SELECT c.*, u.nombre auditor FROM controles_limpieza c "
        "JOIN usuarios u ON u.id = c.auditor_id WHERE c.fecha = ?", (fecha,))}

    # Los dos turnos son exigibles, así que siempre se devuelven los dos: el
    # que no existe viaja en None y la pantalla ofrece iniciarlo. Los estados
    # se resuelven juntos: es la pantalla de entrada y la abre todo el mundo.
    estados = estados_controles(conn, [f["id"] for f in filas.values()])
    turnos = [{"turno": t,
               "control": _resumen_control(conn, filas.get(t), estados)}
              for t in calc.TURNOS]

    return {"fecha": fecha, "periodo": periodo,
            "turnos": turnos,
            "mes": completitud_periodo(conn, periodo, hoy)}


def _resumen_control(conn: sqlite3.Connection, fila,
                     estados: dict[int, dict] | None = None) -> dict | None:
    if not fila:
        return None
    estado = (estados or {}).get(fila["id"]) or estado_control(conn, fila["id"])
    return {**dict(fila),
            "porcentaje": estado["porcentaje_general"],
            "sectores_confirmados": len(
                [s for s in estado["sectores"] if s["confirmado"]]),
            "sectores_totales": len(estado["sectores"]),
            "sectores_pendientes": estado["sectores_pendientes"]}


def completitud_periodo(conn: sqlite3.Connection, periodo: str,
                        hoy: date | None = None) -> dict:
    """Estado del mes: qué días se auditaron, cuáles faltan y con qué cobertura.

    Distingue "todavía no le toca" de "quedó sin hacer": solo lo segundo es un
    incumplimiento del plan de auditoría. Sin esa distinción la app mostraría
    alarma todos los días del mes por los días que aún no llegaron.
    """
    hoy = hoy or date.today()

    filas = [dict(f) for f in conn.execute(
        "SELECT fecha, turno, id, estado FROM controles_limpieza WHERE periodo = ?",
        (periodo,))]

    # Un día puede tener los dos turnos en estados distintos: se lo considera
    # cerrado si cerró alguno, y abierto si le queda alguno sin cerrar.
    turnos_cerrados, turnos_abiertos = {}, {}
    for f in filas:
        destino = turnos_cerrados if f["estado"] == "CERRADO" else turnos_abiertos
        destino.setdefault(f["fecha"], []).append(f["turno"])

    cerrados = sorted(turnos_cerrados)
    abiertos = sorted(turnos_abiertos)

    cobertura_minima = db.get_config(conn, "cobertura_minima_mes",
                                     calc.COBERTURA_MINIMA_DEFAULT)
    # El día de hoy todavía está en curso: no es un faltante vencido.
    r = calc.completitud_mes(cerrados, periodo, hoy.isoformat(), cobertura_minima)
    r["dias_iniciados"] = sorted({f["fecha"] for f in filas})
    r["dias_abiertos"] = abiertos
    r["dias_cerrados"] = r.pop("dias_auditados")
    r["turnos_cerrados_por_dia"] = turnos_cerrados
    r["turnos_abiertos_por_dia"] = turnos_abiertos
    r["turnos"] = calc.completitud_turnos(turnos_cerrados, periodo, hoy.isoformat())
    return r


def resumen_mensual_limpieza(conn: sqlite3.Connection, periodo: str) -> dict:
    """Promedio mensual por sector sobre los días auditados (informe 5.1).

    Con periodicidad diaria el mes tiene ~30 controles, así que el informe
    presenta el promedio por sector y deja el detalle día por día como serie
    aparte (`dias`), en lugar de una columna por control.
    """
    # La clave lleva el turno: con dos recorridas por día, indexar solo por
    # fecha hacía que la segunda pisara a la primera y el mes se calculara
    # sobre la mitad de los datos.
    controles = {f"{f['fecha']}·{f['turno']}": f["id"] for f in conn.execute(
        "SELECT id, fecha, turno FROM controles_limpieza "
        "WHERE periodo = ? AND estado = 'CERRADO' ORDER BY fecha, turno", (periodo,))}

    # De a uno esto eran ~32 consultas por control; en lote son las mismas seis
    # sin importar si el mes tiene tres recorridas o sesenta y dos.
    por_id = estados_controles(conn, list(controles.values()))
    estados = {clave: por_id[cid] for clave, cid in controles.items()}

    filas = []
    for s in conn.execute("SELECT clave, nombre FROM sectores_limpieza "
                          "WHERE activo = 1 ORDER BY orden"):
        por_dia = {}
        for clave, est in estados.items():
            por_dia[clave] = next((x["porcentaje"] for x in est["sectores"]
                                   if x["clave"] == s["clave"]), None)
        filas.append({"clave": s["clave"], "nombre": s["nombre"],
                      "dias": por_dia,
                      "dias_con_datos": len([v for v in por_dia.values()
                                             if v is not None]),
                      "mensual": calc.sector_mensual(list(por_dia.values()))})

    general = calc.estado_general_limpieza([f["mensual"] for f in filas])
    completitud = completitud_periodo(conn, periodo)

    # Serie por recorrida (fecha·turno) para la tendencia del informe.
    serie = {clave: est["porcentaje_general"] for clave, est in estados.items()}

    return {"periodo": periodo, "sectores": filas, "porcentaje_general": general,
            "serie_diaria": serie,
            "completitud": completitud,
            # Sobre cuántas recorridas se calculó el promedio. El informe tiene
            # que decirlo: no es lo mismo un 95% sobre 60 turnos que sobre 3.
            "turnos_considerados": len(controles),
            "dias_considerados": len({c.split("·")[0] for c in controles}),
            "dias_del_mes": completitud["dias_esperados"],
            "equipamiento": {f: estados[f]["equipamiento"] for f in sorted(estados)}}


# ==========================================================================
# CERTIFICACIÓN MENSUAL (2.3)
# ==========================================================================

def certificacion(conn: sqlite3.Connection, periodo: str,
                  resumen: dict | None = None) -> dict:
    """Certificación del período.

    `resumen`: el `resumen_mensual_limpieza` del mismo período, si el llamador
    ya lo tiene. El informe mensual necesita los dos y calcularlo por dentro lo
    hacía dos veces —el trabajo más caro de la app, repetido entero—.
    """
    datos = conn.execute(
        "SELECT * FROM periodo_datos WHERE periodo = ?", (periodo,)).fetchone()
    datos = dict(datos) if datos else {}

    if resumen is None:
        resumen = resumen_mensual_limpieza(conn, periodo)
    # Se cuentan TODAS las NC del período, abiertas y resueltas: la NC penaliza
    # en el momento en que se releva y resolverla no devuelve el punto perdido.
    # Contar solo las abiertas dejaba que el contratista recuperara certificación
    # cerrando no conformidades al final del mes.
    nc_periodo = conn.execute(
        "SELECT COUNT(*) c FROM no_conformidades WHERE periodo = ?",
        (periodo,)).fetchone()["c"]
    nc_abiertas = conn.execute(
        "SELECT COUNT(*) c FROM no_conformidades WHERE periodo = ? AND estado = 'ABIERTA'",
        (periodo,)).fetchone()["c"]

    insumos = [dict(f) for f in conn.execute(
        "SELECT i.nombre, i.punto_pedido, s.stock FROM insumos i "
        "LEFT JOIN insumo_stock s ON s.insumo_id = i.id AND s.periodo = ? "
        "WHERE i.activo = 1", (periodo,))]
    insumos_relevados = [i for i in insumos if i["stock"] is not None]

    # Ítems binarios: solo valen 100% si el admin declaró haberlos verificado.
    # Sin esa declaración son Sin datos, no un aprobado automático.
    penalizacion = db.get_config(conn, "penalizacion_por_nc",
                                 calc.PENALIZACION_NC_DEFAULT)
    tope = db.get_config(conn, "penalizacion_nc_tope",
                         calc.PENALIZACION_NC_TOPE_DEFAULT)
    equipamiento = equipamiento_mensual(conn, periodo)

    items = {
        "documentacion": (calc.item_binario(datos.get("hallazgos_documentacion", 0))
                          if datos.get("documentacion_verificada") else None),
        "ley_19587": (calc.item_binario(datos.get("hallazgos_ley_19587", 0))
                      if datos.get("ley_19587_verificada") else None),
        "programacion_trabajos": calc.item_programacion(
            datos.get("horas_hombre_programadas"),
            datos.get("horas_hombre_perdidas", 0)),
        # Se alimenta del equipamiento relevado en los controles diarios, igual
        # que calidad de servicio se alimenta del check-list.
        "maquinarias": equipamiento["porcentaje"],
        "insumos": calc.item_insumos(insumos_relevados),
        "calidad_servicio": calc.item_calidad_servicio(
            resumen["porcentaje_general"], nc_periodo, penalizacion, tope),
    }

    pesos = db.get_config(conn, "pesos", calc.PESOS_CERTIFICACION_DEFAULT)
    resultado = calc.certificacion_mensual(items, pesos)
    resultado["periodo"] = periodo
    resultado["no_conformidades_periodo"] = nc_periodo
    resultado["no_conformidades_abiertas"] = nc_abiertas
    resultado["calidad_base"] = resumen["porcentaje_general"]
    resultado["equipamiento"] = equipamiento
    resultado["importe"] = calc.importe_a_certificar(
        resultado["porcentaje"], datos.get("monto_adjudicado"))
    resultado["monto_adjudicado"] = datos.get("monto_adjudicado")
    resultado["penalizacion_nc"] = {
        "por_nc": penalizacion, "tope": tope,
        "descuento_aplicado": min(nc_abiertas * penalizacion, tope),
    }
    resultado["completitud"] = resumen["completitud"]
    resultado["advertencias"] = _advertencias_certificacion(
        conn, nc_abiertas, penalizacion, resultado, resumen)
    return resultado


def _advertencias_certificacion(conn, nc_abiertas, penalizacion, resultado,
                                resumen) -> list[dict]:
    """Advertencias que la UI y el PDF deben mostrar junto al porcentaje.

    La más importante: el criterio de penalización por no conformidad no surge
    del pliego. Mientras no se confirme, cualquier importe calculado con NC
    abiertas se apoya en un supuesto de implementación.
    """
    avisos = []

    # La cobertura va primero: es lo que más puede distorsionar el importe.
    comp = resumen["completitud"]
    hechos, esperados = len(comp["dias_cerrados"]), comp["dias_esperados"]
    vencidos = comp["dias_vencidos_sin_control"]

    if not comp["cobertura_suficiente"]:
        avisos.append({
            "codigo": "COBERTURA_INSUFICIENTE",
            "nivel": "ADVERTENCIA",
            "mensaje": (
                f"Se auditaron {hechos} de {esperados} días del mes "
                f"(cobertura {comp['cobertura']:.0%}, mínimo esperado "
                f"{comp['cobertura_minima']:.0%}). El porcentaje se calculó solo "
                "sobre los días auditados: con pocos días el resultado depende "
                "mucho de cuáles se relevaron y es poco representativo del mes."),
            "dias_auditados": hechos, "dias_esperados": esperados,
            "cobertura": comp["cobertura"],
        })
    elif not comp["completo"]:
        avisos.append({
            "codigo": "MES_INCOMPLETO",
            "nivel": "INFO",
            "mensaje": (
                f"Se auditaron {hechos} de {esperados} días del mes "
                f"(cobertura {comp['cobertura']:.0%}). "
                + (f"{len(vencidos)} día(s) quedaron sin control. "
                   if vencidos else "")
                + "Los días sin auditar no computan ni penalizan al contratista."),
            "dias_auditados": hechos, "dias_esperados": esperados,
        })

    if comp["dias_abiertos"]:
        avisos.append({
            "codigo": "CONTROLES_ABIERTOS",
            "nivel": "ADVERTENCIA",
            "mensaje": (
                f"Hay {len(comp['dias_abiertos'])} control(es) iniciados y sin "
                "cerrar: " + ", ".join(comp["dias_abiertos"][:5])
                + ("…" if len(comp["dias_abiertos"]) > 5 else "")
                + ". Un control sin cerrar no entra en el cálculo. "
                  "Conviene cerrarlos antes de certificar."),
        })

    confirmada = db.get_config(conn, "penalizacion_nc_confirmada", False)
    if not confirmada and penalizacion:
        aviso = {
            "codigo": "PENALIZACION_NC_NO_CONFIRMADA",
            "nivel": "ADVERTENCIA" if nc_abiertas else "INFO",
            "mensaje": (
                f"El descuento por no conformidad ({penalizacion:.1%} por NC, tope "
                f"{db.get_config(conn, 'penalizacion_nc_tope', 0):.0%}) es un valor "
                "provisorio que NO surge del pliego: el PET no fija la fórmula. "
                "Acordar el criterio con el contratista y confirmarlo en "
                "Configuración → Certificación."),
        }
        if nc_abiertas:
            aviso["mensaje"] += (
                f" Este período tiene {nc_abiertas} NC abierta(s), por lo que el "
                "porcentaje a certificar depende de ese valor.")
        avisos.append(aviso)

    # Requisito contractual: ítems obligatorios bloquean la certificación.
    if resultado.get("items_obligatorios_faltantes"):
        nombres_items = {
            "documentacion": "Documentación obligatoria",
            "ley_19587": "Ley 19587 (seguridad e higiene)",
            "programacion_trabajos": "Programación de trabajos",
        }
        items_faltantes = resultado["items_obligatorios_faltantes"]
        avisos.append({
            "codigo": "ITEMS_OBLIGATORIOS_FALTANTES",
            "nivel": "BLOQUEANTE",
            "mensaje": (
                "No se puede completar la certificación. Faltan datos obligatorios: "
                + ", ".join(nombres_items.get(k, k) for k in items_faltantes) + ". "
                "Estos ítems son requisitos duros del contrato y su carga es "
                "indispensable para calcular el porcentaje de certificación."),
        })
    elif resultado.get("items_sin_datos"):
        avisos.append({
            "codigo": "ITEMS_SIN_DATOS",
            "nivel": "ADVERTENCIA",
            "mensaje": (
                "Ítems sin datos cargados: "
                + ", ".join(resultado["items_sin_datos"])
                + f". Su peso se redistribuyó entre los evaluados "
                  f"({resultado['peso_evaluado']:.0%} del total)."),
        })

    return avisos


# ==========================================================================
# MÓDULO LoS
# ==========================================================================

def _objetivo_limpieza_bano(conn, tipo_nucleo: str, equipo: str) -> float:
    """Objetivo del equipo según el tipo de núcleo (3.1.b).

    PMR y recinto de bebés exigen 100% en todo. En los núcleos comunes,
    espejos, pisos y cambiador exigen 100% aunque el resto sea 80%.
    """
    objetivos = db.get_config(conn, "banos_limpieza_objetivos", {})
    if tipo_nucleo in ("PMR", "RECINTO_BEBES"):
        return 1.00
    for sector in ("BACHAS", "BOXES", "MINGITORIOS"):
        if equipo in objetivos.get(sector, {}):
            return objetivos[sector][equipo]
    return 0.80


def rendimiento_items_checklist(conn: sqlite3.Connection, periodo: str,
                                slugs: list[str],
                                sectores: list[str] | None = None) -> dict:
    """Promedio mensual de un conjunto de ítems del check-list diario.

    Es el puente entre el relevamiento diario y el LoS: en vez de relevar dos
    veces lo mismo (una en el check-list y otra en LoS), el nivel de servicio
    se calcula del trabajo que el auditor ya hizo todos los días.

    Devuelve None si esos ítems no se relevaron en ningún día cerrado del mes:
    sin datos no se inventa un cumplimiento.
    """
    if not slugs:
        return {"porcentaje": None, "dias": 0, "motivo": "Sin ítems vinculados"}

    marcadores = ",".join("?" * len(slugs))
    sql = (
        "SELECT c.fecha, i.clave, d.estado FROM controles_limpieza c "
        "JOIN control_sectores cs ON cs.control_id = c.id AND cs.confirmado = 1 "
        "JOIN items_limpieza i ON i.sector_id = cs.sector_id AND i.activo = 1 "
        "JOIN sectores_limpieza s ON s.id = i.sector_id "
        "LEFT JOIN desvios d ON d.control_id = c.id AND d.item_id = i.id "
        f"WHERE c.periodo = ? AND c.estado = 'CERRADO' AND i.clave IN ({marcadores})")
    args = [periodo, *slugs]
    if sectores:
        sql += " AND s.clave IN (" + ",".join("?" * len(sectores)) + ")"
        args += sectores

    # Por excepción: un ítem confirmado sin desvío vale 1.0. Se agrupa por día
    # para que el promedio mensual sea de días, no de filas.
    por_dia = {}
    for f in conn.execute(sql, args):
        estado = f["estado"] or "CUMPLE"
        if estado == calc.NO_VERIFICABLE:
            continue
        por_dia.setdefault(f["fecha"], []).append(calc.VALOR_ESTADO[estado])

    if not por_dia:
        return {"porcentaje": None, "dias": 0,
                "motivo": "Estos ítems no se relevaron en el período"}

    diarios = [sum(v) / len(v) for v in por_dia.values()]
    return {"porcentaje": calc.promedio(diarios), "dias": len(diarios),
            "motivo": None}


def evaluar_banos_desde_checklist(conn: sqlite3.Connection, periodo: str) -> dict:
    """3.1.b — Limpieza de baños calculada del check-list diario.

    Cada equipo del LoS (bachas, jabonera, espejos…) toma el promedio mensual
    de los ítems del check-list que lo miden y se compara contra su objetivo.
    Los equipos con objetivo 100% son estrictos a propósito: un solo día con
    desvío parcial ya los hace incumplir, que es el nivel de servicio contratado.
    """
    enlaces = db.get_config(conn, "banos_link_checklist", {})
    sectores = db.get_config(conn, "banos_sectores_checklist", [])
    objetivos = db.get_config(conn, "banos_limpieza_objetivos", {})

    # Objetivo por equipo. Se usan los sectores comunes (bachas, boxes,
    # mingitorios) y no PMR ni recinto de bebés: el check-list releva los baños
    # del aeropuerto sin distinguir el núcleo, así que aplicar el 100% de PMR a
    # todos exigiría a los baños comunes un estándar que no es el suyo. Los
    # equipos que el manual pone en 100% dentro del sector común (espejos,
    # pisos, cambiador) sí mantienen esa exigencia.
    exigencia = {}
    for sector in ("BACHAS", "BOXES", "MINGITORIOS"):
        for equipo, obj in (objetivos.get(sector) or {}).items():
            exigencia[equipo] = max(exigencia.get(equipo, 0), obj)

    detalle, todos_ok, evaluados = {}, True, 0
    for equipo, slugs in enlaces.items():
        objetivo = exigencia.get(equipo, 0.80)
        r = rendimiento_items_checklist(conn, periodo, slugs, sectores)
        cumple = (None if r["porcentaje"] is None
                  else calc.cumple(r["porcentaje"], objetivo))
        if cumple is False:
            todos_ok = False
        if cumple is not None:
            evaluados += 1
        detalle[equipo] = {"porcentaje": r["porcentaje"], "objetivo": objetivo,
                           "dias": r["dias"], "cumple": cumple,
                           "motivo": r.get("motivo")}

    return {"origen": "checklist", "equipos": detalle,
            "equipos_evaluados": evaluados,
            "cumple": todos_ok if evaluados else None,
            "motivo": None if evaluados else
                      "Los ítems de baños no se relevaron en el período"}


def evaluar_banos(conn: sqlite3.Connection, datos: dict, periodo=None,
                  hoy: date | None = None) -> dict:
    """3.1 — Artefactos en servicio (a) + limpieza (b). Todo derivado.

    Ninguna de las dos mitades se carga desde LoS:

      * la limpieza sale del check-list diario, porque es el mismo objeto que
        el auditor releva todos los días;
      * los artefactos clausurados salen de artefacto_baja, que se carga desde
        el control diario — el auditor los ve al entrar al baño, y el
        check-list no puede deducirlos: un inodoro fuera de servicio se ve
        igual de limpio que uno en uso.

    El mes cumple solo si cumple TODOS los días medidos: el nivel de servicio
    es una exigencia permanente, no un promedio que un buen día compense.
    `datos` se ignora y se mantiene por compatibilidad de firma con el resto
    de los evaluadores.
    """
    nucleos = [dict(f) for f in conn.execute(
        "SELECT id, nombre, tipo FROM nucleos_sanitarios WHERE activo = 1")]
    if not nucleos:
        return {"cumple": None, "motivo": "Requiere configuración: núcleos sanitarios"}

    periodo = periodo or periodo_actual()
    instalados_por_nucleo: dict[int, dict] = {n["id"]: {} for n in nucleos}
    for f in conn.execute(
            "SELECT nucleo_id, equipo, instalados FROM nucleo_equipos"):
        if f["nucleo_id"] in instalados_por_nucleo:
            instalados_por_nucleo[f["nucleo_id"]][f["equipo"]] = f["instalados"]

    desde, hasta = _rango_medible(periodo, hoy)

    # Sin ninguna auditoría y sin ninguna clausura no hay evidencia de que
    # alguien haya mirado los baños: Sin datos, nunca "todo en servicio".
    hay_auditoria = conn.execute(
        "SELECT 1 FROM controles_limpieza WHERE periodo = ? AND estado = 'CERRADO' "
        "LIMIT 1", (periodo,)).fetchone()
    hay_bajas = conn.execute(
        "SELECT 1 FROM artefacto_baja WHERE desde <= ? AND (hasta IS NULL OR hasta >= ?) "
        "LIMIT 1", (hasta.isoformat(), desde.isoformat())).fetchone()

    servicio = {"cumple": None, "dias_incumplen": [], "detalle_peor": [],
                "motivo": "Sin controles cerrados ni clausuras en el período"}

    if hay_auditoria or hay_bajas:
        # Las clausuras del mes entero, una sola vez: el corte de cada día se
        # saca de acá. Antes era una consulta por jornada del período.
        clausuras = clausuras_en_rango(conn, desde.isoformat(), hasta.isoformat())
        dias_incumplen, peor = [], []
        dia = desde
        while dia <= hasta:
            fuera = fuera_servicio_el_dia(clausuras, dia.isoformat())
            detalle_dia, dia_ok = [], True
            for n in nucleos:
                inst = instalados_por_nucleo[n["id"]]
                fn = fuera.get(n["id"], {})
                r = calc.banos_en_servicio({
                    "nombre": n["nombre"], "tipo": n["tipo"],
                    "artefactos": {e: {"instalados": inst[e],
                                       "fuera_servicio": fn.get(e, 0)}
                                   for e in ARTEFACTOS_CON_SERVICIO if inst.get(e)}})
                detalle_dia.append({"nucleo": n["nombre"], "tipo": n["tipo"],
                                    "en_servicio": r})
                if r["cumple"] is False:
                    dia_ok = False
            if not dia_ok:
                dias_incumplen.append(dia.isoformat())
                if not peor:
                    peor = detalle_dia
            dia += timedelta(days=1)

        servicio = {"cumple": not dias_incumplen,
                    "dias_incumplen": dias_incumplen,
                    "detalle_peor": peor, "motivo": None,
                    "desde": desde.isoformat(), "hasta": hasta.isoformat()}

    limpieza = evaluar_banos_desde_checklist(conn, periodo)

    partes = [servicio["cumple"], limpieza["cumple"]]
    evaluadas = [p for p in partes if p is not None]
    return {"en_servicio": servicio, "limpieza": limpieza,
            "cumple": all(evaluadas) if evaluadas else None}


def evaluar_iluminacion(conn: sqlite3.Connection, datos: dict) -> dict:
    """3.3 — Por sector, con la regla de luminarias consecutivas."""
    sectores = [dict(f) for f in conn.execute(
        "SELECT sector, cantidad FROM luminarias_sector")]
    if not sectores:
        return {"cumple": None, "motivo": "Requiere configuración: luminarias por sector"}

    objetivo = db.get_config(conn, "iluminacion_objetivo", 0.90)
    quemadas = datos.get("quemadas", {})
    consecutivas = datos.get("consecutivas_mismo_cono", {})

    detalle = {}
    for s in sectores:
        detalle[s["sector"]] = calc.iluminacion_sector(
            s["cantidad"], quemadas.get(s["sector"], 0),
            bool(consecutivas.get(s["sector"], False)), objetivo)

    evaluados = [d for d in detalle.values() if d["cumple"] is not None]
    return {"sectores": detalle,
            "cumple": all(d["cumple"] for d in evaluados) if evaluados else None}


def evaluar_asientos(conn: sqlite3.Connection, datos: dict) -> dict:
    fila = conn.execute(
        "SELECT instalados FROM asientos_preembarque WHERE id = 1").fetchone()
    instalados = fila["instalados"] if fila else 0
    if not instalados:
        return {"cumple": None, "motivo": "Requiere configuración: asientos instalados"}
    return calc.asientos_preembarque(
        instalados, datos.get("inutilizables", 0),
        db.get_config(conn, "asientos_minimo", calc.ASIENTOS_MINIMOS_IRJ))


def evaluar_puntos_carga(conn: sqlite3.Connection, datos: dict) -> dict:
    puertas = [dict(f) for f in conn.execute(
        "SELECT id, nombre, php, instaladas FROM puertas_embarque")]
    if not puertas:
        return {"cumple": None, "motivo": "Requiere configuración: puertas de embarque"}

    tomas_por_100 = db.get_config(conn, "tomas_por_100_pax", calc.TOMAS_POR_100_PAX)
    fuera = datos.get("fuera_servicio", {})
    detalle = [calc.puntos_de_carga({**p, "fuera_servicio": fuera.get(str(p["id"]), 0)},
                                    tomas_por_100) for p in puertas]
    evaluados = [d for d in detalle if d["cumple"] is not None]
    return {"puertas": detalle,
            "cumple": all(d["cumple"] for d in evaluados) if evaluados else None}


def evaluar_elevacion(conn: sqlite3.Connection, periodo: str) -> dict:
    """3.7 — Acumula los eventos del mes registrados en la base."""
    equipos = [dict(f) for f in conn.execute(
        "SELECT id, nombre, redundancia FROM medios_elevacion WHERE activo = 1")]
    if not equipos:
        return {"cumple": None, "motivo": "Requiere configuración: medios de elevación"}

    anio, mes = (int(x) for x in periodo.split("-"))
    dias = calendar.monthrange(anio, mes)[1]
    horas_dia = db.get_config(conn, "elevacion_horas_dia", calc.HORAS_DIA_ELEVACION)
    umbrales = db.get_config(conn, "elevacion_umbrales", calc.ELEVACION_IRJ)

    detalle = []
    for e in equipos:
        eventos = [{"horas": f["horas"] or 0} for f in conn.execute(
            "SELECT horas FROM elevacion_eventos WHERE equipo_id = ? AND periodo = ?",
            (e["id"], periodo))]
        detalle.append(calc.medio_elevacion(
            {"nombre": e["nombre"], "redundancia": bool(e["redundancia"]),
             "eventos": eventos}, dias, horas_dia, umbrales))

    return {"equipos": detalle, "dias_del_mes": dias,
            "cumple": all(d["cumple"] for d in detalle)}


def evaluar_pista(conn: sqlite3.Connection, datos: dict) -> dict:
    secciones = [dict(f) for f in conn.execute(
        "SELECT id, identificador, tipo FROM secciones_pavimento WHERE activo = 1")]
    if not secciones:
        return {"cumple": None, "motivo": "Requiere configuración: secciones de pavimento"}

    pci = datos.get("pci", {})
    def con_pci(tipo):
        return [{"id": s["identificador"], "pci": pci.get(str(s["id"]))}
                for s in secciones if s["tipo"] == tipo and pci.get(str(s["id"])) is not None]

    r_pista = calc.pci_secciones(con_pci("PISTA"), "PISTA")
    r_rodaje = calc.pci_secciones(con_pci("RODAJE"), "RODAJE")
    disp = calc.disponibilidad_pista(datos.get("indisponibilidades_no_programadas", 0))

    partes = [r_pista["cumple"], r_rodaje["cumple"], disp["cumple"]]
    evaluadas = [p for p in partes if p is not None]
    return {"disponibilidad": disp, "pista": r_pista, "rodaje": r_rodaje,
            "cumple": all(evaluadas) if evaluadas else None}


def evaluar_confort(conn: sqlite3.Connection, datos: dict) -> dict:
    """3.2 — Medición obligatoria (excepción a la lógica por excepción).

    Solo temperatura: la velocidad de aire se quitó porque no se mide en la
    terminal y pedir un dato que nadie releva produce ceros falsos.
    """
    mediciones = datos.get("mediciones", [])
    if not mediciones:
        return {"cumple": None, "motivo": "Sin mediciones cargadas"}

    params = db.get_config(conn, "confort_termico", calc.CONFORT_IRJ)
    estacion = datos.get("estacion") or estacion_actual(conn)
    detalle = [{"zona": m.get("zona"),
                **calc.confort_termico(m.get("temperatura"), estacion, None, params)}
               for m in mediciones]
    evaluadas = [d for d in detalle if d["cumple"] is not None]
    return {"estacion": estacion, "zonas": detalle,
            # Las zonas fuera de rango exigen foto: son el hallazgo del ítem.
            "zonas_fuera_de_rango": [d["zona"] for d in detalle
                                     if d["cumple"] is False],
            "cumple": all(d["cumple"] for d in evaluadas) if evaluadas else None}


def evaluar_gel(conn: sqlite3.Connection, datos: dict) -> dict:
    """3.9 — Tiempo de conmutación del grupo electrógeno (RAAC 154).

    Se mide el tiempo que tarda el GEL en tomar la carga, sin asociarlo a una
    ayuda luminosa: el que conmuta es el grupo, no cada baliza.
    """
    pruebas = datos.get("pruebas", [])
    if not pruebas:
        return {"cumple": None, "motivo": "Sin pruebas de conmutación cargadas"}

    tabla = db.get_config(conn, "gel_tiempos_conmutacion", calc.TIEMPOS_CONMUTACION)
    default = db.get_config(conn, "gel_categoria_irj", "APROX_NO_PRECISION")
    detalle = [{"fecha": p.get("fecha"),
                **calc.prueba_gel(p.get("tiempo_s"), p.get("categoria", default), tabla)}
               for p in pruebas]
    evaluadas = [d for d in detalle if d["cumple"] is not None]
    return {"pruebas": detalle,
            "cumple": all(d["cumple"] for d in evaluadas) if evaluadas else None}


def evaluar_infraestructura(conn: sqlite3.Connection, datos: dict) -> dict:
    return calc.infraestructura(datos.get("subitems", {}))


def limpieza_terminal_desde_checklist(conn: sqlite3.Connection,
                                      periodo: str | None = None) -> dict:
    """3.8 — Grado A/B/C/D de cada sub-ítem derivado del check-list diario.

    Se expone aparte del evaluador porque el formulario necesita mostrarle al
    auditor qué valor trae el check-list antes de que decida pisarlo: si no lo
    ve, no puede saber qué está anulando.

    Devuelve {clave: {"grado", "porcentaje", "dias"}} solo para los sub-ítems
    con datos en el período.
    """
    periodo = periodo or periodo_actual()
    enlaces = db.get_config(conn, "limpieza_terminal_link_checklist", {})

    # El check-list da un porcentaje; el LoS trabaja en grados A/B/C/D. Un
    # sub-ítem sin desvíos es A; con desvíos, el grado sigue la severidad.
    def grado(pct):
        if pct is None:
            return None
        if pct >= 0.995:
            return "A"
        if pct >= 0.90:
            return "B"
        if pct >= 0.75:
            return "C"
        return "D"

    derivado = {}
    for clave, slugs in enlaces.items():
        r = rendimiento_items_checklist(conn, periodo, slugs)
        g = grado(r["porcentaje"])
        if g:
            derivado[clave] = {"grado": g, "porcentaje": r["porcentaje"],
                               "dias": r["dias"]}
    return derivado


def evaluar_limpieza_terminal(conn: sqlite3.Connection, datos: dict,
                              periodo=None) -> dict:
    """3.8 — Limpieza de terminal, alimentada por el check-list diario.

    Los sub-ítems que tienen equivalente en el check-list (cestos, vidrios,
    corredores, techos, contenedores) se calculan de ahí. El formulario solo
    envía los sub-ítems que el auditor decidió pisar a mano; un valor manual
    presente es siempre una anulación deliberada y por eso tiene prioridad.
    """
    periodo = periodo or periodo_actual()

    subitems, origen = {}, {}
    for clave, d in limpieza_terminal_desde_checklist(conn, periodo).items():
        subitems[clave] = d["grado"]
        origen[clave] = {"origen": "checklist", "porcentaje": d["porcentaje"],
                         "dias": d["dias"]}

    # Lo cargado a mano pisa al check-list: es una observación deliberada.
    for clave, llenado in (datos.get("llenado") or {}).items():
        subitems[clave] = calc.grado_por_llenado(llenado)
        origen[clave] = {"origen": "manual", "llenado": llenado}
    for clave, g in (datos.get("subitems") or {}).items():
        if g:
            subitems[clave] = g
            origen[clave] = {"origen": "manual"}

    resultado = calc.limpieza_terminal(subitems)
    resultado["origen"] = origen
    return resultado


EVALUADORES = {
    "banos": evaluar_banos,
    "confort_termico": evaluar_confort,
    "iluminacion": evaluar_iluminacion,
    "infraestructura": evaluar_infraestructura,
    "asientos_preembarque": evaluar_asientos,
    "puntos_carga": evaluar_puntos_carga,
    "limpieza_terminal": evaluar_limpieza_terminal,
    "gel": evaluar_gel,
    "pista_rodajes": evaluar_pista,
}


def evaluar_item_los(conn: sqlite3.Connection, item_clave: str, datos: dict,
                     periodo: str | None = None) -> dict:
    """Despacha al evaluador del ítem. Elevación se calcula del acumulado del mes."""
    fila = conn.execute(
        "SELECT aplica FROM los_items WHERE clave = ?", (item_clave,)).fetchone()
    if not fila:
        raise LookupError(f"Ítem LoS desconocido: {item_clave}")
    if not fila["aplica"]:
        return {"cumple": None, "no_aplica": True,
                "motivo": "Ítem marcado como NO APLICA en la configuración"}

    if item_clave == "medios_elevacion":
        return evaluar_elevacion(conn, periodo or periodo_actual())
    if item_clave not in EVALUADORES:
        raise LookupError(f"Ítem LoS sin evaluador: {item_clave}")

    # Baños y limpieza de terminal se calculan del check-list del período.
    if item_clave in ("banos", "limpieza_terminal"):
        return EVALUADORES[item_clave](conn, datos or {}, periodo or periodo_actual())
    return EVALUADORES[item_clave](conn, datos or {})


def periodicidad_item_los(conn: sqlite3.Connection, item_clave: str) -> str:
    fila = conn.execute("SELECT periodicidad FROM los_items WHERE clave = ?",
                        (item_clave,)).fetchone()
    if not fila:
        raise LookupError(f"Ítem LoS desconocido: {item_clave}")
    return fila["periodicidad"]


def guardar_medicion_los(conn: sqlite3.Connection, relevamiento_id: int,
                         item_clave: str, datos: dict,
                         observaciones: str | None = None,
                         fecha: str | None = None) -> dict:
    """Persiste entrada y resultado juntos, para que el informe sea reproducible
    aunque después cambien los umbrales de configuración.

    Los ítems DIARIO guardan una medición por día; el resto, una sola por
    período (fecha ''). Mandar fecha en un ítem que no es diario sería crear
    una segunda medición mensual encubierta, así que se rechaza.
    """
    fila = conn.execute(
        "SELECT periodo, estado FROM relevamientos_los WHERE id = ?",
        (relevamiento_id,)).fetchone()
    if not fila:
        raise LookupError(f"No existe el relevamiento {relevamiento_id}")
    if fila["estado"] == "CERRADO":
        raise PermissionError("El relevamiento está cerrado")

    periodicidad = periodicidad_item_los(conn, item_clave)
    if periodicidad == "DIARIO":
        # Se asume hoy solo si hoy cae dentro del período relevado. Al cargar
        # un mes anterior, adivinar la fecha escribiría el relevamiento en un
        # día que el auditor no recorrió: ahí se exige que la indique.
        hoy = date.today().isoformat()
        if not fecha:
            if hoy[:7] != fila["periodo"]:
                raise ValueError(
                    f"El ítem {item_clave} es diario y el período {fila['periodo']} "
                    "no es el actual: indicá la fecha del relevamiento")
            fecha = hoy
        try:
            date.fromisoformat(fecha)
        except ValueError:
            raise ValueError("Fecha inválida: se espera AAAA-MM-DD")
        if fecha[:7] != fila["periodo"]:
            raise ValueError(
                f"La fecha {fecha} no pertenece al período {fila['periodo']}")
    elif fecha:
        raise ValueError(
            f"El ítem {item_clave} no es diario: no admite una fecha de relevamiento")
    else:
        fecha = ""

    resultado = evaluar_item_los(conn, item_clave, datos, fila["periodo"])
    cumple = resultado.get("cumple")

    conn.execute(
        "INSERT INTO los_mediciones (relevamiento_id, item_clave, fecha, datos, "
        "resultado, cumple, observaciones) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT (relevamiento_id, item_clave, fecha) DO UPDATE SET "
        "datos = excluded.datos, resultado = excluded.resultado, "
        "cumple = excluded.cumple, observaciones = excluded.observaciones",
        (relevamiento_id, item_clave, fecha, json.dumps(datos, ensure_ascii=False),
         json.dumps(resultado, ensure_ascii=False),
         None if cumple is None else int(cumple), observaciones))

    _generar_nc_infraestructura(conn, fila["periodo"], item_clave, resultado)
    conn.commit()
    return resultado


def cumple_item_diario(conn: sqlite3.Connection, relevamiento_id: int,
                       item_clave: str) -> dict:
    """Consolida las mediciones diarias de un ítem en el resultado del mes.

    El mes cumple solo si cumplen todos los días relevados: el nivel de
    servicio es una exigencia permanente, no un promedio que un buen día pueda
    compensar. Los días sin relevar no cuentan como cumplidos ni como
    incumplidos — igual que un sector sin confirmar en el check-list.
    """
    filas = [dict(f) for f in conn.execute(
        "SELECT fecha, cumple FROM los_mediciones "
        "WHERE relevamiento_id = ? AND item_clave = ? AND fecha <> '' "
        "ORDER BY fecha", (relevamiento_id, item_clave))]

    evaluados = [f for f in filas if f["cumple"] is not None]
    incumplen = [f["fecha"] for f in evaluados if not f["cumple"]]

    # Períodos cargados antes de que el ítem fuera diario: tienen una única
    # medición mensual (fecha ''). Se sigue respetando mientras no haya
    # relevamientos diarios, para no borrar de la vista lo ya auditado.
    if not filas:
        previa = conn.execute(
            "SELECT cumple FROM los_mediciones WHERE relevamiento_id = ? "
            "AND item_clave = ? AND fecha = ''",
            (relevamiento_id, item_clave)).fetchone()
        if previa and previa["cumple"] is not None:
            return {"dias_relevados": [], "dias_evaluados": 0,
                    "dias_incumplen": [], "cumple": bool(previa["cumple"]),
                    "origen": "medicion_mensual_previa"}

    return {
        "dias_relevados": [f["fecha"] for f in filas],
        "dias_evaluados": len(evaluados),
        "dias_incumplen": incumplen,
        "cumple": (None if not evaluados else not incumplen),
    }


def _generar_nc_infraestructura(conn, periodo, item_clave, resultado) -> None:
    """Los grados C y D generan no conformidad con prioridad (3.4)."""
    for nc in resultado.get("no_conformidades", []) or []:
        ya = conn.execute(
            "SELECT id FROM no_conformidades WHERE periodo = ? AND origen = 'LOS' "
            "AND item = ? AND estado = 'ABIERTA'",
            (periodo, nc["subitem"])).fetchone()
        if not ya:
            conn.execute(
                "INSERT INTO no_conformidades (periodo, origen, sector, item, "
                "descripcion, prioridad) VALUES (?,'LOS',?,?,?,?)",
                (periodo, item_clave, nc["subitem"],
                 f"Grado {nc['grado']} relevado en {nc['subitem']}", nc["prioridad"]))


def relevamiento_los_actual(conn: sqlite3.Connection, periodo: str) -> dict | None:
    """El último relevamiento LoS del período, esté abierto o cerrado.

    Puede haber más de uno histórico (uno por vez que se abrió y cerró); acá
    interesa el vigente, para que la pantalla tenga un solo lugar donde seguir
    cargando.

    Devuelve también los cerrados a propósito. Filtrando por ABIERTO, cerrar el
    relevamiento hacía que la pantalla lo viera como inexistente: el aviso de
    "relevamiento cerrado" nunca llegaba a mostrarse y la siguiente carga abría
    un relevamiento nuevo, partiendo las mediciones del mes en dos. El estado
    viaja en la respuesta y es la UI la que decide si permite editar.
    """
    fila = conn.execute(
        "SELECT * FROM relevamientos_los WHERE periodo = ? "
        "ORDER BY fecha DESC LIMIT 1", (periodo,)).fetchone()
    return dict(fila) if fila else None


def obtener_o_crear_relevamiento_los(conn: sqlite3.Connection, periodo: str,
                                     usuario_id: int) -> int:
    """Devuelve el relevamiento abierto del período, creándolo si no existe.

    Evita que cada carga desde la app dispare un relevamiento nuevo: sin esto,
    abrir la pantalla de LoS varias veces en el mismo mes iría acumulando
    relevamientos vacíos.
    """
    # Solo se reutiliza uno abierto: un relevamiento cerrado es historial y
    # volver a escribirle sería editar un período ya certificado.
    existente = conn.execute(
        "SELECT id FROM relevamientos_los WHERE periodo = ? AND estado = 'ABIERTO' "
        "ORDER BY fecha DESC LIMIT 1", (periodo,)).fetchone()
    if existente:
        return existente["id"]
    cur = conn.execute(
        "INSERT INTO relevamientos_los (periodo, auditor_id) VALUES (?,?)",
        (periodo, usuario_id))
    conn.commit()
    return cur.lastrowid


def medicion_del_dia(conn: sqlite3.Connection, relevamiento_id: int,
                     item_clave: str, fecha: str) -> dict | None:
    """Medición de un ítem diario en una fecha, para poder editarla."""
    f = conn.execute(
        "SELECT id, item_clave, fecha, datos, resultado, cumple, observaciones "
        "FROM los_mediciones WHERE relevamiento_id = ? AND item_clave = ? "
        "AND fecha = ?", (relevamiento_id, item_clave, fecha)).fetchone()
    if not f:
        return None
    return {**dict(f),
            "datos": json.loads(f["datos"]) if f["datos"] else {},
            "resultado": json.loads(f["resultado"]) if f["resultado"] else None}


def mediciones_relevamiento(conn: sqlite3.Connection, relevamiento_id: int) -> dict:
    """Mediciones ya cargadas en un relevamiento, para prellenar los formularios.

    Devuelve tanto los datos crudos (lo que el auditor tipeó) como el
    resultado ya calculado, así la pantalla puede mostrar el estado sin
    esperar un segundo viaje al servidor.
    """
    # Solo las mensuales: las diarias tienen una por día y no entran en un
    # diccionario indexado por ítem. Se consultan aparte, por fecha.
    mediciones = {}
    for f in conn.execute(
            "SELECT id, item_clave, datos, resultado, cumple, observaciones "
            "FROM los_mediciones WHERE relevamiento_id = ? AND fecha = ''",
            (relevamiento_id,)):
        # Con el sub-ítem, para que el informe sepa qué retrata cada foto.
        fotos = [{"archivo": r["archivo"], "subitem": r["subitem"]}
                 for r in conn.execute(
                     "SELECT archivo, subitem FROM fotos "
                     "WHERE entidad = 'los_medicion' AND entidad_id = ?", (f["id"],))]
        mediciones[f["item_clave"]] = {
            "datos": json.loads(f["datos"]),
            "resultado": json.loads(f["resultado"]) if f["resultado"] else None,
            "cumple": None if f["cumple"] is None else bool(f["cumple"]),
            "observaciones": f["observaciones"],
            "fotos": fotos,
        }
    return mediciones


def reabrir_relevamiento_los(conn: sqlite3.Connection, relevamiento_id: int,
                             usuario_id: int, motivo: str) -> dict:
    """Solo admin. Mismo criterio que reabrir un control de limpieza: el
    historial es inmutable salvo por esta vía, que queda logueada."""
    if not (motivo or "").strip():
        raise ValueError("Reabrir un relevamiento cerrado exige un motivo")
    # Mismo criterio que reabrir un control: no mueve números, pero no puede
    # quedar en el log una reapertura que no ocurrió.
    if not conn.execute("SELECT id FROM relevamientos_los WHERE id = ?",
                        (relevamiento_id,)).fetchone():
        raise LookupError(f"No existe el relevamiento {relevamiento_id}")
    conn.execute(
        "UPDATE relevamientos_los SET estado = 'ABIERTO', cerrado_en = NULL "
        "WHERE id = ?", (relevamiento_id,))
    registrar_log(conn, usuario_id, "REABRIR_RELEVAMIENTO_LOS", "relevamientos_los",
                  relevamiento_id, {"motivo": motivo})
    conn.commit()
    return {"ok": True}


def eventos_elevacion(conn: sqlite3.Connection, periodo: str,
                      equipo_id: int | None = None) -> list[dict]:
    """Eventos de indisponibilidad del mes, con el nombre del equipo — para
    que la pantalla muestre de dónde salen las horas acumuladas."""
    sql = ("SELECT e.*, m.nombre equipo_nombre FROM elevacion_eventos e "
           "JOIN medios_elevacion m ON m.id = e.equipo_id WHERE e.periodo = ?")
    args = [periodo]
    if equipo_id is not None:
        sql += " AND e.equipo_id = ?"
        args.append(equipo_id)
    return [dict(f) for f in conn.execute(sql + " ORDER BY e.inicio", args)]


def dashboard_los(conn: sqlite3.Connection, periodo: str,
                  hoy: date | None = None) -> dict:
    """3.12 — Estado de los 11 ítems y % global del período.

    Los ítems diarios informan además si ya se relevaron HOY: es la única
    pregunta que el auditor se hace al abrir la pantalla durante la recorrida.
    El estado acumulado del mes sigue estando, pero no es lo que necesita para
    decidir qué le falta caminar.
    """
    relevamiento = conn.execute(
        "SELECT id FROM relevamientos_los WHERE periodo = ? "
        "ORDER BY fecha DESC LIMIT 1", (periodo,)).fetchone()

    mediciones = {}
    if relevamiento:
        # Solo las mensuales: las diarias se consolidan aparte, con la regla de
        # que el mes cumple únicamente si cumplen todos los días relevados.
        mediciones = {f["item_clave"]: f for f in conn.execute(
            "SELECT item_clave, cumple, resultado, datos FROM los_mediciones "
            "WHERE relevamiento_id = ? AND fecha = ''", (relevamiento["id"],))}

    nc_por_item = {f["item"]: f["c"] for f in conn.execute(
        "SELECT item, COUNT(*) c FROM no_conformidades "
        "WHERE periodo = ? AND estado = 'ABIERTA' GROUP BY item", (periodo,))}

    pendientes = {p["item"] for p in db.inventario_pendiente(conn)}

    fecha_hoy = (hoy or date.today()).isoformat()

    items, diario = [], {}
    for f in conn.execute("SELECT clave, nombre, aplica, periodicidad "
                          "FROM los_items ORDER BY orden"):
        med = mediciones.get(f["clave"])
        cumple = None if med is None or med["cumple"] is None else bool(med["cumple"])

        # Estos ítems no dependen de una medición cargada: salen del acumulado
        # del mes (elevación) o del check-list diario (baños, limpieza de
        # terminal). Se recalculan siempre para reflejar los controles nuevos.
        if f["aplica"] and f["clave"] in ("medios_elevacion", "banos",
                                          "limpieza_terminal"):
            datos_previos = json.loads(med["datos"]) if (
                med and "datos" in med.keys() and med["datos"]) else {}
            cumple = evaluar_item_los(conn, f["clave"], datos_previos,
                                      periodo).get("cumple")
        elif f["aplica"] and f["periodicidad"] == "DIARIO":
            consolidado = (cumple_item_diario(conn, relevamiento["id"], f["clave"])
                           if relevamiento
                           else {"dias_relevados": [], "dias_evaluados": 0,
                                 "dias_incumplen": [], "cumple": None})
            consolidado["relevado_hoy"] = fecha_hoy in consolidado["dias_relevados"]
            consolidado["cumple_hoy"] = (
                None if not consolidado["relevado_hoy"]
                else fecha_hoy not in consolidado["dias_incumplen"])
            diario[f["clave"]] = consolidado
            cumple = consolidado["cumple"]

        item = calc.ItemLoS(
            clave=f["clave"], nombre=f["nombre"], aplica=bool(f["aplica"]),
            cumple=cumple, nc_abiertas=nc_por_item.get(f["clave"], 0))
        items.append(item)

    periodicidades = {f["clave"]: f["periodicidad"] for f in conn.execute(
        "SELECT clave, periodicidad FROM los_items")}

    resultado = calc.resultado_los(items)
    resultado["periodo"] = periodo
    resultado["fecha"] = fecha_hoy
    resultado["requieren_configuracion"] = sorted(pendientes)
    for entrada in resultado["items"]:
        entrada["requiere_configuracion"] = entrada["clave"] in pendientes
        entrada["periodicidad"] = periodicidades.get(entrada["clave"], "MENSUAL")
        if entrada["clave"] in diario:
            entrada["diario"] = diario[entrada["clave"]]
    return resultado


# ==========================================================================
# Utilidades
# ==========================================================================

def estado_onboarding(conn: sqlite3.Connection) -> dict:
    """Qué falta cargar para que la app quede operativa (sección 4.2).

    El módulo Limpieza funciona desde el minuto cero porque su seed viene
    completo; el de LoS no puede evaluar los ítems cuantitativos hasta que
    alguien cargue el inventario físico del aeropuerto. Esta función alimenta
    el asistente de primera configuración y el cartel de "Requiere
    configuración" en el dashboard.
    """
    def _contar(tabla):
        return conn.execute(f"SELECT COUNT(*) c FROM {tabla}").fetchone()["c"]

    asientos = conn.execute(
        "SELECT instalados FROM asientos_preembarque WHERE id = 1").fetchone()

    pasos = [
        {"clave": "nucleos", "titulo": "Núcleos sanitarios",
         "descripcion": "Cantidad instalada de cada artefacto por baño "
                        "(Damas, Caballeros, PMR, recinto de bebés).",
         "items_los": ["banos"], "cargados": _contar("nucleos_sanitarios")},
        {"clave": "luminarias", "titulo": "Luminarias por sector",
         "descripcion": "Total de luminarias de cada sector, para calcular el "
                        "porcentaje de encendidas.",
         "items_los": ["iluminacion"], "cargados": _contar("luminarias_sector")},
        {"clave": "asientos", "titulo": "Asientos de preembarque",
         "descripcion": "Total de asientos instalados en la sala.",
         "items_los": ["asientos_preembarque"],
         "cargados": 1 if (asientos and asientos["instalados"] > 0) else 0},
        {"clave": "puertas", "titulo": "Puntos de carga",
         "descripcion": "Por puerta de embarque: tomas instaladas y pasajeros "
                        "en hora pico de referencia.",
         "items_los": ["puntos_carga"], "cargados": _contar("puertas_embarque")},
        {"clave": "elevacion", "titulo": "Medios de elevación",
         "descripcion": "Ascensores, escaleras mecánicas y plataformas, "
                        "indicando si tienen equipo redundante.",
         "items_los": ["medios_elevacion"], "cargados": _contar("medios_elevacion")},
        {"clave": "secciones", "titulo": "Secciones de pista y rodaje",
         "descripcion": "Identificador de cada sección, para la carga de PCI.",
         "items_los": ["pista_rodajes"], "cargados": _contar("secciones_pavimento")},
    ]
    # Un bloque de inventario solo se exige si alguno de los ítems LoS que
    # alimenta rige en este aeropuerto. IRJ no tiene mangas ni medios de
    # elevación: pedir su inventario dejaba el onboarding en "falta cargar 1
    # bloque" para siempre, sin nada que el admin pudiera hacer al respecto.
    # `inventario_pendiente` ya filtraba por `aplica`, así que la respuesta se
    # contradecía a sí misma: ningún ítem bloqueado y el progreso incompleto.
    rige = {f["clave"]: bool(f["aplica"]) for f in conn.execute(
        "SELECT clave, aplica FROM los_items")}
    for p in pasos:
        p["aplica"] = any(rige.get(i, True) for i in p["items_los"])
        p["completo"] = p["cargados"] > 0

    aplicables = [p for p in pasos if p["aplica"]]
    completos = [p for p in aplicables if p["completo"]]
    return {
        "pasos": pasos,
        "completos": len(completos),
        "total": len(aplicables),
        "terminado": len(completos) == len(aplicables),
        # Sin bloques aplicables no hay nada que configurar, y eso es el 100%:
        # una división por cero acá dejaría la app sin arrancar.
        "progreso": len(completos) / len(aplicables) if aplicables else 1.0,
        "items_bloqueados": [p["item"] for p in db.inventario_pendiente(conn)],
        # Cuántos ítems LoS rigen. La pantalla lo anunciaba con un número
        # escrito a mano —"los 11 ítems"— que dejaba de ser cierto en cuanto
        # se marcaba uno como no aplicable.
        "items_los_aplicables": sum(1 for v in rige.values() if v),
    }


def periodo_actual() -> str:
    return date.today().strftime("%Y-%m")


def estacion_actual(conn: sqlite3.Connection, hoy: date | None = None) -> str:
    """Estación según las fechas de cambio configuradas (hemisferio sur)."""
    hoy = hoy or date.today()
    inicio_verano = db.get_config(conn, "inicio_verano", "10-01")
    inicio_invierno = db.get_config(conn, "inicio_invierno", "04-01")
    mmdd = hoy.strftime("%m-%d")
    # El verano cruza el fin de año: va de inicio_verano a inicio_invierno.
    if inicio_verano > inicio_invierno:
        return "VERANO" if (mmdd >= inicio_verano or mmdd < inicio_invierno) else "INVIERNO"
    return "VERANO" if inicio_verano <= mmdd < inicio_invierno else "INVIERNO"


# ==========================================================================
# FRENO A LA PRUEBA DE CONTRASEÑAS
# ==========================================================================

# Valores por defecto; el admin los puede cambiar desde la configuración.
# Diez intentos en un cuarto de hora no molestan a nadie que se haya olvidado
# la contraseña, y cortan de raíz el barrido automático.
LOGIN_MAX_INTENTOS = 10
LOGIN_VENTANA_MINUTOS = 15


def _limites_login(conn) -> tuple[int, int]:
    return (db.get_config(conn, "login_max_intentos", LOGIN_MAX_INTENTOS),
            db.get_config(conn, "login_ventana_minutos", LOGIN_VENTANA_MINUTOS))


def login_bloqueado(conn: sqlite3.Connection, usuario: str,
                    ip: str | None) -> int | None:
    """Minutos que faltan para poder reintentar, o None si no está bloqueado.

    Se cuentan por separado los fallos del usuario y los de la IP: lo primero
    protege una cuenta concreta de que le prueben contraseñas, lo segundo frena
    a quien barre muchos usuarios desde el mismo origen.
    """
    maximo, ventana = _limites_login(conn)
    if maximo <= 0:
        return None

    claves = [f"usuario:{usuario}"] + ([f"ip:{ip}"] if ip else [])

    # Las dos claves se resuelven en una sola consulta: contra una base remota
    # cada ida y vuelta cuesta más que el trabajo que hace, y esto está en el
    # camino de todos los logins.
    marcadores = ",".join("?" for _ in claves)
    filas = conn.execute(
        f"SELECT clave, COUNT(*) c, MIN(momento) primero FROM intentos_login "
        f"WHERE clave IN ({marcadores}) AND momento > datetime('now', ?) "
        f"GROUP BY clave",
        (*claves, f"-{ventana} minutes")).fetchall()

    for fila in filas:
        if fila["c"] < maximo:
            continue
        # Se espera a que el intento más viejo salga de la ventana.
        from datetime import datetime, timedelta
        try:
            vence = (datetime.strptime(fila["primero"], "%Y-%m-%d %H:%M:%S")
                     + timedelta(minutes=ventana))
            faltan = int((vence - datetime.utcnow()).total_seconds() // 60) + 1
        except (ValueError, TypeError):
            faltan = ventana
        return max(1, faltan)
    return None


def registrar_intento_fallido(conn: sqlite3.Connection, usuario: str,
                              ip: str | None) -> None:
    """Anota el fallo y limpia lo que ya salió de la ventana."""
    _, ventana = _limites_login(conn)
    conn.execute("INSERT INTO intentos_login (clave) VALUES (?)",
                 (f"usuario:{usuario}",))
    if ip:
        conn.execute("INSERT INTO intentos_login (clave) VALUES (?)", (f"ip:{ip}",))
    # La tabla no debe crecer sin fin: lo viejo ya no cuenta para nada.
    conn.execute("DELETE FROM intentos_login WHERE momento < datetime('now', ?)",
                 (f"-{max(ventana, 60)} minutes",))
    conn.commit()


def limpiar_intentos(conn: sqlite3.Connection, usuario: str,
                     ip: str | None) -> None:
    """Un login correcto borra el historial de fallos de ese usuario y esa IP."""
    conn.execute("DELETE FROM intentos_login WHERE clave = ?", (f"usuario:{usuario}",))
    if ip:
        conn.execute("DELETE FROM intentos_login WHERE clave = ?", (f"ip:{ip}",))


def registrar_log(conn: sqlite3.Connection, usuario_id: int | None, accion: str,
                  entidad: str | None = None, entidad_id: int | None = None,
                  detalle: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO auditoria_log (usuario_id, accion, entidad, entidad_id, detalle) "
        "VALUES (?,?,?,?,?)",
        (usuario_id, accion, entidad, entidad_id,
         json.dumps(detalle, ensure_ascii=False) if detalle else None))


# ==========================================================================
# CENTRO DE NOVEDADES
# ==========================================================================

# Días sin resolver a partir de los cuales una no conformidad se considera
# demorada. Es un umbral de gestión, no un valor del pliego: sirve para separar
# lo que quedó pendiente ayer de lo que lleva semanas sin respuesta.
DIAS_NC_DEMORADA = 7

# Criticidad de una novedad:
#   ALTA  algo que ya está incumpliendo o que impacta el importe a certificar.
#   MEDIA algo que hay que atender pero todavía no penaliza.
CRITICIDAD_ALTA = "ALTA"
CRITICIDAD_MEDIA = "MEDIA"


def novedades(conn: sqlite3.Connection, hoy: date | None = None,
              es_admin: bool = False) -> dict:
    """Lo que el auditor tiene que saber al abrir la app.

    Se arma sobre los datos que ya existen: no hay tabla de novedades ni
    estado que mantener sincronizado, así que nunca puede quedar desfasada de
    la realidad que reporta.

    Las novedades vienen agregadas, no una por hallazgo: con 19 no
    conformidades abiertas, una notificación por cada una es ruido que se
    aprende a ignorar. Cada entrada dice cuántos casos agrupa y a dónde ir.
    """
    hoy = hoy or date.today()
    periodo = hoy.strftime("%Y-%m")
    lista = []

    def agregar(clave, criticidad, titulo, detalle, ruta, cantidad=1):
        lista.append({"clave": clave, "criticidad": criticidad, "titulo": titulo,
                      "detalle": detalle, "ruta": ruta, "cantidad": cantidad})

    # -- no conformidades sin resolver ------------------------------------
    # Se incluyen las de HOY: una no conformidad que cargó el turno mañana es
    # justamente lo que el turno tarde tiene que salir a verificar.
    pendientes = nc_pendientes_anteriores(conn, hoy.isoformat(), limite=500,
                                          incluir_fecha=True)

    # Categorías excluyentes, de más a menos urgente, para que una misma no
    # conformidad no aparezca contada dos veces.
    demoradas = [p for p in pendientes if p["dias_pendiente"] >= DIAS_NC_DEMORADA]
    resto = [p for p in pendientes if p not in demoradas]
    inmediatas = [p for p in resto if p["prioridad"] == "INMEDIATA"]
    programadas = [p for p in resto if p not in inmediatas]

    if demoradas:
        peor = max(p["dias_pendiente"] for p in demoradas)
        agregar("nc_demoradas", CRITICIDAD_ALTA,
                f"{len(demoradas)} no conformidad(es) demorada(s)",
                f"Sin resolver hace {peor} días o más. Verificalas en recorrida.",
                "/limpieza", len(demoradas))
    if inmediatas:
        agregar("nc_inmediatas", CRITICIDAD_ALTA,
                f"{len(inmediatas)} no conformidad(es) de resolución inmediata",
                f"{inmediatas[0]['sector'] or inmediatas[0]['origen']}: "
                f"{inmediatas[0]['descripcion']}", "/limpieza", len(inmediatas))
    if programadas:
        agregar("nc_pendientes", CRITICIDAD_MEDIA,
                f"{len(programadas)} no conformidad(es) sin resolver",
                "De resolución programada.", "/limpieza", len(programadas))

    # -- plan de auditoría --------------------------------------------------
    mes = completitud_periodo(conn, periodo, hoy)
    vencidos = mes["dias_vencidos_sin_control"]
    if vencidos:
        agregar("dias_sin_control", CRITICIDAD_ALTA,
                f"{len(vencidos)} día(s) sin ningún control",
                f"El último fue el {vencidos[-1]}. No computan ni penalizan, "
                "pero bajan la representatividad del mes.",
                "/limpieza", len(vencidos))

    parciales = mes["turnos"]["dias_parciales"]
    if parciales:
        # A limpieza y no a la pantalla de inicio: el centro de novedades se
        # abre casi siempre desde inicio, así que mandar ahí era repintar la
        # pantalla donde el auditor ya estaba y el botón parecía muerto. En
        # limpieza está el listado de días por turno, que es donde se actúa.
        agregar("turnos_faltantes", CRITICIDAD_MEDIA,
                f"{len(parciales)} día(s) con una sola recorrida",
                "Se exigen dos controles diarios.", "/limpieza", len(parciales))

    if mes["cobertura"] is not None and not mes["cobertura_suficiente"]:
        agregar("cobertura", CRITICIDAD_MEDIA,
                f"Cobertura del mes en {round(mes['cobertura'] * 100)}%",
                f"Por debajo del mínimo esperado "
                f"({round(mes['cobertura_minima'] * 100)}%).", "/limpieza")

    # -- maquinaria fuera de servicio ---------------------------------------
    # Solo las bajas sin reposición: son las que siguen descontando del ítem 4
    # de la certificación día a día mientras nadie las cierre.
    abiertas = [dict(f) for f in conn.execute(
        "SELECT b.id, b.desde, e.nombre equipo FROM equipamiento_baja b "
        "JOIN equipamiento_limpieza e ON e.id = b.equipamiento_id "
        "WHERE b.hasta IS NULL AND b.desde <= ? ORDER BY b.desde",
        (hoy.isoformat(),))]

    # Marcas sueltas del modelo anterior (equipamiento_faltante), que se siguen
    # generando desde cualquier tablet que todavía tenga cacheado el frontend
    # viejo. Sin esto, una máquina cargada por ese camino no produce novedad.
    marcadas = [dict(f) for f in conn.execute(
        "SELECT e.nombre equipo, c.fecha desde FROM equipamiento_faltante f "
        "JOIN equipamiento_limpieza e ON e.id = f.equipamiento_id "
        "JOIN controles_limpieza c ON c.id = f.control_id "
        "WHERE c.fecha = ?", (hoy.isoformat(),))]
    ya = {a["equipo"] for a in abiertas}
    fuera = abiertas + [m for m in marcadas if m["equipo"] not in ya]

    if fuera:
        dias = (hoy - date.fromisoformat(fuera[0]["desde"])).days
        agregar("maquinaria_baja", CRITICIDAD_ALTA,
                f"{len(fuera)} máquina(s) fuera de servicio",
                f"{fuera[0]['equipo']}" + (f" lleva {dias} día(s) de baja."
                                           if dias else " desde hoy.")
                # Tampoco a inicio: el equipamiento se ve y se da de alta desde
                # el control diario, y a él se llega por limpieza. `/config` no
                # sirve como destino porque es solo de admin y a un auditor lo
                # rebota a inicio, que es el problema que se está corrigiendo.
                + " Descuenta del importe a certificar.", "/limpieza", len(fuera))

    # -- niveles de servicio -------------------------------------------------
    try:
        dash = dashboard_los(conn, periodo)
        incumplen = [i for i in dash["items"] if i["estado"] == "NO_CUMPLE"]
        if incumplen:
            agregar("los_no_cumple", CRITICIDAD_ALTA,
                    f"{len(incumplen)} ítem(s) de LoS no cumplen",
                    ", ".join(i["nombre"] for i in incumplen[:3])
                    + ("…" if len(incumplen) > 3 else ""),
                    "/los", len(incumplen))
    except Exception:
        # Una falla evaluando LoS no puede dejar sin novedades al resto.
        pass

    # -- configuración pendiente (solo al admin) -----------------------------
    if es_admin:
        pendiente_inv = db.inventario_pendiente(conn)
        if pendiente_inv:
            agregar("inventario", CRITICIDAD_MEDIA,
                    f"{len(pendiente_inv)} ítem(s) sin inventario cargado",
                    "No se pueden relevar y quedan como Sin datos.",
                    "/config", len(pendiente_inv))

    orden = {CRITICIDAD_ALTA: 0, CRITICIDAD_MEDIA: 1}
    lista.sort(key=lambda n: orden[n["criticidad"]])

    return {
        "fecha": hoy.isoformat(),
        "periodo": periodo,
        "novedades": lista,
        "total": len(lista),
        "criticas": len([n for n in lista if n["criticidad"] == CRITICIDAD_ALTA]),
    }
