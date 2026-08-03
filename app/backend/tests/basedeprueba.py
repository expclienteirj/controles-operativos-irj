"""Base de pruebas: la misma suite corre contra SQLite o contra Postgres.

Sin `IRJ_DB_URL` se comporta igual que siempre —SQLite en memoria, un motor
nuevo por test— y no cambia nada.

Con `IRJ_DB_URL` apuntando a una base Postgres de desarrollo, el esquema se
crea una sola vez y cada test arranca con las tablas vacías y el seed recién
aplicado. Crear y destruir una base por test costaría segundos cada vez; vaciar
las tablas cuesta milisegundos y deja el mismo punto de partida.

Que los 540 tests pasen contra los dos motores es lo que da confianza de que la
capa de compatibilidad no cambió ninguna respuesta — sobre todo en el cálculo
de los importes a certificar.
"""

from __future__ import annotations

import os
import secrets

import db
import seed

URL = os.environ.get("IRJ_DB_URL", "")

_esquema_creado = False


def usa_postgres() -> bool:
    return bool(URL)


def nueva(admin_password: str | None = None, path: str = ":memory:"):
    """Devuelve (conn, resumen) con la base recién inicializada y vacía."""
    if not URL:
        return db.inicializar(path, admin_password=admin_password)

    global _esquema_creado
    conn = db.conectar(url=URL)
    if not _esquema_creado:
        _borrar_esquema(conn)
        db.crear_esquema(conn)
        _esquema_creado = True
    else:
        _vaciar(conn)

    resumen = seed.aplicar_seed(conn)

    fila = conn.execute("SELECT COUNT(*) c FROM usuarios").fetchone()
    if fila["c"] == 0:
        password = admin_password or secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES (?,?,?,?)",
            ("admin", "Administrador", db.hash_password(password), "admin"))
        conn.commit()
        resumen["admin_password"] = password

    return conn, resumen


def _tablas(conn) -> list[str]:
    return [f["tablename"] for f in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]


def _vaciar(conn) -> None:
    """Deja todas las tablas vacías y los contadores de id en cero.

    CASCADE es necesario por las claves foráneas; RESTART IDENTITY hace que los
    ids arranquen de 1 en cada test, como pasa con una base SQLite nueva —
    varios tests dan por sentado que el primer registro es el id 1.
    """
    tablas = _tablas(conn)
    if not tablas:
        return
    conn.executescript(
        "TRUNCATE " + ", ".join(tablas) + " RESTART IDENTITY CASCADE")
    conn.commit()


def _borrar_esquema(conn) -> None:
    """Parte de cero. La base de desarrollo es descartable por definición."""
    conn.executescript("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    conn.commit()
