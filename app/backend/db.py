"""Acceso a la base: conexión, creación del esquema y bootstrap inicial.

Soporta dos motores. SQLite es el de siempre y sigue siendo el default. Si está
definida la variable de entorno `IRJ_DB_URL`, se usa Postgres a través de la
capa de compatibilidad (`pgcompat`, `pgconn`), que hace que el mismo SQL —el
que los 540 tests ya cubren— funcione sobre los dos.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("IRJ_DB", os.path.join(BASE_DIR, "data", "irj.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

# Cadena de conexión a Postgres. Vacía = SQLite. Nunca se escribe en el código:
# la pone el entorno (.env local, panel del host en producción).
DB_URL = os.environ.get("IRJ_DB_URL", "")

PBKDF2_ITERACIONES = 200_000


def usa_postgres(url: str | None = None) -> bool:
    return bool(url if url is not None else DB_URL)


def _errores_integridad() -> tuple:
    """Excepciones de violación de integridad, sea cual sea el motor.

    Los dos drivers siguen la PEP 249 y definen `IntegrityError`, pero son
    clases distintas: atrapar solo la de sqlite3 hacía que sobre Postgres un
    duplicado se escapara como error genérico y la API respondiera 500 en vez
    de 409.
    """
    errores = [sqlite3.IntegrityError]
    try:
        import psycopg
        errores.append(psycopg.errors.IntegrityError)
    except ImportError:
        pass
    return tuple(errores)


ERRORES_INTEGRIDAD = _errores_integridad()


def hash_password(password: str, salt: str | None = None) -> str:
    """PBKDF2-SHA256. Formato almacenado: pbkdf2$<iteraciones>$<salt>$<hash>."""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(),
                             PBKDF2_ITERACIONES)
    return f"pbkdf2${PBKDF2_ITERACIONES}${salt}${dk.hex()}"


def verificar_password(password: str, almacenado: str) -> bool:
    try:
        _, iteraciones, salt, esperado = almacenado.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(),
                                 int(iteraciones))
        return secrets.compare_digest(dk.hex(), esperado)
    except (ValueError, AttributeError):
        return False


def conectar(path: str | None = None, url: str | None = None):
    """Abre la conexión al motor que corresponda.

    `url` (o `IRJ_DB_URL`) manda: si hay cadena de Postgres se usa esa y `path`
    se ignora. Devuelve algo que se comporta como `sqlite3.Connection` en los
    dos casos, así que el resto del backend no distingue.
    """
    url = url if url is not None else DB_URL
    if url:
        import pgconn
        return pgconn.ConexionPG(url)

    path = path or DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ==========================================================================
# Reuso de conexiones
# ==========================================================================
#
# Abrir una conexión a Postgres no es gratis: contra el pooler de Supabase son
# un handshake TLS más la autenticación, del orden de 100 ms. La API abría una
# por request y la cerraba al terminar, así que ese costo se pagaba en cada
# llamada —incluso en las que resuelven con dos consultas.
#
# El proceso sobrevive entre requests en los dos despliegues (el
# ThreadingHTTPServer de la Mac y el contenedor tibio de Vercel), así que la
# conexión se puede guardar y volver a usar. Se guarda una lista y no una sola
# porque el servidor local atiende con varios hilos y psycopg no admite que dos
# los compartan.
#
# SQLite queda afuera a propósito: abrir un archivo local no cuesta nada y
# compartir la conexión entre hilos traería problemas sin ninguna ganancia.

POOL_MAX = 4

# Tras este tiempo sin usarse, la conexión se verifica antes de entregarla: el
# pooler puede haberla cerrado del otro lado sin que este proceso se entere, y
# eso no se nota hasta que falla la primera consulta del request.
SEGUNDOS_VERIFICAR = 30

_pool: list = []
_pool_lock = None


def _lock():
    global _pool_lock
    if _pool_lock is None:
        import threading
        _pool_lock = threading.Lock()
    return _pool_lock


def _viva(conn, ocioso: float) -> bool:
    """¿La conexión sigue sirviendo? Verifica solo si estuvo ociosa un rato."""
    if conn.cerrada:
        return False
    if ocioso < SEGUNDOS_VERIFICAR:
        return True
    try:
        conn.execute("SELECT 1").fetchone()
        return True
    except Exception:                               # noqa: BLE001
        return False


def tomar_conexion(path: str | None = None, url: str | None = None):
    """Conexión lista para atender un request. Devolverla con `devolver_conexion`."""
    import time
    url = url if url is not None else DB_URL
    if not url:
        return conectar(path, url)

    while True:
        with _lock():
            if not _pool:
                break
            conn, desde = _pool.pop()
        # Fuera del lock: verificar puede costar una ida y vuelta a la base y
        # no hay razón para que eso bloquee a otro hilo que quiere una conexión.
        if _viva(conn, time.time() - desde):
            return conn
        try:
            conn.close()
        except Exception:                           # noqa: BLE001
            pass
    return conectar(path, url)


def devolver_conexion(conn) -> None:
    """Cierra la conexión, o la guarda para el próximo request si conviene."""
    import time
    if conn is None:
        return
    if not usa_postgres() or conn.__class__.__name__ != "ConexionPG":
        conn.close()
        return

    # Cualquier transacción a medias muere acá: lo que el request no confirmó
    # no puede aparecer en el siguiente, que es lo que pasaría si la conexión
    # volviera al pool con una transacción abierta.
    try:
        conn.rollback()
    except Exception:                               # noqa: BLE001
        try:
            conn.close()
        except Exception:                           # noqa: BLE001
            pass
        return

    with _lock():
        if len(_pool) < POOL_MAX and not conn.cerrada:
            _pool.append((conn, time.time()))
            return
    conn.close()


def cerrar_pool() -> None:
    """Cierra lo guardado. Para los tests y para un apagado ordenado."""
    with _lock():
        guardadas, _pool[:] = list(_pool), []
    for conn, _ in guardadas:
        try:
            conn.close()
        except Exception:                           # noqa: BLE001
            pass


def crear_esquema(conn) -> None:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        ddl = f.read()

    if _es_postgres(conn):
        import pgcompat
        # Las funciones primero: el esquema las usa en los DEFAULT.
        conn.executescript(pgcompat.FUNCIONES_SQL)
        conn.commit()
        _crear_tablas_pg(conn, pgcompat.sentencias(pgcompat.traducir_esquema(ddl)))
        # `migrar()` son recetas de reconstrucción propias de SQLite, para
        # actualizar bases viejas en uso. Una base Postgres se crea con el
        # esquema ya en su forma final, así que no corresponde aplicarlas.
        conn.commit()
        return

    conn.executescript(ddl)
    migrar(conn)
    conn.commit()


def _es_postgres(conn) -> bool:
    return conn.__class__.__name__ == "ConexionPG"


def _crear_tablas_pg(conn, sentencias: list[str]) -> None:
    """Ejecuta el DDL en pasadas, tolerando referencias hacia adelante.

    SQLite resuelve las claves foráneas de forma perezosa, así que el esquema
    puede declarar una referencia a una tabla que se define más abajo (hoy pasa
    con artefacto_baja → nucleos_sanitarios). Postgres exige que la tabla
    referenciada ya exista.

    En vez de reordenar `schema.sql` —que dejaría el orden como un requisito
    tácito, fácil de romper sin que nadie lo note— se reintenta lo que falla
    hasta que una pasada no logra ningún avance. Si en ese punto queda algo
    pendiente, es un error real y se levanta.
    """
    pendientes = list(sentencias)
    while pendientes:
        fallaron, ultimo_error = [], None
        for sentencia in pendientes:
            try:
                conn.executescript(sentencia)
                conn.commit()
            except Exception as e:                      # noqa: BLE001
                conn.rollback()
                fallaron.append(sentencia)
                ultimo_error = e
        if len(fallaron) == len(pendientes):
            raise RuntimeError(
                f"No se pudo crear el esquema; {len(fallaron)} sentencia(s) "
                f"sin resolver. Último error: {ultimo_error}")
        pendientes = fallaron


def migrar(conn: sqlite3.Connection) -> list[str]:
    """Ajusta bases creadas con versiones anteriores del esquema.

    `CREATE TABLE IF NOT EXISTS` no toca las tablas que ya existen, así que un
    cambio de columnas no llega solo a una base en uso. Cada migración se
    escribe para poder ejecutarse muchas veces sin efecto.
    """
    aplicadas = []

    def columnas(tabla):
        return {f["name"] for f in conn.execute(f"PRAGMA table_info({tabla})")}

    # El ítem 4 pasó de medir horas máquina a medir equipos disponibles: las
    # horas no eran exigibles por pliego ni medibles en el aeropuerto.
    cols = columnas("periodo_datos")
    for vieja in ("horas_maquina_programadas", "horas_maquina_no_disp"):
        if vieja in cols:
            conn.execute(f"ALTER TABLE periodo_datos DROP COLUMN {vieja}")
            aplicadas.append(f"periodo_datos: baja de {vieja}")

    # `horas_hombre_perdidas` nació NOT NULL DEFAULT 0, lo que volvía
    # indistinguible "nadie lo cargó" de "se constató que no se perdió ninguna
    # hora". El primero regalaba un 100% en el ítem que pesa 40%. SQLite no
    # permite quitar un NOT NULL con ALTER TABLE, así que hay que reconstruir.
    #
    # Los valores ya cargados se copian tal cual: un cero viejo puede haber
    # sido deliberado o el default, y no hay manera de saberlo. Convertirlos a
    # NULL sería afirmar que nadie los cargó, que es otra invención.
    info = {f["name"]: f for f in conn.execute("PRAGMA table_info(periodo_datos)")}
    if info.get("horas_hombre_perdidas") and info["horas_hombre_perdidas"]["notnull"]:
        conn.execute("""
            CREATE TABLE periodo_datos_nueva (
                periodo                   TEXT PRIMARY KEY,
                horas_hombre_programadas  REAL,
                horas_hombre_perdidas     REAL,
                documentacion_verificada  INTEGER NOT NULL DEFAULT 0,
                hallazgos_documentacion   INTEGER NOT NULL DEFAULT 0,
                ley_19587_verificada      INTEGER NOT NULL DEFAULT 0,
                hallazgos_ley_19587       INTEGER NOT NULL DEFAULT 0,
                monto_adjudicado          REAL,
                cerrado                   INTEGER NOT NULL DEFAULT 0
            )""")
        conn.execute("""
            INSERT INTO periodo_datos_nueva
            SELECT periodo, horas_hombre_programadas, horas_hombre_perdidas,
                   documentacion_verificada, hallazgos_documentacion,
                   ley_19587_verificada, hallazgos_ley_19587,
                   monto_adjudicado, cerrado
            FROM periodo_datos""")
        conn.execute("DROP TABLE periodo_datos")
        conn.execute("ALTER TABLE periodo_datos_nueva RENAME TO periodo_datos")
        aplicadas.append("periodo_datos: horas_hombre_perdidas admite sin dato")

    # La evidencia LoS pasó a identificar qué sub-ítem retrata.
    if "subitem" not in columnas("fotos"):
        conn.execute("ALTER TABLE fotos ADD COLUMN subitem TEXT")
        aplicadas.append("fotos: alta de subitem")

    if "registrado_en" not in columnas("equipamiento_faltante"):
        # Sin DEFAULT: SQLite no admite uno no constante en ALTER TABLE.
        conn.execute("ALTER TABLE equipamiento_faltante ADD COLUMN registrado_en TEXT")
        conn.execute("UPDATE equipamiento_faltante "
                     "SET registrado_en = datetime('now') WHERE registrado_en IS NULL")
        aplicadas.append("equipamiento_faltante: alta de registrado_en")

    # El control pasó de uno por día a uno por turno (mañana y tarde). La tabla
    # vieja tiene UNIQUE(fecha), que SQLite no permite quitar con ALTER TABLE:
    # hay que reconstruirla. Los controles ya cargados quedan como turno mañana,
    # que es lo único que se puede afirmar de ellos.
    if "turno" not in columnas("controles_limpieza"):
        # Receta de reconstrucción de SQLite. Sin desactivar las foreign keys,
        # el DROP arrastraría en cascada desvíos, confirmaciones y equipamiento
        # de todos los controles ya cargados. `legacy_alter_table` evita que el
        # RENAME reescriba las referencias de las tablas hijas apuntándolas a
        # la tabla temporal.
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.executescript("""
            CREATE TABLE controles_limpieza_nueva (
                id          INTEGER PRIMARY KEY,
                fecha       TEXT NOT NULL,
                turno       TEXT NOT NULL DEFAULT 'MANANA'
                            CHECK (turno IN ('MANANA', 'TARDE')),
                periodo     TEXT NOT NULL,
                auditor_id  INTEGER NOT NULL REFERENCES usuarios(id),
                iniciado_en TEXT NOT NULL DEFAULT (datetime('now')),
                cerrado_en  TEXT,
                estado      TEXT NOT NULL DEFAULT 'ABIERTO'
                            CHECK (estado IN ('ABIERTO', 'CERRADO')),
                UNIQUE (fecha, turno),
                CHECK (periodo = substr(fecha, 1, 7))
            );
            INSERT INTO controles_limpieza_nueva
                (id, fecha, turno, periodo, auditor_id, iniciado_en, cerrado_en, estado)
                -- COALESCE: la columna es NOT NULL en la tabla nueva y una base
                -- vieja puede traerla vacía; sin esto la migración aborta y la
                -- app no arranca.
                SELECT id, fecha, 'MANANA', periodo, auditor_id,
                       COALESCE(iniciado_en, datetime('now')),
                       cerrado_en, COALESCE(estado, 'ABIERTO')
                  FROM controles_limpieza;
            DROP TABLE controles_limpieza;
            ALTER TABLE controles_limpieza_nueva RENAME TO controles_limpieza;
            CREATE INDEX IF NOT EXISTS idx_controles_periodo
                ON controles_limpieza(periodo);
        """)
        conn.execute("PRAGMA legacy_alter_table = OFF")
        # Se verifica antes de volver a exigirlas: si la reconstrucción dejó
        # huérfanos, es preferible saberlo acá y no en la primera consulta.
        rotas = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.execute("PRAGMA foreign_keys = ON")
        if rotas:
            raise RuntimeError(
                f"La migración de turnos dejó {len(rotas)} referencia(s) rotas")
        aplicadas.append("controles_limpieza: alta de turno (mañana/tarde)")

    # Periodicidad por ítem LoS: no todos son diarizables. El valor real lo
    # fija el seed; acá solo se crea la columna.
    if "periodicidad" not in columnas("los_items"):
        conn.execute("ALTER TABLE los_items ADD COLUMN periodicidad TEXT "
                     "NOT NULL DEFAULT 'MENSUAL'")
        aplicadas.append("los_items: alta de periodicidad")

    # Los ítems diarios guardan una medición por día. Las que ya existen son
    # mensuales y se quedan con fecha '' (no NULL: en SQLite dos NULL no
    # colisionan y el UNIQUE dejaría entrar duplicados del mismo ítem).
    #
    # Hay que reconstruir la tabla y no basta con ALTER TABLE: la vieja tiene
    # UNIQUE(relevamiento_id, item_clave), cuyo índice implícito no se puede
    # borrar y seguiría impidiendo dos fechas del mismo ítem.
    if "fecha" not in columnas("los_mediciones"):
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.executescript("""
            CREATE TABLE los_mediciones_nueva (
                id              INTEGER PRIMARY KEY,
                relevamiento_id INTEGER NOT NULL
                                REFERENCES relevamientos_los(id) ON DELETE CASCADE,
                item_clave      TEXT NOT NULL REFERENCES los_items(clave),
                fecha           TEXT NOT NULL DEFAULT '',
                datos           TEXT NOT NULL,
                resultado       TEXT,
                cumple          INTEGER,
                observaciones   TEXT,
                creado_en       TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (relevamiento_id, item_clave, fecha)
            );
            INSERT INTO los_mediciones_nueva
                (id, relevamiento_id, item_clave, fecha, datos, resultado,
                 cumple, observaciones, creado_en)
                SELECT id, relevamiento_id, item_clave, '', datos, resultado,
                       cumple, observaciones, COALESCE(creado_en, datetime('now'))
                  FROM los_mediciones;
            DROP TABLE los_mediciones;
            ALTER TABLE los_mediciones_nueva RENAME TO los_mediciones;
            CREATE INDEX IF NOT EXISTS idx_los_med_relev
                ON los_mediciones(relevamiento_id);
        """)
        conn.execute("PRAGMA legacy_alter_table = OFF")
        rotas = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.execute("PRAGMA foreign_keys = ON")
        if rotas:
            raise RuntimeError(
                f"La migración de mediciones LoS dejó {len(rotas)} referencia(s) rotas")
        aplicadas.append("los_mediciones: alta de fecha")

    # Seguimiento de no conformidades entre auditorías: quién la cerró, qué
    # constató y de qué día venía (el período solo no alcanza para arrastrar
    # una NC del último día del mes al primero del siguiente).
    cols = columnas("no_conformidades")
    for col, tipo in (("fecha_origen", "TEXT"), ("resuelto_por", "INTEGER"),
                      ("resolucion", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE no_conformidades ADD COLUMN {col} {tipo}")
            aplicadas.append(f"no_conformidades: alta de {col}")

    # Las NC ya cargadas no tienen fecha: se deduce del control que las originó
    # y, si no hay desvío asociado, del día en que se registraron.
    if "no_conformidades: alta de fecha_origen" in aplicadas:
        conn.execute(
            "UPDATE no_conformidades SET fecha_origen = COALESCE("
            "  (SELECT c.fecha FROM desvios d "
            "     JOIN controles_limpieza c ON c.id = d.control_id "
            "    WHERE d.id = no_conformidades.desvio_id), date(creado_en)) "
            "WHERE fecha_origen IS NULL")

    if aplicadas:
        conn.commit()

    # La evidencia pasó de un directorio plano a un árbol por período: ver
    # fotos.py. Se hace acá para que una base en uso quede consistente al
    # arrancar, sin intervención manual.
    try:
        import api
        import fotos
        movidas = fotos.migrar_planas(conn, api.UPLOADS_DIR)
        if movidas:
            aplicadas.append(f"fotos: {len(movidas)} reubicada(s)")
    except Exception:
        # Un fallo reubicando fotos no puede impedir que la app arranque.
        pass

    return aplicadas


def inicializar(path: str | None = None,
                admin_password: str | None = None,
                url: str | None = None) -> tuple[object, dict]:
    """Crea el esquema, aplica el seed y garantiza un usuario admin."""
    import seed

    conn = conectar(path, url)
    crear_esquema(conn)
    resumen = seed.aplicar_seed(conn)

    fila = conn.execute("SELECT COUNT(*) c FROM usuarios").fetchone()
    if fila["c"] == 0:
        # Contraseña aleatoria si no se indica una: nunca un default adivinable.
        password = admin_password or secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES (?,?,?,?)",
            ("admin", "Administrador", hash_password(password), "admin"))
        conn.commit()
        resumen["admin_password"] = password

    return conn, resumen


def get_config(conn: sqlite3.Connection, clave: str, default=None):
    fila = conn.execute("SELECT valor FROM config WHERE clave = ?", (clave,)).fetchone()
    return json.loads(fila["valor"]) if fila else default


def set_config(conn: sqlite3.Connection, clave: str, valor) -> None:
    conn.execute(
        "UPDATE config SET valor = ?, actualizado = datetime('now') WHERE clave = ?",
        (json.dumps(valor, ensure_ascii=False), clave))
    conn.commit()


def inventario_pendiente(conn: sqlite3.Connection) -> list[dict]:
    """Ítems LoS que no se pueden relevar porque su inventario está vacío (4.2).

    Sostiene la regla de que un inventario sin cargar es 'Requiere configuración'
    y nunca 100%.
    """
    pendientes = []
    for fila in conn.execute(
            "SELECT clave, nombre, requiere_inventario FROM los_items "
            "WHERE aplica = 1 AND requiere_inventario IS NOT NULL ORDER BY orden"):
        tabla = fila["requiere_inventario"]
        if tabla == "asientos_preembarque":
            n = conn.execute(
                "SELECT instalados FROM asientos_preembarque WHERE id = 1").fetchone()
            cargado = bool(n and n["instalados"] > 0)
        else:
            n = conn.execute(f"SELECT COUNT(*) c FROM {tabla}").fetchone()
            cargado = n["c"] > 0
        if not cargado:
            pendientes.append({"item": fila["clave"], "nombre": fila["nombre"],
                               "inventario": tabla})
    return pendientes


if __name__ == "__main__":
    conn, resumen = inicializar()
    if DB_URL:
        # No se imprime la cadena: lleva la contraseña de la base.
        print("Base creada en Postgres (IRJ_DB_URL)")
    else:
        print(f"Base creada en: {DB_PATH}")
    for k, v in resumen.items():
        print(f"  {k}: {v}")
    pend = inventario_pendiente(conn)
    print(f"\nÍtems LoS que requieren carga de inventario ({len(pend)}):")
    for p in pend:
        print(f"  - {p['nombre']}  (tabla: {p['inventario']})")
