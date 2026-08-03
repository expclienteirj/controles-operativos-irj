"""Envoltorio de conexión Postgres con la forma de `sqlite3.Connection`.

El resto del backend está escrito contra la API de `sqlite3`: llama a
`conn.execute(sql, params)` y espera de vuelta algo iterable con filas que se
indexan por nombre de columna —y, en algún caso, por posición—, además de
`cur.lastrowid` tras un INSERT. Esta clase ofrece exactamente eso sobre
psycopg, para que `services.py`, `api.py`, `informes.py` y `seed.py` no se
enteren de qué motor tienen debajo.

Lo que NO se replica a propósito: las PRAGMA se descartan, porque en Postgres
las claves foráneas siempre rigen, que es lo único que la app pedía con ellas.
"""

from __future__ import annotations

import re

import psycopg

import pgcompat


class Fila(dict):
    """Fila con el comportamiento de `sqlite3.Row`, no el de un dict.

    Dos diferencias con un dict común, y las dos importan:

    - Se indexa por nombre o por posición. El acceso posicional se usa en un
      solo lugar (`seed.py`), pero replicarlo sale más barato que reescribir
      código probado.
    - **Al iterarla devuelve los valores, no las claves.** `informes.py` hace
      `tuple(fila)` para volcar cada fila al CSV: con un dict común, los
      exports salían con los nombres de las columnas repetidos en lugar de los
      datos. `dict(fila)` sigue funcionando porque usa `keys()`.
    """

    def __getitem__(self, clave):
        if isinstance(clave, int):
            return list(self.values())[clave]
        return super().__getitem__(clave)

    def __iter__(self):
        return iter(self.values())


_RE_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][\w]*)", re.IGNORECASE)

# Tablas con columna `id`, por conexión. Se consulta una vez: el esquema no
# cambia mientras la app corre.
_CACHE_TABLAS_ID: dict[int, set[str]] = {}


def _adaptar(params):
    """Convierte los booleanos de Python a 0/1, como hace SQLite.

    El esquema guarda las banderas en columnas INTEGER a propósito, para que
    las comparaciones `activo = 1` repartidas por el código sigan valiendo.
    Postgres tipa un `True` de Python como boolean y rechaza insertarlo en una
    columna entera; SQLite lo convertía solo. La conversión va acá y no en la
    API para que ningún endpoint tenga que acordarse de hacerla.
    """
    return tuple(int(v) if isinstance(v, bool) else v for v in params)


def _tabla_insertada(sql: str) -> str | None:
    """Nombre de la tabla de un INSERT, o None si no lo es o ya trae RETURNING."""
    if re.search(r"\bRETURNING\b", sql, re.IGNORECASE):
        return None
    m = _RE_INSERT.match(sql)
    return m.group(1).lower() if m else None


def _tablas_con_id(conn) -> set[str]:
    clave = id(conn)
    if clave not in _CACHE_TABLAS_ID:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'id'")
            _CACHE_TABLAS_ID[clave] = {f[0].lower() for f in cur.fetchall()}
    return _CACHE_TABLAS_ID[clave]


def _fabrica_filas(cursor):
    """Row factory de psycopg que produce `Fila`."""
    columnas = [d.name for d in cursor.description] if cursor.description else []

    def construir(valores):
        return Fila(zip(columnas, valores))

    return construir


class CursorPG:
    """Cursor con la interfaz que usa el backend. `execute` devuelve el cursor."""

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor(row_factory=_fabrica_filas)
        self._ultimo_id = None

    def execute(self, sql: str, params=()):
        sql_pg = pgcompat.traducir(sql, con_parametros=bool(params))
        tabla = _tabla_insertada(sql_pg)
        devuelve_id = tabla is not None and tabla in _tablas_con_id(self._conn)
        if devuelve_id:
            sql_pg += " RETURNING id"

        self._cur.execute(sql_pg, _adaptar(params) if params else None)

        self._ultimo_id = None
        if devuelve_id:
            # Se consume acá para que el id quede disponible en `lastrowid` sin
            # que el llamador tenga que saber que la consulta cambió. Un INSERT
            # con ON CONFLICT DO NOTHING que no insertó nada no devuelve fila:
            # en ese caso `lastrowid` queda en None, que es la verdad.
            fila = self._cur.fetchone()
            self._ultimo_id = fila["id"] if fila else None
        return self

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self):
        # `for fila in conn.execute(...)` es el patrón dominante del backend.
        return iter(self._cur) if self._cur.description else iter(())

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        """Id de la última fila insertada por este cursor.

        Sale del `RETURNING id` que agrega `execute`, no de `lastval()`.
        `lastval()` devuelve el último valor generado por *cualquier* secuencia
        de la sesión: si el INSERT era un OR IGNORE que no insertó nada, o si
        otra tabla había usado su secuencia antes, devolvía un id ajeno y la
        fila hija terminaba apuntando a un padre inexistente.
        """
        return self._ultimo_id


class ConexionPG:
    """Se comporta como `sqlite3.Connection` para lo que el backend usa."""

    def __init__(self, dsn: str):
        self._conn = psycopg.connect(dsn)
        self._conn.autocommit = False
        # psycopg prepara automáticamente una consulta tras repetirla unas
        # pocas veces. El pooler de Supabase en modo transaction (PgBouncer,
        # puerto 6543) no soporta sentencias preparadas: la app funcionaría un
        # rato y después empezaría a fallar sola, con un error difícil de
        # relacionar con esto. None desactiva la preparación automática.
        self._conn.prepare_threshold = None

    def execute(self, sql: str, params=()):
        return CursorPG(self._conn).execute(sql, params)

    def cursor(self):
        return CursorPG(self._conn)

    def executescript(self, script: str):
        """Ejecuta un bloque de sentencias. Solo se usa para crear el esquema."""
        with self._conn.cursor() as cur:
            cur.execute(script)
        return self

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    @property
    def cerrada(self) -> bool:
        return self._conn.closed

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
