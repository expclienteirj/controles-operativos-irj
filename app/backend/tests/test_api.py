"""Tests de la API: se levanta un servidor real y se le pega por HTTP."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="irj-test-")
os.environ["IRJ_DB"] = os.path.join(TMP, "test.db")

import api  # noqa: E402
import db   # noqa: E402
import basedeprueba  # noqa: E402

db.DB_PATH = os.environ["IRJ_DB"]
api.UPLOADS_DIR = os.path.join(TMP, "uploads")

PIXEL_PNG = ("data:image/png;base64,"
             "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
             "IQAAAABJRU5ErkJggg==")


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conn, _ = basedeprueba.nueva(admin_password="admin1234", path=db.DB_PATH)
        conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('jperez','J. Pérez',?, 'auditor')", (db.hash_password("audit1234"),))
        conn.commit()
        conn.close()

        cls.server = api.crear_servidor(0)
        cls.puerto = cls.server.server_address[1]
        cls.hilo = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.hilo.start()

        cls.token_admin = cls.login("admin", "admin1234")["token"]
        cls.token_auditor = cls.login("jperez", "audit1234")["token"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(TMP, ignore_errors=True)

    # -- helper HTTP --
    @classmethod
    def pedir(cls, metodo, camino, body=None, token=None):
        url = f"http://127.0.0.1:{cls.puerto}{camino}"
        datos = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=datos, method=metodo)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            cuerpo = e.read()
            try:
                return e.code, json.loads(cuerpo)
            except json.JSONDecodeError:
                return e.code, {"raw": cuerpo}

    @classmethod
    def login(cls, usuario, password):
        _, r = cls.pedir("POST", "/api/login",
                         {"usuario": usuario, "password": password})
        return r

    def admin(self, metodo, camino, body=None):
        return self.pedir(metodo, camino, body, self.token_admin)

    def auditor(self, metodo, camino, body=None):
        return self.pedir(metodo, camino, body, self.token_auditor)


class TestAutenticacion(TestAPI):
    def test_login_correcto(self):
        r = self.login("jperez", "audit1234")
        self.assertIn("token", r)
        self.assertEqual(r["usuario"]["rol"], "auditor")

    def test_login_con_password_incorrecta(self):
        codigo, r = self.pedir("POST", "/api/login",
                               {"usuario": "jperez", "password": "mal"})
        self.assertEqual(codigo, 401)
        self.assertNotIn("token", r)

    def test_usuario_inexistente_da_el_mismo_mensaje(self):
        """No revelar qué usuarios existen."""
        _, r1 = self.pedir("POST", "/api/login", {"usuario": "nadie", "password": "x"})
        _, r2 = self.pedir("POST", "/api/login", {"usuario": "jperez", "password": "x"})
        self.assertEqual(r1["error"], r2["error"])

    def test_sin_token_es_401(self):
        codigo, _ = self.pedir("GET", "/api/sectores")
        self.assertEqual(codigo, 401)

    def test_token_invalido_es_401(self):
        codigo, _ = self.pedir("GET", "/api/sectores", token="token-falso")
        self.assertEqual(codigo, 401)

    def test_auditor_no_puede_editar_configuracion(self):
        codigo, _ = self.auditor("PUT", "/api/config/iluminacion_objetivo", {"valor": 0.5})
        self.assertEqual(codigo, 403)

    def test_admin_si_puede(self):
        codigo, _ = self.admin("PUT", "/api/config/iluminacion_objetivo", {"valor": 0.92})
        self.assertEqual(codigo, 200)
        _, r = self.admin("GET", "/api/config?grupo=los")
        valor = next(c["valor"] for c in r["config"] if c["clave"] == "iluminacion_objetivo")
        self.assertEqual(valor, 0.92)
        self.admin("PUT", "/api/config/iluminacion_objetivo", {"valor": 0.90})

    def test_config_inexistente_es_404(self):
        codigo, _ = self.admin("PUT", "/api/config/no-existe", {"valor": 1})
        self.assertEqual(codigo, 404)

    def test_cambio_de_password_valida_la_actual(self):
        codigo, _ = self.auditor("POST", "/api/password",
                                 {"actual": "incorrecta", "nueva": "nuevaclave1"})
        self.assertEqual(codigo, 401)

    def test_password_nueva_debe_ser_larga(self):
        codigo, _ = self.auditor("POST", "/api/password",
                                 {"actual": "audit1234", "nueva": "corta"})
        self.assertEqual(codigo, 400)

    def test_la_sesion_sobrevive_al_reinicio_del_servidor(self):
        """La tablet puede tener trabajo encolado cuando el servidor reinicia.

        Si los tokens vivieran en memoria, ese trabajo quedaría sin poder
        subirse hasta que alguien volviera a iniciar sesión a mano.
        """
        token = self.login('jperez', 'audit1234')['token']

        # Simula el reinicio: nuevo servidor, mismo archivo de base.
        self.__class__.server.shutdown()
        self.__class__.server.server_close()
        self.__class__.server = api.crear_servidor(self.__class__.puerto)
        threading.Thread(target=self.__class__.server.serve_forever,
                         daemon=True).start()

        codigo, r = self.pedir('GET', '/api/sesion', token=token)
        self.assertEqual(codigo, 200)
        self.assertEqual(r['usuario']['usuario'], 'jperez')

    def test_la_sesion_registra_el_ultimo_uso(self):
        token = self.login('jperez', 'audit1234')['token']
        self.pedir('GET', '/api/sectores', token=token)
        conn = db.conectar()
        fila = conn.execute(
            'SELECT ultimo_uso FROM sesiones WHERE token = ?', (token,)).fetchone()
        conn.close()
        self.assertIsNotNone(fila)

    def test_cambiar_password_cierra_las_demas_sesiones(self):
        conn = db.conectar()
        conn.execute("INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
                     "VALUES ('temp','Temp',?, 'auditor')", (db.hash_password('temp1234'),))
        conn.commit()
        conn.close()

        viejo = self.login('temp', 'temp1234')['token']
        nuevo = self.login('temp', 'temp1234')['token']

        codigo, _ = self.pedir('POST', '/api/password',
                               {'actual': 'temp1234', 'nueva': 'temp5678'}, token=nuevo)
        self.assertEqual(codigo, 200)

        # La sesión que hizo el cambio sigue viva; la otra no.
        self.assertEqual(self.pedir('GET', '/api/sesion', token=nuevo)[0], 200)
        self.assertEqual(self.pedir('GET', '/api/sesion', token=viejo)[0], 401)

    def test_usuario_desactivado_pierde_la_sesion(self):
        conn = db.conectar()
        conn.execute("INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
                     "VALUES ('baja','Baja',?, 'auditor')", (db.hash_password('baja1234'),))
        conn.commit()
        conn.close()

        token = self.login('baja', 'baja1234')['token']
        self.assertEqual(self.pedir('GET', '/api/sesion', token=token)[0], 200)

        conn = db.conectar()
        conn.execute("UPDATE usuarios SET activo = 0 WHERE usuario = 'baja'")
        conn.commit()
        conn.close()
        self.assertEqual(self.pedir('GET', '/api/sesion', token=token)[0], 401)

    def test_logout_invalida_el_token(self):
        token = self.login("jperez", "audit1234")["token"]
        self.pedir("POST", "/api/logout", token=token)
        codigo, _ = self.pedir("GET", "/api/sectores", token=token)
        self.assertEqual(codigo, 401)


class TestFlujoLimpieza(TestAPI):
    def _control_nuevo(self, fecha, turno="MANANA"):
        codigo, r = self.auditor("POST", "/api/controles",
                                 {"fecha": fecha, "turno": turno})
        self.assertEqual(codigo, 200, r)
        return r["control_id"]

    def test_los_dos_turnos_del_dia_son_controles_distintos(self):
        m = self._control_nuevo("2020-01-08", "MANANA")
        t = self._control_nuevo("2020-01-08", "TARDE")
        self.assertNotEqual(m, t)

        _, r = self.auditor("GET", "/api/controles?periodo=2020-01")
        delDia = [c for c in r["controles"] if c["fecha"] == "2020-01-08"]
        self.assertEqual(sorted(c["turno"] for c in delDia), ["MANANA", "TARDE"])

    def test_no_se_repite_el_mismo_turno_del_mismo_dia(self):
        self._control_nuevo("2020-01-09", "TARDE")
        codigo, _ = self.auditor("POST", "/api/controles",
                                 {"fecha": "2020-01-09", "turno": "TARDE"})
        self.assertEqual(codigo, 409)

    def test_turno_invalido_se_rechaza(self):
        codigo, _ = self.auditor("POST", "/api/controles",
                                 {"fecha": "2020-01-10", "turno": "NOCHE"})
        self.assertEqual(codigo, 400)

    def test_sin_turno_explicito_se_abre_el_de_la_manana(self):
        """Compatibilidad: el cliente viejo no manda turno."""
        codigo, r = self.auditor("POST", "/api/controles", {"fecha": "2020-01-11"})
        self.assertEqual(codigo, 200)
        self.assertEqual(r["turno"], "MANANA")

    def test_sectores_con_items(self):
        _, r = self.auditor("GET", "/api/sectores")
        self.assertEqual(len(r["sectores"]), 9)
        embarque = next(s for s in r["sectores"] if s["clave"] == "sala_embarque")
        self.assertEqual(len(embarque["items"]), 9)   # + "Techo"

    def test_control_duplicado_es_409(self):
        """Un solo control por día: reabrir el mismo día no crea otro."""
        self._control_nuevo("2020-01-05")
        codigo, _ = self.auditor("POST", "/api/controles", {"fecha": "2020-01-05"})
        self.assertEqual(codigo, 409)

    def test_fecha_invalida(self):
        codigo, _ = self.auditor("POST", "/api/controles", {"fecha": "05/01/2020"})
        self.assertEqual(codigo, 400)

    def test_no_se_abre_el_control_de_una_fecha_futura(self):
        """Un control registra una recorrida hecha, no una planificación."""
        codigo, r = self.auditor("POST", "/api/controles", {"fecha": "2099-01-01"})
        self.assertEqual(codigo, 400)
        self.assertIn("futura", r["error"])

    def test_sin_fecha_abre_el_control_de_hoy(self):
        import datetime
        hoy = datetime.date.today().isoformat()
        codigo, r = self.auditor("POST", "/api/controles", {})
        self.assertEqual(codigo, 200, r)
        self.assertEqual(r["fecha"], hoy)

    def test_control_de_hoy(self):
        import datetime
        hoy = datetime.date.today().isoformat()
        codigo, r = self.auditor("GET", "/api/controles/hoy")
        self.assertEqual(codigo, 200)
        self.assertEqual(r["fecha"], hoy)
        self.assertEqual(r["periodo"], hoy[:7])
        self.assertIn("mes", r)

    def test_completitud_del_periodo(self):
        codigo, r = self.auditor("GET", "/api/periodos/2020-02/completitud")
        self.assertEqual(codigo, 200)
        self.assertEqual(r["dias_esperados"], 29)   # 2020 bisiesto
        self.assertEqual(r["cobertura"], 0.0)

    def test_flujo_completo_por_excepcion(self):
        cid = self._control_nuevo("2020-03-01")

        _, est = self.auditor("GET", f"/api/controles/{cid}")
        self.assertIsNone(est["porcentaje_general"])
        self.assertEqual(len(est["sectores_pendientes"]), 9)

        # Un desvío con foto
        _, sectores = self.auditor("GET", "/api/sectores")
        sanidad = next(s for s in sectores["sectores"] if s["clave"] == "sanidad")
        codigo, r = self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": sanidad["items"][0]["id"], "estado": "DESVIO_TOTAL",
            "observacion": "Piso con residuos", "fotos": [PIXEL_PNG]})
        self.assertEqual(codigo, 200, r)
        self.assertFalse(r.get("falta_foto"))

        # No cierra con sectores pendientes
        codigo, r = self.auditor("POST", f"/api/controles/{cid}/cerrar")
        self.assertEqual(codigo, 400)
        self.assertIn("Faltan confirmar", r["error"])

        for s in sectores["sectores"]:
            self.auditor("POST", f"/api/controles/{cid}/sectores/{s['id']}/confirmar")

        codigo, est = self.auditor("POST", f"/api/controles/{cid}/cerrar")
        self.assertEqual(codigo, 200)
        sanidad_est = next(x for x in est["sectores"] if x["clave"] == "sanidad")
        self.assertAlmostEqual(sanidad_est["porcentaje"], 5 / 6)

        # Cerrado: inmutable
        codigo, _ = self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": sanidad["items"][1]["id"], "estado": "DESVIO_TOTAL",
            "observacion": "Tarde"})
        self.assertEqual(codigo, 403)

    def test_confirmar_y_deshacer_desde_la_grilla(self):
        """Atajo 'TODO OK': un toque confirma, otro deshace."""
        cid = self._control_nuevo("2020-12-01")
        _, sectores = self.auditor("GET", "/api/sectores")
        sid = sectores["sectores"][0]["id"]

        codigo, _ = self.auditor(
            "POST", f"/api/controles/{cid}/sectores/{sid}/confirmar")
        self.assertEqual(codigo, 200)
        _, est = self.auditor("GET", f"/api/controles/{cid}")
        self.assertTrue(next(s for s in est["sectores"]
                             if s["sector_id"] == sid)["confirmado"])

        codigo, _ = self.auditor(
            "DELETE", f"/api/controles/{cid}/sectores/{sid}/confirmar")
        self.assertEqual(codigo, 200)
        _, est = self.auditor("GET", f"/api/controles/{cid}")
        s = next(x for x in est["sectores"] if x["sector_id"] == sid)
        self.assertFalse(s["confirmado"])
        self.assertIsNone(s["porcentaje"])   # vuelve a Sin datos, no a 0%

    def test_deshacer_en_control_cerrado_es_403(self):
        cid = self._control_nuevo("2020-12-02")
        _, sectores = self.auditor("GET", "/api/sectores")
        for s in sectores["sectores"]:
            self.auditor("POST", f"/api/controles/{cid}/sectores/{s['id']}/confirmar")
        self.auditor("POST", f"/api/controles/{cid}/cerrar")

        codigo, _ = self.auditor(
            "DELETE",
            f"/api/controles/{cid}/sectores/{sectores['sectores'][0]['id']}/confirmar")
        self.assertEqual(codigo, 403)

    def test_desvio_sin_observacion_es_400(self):
        cid = self._control_nuevo("2020-04-01")
        _, sectores = self.auditor("GET", "/api/sectores")
        item = sectores["sectores"][0]["items"][0]["id"]
        codigo, r = self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": item, "estado": "DESVIO_TOTAL", "observacion": "  "})
        self.assertEqual(codigo, 400)
        self.assertIn("observación", r["error"].lower())

    def test_desvio_sin_foto_se_marca(self):
        cid = self._control_nuevo("2020-05-01")
        _, sectores = self.auditor("GET", "/api/sectores")
        item = sectores["sectores"][0]["items"][0]["id"]
        _, r = self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": item, "estado": "DESVIO_TOTAL", "observacion": "Sin evidencia"})
        self.assertTrue(r["falta_foto"])

    def test_foto_corrupta_es_400(self):
        cid = self._control_nuevo("2020-06-01")
        _, sectores = self.auditor("GET", "/api/sectores")
        item = sectores["sectores"][0]["items"][0]["id"]
        codigo, _ = self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": item, "estado": "DESVIO_TOTAL", "observacion": "x",
            "fotos": ["data:image/png;base64,esto-no-es-base64!!"]})
        self.assertEqual(codigo, 400)

    def test_no_se_aceptan_archivos_que_no_sean_imagen(self):
        cid = self._control_nuevo("2020-07-01")
        _, sectores = self.auditor("GET", "/api/sectores")
        item = sectores["sectores"][0]["items"][0]["id"]
        codigo, _ = self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": item, "estado": "DESVIO_TOTAL", "observacion": "x",
            "fotos": ["data:text/html;base64,PHNjcmlwdD4="]})
        self.assertEqual(codigo, 400)

    def test_solo_admin_reabre(self):
        cid = self._control_nuevo("2020-08-01")
        _, sectores = self.auditor("GET", "/api/sectores")
        for s in sectores["sectores"]:
            self.auditor("POST", f"/api/controles/{cid}/sectores/{s['id']}/confirmar")
        self.auditor("POST", f"/api/controles/{cid}/cerrar")

        codigo, _ = self.auditor("POST", f"/api/controles/{cid}/reabrir", {"motivo": "x"})
        self.assertEqual(codigo, 403)
        codigo, _ = self.admin("POST", f"/api/controles/{cid}/reabrir",
                               {"motivo": "Carga tardía"})
        self.assertEqual(codigo, 200)

    def test_no_conformidad_se_lista_y_se_resuelve(self):
        cid = self._control_nuevo("2020-09-01")
        _, sectores = self.auditor("GET", "/api/sectores")
        item = sectores["sectores"][0]["items"][0]["id"]
        self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": item, "estado": "DESVIO_PARCIAL", "observacion": "Manchas"})

        _, r = self.auditor("GET", "/api/periodos/2020-09/no-conformidades")
        self.assertEqual(len(r["no_conformidades"]), 1)
        nc = r["no_conformidades"][0]
        self.assertEqual(nc["prioridad"], "PROGRAMADA")

        # Cerrarla exige decir qué se constató: sin eso no hay trazabilidad.
        codigo, _ = self.auditor("PUT", f"/api/no-conformidades/{nc['id']}",
                                 {"estado": "RESUELTA"})
        self.assertEqual(codigo, 400)

        codigo, _ = self.auditor("PUT", f"/api/no-conformidades/{nc['id']}",
                                 {"estado": "RESUELTA",
                                  "resolucion": "Sector relimpiado y verificado"})
        self.assertEqual(codigo, 200)
        _, r = self.auditor("GET", "/api/periodos/2020-09/no-conformidades")
        resuelta = r["no_conformidades"][0]
        self.assertEqual(resuelta["estado"], "RESUELTA")
        self.assertEqual(resuelta["resolucion"], "Sector relimpiado y verificado")
        self.assertIsNotNone(resuelta["resuelto_por"])

    def test_nc_de_dias_anteriores_se_arrastran_al_auditor_siguiente(self):
        """La NC de un día tiene que llegarle al auditor del día siguiente,
        aunque haya cambiado el mes."""
        cid = self._control_nuevo("2021-03-31")
        _, sectores = self.auditor("GET", "/api/sectores")
        item = sectores["sectores"][0]["items"][0]["id"]
        self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": item, "estado": "DESVIO_TOTAL",
            "observacion": "Piso sucio (arrastre)"})

        # Cruza el fin de mes: se consulta por fecha, no por período.
        _, r = self.auditor("GET", "/api/no-conformidades/pendientes?fecha=2021-04-01")
        propias = [p for p in r["pendientes"]
                   if p["descripcion"] == "Piso sucio (arrastre)"]
        self.assertEqual(len(propias), 1)
        p = propias[0]
        self.assertEqual(p["fecha_origen"], "2021-03-31")
        self.assertEqual(p["dias_pendiente"], 1)
        self.assertEqual(p["prioridad"], "INMEDIATA")

        # Una vez resuelta deja de arrastrarse.
        self.auditor("PUT", f"/api/no-conformidades/{p['id']}",
                     {"estado": "RESUELTA", "resolucion": "Verificado en turno mañana"})
        _, r = self.auditor("GET", "/api/no-conformidades/pendientes?fecha=2021-04-01")
        self.assertNotIn(p["id"], [x["id"] for x in r["pendientes"]])

    def test_resolver_nc_no_recupera_certificacion(self):
        """La NC penaliza cuando ocurre; cerrarla mide tiempo de resolución,
        no devuelve el punto perdido."""
        cid = self._control_nuevo("2021-05-02")
        _, sectores = self.auditor("GET", "/api/sectores")
        item = sectores["sectores"][0]["items"][0]["id"]
        self.auditor("POST", f"/api/controles/{cid}/desvios", {
            "item_id": item, "estado": "DESVIO_PARCIAL", "observacion": "Manchas"})

        _, antes = self.auditor("GET", "/api/periodos/2021-05/certificacion")
        _, r = self.auditor("GET", "/api/periodos/2021-05/no-conformidades")
        self.assertEqual(len(r["no_conformidades"]), 1)
        self.auditor("PUT", f"/api/no-conformidades/{r['no_conformidades'][0]['id']}",
                     {"estado": "RESUELTA", "resolucion": "Relimpiado"})
        _, despues = self.auditor("GET", "/api/periodos/2021-05/certificacion")

        self.assertEqual(antes["porcentaje"], despues["porcentaje"])
        self.assertEqual(despues["no_conformidades_periodo"], 1)
        self.assertEqual(despues["no_conformidades_abiertas"], 0)


class TestCertificacionAPI(TestAPI):
    def test_datos_del_periodo_y_certificacion(self):
        # Los tres obligatorios van juntos: sin ellos la certificación se
        # bloquea y no hay `detalle` que revisar.
        codigo, _ = self.admin("PUT", "/api/periodos/2031-01/datos", {
            "horas_hombre_programadas": 1000, "horas_hombre_perdidas": 50,
            "documentacion_verificada": 1, "ley_19587_verificada": 1,
            "monto_adjudicado": 5_000_000})
        self.assertEqual(codigo, 200)

        _, cert = self.auditor("GET", "/api/periodos/2031-01/certificacion")
        self.assertEqual(cert["detalle"]["programacion_trabajos"]["valor"], 0.95)
        self.assertEqual(cert["monto_adjudicado"], 5_000_000)

    def test_auditor_no_carga_datos_del_periodo(self):
        codigo, _ = self.auditor("PUT", "/api/periodos/2031-02/datos",
                                 {"horas_hombre_programadas": 100})
        self.assertEqual(codigo, 403)

    def test_periodo_vacio_no_certifica_nada(self):
        codigo, cert = self.auditor("GET", "/api/periodos/2099-12/certificacion")
        self.assertEqual(codigo, 200)
        self.assertIsNone(cert["porcentaje"])
        self.assertEqual(len(cert["items_sin_datos"]), 6)

    def test_verificacion_de_documentacion_se_carga_desde_la_api(self):
        self.admin("PUT", "/api/periodos/2031-06/datos",
                   {"documentacion_verificada": 1, "ley_19587_verificada": 1,
                    "horas_hombre_programadas": 1000})
        _, cert = self.auditor("GET", "/api/periodos/2031-06/certificacion")
        self.assertEqual(cert["detalle"]["documentacion"]["valor"], 1.0)
        self.assertNotIn("documentacion", cert["items_sin_datos"])

    def test_la_api_bloquea_la_certificacion_sin_los_obligatorios(self):
        """El contrato exige los tres ítems: la API no puede devolver un
        porcentaje redistribuyendo el peso de lo que falta."""
        self.admin("PUT", "/api/periodos/2031-07/datos",
                   {"documentacion_verificada": 1, "monto_adjudicado": 5_000_000})
        _, cert = self.auditor("GET", "/api/periodos/2031-07/certificacion")
        self.assertIsNone(cert["porcentaje"])
        self.assertIsNone(cert["importe"])
        self.assertCountEqual(cert["items_obligatorios_faltantes"],
                              ["ley_19587", "programacion_trabajos"])


class TestLoSAPI(TestAPI):
    def test_items_los_marcan_configuracion_pendiente(self):
        _, r = self.auditor("GET", "/api/los/items")
        self.assertEqual(len(r["items"]), 11)
        pasarelas = next(i for i in r["items"] if i["clave"] == "pasarelas")
        self.assertFalse(pasarelas["aplica"])
        banos = next(i for i in r["items"] if i["clave"] == "banos")
        self.assertTrue(banos["requiere_configuracion"])

    def test_alta_de_inventario_y_relevamiento(self):
        codigo, r = self.admin("POST", "/api/inventario/luminarias",
                               {"sector": "Hall central", "cantidad": 50})
        self.assertEqual(codigo, 200, r)

        _, items = self.auditor("GET", "/api/los/items")
        ilum = next(i for i in items["items"] if i["clave"] == "iluminacion")
        self.assertFalse(ilum["requiere_configuracion"])

        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-03"})
        # Iluminación es diaria: la medición lleva la fecha de la recorrida.
        codigo, med = self.auditor(
            "POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/mediciones",
            {"item": "iluminacion", "fecha": "2031-03-04",
             "datos": {"quemadas": {"Hall central": 5}}})
        self.assertEqual(codigo, 200, med)
        self.assertTrue(med["resultado"]["cumple"])

    def test_item_diario_de_un_periodo_pasado_exige_la_fecha(self):
        """Asumir hoy escribiría el relevamiento en un día que nadie recorrió."""
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-04"})
        codigo, r = self.auditor(
            "POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/mediciones",
            {"item": "iluminacion", "datos": {"quemadas": {}}})
        self.assertEqual(codigo, 400)
        self.assertIn("fecha", r["error"])

    def test_item_diario_rechaza_una_fecha_de_otro_periodo(self):
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-05"})
        codigo, _ = self.auditor(
            "POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/mediciones",
            {"item": "iluminacion", "fecha": "2031-06-01", "datos": {"quemadas": {}}})
        self.assertEqual(codigo, 400)

    def test_item_mensual_no_admite_fecha(self):
        """Mandarle fecha a un ítem mensual crearía una segunda medición
        mensual encubierta."""
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-07"})
        codigo, _ = self.auditor(
            "POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/mediciones",
            {"item": "gel", "fecha": "2031-07-01", "datos": {}})
        self.assertEqual(codigo, 400)

    def test_item_diario_guarda_una_medicion_por_dia(self):
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-08"})
        rid = rel["relevamiento_id"]
        for dia, quemadas in (("2031-08-01", {}), ("2031-08-02", {"Hall central": 5})):
            codigo, _ = self.auditor(
                "POST", f"/api/los/relevamientos/{rid}/mediciones",
                {"item": "iluminacion", "fecha": dia, "datos": {"quemadas": quemadas}})
            self.assertEqual(codigo, 200)

        _, dash = self.auditor("GET", "/api/los/dashboard?periodo=2031-08")
        ilum = next(i for i in dash["items"] if i["clave"] == "iluminacion")
        self.assertEqual(ilum["periodicidad"], "DIARIO")
        self.assertEqual(ilum["diario"]["dias_relevados"],
                         ["2031-08-01", "2031-08-02"])

    def test_el_mes_no_cumple_si_falla_un_solo_dia(self):
        """El nivel de servicio es permanente: un buen día no compensa a otro."""
        self.admin("POST", "/api/inventario/luminarias",
                   {"sector": "Sector falla", "cantidad": 10})
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-09"})
        rid = rel["relevamiento_id"]
        self.auditor("POST", f"/api/los/relevamientos/{rid}/mediciones",
                     {"item": "iluminacion", "fecha": "2031-09-01",
                      "datos": {"quemadas": {}}})
        self.auditor("POST", f"/api/los/relevamientos/{rid}/mediciones",
                     {"item": "iluminacion", "fecha": "2031-09-02",
                      "datos": {"quemadas": {"Sector falla": 9}}})

        _, dash = self.auditor("GET", "/api/los/dashboard?periodo=2031-09")
        ilum = next(i for i in dash["items"] if i["clave"] == "iluminacion")
        self.assertEqual(ilum["estado"], "NO_CUMPLE")
        self.assertEqual(ilum["diario"]["dias_incumplen"], ["2031-09-02"])

    def test_auditor_no_carga_inventario(self):
        codigo, _ = self.auditor("POST", "/api/inventario/luminarias",
                                 {"sector": "Otro", "cantidad": 10})
        self.assertEqual(codigo, 403)

    def test_recurso_de_inventario_desconocido(self):
        codigo, _ = self.admin("POST", "/api/inventario/dragones", {"nombre": "x"})
        self.assertEqual(codigo, 404)

    def test_nucleo_con_equipos(self):
        codigo, r = self.admin("POST", "/api/inventario/nucleos", {
            "nombre": "PMR Hall", "tipo": "PMR",
            "equipos": {"inodoros": 1, "bachas": 1, "cestos": 1}})
        self.assertEqual(codigo, 200, r)
        _, listado = self.admin("GET", "/api/inventario/nucleos")
        nucleo = next(n for n in listado["nucleos"] if n["nombre"] == "PMR Hall")
        self.assertEqual(nucleo["equipos"]["inodoros"], 1)

    def test_tipo_de_nucleo_invalido_se_rechaza_con_mensaje_util(self):
        codigo, r = self.admin("POST", "/api/inventario/nucleos",
                               {"nombre": "X", "tipo": "MIXTO",
                                "equipos": {"inodoros": 1}})
        self.assertEqual(codigo, 400)
        self.assertIn("DAMAS", r["error"])   # enumera las opciones válidas

    def test_dashboard_los(self):
        codigo, d = self.auditor("GET", "/api/los/dashboard?periodo=2031-04")
        self.assertEqual(codigo, 200)
        self.assertEqual(d["no_aplica"], ["pasarelas"])
        self.assertEqual(d["items_aplicables"], 10)

    def test_medicion_de_item_inexistente(self):
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-05"})
        codigo, _ = self.auditor(
            "POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/mediciones",
            {"item": "teletransportador", "datos": {}})
        self.assertEqual(codigo, 404)

    def test_relevamiento_actual_sin_datos(self):
        codigo, r = self.auditor("GET", "/api/los/relevamientos/actual?periodo=2031-06")
        self.assertEqual(codigo, 200)
        self.assertIsNone(r["relevamiento"])
        self.assertEqual(r["mediciones"], {})

    def test_crear_relevamiento_es_idempotente(self):
        _, r1 = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-07"})
        _, r2 = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-07"})
        self.assertEqual(r1["relevamiento_id"], r2["relevamiento_id"])

    def test_relevamiento_actual_prellena_lo_ya_cargado(self):
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-08"})
        self.auditor("POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/mediciones",
                    {"item": "gel", "datos": {
                        "pruebas": [{"ayuda_luminosa": "PAPI", "tiempo_s": 10}]}})

        codigo, r = self.auditor("GET", "/api/los/relevamientos/actual?periodo=2031-08")
        self.assertEqual(codigo, 200)
        self.assertEqual(r["relevamiento"]["id"], rel["relevamiento_id"])
        self.assertIn("gel", r["mediciones"])
        self.assertTrue(r["mediciones"]["gel"]["resultado"]["cumple"])

    def test_solo_admin_reabre_relevamiento(self):
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2031-09"})
        self.auditor("POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/cerrar")

        codigo, _ = self.auditor(
            "POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/reabrir",
            {"motivo": "x"})
        self.assertEqual(codigo, 403)
        codigo, _ = self.admin(
            "POST", f"/api/los/relevamientos/{rel['relevamiento_id']}/reabrir",
            {"motivo": "Carga tardía"})
        self.assertEqual(codigo, 200)

    def test_eventos_de_elevacion(self):
        _, eq = self.admin("POST", "/api/inventario/elevacion",
                           {"nombre": "Ascensor Norte", "redundancia": False})
        codigo, r = self.auditor("POST", "/api/los/elevacion/eventos", {
            "equipo_id": eq["id"], "periodo": "2031-10",
            "inicio": "2031-10-05 08:00", "horas": 6, "motivo": "Mantenimiento"})
        self.assertEqual(codigo, 200, r)

        _, lista = self.auditor("GET", "/api/los/elevacion/eventos?periodo=2031-10")
        self.assertEqual(len(lista["eventos"]), 1)
        self.assertEqual(lista["eventos"][0]["equipo_nombre"], "Ascensor Norte")

        codigo, _ = self.admin(
            "DELETE", f"/api/los/elevacion/eventos/{lista['eventos'][0]['id']}")
        self.assertEqual(codigo, 200)
        _, lista = self.auditor("GET", "/api/los/elevacion/eventos?periodo=2031-10")
        self.assertEqual(lista["eventos"], [])

    def test_evento_elevacion_sin_horas_es_400(self):
        _, eq = self.admin("POST", "/api/inventario/elevacion", {"nombre": "A3"})
        codigo, _ = self.auditor("POST", "/api/los/elevacion/eventos",
                                 {"equipo_id": eq["id"], "inicio": "2031-11-01 08:00"})
        self.assertEqual(codigo, 400)

    def test_evento_elevacion_equipo_inexistente_es_404(self):
        codigo, _ = self.auditor("POST", "/api/los/elevacion/eventos",
                                 {"equipo_id": 999999, "inicio": "2031-11-01 08:00",
                                  "horas": 2})
        self.assertEqual(codigo, 404)

    def test_auditor_no_borra_evento_elevacion(self):
        _, eq = self.admin("POST", "/api/inventario/elevacion", {"nombre": "A4"})
        _, ev = self.auditor("POST", "/api/los/elevacion/eventos", {
            "equipo_id": eq["id"], "inicio": "2031-11-02 08:00", "horas": 3})
        codigo, _ = self.auditor("DELETE", f"/api/los/elevacion/eventos/{ev['evento_id']}")
        self.assertEqual(codigo, 403)


class TestEquipamientoAPI(TestAPI):
    """El ítem 4 se releva en el control diario, no se carga como horas."""

    def _control(self, fecha):
        _, r = self.auditor("POST", "/api/controles", {"fecha": fecha})
        return r["control_id"]

    def test_el_control_lista_los_equipos_exigidos(self):
        cid = self._control("2021-03-01")
        codigo, r = self.auditor("GET", f"/api/controles/{cid}/equipamiento")
        self.assertEqual(codigo, 200)
        self.assertEqual(len(r["equipos"]), 6)
        self.assertTrue(all(not e["fuera_servicio"] for e in r["equipos"]))
        self.assertEqual(r["resumen"]["porcentaje"], 1.0)

    def test_marcar_y_desmarcar_fuera_de_servicio(self):
        cid = self._control("2021-03-02")
        _, r = self.auditor("GET", f"/api/controles/{cid}/equipamiento")
        eid = r["equipos"][0]["id"]

        codigo, _ = self.auditor(f"PUT", f"/api/controles/{cid}/equipamiento/{eid}",
                                 {"fuera_servicio": True,
                                  "observacion": "Motor quemado"})
        self.assertEqual(codigo, 200)
        _, r = self.auditor("GET", f"/api/controles/{cid}/equipamiento")
        marcado = next(e for e in r["equipos"] if e["id"] == eid)
        self.assertTrue(marcado["fuera_servicio"])
        self.assertEqual(marcado["observacion"], "Motor quemado")
        self.assertAlmostEqual(r["resumen"]["porcentaje"], 5 / 6)

        self.auditor("PUT", f"/api/controles/{cid}/equipamiento/{eid}",
                     {"fuera_servicio": False})
        _, r = self.auditor("GET", f"/api/controles/{cid}/equipamiento")
        self.assertEqual(r["resumen"]["porcentaje"], 1.0)

    def test_fuera_de_servicio_exige_motivo(self):
        """Mismo criterio que un desvío: sin observación no se registra."""
        cid = self._control("2021-03-03")
        _, r = self.auditor("GET", f"/api/controles/{cid}/equipamiento")
        eid = r["equipos"][0]["id"]
        codigo, _ = self.auditor(f"PUT", f"/api/controles/{cid}/equipamiento/{eid}",
                                 {"fuera_servicio": True, "observacion": "  "})
        self.assertEqual(codigo, 400)

    def test_no_se_marca_en_un_control_cerrado(self):
        cid = self._control("2021-03-04")
        _, sectores = self.auditor("GET", "/api/sectores")
        for s in sectores["sectores"]:
            self.auditor("POST", f"/api/controles/{cid}/sectores/{s['id']}/confirmar")
        self.auditor("POST", f"/api/controles/{cid}/cerrar")

        _, r = self.auditor("GET", f"/api/controles/{cid}/equipamiento")
        eid = r["equipos"][0]["id"]
        codigo, _ = self.auditor(f"PUT", f"/api/controles/{cid}/equipamiento/{eid}",
                                 {"fuera_servicio": True, "observacion": "x"})
        self.assertEqual(codigo, 403)

    def test_equipo_inexistente_es_404(self):
        cid = self._control("2021-03-05")
        codigo, _ = self.auditor(f"PUT", f"/api/controles/{cid}/equipamiento/99999",
                                 {"fuera_servicio": True, "observacion": "x"})
        self.assertEqual(codigo, 404)

    def test_confirmar_equipos_del_periodo(self):
        _, r = self.admin("GET", "/api/periodos/2021-04/equipamiento")
        self.assertEqual(len(r["equipos"]), 6)

        ids = [e["id"] for e in r["equipos"]][:4]     # este mes solo rigen 4
        codigo, _ = self.admin("PUT", "/api/periodos/2021-04/equipamiento",
                               {"exigidos": ids})
        self.assertEqual(codigo, 200)

        _, r = self.admin("GET", "/api/periodos/2021-04/equipamiento")
        self.assertEqual(len([e for e in r["equipos"] if e["exigido"]]), 4)
        self.assertEqual(r["resultado"]["exigidos"], 4)

    def test_auditor_no_confirma_equipos_del_periodo(self):
        codigo, _ = self.auditor("PUT", "/api/periodos/2021-05/equipamiento",
                                 {"exigidos": []})
        self.assertEqual(codigo, 403)

    def test_equipo_inexistente_en_la_confirmacion_es_404(self):
        codigo, _ = self.admin("PUT", "/api/periodos/2021-06/equipamiento",
                               {"exigidos": [99999]})
        self.assertEqual(codigo, 404)

    def test_horas_maquina_ya_no_se_aceptan(self):
        """Se quitaron: no eran exigibles por pliego ni medibles."""
        codigo, r = self.admin("PUT", "/api/periodos/2021-07/datos",
                               {"horas_maquina_programadas": 200})
        self.assertEqual(codigo, 400)
        self.assertIn("Nada para actualizar", r["error"])


class TestConfiguracion(TestAPI):
    def test_onboarding_reporta_lo_que_falta(self):
        codigo, r = self.admin("GET", "/api/onboarding")
        self.assertEqual(codigo, 200)
        self.assertEqual(r["total"], 6)
        self.assertIn("pasos", r)
        claves = {p["clave"] for p in r["pasos"]}
        self.assertEqual(claves, {"nucleos", "luminarias", "asientos",
                                  "puertas", "elevacion", "secciones"})

    def test_onboarding_avanza_al_cargar_inventario(self):
        _, antes = self.admin("GET", "/api/onboarding")
        self.admin("POST", "/api/inventario/secciones",
                   {"identificador": "RWY-01", "tipo": "PISTA"})
        _, despues = self.admin("GET", "/api/onboarding")
        self.assertEqual(despues["completos"], antes["completos"] + 1)
        paso = next(p for p in despues["pasos"] if p["clave"] == "secciones")
        self.assertTrue(paso["completo"])

    def test_config_rechaza_valor_invalido(self):
        """El 90 en vez de 0,90: el error de carga más probable."""
        codigo, r = self.admin("PUT", "/api/config/iluminacion_objetivo", {"valor": 90})
        self.assertEqual(codigo, 400)
        self.assertIn("0,90 es 90%", r["error"])

        _, cfg = self.admin("GET", "/api/config?grupo=los")
        valor = next(c["valor"] for c in cfg["config"]
                     if c["clave"] == "iluminacion_objetivo")
        self.assertEqual(valor, 0.90)      # no se guardó nada

    def test_config_rechaza_pesos_que_no_cierran(self):
        malos = {"documentacion": 0.10, "ley_19587": 0.10,
                 "programacion_trabajos": 0.40, "maquinarias": 0.10,
                 "insumos": 0.10, "calidad_servicio": 0.50}
        codigo, r = self.admin("PUT", "/api/config/pesos", {"valor": malos})
        self.assertEqual(codigo, 400)
        self.assertIn("100%", r["error"])

    def test_config_acepta_pesos_validos(self):
        buenos = {"documentacion": 0.05, "ley_19587": 0.05,
                  "programacion_trabajos": 0.40, "maquinarias": 0.10,
                  "insumos": 0.10, "calidad_servicio": 0.30}
        codigo, _ = self.admin("PUT", "/api/config/pesos", {"valor": buenos})
        self.assertEqual(codigo, 200)
        # restaurar para no afectar otros tests
        self.admin("PUT", "/api/config/pesos", {"valor": {
            "documentacion": 0.10, "ley_19587": 0.10, "programacion_trabajos": 0.40,
            "maquinarias": 0.10, "insumos": 0.10, "calidad_servicio": 0.20}})

    def test_edicion_de_config_queda_logueada_con_valor_anterior(self):
        self.admin("PUT", "/api/config/cobertura_minima_mes", {"valor": 0.7})
        conn = db.conectar()
        fila = conn.execute(
            "SELECT detalle FROM auditoria_log WHERE accion = 'EDITAR_CONFIG' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        self.assertIn("anterior", fila["detalle"])
        self.admin("PUT", "/api/config/cobertura_minima_mes", {"valor": 0.8})

    def test_editar_inventario(self):
        _, nuevo = self.admin("POST", "/api/inventario/luminarias",
                              {"sector": "Sala VIP", "cantidad": 10})
        codigo, _ = self.admin(f"PUT", f"/api/inventario/luminarias/{nuevo['id']}",
                               {"sector": "Sala VIP", "cantidad": 25})
        self.assertEqual(codigo, 200)
        _, listado = self.admin("GET", "/api/inventario/luminarias")
        fila = next(l for l in listado["luminarias"] if l["id"] == nuevo["id"])
        self.assertEqual(fila["cantidad"], 25)

    def test_editar_inventario_inexistente_es_404(self):
        codigo, _ = self.admin("PUT", "/api/inventario/luminarias/99999",
                               {"sector": "X", "cantidad": 5})
        self.assertEqual(codigo, 404)

    def test_editar_nucleo_reemplaza_sus_equipos(self):
        _, nuevo = self.admin("POST", "/api/inventario/nucleos", {
            "nombre": "Caballeros Arribos", "tipo": "CABALLEROS",
            "equipos": {"inodoros": 2, "mingitorios": 2}})
        self.admin("PUT", f"/api/inventario/nucleos/{nuevo['id']}", {
            "nombre": "Caballeros Arribos", "tipo": "CABALLEROS",
            "equipos": {"inodoros": 3, "bachas": 2}})

        _, listado = self.admin("GET", "/api/inventario/nucleos")
        n = next(x for x in listado["nucleos"] if x["id"] == nuevo["id"])
        self.assertEqual(n["equipos"], {"inodoros": 3, "bachas": 2})

    def test_equipos_en_cero_no_se_guardan(self):
        _, nuevo = self.admin("POST", "/api/inventario/nucleos", {
            "nombre": "PMR Arribos", "tipo": "PMR",
            "equipos": {"inodoros": 1, "mingitorios": 0}})
        _, listado = self.admin("GET", "/api/inventario/nucleos")
        n = next(x for x in listado["nucleos"] if x["id"] == nuevo["id"])
        self.assertEqual(n["equipos"], {"inodoros": 1})

    def test_asientos_rechaza_valor_invalido(self):
        codigo, _ = self.admin("PUT", "/api/inventario/asientos", {"instalados": -5})
        self.assertEqual(codigo, 400)

    def test_toggle_de_item_los(self):
        codigo, r = self.admin("PUT", "/api/los/items/pasarelas", {"aplica": True})
        self.assertEqual(codigo, 200)
        self.assertTrue(r["aplica"])

        _, items = self.auditor("GET", "/api/los/items")
        self.assertTrue(next(i for i in items["items"]
                             if i["clave"] == "pasarelas")["aplica"])

        self.admin("PUT", "/api/los/items/pasarelas", {"aplica": False})

    def test_toggle_de_item_inexistente_es_404(self):
        codigo, _ = self.admin("PUT", "/api/los/items/inventado", {"aplica": True})
        self.assertEqual(codigo, 404)

    def test_auditor_no_edita_items_los(self):
        codigo, _ = self.auditor("PUT", "/api/los/items/pasarelas", {"aplica": True})
        self.assertEqual(codigo, 403)


class TestEquipamientoEInsumos(TestAPI):
    def test_alta_edicion_y_baja_de_equipamiento(self):
        _, listado = self.auditor("GET", "/api/equipamiento")
        self.assertEqual(len(listado["equipamiento"]), 6)   # del seed

        codigo, nuevo = self.admin("POST", "/api/equipamiento",
                                   {"nombre": "Carro de limpieza"})
        self.assertEqual(codigo, 200)

        self.admin("PUT", f"/api/equipamiento/{nuevo['id']}", {"exigido": False})
        _, listado = self.auditor("GET", "/api/equipamiento")
        fila = next(e for e in listado["equipamiento"] if e["id"] == nuevo["id"])
        self.assertEqual(fila["exigido"], 0)

        self.admin("DELETE", f"/api/equipamiento/{nuevo['id']}")
        _, listado = self.auditor("GET", "/api/equipamiento")
        self.assertEqual(len(listado["equipamiento"]), 6)

    def test_equipamiento_duplicado_es_409(self):
        self.admin("POST", "/api/equipamiento", {"nombre": "Mopa industrial"})
        codigo, _ = self.admin("POST", "/api/equipamiento", {"nombre": "Mopa industrial"})
        self.assertEqual(codigo, 409)

    def test_equipamiento_sin_nombre_es_400(self):
        codigo, _ = self.admin("POST", "/api/equipamiento", {"nombre": "  "})
        self.assertEqual(codigo, 400)

    def test_auditor_no_crea_equipamiento(self):
        codigo, _ = self.auditor("POST", "/api/equipamiento", {"nombre": "X"})
        self.assertEqual(codigo, 403)

    def test_insumos_y_stock_alimentan_la_certificacion(self):
        _, det = self.admin("POST", "/api/insumos",
                            {"nombre": "Detergente", "punto_pedido": 10,
                             "unidad": "litros"})
        _, papel = self.admin("POST", "/api/insumos",
                              {"nombre": "Papel higiénico", "punto_pedido": 50,
                               "unidad": "rollos"})

        # Uno por encima del punto de pedido, otro por debajo ⇒ 50%
        self.auditor("PUT", f"/api/periodos/2033-01/insumos/{det['id']}", {"stock": 40})
        self.auditor("PUT", f"/api/periodos/2033-01/insumos/{papel['id']}", {"stock": 5})

        # Los obligatorios del contrato, sin los cuales no se llega al detalle.
        self.admin("PUT", "/api/periodos/2033-01/datos",
                   {"documentacion_verificada": 1, "ley_19587_verificada": 1,
                    "horas_hombre_programadas": 1000})

        _, cert = self.auditor("GET", "/api/periodos/2033-01/certificacion")
        self.assertEqual(cert["detalle"]["insumos"]["valor"], 0.5)

    def test_listado_de_insumos_trae_el_stock_del_periodo(self):
        _, ins = self.admin("POST", "/api/insumos",
                            {"nombre": "Lavandina", "punto_pedido": 20})
        self.auditor("PUT", f"/api/periodos/2033-02/insumos/{ins['id']}", {"stock": 30})

        _, r = self.auditor("GET", "/api/insumos?periodo=2033-02")
        fila = next(i for i in r["insumos"] if i["id"] == ins["id"])
        self.assertEqual(fila["stock"], 30)

        # En otro período el mismo insumo aparece sin relevar
        _, otro = self.auditor("GET", "/api/insumos?periodo=2033-03")
        fila = next(i for i in otro["insumos"] if i["id"] == ins["id"])
        self.assertIsNone(fila["stock"])

    def test_stock_negativo_es_400(self):
        _, ins = self.admin("POST", "/api/insumos",
                            {"nombre": "Trapos", "punto_pedido": 5})
        codigo, _ = self.auditor("PUT", f"/api/periodos/2033-04/insumos/{ins['id']}",
                                 {"stock": -1})
        self.assertEqual(codigo, 400)

    def test_insumo_sin_punto_de_pedido_es_400(self):
        codigo, _ = self.admin("POST", "/api/insumos", {"nombre": "Sin punto"})
        self.assertEqual(codigo, 400)

    def test_datos_del_periodo_se_pueden_leer(self):
        self.admin("PUT", "/api/periodos/2033-05/datos",
                   {"horas_hombre_programadas": 900, "monto_adjudicado": 123456})
        codigo, r = self.admin("GET", "/api/periodos/2033-05/datos")
        self.assertEqual(codigo, 200)
        self.assertEqual(r["datos"]["horas_hombre_programadas"], 900)
        self.assertEqual(r["datos"]["monto_adjudicado"], 123456)

    def test_periodo_sin_datos_devuelve_null(self):
        _, r = self.admin("GET", "/api/periodos/2098-01/datos")
        self.assertIsNone(r["datos"])


class TestSync(TestAPI):
    def test_operacion_duplicada_no_se_aplica_dos_veces(self):
        """Escenario real: la tablet pierde red al confirmar y reintenta."""
        _, ctrl = self.auditor("POST", "/api/controles", {"fecha": "2020-10-01"})
        cid = ctrl["control_id"]
        _, sectores = self.auditor("GET", "/api/sectores")
        item = sectores["sectores"][0]["items"][0]["id"]

        op = {"uuid": "op-unica-123", "metodo": "POST",
              "ruta": f"/api/controles/{cid}/desvios",
              "body": {"item_id": item, "estado": "DESVIO_TOTAL",
                       "observacion": "Cargado offline"}}

        _, r1 = self.auditor("POST", "/api/sync", {"operaciones": [op]})
        self.assertEqual(r1["resultados"][0]["estado"], "OK")

        _, r2 = self.auditor("POST", "/api/sync", {"operaciones": [op]})
        self.assertEqual(r2["resultados"][0]["estado"], "DUPLICADA")

        _, nc = self.auditor("GET", "/api/periodos/2020-10/no-conformidades")
        self.assertEqual(len(nc["no_conformidades"]), 1)

    def test_lote_mixto_no_aborta_por_un_error(self):
        _, ctrl = self.auditor("POST", "/api/controles", {"fecha": "2020-11-01"})
        cid = ctrl["control_id"]
        _, sectores = self.auditor("GET", "/api/sectores")
        sid = sectores["sectores"][0]["id"]

        _, r = self.auditor("POST", "/api/sync", {"operaciones": [
            {"uuid": "ok-1", "metodo": "POST",
             "ruta": f"/api/controles/{cid}/sectores/{sid}/confirmar"},
            {"uuid": "malo-1", "metodo": "POST",
             "ruta": f"/api/controles/{cid}/desvios",
             "body": {"item_id": 999999, "estado": "DESVIO_TOTAL", "observacion": "x"}},
            {"uuid": "ok-2", "metodo": "GET", "ruta": f"/api/controles/{cid}"},
        ]})
        estados = [x["estado"] for x in r["resultados"]]
        self.assertEqual(estados, ["OK", "ERROR", "OK"])

    def test_operacion_sin_uuid_se_rechaza(self):
        _, r = self.auditor("POST", "/api/sync", {"operaciones": [
            {"metodo": "GET", "ruta": "/api/sectores"}]})
        self.assertEqual(r["resultados"][0]["estado"], "ERROR")


class TestSeguridad(TestAPI):
    def test_no_se_sirve_fuera_del_directorio_de_fotos(self):
        codigo, _ = self.auditor("GET", "/api/fotos/..%2F..%2Fschema.sql")
        self.assertIn(codigo, (400, 404))

    def test_json_invalido_es_400(self):
        url = f"http://127.0.0.1:{self.puerto}/api/login"
        req = urllib.request.Request(url, data=b"{no es json", method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                codigo = r.status
        except urllib.error.HTTPError as e:
            codigo = e.code
        self.assertEqual(codigo, 400)

    def test_ruta_inexistente_es_404(self):
        codigo, _ = self.auditor("GET", "/api/no-existe")
        self.assertEqual(codigo, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestLoSPeriodicidad(TestAPI):
    """Punto 6: solo se diarizan los ítems que se relevan mirando."""

    def test_cada_item_declara_su_periodicidad(self):
        _, dash = self.auditor("GET", "/api/los/dashboard?periodo=2032-01")
        p = {i["clave"]: i["periodicidad"] for i in dash["items"]}
        self.assertEqual(p["iluminacion"], "DIARIO")
        self.assertEqual(p["infraestructura"], "DIARIO")
        self.assertEqual(p["asientos_preembarque"], "DIARIO")
        self.assertEqual(p["puntos_carga"], "DIARIO")
        # Los que exigen instrumental, índice o acumulación quedan como estaban.
        self.assertEqual(p["confort_termico"], "MENSUAL")
        self.assertEqual(p["gel"], "MENSUAL")
        self.assertEqual(p["pista_rodajes"], "MENSUAL")
        self.assertEqual(p["medios_elevacion"], "POR_EVENTO")
        # Los que ya salen del check-list no se cargan a mano.
        self.assertEqual(p["banos"], "DERIVADO")
        self.assertEqual(p["limpieza_terminal"], "DERIVADO")

    def test_una_medicion_mensual_previa_sigue_valiendo(self):
        """Al diarizar un ítem, lo ya auditado no puede desaparecer de la vista."""
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2032-02"})
        rid = rel["relevamiento_id"]

        # Se simula el dato viejo: una medición mensual del ítem ahora diario.
        conn = db.conectar()
        conn.execute(
            "INSERT INTO los_mediciones (relevamiento_id, item_clave, fecha, datos, "
            "cumple) VALUES (?, 'infraestructura', '', '{}', 1)", (rid,))
        conn.commit()
        conn.close()

        _, dash = self.auditor("GET", "/api/los/dashboard?periodo=2032-02")
        infra = next(i for i in dash["items"] if i["clave"] == "infraestructura")
        self.assertEqual(infra["estado"], "CUMPLE")
        self.assertEqual(infra["diario"]["origen"], "medicion_mensual_previa")


class TestReapertura(TestAPI):
    """Reabrir es la única vía para tocar un cierre, y es solo de admin."""

    def _cerrado(self, fecha):
        cid = self._control_nuevo(fecha)
        _, s = self.auditor("GET", "/api/sectores")
        for sec in s["sectores"]:
            self.auditor("POST", f"/api/controles/{cid}/sectores/{sec['id']}/confirmar")
        codigo, _ = self.auditor("POST", f"/api/controles/{cid}/cerrar")
        self.assertEqual(codigo, 200)
        return cid

    def _control_nuevo(self, fecha, turno="MANANA"):
        _, r = self.auditor("POST", "/api/controles", {"fecha": fecha, "turno": turno})
        return r["control_id"]

    def test_el_auditor_no_puede_reabrir(self):
        cid = self._cerrado("2025-11-04")
        codigo, _ = self.auditor("POST", f"/api/controles/{cid}/reabrir",
                                 {"motivo": "Me equivoqué"})
        self.assertEqual(codigo, 403)

    def test_reabrir_exige_motivo(self):
        cid = self._cerrado("2025-11-05")
        codigo, _ = self.admin("POST", f"/api/controles/{cid}/reabrir", {})
        self.assertEqual(codigo, 400)

    def test_reabrir_deja_el_control_editable_y_queda_en_el_log(self):
        cid = self._cerrado("2025-11-06")
        codigo, _ = self.admin("POST", f"/api/controles/{cid}/reabrir",
                               {"motivo": "Faltó cargar un desvío"})
        self.assertEqual(codigo, 200)

        _, r = self.auditor("GET", f"/api/controles/{cid}")
        self.assertEqual(r["control"]["estado"], "ABIERTO")
        self.assertIsNone(r["control"]["cerrado_en"])

        conn = db.conectar()
        fila = conn.execute(
            "SELECT accion, detalle FROM auditoria_log WHERE entidad_id = ? "
            "AND accion = 'REABRIR_CONTROL'", (cid,)).fetchone()
        conn.close()
        self.assertIsNotNone(fila)
        self.assertIn("Faltó cargar un desvío", fila["detalle"])

    def test_un_relevamiento_los_cerrado_sigue_siendo_el_del_periodo(self):
        """Si al cerrarlo la pantalla lo viera como inexistente, no habría dónde
        mostrar el aviso de cerrado ni el botón de reabrir, y la carga siguiente
        abriría un relevamiento nuevo partiendo el mes en dos."""
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2033-02"})
        rid = rel["relevamiento_id"]
        self.auditor("POST", f"/api/los/relevamientos/{rid}/cerrar")

        _, r = self.auditor("GET", "/api/los/relevamientos/actual?periodo=2033-02")
        self.assertIsNotNone(r["relevamiento"])
        self.assertEqual(r["relevamiento"]["id"], rid)
        self.assertEqual(r["relevamiento"]["estado"], "CERRADO")

    def test_reabrir_relevamiento_los_solo_admin_y_con_motivo(self):
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2033-03"})
        rid = rel["relevamiento_id"]
        self.auditor("POST", f"/api/los/relevamientos/{rid}/cerrar")

        self.assertEqual(
            self.auditor("POST", f"/api/los/relevamientos/{rid}/reabrir",
                         {"motivo": "x"})[0], 403)
        self.assertEqual(
            self.admin("POST", f"/api/los/relevamientos/{rid}/reabrir", {})[0], 400)
        self.assertEqual(
            self.admin("POST", f"/api/los/relevamientos/{rid}/reabrir",
                       {"motivo": "Falta cargar pista"})[0], 200)

        _, r = self.auditor("GET", "/api/los/relevamientos/actual?periodo=2033-03")
        self.assertEqual(r["relevamiento"]["estado"], "ABIERTO")

    def test_cerrado_no_admite_cargas_hasta_reabrirlo(self):
        _, rel = self.auditor("POST", "/api/los/relevamientos", {"periodo": "2033-04"})
        rid = rel["relevamiento_id"]
        self.auditor("POST", f"/api/los/relevamientos/{rid}/cerrar")

        codigo, _ = self.auditor(
            "POST", f"/api/los/relevamientos/{rid}/mediciones",
            {"item": "gel", "datos": {}})
        self.assertEqual(codigo, 403)


class TestArtefactosDesdeElControl(TestAPI):
    """3.1.a se carga en el control diario, no en LoS."""

    # El núcleo se crea a demanda y no en setUpClass: TestAPI ya usa
    # setUpClass para levantar la base y el servidor, y sobrescribirlo dispara
    # una segunda inicialización sobre el mismo archivo.
    _nucleo = None

    @property
    def nucleo(self):
        if TestArtefactosDesdeElControl._nucleo is None:
            conn = db.conectar()
            cur = conn.execute(
                "INSERT INTO nucleos_sanitarios (nombre, tipo, activo) "
                "VALUES ('PMR test', 'PMR', 1)")
            nid = cur.lastrowid
            conn.execute("INSERT INTO nucleo_equipos (nucleo_id, equipo, instalados) "
                         "VALUES (?, 'inodoros', 2)", (nid,))
            conn.commit()
            conn.close()
            TestArtefactosDesdeElControl._nucleo = nid
        return TestArtefactosDesdeElControl._nucleo

    def _control(self, fecha):
        _, r = self.auditor("POST", "/api/controles", {"fecha": fecha})
        return r["control_id"]

    def test_el_control_lista_los_nucleos_con_su_estado(self):
        cid = self._control("2025-12-01")
        codigo, r = self.auditor("GET", f"/api/controles/{cid}/artefactos")
        self.assertEqual(codigo, 200)
        n = next(x for x in r["nucleos"] if x["id"] == self.nucleo)
        eq = n["equipos"][0]
        self.assertEqual(eq["instalados"], 2)
        self.assertEqual(eq["fuera_servicio"], 0)

    def test_clausurar_se_refleja_en_el_control_del_dia(self):
        cid = self._control("2025-12-02")
        codigo, _ = self.auditor(
            "POST", f"/api/controles/{cid}/artefactos/baja",
            {"nucleo_id": self.nucleo, "equipo": "inodoros", "cantidad": 1,
             "desde": "2025-12-02", "motivo": "Pérdida de agua"})
        self.assertEqual(codigo, 200)

        _, r = self.auditor("GET", f"/api/controles/{cid}/artefactos")
        n = next(x for x in r["nucleos"] if x["id"] == self.nucleo)
        self.assertEqual(n["equipos"][0]["fuera_servicio"], 1)

    def test_no_se_clausura_mas_de_lo_instalado(self):
        cid = self._control("2025-12-03")
        codigo, r = self.auditor(
            "POST", f"/api/controles/{cid}/artefactos/baja",
            {"nucleo_id": self.nucleo, "equipo": "inodoros", "cantidad": 9,
             "desde": "2025-12-03", "motivo": "x"})
        self.assertEqual(codigo, 400)
        self.assertIn("instalados", r["error"])

    def test_la_clausura_exige_motivo(self):
        cid = self._control("2025-12-04")
        codigo, _ = self.auditor(
            "POST", f"/api/controles/{cid}/artefactos/baja",
            {"nucleo_id": self.nucleo, "equipo": "inodoros", "cantidad": 1,
             "desde": "2025-12-04"})
        self.assertEqual(codigo, 400)

    def test_solo_admin_borra_una_clausura(self):
        cid = self._control("2025-12-05")
        _, r = self.auditor(
            "POST", f"/api/controles/{cid}/artefactos/baja",
            {"nucleo_id": self.nucleo, "equipo": "inodoros", "cantidad": 1,
             "desde": "2025-12-05", "motivo": "Rotura"})
        bid = r["baja_id"]
        self.assertEqual(self.auditor("DELETE", f"/api/artefactos/bajas/{bid}")[0], 403)
        self.assertEqual(self.admin("DELETE", f"/api/artefactos/bajas/{bid}")[0], 200)
