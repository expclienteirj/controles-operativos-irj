"""Almacenamiento de los archivos de evidencia, en disco o en Supabase Storage.

Mismo patrón que `db.py`: el disco local sigue siendo el default y no cambia
nada; si están definidas las variables de Supabase, la evidencia va al bucket.
El resto del backend no distingue — `fotos.guardar()`, el endpoint que sirve
las fotos y el armado del PDF hablan con esta interfaz.

Variables de entorno para usar Supabase (ninguna se escribe en el código):

    SUPABASE_URL          https://<proyecto>.supabase.co
    SUPABASE_SERVICE_KEY  clave de servicio; NUNCA va al frontend
    SUPABASE_BUCKET       nombre del bucket (default: 'evidencia')

Se habla el REST de Storage con urllib en vez del SDK oficial: son dos
llamadas y evita sumar otra dependencia a un backend que hoy tiene dos.
"""

from __future__ import annotations

import mimetypes
import os
import urllib.error
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "evidencia")

TIEMPO_LIMITE = 20        # segundos por operación contra Storage


class ErrorAlmacen(Exception):
    pass


def ruta_valida(relativa: str) -> str:
    """Rechaza rutas que intenten salir del árbol de evidencia.

    Vale para los dos backends: en disco evita escribir fuera de uploads, y en
    Supabase evita armar una URL que apunte a otra carpeta del bucket.
    """
    if not relativa or relativa.startswith(("/", "\\")):
        raise ErrorAlmacen("Ruta de foto inválida")
    normalizada = os.path.normpath(relativa)
    if normalizada.startswith("..") or os.path.isabs(normalizada):
        raise ErrorAlmacen("Ruta de foto inválida")
    return normalizada.replace(os.sep, "/")


class AlmacenLocal:
    """Disco local. Es el comportamiento histórico, sin cambios."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def _absoluta(self, relativa: str) -> str:
        return os.path.join(self.base_dir, ruta_valida(relativa))

    def guardar(self, relativa: str, binario: bytes) -> None:
        destino = self._absoluta(relativa)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, "wb") as f:
            f.write(binario)

    def leer(self, relativa: str) -> bytes | None:
        destino = self._absoluta(relativa)
        if not os.path.isfile(destino):
            return None
        with open(destino, "rb") as f:
            return f.read()

    def existe(self, relativa: str) -> bool:
        return os.path.isfile(self._absoluta(relativa))


class AlmacenSupabase:
    """Bucket de Supabase Storage por su API REST."""

    def __init__(self, url: str, clave: str, bucket: str):
        self.base = url.rstrip("/")
        self.clave = clave
        self.bucket = bucket

    def _url(self, relativa: str) -> str:
        return f"{self.base}/storage/v1/object/{self.bucket}/{ruta_valida(relativa)}"

    def _pedir(self, metodo: str, relativa: str, datos: bytes | None = None,
               tipo: str | None = None):
        pedido = urllib.request.Request(self._url(relativa), data=datos,
                                        method=metodo)
        pedido.add_header("Authorization", f"Bearer {self.clave}")
        if tipo:
            pedido.add_header("Content-Type", tipo)
            # Reintentar una foto no debe fallar por existir: la ruta ya lleva
            # un sufijo aleatorio, así que una colisión es un reintento.
            pedido.add_header("x-upsert", "true")
        return urllib.request.urlopen(pedido, timeout=TIEMPO_LIMITE)

    def guardar(self, relativa: str, binario: bytes) -> None:
        tipo = mimetypes.guess_type(relativa)[0] or "image/jpeg"
        try:
            self._pedir("POST", relativa, binario, tipo).close()
        except urllib.error.HTTPError as e:
            raise ErrorAlmacen(
                f"No se pudo guardar la evidencia en Storage ({e.code})") from e
        except urllib.error.URLError as e:
            raise ErrorAlmacen(
                f"Storage inaccesible al guardar la evidencia: {e.reason}") from e

    def leer(self, relativa: str) -> bytes | None:
        try:
            with self._pedir("GET", relativa) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise ErrorAlmacen(f"No se pudo leer la evidencia ({e.code})") from e
        except urllib.error.URLError as e:
            raise ErrorAlmacen(f"Storage inaccesible: {e.reason}") from e

    def existe(self, relativa: str) -> bool:
        return self.leer(relativa) is not None


def usa_supabase() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def obtener(base_dir: str):
    """Devuelve el almacén configurado. Disco local salvo que Supabase esté puesto."""
    if usa_supabase():
        return AlmacenSupabase(SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_BUCKET)
    return AlmacenLocal(base_dir)
