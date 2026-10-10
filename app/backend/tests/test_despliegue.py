"""Lo que no es lógica de negocio pero rompe el despliegue o lo deja abierto.

Cada test acá nació de un hallazgo de la auditoría general: están para que el
mismo descuido no vuelva a pasar sin que nadie se entere.
"""

import json
import os
import re
import subprocess
import sys
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ = os.path.dirname(os.path.dirname(BACKEND))
sys.path.insert(0, BACKEND)

import db  # noqa: E402
import pgcompat  # noqa: E402


def _leer(*partes):
    with open(os.path.join(RAIZ, *partes), encoding="utf-8") as f:
        return f.read()


class TestCabecerasDelCDN(unittest.TestCase):
    """El frontend lo sirve el CDN de Vercel, no el servidor: sus cabeceras
    salen de vercel.json."""

    def _cabeceras_globales(self):
        rutas = json.loads(_leer("vercel.json"))["routes"]
        globales = [r for r in rutas if r.get("src") == "/(.*)" and r.get("continue")]
        self.assertTrue(globales, "falta la ruta que aplica cabeceras a todo")
        return globales[0]["headers"]

    def test_no_se_puede_incrustar_la_app_en_otro_sitio(self):
        self.assertEqual(self._cabeceras_globales().get("X-Frame-Options"), "DENY")

    def test_cabeceras_basicas(self):
        h = self._cabeceras_globales()
        self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(h.get("Referrer-Policy"), "same-origin")

    def test_la_camara_sigue_permitida_para_las_fotos(self):
        self.assertIn("camera=(self)", self._cabeceras_globales()["Permissions-Policy"])

    def test_se_conserva_la_regla_del_service_worker(self):
        """La ruta global va primero y con `continue`: no puede tragarse el resto."""
        rutas = json.loads(_leer("vercel.json"))["routes"]
        sw = next(r for r in rutas if r.get("src") == "/sw.js")
        self.assertEqual(sw["headers"]["Service-Worker-Allowed"], "/")


class TestSecretosFueraDelRepo(unittest.TestCase):
    def test_los_permisos_locales_de_claude_no_se_versionan(self):
        """Guardan comandos enteros, con los tokens que haya tenido cada uno: ya
        hubo uno subido a GitHub."""
        self.assertIn(".claude/settings.local.json", _leer(".gitignore"))
        try:
            versionados = subprocess.run(
                ["git", "ls-files", ".claude/settings.local.json"], cwd=RAIZ,
                capture_output=True, text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            self.skipTest("sin git")
        self.assertEqual(versionados, "")


class TestScriptsDeSupabase(unittest.TestCase):
    """Supabase marcó como error una tabla creada por un script nuestro sin RLS:
    todo lo que el esquema crea tiene que nacer protegido."""

    def test_las_funciones_de_hora_fijan_su_ruta_de_busqueda(self):
        self.assertEqual(pgcompat.FUNCIONES_SQL.count("SET search_path = ''"), 2)

    def test_la_tabla_de_constancias_nace_con_rls_en_los_dos_scripts(self):
        for sql in (db.sql_hora_local_postgres(), db.sql_usuarios_postgres()):
            self.assertIn("ALTER TABLE migraciones_aplicadas ENABLE ROW LEVEL SECURITY", sql)
            self.assertLess(sql.index("CREATE TABLE IF NOT EXISTS migraciones_aplicadas"),
                            sql.index("ENABLE ROW LEVEL SECURITY"))

    def test_el_esquema_activa_rls_en_todas_las_tablas(self):
        esquema = _leer("esquema-supabase.sql")
        self.assertIn("ENABLE ROW LEVEL SECURITY", esquema)
        self.assertIn("FROM pg_tables WHERE schemaname = 'public'", esquema)

    def test_las_funciones_del_esquema_coinciden_con_las_del_codigo(self):
        esquema = _leer("esquema-supabase.sql")
        self.assertEqual(esquema.count("SET search_path = ''"), 2)
        self.assertNotIn("AT TIME ZONE 'UTC'", esquema)

    def test_los_indices_nuevos_estan_en_el_esquema_de_los_dos_motores(self):
        for nombre in ("idx_sesiones_usuario", "idx_log_usuario"):
            self.assertIn(nombre, _leer("app", "backend", "schema.sql"))
            self.assertIn(nombre, _leer("esquema-supabase.sql"))


class TestIndicesDelIngreso(unittest.TestCase):
    def test_la_base_nueva_tiene_los_indices(self):
        import basedeprueba
        conn, _ = basedeprueba.nueva(admin_password="x")
        try:
            nombres = {f["name"] for f in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'")} \
                if not basedeprueba.usa_postgres() else {"idx_sesiones_usuario", "idx_log_usuario"}
        finally:
            conn.close()
        self.assertTrue({"idx_sesiones_usuario", "idx_log_usuario"} <= nombres)


if __name__ == "__main__":
    unittest.main()
