"""
Almacenamiento de la evidencia fotográfica.

Las fotos se guardan en una jerarquía que refleja de dónde salieron, en vez de
un único directorio plano. Con auditorías diarias durante años, un solo
directorio vuelve imposible ubicar un archivo a mano, respaldar un período o
entregar la evidencia de un mes:

    uploads/
      2026-07/                          <- período: se archiva o comprime entero
        limpieza/
          2026-07-15/                   <- día del control
            sanidad__piso__143052__a3f9.jpg
        los/
          infraestructura/
            2026-07-20__101533__b7c2.jpg

La ruta sola ya dice qué es la foto sin consultar la base. El sufijo aleatorio
evita colisiones entre dos hallazgos del mismo ítem en el mismo segundo.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime

MAX_FOTO = 8 * 1024 * 1024
EXTENSIONES = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "webp": ".webp"}


class ErrorFoto(ValueError):
    pass


def _slug(texto: str, largo: int = 28) -> str:
    """Fragmento de nombre seguro para el sistema de archivos."""
    if not texto:
        return "sin-nombre"
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return (t[:largo] or "sin-nombre")


def decodificar(data_url: str) -> tuple[bytes, str]:
    """Valida y decodifica una data URL. Devuelve (binario, extensión)."""
    if not isinstance(data_url, str) or "," not in data_url:
        raise ErrorFoto("Formato de foto inválido: se espera una data URL")

    cabecera, _, payload = data_url.partition(",")
    if not cabecera.startswith("data:image/"):
        raise ErrorFoto("Solo se aceptan imágenes")
    try:
        binario = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ErrorFoto("La foto no es base64 válido")
    if len(binario) > MAX_FOTO:
        raise ErrorFoto("La foto supera el tamaño máximo permitido")

    ext = EXTENSIONES.get(cabecera.split("/")[1].split(";")[0].lower())
    if not ext:
        raise ErrorFoto("Formato de imagen no soportado")
    return binario, ext


def ruta_relativa(contexto: dict, ext: str) -> str:
    """Arma la ruta donde vive la foto, a partir de su contexto.

    contexto acepta: periodo, fecha, modulo ('limpieza'|'los'), sector, item.
    Todo es opcional; lo que falte se reemplaza por un tramo neutro para que
    ninguna foto quede fuera del árbol.
    """
    ahora = datetime.now()
    fecha = contexto.get("fecha") or ahora.strftime("%Y-%m-%d")
    periodo = contexto.get("periodo") or fecha[:7]
    modulo = contexto.get("modulo") or "otros"
    hora = ahora.strftime("%H%M%S")
    sufijo = secrets.token_hex(2)

    partes = [periodo, modulo]
    if modulo == "limpieza":
        partes.append(fecha)
        nombre = "__".join(filter(None, [
            _slug(contexto.get("sector")),
            _slug(contexto.get("item")),
            hora, sufijo])) + ext
    else:
        partes.append(_slug(contexto.get("item"), 32))
        nombre = f"{fecha}__{hora}__{sufijo}{ext}"

    return os.path.join(*partes, nombre)


def guardar(conn: sqlite3.Connection, base_dir: str, entidad: str, entidad_id: int,
            data_url: str, contexto: dict | None = None,
            subitem: str | None = None) -> str:
    """Guarda la foto en disco y la registra. Devuelve la ruta relativa.

    `subitem` identifica qué se fotografió dentro de la medición (por ejemplo
    'cielorraso' dentro de infraestructura). Sin eso, con varios sub-ítems en
    grado C o D no se sabría a cuál corresponde cada foto.
    """
    binario, ext = decodificar(data_url)
    ctx = dict(contexto or {})
    if subitem:
        # El sub-ítem entra en el nombre del archivo: la ruta sola alcanza
        # para saber qué retrata.
        ctx["item"] = f"{ctx.get('item', '')}-{subitem}".strip("-")
    relativa = ruta_relativa(ctx, ext)

    # `base_dir` deja de ser necesariamente un directorio: si Supabase está
    # configurado, la evidencia va al bucket y la ruta relativa —que es lo que
    # se guarda en la base— no cambia. Ver `almacen.py`.
    import almacen
    almacen.obtener(base_dir).guardar(relativa, binario)

    conn.execute(
        "INSERT INTO fotos (entidad, entidad_id, subitem, archivo) VALUES (?,?,?,?)",
        (entidad, entidad_id, subitem, relativa))
    return relativa


def ruta_segura(base_dir: str, archivo: str) -> str:
    """Resuelve la ruta de una foto sin permitir salir del directorio base.

    `archivo` ahora contiene barras (es una ruta relativa), así que no alcanza
    con rechazar '/': hay que comprobar que el resultado siga dentro de base.
    """
    if not archivo or archivo.startswith(("/", "\\")):
        raise ErrorFoto("Ruta de foto inválida")
    destino = os.path.normpath(os.path.join(base_dir, archivo))
    base = os.path.normpath(base_dir)
    if not destino.startswith(base + os.sep) and destino != base:
        raise ErrorFoto("Ruta de foto inválida")
    return destino


def contexto_desvio(conn: sqlite3.Connection, desvio_id: int) -> dict:
    """Contexto de una foto de desvío, para ubicarla en el árbol."""
    fila = conn.execute(
        "SELECT c.fecha, c.periodo, s.nombre sector, i.nombre item "
        "FROM desvios d "
        "JOIN controles_limpieza c ON c.id = d.control_id "
        "JOIN items_limpieza i ON i.id = d.item_id "
        "JOIN sectores_limpieza s ON s.id = i.sector_id "
        "WHERE d.id = ?", (desvio_id,)).fetchone()
    if not fila:
        return {"modulo": "limpieza"}
    return {"modulo": "limpieza", "fecha": fila["fecha"], "periodo": fila["periodo"],
            "sector": fila["sector"], "item": fila["item"]}


def contexto_los(conn: sqlite3.Connection, medicion_id: int) -> dict:
    fila = conn.execute(
        "SELECT r.periodo, m.item_clave FROM los_mediciones m "
        "JOIN relevamientos_los r ON r.id = m.relevamiento_id "
        "WHERE m.id = ?", (medicion_id,)).fetchone()
    if not fila:
        return {"modulo": "los"}
    return {"modulo": "los", "periodo": fila["periodo"], "item": fila["item_clave"]}


def migrar_planas(conn: sqlite3.Connection, base_dir: str) -> list[str]:
    """Reubica las fotos guardadas con el esquema plano anterior.

    Idempotente: las que ya están en subcarpetas se saltean.
    """
    movidas = []
    for fila in conn.execute("SELECT id, entidad, entidad_id, archivo FROM fotos"):
        archivo = fila["archivo"]
        if os.sep in archivo or "/" in archivo:
            continue                              # ya migrada

        origen = os.path.join(base_dir, archivo)
        if not os.path.isfile(origen):
            continue                              # registro sin archivo

        ext = os.path.splitext(archivo)[1] or ".jpg"
        contexto = (contexto_desvio(conn, fila["entidad_id"])
                    if fila["entidad"] == "desvio"
                    else contexto_los(conn, fila["entidad_id"]))
        relativa = ruta_relativa(contexto, ext)

        destino = os.path.join(base_dir, relativa)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        os.rename(origen, destino)
        conn.execute("UPDATE fotos SET archivo = ? WHERE id = ?",
                     (relativa, fila["id"]))
        movidas.append(f"{archivo} -> {relativa}")

    if movidas:
        conn.commit()
    return movidas
