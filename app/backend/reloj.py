"""El reloj de la aplicación: hora de Argentina, siempre.

Todo lo que el sistema guarda o decide por fecha se mide en hora local del
aeropuerto. El servidor no es una referencia confiable: en Vercel corre en UTC,
y `date.today()` pasa a "mañana" a las 21:00 locales, justo cuando termina la
operación. Esa diferencia no es cosmética. La regla de oro del sistema —no se
abre una recorrida de un día futuro— se evalúa contra "hoy", y las marcas de
tiempo que se muestran en pantalla y en el informe mensual salían tres horas
corridas.

Por eso nadie usa `date.today()`, `datetime.now()` ni `datetime.utcnow()`: usa
este módulo. Un test lo hace cumplir (`test_reloj.py`), porque el olvido es
silencioso: en la máquina de desarrollo, que está en hora argentina, ambas
formas dan lo mismo y el error aparece recién en producción.

Argentina no tiene horario de verano desde 2009, así que alcanza con un
desplazamiento fijo de UTC−3. Se evita a propósito `zoneinfo`: depende de que el
sistema operativo traiga la base de zonas horarias, y un entorno sin ella
rompería el arranque en vez de dar la hora.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

DESPLAZAMIENTO_HORAS = -3
ZONA = timezone(timedelta(hours=DESPLAZAMIENTO_HORAS))

# Mismo formato que produce `datetime('now')` de SQLite, que es el que usa toda
# la base: las marcas son TEXT y la app las compara y ordena como cadenas.
FORMATO_MARCA = "%Y-%m-%d %H:%M:%S"

# Nombre de la zona en Postgres, que sí trae su propia base de zonas horarias.
ZONA_POSTGRES = "America/Argentina/Buenos_Aires"


def ahora() -> datetime:
    """Fecha y hora actuales en Argentina, sin zona adjunta (naive)."""
    return datetime.now(ZONA).replace(tzinfo=None)


def hoy() -> date:
    """El día de hoy en Argentina."""
    return ahora().date()


def marca() -> str:
    """Ahora como texto 'AAAA-MM-DD HH:MM:SS', el formato de la base."""
    return ahora().strftime(FORMATO_MARCA)
