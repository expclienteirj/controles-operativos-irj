"""Ingreso con PIN, freno escalonado y administración de usuarios.

Un PIN de cuatro dígitos tiene diez mil combinaciones: no se sostiene solo. Estos
tests fijan lo que lo sostiene (el sorteo evita los obvios, el freno escala hasta
bloquear por un día) y las salvaguardas que evitan que el admin se deje afuera.
"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import basedeprueba  # noqa: E402
import db  # noqa: E402
import reloj  # noqa: E402
import services  # noqa: E402
import usuarios  # noqa: E402

IP = "190.1.2.3"


def _hace(minutos):
    return (reloj.ahora() - datetime.timedelta(minutes=minutos)).strftime(
        reloj.FORMATO_MARCA)


class Base(unittest.TestCase):
    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="admin1234")
        self.admin = self.conn.execute(
            "SELECT id FROM usuarios WHERE usuario = 'admin'").fetchone()["id"]

    def tearDown(self):
        self.conn.close()

    def crear(self, usuario="maria", rol="auditor", nombre="María Pérez"):
        return usuarios.crear(self.conn, self.admin, usuario, nombre, rol)

    def fallos(self, cantidad, hace_min=0, usuario="maria"):
        """Fallos con fecha fija, para ejercitar las ventanas sin esperar."""
        for _ in range(cantidad):
            self.conn.execute(
                "INSERT INTO intentos_login (clave, momento) VALUES (?, ?)",
                (f"usuario:{usuario}", _hace(hace_min)))
        self.conn.commit()


class TestPolitica(unittest.TestCase):
    def test_los_pin_obvios_se_rechazan(self):
        for pin in ("0000", "1111", "9999", "1234", "4321", "0123", "6789",
                    "1212", "0101", "1990", "2005", "2580"):
            self.assertTrue(usuarios.pin_trivial(pin), pin)

    def test_un_pin_comun_pero_no_obvio_se_acepta(self):
        for pin in ("5821", "7304", "4829", "6157"):
            self.assertFalse(usuarios.pin_trivial(pin), pin)

    def test_el_sorteo_nunca_da_un_pin_obvio(self):
        for _ in range(2000):
            pin = usuarios.generar_pin()
            self.assertRegex(pin, r"^\d{4}$")
            self.assertFalse(usuarios.pin_trivial(pin), pin)

    def test_el_pin_conserva_los_ceros_a_la_izquierda(self):
        # `f"{n:04d}"`: un PIN de 7 caracteres de largo 1 sería otro bug.
        vistos = {usuarios.generar_pin() for _ in range(3000)}
        self.assertTrue(any(p.startswith("0") for p in vistos))
        self.assertTrue(all(len(p) == 4 for p in vistos))

    def test_el_auditor_elige_un_pin_y_el_admin_una_contrasena(self):
        self.assertEqual(usuarios.validar_credencial("auditor", "5821"), "pin")
        self.assertEqual(usuarios.validar_credencial("admin", "una-clave-larga"),
                         "password")
        for malo in ("123", "12345", "abcd", "", "12 4"):
            with self.assertRaises(ValueError, msg=malo):
                usuarios.validar_credencial("auditor", malo)
        for malo in ("corta", "5821", "aaaaaaaa", ""):
            with self.assertRaises(ValueError, msg=malo):
                usuarios.validar_credencial("admin", malo)

    def test_un_admin_nunca_baja_a_pin(self):
        with self.assertRaises(ValueError):
            usuarios.validar_credencial("admin", "5821")


class TestFrenoEscalonado(Base):
    def test_cuatro_fallos_no_bloquean(self):
        self.fallos(4)
        self.assertIsNone(services.login_bloqueado(self.conn, "maria", IP))

    def test_cinco_fallos_bloquean_unos_minutos(self):
        self.fallos(5)
        espera = services.login_bloqueado(self.conn, "maria", IP)
        self.assertIsNotNone(espera)
        self.assertLessEqual(espera, 15)

    def test_pasado_el_cuarto_de_hora_se_puede_reintentar(self):
        self.fallos(5, hace_min=20)
        self.assertIsNone(services.login_bloqueado(self.conn, "maria", IP))

    def test_insistir_cada_cuarto_de_hora_termina_bloqueando_una_hora(self):
        """El atajo del umbral único: esperar y volver a probar cinco más."""
        self.fallos(5, hace_min=40)
        self.fallos(5, hace_min=20)
        espera = services.login_bloqueado(self.conn, "maria", IP)
        self.assertIsNotNone(espera)
        self.assertGreater(espera, 15)
        self.assertLessEqual(espera, 60)

    def test_veinte_fallos_en_el_dia_bloquean_por_horas(self):
        for hace in (1300, 1000, 700, 400):
            self.fallos(5, hace_min=hace)
        espera = services.login_bloqueado(self.conn, "maria", IP)
        self.assertIsNotNone(espera)
        self.assertGreater(espera, 60)

    def test_con_ese_techo_probar_todo_el_espacio_de_un_pin_lleva_un_ano(self):
        """La cuenta que justifica aceptar cuatro dígitos."""
        dias = 10_000 / services.LOGIN_ESCALONES[-1][0] \
            * services.LOGIN_ESCALONES[-1][1] / (24 * 60)
        self.assertGreater(dias, 365)

    def test_la_espera_larga_se_dice_en_horas(self):
        self.assertEqual(services.espera_legible(1), "1 minuto")
        self.assertEqual(services.espera_legible(12), "12 minutos")
        self.assertEqual(services.espera_legible(60), "1 hora")
        self.assertEqual(services.espera_legible(61), "2 horas")
        self.assertEqual(services.espera_legible(23 * 60), "23 horas")

    def test_el_admin_puede_liberar_un_bloqueo(self):
        creado = self.crear()
        self.fallos(5)
        self.assertIsNotNone(services.login_bloqueado(self.conn, "maria", None))
        usuarios.desbloquear(self.conn, self.admin, creado["id"])
        self.assertIsNone(services.login_bloqueado(self.conn, "maria", None))

    def test_liberar_tambien_limpia_la_ip_de_donde_salieron_los_fallos(self):
        """El caso real: cinco errores desde la misma tablet. Liberar solo al
        usuario dejaba la IP bloqueada y el usuario seguía esperando."""
        creado = self.crear()
        for _ in range(5):
            services.registrar_intento_fallido(self.conn, "maria", IP)
            services.registrar_log(self.conn, creado["id"], "LOGIN_FALLIDO",
                                   "usuarios", creado["id"], {"ip": IP})
        self.conn.commit()
        self.assertIsNotNone(services.login_bloqueado(self.conn, "maria", IP))
        usuarios.desbloquear(self.conn, self.admin, creado["id"])
        self.assertIsNone(services.login_bloqueado(self.conn, "maria", IP))

    def test_liberar_no_toca_a_una_ip_que_nunca_fallo_contra_este_usuario(self):
        creado = self.crear()
        otra = "200.9.9.9"
        for _ in range(services.LOGIN_MAX_INTENTOS * services.LOGIN_FACTOR_IP):
            services.registrar_intento_fallido(self.conn, "barrido", otra)
        usuarios.desbloquear(self.conn, self.admin, creado["id"])
        self.assertIsNotNone(services.login_bloqueado(self.conn, "cualquiera", otra))

    def test_un_nombre_que_no_existe_se_frena_igual(self):
        """El freno no puede servir para averiguar quién tiene cuenta."""
        self.fallos(5, usuario="fantasma")
        self.assertIsNotNone(services.login_bloqueado(self.conn, "fantasma", None))

    def test_si_el_admin_sube_el_primer_umbral_los_escalones_no_lo_contradicen(self):
        db.set_config(self.conn, "login_max_intentos", 30)
        self.fallos(25, hace_min=100)
        self.assertIsNone(services.login_bloqueado(self.conn, "maria", None))

    def test_en_cero_el_freno_queda_apagado_entero(self):
        db.set_config(self.conn, "login_max_intentos", 0)
        self.fallos(50)
        self.assertIsNone(services.login_bloqueado(self.conn, "maria", IP))

    def test_los_fallos_se_conservan_lo_que_dura_el_escalon_mas_largo(self):
        self.fallos(3, hace_min=1300)
        services.registrar_intento_fallido(self.conn, "otro", None)
        quedan = self.conn.execute(
            "SELECT COUNT(*) c FROM intentos_login WHERE clave = 'usuario:maria'"
        ).fetchone()["c"]
        self.assertEqual(quedan, 3)        # 1300 min < 1440: todavía cuentan
        self.fallos(2, hace_min=1500)
        services.registrar_intento_fallido(self.conn, "otro", None)
        viejos = self.conn.execute(
            "SELECT COUNT(*) c FROM intentos_login WHERE momento < ?",
            (_hace(1440),)).fetchone()["c"]
        self.assertEqual(viejos, 0)

    def test_intentos_restantes_cuenta_hacia_atras(self):
        self.assertEqual(services.intentos_restantes(self.conn, "maria", None), 5)
        self.fallos(3)
        self.assertEqual(services.intentos_restantes(self.conn, "maria", None), 2)
        self.fallos(10)
        self.assertEqual(services.intentos_restantes(self.conn, "maria", None), 0)

    def test_el_aviso_es_igual_para_un_nombre_que_existe_y_uno_que_no(self):
        self.crear()
        self.fallos(2, usuario="maria")
        self.fallos(2, usuario="fantasma")
        self.assertEqual(services.intentos_restantes(self.conn, "maria", None),
                         services.intentos_restantes(self.conn, "fantasma", None))


class TestAlta(Base):
    def test_un_auditor_nace_con_un_pin_de_cuatro_digitos(self):
        r = self.crear()
        self.assertEqual(r["credencial"], "pin")
        self.assertRegex(r["credencial_temporal"], r"^\d{4}$")
        fila = self.conn.execute(
            "SELECT password_hash, credencial FROM usuarios WHERE usuario = 'maria'"
        ).fetchone()
        self.assertEqual(fila["credencial"], "pin")
        self.assertTrue(db.verificar_password(r["credencial_temporal"],
                                              fila["password_hash"]))

    def test_un_admin_nace_con_una_contrasena_larga(self):
        r = self.crear("jefe", "admin", "Jefe")
        self.assertEqual(r["credencial"], "password")
        self.assertGreaterEqual(len(r["credencial_temporal"]), 12)

    def test_el_usuario_se_normaliza_a_minusculas(self):
        r = self.crear("  Maria.P  ")
        self.assertEqual(r["usuario"], "maria.p")

    def test_usuarios_invalidos(self):
        for malo in ("", "ab", "con espacio", "ñandú", "a" * 31, "x/y"):
            with self.assertRaises(ValueError, msg=malo):
                self.crear(malo)

    def test_no_admite_dos_usuarios_iguales(self):
        self.crear()
        with self.assertRaises(ValueError):
            self.crear()

    def test_rol_y_nombre_se_validan(self):
        with self.assertRaises(ValueError):
            self.crear("x1y", "superadmin")
        with self.assertRaises(ValueError):
            self.crear("x2y", "auditor", "a")

    def test_queda_en_el_log_sin_la_credencial(self):
        r = self.crear()
        log = self.conn.execute(
            "SELECT detalle FROM auditoria_log WHERE accion = 'USUARIO_CREADO'"
        ).fetchone()["detalle"]
        self.assertNotIn(r["credencial_temporal"], log)

    def test_el_listado_nunca_trae_el_hash(self):
        self.crear()
        for u in usuarios.listar(self.conn):
            self.assertNotIn("password_hash", u)

    def test_el_listado_informa_bloqueo_y_sesiones(self):
        r = self.crear()
        self.fallos(5)
        fila = next(u for u in usuarios.listar(self.conn) if u["id"] == r["id"])
        self.assertIsNotNone(fila["bloqueado_min"])
        self.assertEqual(fila["sesiones"], 0)


class TestSalvaguardas(Base):
    def test_no_podes_desactivarte_a_vos_mismo(self):
        with self.assertRaises(PermissionError):
            usuarios.modificar(self.conn, self.admin, self.admin, activo=False)

    def test_no_podes_quitarte_el_rol_de_admin(self):
        with self.assertRaises(PermissionError):
            usuarios.modificar(self.conn, self.admin, self.admin, rol="auditor")

    def test_nunca_queda_el_sistema_sin_un_admin_activo(self):
        otro = self.crear("jefe", "admin", "Jefe")
        # Otro admin desactiva al primero: queda uno, está bien.
        usuarios.modificar(self.conn, otro["id"], self.admin, activo=False)
        # Ahora el único activo es 'jefe': nadie más puede sacarlo.
        tercero = self.crear("tercero", "admin", "Tercero")
        usuarios.modificar(self.conn, tercero["id"], otro["id"], activo=False)
        with self.assertRaises(PermissionError):
            usuarios.modificar(self.conn, self.admin, tercero["id"], activo=False)

    def test_desactivar_corta_las_sesiones(self):
        r = self.crear()
        self.conn.execute("INSERT INTO sesiones (token, usuario_id) VALUES ('h', ?)",
                          (r["id"],))
        self.conn.commit()
        usuarios.modificar(self.conn, self.admin, r["id"], activo=False)
        n = self.conn.execute("SELECT COUNT(*) c FROM sesiones WHERE usuario_id = ?",
                              (r["id"],)).fetchone()["c"]
        self.assertEqual(n, 0)

    def test_cambiar_el_rol_regenera_la_credencial_del_tipo_nuevo(self):
        """Pasar a admin con el PIN viejo dejaría la cuenta más poderosa
        protegida por cuatro dígitos."""
        r = self.crear()
        cambio = usuarios.modificar(self.conn, self.admin, r["id"], rol="admin")
        self.assertEqual(cambio["credencial"], "password")
        self.assertNotRegex(cambio["credencial_temporal"], r"^\d{4}$")
        fila = self.conn.execute(
            "SELECT password_hash FROM usuarios WHERE id = ?", (r["id"],)).fetchone()
        self.assertFalse(db.verificar_password(r["credencial_temporal"],
                                               fila["password_hash"]))

    def test_cambiar_solo_el_nombre_no_toca_la_credencial(self):
        r = self.crear()
        cambio = usuarios.modificar(self.conn, self.admin, r["id"], nombre="María P.")
        self.assertNotIn("credencial_temporal", cambio)

    def test_un_usuario_inexistente_es_404(self):
        with self.assertRaises(LookupError):
            usuarios.modificar(self.conn, self.admin, 9999, activo=False)
        with self.assertRaises(LookupError):
            usuarios.resetear(self.conn, self.admin, 9999)

    def test_resetear_invalida_la_credencial_vieja_y_libera_el_bloqueo(self):
        r = self.crear()
        self.fallos(5)
        self.conn.execute("INSERT INTO sesiones (token, usuario_id) VALUES ('h2', ?)",
                          (r["id"],))
        self.conn.commit()
        nuevo = usuarios.resetear(self.conn, self.admin, r["id"])
        fila = self.conn.execute(
            "SELECT password_hash FROM usuarios WHERE id = ?", (r["id"],)).fetchone()
        self.assertTrue(db.verificar_password(nuevo["credencial_temporal"],
                                              fila["password_hash"]))
        self.assertFalse(db.verificar_password(r["credencial_temporal"],
                                               fila["password_hash"]) and
                         r["credencial_temporal"] != nuevo["credencial_temporal"])
        self.assertIsNone(services.login_bloqueado(self.conn, "maria", None))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM sesiones WHERE usuario_id = ?",
            (r["id"],)).fetchone()["c"], 0)

    def test_resetear_pasa_a_un_auditor_con_contrasena_vieja_al_pin(self):
        self.conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('viejo','Viejo',?, 'auditor')", (db.hash_password("clave-vieja"),))
        self.conn.commit()
        uid = self.conn.execute(
            "SELECT id FROM usuarios WHERE usuario = 'viejo'").fetchone()["id"]
        r = usuarios.resetear(self.conn, self.admin, uid)
        self.assertEqual(r["credencial"], "pin")

    def test_no_se_resetea_a_uno_mismo(self):
        with self.assertRaises(PermissionError):
            usuarios.resetear(self.conn, self.admin, self.admin)

    def test_cerrar_sesiones_puede_conservar_la_propia(self):
        self.conn.execute("INSERT INTO sesiones (token, usuario_id) VALUES (?, ?)",
                          (db.hash_token("mia"), self.admin))
        self.conn.execute("INSERT INTO sesiones (token, usuario_id) VALUES (?, ?)",
                          (db.hash_token("otra"), self.admin))
        self.conn.commit()
        usuarios.cerrar_sesiones(self.conn, self.admin, self.admin,
                                 conservar_token="mia")
        quedan = [f["token"] for f in self.conn.execute("SELECT token FROM sesiones")]
        self.assertEqual(quedan, [db.hash_token("mia")])

    def test_cada_accion_queda_en_el_log(self):
        r = self.crear()
        usuarios.modificar(self.conn, self.admin, r["id"], nombre="María P.")
        usuarios.resetear(self.conn, self.admin, r["id"])
        usuarios.desbloquear(self.conn, self.admin, r["id"])
        usuarios.cerrar_sesiones(self.conn, self.admin, r["id"])
        acciones = {f["accion"] for f in self.conn.execute(
            "SELECT accion FROM auditoria_log")}
        for esperada in ("USUARIO_CREADO", "USUARIO_MODIFICADO",
                         "CREDENCIAL_RESETEADA", "USUARIO_DESBLOQUEADO",
                         "SESIONES_CERRADAS"):
            self.assertIn(esperada, acciones)


class TestTokenConHash(unittest.TestCase):
    def test_la_huella_no_es_el_token_y_es_estable(self):
        h = db.hash_token("abc")
        self.assertNotEqual(h, "abc")
        self.assertEqual(h, db.hash_token("abc"))
        self.assertEqual(len(h), 64)
        self.assertNotEqual(h, db.hash_token("abd"))


class TestMigracionDelIngreso(Base):
    def test_una_base_vieja_gana_la_columna_y_pierde_las_sesiones_en_claro(self):
        self.conn.execute("DELETE FROM migraciones_aplicadas "
                          "WHERE nombre = ?", (db.MIGRACION_SESIONES_CON_HASH,))
        self.conn.execute("INSERT INTO sesiones (token, usuario_id) VALUES "
                          "('token-en-claro', ?)", (self.admin,))
        self.conn.execute("UPDATE config SET valor = '10' "
                          "WHERE clave = 'login_max_intentos'")
        self.conn.commit()
        db.migrar(self.conn)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM sesiones").fetchone()["c"], 0)
        self.assertEqual(db.get_config(self.conn, "login_max_intentos"), 5)

    def test_correrla_de_nuevo_no_vuelve_a_cerrar_sesiones(self):
        db.migrar(self.conn)
        self.conn.execute("INSERT INTO sesiones (token, usuario_id) VALUES "
                          "(?, ?)", (db.hash_token("viva"), self.admin))
        self.conn.commit()
        db.migrar(self.conn)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM sesiones").fetchone()["c"], 1)

    def test_no_pisa_un_umbral_que_el_admin_ya_ajusto(self):
        self.conn.execute("DELETE FROM migraciones_aplicadas "
                          "WHERE nombre = ?", (db.MIGRACION_SESIONES_CON_HASH,))
        db.set_config(self.conn, "login_max_intentos", 8)
        db.migrar(self.conn)
        self.assertEqual(db.get_config(self.conn, "login_max_intentos"), 8)

    def test_el_script_de_supabase_esta_al_dia(self):
        raiz = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        with open(os.path.join(raiz, "migracion-usuarios.sql"), encoding="utf-8") as f:
            self.assertEqual(
                f.read(), db.sql_usuarios_postgres(),
                "migracion-usuarios.sql está desactualizado. Regenerar con:\n"
                "  python3 -c \"import sys; sys.path.insert(0,'app/backend'); "
                "import db; print(db.sql_usuarios_postgres(), end='')\" "
                "> migracion-usuarios.sql")

    def test_el_script_de_supabase_corre_antes_que_el_codigo(self):
        sql = db.sql_usuarios_postgres()
        self.assertIn("ADD COLUMN IF NOT EXISTS credencial", sql)
        self.assertIn("DELETE FROM sesiones", sql)
        self.assertIn("IF EXISTS (SELECT 1 FROM migraciones_aplicadas", sql)
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))


if __name__ == "__main__":
    unittest.main()
