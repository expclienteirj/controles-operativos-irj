"""Tests del almacenamiento de evidencia: disco local y Supabase Storage.

El backend de Supabase se prueba contra un servidor de mentira que habla su
mismo REST. Así se verifica la forma real de las llamadas —método, ruta,
cabeceras, cuerpo— sin necesidad de credenciales ni de red.
"""

import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import almacen  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


class TestAlmacenLocal(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="irj-almacen-")
        self.almacen = almacen.AlmacenLocal(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_guardar_y_leer(self):
        self.almacen.guardar("2026-08/limpieza/2026-08-03/sanidad__piso__1.jpg", PNG)
        leido = self.almacen.leer("2026-08/limpieza/2026-08-03/sanidad__piso__1.jpg")
        self.assertEqual(leido, PNG)

    def test_crea_los_directorios_intermedios(self):
        self.almacen.guardar("a/b/c/d.jpg", PNG)
        self.assertTrue(self.almacen.existe("a/b/c/d.jpg"))

    def test_una_foto_inexistente_es_none_no_error(self):
        """El informe no puede caerse porque falte una foto."""
        self.assertIsNone(self.almacen.leer("2026-08/no-esta.jpg"))
        self.assertFalse(self.almacen.existe("2026-08/no-esta.jpg"))


class TestRutasPeligrosas(unittest.TestCase):
    """La ruta viene de la URL: no puede permitir salir del árbol."""

    def test_rechaza_subir_de_directorio(self):
        for ruta in ("../secreto.txt", "a/../../secreto.txt", "../../etc/passwd"):
            with self.assertRaises(almacen.ErrorAlmacen, msg=ruta):
                almacen.ruta_valida(ruta)

    def test_rechaza_rutas_absolutas(self):
        for ruta in ("/etc/passwd", "\\windows\\system32"):
            with self.assertRaises(almacen.ErrorAlmacen, msg=ruta):
                almacen.ruta_valida(ruta)

    def test_rechaza_vacia(self):
        with self.assertRaises(almacen.ErrorAlmacen):
            almacen.ruta_valida("")

    def test_acepta_las_rutas_que_genera_la_app(self):
        buena = "2026-08/limpieza/2026-08-03/sanidad__piso__143052__a3f9.jpg"
        self.assertEqual(almacen.ruta_valida(buena), buena)


class _StorageFalso(BaseHTTPRequestHandler):
    """Imita lo justo del REST de Supabase Storage."""

    objetos: dict = {}
    pedidos: list = []

    def _registrar(self, cuerpo=None):
        _StorageFalso.pedidos.append({
            "metodo": self.command,
            "ruta": self.path,
            "autorizacion": self.headers.get("Authorization"),
            "tipo": self.headers.get("Content-Type"),
            "upsert": self.headers.get("x-upsert"),
            "cuerpo": cuerpo,
        })

    def do_POST(self):
        largo = int(self.headers.get("Content-Length") or 0)
        cuerpo = self.rfile.read(largo)
        self._registrar(cuerpo)
        _StorageFalso.objetos[self.path] = cuerpo
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"Key":"ok"}')

    def do_GET(self):
        self._registrar()
        cuerpo = _StorageFalso.objetos.get(self.path)
        if cuerpo is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_):
        pass


class TestAlmacenSupabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _StorageFalso)
        cls.puerto = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _StorageFalso.objetos.clear()
        _StorageFalso.pedidos.clear()
        self.almacen = almacen.AlmacenSupabase(
            f"http://127.0.0.1:{self.puerto}", "clave-de-prueba", "evidencia")

    def test_guardar_y_leer_ida_y_vuelta(self):
        ruta = "2026-08/limpieza/2026-08-03/piso__1.jpg"
        self.almacen.guardar(ruta, PNG)
        self.assertEqual(self.almacen.leer(ruta), PNG)

    def test_la_subida_va_al_bucket_y_la_ruta_correctos(self):
        self.almacen.guardar("2026-08/los/gel/foto.jpg", PNG)
        p = _StorageFalso.pedidos[0]
        self.assertEqual(p["metodo"], "POST")
        self.assertEqual(p["ruta"],
                         "/storage/v1/object/evidencia/2026-08/los/gel/foto.jpg")
        self.assertEqual(p["cuerpo"], PNG)

    def test_manda_la_clave_de_servicio_y_el_tipo(self):
        self.almacen.guardar("2026-08/x.jpg", PNG)
        p = _StorageFalso.pedidos[0]
        self.assertEqual(p["autorizacion"], "Bearer clave-de-prueba")
        self.assertEqual(p["tipo"], "image/jpeg")
        # Reintentar una foto no puede fallar porque la ruta ya exista.
        self.assertEqual(p["upsert"], "true")

    def test_una_foto_inexistente_es_none_no_error(self):
        self.assertIsNone(self.almacen.leer("2026-08/no-esta.jpg"))

    def test_la_ruta_peligrosa_no_llega_a_la_red(self):
        with self.assertRaises(almacen.ErrorAlmacen):
            self.almacen.leer("../../otro-bucket/secreto.jpg")
        self.assertEqual(_StorageFalso.pedidos, [])

    def test_storage_caido_avisa_en_vez_de_romper_silencioso(self):
        caido = almacen.AlmacenSupabase("http://127.0.0.1:1", "k", "evidencia")
        with self.assertRaises(almacen.ErrorAlmacen):
            caido.guardar("2026-08/x.jpg", PNG)


class TestSeleccionDeAlmacen(unittest.TestCase):
    def setUp(self):
        self.previas = (almacen.SUPABASE_URL, almacen.SUPABASE_SERVICE_KEY)

    def tearDown(self):
        almacen.SUPABASE_URL, almacen.SUPABASE_SERVICE_KEY = self.previas

    def test_sin_variables_usa_el_disco(self):
        almacen.SUPABASE_URL = almacen.SUPABASE_SERVICE_KEY = ""
        self.assertIsInstance(almacen.obtener("/tmp/x"), almacen.AlmacenLocal)

    def test_con_variables_usa_supabase(self):
        almacen.SUPABASE_URL = "https://proyecto.supabase.co"
        almacen.SUPABASE_SERVICE_KEY = "clave"
        self.assertIsInstance(almacen.obtener("/tmp/x"), almacen.AlmacenSupabase)

    def test_la_url_sola_no_alcanza(self):
        """Sin clave no se puede escribir: mejor disco que fallar en cada foto."""
        almacen.SUPABASE_URL = "https://proyecto.supabase.co"
        almacen.SUPABASE_SERVICE_KEY = ""
        self.assertIsInstance(almacen.obtener("/tmp/x"), almacen.AlmacenLocal)


if __name__ == "__main__":
    unittest.main()
