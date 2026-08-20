"""
Informes del pliego (sección 5): PDF mensual de limpieza, PDF de LoS y
exports CSV de datos crudos.

Los PDF se arman sobre `pdf.py`. Todo el contenido sale de lo que ya calculó
el motor: este módulo no recalcula nada, solo maqueta. Si un dato falta, el
informe lo dice explícitamente en lugar de omitirlo — un informe que se
certifica no puede tener silencios.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3

import calc
import db
import fotos
import pdf
import services

# Paleta, alineada con la de la app.
AZUL = (0.043, 0.239, 0.420)
VERDE = (0.106, 0.561, 0.302)
ROJO = (0.753, 0.224, 0.169)
AMBAR = (0.545, 0.396, 0.000)
GRIS = (0.42, 0.45, 0.50)
GRIS_CLARO = (0.94, 0.95, 0.96)
BORDE = (0.84, 0.86, 0.89)
NEGRO = (0.10, 0.12, 0.15)

MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
         'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def nombre_periodo(periodo: str) -> str:
    anio, mes = periodo.split('-')
    return f"{MESES[int(mes) - 1]} {anio}"


def pct(valor, decimales=1) -> str:
    """Porcentaje en formato local. Un guión cuando no hay dato: nunca 0%."""
    if valor is None:
        return '—'
    return f"{valor * 100:.{decimales}f}".replace('.', ',') + '%'


class Informe:
    """Envoltorio de `pdf.PDF` con flujo vertical y saltos de página.

    Lleva el cursor `y` y repite encabezado y pie en cada página, que es lo
    que distingue un informe de un volcado de datos.
    """

    def __init__(self, titulo, subtitulo, periodo, aeropuerto, concesionario):
        self.doc = pdf.PDF()
        self.titulo = titulo
        self.subtitulo = subtitulo
        self.periodo = periodo
        self.aeropuerto = aeropuerto
        self.concesionario = concesionario
        self.ancho_util = self.doc.ancho - 2 * pdf.MARGEN
        self.x = pdf.MARGEN
        self.y = 0
        self._encabezado()

    # -- estructura de página --------------------------------------------

    def _encabezado(self):
        d = self.doc
        d.rect(0, 0, d.ancho, 74, relleno=AZUL)

        # Nombre del aeropuerto y título comparten renglón. El nombre completo
        # ("Aeropuerto Capitán Vicente Almandos Almonacid — IRJ") es largo y se
        # superponía con el título: se achica hasta que ambos entren.
        # 40 pt de aire entre ambos: con menos quedan pegados y se leen como
        # una sola frase.
        disponible = d.ancho - 2 * pdf.MARGEN - 40
        tam_titulo = 11
        tam_nombre = 12
        while (pdf.ancho_texto(self.aeropuerto, tam_nombre, True)
               + pdf.ancho_texto(self.titulo, tam_titulo, True) > disponible
               and tam_nombre > 7.5):
            tam_nombre -= 0.5
            tam_titulo -= 0.25

        d.texto(pdf.MARGEN, 30, self.aeropuerto, tam_nombre, True, (1, 1, 1))
        d.texto(pdf.MARGEN, 46, f"Concesionario: {self.concesionario}", 8.5,
                False, (0.85, 0.90, 0.95))
        d.texto_derecha(d.ancho - pdf.MARGEN, 30, self.titulo, tam_titulo, True,
                        (1, 1, 1))
        d.texto_derecha(d.ancho - pdf.MARGEN, 46, nombre_periodo(self.periodo),
                        9, False, (0.85, 0.90, 0.95))
        self.y = 104

    def _pie(self, numero):
        d = self.doc
        y = d.alto - 28
        d.linea(pdf.MARGEN, y - 10, d.ancho - pdf.MARGEN, y - 10, 0.5, BORDE)
        d.texto(pdf.MARGEN, y, self.subtitulo, 7.5, False, GRIS)
        d.texto_derecha(d.ancho - pdf.MARGEN, y, f"Página {numero}", 7.5, False, GRIS)

    def salto(self):
        self._pie(self.doc.paginas_totales)
        self.doc.nueva_pagina()
        self._encabezado()

    def espacio(self, alto):
        """Reserva espacio; salta de página si no entra."""
        if self.y + alto > self.doc.alto - 56:
            self.salto()

    # -- bloques ----------------------------------------------------------

    def titulo_seccion(self, texto):
        self.espacio(40)
        self.doc.texto(self.x, self.y, texto, 12, True, AZUL)
        self.doc.linea(self.x, self.y + 6, self.doc.ancho - pdf.MARGEN,
                       self.y + 6, 1, AZUL)
        self.y += 24

    def parrafo(self, texto, tam=9, color=NEGRO, negrita=False):
        alto = 14 + tam * 1.35 * (len(str(texto)) // 110 + 1)
        self.espacio(alto)
        self.y = self.doc.parrafo(self.x, self.y, texto, self.ancho_util,
                                  tam, negrita, color) + 8

    def aviso(self, texto, color=AMBAR, titulo=None):
        """Recuadro destacado: se usa para las advertencias que no pueden
        pasar inadvertidas (cobertura baja, criterios provisorios)."""
        lineas = (len(str(texto)) // 105 + 1) + (1 if titulo else 0)
        alto = 16 + lineas * 12
        self.espacio(alto + 12)
        self.doc.rect(self.x, self.y - 10, self.ancho_util, alto,
                      relleno=(0.99, 0.96, 0.90))
        self.doc.rect(self.x, self.y - 10, 3, alto, relleno=color)
        y = self.y
        if titulo:
            self.doc.texto(self.x + 10, y, titulo, 9, True, color)
            y += 13
        y = self.doc.parrafo(self.x + 10, y, texto, self.ancho_util - 20, 8.5,
                             False, NEGRO)
        self.y = max(y, self.y + alto - 10) + 10

    def tabla(self, columnas, filas, alto_fila=17):
        """columnas: [(titulo, ancho, alineacion)] con alineacion 'izq'|'der'."""
        self.espacio(alto_fila * 2)
        d = self.doc

        # Encabezado
        d.rect(self.x, self.y - 11, self.ancho_util, alto_fila, relleno=GRIS_CLARO)
        cx = self.x
        for titulo, ancho, alineacion in columnas:
            if alineacion == 'der':
                d.texto_derecha(cx + ancho - 6, self.y, titulo, 8, True, GRIS)
            else:
                d.texto(cx + 6, self.y, titulo, 8, True, GRIS)
            cx += ancho
        self.y += alto_fila

        for fila in filas:
            if self.y + alto_fila > d.alto - 56:
                self.salto()
                # Repetir encabezado tras el salto, si no la tabla queda huérfana.
                d.rect(self.x, self.y - 11, self.ancho_util, alto_fila,
                       relleno=GRIS_CLARO)
                cx = self.x
                for titulo, ancho, alineacion in columnas:
                    if alineacion == 'der':
                        d.texto_derecha(cx + ancho - 6, self.y, titulo, 8, True, GRIS)
                    else:
                        d.texto(cx + 6, self.y, titulo, 8, True, GRIS)
                    cx += ancho
                self.y += alto_fila

            cx = self.x
            for (titulo, ancho, alineacion), celda in zip(columnas, fila):
                valor, color, negrita = (celda if isinstance(celda, tuple)
                                         else (celda, NEGRO, False))
                if alineacion == 'der':
                    self.doc.texto_derecha(cx + ancho - 6, self.y, valor, 8.5,
                                           negrita, color)
                else:
                    self.doc.texto(cx + 6, self.y, valor, 8.5, negrita, color)
                cx += ancho
            self.doc.linea(self.x, self.y + 5, self.x + self.ancho_util,
                           self.y + 5, 0.4, BORDE)
            self.y += alto_fila
        self.y += 8

    def dato_grande(self, etiqueta, valor, detalle=None, color=AZUL):
        self.espacio(70)
        self.doc.rect(self.x, self.y - 12, self.ancho_util, 58,
                      relleno=(0.97, 0.98, 0.99), borde=BORDE)
        self.doc.texto(self.x + 14, self.y + 4, etiqueta, 8.5, False, GRIS)
        self.doc.texto(self.x + 14, self.y + 28, valor, 22, True, color)
        if detalle:
            self.doc.texto_derecha(self.x + self.ancho_util - 14, self.y + 28,
                                   detalle, 10, False, GRIS)
        self.y += 66

    def firmas(self, etiquetas, emisor=None):
        """Bloques de firma al pie.

        El primero se pre-completa con el auditor que emitió el informe: nombre,
        usuario y fecha/hora. No es una firma criptográfica —el PDF sigue siendo
        alterable— pero deja constancia de quién lo generó, que es lo que hoy
        falta cuando el bloque sale en blanco.
        """
        self.espacio(110)
        ancho = (self.ancho_util - 30) / len(etiquetas)
        for i, etiqueta in enumerate(etiquetas):
            x = self.x + i * (ancho + 15)

            if i == 0 and emisor:
                self.doc.texto(x, self.y + 14, emisor.get("nombre", ""), 11, True, NEGRO)
                detalle = f"Usuario: {emisor.get('usuario', '')}"
                self.doc.texto(x, self.y + 28, detalle, 8, False, GRIS)
                self.doc.texto(x, self.y + 39,
                               f"Emitido {emisor.get('emitido', '')}", 8, False, GRIS)
            else:
                self.doc.texto(x, self.y + 28, "Aclaración:", 8, False, GRIS)
                self.doc.texto(x, self.y + 39, "Fecha:", 8, False, GRIS)

            self.doc.linea(x, self.y + 48, x + ancho, self.y + 48, 0.6, GRIS)
            self.doc.texto(x, self.y + 62, etiqueta, 8, True, GRIS)
        self.y += 86

    def generar(self) -> bytes:
        self._pie(self.doc.paginas_totales)
        return self.doc.generar()


# ==========================================================================
# Informe mensual de limpieza (5.1)
# ==========================================================================

NOMBRE_ITEM_CERT = {
    'documentacion': 'Documentación obligatoria',
    'ley_19587': 'Ley 19587 (seguridad e higiene)',
    'programacion_trabajos': 'Programación de trabajos',
    'maquinarias': 'Maquinarias y equipos exigidos',
    'insumos': 'Disponibilidad de insumos',
    'calidad_servicio': 'Calidad de servicio',
}


def informe_limpieza(conn: sqlite3.Connection, periodo: str,
                     incluir_fotos: bool = True, emisor: dict | None = None) -> bytes:
    resumen = services.resumen_mensual_limpieza(conn, periodo)
    # Se le pasa el resumen ya calculado: es el mismo período y por dentro la
    # certificación lo rehacía entero.
    cert = services.certificacion(conn, periodo, resumen)
    comp = resumen['completitud']

    inf = Informe(
        titulo='Auditoría mensual · Servicio de limpieza',
        subtitulo='Auditoría del servicio de limpieza — Operaciones A. Argentina',
        periodo=periodo,
        aeropuerto=db.get_config(conn, 'aeropuerto_nombre', 'Aeropuerto') + ' — '
                   + db.get_config(conn, 'aeropuerto_codigo', ''),
        concesionario=db.get_config(conn, 'concesionario', ''))

    inf.parrafo(
        'REF: resultado del control de calidad de las tareas realizadas por el '
        'contratista, conforme al pliego vigente. El relevamiento se realiza por '
        'excepción: se asume cumplimiento y se registran únicamente los desvíos '
        'constatados por personal de Operaciones.', 9, GRIS)

    # -- cobertura: lo primero, porque condiciona todo lo demás --
    inf.titulo_seccion('Cobertura del período')
    inf.dato_grande(
        'Cumplimiento general del servicio',
        pct(resumen['porcentaje_general']),
        f"calculado sobre {resumen['turnos_considerados']} recorrida(s) "
        f"en {resumen['dias_considerados']} de {resumen['dias_del_mes']} días",
        VERDE if (resumen['porcentaje_general'] or 0) >= 0.9 else AMBAR)

    if not comp['cobertura_suficiente']:
        inf.aviso(
            f"Se auditaron {len(comp['dias_cerrados'])} de {comp['dias_esperados']} "
            f"días del mes (cobertura {pct(comp['cobertura'], 0)}, mínimo esperado "
            f"{pct(comp['cobertura_minima'], 0)}). El porcentaje surge únicamente de "
            'los días efectivamente auditados: con pocos días el resultado depende '
            'mucho de cuáles se relevaron y es poco representativo del mes.',
            ROJO, 'Cobertura insuficiente')
    elif not comp['completo']:
        inf.aviso(
            f"Se auditaron {len(comp['dias_cerrados'])} de {comp['dias_esperados']} "
            f"días ({pct(comp['cobertura'], 0)}). Los días sin auditar no computan "
            'ni penalizan al contratista.', AZUL, 'Mes incompleto')

    # -- sectores --
    inf.titulo_seccion('Cumplimiento por sector')
    filas = []
    for s in resumen['sectores']:
        p = s['mensual']
        color = NEGRO if p is None else (VERDE if p >= 0.9 else
                                         (AMBAR if p >= 0.7 else ROJO))
        filas.append([
            s['nombre'],
            str(s['dias_con_datos']),
            (pct(p), color, True),
        ])
    inf.tabla([('Sector', 300, 'izq'), ('Días relevados', 100, 'der'),
               ('Promedio mensual', 111, 'der')], filas)

    # -- certificación --
    inf.titulo_seccion('Certificación mensual')
    # Sin importe en pesos: el informe certifica el porcentaje de cumplimiento,
    # y aplicarlo al valor adjudicado del sitio (PCP 4.3) es de quien liquida.
    # Este PDF se comparte con el contratista y no tiene por qué transportar el
    # monto del contrato.
    inf.dato_grande(
        'Porcentaje a certificar',
        pct(cert['porcentaje']),
        f"sobre {len(cert['detalle'])} ítems del pliego",
        VERDE if (cert['porcentaje'] or 0) >= 0.9 else AMBAR)

    filas = []
    for clave, nombre in NOMBRE_ITEM_CERT.items():
        detalle = cert['detalle'].get(clave)
        if detalle is None:
            filas.append([nombre, '—', ('Sin datos', GRIS, False), '—'])
        else:
            filas.append([
                nombre,
                pct(detalle['peso'], 0),
                (pct(detalle['valor']), NEGRO, True),
                pct(detalle['aporte']),
            ])
    inf.tabla([('Ítem', 250, 'izq'), ('Peso', 70, 'der'),
               ('Resultado', 95, 'der'), ('Aporte', 96, 'der')], filas)

    for a in cert.get('advertencias', []):
        if a['codigo'] in ('COBERTURA_INSUFICIENTE', 'MES_INCOMPLETO'):
            continue          # ya se informó arriba
        inf.aviso(a['mensaje'], AMBAR if a['nivel'] == 'ADVERTENCIA' else AZUL)

    # -- equipamiento: de dónde sale el ítem 4 --
    equip = cert.get('equipamiento') or {}
    if equip.get('exigidos'):
        inf.titulo_seccion('Maquinarias y equipos exigidos')
        inf.parrafo(
            f"El pliego exige {equip['exigidos']} equipo(s). Cada uno aporta sus "
            f"días en servicio sobre los {equip['dias_considerados']} día(s) "
            'transcurridos del período, y recién después se promedian entre '
            'equipos: una rotura de dos días no pesa igual que una de todo el mes.',
            9, GRIS)

        con_faltas = equip.get('equipos_con_faltas') or []
        if con_faltas:
            inf.tabla([('Equipo', 380, 'izq'), ('Días fuera de servicio', 131, 'der')],
                      [[e['nombre'], (str(e['dias_fuera_servicio']), ROJO, True)]
                       for e in con_faltas])
        else:
            inf.parrafo('Todos los equipos exigidos estuvieron disponibles en los '
                        'días auditados.', 9, GRIS)

    # -- no conformidades --
    ncs = [dict(f) for f in conn.execute(
        "SELECT * FROM no_conformidades WHERE periodo = ? AND origen = 'LIMPIEZA' "
        "ORDER BY creado_en", (periodo,))]
    inf.titulo_seccion(f'No conformidades del período ({len(ncs)})')

    if not ncs:
        inf.parrafo('No se registraron no conformidades en el período.', 9, GRIS)
    else:
        for nc in ncs:
            _bloque_nc(inf, conn, nc, incluir_fotos)

    inf.titulo_seccion('Conformidad')
    inf.parrafo(
        'El presente informe refleja el resultado de las auditorías realizadas '
        'en el período indicado.', 9, GRIS)
    inf.firmas(['Auditor — Aeropuertos Argentina', 'Responsable Contratista'],
               emisor)

    return inf.generar()


def _bloque_nc(inf: Informe, conn, nc: dict, incluir_fotos: bool):
    """Una no conformidad con su evidencia fotográfica."""
    inf.espacio(60)
    color = ROJO if nc['prioridad'] == 'INMEDIATA' else AMBAR
    estado = 'RESUELTA' if nc['estado'] == 'RESUELTA' else 'ABIERTA'

    inf.doc.texto(inf.x, inf.y, f"{nc['sector'] or ''} · {nc['item'] or ''}",
                  9.5, True, NEGRO)
    inf.doc.texto_derecha(inf.x + inf.ancho_util, inf.y,
                          f"{nc['prioridad'] or ''} · {estado}", 8, True,
                          VERDE if estado == 'RESUELTA' else color)
    inf.y += 14
    inf.y = inf.doc.parrafo(inf.x, inf.y, nc['descripcion'], inf.ancho_util,
                            8.5, False, NEGRO) + 4
    inf.doc.texto(inf.x, inf.y, f"Registrada: {nc['creado_en']}"
                  + (f" · Resuelta: {nc['resuelto_en']}" if nc['resuelto_en'] else ''),
                  7.5, False, GRIS)
    inf.y += 12

    if incluir_fotos and nc.get('desvio_id'):
        fotos = [f['archivo'] for f in conn.execute(
            "SELECT archivo FROM fotos WHERE entidad = 'desvio' AND entidad_id = ?",
            (nc['desvio_id'],))]
        _fila_fotos(inf, fotos)

    inf.doc.linea(inf.x, inf.y, inf.x + inf.ancho_util, inf.y, 0.4, BORDE)
    inf.y += 12


def _fila_fotos(inf: Informe, archivos: list[str], alto=90):
    """Miniaturas de evidencia. Solo JPEG: es lo que produce la app."""
    if not archivos:
        return
    from PIL import Image
    import almacen
    import api

    deposito = almacen.obtener(api.UPLOADS_DIR)
    x = inf.x
    inf.espacio(alto + 14)
    for archivo in archivos[:4]:                # 4 por fila alcanza y sobra
        # Se leen los bytes en vez de abrir una ruta: con la evidencia en
        # Supabase Storage no hay archivo local que abrir, y PIL trabaja igual
        # sobre un buffer en memoria.
        try:
            binario = deposito.leer(archivo)
        except almacen.ErrorAlmacen:
            continue                            # el informe no depende de la evidencia
        if binario is None:
            continue
        try:
            with Image.open(io.BytesIO(binario)) as img:
                img = img.convert('RGB')
                w, h = img.size
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=70)
                datos = buf.getvalue()
        except Exception:
            continue                            # una foto ilegible no frena el informe
        ancho = alto * w / h
        if x + ancho > inf.x + inf.ancho_util:
            break
        inf.doc.imagen_jpeg(x, inf.y, ancho, alto, datos, (w, h))
        x += ancho + 8
    inf.y += alto + 10


# ==========================================================================
# Informe de un control diario
# ==========================================================================

NOMBRE_TURNO = {'MANANA': 'turno mañana', 'TARDE': 'turno tarde'}


def informe_dia(conn: sqlite3.Connection, fecha: str,
                emisor: dict | None = None, turno: str | None = None) -> bytes:
    """Informe de una recorrida puntual, con su evidencia.

    A diferencia del mensual, las fotos van a tamaño grande: este es el
    documento que acompaña el reclamo puntual al contratista, y ahí la
    evidencia es el contenido principal, no un anexo.

    El informe es de UNA recorrida, no del día: desde que se exigen dos turnos
    diarios, cada uno tiene sus propios sectores, desvíos y firmas, y mezclarlos
    en un solo documento haría irrastreable quién constató qué. Sin `turno` se
    toma el primero del día, y el encabezado dice siempre cuál es — antes se
    elegía uno en silencio y el otro turno no aparecía en ningún informe.
    """
    sql = ("SELECT c.*, u.nombre auditor FROM controles_limpieza c "
           "JOIN usuarios u ON u.id = c.auditor_id WHERE c.fecha = ?")
    args = [fecha]
    if turno:
        sql += " AND c.turno = ?"
        args.append(turno)
    fila = conn.execute(sql + " ORDER BY c.turno", args).fetchone()
    if not fila:
        raise LookupError(
            f"No hay control registrado el {fecha}"
            + (f" ({NOMBRE_TURNO.get(turno, turno)})" if turno else ""))

    estado = services.estado_control(conn, fila["id"])
    equip = estado["equipamiento"]

    nombre_turno = NOMBRE_TURNO.get(fila["turno"], fila["turno"])
    inf = Informe(
        titulo=f'Control diario · {nombre_turno.capitalize()} · Servicio de limpieza',
        subtitulo='Auditoría del servicio de limpieza — Operaciones A. Argentina',
        periodo=fila["periodo"],
        aeropuerto=db.get_config(conn, 'aeropuerto_nombre', 'Aeropuerto') + ' — '
                   + db.get_config(conn, 'aeropuerto_codigo', ''),
        concesionario=db.get_config(conn, 'concesionario', ''))

    inf.parrafo(
        f"Control del {_fecha_larga(fecha)} — {nombre_turno}. "
        f"Auditor: {fila['auditor']}. "
        + (f"Cerrado el {fila['cerrado_en']}."
           if fila["estado"] == "CERRADO"
           else "ATENCIÓN: el control todavía está abierto; los sectores sin "
                "confirmar no computan."), 9, GRIS)

    inf.dato_grande(
        'Cumplimiento de la recorrida', pct(estado['porcentaje_general']),
        f"{len(estado['sectores']) - len(estado['sectores_pendientes'])} de "
        f"{len(estado['sectores'])} sectores confirmados",
        VERDE if (estado['porcentaje_general'] or 0) >= 0.9 else AMBAR)

    if estado["sectores_pendientes"]:
        # Por nombre, no por clave técnica: el informe lo lee el contratista.
        pendientes = [s["nombre"] for s in estado["sectores"] if not s["confirmado"]]
        inf.aviso(
            "Sectores sin confirmar: " + ", ".join(pendientes)
            + ". No computan como 100%: quedan sin datos.",
            AMBAR, 'Control incompleto')

    # -- sectores --
    inf.titulo_seccion('Sectores')
    ETIQUETA = {"SIN_NOVEDADES": "Sin novedades", "CON_DESVIOS": "Con desvíos",
                "PENDIENTE": "Sin verificar"}
    inf.tabla([('Sector', 300, 'izq'), ('Estado', 120, 'izq'),
               ('Cumplimiento', 91, 'der')],
              [[s['nombre'], ETIQUETA.get(s['estado'], s['estado']),
                (pct(s['porcentaje']),
                 VERDE if s['porcentaje'] == 1 else
                 (GRIS if s['porcentaje'] is None else AMBAR), True)]
               for s in estado['sectores']])

    # -- equipamiento --
    if equip["exigidos"]:
        inf.titulo_seccion('Maquinarias y equipos')
        if equip["detalle_faltantes"]:
            inf.tabla([('Equipo fuera de servicio', 300, 'izq'),
                       ('Observación', 211, 'izq')],
                      [[(f['nombre'], ROJO, True), f['observacion'] or '']
                       for f in equip['detalle_faltantes']])
        else:
            inf.parrafo(f"Los {equip['exigidos']} equipos exigidos estuvieron "
                        'disponibles.', 9, GRIS)

    # -- desvíos con evidencia --
    desvios = [dict(f) for f in conn.execute(
        "SELECT d.*, i.nombre item, s.nombre sector, u.nombre auditor "
        "FROM desvios d JOIN items_limpieza i ON i.id = d.item_id "
        "JOIN sectores_limpieza s ON s.id = i.sector_id "
        "JOIN usuarios u ON u.id = d.creado_por "
        "WHERE d.control_id = ? ORDER BY s.orden, i.orden", (fila["id"],))]

    inf.titulo_seccion(f'Desvíos registrados ({len(desvios)})')
    if not desvios:
        inf.parrafo('No se registraron desvíos en este control.', 9, GRIS)
    else:
        SEVERIDAD = {"DESVIO_TOTAL": ("No cumple", ROJO),
                     "DESVIO_PARCIAL": ("Cumple a medias", AMBAR),
                     "NO_VERIFICABLE": ("No verificable", GRIS)}
        for d in desvios:
            etiqueta, color = SEVERIDAD.get(d["estado"], (d["estado"], NEGRO))
            inf.espacio(70)
            inf.doc.texto(inf.x, inf.y, f"{d['sector']} · {d['item']}", 10, True, NEGRO)
            inf.doc.texto_derecha(inf.x + inf.ancho_util, inf.y, etiqueta,
                                  8.5, True, color)
            inf.y += 15
            inf.y = inf.doc.parrafo(inf.x, inf.y, d["observacion"],
                                    inf.ancho_util, 9, False, NEGRO) + 6

            archivos = [f["archivo"] for f in conn.execute(
                "SELECT archivo FROM fotos WHERE entidad = 'desvio' AND entidad_id = ?",
                (d["id"],))]
            if archivos:
                _fila_fotos(inf, archivos, alto=190)
            else:
                inf.doc.texto(inf.x, inf.y, 'Sin evidencia fotográfica', 8, True, AMBAR)
                inf.y += 14

            inf.doc.linea(inf.x, inf.y, inf.x + inf.ancho_util, inf.y, 0.4, BORDE)
            inf.y += 14

    inf.firmas(['Auditor — Aeropuertos Argentina', 'Responsable Contratista'], emisor)
    return inf.generar()


DIAS_SEMANA = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado',
               'domingo']


def _fecha_larga(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{DIAS_SEMANA[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


# ==========================================================================
# Informe LoS (5.2)
# ==========================================================================

def informe_los(conn: sqlite3.Connection, periodo: str,
                emisor: dict | None = None) -> bytes:
    dash = services.dashboard_los(conn, periodo)
    rel = conn.execute(
        "SELECT r.*, u.nombre auditor FROM relevamientos_los r "
        "JOIN usuarios u ON u.id = r.auditor_id WHERE r.periodo = ? "
        "ORDER BY r.fecha DESC LIMIT 1", (periodo,)).fetchone()
    mediciones = services.mediciones_relevamiento(conn, rel['id']) if rel else {}

    inf = Informe(
        titulo='Niveles de Servicio (LoS)',
        subtitulo='Definición de los Niveles de Servicio REV.02 — Aeropuertos Argentina',
        periodo=periodo,
        aeropuerto=db.get_config(conn, 'aeropuerto_nombre', 'Aeropuerto') + ' — '
                   + db.get_config(conn, 'aeropuerto_codigo', ''),
        concesionario=db.get_config(conn, 'concesionario', ''))

    categoria = db.get_config(conn, 'aeropuerto_categoria', '')
    inf.parrafo(
        f'Evaluación de los 11 ítems del manual de Niveles de Servicio con los '
        f'parámetros correspondientes a la categoría {categoria}. '
        + (f"Relevamiento del {rel['fecha']} por {rel['auditor']}."
           if rel else 'Sin relevamiento registrado en el período.'), 9, GRIS)

    inf.titulo_seccion('Resultado global')
    inf.dato_grande(
        'Ítems que cumplen sobre los evaluados',
        pct(dash['porcentaje']),
        f"{dash['items_cumplen']} de {dash['items_evaluados']} evaluados · "
        f"{dash['items_aplicables']} aplicables",
        VERDE if (dash['porcentaje'] or 0) >= 0.9 else AMBAR)

    # Los avisos nombran los ítems como los ve el lector, no por su clave.
    nombres = {i['clave']: i['nombre'] for i in dash['items']}
    legible = lambda claves: ', '.join(nombres.get(c, c) for c in claves)  # noqa: E731

    if dash['items_sin_datos']:
        inf.aviso(
            f"{len(dash['items_sin_datos'])} ítem(s) sin datos en el período: "
            + legible(dash['items_sin_datos']) + '. No se computan como '
            'incumplimiento, pero tampoco como cumplimiento.',
            AMBAR, 'Ítems sin relevar')

    if dash['requieren_configuracion']:
        inf.aviso(
            'Los siguientes ítems no pueden evaluarse porque falta cargar su '
            'inventario físico en la configuración: '
            + legible(dash['requieren_configuracion']) + '.',
            ROJO, 'Inventario sin cargar')

    # -- cuadro resumen --
    inf.titulo_seccion('Estado por ítem')
    ETIQUETA = {'CUMPLE': 'Cumple', 'NO_CUMPLE': 'No cumple',
                'SIN_DATOS': 'Sin datos', 'NO_APLICA': 'No aplica'}
    filas = []
    for i in dash['items']:
        color = {'CUMPLE': VERDE, 'NO_CUMPLE': ROJO}.get(i['estado'], GRIS)
        filas.append([
            i['nombre'],
            (ETIQUETA[i['estado']], color, True),
            str(i['nc_abiertas']) if i['nc_abiertas'] else '—',
        ])
    inf.tabla([('Ítem del manual', 330, 'izq'), ('Estado', 110, 'der'),
               ('NC abiertas', 71, 'der')], filas)

    # -- detalle por ítem --
    inf.titulo_seccion('Detalle de las mediciones')
    hubo_detalle = False
    for i in dash['items']:
        med = mediciones.get(i['clave'])
        if not med or not med.get('resultado'):
            continue
        hubo_detalle = True
        _detalle_los(inf, i, med)

    if not hubo_detalle:
        inf.parrafo('No hay mediciones cargadas en el período.', 9, GRIS)

    inf.firmas(['Auditor — Aeropuertos Argentina', 'Responsable Contratista'],
               emisor)
    return inf.generar()


def _detalle_los(inf: Informe, item: dict, med: dict):
    """Desglose de un ítem: qué se midió y contra qué objetivo."""
    inf.espacio(50)
    color = VERDE if med['cumple'] else (ROJO if med['cumple'] is False else GRIS)
    inf.doc.texto(inf.x, inf.y, item['nombre'], 10, True, NEGRO)
    inf.doc.texto_derecha(inf.x + inf.ancho_util, inf.y,
                          'Cumple' if med['cumple'] else
                          ('No cumple' if med['cumple'] is False else 'Sin datos'),
                          8.5, True, color)
    inf.y += 15

    for linea in _lineas_resultado(item['clave'], med['resultado']):
        inf.espacio(14)
        inf.doc.texto(inf.x + 10, inf.y, linea[0], 8.5, False, NEGRO)
        inf.doc.texto_derecha(inf.x + inf.ancho_util, inf.y, linea[1], 8.5,
                              False, linea[2] if len(linea) > 2 else GRIS)
        inf.y += 13

    if med.get('observaciones'):
        inf.y = inf.doc.parrafo(inf.x + 10, inf.y + 2, 'Observaciones: '
                                + med['observaciones'], inf.ancho_util - 10,
                                8, False, GRIS) + 4

    # Evidencia agrupada por sub-ítem: una foto suelta no diría qué retrata
    # cuando hay varios criterios en grado C o D.
    por_sub = {}
    for f in med.get('fotos') or []:
        archivo, sub = (f, None) if isinstance(f, str) else (f['archivo'], f['subitem'])
        por_sub.setdefault(sub, []).append(archivo)

    for sub, archivos in por_sub.items():
        if sub:
            inf.espacio(20)
            inf.doc.texto(inf.x + 10, inf.y + 4,
                          sub.replace('_', ' ').capitalize(), 8.5, True, GRIS)
            inf.y += 16
        _fila_fotos(inf, archivos, alto=110)

    inf.doc.linea(inf.x, inf.y + 2, inf.x + inf.ancho_util, inf.y + 2, 0.4, BORDE)
    inf.y += 14


def _lineas_resultado(clave: str, r: dict) -> list[tuple]:
    """Traduce el resultado de cada ítem a filas legibles.

    Cada ítem tiene una forma distinta porque cada uno mide otra cosa; esto
    concentra en un solo lugar cómo se lee cada una.
    """
    ok = lambda c: VERDE if c else ROJO      # noqa: E731
    L = []

    if clave == 'banos':
        # `nucleos` es la forma anterior, cuando los artefactos fuera de
        # servicio se cargaban a mano desde LoS. Se sigue leyendo para que los
        # informes de períodos ya cerrados salgan igual que entonces.
        for n in r.get('nucleos', []):
            for grupo, d in (n.get('en_servicio', {}).get('grupos') or {}).items():
                if d['porcentaje'] is not None:
                    L.append((f"{n['nucleo']} · {grupo} en servicio",
                              f"{pct(d['porcentaje'])} (objetivo {pct(d['objetivo'], 0)})",
                              ok(d['cumple'])))

        servicio = r.get('en_servicio') or {}
        if servicio.get('dias_incumplen'):
            dias = servicio['dias_incumplen']
            L.append(("Artefactos en servicio",
                      f"{len(dias)} día(s) por debajo del objetivo "
                      f"(desde {dias[0]})", ROJO))
        elif servicio.get('cumple'):
            L.append(("Artefactos en servicio", "Todos los días en objetivo", VERDE))

        for equipo, d in ((r.get('limpieza') or {}).get('equipos') or {}).items():
            if d['porcentaje'] is not None and d['cumple'] is False:
                L.append((f"Limpieza · {equipo}",
                          f"{pct(d['porcentaje'])} (objetivo {pct(d['objetivo'], 0)})",
                          ROJO))
    elif clave == 'confort_termico':
        for z in r.get('zonas', []):
            L.append((f"{z['zona']} ({r.get('estacion', '').lower()})",
                      f"{z['temperatura']} °C (rango {z['rango'][0]}–{z['rango'][1]})",
                      ok(z['cumple'])))
    elif clave == 'iluminacion':
        for sector, d in (r.get('sectores') or {}).items():
            extra = ' · consecutivas en el mismo cono' if d.get('consecutivas_mismo_cono') else ''
            L.append((f"{sector}{extra}",
                      f"{pct(d['porcentaje'])} encendidas (objetivo {pct(d['objetivo'], 0)})",
                      ok(d['cumple'])))
    elif clave == 'infraestructura':
        for sub, d in (r.get('subitems') or {}).items():
            if d['grado'] and not d['cumple']:
                L.append((sub.replace('_', ' ').capitalize(),
                          f"Grado {d['grado']} — {d['prioridad_nc']}", ROJO))
        if not L:
            L.append(('Todos los sub-ítems en grado A o B', 'Cumple', VERDE))
    elif clave == 'asientos_preembarque':
        L.append(('Asientos utilizables',
                  f"{r['utilizables']} de {r['instalados']} (mínimo {r['minimo']})",
                  ok(r['cumple'])))
    elif clave == 'puntos_carga':
        for p in r.get('puertas', []):
            L.append((p['puerta'],
                      f"{p['operativas']} operativas de {p['requeridas']} requeridas",
                      ok(p['cumple'])))
    elif clave == 'medios_elevacion':
        for e in r.get('equipos', []):
            L.append((f"{e['equipo']} ({'con' if e['redundancia'] else 'sin'} redundancia)",
                      f"{pct(e['disponibilidad'])} · {e['horas_indisponible']} h "
                      f"(máx {e['indisp_max_mensual_hs']} h)", ok(e['cumple'])))
    elif clave == 'limpieza_terminal':
        for sub, d in (r.get('subitems') or {}).items():
            if d['grado'] and not d['cumple']:
                L.append((sub.replace('_', ' ').capitalize(),
                          f"Grado {d['grado']}", ROJO))
        if not L:
            L.append(('Todos los sub-ítems en grado A o B', 'Cumple', VERDE))
    elif clave == 'gel':
        for p in r.get('pruebas', []):
            etiqueta = 'Tiempo de conmutación'
            if p.get('fecha'):
                etiqueta += f" · {p['fecha']}"
            L.append((etiqueta,
                      f"{p['tiempo_medido_s']} s (máx {p['tiempo_maximo_s']} s)",
                      ok(p['cumple'])))
    elif clave == 'pista_rodajes':
        d = r.get('disponibilidad', {})
        L.append(('Disponibilidad en horario operativo',
                  f"{d.get('eventos_no_programados', 0)} evento(s) no programado(s)",
                  ok(d.get('cumple'))))
        for tipo in ('pista', 'rodaje'):
            p = r.get(tipo) or {}
            if p.get('cumple') is not None:
                L.append((f"Secciones de {tipo} con PCI > {p['umbral_pci']}",
                          f"{p['secciones_sobre_umbral']} de {p['secciones_totales']} "
                          f"({pct(p['proporcion'], 0)}, mínimo {pct(p['proporcion_min'], 0)})",
                          ok(p['cumple'])))
    return L


# ==========================================================================
# Exports CSV (5.3)
# ==========================================================================

def _csv(encabezados, filas) -> bytes:
    salida = io.StringIO()
    # QUOTE_ALL evita que un texto con coma o punto y coma rompa la columna,
    # que es lo habitual en las observaciones del auditor.
    escritor = csv.writer(salida, delimiter=';', quoting=csv.QUOTE_ALL)
    escritor.writerow(encabezados)
    escritor.writerows(filas)
    # BOM: sin esto Excel en Windows abre los acentos rotos.
    return '﻿'.encode('utf-8') + salida.getvalue().encode('utf-8')


def csv_desvios(conn: sqlite3.Connection, periodo: str) -> bytes:
    filas = conn.execute(
        "SELECT c.fecha, s.nombre sector, i.nombre item, d.estado, d.observacion, "
        "u.nombre auditor, d.creado_en, "
        "(SELECT COUNT(*) FROM fotos f WHERE f.entidad = 'desvio' "
        " AND f.entidad_id = d.id) fotos "
        "FROM desvios d "
        "JOIN controles_limpieza c ON c.id = d.control_id "
        "JOIN items_limpieza i ON i.id = d.item_id "
        "JOIN sectores_limpieza s ON s.id = i.sector_id "
        "JOIN usuarios u ON u.id = d.creado_por "
        "WHERE c.periodo = ? ORDER BY c.fecha, s.orden", (periodo,)).fetchall()
    return _csv(['Fecha', 'Sector', 'Ítem', 'Estado', 'Observación', 'Auditor',
                 'Registrado', 'Fotos'], [tuple(f) for f in filas])


def csv_controles(conn: sqlite3.Connection, periodo: str) -> bytes:
    """Un renglón por sector y día, con el % que aportó al mes."""
    resumen = services.resumen_mensual_limpieza(conn, periodo)
    # La clave de cada medición es 'fecha·turno' desde que hay dos recorridas
    # diarias. Se separa en dos columnas: pegado en una sola, el campo dejaba
    # de ser una fecha y rompía cualquier planilla que lo ordene o filtre.
    NOMBRE_TURNO = {'MANANA': 'Mañana', 'TARDE': 'Tarde'}
    filas = []
    for s in resumen['sectores']:
        for clave in sorted(s['dias']):
            fecha, _, turno = clave.partition('·')
            v = s['dias'][clave]
            filas.append((fecha, NOMBRE_TURNO.get(turno, turno), s['nombre'],
                          '' if v is None else f"{v * 100:.1f}".replace('.', ',')))
    return _csv(['Fecha', 'Turno', 'Sector', 'Cumplimiento %'], filas)


def csv_no_conformidades(conn: sqlite3.Connection, periodo: str) -> bytes:
    filas = conn.execute(
        "SELECT periodo, origen, sector, item, descripcion, prioridad, estado, "
        "creado_en, resuelto_en FROM no_conformidades WHERE periodo = ? "
        "ORDER BY creado_en", (periodo,)).fetchall()
    return _csv(['Período', 'Origen', 'Sector', 'Ítem', 'Descripción', 'Prioridad',
                 'Estado', 'Registrada', 'Resuelta'], [tuple(f) for f in filas])


def csv_los(conn: sqlite3.Connection, periodo: str) -> bytes:
    dash = services.dashboard_los(conn, periodo)
    ETIQUETA = {'CUMPLE': 'Cumple', 'NO_CUMPLE': 'No cumple',
                'SIN_DATOS': 'Sin datos', 'NO_APLICA': 'No aplica'}
    filas = [(periodo, i['nombre'], ETIQUETA[i['estado']],
              'Sí' if i['aplica'] else 'No', i['nc_abiertas'])
             for i in dash['items']]
    return _csv(['Período', 'Ítem LoS', 'Estado', 'Aplica', 'NC abiertas'], filas)


EXPORTS = {
    'desvios': (csv_desvios, 'desvios'),
    'controles': (csv_controles, 'controles-diarios'),
    'no-conformidades': (csv_no_conformidades, 'no-conformidades'),
    'los': (csv_los, 'niveles-de-servicio'),
}
