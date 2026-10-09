"""Hora local: todo lo que el sistema fecha, lo fecha en Argentina.

El defecto que esto cierra no se ve en la máquina de desarrollo, que está en
hora argentina, y por eso necesita tests que fuercen la diferencia: en Vercel el
servidor corre en UTC, y a partir de las 21:00 locales `date.today()` ya es
mañana. La regla de oro del sistema —no se abre la recorrida de otro día— se
evalúa contra "hoy", así que un "hoy" adelantado dejaba al auditor de la tarde
sin poder abrir su propio control.
"""

import datetime
import os
import re
import sys
import unittest
from unittest import mock

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ = os.path.dirname(os.path.dirname(BACKEND))
sys.path.insert(0, BACKEND)

import api  # noqa: E402
import basedeprueba  # noqa: E402
import db  # noqa: E402
import pgcompat  # noqa: E402
import reloj  # noqa: E402


def _leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read()


def _segundos_entre(a: str, b: str) -> float:
    formato = reloj.FORMATO_MARCA
    return abs((datetime.datetime.strptime(a, formato)
                - datetime.datetime.strptime(b, formato)).total_seconds())


class TestReloj(unittest.TestCase):
    def test_ahora_esta_tres_horas_detras_de_utc(self):
        utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        diferencia = (utc - reloj.ahora()).total_seconds()
        self.assertAlmostEqual(diferencia, 3 * 3600, delta=5)

    def test_hoy_a_las_diez_de_la_noche_sigue_siendo_hoy(self):
        """22:30 en Argentina son las 01:30 del día siguiente en UTC. El reloj
        del servidor diría mañana; el del aeropuerto, hoy."""
        noche = datetime.datetime(2031, 3, 10, 22, 30)
        with mock.patch.object(reloj, "ahora", return_value=noche):
            self.assertEqual(reloj.hoy(), datetime.date(2031, 3, 10))
            self.assertEqual(reloj.marca(), "2031-03-10 22:30:00")

    def test_la_marca_tiene_el_formato_que_compara_la_base(self):
        self.assertRegex(reloj.marca(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class TestReglaDeOroEnLaNoche(unittest.TestCase):
    """La consecuencia visible: abrir el control de hoy después de las 21:00."""

    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="admin1234")
        self.conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('noche','Noche',?, 'auditor')", (db.hash_password("x" * 8),))
        self.conn.commit()
        self.uid = self.conn.execute(
            "SELECT id FROM usuarios WHERE usuario = 'noche'").fetchone()["id"]

    def tearDown(self):
        self.conn.close()

    def _abrir(self, fecha):
        return api.crear_control({"body": {"fecha": fecha}, "conn": self.conn,
                                  "sesion": {"usuario_id": self.uid}})

    def test_a_las_diez_de_la_noche_se_abre_el_control_de_hoy(self):
        noche = datetime.datetime(2031, 3, 10, 22, 30)
        with mock.patch.object(reloj, "ahora", return_value=noche):
            r = self._abrir("2031-03-10")
        self.assertEqual(r["fecha"], "2031-03-10")

    def test_a_las_diez_de_la_noche_el_dia_siguiente_sigue_siendo_futuro(self):
        """Con el reloj de UTC este era el control que SÍ se podía abrir, y el
        de hoy el que se rechazaba como 'fecha anterior'."""
        noche = datetime.datetime(2031, 3, 10, 22, 30)
        with mock.patch.object(reloj, "ahora", return_value=noche):
            with self.assertRaises(api.ErrorAPI) as cm:
                self._abrir("2031-03-11")
        self.assertIn("futura", str(cm.exception))


class TestSQLiteEnHoraLocal(unittest.TestCase):
    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="admin1234")

    def tearDown(self):
        self.conn.close()

    def test_now_es_hora_de_argentina(self):
        r = self.conn.execute("SELECT datetime('now')").fetchone()[0]
        self.assertLessEqual(_segundos_entre(r, reloj.marca()), 5)

    def test_los_default_del_esquema_guardan_hora_local(self):
        """El caso real: 21 columnas se llenan solas con el DEFAULT."""
        self.conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('u','U','h','auditor')")
        creado = self.conn.execute(
            "SELECT creado_en FROM usuarios WHERE usuario = 'u'").fetchone()[0]
        self.assertLessEqual(_segundos_entre(creado, reloj.marca()), 5)

    def test_now_con_modificador_resta_desde_la_hora_local(self):
        r = self.conn.execute("SELECT datetime('now', '-30 days')").fetchone()[0]
        esperado = (reloj.ahora() - datetime.timedelta(days=30)).strftime(
            reloj.FORMATO_MARCA)
        self.assertLessEqual(_segundos_entre(r, esperado), 5)

    def test_now_con_modificador_como_parametro(self):
        """Así lo usa la app para vencer sesiones y ventanas de login."""
        r = self.conn.execute("SELECT datetime('now', ?)", ("-15 minutes",)).fetchone()[0]
        esperado = (reloj.ahora() - datetime.timedelta(minutes=15)).strftime(
            reloj.FORMATO_MARCA)
        self.assertLessEqual(_segundos_entre(r, esperado), 5)

    def test_una_marca_ya_formada_no_se_desplaza(self):
        """Solo 'now' se reinterpreta. Una fecha concreta se devuelve tal cual."""
        r = self.conn.execute(
            "SELECT datetime('2026-08-05 13:21:00')").fetchone()[0]
        self.assertEqual(r, "2026-08-05 13:21:00")
        r = self.conn.execute(
            "SELECT datetime('2026-08-05 13:21:00', '+1 hours')").fetchone()[0]
        self.assertEqual(r, "2026-08-05 14:21:00")

    def test_los_valores_nulos_siguen_siendo_nulos(self):
        self.assertIsNone(self.conn.execute("SELECT datetime(NULL)").fetchone()[0])


class TestMigracionDeLosDatosViejos(unittest.TestCase):
    """Las filas ya guardadas en UTC se corrigen una vez, y solo una."""

    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="admin1234")
        # Se simula una base de antes del cambio: sin constancia de la migración.
        self.conn.execute("DELETE FROM migraciones_aplicadas")
        self.conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol, creado_en) "
            "VALUES ('viejo','V','h','auditor','2026-08-05 16:21:00')")
        self.conn.execute(
            "INSERT INTO intentos_login (clave, momento) VALUES "
            "('usuario:viejo','2026-08-05 16:21:00')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _creado(self):
        return self.conn.execute(
            "SELECT creado_en FROM usuarios WHERE usuario = 'viejo'").fetchone()[0]

    def test_se_restan_tres_horas(self):
        db.migrar(self.conn)
        self.assertEqual(self._creado(), "2026-08-05 13:21:00")

    def test_correrla_dos_veces_no_resta_seis(self):
        """La razón de ser de la constancia: repetir el script no puede dañar."""
        db.migrar(self.conn)
        db.migrar(self.conn)
        db.migrar(self.conn)
        self.assertEqual(self._creado(), "2026-08-05 13:21:00")

    def test_queda_constancia(self):
        db.migrar(self.conn)
        self.assertTrue(db.migracion_aplicada(self.conn, db.MIGRACION_HORA_LOCAL))

    def test_una_fecha_sin_hora_no_se_corre_de_dia(self):
        """'2026-08-05' menos tres horas sería '2026-08-04 21:00:00'."""
        self.conn.execute(
            "INSERT INTO relevamientos_los (periodo, auditor_id, fecha) "
            "VALUES ('2026-08', 1, '2026-08-05')")
        self.conn.commit()
        db.migrar(self.conn)
        fecha = self.conn.execute(
            "SELECT fecha FROM relevamientos_los").fetchone()[0]
        self.assertEqual(fecha, "2026-08-05")

    def test_los_intentos_de_login_se_descartan(self):
        db.migrar(self.conn)
        n = self.conn.execute("SELECT COUNT(*) FROM intentos_login").fetchone()[0]
        self.assertEqual(n, 0)

    def test_una_base_nueva_queda_registrada_como_migrada(self):
        conn, _ = basedeprueba.nueva(admin_password="admin1234")
        try:
            self.assertTrue(db.migracion_aplicada(conn, db.MIGRACION_HORA_LOCAL))
        finally:
            conn.close()

    def test_sin_tabla_de_constancias_no_se_consulta(self):
        """En Postgres una consulta fallida aborta la transacción: el chequeo de
        salud tiene que poder responder 'no aplicada' sin tocar la tabla."""
        conn, _ = basedeprueba.nueva(admin_password="admin1234")
        try:
            conn.execute("DROP TABLE migraciones_aplicadas")
            self.assertFalse(db.migracion_aplicada(conn, db.MIGRACION_HORA_LOCAL))
        finally:
            conn.close()


class TestPostgresEnHoraLocal(unittest.TestCase):
    def test_la_funcion_de_la_base_mide_en_hora_de_argentina(self):
        self.assertIn("America/Argentina/Buenos_Aires", pgcompat.FUNCIONES_SQL)
        self.assertNotIn("'UTC'", pgcompat.FUNCIONES_SQL)

    def test_el_script_para_supabase_esta_al_dia(self):
        """Se genera desde `db.py`: si alguien agrega una columna a la lista y
        no regenera el archivo, el script que se corre a mano quedaría corto."""
        archivo = os.path.join(RAIZ, "migracion-hora-local.sql")
        self.assertEqual(
            _leer(archivo), db.sql_hora_local_postgres(),
            "migracion-hora-local.sql está desactualizado. Regenerar con:\n"
            "  python3 -c \"import sys; sys.path.insert(0,'app/backend'); "
            "import db; print(db.sql_hora_local_postgres(), end='')\" "
            "> migracion-hora-local.sql")

    def test_el_script_se_niega_a_repetirse(self):
        sql = db.sql_hora_local_postgres()
        self.assertIn("IF EXISTS (SELECT 1 FROM migraciones_aplicadas", sql)
        self.assertIn(db.MIGRACION_HORA_LOCAL, sql)
        # Todo en una transacción: función, corrección y constancia juntas.
        self.assertTrue(sql.index("BEGIN;") < sql.index("CREATE OR REPLACE FUNCTION"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))

    def test_el_script_corrige_todas_las_columnas_con_marca(self):
        sql = db.sql_hora_local_postgres()
        for tabla, columna in db.COLUMNAS_CON_MARCA:
            self.assertIn(f"UPDATE {tabla} SET {columna} =", sql)

    def test_el_esquema_de_supabase_usa_la_misma_funcion(self):
        esquema = _leer(os.path.join(RAIZ, "esquema-supabase.sql"))
        self.assertIn("America/Argentina/Buenos_Aires", esquema)
        self.assertNotIn("AT TIME ZONE 'UTC'", esquema)
        self.assertIn("CREATE TABLE IF NOT EXISTS migraciones_aplicadas", esquema)

    def test_toda_columna_con_marca_del_esquema_esta_en_la_lista(self):
        """Una columna nueva con `DEFAULT (datetime('now'))` nace en hora local y
        no necesita corrección; pero una que ya existía y se olvidó de la lista
        queda mezclada para siempre. Este test obliga a decidir cada una."""
        ddl = _leer(os.path.join(BACKEND, "schema.sql"))
        con_default, tabla = set(), None
        for linea in ddl.splitlines():
            m = re.match(r"\s*CREATE TABLE IF NOT EXISTS (\w+)", linea)
            if m:
                tabla = m.group(1)
            m = re.match(r"\s+(\w+)\s+TEXT\b.*DEFAULT \(datetime\('now'\)\)", linea)
            if m and tabla:
                con_default.add((tabla, m.group(1)))
        esperadas = set(db.COLUMNAS_CON_MARCA)
        efimeras = {(t, "momento") for t in db.TABLAS_EFIMERAS}
        # La constancia nace con la migración: nunca hubo valores en UTC.
        exentas = {("migraciones_aplicadas", "aplicada_en")}
        self.assertEqual(con_default - esperadas - efimeras - exentas, set(),
                         "columnas con hora automática que la migración no corrige")
        # Las que no tienen DEFAULT las llena el código con `= datetime('now')`
        # (cierre de un control, confirmación de un sector, resolución de una
        # NC). Se comprueba que sea así y no se da por supuesto: una columna que
        # dejó de llenarse con la hora no tiene nada que corregir en la lista.
        codigo = _leer(os.path.join(BACKEND, "services.py")) \
            + _leer(os.path.join(BACKEND, "api.py"))
        sin_default = esperadas - con_default
        for tabla, columna in sorted(sin_default):
            self.assertIn(f"{columna} = datetime('now')", codigo,
                          f"{tabla}.{columna}: ni DEFAULT ni asignada por el código")


class TestNadieUsaElRelojDelServidor(unittest.TestCase):
    """Que el servidor esté en UTC no se nota en desarrollo: ahí las dos formas
    dan lo mismo. Sin este test el olvido aparece recién en producción."""

    PROHIBIDO = re.compile(
        r"\b(?:date\.today|datetime\.now|datetime\.utcnow|datetime\.today|"
        r"time\.localtime|time\.gmtime|utcfromtimestamp)\s*\(")

    def test_el_backend_pide_la_hora_a_reloj(self):
        infractores = []
        for nombre in sorted(os.listdir(BACKEND)):
            if not nombre.endswith(".py") or nombre == "reloj.py":
                continue
            for n, linea in enumerate(_leer(os.path.join(BACKEND, nombre)).splitlines(), 1):
                codigo = linea.split("#", 1)[0]
                if self.PROHIBIDO.search(codigo):
                    infractores.append(f"{nombre}:{n}: {linea.strip()}")
        self.assertEqual(infractores, [],
                         "Usar `reloj.hoy()` / `reloj.ahora()`: el servidor "
                         "corre en UTC y su 'hoy' se adelanta a las 21:00.")


class TestSaludAvisaLaMigracionPendiente(unittest.TestCase):
    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="admin1234")

    def tearDown(self):
        self.conn.close()

    def test_una_base_migrada_no_reporta_nada(self):
        problemas = api.chequeos_estructurales(self.conn)
        self.assertEqual([p for p in problemas if "migracion" in p], [])

    def test_una_base_sin_migrar_lo_dice_en_version(self):
        """Es el aviso para quien despliega el código antes de correr el script
        en Supabase: las filas viejas siguen en UTC."""
        self.conn.execute("DELETE FROM migraciones_aplicadas")
        problemas = api.chequeos_estructurales(self.conn)
        self.assertTrue(any("migracion-hora-local.sql" in p for p in problemas),
                        problemas)

    def test_sin_siquiera_la_tabla_tambien_lo_dice(self):
        self.conn.execute("DROP TABLE migraciones_aplicadas")
        problemas = api.chequeos_estructurales(self.conn)
        self.assertTrue(any("migracion-hora-local.sql" in p for p in problemas),
                        problemas)


if __name__ == "__main__":
    unittest.main()
