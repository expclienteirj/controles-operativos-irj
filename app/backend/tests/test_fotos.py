"""Tests del almacenamiento de evidencia fotográfica.

La foto es la prueba de un hallazgo que puede terminar en un descuento al
contratista: importa que se guarde donde corresponde, que se pueda ubicar sin
consultar la base y que nadie pueda leer archivos fuera del directorio.
"""

import base64
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db      # noqa: E402
import basedeprueba  # noqa: E402
import almacen  # noqa: E402
import fotos   # noqa: E402
import services  # noqa: E402

PERIODO = "2026-07"

# JPEG mínimo válido (1x1 px).
JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")
DATA_URL = "data:image/jpeg;base64," + base64.b64encode(JPEG_1PX).decode()


class TestDecodificar(unittest.TestCase):
    def test_data_url_valida(self):
        binario, ext = fotos.decodificar(DATA_URL)
        self.assertEqual(ext, ".jpg")
        self.assertTrue(binario.startswith(b"\xff\xd8"))     # cabecera JPEG

    def test_rechaza_lo_que_no_es_data_url(self):
        for malo in ("", None, "hola", 123):
            with self.assertRaises(fotos.ErrorFoto):
                fotos.decodificar(malo)

    def test_rechaza_lo_que_no_es_imagen(self):
        with self.assertRaises(fotos.ErrorFoto):
            fotos.decodificar("data:text/html;base64,PHNjcmlwdD4=")

    def test_rechaza_base64_corrupto(self):
        with self.assertRaises(fotos.ErrorFoto):
            fotos.decodificar("data:image/jpeg;base64,esto-no-es-base64!!")

    def test_rechaza_formato_no_soportado(self):
        with self.assertRaises(fotos.ErrorFoto):
            fotos.decodificar("data:image/tiff;base64,AAAA")

    def test_rechaza_archivos_gigantes(self):
        gigante = "data:image/jpeg;base64," + base64.b64encode(
            b"x" * (fotos.MAX_FOTO + 10)).decode()
        with self.assertRaises(fotos.ErrorFoto) as ctx:
            fotos.decodificar(gigante)
        self.assertIn("tamaño máximo", str(ctx.exception))


class TestRutas(unittest.TestCase):
    def test_ruta_de_limpieza_refleja_dia_sector_e_item(self):
        r = fotos.ruta_relativa({
            "modulo": "limpieza", "periodo": "2026-07", "fecha": "2026-07-15",
            "sector": "Baños hall - Sector público", "item": "Espejos"}, ".jpg")
        partes = r.split(os.sep)
        self.assertEqual(partes[:3], ["2026-07", "limpieza", "2026-07-15"])
        self.assertIn("banos-hall-sector-publico", partes[3])
        self.assertIn("espejos", partes[3])
        self.assertTrue(r.endswith(".jpg"))

    def test_ruta_de_los_agrupa_por_item(self):
        r = fotos.ruta_relativa({
            "modulo": "los", "periodo": "2026-07", "item": "infraestructura"}, ".jpg")
        partes = r.split(os.sep)
        self.assertEqual(partes[:3], ["2026-07", "los", "infraestructura"])

    def test_sin_contexto_igual_queda_dentro_del_arbol(self):
        """Una foto sin contexto no puede quedar suelta en la raíz."""
        r = fotos.ruta_relativa({}, ".jpg")
        partes = r.split(os.sep)
        self.assertGreaterEqual(len(partes), 3)
        self.assertRegex(partes[0], r"^\d{4}-\d{2}$")     # período
        self.assertEqual(partes[1], "otros")

    def test_dos_fotos_del_mismo_item_no_colisionan(self):
        ctx = {"modulo": "limpieza", "fecha": "2026-07-15",
               "sector": "Sanidad", "item": "Piso"}
        self.assertNotEqual(fotos.ruta_relativa(ctx, ".jpg"),
                            fotos.ruta_relativa(ctx, ".jpg"))

    def test_acentos_y_barras_no_llegan_al_nombre(self):
        r = fotos.ruta_relativa({
            "modulo": "limpieza", "fecha": "2026-07-15",
            "sector": "../../etc", "item": "Piso/Techo ñandú"}, ".jpg")
        nombre = r.split(os.sep)[-1]
        self.assertNotIn("..", nombre)
        self.assertNotIn("/", nombre)
        self.assertNotIn("ñ", nombre)


class TestRutaSegura(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="irj-fotos-")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_ruta_relativa_valida(self):
        r = fotos.ruta_segura(self.base, "2026-07/limpieza/2026-07-15/foto.jpg")
        self.assertTrue(r.startswith(self.base))

    def test_no_permite_salir_del_directorio(self):
        """Las rutas ahora tienen barras, así que rechazar '/' no alcanza."""
        for malo in ("../../etc/passwd", "2026-07/../../../etc/passwd",
                     "/etc/passwd", ""):
            with self.assertRaises(fotos.ErrorFoto, msg=malo):
                fotos.ruta_segura(self.base, malo)


class TestGuardadoYMigracion(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="irj-fotos-")
        self.conn, _ = basedeprueba.nueva(admin_password="x")
        self.auditor = self.conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('jperez','J. Pérez',?, 'auditor')",
            (db.hash_password("x"),)).lastrowid
        self.conn.commit()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        self.conn.close()

    def _desvio(self):
        cid = self.conn.execute(
            "INSERT INTO controles_limpieza (fecha, periodo, auditor_id) "
            "VALUES (?,?,?)", (f"{PERIODO}-15", PERIODO, self.auditor)).lastrowid
        item = self.conn.execute(
            "SELECT i.id FROM items_limpieza i JOIN sectores_limpieza s "
            "ON s.id = i.sector_id WHERE s.clave = 'sanidad' LIMIT 1").fetchone()["id"]
        self.conn.commit()
        return services.registrar_desvio(
            self.conn, cid, item, "DESVIO_TOTAL", "Piso sucio",
            self.auditor)["desvio_id"]

    def test_guardar_crea_el_archivo_y_lo_registra(self):
        desvio_id = self._desvio()
        rel = fotos.guardar(self.conn, self.base, "desvio", desvio_id, DATA_URL,
                            fotos.contexto_desvio(self.conn, desvio_id))
        # Se pregunta por el almacén y no por el disco: con la evidencia
        # en Supabase Storage no hay archivo local que mirar.
        self.assertTrue(almacen.obtener(self.base).existe(rel))
        fila = self.conn.execute(
            "SELECT archivo FROM fotos WHERE entidad_id = ?", (desvio_id,)).fetchone()
        self.assertEqual(fila["archivo"], rel)

    def test_el_contexto_sale_del_desvio(self):
        desvio_id = self._desvio()
        ctx = fotos.contexto_desvio(self.conn, desvio_id)
        self.assertEqual(ctx["modulo"], "limpieza")
        self.assertEqual(ctx["fecha"], f"{PERIODO}-15")
        self.assertEqual(ctx["sector"], "Sanidad")

    def test_migracion_reubica_las_fotos_planas(self):
        """Las fotos guardadas con el esquema viejo se mueven al árbol."""
        desvio_id = self._desvio()
        os.makedirs(self.base, exist_ok=True)
        plano = "desvio_1_abc123.jpg"
        with open(os.path.join(self.base, plano), "wb") as f:
            f.write(JPEG_1PX)
        self.conn.execute(
            "INSERT INTO fotos (entidad, entidad_id, archivo) VALUES ('desvio',?,?)",
            (desvio_id, plano))
        self.conn.commit()

        movidas = fotos.migrar_planas(self.conn, self.base)
        self.assertEqual(len(movidas), 1)
        self.assertFalse(os.path.isfile(os.path.join(self.base, plano)))

        nueva = self.conn.execute(
            "SELECT archivo FROM fotos WHERE entidad_id = ?", (desvio_id,)).fetchone()
        self.assertIn(os.sep, nueva["archivo"])
        self.assertTrue(os.path.isfile(os.path.join(self.base, nueva["archivo"])))

    def test_migracion_es_idempotente(self):
        desvio_id = self._desvio()
        fotos.guardar(self.conn, self.base, "desvio", desvio_id, DATA_URL,
                      fotos.contexto_desvio(self.conn, desvio_id))
        self.assertEqual(fotos.migrar_planas(self.conn, self.base), [])

    def test_registro_sin_archivo_no_rompe_la_migracion(self):
        self.conn.execute(
            "INSERT INTO fotos (entidad, entidad_id, archivo) "
            "VALUES ('desvio', 999, 'no_existe.jpg')")
        self.conn.commit()
        self.assertEqual(fotos.migrar_planas(self.conn, self.base), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSubitem(TestGuardadoYMigracion):
    """La evidencia LoS identifica qué sub-ítem retrata.

    Con varios criterios en grado C o D, una foto sin etiquetar no permitiría
    saber a cuál corresponde al leer el informe.
    """

    # Infraestructura es un ítem diario: la medición va con la fecha de la
    # recorrida y se consulta por día, no por ítem.
    FECHA = f"{PERIODO}-05"

    def _medicion(self):
        rel = self.conn.execute(
            "INSERT INTO relevamientos_los (periodo, auditor_id) VALUES (?,?)",
            (PERIODO, self.auditor)).lastrowid
        self.conn.commit()
        services.guardar_medicion_los(self.conn, rel, "infraestructura", {
            "subitems": {"cielorraso": "D", "vidrios": "C"}}, fecha=self.FECHA)
        return self.conn.execute(
            "SELECT id FROM los_mediciones WHERE relevamiento_id = ?",
            (rel,)).fetchone()["id"], rel

    def test_la_foto_guarda_su_subitem(self):
        med_id, _ = self._medicion()
        fotos.guardar(self.conn, self.base, "los_medicion", med_id, DATA_URL,
                      {"modulo": "los", "periodo": PERIODO, "item": "infraestructura"},
                      subitem="cielorraso")
        fila = self.conn.execute(
            "SELECT subitem, archivo FROM fotos WHERE entidad_id = ?",
            (med_id,)).fetchone()
        self.assertEqual(fila["subitem"], "cielorraso")
        self.assertIn("cielorraso", fila["archivo"])

    def test_dos_subitems_quedan_diferenciados(self):
        med_id, rel = self._medicion()
        for sub in ("cielorraso", "vidrios"):
            fotos.guardar(self.conn, self.base, "los_medicion", med_id, DATA_URL,
                          {"modulo": "los", "periodo": PERIODO,
                           "item": "infraestructura"}, subitem=sub)

        med = services.medicion_del_dia(self.conn, rel, "infraestructura", self.FECHA)
        subs = {f["subitem"] for f in self.conn.execute(
            "SELECT subitem FROM fotos WHERE entidad = 'los_medicion' "
            "AND entidad_id = ?", (med["id"],))}
        self.assertEqual(subs, {"cielorraso", "vidrios"})

    def test_sin_subitem_sigue_funcionando(self):
        """Las fotos de desvío de limpieza no tienen sub-ítem."""
        desvio_id = self._desvio()
        fotos.guardar(self.conn, self.base, "desvio", desvio_id, DATA_URL,
                      fotos.contexto_desvio(self.conn, desvio_id))
        fila = self.conn.execute(
            "SELECT subitem FROM fotos WHERE entidad_id = ?", (desvio_id,)).fetchone()
        self.assertIsNone(fila["subitem"])
