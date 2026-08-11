"""
API REST — Controles Operativos IRJ.

http.server de la biblioteca estándar: sin dependencias externas, un solo
aeropuerto, tráfico de una tablet. Sirve además el frontend estático de la PWA.

Autenticación por token en memoria (Authorization: Bearer <token>).
Los tokens se pierden al reiniciar el proceso; la PWA reintenta el login.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import base64
import binascii
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import almacen
import calc
import db
import fotos
import informes
import services
import validacion

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")
UPLOADS_DIR = os.path.join(BASE_DIR, "data", "uploads")

MAX_BODY = 12 * 1024 * 1024        # 12 MB: tolera una foto comprimida en base64
MAX_FOTO = 8 * 1024 * 1024

# Vencimiento de sesión. Generoso a propósito: la tablet puede pasar días sin
# red y necesita poder subir lo encolado cuando vuelva.
SESION_DIAS = 30

# Cada cuánto se refresca `sesiones.ultimo_uso`. Ver `_conviene_refrescar_sesion`.
FRESCURA_SESION = 15 * 60


def _conviene_refrescar_sesion(ultimo_uso) -> bool:
    """¿Hace falta actualizar `ultimo_uso`, o la marca sigue siendo buena?

    Las marcas se guardan como texto UTC con el formato de `datetime('now')`
    de SQLite, que es el mismo que produce la función homónima de `pgcompat`.
    Ante cualquier valor que no se pueda leer se refresca: perder la marca
    vencería la sesión de un auditor que sí está trabajando.
    """
    import datetime as _dt
    try:
        marca = _dt.datetime.strptime(str(ultimo_uso)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return True
    ahora = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    return (ahora - marca).total_seconds() >= FRESCURA_SESION


class ErrorAPI(Exception):
    def __init__(self, mensaje: str, codigo: int = 400):
        super().__init__(mensaje)
        self.mensaje, self.codigo = mensaje, codigo


# ==========================================================================
# Router
# ==========================================================================

RUTAS: list[tuple[str, re.Pattern, callable, str]] = []


def ruta(metodo: str, patron: str, rol: str = "auditor"):
    """rol: 'publico' | 'auditor' | 'admin' (auditor implica sesión válida)."""
    def deco(fn):
        RUTAS.append((metodo, re.compile(f"^{patron}$"), fn, rol))
        return fn
    return deco


# ---------------------------------------------------------------- sesión --

@ruta("POST", r"/api/login", rol="publico")
def login(ctx):
    usuario = (ctx["body"].get("usuario") or "").strip()
    password = ctx["body"].get("password") or ""
    ip = ctx.get("ip")

    # Freno antes de mirar la contraseña: sin esto, con la app publicada en
    # internet cualquiera puede probar claves sin límite. El mensaje no dice si
    # el usuario existe, igual que el de credenciales incorrectas.
    faltan = services.login_bloqueado(ctx["conn"], usuario, ip)
    if faltan is not None:
        raise ErrorAPI(
            f"Demasiados intentos fallidos. Reintentá en {faltan} minuto(s).", 429)

    fila = ctx["conn"].execute(
        "SELECT id, usuario, nombre, rol, password_hash, activo FROM usuarios "
        "WHERE usuario = ?", (usuario,)).fetchone()

    # Mensaje genérico y verificación siempre ejecutada: no revelar si el
    # usuario existe ni permitir distinguirlo por tiempo de respuesta.
    hash_ref = fila["password_hash"] if fila else db.hash_password("inexistente")
    if not db.verificar_password(password, hash_ref) or not fila or not fila["activo"]:
        services.registrar_intento_fallido(ctx["conn"], usuario, ip)
        raise ErrorAPI("Usuario o contraseña incorrectos", 401)

    services.limpiar_intentos(ctx["conn"], usuario, ip)
    token = secrets.token_urlsafe(32)
    ctx["conn"].execute("INSERT INTO sesiones (token, usuario_id) VALUES (?,?)",
                        (token, fila["id"]))
    ctx["conn"].execute(
        "DELETE FROM sesiones WHERE ultimo_uso < datetime('now', ?)",
        (f"-{SESION_DIAS} days",))
    services.registrar_log(ctx["conn"], fila["id"], "LOGIN")
    ctx["conn"].commit()
    return {"token": token, "usuario": {"id": fila["id"], "usuario": fila["usuario"],
                                        "nombre": fila["nombre"], "rol": fila["rol"]}}


@ruta("POST", r"/api/logout")
def logout(ctx):
    ctx["conn"].execute("DELETE FROM sesiones WHERE token = ?", (ctx["token"],))
    ctx["conn"].commit()
    return {"ok": True}


@ruta("GET", r"/api/sesion")
def sesion(ctx):
    return {"usuario": ctx["sesion"]}


@ruta("POST", r"/api/password")
def cambiar_password(ctx):
    actual = ctx["body"].get("actual") or ""
    nueva = ctx["body"].get("nueva") or ""
    if len(nueva) < 8:
        raise ErrorAPI("La nueva contraseña debe tener al menos 8 caracteres")

    fila = ctx["conn"].execute(
        "SELECT password_hash FROM usuarios WHERE id = ?",
        (ctx["sesion"]["usuario_id"],)).fetchone()
    if not db.verificar_password(actual, fila["password_hash"]):
        raise ErrorAPI("La contraseña actual no es correcta", 401)

    ctx["conn"].execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                        (db.hash_password(nueva), ctx["sesion"]["usuario_id"]))
    # Cerrar las demás sesiones del usuario: si cambió la contraseña porque
    # sospecha que alguien la conoce, dejar sesiones vivas anularía el cambio.
    ctx["conn"].execute("DELETE FROM sesiones WHERE usuario_id = ? AND token != ?",
                        (ctx["sesion"]["usuario_id"], ctx["token"]))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"], "CAMBIO_PASSWORD")
    ctx["conn"].commit()
    return {"ok": True}


# ------------------------------------------------------------------- versión --

def firma_version() -> str:
    """Identificador opaco y estable de la versión desplegada.

    Responde a una pregunta que hasta ahora no tenía forma objetiva de
    contestarse: ¿qué código está corriendo en producción? Un cambio que solo
    toca el backend no deja ninguna huella descargable, así que después de
    publicar no había manera de distinguir "ya salió" de "todavía no".

    Se publica el **hash** del commit, no el commit. sha256 no es reversible y
    el espacio de un sha de git es de 160 bits, así que el valor no revela nada
    del repositorio, pero cambia exactamente cuando cambia lo desplegado. Para
    verificar desde afuera se compara contra el mismo hash calculado sobre el
    commit local:

        python3 -c "import hashlib,subprocess as s; \\
          print(hashlib.sha256(s.check_output(['git','rev-parse','HEAD']).strip()).hexdigest()[:12])"

    Fuera de Vercel no hay commit y devuelve 'local', que es la verdad.
    """
    sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    return hashlib.sha256(sha.encode()).hexdigest()[:12] if sha else "local"


@ruta("GET", r"/api/version", rol="publico")
def version(ctx):
    """Pública a propósito: sirve para confirmar un despliegue sin credenciales.

    No expone el commit ni ningún dato del repositorio ni de la operación.
    """
    return {"firma": firma_version(),
            "entorno": os.environ.get("VERCEL_ENV", "local")}


# ------------------------------------------------------------ configuración --

@ruta("GET", r"/api/config")
def get_config(ctx):
    grupo = ctx["query"].get("grupo", [None])[0]
    sql = "SELECT clave, valor, grupo, descripcion, editable FROM config"
    args = ()
    if grupo:
        sql += " WHERE grupo = ?"
        args = (grupo,)
    return {"config": [{**dict(f), "valor": json.loads(f["valor"])}
                       for f in ctx["conn"].execute(sql + " ORDER BY grupo, clave", args)]}


@ruta("PUT", r"/api/config/([\w-]+)", rol="admin")
def put_config(ctx, clave):
    fila = ctx["conn"].execute(
        "SELECT valor, editable FROM config WHERE clave = ?", (clave,)).fetchone()
    if not fila:
        raise ErrorAPI(f"No existe la clave de configuración '{clave}'", 404)
    if not fila["editable"]:
        raise ErrorAPI(f"La clave '{clave}' no es editable", 403)
    if "valor" not in ctx["body"]:
        raise ErrorAPI("Falta el campo 'valor'")

    # Un umbral absurdo no rompe la app: produce números plausibles pero mal
    # calculados, sobre los que después se paga. Se rechaza en el borde.
    try:
        valor = validacion.validar(clave, ctx["body"]["valor"])
    except validacion.ErrorValidacion as e:
        raise ErrorAPI(str(e), 400)

    anterior = json.loads(fila["valor"])
    db.set_config(ctx["conn"], clave, valor)
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"], "EDITAR_CONFIG",
                           "config", None,
                           {"clave": clave, "anterior": anterior, "nuevo": valor})
    ctx["conn"].commit()
    return {"ok": True, "clave": clave, "valor": valor}


@ruta("GET", r"/api/onboarding")
def get_onboarding(ctx):
    return services.estado_onboarding(ctx["conn"])


@ruta("GET", r"/api/inventario/pendiente")
def inventario_pendiente(ctx):
    return {"pendientes": db.inventario_pendiente(ctx["conn"])}


# Alta/baja genérica de las tablas de inventario (4.2). La lista blanca evita
# exponer tablas que no son inventario.
TABLAS_INVENTARIO = {
    "nucleos": ("nucleos_sanitarios", ("nombre", "tipo", "ubicacion")),
    "luminarias": ("luminarias_sector", ("sector", "cantidad")),
    "puertas": ("puertas_embarque", ("nombre", "php", "instaladas")),
    "elevacion": ("medios_elevacion", ("nombre", "tipo", "redundancia")),
    "secciones": ("secciones_pavimento", ("identificador", "tipo")),
}


# Debe ir antes que la ruta genérica /api/inventario/(\w+): el despacho toma
# la primera coincidencia y "asientos" también matchea el comodín.
@ruta("GET", r"/api/inventario/asientos")
def get_asientos(ctx):
    fila = ctx["conn"].execute(
        "SELECT instalados FROM asientos_preembarque WHERE id = 1").fetchone()
    return {"instalados": fila["instalados"] if fila else 0,
            "minimo": db.get_config(ctx["conn"], "asientos_minimo",
                                    calc.ASIENTOS_MINIMOS_IRJ)}


@ruta("GET", r"/api/inventario/(\w+)")
def listar_inventario(ctx, recurso):
    tabla, _ = _tabla_inventario(recurso)
    filas = [dict(f) for f in ctx["conn"].execute(f"SELECT * FROM {tabla}")]
    if recurso == "nucleos":
        for n in filas:
            n["equipos"] = {f["equipo"]: f["instalados"] for f in ctx["conn"].execute(
                "SELECT equipo, instalados FROM nucleo_equipos WHERE nucleo_id = ?",
                (n["id"],))}
    return {recurso: filas}


@ruta("POST", r"/api/inventario/(\w+)", rol="admin")
def crear_inventario(ctx, recurso):
    tabla, campos = _tabla_inventario(recurso)
    try:
        validacion.validar_inventario(recurso, ctx["body"])
    except validacion.ErrorValidacion as e:
        raise ErrorAPI(str(e), 400)

    datos = {c: ctx["body"].get(c) for c in campos if c in ctx["body"]}
    if not datos:
        raise ErrorAPI(f"Faltan campos. Esperados: {', '.join(campos)}")

    cols = ", ".join(datos)
    marcas = ", ".join("?" * len(datos))
    try:
        cur = ctx["conn"].execute(
            f"INSERT INTO {tabla} ({cols}) VALUES ({marcas})", tuple(datos.values()))
    except db.ERRORES_INTEGRIDAD as e:
        raise ErrorAPI(f"No se pudo crear: {e}", 409)

    nuevo_id = cur.lastrowid
    if recurso == "nucleos":
        _guardar_equipos_nucleo(ctx["conn"], nuevo_id, ctx["body"].get("equipos"))

    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"], "ALTA_INVENTARIO",
                           tabla, nuevo_id, datos)
    ctx["conn"].commit()
    return {"id": nuevo_id}


@ruta("PUT", r"/api/inventario/(\w+)/(\d+)", rol="admin")
def editar_inventario(ctx, recurso, id_):
    tabla, campos = _tabla_inventario(recurso)
    id_ = int(id_)

    existe = ctx["conn"].execute(
        f"SELECT id FROM {tabla} WHERE id = ?", (id_,)).fetchone()
    if not existe:
        raise ErrorAPI("No existe el elemento de inventario", 404)

    try:
        validacion.validar_inventario(recurso, ctx["body"])
    except validacion.ErrorValidacion as e:
        raise ErrorAPI(str(e), 400)

    datos = {c: ctx["body"].get(c) for c in campos if c in ctx["body"]}
    if datos:
        asignaciones = ", ".join(f"{c} = ?" for c in datos)
        try:
            ctx["conn"].execute(f"UPDATE {tabla} SET {asignaciones} WHERE id = ?",
                                (*datos.values(), id_))
        except db.ERRORES_INTEGRIDAD as e:
            raise ErrorAPI(f"No se pudo actualizar: {e}", 409)

    if recurso == "nucleos" and "equipos" in ctx["body"]:
        ctx["conn"].execute("DELETE FROM nucleo_equipos WHERE nucleo_id = ?", (id_,))
        _guardar_equipos_nucleo(ctx["conn"], id_, ctx["body"]["equipos"])

    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "EDITAR_INVENTARIO", tabla, id_, datos)
    ctx["conn"].commit()
    return {"ok": True, "id": id_}


@ruta("DELETE", r"/api/inventario/(\w+)/(\d+)", rol="admin")
def borrar_inventario(ctx, recurso, id_):
    tabla, _ = _tabla_inventario(recurso)
    ctx["conn"].execute(f"DELETE FROM {tabla} WHERE id = ?", (int(id_),))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "BAJA_INVENTARIO", tabla, int(id_))
    ctx["conn"].commit()
    return {"ok": True}


@ruta("PUT", r"/api/inventario/asientos", rol="admin")
def put_asientos(ctx):
    instalados = ctx["body"].get("instalados")
    if isinstance(instalados, bool) or not isinstance(instalados, int) or instalados < 0:
        raise ErrorAPI("La cantidad de asientos debe ser un entero de 0 o más")
    cur = ctx["conn"].execute(
        "UPDATE asientos_preembarque SET instalados = ? WHERE id = 1", (instalados,))
    if not cur.rowcount:
        # `asientos_preembarque` es de fila única y esa fila la crea el seed.
        # `seed-supabase.sql` no la incluía, así que en Supabase la tabla quedaba
        # vacía: el UPDATE no tocaba ninguna fila, la API respondía 200, la
        # pantalla decía "Asientos actualizados" y el valor no se guardaba en
        # ningún lado. Insertarla acá arregla además las bases que ya quedaron
        # en ese estado, sin depender de que alguien corra un seed correctivo.
        ctx["conn"].execute(
            "INSERT INTO asientos_preembarque (id, instalados) VALUES (1, ?)",
            (instalados,))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "EDITAR_INVENTARIO", "asientos_preembarque", 1,
                           {"instalados": instalados})
    ctx["conn"].commit()
    return {"instalados": instalados}


def _guardar_equipos_nucleo(conn, nucleo_id, equipos):
    """Solo se guardan los equipos con cantidad: un cero no aporta nada al
    cálculo y ensuciaría el inventario."""
    for equipo, cantidad in (equipos or {}).items():
        if int(cantidad) > 0:
            conn.execute(
                "INSERT INTO nucleo_equipos (nucleo_id, equipo, instalados) "
                "VALUES (?,?,?)", (nucleo_id, equipo, int(cantidad)))


def _tabla_inventario(recurso):
    if recurso not in TABLAS_INVENTARIO:
        raise ErrorAPI(f"Recurso de inventario desconocido: {recurso}", 404)
    return TABLAS_INVENTARIO[recurso]


# ------------------------------------------------- equipamiento de limpieza --

@ruta("GET", r"/api/equipamiento")
def listar_equipamiento(ctx):
    return {"equipamiento": [dict(f) for f in ctx["conn"].execute(
        "SELECT * FROM equipamiento_limpieza ORDER BY orden, nombre")]}


@ruta("POST", r"/api/equipamiento", rol="admin")
def crear_equipamiento(ctx):
    nombre = (ctx["body"].get("nombre") or "").strip()
    if not nombre:
        raise ErrorAPI("El nombre del equipo es obligatorio")
    clave = re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_")
    try:
        cur = ctx["conn"].execute(
            "INSERT INTO equipamiento_limpieza (clave, nombre, exigido, orden) "
            "VALUES (?,?,?,?)",
            (clave, nombre, 1 if ctx["body"].get("exigido", True) else 0,
             int(ctx["body"].get("orden") or 99)))
    except db.ERRORES_INTEGRIDAD:
        raise ErrorAPI(f"Ya existe un equipo llamado '{nombre}'", 409)
    ctx["conn"].commit()
    return {"id": cur.lastrowid, "clave": clave}


@ruta("PUT", r"/api/equipamiento/(\d+)", rol="admin")
def editar_equipamiento(ctx, id_):
    campos = {}
    if "nombre" in ctx["body"]:
        nombre = (ctx["body"]["nombre"] or "").strip()
        if not nombre:
            raise ErrorAPI("El nombre del equipo no puede quedar vacío")
        campos["nombre"] = nombre
    if "exigido" in ctx["body"]:
        campos["exigido"] = 1 if ctx["body"]["exigido"] else 0
    if not campos:
        raise ErrorAPI("Nada para actualizar")

    # `exigido` entra en el ítem 4 de la certificación vía `equipos_exigidos`.
    # Sin esta verificación, editar un equipo borrado devolvía 200 y no cambiaba
    # nada: la app daba por hecho un cambio en el cálculo del importe que nunca
    # ocurrió. Es escenario real porque el equipamiento se puede borrar y la
    # tablet reenvía desde la cola operaciones armadas antes de ese borrado.
    if not ctx["conn"].execute(
            "SELECT id FROM equipamiento_limpieza WHERE id = ?",
            (int(id_),)).fetchone():
        raise ErrorAPI(f"No existe el equipo {id_}", 404)

    ctx["conn"].execute(
        f"UPDATE equipamiento_limpieza SET {', '.join(f'{c} = ?' for c in campos)} "
        "WHERE id = ?", (*campos.values(), int(id_)))
    ctx["conn"].commit()
    return {"ok": True}


@ruta("DELETE", r"/api/equipamiento/(\d+)", rol="admin")
def borrar_equipamiento(ctx, id_):
    ctx["conn"].execute("DELETE FROM equipamiento_limpieza WHERE id = ?", (int(id_),))
    ctx["conn"].commit()
    return {"ok": True}


# ------------------------------------------------------------------ insumos --

@ruta("GET", r"/api/insumos")
def listar_insumos(ctx):
    periodo = ctx["query"].get("periodo", [services.periodo_actual()])[0]
    return {"periodo": periodo, "insumos": [dict(f) for f in ctx["conn"].execute(
        "SELECT i.*, s.stock, s.relevado_en FROM insumos i "
        "LEFT JOIN insumo_stock s ON s.insumo_id = i.id AND s.periodo = ? "
        "WHERE i.activo = 1 ORDER BY i.nombre", (periodo,))]}


@ruta("POST", r"/api/insumos", rol="admin")
def crear_insumo(ctx):
    nombre = (ctx["body"].get("nombre") or "").strip()
    if not nombre:
        raise ErrorAPI("El nombre del insumo es obligatorio")
    punto = ctx["body"].get("punto_pedido")
    if punto is None or isinstance(punto, bool) or not isinstance(punto, (int, float)) \
            or punto < 0:
        raise ErrorAPI("El punto de pedido debe ser un número de 0 o más")
    try:
        cur = ctx["conn"].execute(
            "INSERT INTO insumos (nombre, punto_pedido, unidad) VALUES (?,?,?)",
            (nombre, punto, ctx["body"].get("unidad")))
    except db.ERRORES_INTEGRIDAD:
        raise ErrorAPI(f"Ya existe un insumo llamado '{nombre}'", 409)
    ctx["conn"].commit()
    return {"id": cur.lastrowid}


@ruta("PUT", r"/api/insumos/(\d+)", rol="admin")
def editar_insumo(ctx, id_):
    campos = {}
    for c in ("nombre", "punto_pedido", "unidad", "activo"):
        if c in ctx["body"]:
            campos[c] = ctx["body"][c]
    if not campos:
        raise ErrorAPI("Nada para actualizar")

    # `activo` decide qué insumos entran al ítem 5 de la certificación y
    # `punto_pedido` es el umbral contra el que se los mide: un UPDATE que no
    # afecta ninguna fila es un cambio de cálculo que la app cree hecho.
    if not ctx["conn"].execute(
            "SELECT id FROM insumos WHERE id = ?", (int(id_),)).fetchone():
        raise ErrorAPI(f"No existe el insumo {id_}", 404)

    ctx["conn"].execute(
        f"UPDATE insumos SET {', '.join(f'{c} = ?' for c in campos)} WHERE id = ?",
        (*campos.values(), int(id_)))
    ctx["conn"].commit()
    return {"ok": True}


@ruta("PUT", r"/api/periodos/([\d-]+)/insumos/(\d+)")
def registrar_stock(ctx, periodo, insumo_id):
    """Stock relevado en el mes. Alimenta el ítem 5 de la certificación."""
    stock = ctx["body"].get("stock")
    if stock is None or isinstance(stock, bool) or not isinstance(stock, (int, float)) \
            or stock < 0:
        raise ErrorAPI("El stock debe ser un número de 0 o más")
    ctx["conn"].execute(
        "INSERT INTO insumo_stock (periodo, insumo_id, stock) VALUES (?,?,?) "
        "ON CONFLICT (periodo, insumo_id) DO UPDATE SET "
        "stock = excluded.stock, relevado_en = datetime('now')",
        (periodo, int(insumo_id), stock))
    ctx["conn"].commit()
    return {"ok": True}


# ------------------------------------------------------------------ limpieza --

@ruta("GET", r"/api/sectores")
def listar_sectores(ctx):
    sectores = []
    for s in ctx["conn"].execute(
            "SELECT id, clave, nombre FROM sectores_limpieza WHERE activo = 1 ORDER BY orden"):
        items = [dict(f) for f in ctx["conn"].execute(
            "SELECT id, clave, nombre FROM items_limpieza "
            "WHERE sector_id = ? AND activo = 1 ORDER BY orden", (s["id"],))]
        sectores.append({**dict(s), "items": items})
    return {"sectores": sectores}


@ruta("GET", r"/api/controles")
def listar_controles(ctx):
    periodo = ctx["query"].get("periodo", [services.periodo_actual()])[0]
    return {"periodo": periodo, "controles": [dict(f) for f in ctx["conn"].execute(
        "SELECT c.*, u.nombre auditor FROM controles_limpieza c "
        "JOIN usuarios u ON u.id = c.auditor_id WHERE c.periodo = ? "
        "ORDER BY c.fecha, c.turno", (periodo,))]}


@ruta("GET", r"/api/controles/hoy")
def control_de_hoy(ctx):
    """Pantalla de entrada del auditor: qué control toca hoy y qué falta del mes."""
    return services.control_del_dia(ctx["conn"])


@ruta("GET", r"/api/periodos/([\d-]+)/completitud")
def completitud(ctx, periodo):
    return services.completitud_periodo(ctx["conn"], periodo)


@ruta("POST", r"/api/controles")
def crear_control(ctx):
    """Abre el control de un día. Por defecto, el de hoy."""
    import datetime as _dt

    fecha = ctx["body"].get("fecha") or _dt.date.today().isoformat()
    turno = (ctx["body"].get("turno") or "MANANA").upper()
    try:
        _dt.date.fromisoformat(fecha)
    except ValueError:
        raise ErrorAPI("Fecha inválida: se espera AAAA-MM-DD")
    if turno not in calc.TURNOS:
        raise ErrorAPI(f"Turno inválido: debe ser uno de {', '.join(calc.TURNOS)}")

    # No se abren controles de días que todavía no ocurrieron: un control es
    # el registro de una recorrida, no una planificación.
    if fecha > _dt.date.today().isoformat():
        raise ErrorAPI("No se puede abrir el control de una fecha futura")

    try:
        cur = ctx["conn"].execute(
            "INSERT INTO controles_limpieza (fecha, turno, periodo, auditor_id) "
            "VALUES (?,?,?,?)",
            (fecha, turno, fecha[:7], ctx["sesion"]["usuario_id"]))
    except db.ERRORES_INTEGRIDAD:
        raise ErrorAPI(f"Ya existe el control del {fecha} (turno {turno})", 409)
    ctx["conn"].commit()
    return {"control_id": cur.lastrowid, "fecha": fecha, "turno": turno,
            "periodo": fecha[:7]}


@ruta("GET", r"/api/controles/(\d+)")
def get_control(ctx, control_id):
    control_id = int(control_id)
    fila = ctx["conn"].execute(
        "SELECT * FROM controles_limpieza WHERE id = ?", (control_id,)).fetchone()
    if not fila:
        raise ErrorAPI("No existe el control", 404)

    estado = services.estado_control(ctx["conn"], control_id)
    estado["control"] = dict(fila)
    estado["desvios"] = [dict(f) for f in ctx["conn"].execute(
        "SELECT d.*, i.clave item_clave, i.nombre item_nombre, s.clave sector_clave "
        "FROM desvios d JOIN items_limpieza i ON i.id = d.item_id "
        "JOIN sectores_limpieza s ON s.id = i.sector_id WHERE d.control_id = ?",
        (control_id,))]
    return estado


@ruta("POST", r"/api/controles/(\d+)/sectores/(\d+)/confirmar")
def confirmar(ctx, control_id, sector_id):
    return services.confirmar_sector(ctx["conn"], int(control_id), int(sector_id),
                                     ctx["sesion"]["usuario_id"])


@ruta("DELETE", r"/api/controles/(\d+)/sectores/(\d+)/confirmar")
def desconfirmar(ctx, control_id, sector_id):
    return services.desconfirmar_sector(ctx["conn"], int(control_id), int(sector_id),
                                        ctx["sesion"]["usuario_id"])


@ruta("GET", r"/api/controles/(\d+)/equipamiento")
def get_equipamiento_control(ctx, control_id):
    """Equipos exigidos del día y cuáles se marcaron fuera de servicio."""
    control_id = int(control_id)
    fila = ctx["conn"].execute(
        "SELECT periodo FROM controles_limpieza WHERE id = ?", (control_id,)).fetchone()
    if not fila:
        raise ErrorAPI("No existe el control", 404)

    fuera = {f["equipamiento_id"]: f["observacion"] for f in ctx["conn"].execute(
        "SELECT equipamiento_id, observacion FROM equipamiento_faltante "
        "WHERE control_id = ?", (control_id,))}

    # Baja vigente al día del control: es lo que el auditor tiene que ver para
    # no volver a cargar una máquina que ya está declarada fuera de servicio.
    fecha = ctx["conn"].execute(
        "SELECT fecha FROM controles_limpieza WHERE id = ?",
        (control_id,)).fetchone()["fecha"]
    bajas = {f["equipamiento_id"]: dict(f) for f in ctx["conn"].execute(
        "SELECT id, equipamiento_id, desde, hasta, motivo FROM equipamiento_baja "
        "WHERE desde <= ? AND (hasta IS NULL OR hasta >= ?)", (fecha, fecha))}

    equipos = [{**e, "fuera_servicio": e["id"] in fuera,
                "observacion": fuera.get(e["id"]),
                "baja": bajas.get(e["id"])}
               for e in services.equipos_exigidos(ctx["conn"], fila["periodo"])
               if e["exigido"]]
    return {"control_id": control_id, "fecha": fecha, "equipos": equipos,
            "resumen": services._equipamiento_control(ctx["conn"], control_id,
                                                      fila["periodo"]),
            "mensual": services.equipamiento_mensual(ctx["conn"], fila["periodo"])}


@ruta("POST", r"/api/controles/(\d+)/equipamiento/(\d+)/baja")
def dar_baja_equipo(ctx, control_id, equipamiento_id):
    """Declara los días de baja de una máquina desde el control diario.

    Reemplaza al marcado día por día: el auditor carga el tramo una sola vez y
    el ítem 4 de la certificación lo descuenta aunque algún día del tramo no
    tenga control cerrado.
    """
    control_id = int(control_id)
    services._verificar_control_abierto(ctx["conn"], control_id)
    fila = ctx["conn"].execute(
        "SELECT fecha FROM controles_limpieza WHERE id = ?", (control_id,)).fetchone()
    if not fila:
        raise ErrorAPI("No existe el control", 404)

    b = ctx["body"]
    return services.registrar_baja_equipo(
        ctx["conn"], int(equipamiento_id), b.get("desde") or fila["fecha"],
        b.get("hasta"), b.get("motivo"), ctx["sesion"]["usuario_id"], control_id)


@ruta("PUT", r"/api/equipamiento/bajas/(\d+)")
def editar_baja_equipo(ctx, baja_id):
    """Corrige una baja o la cierra al reponer la máquina.

    Sin cuerpo se interpreta como "repuesta hoy", que es el caso frecuente
    desde el control diario. Con `desde`/`hasta`/`motivo` corrige lo cargado, y
    con `reabrir` vuelve a dejarla sin fecha de reposición.
    """
    import datetime as _dt

    b = ctx["body"] or {}
    corrige = any(k in b for k in ("desde", "motivo", "reabrir"))
    return services.editar_baja_equipo(
        ctx["conn"], int(baja_id), ctx["sesion"]["usuario_id"],
        desde=b.get("desde"),
        hasta=b.get("hasta") if corrige else (b.get("hasta")
                                              or _dt.date.today().isoformat()),
        motivo=b.get("motivo"), reabrir=bool(b.get("reabrir")))


@ruta("DELETE", r"/api/equipamiento/bajas/(\d+)", rol="admin")
def borrar_baja_equipo(ctx, baja_id):
    """Baja mal cargada. Solo admin: incide directamente sobre el pago."""
    return services.borrar_baja_equipo(ctx["conn"], int(baja_id),
                                       ctx["sesion"]["usuario_id"])


@ruta("GET", r"/api/controles/(\d+)/artefactos")
def get_artefactos_control(ctx, control_id):
    """Artefactos sanitarios y su estado de servicio al día del control.

    Vive en el control diario y no en LoS porque es una observación de
    recorrida: el auditor lo ve al entrar al baño.
    """
    control_id = int(control_id)
    fila = ctx["conn"].execute(
        "SELECT fecha, periodo FROM controles_limpieza WHERE id = ?",
        (control_id,)).fetchone()
    if not fila:
        raise ErrorAPI("No existe el control", 404)

    fuera = services.artefactos_fuera_servicio_en(ctx["conn"], fila["fecha"])
    nucleos = []
    for n in ctx["conn"].execute(
            "SELECT id, nombre, tipo FROM nucleos_sanitarios WHERE activo = 1 "
            "ORDER BY nombre"):
        equipos = [
            {"equipo": f["equipo"], "instalados": f["instalados"],
             "fuera_servicio": fuera.get(n["id"], {}).get(f["equipo"], 0)}
            for f in ctx["conn"].execute(
                "SELECT equipo, instalados FROM nucleo_equipos WHERE nucleo_id = ?",
                (n["id"],))
            if f["equipo"] in services.ARTEFACTOS_CON_SERVICIO and f["instalados"]]
        if equipos:
            nucleos.append({**dict(n), "equipos": equipos})

    return {"control_id": control_id, "fecha": fila["fecha"], "nucleos": nucleos,
            "bajas": services.artefactos_baja(ctx["conn"], fila["periodo"])}


@ruta("POST", r"/api/controles/(\d+)/artefactos/baja")
def dar_baja_artefacto(ctx, control_id):
    """Registra artefactos sanitarios clausurados por un tramo de días."""
    control_id = int(control_id)
    services._verificar_control_abierto(ctx["conn"], control_id)
    fila = ctx["conn"].execute(
        "SELECT fecha FROM controles_limpieza WHERE id = ?", (control_id,)).fetchone()
    if not fila:
        raise ErrorAPI("No existe el control", 404)

    b = ctx["body"]
    return services.registrar_baja_artefacto(
        ctx["conn"], int(b.get("nucleo_id") or 0), b.get("equipo") or "",
        b.get("cantidad") or 1, b.get("desde") or fila["fecha"], b.get("hasta"),
        b.get("motivo"), ctx["sesion"]["usuario_id"], control_id)


@ruta("PUT", r"/api/artefactos/bajas/(\d+)")
def editar_baja_artefacto(ctx, baja_id):
    """Corrige la clausura, o la cierra cuando el artefacto vuelve a servicio."""
    import datetime as _dt

    b = ctx["body"] or {}
    corrige = any(k in b for k in ("desde", "cantidad", "motivo", "reabrir"))
    return services.editar_baja_artefacto(
        ctx["conn"], int(baja_id), ctx["sesion"]["usuario_id"],
        desde=b.get("desde"),
        hasta=b.get("hasta") if corrige else (b.get("hasta")
                                              or _dt.date.today().isoformat()),
        cantidad=b.get("cantidad"), motivo=b.get("motivo"),
        reabrir=bool(b.get("reabrir")))


@ruta("DELETE", r"/api/artefactos/bajas/(\d+)", rol="admin")
def borrar_baja_artefacto(ctx, baja_id):
    """Clausura mal cargada. Solo admin: mueve el resultado del ítem 3.1."""
    return services.borrar_baja_artefacto(ctx["conn"], int(baja_id),
                                          ctx["sesion"]["usuario_id"])


@ruta("GET", r"/api/periodos/([\d-]+)/equipamiento/bajas")
def listar_bajas_equipo(ctx, periodo):
    return {"periodo": periodo,
            "bajas": services.bajas_equipamiento(ctx["conn"], periodo),
            "mensual": services.equipamiento_mensual(ctx["conn"], periodo)}


@ruta("PUT", r"/api/controles/(\d+)/equipamiento/(\d+)")
def marcar_equipamiento(ctx, control_id, equipamiento_id):
    """Marca o desmarca un equipo como fuera de servicio en el día.

    Por excepción, como el resto del control: todo equipo se asume disponible
    y solo se registra el que falta.
    """
    control_id, equipamiento_id = int(control_id), int(equipamiento_id)
    services._verificar_control_abierto(ctx["conn"], control_id)

    if not ctx["conn"].execute("SELECT 1 FROM equipamiento_limpieza WHERE id = ?",
                               (equipamiento_id,)).fetchone():
        raise ErrorAPI("No existe ese equipo", 404)

    fuera = bool(ctx["body"].get("fuera_servicio"))
    if fuera:
        observacion = (ctx["body"].get("observacion") or "").strip()
        if not observacion:
            raise ErrorAPI("Indicá por qué el equipo está fuera de servicio")
        ctx["conn"].execute(
            "INSERT INTO equipamiento_faltante (control_id, equipamiento_id, observacion) "
            "VALUES (?,?,?) ON CONFLICT (control_id, equipamiento_id) "
            "DO UPDATE SET observacion = excluded.observacion",
            (control_id, equipamiento_id, observacion))
    else:
        ctx["conn"].execute(
            "DELETE FROM equipamiento_faltante WHERE control_id = ? AND equipamiento_id = ?",
            (control_id, equipamiento_id))

    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "EQUIPAMIENTO_FUERA_SERVICIO" if fuera
                           else "EQUIPAMIENTO_REPUESTO",
                           "equipamiento_faltante", control_id,
                           {"equipamiento_id": equipamiento_id})
    ctx["conn"].commit()
    return {"ok": True, "fuera_servicio": fuera}


@ruta("POST", r"/api/controles/(\d+)/desvios")
def crear_desvio(ctx, control_id):
    r = services.registrar_desvio(
        ctx["conn"], int(control_id), int(ctx["body"].get("item_id") or 0),
        ctx["body"].get("estado"), ctx["body"].get("observacion") or "",
        ctx["sesion"]["usuario_id"])

    for foto in ctx["body"].get("fotos") or []:
        _guardar_foto(ctx["conn"], "desvio", r["desvio_id"], foto)
    ctx["conn"].commit()

    # La foto puede ser obligatoria por configuración (4.3).
    if db.get_config(ctx["conn"], "foto_obligatoria_desvio", True) \
            and ctx["body"].get("estado") != calc.NO_VERIFICABLE:
        n = ctx["conn"].execute(
            "SELECT COUNT(*) c FROM fotos WHERE entidad = 'desvio' AND entidad_id = ?",
            (r["desvio_id"],)).fetchone()["c"]
        r["falta_foto"] = n == 0
    return r


@ruta("DELETE", r"/api/controles/(\d+)/desvios/(\d+)")
def borrar_desvio(ctx, control_id, desvio_id):
    services._verificar_control_abierto(ctx["conn"], int(control_id))
    ctx["conn"].execute("DELETE FROM no_conformidades WHERE desvio_id = ?", (int(desvio_id),))
    ctx["conn"].execute("DELETE FROM desvios WHERE id = ? AND control_id = ?",
                        (int(desvio_id), int(control_id)))
    ctx["conn"].commit()
    return {"ok": True}


@ruta("POST", r"/api/controles/(\d+)/cerrar")
def cerrar(ctx, control_id):
    return services.cerrar_control(ctx["conn"], int(control_id),
                                   ctx["sesion"]["usuario_id"])


@ruta("POST", r"/api/controles/(\d+)/reabrir", rol="admin")
def reabrir(ctx, control_id):
    return services.reabrir_control(ctx["conn"], int(control_id),
                                    ctx["sesion"]["usuario_id"],
                                    ctx["body"].get("motivo") or "")


# ------------------------------------------------------------------ período --

@ruta("GET", r"/api/periodos/([\d-]+)/limpieza")
def resumen_limpieza(ctx, periodo):
    return services.resumen_mensual_limpieza(ctx["conn"], periodo)


@ruta("GET", r"/api/periodos/([\d-]+)/certificacion")
def get_certificacion(ctx, periodo):
    return services.certificacion(ctx["conn"], periodo)


@ruta("PUT", r"/api/periodos/([\d-]+)/datos", rol="admin")
def put_periodo_datos(ctx, periodo):
    campos = ("horas_hombre_programadas", "horas_hombre_perdidas",
              "documentacion_verificada", "hallazgos_documentacion",
              "ley_19587_verificada", "hallazgos_ley_19587", "monto_adjudicado")
    datos = {c: ctx["body"][c] for c in campos if c in ctx["body"]}
    if not datos:
        raise ErrorAPI(f"Nada para actualizar. Campos: {', '.join(campos)}")

    ctx["conn"].execute("INSERT OR IGNORE INTO periodo_datos (periodo) VALUES (?)", (periodo,))
    ctx["conn"].execute(
        f"UPDATE periodo_datos SET {', '.join(f'{c} = ?' for c in datos)} WHERE periodo = ?",
        (*datos.values(), periodo))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "DATOS_PERIODO", "periodo_datos", None,
                           {"periodo": periodo, **datos})
    ctx["conn"].commit()
    return {"ok": True, "periodo": periodo, **datos}


@ruta("GET", r"/api/periodos/([\d-]+)/datos")
def get_periodo_datos(ctx, periodo):
    fila = ctx["conn"].execute(
        "SELECT * FROM periodo_datos WHERE periodo = ?", (periodo,)).fetchone()
    return {"periodo": periodo, "datos": dict(fila) if fila else None}


@ruta("GET", r"/api/periodos/([\d-]+)/equipamiento")
def get_equipamiento_periodo(ctx, periodo):
    """Equipos que rigen el período y su disponibilidad acumulada (ítem 4)."""
    return {"periodo": periodo,
            "equipos": services.equipos_exigidos(ctx["conn"], periodo),
            "resultado": services.equipamiento_mensual(ctx["conn"], periodo)}


@ruta("PUT", r"/api/periodos/([\d-]+)/equipamiento", rol="admin")
def put_equipamiento_periodo(ctx, periodo):
    """Confirma qué equipos se exigen en el período.

    Se declara una vez al inicio del mes. Sin confirmación explícita el cálculo
    usa los marcados como exigidos en la configuración.
    """
    exigidos = ctx["body"].get("exigidos")
    if not isinstance(exigidos, list):
        raise ErrorAPI("Se espera 'exigidos' con la lista de IDs de equipos")

    validos = {f["id"] for f in ctx["conn"].execute(
        "SELECT id FROM equipamiento_limpieza")}
    desconocidos = [e for e in exigidos if e not in validos]
    if desconocidos:
        raise ErrorAPI(f"Equipos inexistentes: {desconocidos}", 404)

    ctx["conn"].execute("DELETE FROM periodo_equipamiento WHERE periodo = ?", (periodo,))
    for eid in validos:
        ctx["conn"].execute(
            "INSERT INTO periodo_equipamiento (periodo, equipamiento_id, exigido) "
            "VALUES (?,?,?)", (periodo, eid, 1 if eid in exigidos else 0))

    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "CONFIRMAR_EQUIPAMIENTO_PERIODO", "periodo_equipamiento",
                           None, {"periodo": periodo, "exigidos": exigidos})
    ctx["conn"].commit()
    return {"ok": True, "periodo": periodo, "exigidos": len(exigidos)}


@ruta("GET", r"/api/periodos/([\d-]+)/no-conformidades")
def listar_nc(ctx, periodo):
    return {"no_conformidades": [dict(f) for f in ctx["conn"].execute(
        "SELECT * FROM no_conformidades WHERE periodo = ? ORDER BY creado_en DESC",
        (periodo,))]}


@ruta("PUT", r"/api/no-conformidades/(\d+)")
def resolver_nc(ctx, nc_id):
    """Cualquier auditor puede cerrar la NC que dejó otro; queda en el log."""
    return services.resolver_nc(
        ctx["conn"], int(nc_id), ctx["body"].get("estado"),
        ctx["sesion"]["usuario_id"], ctx["body"].get("resolucion"))


@ruta("GET", r"/api/novedades")
def get_novedades(ctx):
    """Centro de novedades: lo que hay que saber al abrir la app.

    Se calcula al vuelo sobre los datos existentes, así que no puede quedar
    desfasado. El rol entra en juego porque la configuración pendiente solo le
    sirve al admin.
    """
    import datetime as _dt

    fecha = ctx["query"].get("fecha", [None])[0]
    try:
        hoy = _dt.date.fromisoformat(fecha) if fecha else _dt.date.today()
    except ValueError:
        raise ErrorAPI("Fecha inválida: se espera AAAA-MM-DD")
    return services.novedades(ctx["conn"], hoy,
                              es_admin=ctx["sesion"]["rol"] == "admin")


@ruta("GET", r"/api/no-conformidades/pendientes")
def nc_pendientes(ctx):
    """NC abiertas de auditorías anteriores a `fecha` (por defecto, hoy).

    No se filtra por período: el arrastre tiene que cruzar el fin de mes.
    """
    import datetime as _dt

    fecha = ctx["query"].get("fecha", [_dt.date.today().isoformat()])[0]
    try:
        _dt.date.fromisoformat(fecha)
    except ValueError:
        raise ErrorAPI("Fecha inválida: se espera AAAA-MM-DD")
    return {"fecha": fecha,
            "pendientes": services.nc_pendientes_anteriores(ctx["conn"], fecha)}


# ----------------------------------------------------------------------- LoS --

@ruta("GET", r"/api/los/items")
def listar_items_los(ctx):
    pendientes = {p["item"] for p in db.inventario_pendiente(ctx["conn"])}
    items = [{**dict(f), "aplica": bool(f["aplica"]),
              "requiere_configuracion": f["clave"] in pendientes}
             for f in ctx["conn"].execute("SELECT * FROM los_items ORDER BY orden")]
    return {"items": items}


@ruta("PUT", r"/api/los/items/(\w+)", rol="admin")
def editar_item_los(ctx, clave):
    """Activa o desactiva un ítem LoS (sección 4.3).

    Desactivar excluye el ítem de todos los cálculos: en IRJ es el caso de las
    pasarelas telescópicas, que el aeropuerto no posee.
    """
    fila = ctx["conn"].execute(
        "SELECT nombre FROM los_items WHERE clave = ?", (clave,)).fetchone()
    if not fila:
        raise ErrorAPI(f"No existe el ítem LoS '{clave}'", 404)
    if "aplica" not in ctx["body"]:
        raise ErrorAPI("Falta el campo 'aplica'")

    aplica = 1 if ctx["body"]["aplica"] else 0
    ctx["conn"].execute("UPDATE los_items SET aplica = ? WHERE clave = ?",
                        (aplica, clave))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "EDITAR_ITEM_LOS", "los_items", None,
                           {"clave": clave, "aplica": bool(aplica)})
    ctx["conn"].commit()
    return {"ok": True, "clave": clave, "aplica": bool(aplica)}


@ruta("GET", r"/api/los/relevamientos/actual")
def get_relevamiento_actual(ctx):
    """El relevamiento abierto del período y lo ya cargado, para prellenar la
    pantalla al volver a entrar. Sin relevamiento devuelve null: recién se crea
    cuando el auditor guarda la primera medición."""
    periodo = ctx["query"].get("periodo", [services.periodo_actual()])[0]
    rel = services.relevamiento_los_actual(ctx["conn"], periodo)
    mediciones = services.mediciones_relevamiento(ctx["conn"], rel["id"]) if rel else {}
    return {"periodo": periodo, "relevamiento": rel, "mediciones": mediciones}


@ruta("GET", r"/api/los/relevamientos/(\d+)/mediciones")
def get_medicion_del_dia(ctx, relevamiento_id):
    """Medición de un ítem diario en una fecha, para reabrirla y editarla."""
    item = ctx["query"].get("item", [None])[0]
    fecha = ctx["query"].get("fecha", [None])[0]
    if not item or not fecha:
        raise ErrorAPI("Faltan los parámetros 'item' y 'fecha'")
    return {"item": item, "fecha": fecha,
            "medicion": services.medicion_del_dia(
                ctx["conn"], int(relevamiento_id), item, fecha)}


@ruta("POST", r"/api/los/relevamientos")
def crear_relevamiento(ctx):
    """Idempotente: si ya hay un relevamiento abierto para el período, lo
    reutiliza en vez de crear uno nuevo. Sin esto, entrar a la pantalla de LoS
    varias veces en el mes iría acumulando relevamientos vacíos."""
    periodo = ctx["body"].get("periodo") or services.periodo_actual()
    relevamiento_id = services.obtener_o_crear_relevamiento_los(
        ctx["conn"], periodo, ctx["sesion"]["usuario_id"])
    return {"relevamiento_id": relevamiento_id, "periodo": periodo}


@ruta("POST", r"/api/los/relevamientos/(\d+)/reabrir", rol="admin")
def reabrir_relevamiento(ctx, relevamiento_id):
    return services.reabrir_relevamiento_los(
        ctx["conn"], int(relevamiento_id), ctx["sesion"]["usuario_id"],
        ctx["body"].get("motivo") or "")


@ruta("POST", r"/api/los/relevamientos/(\d+)/mediciones")
def crear_medicion(ctx, relevamiento_id):
    item = ctx["body"].get("item")
    if not item:
        raise ErrorAPI("Falta el campo 'item'")
    fecha = ctx["body"].get("fecha")
    resultado = services.guardar_medicion_los(
        ctx["conn"], int(relevamiento_id), item, ctx["body"].get("datos") or {},
        ctx["body"].get("observaciones"), fecha)

    # La foto va contra la medición del día, no contra "la del ítem": en un
    # ítem diario hay una por fecha.
    clave_fecha = fecha if services.periodicidad_item_los(
        ctx["conn"], item) == "DIARIO" else ""
    med = ctx["conn"].execute(
        "SELECT id FROM los_mediciones WHERE relevamiento_id = ? AND item_clave = ? "
        "AND fecha = COALESCE(?, fecha) ORDER BY fecha DESC LIMIT 1",
        (int(relevamiento_id), item, clave_fecha or None)).fetchone()
    for foto in ctx["body"].get("fotos") or []:
        _guardar_foto(ctx["conn"], "los_medicion", med["id"], foto)
    ctx["conn"].commit()
    return {"item": item, "resultado": resultado}


@ruta("POST", r"/api/los/relevamientos/(\d+)/cerrar")
def cerrar_relevamiento(ctx, relevamiento_id):
    # Cerrar un relevamiento que no existe no cambiaba ningún número —el estado
    # del relevamiento no entra en los cálculos— pero sí escribía en el log de
    # auditoría un cierre que nunca pasó. El historial es la defensa del auditor
    # si se discute un importe: no puede contener actos que no ocurrieron.
    if not ctx["conn"].execute(
            "SELECT id FROM relevamientos_los WHERE id = ?",
            (int(relevamiento_id),)).fetchone():
        raise ErrorAPI(f"No existe el relevamiento {relevamiento_id}", 404)

    ctx["conn"].execute(
        "UPDATE relevamientos_los SET estado = 'CERRADO', cerrado_en = datetime('now') "
        "WHERE id = ?", (int(relevamiento_id),))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "CERRAR_RELEVAMIENTO_LOS", "relevamientos_los",
                           int(relevamiento_id))
    ctx["conn"].commit()
    return {"ok": True}


@ruta("GET", r"/api/los/dashboard")
def get_dashboard_los(ctx):
    """`fecha` permite consultar el estado a un día dado; por defecto, hoy."""
    import datetime as _dt

    periodo = ctx["query"].get("periodo", [services.periodo_actual()])[0]
    fecha = ctx["query"].get("fecha", [None])[0]
    try:
        hoy = _dt.date.fromisoformat(fecha) if fecha else None
    except ValueError:
        raise ErrorAPI("Fecha inválida: se espera AAAA-MM-DD")
    return services.dashboard_los(ctx["conn"], periodo, hoy)


@ruta("GET", r"/api/los/limpieza-terminal/checklist")
def get_limpieza_terminal_checklist(ctx):
    """Grados que el check-list diario ya aporta al ítem 3.8.

    El formulario los muestra como valor por defecto para que el auditor vea
    qué está anulando cuando carga un grado a mano.
    """
    periodo = ctx["query"].get("periodo", [services.periodo_actual()])[0]
    return {"periodo": periodo,
            "derivado": services.limpieza_terminal_desde_checklist(
                ctx["conn"], periodo)}


@ruta("GET", r"/api/los/elevacion/eventos")
def listar_eventos_elevacion(ctx):
    periodo = ctx["query"].get("periodo", [services.periodo_actual()])[0]
    equipo_id = ctx["query"].get("equipo_id", [None])[0]
    return {"periodo": periodo, "eventos": services.eventos_elevacion(
        ctx["conn"], periodo, int(equipo_id) if equipo_id else None)}


@ruta("POST", r"/api/los/elevacion/eventos")
def crear_evento_elevacion(ctx):
    b = ctx["body"]
    equipo_id = b.get("equipo_id")
    if not equipo_id:
        raise ErrorAPI("Falta el equipo")
    if not ctx["conn"].execute(
            "SELECT 1 FROM medios_elevacion WHERE id = ?", (equipo_id,)).fetchone():
        raise ErrorAPI("No existe ese medio de elevación", 404)

    horas = b.get("horas")
    if horas is None or isinstance(horas, bool) or not isinstance(horas, (int, float)) \
            or horas < 0:
        raise ErrorAPI("Las horas de indisponibilidad deben ser un número de 0 o más")
    if not b.get("inicio"):
        raise ErrorAPI("Falta la fecha/hora de inicio del evento")

    cur = ctx["conn"].execute(
        "INSERT INTO elevacion_eventos (equipo_id, periodo, inicio, fin, horas, motivo) "
        "VALUES (?,?,?,?,?,?)",
        (int(equipo_id), b.get("periodo") or services.periodo_actual(),
         b.get("inicio"), b.get("fin"), horas, b.get("motivo")))
    ctx["conn"].commit()
    return {"evento_id": cur.lastrowid}


@ruta("DELETE", r"/api/los/elevacion/eventos/(\d+)", rol="admin")
def borrar_evento_elevacion(ctx, evento_id):
    ctx["conn"].execute("DELETE FROM elevacion_eventos WHERE id = ?", (int(evento_id),))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "BAJA_EVENTO_ELEVACION", "elevacion_eventos", int(evento_id))
    ctx["conn"].commit()
    return {"ok": True}


# ---------------------------------------------------------------------- sync --

@ruta("POST", r"/api/sync")
def sync(ctx):
    """Cola de operaciones de la PWA.

    Cada operación viaja con un uuid propio: si la tablet reintenta tras una
    caída de red, la operación ya aplicada se saltea en lugar de duplicarse.
    """
    resultados = []
    for op in ctx["body"].get("operaciones") or []:
        uuid = op.get("uuid")
        if not uuid:
            resultados.append({"uuid": None, "estado": "ERROR",
                               "error": "Falta uuid de la operación"})
            continue

        previa = ctx["conn"].execute(
            "SELECT resultado FROM sync_operaciones WHERE uuid = ?", (uuid,)).fetchone()
        if previa:
            resultados.append({"uuid": uuid, "estado": "DUPLICADA",
                               "resultado": json.loads(previa["resultado"] or "null")})
            continue

        try:
            r = _despachar(ctx, op["metodo"], op["ruta"], op.get("body") or {})
            ctx["conn"].execute(
                "INSERT INTO sync_operaciones (uuid, resultado) VALUES (?,?)",
                (uuid, json.dumps(r, ensure_ascii=False, default=str)))
            ctx["conn"].commit()
            resultados.append({"uuid": uuid, "estado": "OK", "resultado": r})
        except Exception as e:
            # Se descarta lo que la operación fallida haya alcanzado a escribir,
            # y el lote sigue con la siguiente. Sin este rollback, en Postgres
            # un error deja la transacción abortada y arrastra al resto de la
            # cola: el auditor perdería toda la recorrida por un solo desvío
            # rechazado.
            ctx["conn"].rollback()
            resultados.append({"uuid": uuid, "estado": "ERROR", "error": str(e)})

    return {"resultados": resultados}


# --------------------------------------------------------------------- fotos --

def _guardar_foto(conn, entidad: str, entidad_id: int, foto) -> None:
    """Guarda la evidencia en el árbol de carpetas por período (ver fotos.py).

    `foto` puede ser una data URL suelta o un objeto
    {"data": ..., "subitem": ...} cuando la evidencia corresponde a un
    sub-ítem puntual de la medición.
    """
    if isinstance(foto, dict):
        data_url, subitem = foto.get("data"), foto.get("subitem")
    else:
        data_url, subitem = foto, None

    contexto = (fotos.contexto_desvio(conn, entidad_id) if entidad == "desvio"
                else fotos.contexto_los(conn, entidad_id) if entidad == "los_medicion"
                else {})
    try:
        fotos.guardar(conn, UPLOADS_DIR, entidad, entidad_id, data_url,
                      contexto, subitem)
    except fotos.ErrorFoto as e:
        codigo = 413 if "tamaño máximo" in str(e) else 400
        raise ErrorAPI(str(e), codigo)


@ruta("GET", r"/api/fotos/([\w./-]+)")
def get_foto(ctx, nombre):
    """Sirve una foto por su ruta relativa.

    La ruta ahora tiene barras (período/módulo/día), así que no alcanza con
    rechazar '/': `ruta_segura` verifica que el destino resuelto siga dentro
    del directorio de uploads.
    """
    try:
        binario = almacen.obtener(UPLOADS_DIR).leer(nombre)
    except almacen.ErrorAlmacen as e:
        # Ruta inválida es del cliente; el resto es el almacenamiento caído.
        raise ErrorAPI(str(e), 400 if "inválida" in str(e) else 502)
    if binario is None:
        raise ErrorAPI("Foto no encontrada", 404)
    # Única respuesta de la API que sí se cachea, y puede hacerlo para siempre:
    # el nombre lleva fecha, hora y un sufijo aleatorio (ver `fotos.py`), así
    # que una ruta identifica un archivo que nunca cambia. Sin esto la misma
    # foto viajaba de Storage a la tablet cada vez que se abría el sector o el
    # informe que la muestra. `private`: la evidencia no puede quedar en cachés
    # compartidas del camino.
    return {"__binario__": binario,
            "__tipo__": mimetypes.guess_type(nombre)[0] or "image/jpeg",
            "__cache__": "private, max-age=31536000, immutable"}


# ------------------------------------------------------------------ informes --

def _emisor(ctx) -> dict:
    """Datos del auditor que emite el informe, para el bloque de firma."""
    import datetime as _dt
    s = ctx["sesion"]
    return {"nombre": s["nombre"], "usuario": s["usuario"],
            "emitido": _dt.datetime.now().strftime("%d/%m/%Y %H:%M")}


@ruta("GET", r"/api/periodos/([\d-]+)/informe/limpieza")
def informe_limpieza_pdf(ctx, periodo):
    """Informe mensual de limpieza (5.1). `fotos=0` genera una versión liviana
    sin evidencia, útil para revisar antes de firmar."""
    incluir = ctx["query"].get("fotos", ["1"])[0] != "0"
    datos = informes.informe_limpieza(ctx["conn"], periodo, incluir, _emisor(ctx))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "DESCARGA_INFORME", None, None,
                           {"tipo": "limpieza", "periodo": periodo})
    ctx["conn"].commit()
    return {"__binario__": datos, "__tipo__": "application/pdf",
            "__descarga__": f"informe-limpieza-{periodo}.pdf"}


@ruta("GET", r"/api/controles/fecha/([\d-]+)/informe")
def informe_dia_pdf(ctx, fecha):
    """Informe de un control diario, con su evidencia fotográfica."""
    turno = ctx["query"].get("turno", [None])[0]
    if turno and turno not in calc.TURNOS:
        raise ErrorAPI(f"Turno inválido: debe ser uno de {', '.join(calc.TURNOS)}")
    try:
        datos = informes.informe_dia(ctx["conn"], fecha, _emisor(ctx), turno)
    except LookupError as e:
        raise ErrorAPI(str(e), 404)
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "DESCARGA_INFORME", None, None,
                           {"tipo": "diario", "fecha": fecha})
    ctx["conn"].commit()
    return {"__binario__": datos, "__tipo__": "application/pdf",
            "__descarga__": f"control-{fecha}.pdf"}


@ruta("GET", r"/api/periodos/([\d-]+)/informe/los")
def informe_los_pdf(ctx, periodo):
    datos = informes.informe_los(ctx["conn"], periodo, _emisor(ctx))
    services.registrar_log(ctx["conn"], ctx["sesion"]["usuario_id"],
                           "DESCARGA_INFORME", None, None,
                           {"tipo": "los", "periodo": periodo})
    ctx["conn"].commit()
    return {"__binario__": datos, "__tipo__": "application/pdf",
            "__descarga__": f"informe-los-{periodo}.pdf"}


@ruta("GET", r"/api/periodos/([\d-]+)/export/([\w-]+)")
def export_csv(ctx, periodo, recurso):
    entrada = informes.EXPORTS.get(recurso)
    if not entrada:
        raise ErrorAPI(
            "Export desconocido. Disponibles: "
            + ", ".join(sorted(informes.EXPORTS)), 404)
    generador, nombre = entrada
    return {"__binario__": generador(ctx["conn"], periodo),
            "__tipo__": "text/csv; charset=utf-8",
            "__descarga__": f"{nombre}-{periodo}.csv"}


# ==========================================================================
# Despacho
# ==========================================================================

def _despachar(ctx, metodo: str, camino: str, body: dict):
    parsed = urlparse(camino)
    for m, patron, fn, rol in RUTAS:
        if m != metodo:
            continue
        match = patron.match(parsed.path)
        if not match:
            continue

        if rol != "publico":
            if not ctx.get("sesion"):
                raise ErrorAPI("Sesión requerida", 401)
            if rol == "admin" and ctx["sesion"]["rol"] != "admin":
                raise ErrorAPI("Requiere rol de administrador", 403)

        sub = dict(ctx, body=body, query=parse_qs(parsed.query))
        return fn(sub, *match.groups())

    raise ErrorAPI(f"Ruta no encontrada: {metodo} {parsed.path}", 404)


class Handler(BaseHTTPRequestHandler):
    server_version = "ControlesIRJ"
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):
        if os.environ.get("IRJ_VERBOSE"):
            super().log_message(formato, *args)

    # -- helpers --
    def _responder(self, codigo: int, payload, tipo="application/json", cache=None,
                   descarga=None):
        if isinstance(payload, (bytes, bytearray)):
            cuerpo = payload
        else:
            cuerpo = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if descarga:
            # El nombre se genera en el servidor a partir del período, así que
            # no puede traer comillas ni saltos; aun así se sanea por si acaso.
            limpio = re.sub(r'[^\w.\-]', '_', descarga)
            self.send_header("Content-Disposition",
                             f'attachment; filename="{limpio}"')
        # Las respuestas de la API nunca se cachean: contienen estado de
        # auditoría que no puede servirse viejo.
        self.send_header("Cache-Control", cache or "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _sesion(self, conn):
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else None
        if not token:
            return None, None

        fila = conn.execute(
            "SELECT s.token, s.ultimo_uso, u.id usuario_id, u.usuario, u.nombre, u.rol "
            "FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id "
            "WHERE s.token = ? AND u.activo = 1 "
            "AND s.ultimo_uso >= datetime('now', ?)",
            (token, f"-{SESION_DIAS} days")).fetchone()
        if not fila:
            return token, None

        # `ultimo_uso` solo marca actividad para vencer la sesión a los 30 días,
        # así que refrescarlo en cada request no aporta nada y cuesta una
        # escritura más un commit en el camino crítico de todas las llamadas.
        # Se refresca cada FRESCURA_SESION segundos, que a la escala de un
        # vencimiento de 30 días es indistinguible.
        if _conviene_refrescar_sesion(fila["ultimo_uso"]):
            conn.execute("UPDATE sesiones SET ultimo_uso = datetime('now') "
                         "WHERE token = ?", (token,))
            conn.commit()
        return token, {"usuario_id": fila["usuario_id"], "rol": fila["rol"],
                       "usuario": fila["usuario"], "nombre": fila["nombre"]}

    def _body(self):
        largo = int(self.headers.get("Content-Length") or 0)
        if largo == 0:
            return {}
        if largo > MAX_BODY:
            raise ErrorAPI("El cuerpo de la petición es demasiado grande", 413)
        crudo = self.rfile.read(largo)
        try:
            return json.loads(crudo)
        except json.JSONDecodeError:
            raise ErrorAPI("JSON inválido")

    def _ip_cliente(self):
        """IP de quien pide, para el freno de intentos de login.

        Detrás del borde de Vercel la conexión viene del proxy, así que la IP
        real está en X-Forwarded-For; el primer valor de la lista es el cliente
        y el resto son los saltos intermedios. Sirviendo directo con
        `http.server` no hay proxy y se usa la dirección de la conexión.
        """
        reenviada = self.headers.get("X-Forwarded-For", "")
        if reenviada:
            return reenviada.split(",")[0].strip()
        try:
            return self.client_address[0]
        except (AttributeError, IndexError):
            return None

    def _manejar(self, metodo):
        camino = urlparse(self.path).path
        if not camino.startswith("/api/"):
            return self._servir_estatico(camino)

        conn = None
        try:
            body = self._body()
            conn = db.tomar_conexion()
            token, sesion = self._sesion(conn)
            resultado = _despachar(
                {"conn": conn, "sesion": sesion, "token": token,
                 "ip": self._ip_cliente()},
                metodo, self.path, body)

            if isinstance(resultado, dict) and "__binario__" in resultado:
                return self._responder(200, resultado["__binario__"],
                                       resultado["__tipo__"],
                                       cache=resultado.get("__cache__"),
                                       descarga=resultado.get("__descarga__"))
            self._responder(200, resultado)

        except ErrorAPI as e:
            self._responder(e.codigo, {"error": e.mensaje})
        except PermissionError as e:
            self._responder(403, {"error": str(e)})
        except LookupError as e:
            self._responder(404, {"error": str(e)})
        except ValueError as e:
            self._responder(400, {"error": str(e)})
        except Exception as e:                      # noqa: BLE001
            # No filtrar el detalle interno al cliente; queda en el log del server.
            import traceback
            traceback.print_exc()
            self._responder(500, {"error": "Error interno del servidor"})
        finally:
            if conn:
                db.devolver_conexion(conn)

    def _servir_estatico(self, camino):
        if camino in ("/", ""):
            camino = "/index.html"
        destino = os.path.normpath(os.path.join(FRONTEND_DIR, camino.lstrip("/")))
        # El separador final importa: sin él, un directorio hermano llamado
        # "frontend-viejo" pasaría el startswith de "…/frontend" y quedaría
        # servible desde la web.
        dentro = destino == FRONTEND_DIR or destino.startswith(FRONTEND_DIR + os.sep)
        if not dentro or not os.path.isfile(destino):
            # SPA: cualquier ruta desconocida devuelve el index.
            destino = os.path.join(FRONTEND_DIR, "index.html")
            if not os.path.isfile(destino):
                return self._responder(404, {"error": "Frontend no encontrado"})

        with open(destino, "rb") as f:
            contenido = f.read()
        tipo = mimetypes.guess_type(destino)[0] or "application/octet-stream"

        # El shell se revalida siempre. Sin esto el navegador aplica caché
        # heurística y una tablet puede seguir ejecutando código viejo después
        # de actualizar la app; el service worker es quien resuelve el offline.
        es_shell = destino.endswith((".html", ".js", ".css", ".webmanifest"))
        self._responder(200, contenido, tipo,
                        cache="no-cache" if es_shell else "public, max-age=86400")

    def do_GET(self):     self._manejar("GET")      # noqa: E704
    def do_POST(self):    self._manejar("POST")     # noqa: E704
    def do_PUT(self):     self._manejar("PUT")      # noqa: E704
    def do_DELETE(self):  self._manejar("DELETE")   # noqa: E704


def crear_servidor(puerto: int = 8080):
    return ThreadingHTTPServer(("0.0.0.0", puerto), Handler)


if __name__ == "__main__":
    conn, resumen = db.inicializar()
    if "admin_password" in resumen:
        print(f"⚠  Usuario 'admin' creado. Contraseña: {resumen['admin_password']}")
        print("   Cambiala en el primer inicio de sesión.")
    conn.close()

    puerto = int(os.environ.get("PORT", 8080))
    print(f"Controles Operativos IRJ — http://localhost:{puerto}")
    crear_servidor(puerto).serve_forever()
