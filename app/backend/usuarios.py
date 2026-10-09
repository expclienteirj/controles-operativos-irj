"""Usuarios y credenciales: cómo se ingresa y quién administra los accesos.

Dos tipos de credencial, según el rol:

  - Auditor: un PIN de cuatro dígitos. Ingresa a diario desde una tablet
    compartida, y escribir una contraseña con el teclado en pantalla es lo que
    más cuesta del día.
  - Admin: una contraseña de ocho caracteres o más. Es quien carga los datos que
    mueven la certificación y quien administra a los demás: un PIN de cuatro
    dígitos en esa cuenta sería demasiado poco.

Un PIN tiene diez mil combinaciones, así que no se sostiene solo. Lo sostienen
tres cosas que viven en otros lados y conviene tener presentes: el freno
escalonado (`services.login_bloqueado`, 20 fallos por día como máximo), que el
PIN lo genera el sistema en vez de elegirlo la persona, y que se rechazan los
que todos eligen (`pin_trivial`).

Las funciones lanzan `ValueError` (400), `PermissionError` (403) y
`LookupError` (404): el despachador de `api.py` ya las traduce a respuestas.
"""

from __future__ import annotations

import json
import re
import secrets

import db
import services

TIPO_PIN = "pin"
TIPO_PASSWORD = "password"
ROLES = ("auditor", "admin")
PIN_LARGO = 4
PASSWORD_MINIMA = 8

_USUARIO_VALIDO = re.compile(r"^[a-z0-9._-]{3,30}$")

# Los PIN que alguien prueba primero. Se excluyen del sorteo y de lo que una
# persona pueda elegir al cambiarlo.
_TECLADO_VERTICAL = {"2580", "1470", "3690"}
_COMUNES = {"1004", "2001", "1212", "6969", "7777", "0852", "1313"}


# ---------------------------------------------------------------- credencial --

def pin_trivial(pin: str) -> bool:
    """¿Es un PIN de los que se adivinan primero?

    Un PIN de cuatro dígitos ya es débil por tamaño; que además sea 1234 o un
    año de nacimiento lo vuelve adivinable en los primeros intentos, que es
    justo lo que el freno no alcanza a impedir.
    """
    if len(set(pin)) == 1:                          # 0000, 1111, 9999
        return True
    pasos = {int(b) - int(a) for a, b in zip(pin, pin[1:])}
    if pasos in ({1}, {-1}):                        # 1234, 4321, 0123, 6789
        return True
    if pin[:2] == pin[2:]:                          # 1212, 3434, 0101
        return True
    if 1900 <= int(pin) <= 2099:                    # años de nacimiento y actuales
        return True
    return pin in _TECLADO_VERTICAL or pin in _COMUNES


def generar_pin() -> str:
    """Un PIN al azar que no sea de los obvios."""
    while True:
        pin = f"{secrets.randbelow(10 ** PIN_LARGO):0{PIN_LARGO}d}"
        if not pin_trivial(pin):
            return pin


def generar_password() -> str:
    """Contraseña de 12 caracteres para un admin nuevo o reseteado."""
    return secrets.token_urlsafe(9)


def tipo_de_credencial(rol: str) -> str:
    return TIPO_PIN if rol == "auditor" else TIPO_PASSWORD


def validar_credencial(rol: str, valor: str) -> str:
    """Valida una credencial elegida por la persona y devuelve su tipo.

    Lanza ValueError con el mensaje que ve el usuario. Va por rol y no por el
    tipo que tenga hoy: un auditor que todavía entra con una contraseña vieja
    la cambia por un PIN, y un admin nunca baja a PIN.
    """
    if rol == "auditor":
        if not re.fullmatch(rf"\d{{{PIN_LARGO}}}", valor or ""):
            raise ValueError(f"El PIN tiene que ser de {PIN_LARGO} dígitos, solo números")
        if pin_trivial(valor):
            raise ValueError("Ese PIN es muy fácil de adivinar (1234, 0000, un año…). "
                             "Elegí otro")
        return TIPO_PIN
    if len(valor or "") < PASSWORD_MINIMA:
        raise ValueError(f"La contraseña debe tener al menos {PASSWORD_MINIMA} caracteres")
    if len(set(valor)) == 1:
        raise ValueError("La contraseña no puede repetir un solo carácter")
    return TIPO_PASSWORD


def _credencial_nueva(rol: str) -> tuple[str, str]:
    """(valor en claro, tipo) para un usuario nuevo o reseteado."""
    if rol == "auditor":
        return generar_pin(), TIPO_PIN
    return generar_password(), TIPO_PASSWORD


# ------------------------------------------------------------------- consultas --

def _fila(conn, usuario_id: int):
    fila = conn.execute(
        "SELECT id, usuario, nombre, rol, activo, credencial FROM usuarios "
        "WHERE id = ?", (usuario_id,)).fetchone()
    if not fila:
        raise LookupError("No existe ese usuario")
    return fila


def _admins_activos(conn, excepto_id: int | None = None) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM usuarios WHERE rol = 'admin' AND activo = 1 "
        "AND id != ?", (excepto_id or 0,)).fetchone()["c"]


def listar(conn) -> list[dict]:
    """Todos los usuarios con lo que el admin necesita para administrarlos.

    Nunca incluye el hash: la pantalla no tiene nada que hacer con él.
    """
    filas = conn.execute(
        "SELECT u.id, u.usuario, u.nombre, u.rol, u.activo, u.credencial, "
        "       u.creado_en, "
        "       (SELECT COUNT(*) FROM sesiones s WHERE s.usuario_id = u.id) sesiones, "
        "       (SELECT MAX(l.fecha) FROM auditoria_log l "
        "         WHERE l.usuario_id = u.id AND l.accion = 'LOGIN') ultimo_ingreso "
        "FROM usuarios u ORDER BY u.activo DESC, u.rol, u.nombre").fetchall()
    resultado = []
    for f in filas:
        d = dict(f)
        # `login_bloqueado` mira solo la clave del usuario: la IP no interesa
        # acá, y un bloqueo por IP no es del usuario sino de quien intenta.
        espera = services.login_bloqueado(conn, f["usuario"], None)
        d["bloqueado_min"] = espera
        resultado.append(d)
    return resultado


def _liberar_bloqueo(conn, fila) -> None:
    """Borra los fallos que bloquean a este usuario, los suyos y los de su IP.

    El freno cuenta por usuario Y por IP. En una tablet los cinco errores salen
    de la misma IP, así que liberar solo la clave del usuario lo dejaba afuera
    igual: la IP seguía con sus cinco fallos y quedaba esperando un cuarto de
    hora más, justo lo que el admin intentaba evitar.

    Las IP se sacan del registro de auditoría, donde cada fallo de un usuario
    real quedó anotado con la suya. Una IP que nunca falló contra este usuario
    no se toca: liberar a una persona no puede ser un modo de borrar el rastro
    de alguien que está barriendo cuentas.
    """
    conn.execute("DELETE FROM intentos_login WHERE clave = ?",
                 (f"usuario:{fila['usuario']}",))
    recientes = conn.execute(
        "SELECT detalle FROM auditoria_log WHERE usuario_id = ? "
        "AND accion = 'LOGIN_FALLIDO' AND fecha > datetime('now', ?)",
        (fila["id"], f"-{services.LOGIN_RETENCION_MINUTOS} minutes")).fetchall()
    for f in recientes:
        try:
            ip = (json.loads(f["detalle"]) or {}).get("ip")
        except (TypeError, ValueError):
            continue
        if ip:
            conn.execute("DELETE FROM intentos_login WHERE clave = ?", (f"ip:{ip}",))


# ------------------------------------------------------------------ operaciones --

def crear(conn, actor_id: int, usuario: str, nombre: str, rol: str) -> dict:
    """Da de alta un usuario y devuelve su credencial UNA vez.

    La credencial la genera el sistema: así no aparecen 1234 ni fechas de
    nacimiento, ni la clave de la tarjeta del banco de nadie.
    """
    usuario = (usuario or "").strip().lower()
    nombre = (nombre or "").strip()
    if not _USUARIO_VALIDO.match(usuario):
        raise ValueError("El usuario va en minúsculas, de 3 a 30 caracteres: "
                         "letras, números, punto, guion o guion bajo")
    if not (2 <= len(nombre) <= 60):
        raise ValueError("El nombre tiene que tener entre 2 y 60 caracteres")
    if rol not in ROLES:
        raise ValueError("El rol tiene que ser auditor o admin")
    if conn.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)).fetchone():
        raise ValueError("Ya existe un usuario con ese nombre de usuario")

    valor, tipo = _credencial_nueva(rol)
    cur = conn.execute(
        "INSERT INTO usuarios (usuario, nombre, password_hash, rol, credencial) "
        "VALUES (?,?,?,?,?)",
        (usuario, nombre, db.hash_password(valor), rol, tipo))
    nuevo_id = cur.lastrowid
    services.registrar_log(conn, actor_id, "USUARIO_CREADO", "usuarios", nuevo_id,
                           {"usuario": usuario, "rol": rol})
    conn.commit()
    return {"id": nuevo_id, "usuario": usuario, "nombre": nombre, "rol": rol,
            "credencial": tipo, "credencial_temporal": valor}


def modificar(conn, actor_id: int, usuario_id: int, *, nombre: str | None = None,
              rol: str | None = None, activo: bool | None = None) -> dict:
    """Cambia nombre, rol o estado. Devuelve la credencial nueva si cambió el rol.

    Salvaguardas que no admiten excepción: uno no puede sacarse a sí mismo el
    acceso, y nunca puede quedar el sistema sin un admin activo. Ninguna de las
    dos se arregla desde la pantalla: se arreglan tocando la base.
    """
    fila = _fila(conn, usuario_id)
    cambios, log = {}, {}

    if nombre is not None:
        nombre = nombre.strip()
        if not (2 <= len(nombre) <= 60):
            raise ValueError("El nombre tiene que tener entre 2 y 60 caracteres")
        cambios["nombre"] = nombre
        log["nombre"] = nombre

    if rol is not None and rol != fila["rol"]:
        if rol not in ROLES:
            raise ValueError("El rol tiene que ser auditor o admin")
        cambios["rol"] = rol
        log["rol"] = rol

    if activo is not None and bool(activo) != bool(fila["activo"]):
        cambios["activo"] = 1 if activo else 0
        log["activo"] = bool(activo)

    saca_acceso = (cambios.get("activo") == 0
                   or ("rol" in cambios and cambios["rol"] != "admin"
                       and fila["rol"] == "admin"))
    if saca_acceso and fila["id"] == actor_id:
        raise PermissionError("No podés desactivarte ni quitarte el rol de admin "
                              "a vos mismo. Pedíselo a otro administrador")
    if saca_acceso and fila["rol"] == "admin" and fila["activo"] \
            and _admins_activos(conn, excepto_id=fila["id"]) == 0:
        raise PermissionError("Tiene que quedar al menos un administrador activo")

    respuesta = {"id": fila["id"]}
    if "rol" in cambios:
        # El tipo de credencial depende del rol: pasar de auditor a admin con un
        # PIN dejaría la cuenta más poderosa protegida por cuatro dígitos.
        valor, tipo = _credencial_nueva(cambios["rol"])
        conn.execute("UPDATE usuarios SET password_hash = ?, credencial = ? "
                     "WHERE id = ?", (db.hash_password(valor), tipo, fila["id"]))
        respuesta.update(credencial=tipo, credencial_temporal=valor)

    if cambios:
        columnas = ", ".join(f"{c} = ?" for c in cambios)
        conn.execute(f"UPDATE usuarios SET {columnas} WHERE id = ?",
                     (*cambios.values(), fila["id"]))
        # Desactivar o cambiar de rol corta las sesiones abiertas. `_sesion` ya
        # rechaza a un usuario inactivo, pero un cambio de rol no: sin esto la
        # sesión vieja seguiría con los permisos del rol anterior hasta vencer.
        if "activo" in cambios or "rol" in cambios:
            conn.execute("DELETE FROM sesiones WHERE usuario_id = ?", (fila["id"],))
        services.registrar_log(conn, actor_id, "USUARIO_MODIFICADO", "usuarios",
                               fila["id"], {"usuario": fila["usuario"], **log})
    conn.commit()
    return respuesta


def resetear(conn, actor_id: int, usuario_id: int) -> dict:
    """Genera una credencial nueva y la devuelve UNA vez.

    Cierra las sesiones y libera el bloqueo: quien se olvidó el PIN suele estar
    también bloqueado, y resetear sin liberar lo dejaría esperando.
    """
    fila = _fila(conn, usuario_id)
    if fila["id"] == actor_id:
        raise PermissionError("Tu propia contraseña se cambia desde tu sesión, "
                              "no desde acá")
    valor, tipo = _credencial_nueva(fila["rol"])
    conn.execute("UPDATE usuarios SET password_hash = ?, credencial = ? WHERE id = ?",
                 (db.hash_password(valor), tipo, fila["id"]))
    conn.execute("DELETE FROM sesiones WHERE usuario_id = ?", (fila["id"],))
    _liberar_bloqueo(conn, fila)
    services.registrar_log(conn, actor_id, "CREDENCIAL_RESETEADA", "usuarios",
                           fila["id"], {"usuario": fila["usuario"]})
    conn.commit()
    return {"id": fila["id"], "credencial": tipo, "credencial_temporal": valor}


def desbloquear(conn, actor_id: int, usuario_id: int) -> dict:
    """Borra los fallos que bloquean al usuario para que pueda volver a intentar."""
    fila = _fila(conn, usuario_id)
    _liberar_bloqueo(conn, fila)
    services.registrar_log(conn, actor_id, "USUARIO_DESBLOQUEADO", "usuarios",
                           fila["id"], {"usuario": fila["usuario"]})
    conn.commit()
    return {"id": fila["id"], "bloqueado_min": None}


def cerrar_sesiones(conn, actor_id: int, usuario_id: int,
                    conservar_token: str | None = None) -> dict:
    """Cierra todas las sesiones del usuario (menos, opcionalmente, una)."""
    fila = _fila(conn, usuario_id)
    if conservar_token and fila["id"] == actor_id:
        conn.execute("DELETE FROM sesiones WHERE usuario_id = ? AND token != ?",
                     (fila["id"], db.hash_token(conservar_token)))
    else:
        conn.execute("DELETE FROM sesiones WHERE usuario_id = ?", (fila["id"],))
    services.registrar_log(conn, actor_id, "SESIONES_CERRADAS", "usuarios",
                           fila["id"], {"usuario": fila["usuario"]})
    conn.commit()
    return {"id": fila["id"]}
