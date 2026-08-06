"""Freno a la prueba de contraseñas.

Con la app publicada en internet, el endpoint de login queda expuesto a que le
prueben claves sin límite. Estos tests fijan el comportamiento del freno.

El más importante es `test_el_bloqueo_sobrevive_a_conexiones_distintas`: en
Vercel cada request corre en un proceso nuevo, así que un contador en memoria
no frenaría nada. El estado tiene que estar en la base.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import services  # noqa: E402
import basedeprueba  # noqa: E402

IP = "190.0.0.1"
OTRA_IP = "190.0.0.2"


class Base(unittest.TestCase):
    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="x")

    def tearDown(self):
        self.conn.close()

    def fallar(self, veces, usuario="admin", ip=IP):
        for _ in range(veces):
            services.registrar_intento_fallido(self.conn, usuario, ip)


class TestFreno(Base):
    def test_sin_intentos_no_bloquea(self):
        self.assertIsNone(services.login_bloqueado(self.conn, "admin", IP))

    def test_bajo_el_limite_no_bloquea(self):
        self.fallar(services.LOGIN_MAX_INTENTOS - 1)
        self.assertIsNone(services.login_bloqueado(self.conn, "admin", IP))

    def test_al_llegar_al_limite_bloquea(self):
        self.fallar(services.LOGIN_MAX_INTENTOS)
        faltan = services.login_bloqueado(self.conn, "admin", IP)
        self.assertIsNotNone(faltan)
        self.assertGreaterEqual(faltan, 1)
        self.assertLessEqual(faltan, services.LOGIN_VENTANA_MINUTOS + 1)

    def test_un_login_correcto_borra_el_historial(self):
        self.fallar(services.LOGIN_MAX_INTENTOS)
        self.assertIsNotNone(services.login_bloqueado(self.conn, "admin", IP))
        services.limpiar_intentos(self.conn, "admin", IP)
        self.assertIsNone(services.login_bloqueado(self.conn, "admin", IP))

    def test_bloquear_un_usuario_no_bloquea_a_otro(self):
        """Un auditor no puede quedar afuera porque otro se equivocó."""
        self.fallar(services.LOGIN_MAX_INTENTOS, usuario="admin", ip=IP)
        self.assertIsNone(services.login_bloqueado(self.conn, "jperez", OTRA_IP))

    def test_la_ip_frena_el_barrido_de_varios_usuarios(self):
        """Probar un usuario distinto en cada intento no esquiva el freno."""
        for i in range(services.LOGIN_MAX_INTENTOS):
            services.registrar_intento_fallido(self.conn, f"usuario{i}", IP)
        # Un usuario nuevo desde esa misma IP ya está frenado.
        self.assertIsNotNone(services.login_bloqueado(self.conn, "otro", IP))
        # Pero desde otra IP no.
        self.assertIsNone(services.login_bloqueado(self.conn, "otro", OTRA_IP))

    def test_sin_ip_igual_frena_por_usuario(self):
        """Si no se pudo determinar la IP, el freno por usuario sigue valiendo."""
        self.fallar(services.LOGIN_MAX_INTENTOS, ip=None)
        self.assertIsNotNone(services.login_bloqueado(self.conn, "admin", None))

    def test_los_limites_son_configurables(self):
        db.set_config(self.conn, "login_max_intentos", 3)
        self.fallar(3)
        self.assertIsNotNone(services.login_bloqueado(self.conn, "admin", IP))

    def test_en_cero_queda_desactivado(self):
        """Escotilla de emergencia si el freno molestara en operación."""
        db.set_config(self.conn, "login_max_intentos", 0)
        self.fallar(50)
        self.assertIsNone(services.login_bloqueado(self.conn, "admin", IP))

    def test_no_se_acumulan_registros_viejos(self):
        self.fallar(20)
        n = self.conn.execute("SELECT COUNT(*) c FROM intentos_login").fetchone()["c"]
        # Dos filas por intento (usuario e ip); lo viejo se limpia solo.
        self.assertLessEqual(n, 40)


class TestEntreProcesos(Base):
    def test_el_bloqueo_sobrevive_a_conexiones_distintas(self):
        """El caso que importa en serverless.

        Cada request de Vercel abre su propia conexión y muere al terminar. Si
        los intentos se contaran en memoria, cada uno arrancaría de cero y el
        freno no existiría. Se simula abriendo una conexión nueva.
        """
        if basedeprueba.usa_postgres():
            otra = db.conectar(url=basedeprueba.URL)
        else:
            self.skipTest("SQLite en memoria no se comparte entre conexiones")

        try:
            self.fallar(services.LOGIN_MAX_INTENTOS)
            # La conexión nueva no vio ninguno de esos intentos y aun así frena.
            self.assertIsNotNone(services.login_bloqueado(otra, "admin", IP))
        finally:
            otra.close()


if __name__ == "__main__":
    unittest.main()
