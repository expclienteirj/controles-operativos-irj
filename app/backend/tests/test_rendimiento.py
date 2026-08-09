"""Tests de las decisiones tomadas para que la app no se sienta lenta.

Son tests de comportamiento, no de velocidad: no miden milisegundos —eso
dependería de la máquina que los corra— sino las propiedades de las que sale
la velocidad, que sí son verificables y son las que se rompen sin que nadie se
entere:

  * el estado de N controles cuesta un número FIJO de consultas (si alguien
    vuelve a meter una consulta adentro de un bucle, esto lo detecta);
  * `sesiones.ultimo_uso` no se reescribe en cada request;
  * la conexión a Postgres se reutiliza entre requests, y volver al pool no
    arrastra transacciones a medio hacer.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import db  # noqa: E402
import services  # noqa: E402
import basedeprueba  # noqa: E402


class ConexionContada:
    """Envuelve una conexión y cuenta las consultas que la atraviesan."""

    def __init__(self, real):
        self._real = real
        self.consultas = 0

    def execute(self, sql, params=()):
        self.consultas += 1
        return self._real.execute(sql, params)

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)


class TestConsultasPorControl(unittest.TestCase):
    """El costo del informe mensual no puede crecer con los días auditados."""

    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="x")
        self.usuario = self.conn.execute(
            "SELECT id FROM usuarios LIMIT 1").fetchone()["id"]

    def tearDown(self):
        self.conn.close()

    def _control(self, fecha, turno="MANANA"):
        cur = self.conn.execute(
            "INSERT INTO controles_limpieza (fecha, turno, periodo, auditor_id) "
            "VALUES (?,?,?,?)", (fecha, turno, fecha[:7], self.usuario))
        cid = cur.lastrowid
        for s in self.conn.execute(
                "SELECT id FROM sectores_limpieza WHERE activo = 1"):
            services.confirmar_sector(self.conn, cid, s["id"], self.usuario)
        self.conn.commit()
        return cid

    def _consultas_para(self, cantidad):
        ids = [self._control(f"2026-03-{d:02d}") for d in range(1, cantidad + 1)]
        contada = ConexionContada(self.conn)
        estados = services.estados_controles(contada, ids)
        self.assertEqual(len(estados), cantidad)
        return contada.consultas

    def test_el_costo_no_crece_con_la_cantidad_de_controles(self):
        uno = self._consultas_para(1)
        self.setUp()
        diez = self._consultas_para(10)
        self.assertEqual(
            uno, diez,
            f"Resolver 10 controles costó {diez} consultas y resolver 1 costó "
            f"{uno}: volvió a haber una consulta por control.")

    def test_son_pocas_consultas_en_valor_absoluto(self):
        # Deja margen para que el esquema crezca, pero no para que vuelva un
        # bucle: con nueve sectores, la versión vieja gastaba 32 por control.
        self.assertLessEqual(self._consultas_para(5), 12)

    def test_da_lo_mismo_que_resolverlos_de_a_uno(self):
        ids = [self._control(f"2026-03-{d:02d}") for d in (1, 2, 3)]
        lote = services.estados_controles(self.conn, ids)
        for cid in ids:
            self.assertEqual(lote[cid], services.estado_control(self.conn, cid))

    def test_control_inexistente_no_rompe(self):
        # `_equipamiento_control` caía al período en curso cuando el control no
        # existía; la versión en lote tiene que hacer lo mismo.
        estado = services.estado_control(self.conn, 99999)
        self.assertEqual(estado["control_id"], 99999)
        self.assertIsNone(estado["porcentaje_general"])


class TestFrescuraDeSesion(unittest.TestCase):
    """`ultimo_uso` marca actividad para vencer a los 30 días, no cada request."""

    def test_una_marca_recien_puesta_no_se_reescribe(self):
        import datetime as dt
        ahora = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        self.assertFalse(api._conviene_refrescar_sesion(
            ahora.strftime("%Y-%m-%d %H:%M:%S")))

    def test_una_marca_vieja_se_refresca(self):
        import datetime as dt
        vieja = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                 - dt.timedelta(seconds=api.FRESCURA_SESION + 60))
        self.assertTrue(api._conviene_refrescar_sesion(
            vieja.strftime("%Y-%m-%d %H:%M:%S")))

    def test_una_marca_ilegible_se_refresca(self):
        # Perder la marca vencería la sesión de alguien que sí está trabajando:
        # ante la duda, se escribe.
        for valor in (None, "", "ayer", 12345):
            self.assertTrue(api._conviene_refrescar_sesion(valor), valor)


class ConexionFalsa:
    """Se hace pasar por `ConexionPG` para ejercitar el pool sin un Postgres.

    `db.devolver_conexion` decide por el nombre de la clase, que es como el
    resto del backend distingue los dos motores (ver `db._es_postgres`).
    """

    def __init__(self):
        self.cerrada = False
        self.rollbacks = 0
        self.cierres = 0

    def execute(self, sql, params=()):
        class Cursor:
            def fetchone(self):
                return {"?column?": 1}
        return Cursor()

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.cerrada = True
        self.cierres += 1


ConexionFalsa.__name__ = "ConexionPG"


class TestPoolDeConexiones(unittest.TestCase):
    """Abrir una conexión a Supabase cuesta ~100 ms; no se paga por request."""

    def setUp(self):
        db.cerrar_pool()
        self._url_original = db.DB_URL
        db.DB_URL = "postgresql://falsa/irj"      # activa la rama de Postgres

    def tearDown(self):
        db.cerrar_pool()
        db.DB_URL = self._url_original

    def test_la_conexion_devuelta_se_vuelve_a_entregar(self):
        primera = ConexionFalsa()
        db.devolver_conexion(primera)
        self.assertIs(db.tomar_conexion(), primera)

    def test_volver_al_pool_deshace_lo_no_confirmado(self):
        # Sin esto, lo que un request dejó a medias aparecería en el siguiente.
        conn = ConexionFalsa()
        db.devolver_conexion(conn)
        self.assertEqual(conn.rollbacks, 1)

    def test_una_conexion_cerrada_no_vuelve_al_pool(self):
        conn = ConexionFalsa()
        conn.cerrada = True
        db.devolver_conexion(conn)
        self.assertEqual(db._pool, [])

    def test_el_pool_tiene_tope(self):
        conexiones = [ConexionFalsa() for _ in range(db.POOL_MAX + 3)]
        for c in conexiones:
            db.devolver_conexion(c)
        self.assertEqual(len(db._pool), db.POOL_MAX)
        # Las que no entraron se cierran, no quedan colgando contra el pooler.
        self.assertEqual(sum(1 for c in conexiones if c.cerrada), 3)

    def test_con_sqlite_no_se_reutiliza_nada(self):
        # El pool es para Postgres: abrir un archivo local no cuesta nada y
        # compartir la conexión entre hilos solo traería problemas.
        db.DB_URL = ""
        conn, _ = basedeprueba.nueva(admin_password="x")
        db.devolver_conexion(conn)
        self.assertEqual(db._pool, [])


if __name__ == "__main__":
    unittest.main()
