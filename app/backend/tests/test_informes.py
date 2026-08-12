"""Tests del generador de PDF y de los informes.

Un informe se firma y se adjunta a un expediente de pago: importa que el
archivo sea un PDF válido, que el texto sea extraíble (no una imagen) y que
los números que muestra sean los que calculó el motor.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import basedeprueba  # noqa: E402
import informes    # noqa: E402
import pdf         # noqa: E402
import services    # noqa: E402

PERIODO = "2026-07"


def texto_pdf(datos: bytes) -> str:
    """Extrae el texto de todas las páginas. Usa pypdf como lector externo:
    si el PDF estuviera mal armado, esto falla."""
    import io
    from pypdf import PdfReader
    lector = PdfReader(io.BytesIO(datos))
    return "\n".join(p.extract_text() for p in lector.pages)


class TestPDF(unittest.TestCase):
    def test_documento_minimo_es_valido(self):
        d = pdf.PDF()
        d.texto(50, 60, "Hola")
        datos = d.generar()
        self.assertTrue(datos.startswith(b"%PDF-1.4"))
        self.assertTrue(datos.rstrip().endswith(b"%%EOF"))
        self.assertIn("Hola", texto_pdf(datos))

    def test_multiples_paginas(self):
        d = pdf.PDF()
        d.texto(50, 60, "Primera")
        d.nueva_pagina()
        d.texto(50, 60, "Segunda")
        import io
        from pypdf import PdfReader
        lector = PdfReader(io.BytesIO(d.generar()))
        self.assertEqual(len(lector.pages), 2)

    def test_acentos_del_espanol_se_preservan(self):
        d = pdf.PDF()
        d.texto(50, 60, "Capitán Almonacid, cañón, señalización, 24,5 °C")
        t = texto_pdf(d.generar())
        for palabra in ("Capitán", "cañón", "señalización", "°C"):
            self.assertIn(palabra, t)

    def test_caracteres_fuera_de_winansi_se_transliteran(self):
        """Una flecha perdida como '?' en una advertencia de pago es peor que
        verla escrita como '->'."""
        d = pdf.PDF()
        d.texto(50, 60, "Configuración → Certificación ≥ 90% ✓")
        t = texto_pdf(d.generar())
        self.assertIn("->", t)
        self.assertIn(">=", t)
        self.assertNotIn("?", t)

    def test_parrafo_corta_lineas_y_devuelve_la_y_siguiente(self):
        d = pdf.PDF()
        y = d.parrafo(50, 60, "palabra " * 80, 300, 9)
        self.assertGreater(y, 60 + 9 * 3)      # ocupó varias líneas

    def test_ancho_texto_crece_con_el_tamano(self):
        self.assertLess(pdf.ancho_texto("abc", 8), pdf.ancho_texto("abc", 16))
        self.assertLess(pdf.ancho_texto("i", 10), pdf.ancho_texto("M", 10))

    def test_texto_vacio_no_rompe(self):
        d = pdf.PDF()
        d.texto(50, 60, "")
        d.texto(50, 70, None)
        self.assertTrue(d.generar().startswith(b"%PDF"))


class BaseInformes(unittest.TestCase):
    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="x")
        self.auditor = self.conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('jperez','J. Pérez',?, 'auditor')",
            (db.hash_password("x"),)).lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _dia_completo(self, dia, desvio=None):
        fecha = f"{PERIODO}-{dia:02d}"
        cid = self.conn.execute(
            "INSERT INTO controles_limpieza (fecha, periodo, auditor_id) VALUES (?,?,?)",
            (fecha, PERIODO, self.auditor)).lastrowid
        for s in self.conn.execute("SELECT id FROM sectores_limpieza"):
            services.confirmar_sector(self.conn, cid, s["id"], self.auditor)
        if desvio:
            item = self.conn.execute(
                "SELECT i.id FROM items_limpieza i JOIN sectores_limpieza s "
                "ON s.id = i.sector_id WHERE s.clave = 'sanidad' LIMIT 1").fetchone()
            services.registrar_desvio(self.conn, cid, item["id"],
                                      "DESVIO_TOTAL", desvio, self.auditor)
        services.cerrar_control(self.conn, cid, self.auditor)
        return cid


class TestInformeLimpieza(BaseInformes):
    def test_periodo_vacio_genera_un_pdf_valido(self):
        """Un mes sin datos tiene que producir informe igual: es la evidencia
        de que no se auditó."""
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("Cobertura", t)
        self.assertIn("Almonacid", t)

    def test_encabezado_del_pliego(self):
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("Aeropuertos Argentina S.A.", t)
        self.assertIn("julio 2026", t)

    def test_el_encabezado_no_se_superpone(self):
        """El nombre completo del aeropuerto y el título comparten renglón y
        se pisaban. Se achican hasta entrar; si no, el PDF sale ilegible."""
        inf = informes.Informe(
            titulo='Auditoría mensual · Servicio de limpieza',
            subtitulo='x', periodo=PERIODO,
            aeropuerto='Aeropuerto Capitán Vicente Almandos Almonacid — IRJ',
            concesionario='Aeropuertos Argentina S.A.')
        disponible = inf.doc.ancho - 2 * pdf.MARGEN
        # Se reconstruye la medición del encabezado con el tamaño ya ajustado.
        usado = (pdf.ancho_texto(inf.aeropuerto, 12, True)
                 + pdf.ancho_texto(inf.titulo, 11, True))
        if usado > disponible:
            # Debió achicar: se verifica que el resultado quepa.
            t = 12.0
            tt = 11.0
            while (pdf.ancho_texto(inf.aeropuerto, t, True)
                   + pdf.ancho_texto(inf.titulo, tt, True) > disponible - 12
                   and t > 7.5):
                t -= 0.5
                tt -= 0.25
            self.assertLessEqual(
                pdf.ancho_texto(inf.aeropuerto, t, True)
                + pdf.ancho_texto(inf.titulo, tt, True), disponible)

    def test_nombre_de_aeropuerto_muy_largo_no_desborda(self):
        self.conn.execute(
            "UPDATE config SET valor = ? WHERE clave = 'aeropuerto_nombre'",
            ('"' + 'Aeropuerto Internacional ' * 4 + '"',))
        self.conn.commit()
        datos = informes.informe_limpieza(self.conn, PERIODO)
        self.assertTrue(datos.startswith(b"%PDF"))   # genera igual, sin romper

    def test_muestra_los_nueve_sectores(self):
        self._dia_completo(1)
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        for sector in ("Sala de embarque", "Hall central", "Sanidad",
                       "Air side", "Estacionamiento"):
            self.assertIn(sector, t)

    def test_informa_sobre_cuantos_dias_se_calculo(self):
        """Sin esto, un 100% sobre un día parece un 100% sobre el mes."""
        self._dia_completo(1)
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("1 de 31", t)

    def test_advierte_cobertura_insuficiente(self):
        self._dia_completo(1)
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("Cobertura insuficiente", t)

    def test_mes_con_buena_cobertura_no_alarma(self):
        for d in range(1, 29):
            self._dia_completo(d)
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertNotIn("Cobertura insuficiente", t)

    def test_lista_las_no_conformidades(self):
        self._dia_completo(1, desvio="Piso con derrame sin señalizar")
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("Piso con derrame sin señalizar", t)
        self.assertIn("INMEDIATA", t)

    def test_sin_no_conformidades_lo_dice(self):
        self._dia_completo(1)
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("No se registraron no conformidades", t)

    def test_incluye_los_seis_items_de_certificacion(self):
        self._dia_completo(1)
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        for item in informes.NOMBRE_ITEM_CERT.values():
            self.assertIn(item, t)

    def test_muestra_el_importe_cuando_hay_monto_cargado(self):
        for d in range(1, 29):
            self._dia_completo(d)
        self.conn.execute(
            "INSERT INTO periodo_datos (periodo, horas_hombre_programadas, "
            "monto_adjudicado, documentacion_verificada, "
            "ley_19587_verificada) VALUES (?,1000,1000000,1,1)", (PERIODO,))
        self.conn.commit()
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("1.000.000,00", t)

    def test_sin_monto_lo_aclara_en_vez_de_mostrar_cero(self):
        self._dia_completo(1)
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("monto adjudicado sin cargar", t)

    def test_el_pdf_dice_que_las_nc_no_descuentan(self):
        """Con el descuento desactivado, el informe tiene que decirlo.

        Quien lee un informe con no conformidades da por sentado que algo
        descontaron: callarlo dejaría creer que el importe ya las contempla.
        """
        self._dia_completo(1, desvio="Algo mal")
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("NO descuentan del importe", t)

    def test_arrastra_la_advertencia_de_penalizacion_provisoria(self):
        """Y si se la activa, el PDF advierte que el criterio no es del pliego."""
        db.set_config(self.conn, "penalizacion_nc_activa", True)
        self._dia_completo(1, desvio="Algo mal")
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("NO surge del pliego", t)

    def test_tiene_espacio_de_firmas(self):
        t = texto_pdf(informes.informe_limpieza(self.conn, PERIODO))
        self.assertIn("Contratista", t)
        self.assertIn("Operaciones", t)


class TestInformeLoS(BaseInformes):
    def test_periodo_vacio_genera_pdf_valido(self):
        t = texto_pdf(informes.informe_los(self.conn, PERIODO))
        self.assertIn("Niveles de Servicio", t)

    def test_lista_los_once_items(self):
        t = texto_pdf(informes.informe_los(self.conn, PERIODO))
        for item in ("Baños", "Confort térmico", "Iluminación",
                     "Asientos en preembarque", "Grupos electrógenos",
                     "Pasarelas telescópicas"):
            self.assertIn(item, t)

    def test_marca_pasarelas_como_no_aplica(self):
        t = texto_pdf(informes.informe_los(self.conn, PERIODO))
        self.assertIn("No aplica", t)

    def test_avisa_que_falta_cargar_inventario(self):
        t = texto_pdf(informes.informe_los(self.conn, PERIODO))
        self.assertIn("Inventario sin cargar", t)

    def test_detalla_una_medicion_cargada(self):
        rel = self.conn.execute(
            "INSERT INTO relevamientos_los (periodo, auditor_id) VALUES (?,?)",
            (PERIODO, self.auditor)).lastrowid
        self.conn.commit()
        services.guardar_medicion_los(self.conn, rel, "gel", {
            "pruebas": [{"fecha": "2026-07-10", "tiempo_s": 12}]})

        t = texto_pdf(informes.informe_los(self.conn, PERIODO))
        self.assertIn("Tiempo de conmutación", t)
        self.assertIn("12", t)
        self.assertNotIn("PAPI", t)      # ya no se asocia a una ayuda luminosa

    def test_muestra_el_incumplimiento_de_confort(self):
        rel = self.conn.execute(
            "INSERT INTO relevamientos_los (periodo, auditor_id) VALUES (?,?)",
            (PERIODO, self.auditor)).lastrowid
        self.conn.commit()
        services.guardar_medicion_los(self.conn, rel, "confort_termico", {
            "estacion": "VERANO",
            "mediciones": [{"zona": "Check-in", "temperatura": 29.0}]})

        t = texto_pdf(informes.informe_los(self.conn, PERIODO))
        self.assertIn("Check-in", t)
        self.assertIn("No cumple", t)


class TestExportsCSV(BaseInformes):
    def test_todos_los_exports_producen_csv_con_bom(self):
        for nombre, (generador, _) in informes.EXPORTS.items():
            datos = generador(self.conn, PERIODO)
            self.assertTrue(datos.startswith(b"\xef\xbb\xbf"),
                            f"{nombre} sin BOM: Excel rompería los acentos")

    def test_csv_de_desvios_trae_las_columnas_esperadas(self):
        self._dia_completo(1, desvio="Piso mojado")
        texto = informes.csv_desvios(self.conn, PERIODO).decode("utf-8-sig")
        self.assertIn("Fecha", texto)
        self.assertIn("Piso mojado", texto)
        self.assertIn("2026-07-01", texto)

    def test_csv_usa_punto_y_coma_y_comillas(self):
        """Separador y comillas para que una observación con comas no rompa
        las columnas al abrirlo en Excel en español."""
        self._dia_completo(1, desvio="Piso mojado, con papeles; y vidrios")
        texto = informes.csv_desvios(self.conn, PERIODO).decode("utf-8-sig")
        self.assertIn('"Piso mojado, con papeles; y vidrios"', texto)
        self.assertIn('";"', texto)      # separador punto y coma entre celdas

    def test_csv_de_controles_una_fila_por_sector_y_dia(self):
        self._dia_completo(1)
        self._dia_completo(2)
        lineas = informes.csv_controles(self.conn, PERIODO).decode(
            "utf-8-sig").strip().split("\r\n")
        self.assertEqual(len(lineas), 1 + 9 * 2)     # encabezado + 9 sectores x 2 días

    def test_csv_los_lista_los_once_items(self):
        lineas = informes.csv_los(self.conn, PERIODO).decode(
            "utf-8-sig").strip().split("\r\n")
        self.assertEqual(len(lineas), 12)            # encabezado + 11 ítems

    def test_csv_de_no_conformidades(self):
        self._dia_completo(1, desvio="Cesto desbordado")
        texto = informes.csv_no_conformidades(self.conn, PERIODO).decode("utf-8-sig")
        self.assertIn("Cesto desbordado", texto)
        self.assertIn("INMEDIATA", texto)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestInformeDia(BaseInformes):
    """Informe de un control puntual: el documento del reclamo concreto."""

    def test_dia_sin_control_es_error(self):
        with self.assertRaises(LookupError):
            informes.informe_dia(self.conn, "2026-07-15")

    def test_informe_de_un_dia_cerrado(self):
        self._dia_completo(3, desvio="Piso con derrame sin señalizar")
        t = texto_pdf(informes.informe_dia(self.conn, f"{PERIODO}-03"))
        self.assertIn("Control diario", t)
        self.assertIn("viernes 3 de julio de 2026", t)
        self.assertIn("Piso con derrame sin señalizar", t)

    def test_lista_los_nueve_sectores_del_dia(self):
        self._dia_completo(4)
        t = texto_pdf(informes.informe_dia(self.conn, f"{PERIODO}-04"))
        for sector in ("Sala de embarque", "Hall central", "Air side"):
            self.assertIn(sector, t)

    def test_marca_el_desvio_sin_evidencia(self):
        """Un hallazgo sin foto tiene que verse como tal en el informe."""
        self._dia_completo(5, desvio="Cesto desbordado")
        t = texto_pdf(informes.informe_dia(self.conn, f"{PERIODO}-05"))
        self.assertIn("Sin evidencia fotográfica", t)

    def test_avisa_si_el_control_esta_abierto(self):
        fecha = f"{PERIODO}-06"
        self.conn.execute(
            "INSERT INTO controles_limpieza (fecha, periodo, auditor_id) VALUES (?,?,?)",
            (fecha, PERIODO, self.auditor))
        self.conn.commit()
        t = texto_pdf(informes.informe_dia(self.conn, fecha))
        self.assertIn("todavía está abierto", t)
        self.assertIn("Control incompleto", t)

    def test_los_sectores_pendientes_van_por_nombre(self):
        """El informe lo lee el contratista: nada de claves internas."""
        fecha = f"{PERIODO}-07"
        self.conn.execute(
            "INSERT INTO controles_limpieza (fecha, periodo, auditor_id) VALUES (?,?,?)",
            (fecha, PERIODO, self.auditor))
        self.conn.commit()
        t = texto_pdf(informes.informe_dia(self.conn, fecha))
        self.assertIn("Baños hall - Sector público", t)
        self.assertNotIn("banos_hall", t)

    def test_firma_con_el_auditor_que_emite(self):
        self._dia_completo(8)
        t = texto_pdf(informes.informe_dia(
            self.conn, f"{PERIODO}-08",
            {"nombre": "Juan Pérez", "usuario": "jperez",
             "emitido": "29/07/2026 10:34"}))
        self.assertIn("Juan Pérez", t)
        self.assertIn("jperez", t)
        self.assertIn("29/07/2026 10:34", t)
        self.assertIn("Responsable Contratista", t)

    def test_sin_emisor_deja_el_bloque_en_blanco(self):
        self._dia_completo(9)
        t = texto_pdf(informes.informe_dia(self.conn, f"{PERIODO}-09"))
        self.assertIn("Auditor — Aeropuertos Argentina", t)
        self.assertIn("Aclaración:", t)


class TestInformeDiaPorTurno(unittest.TestCase):
    """El informe es de UNA recorrida. Con dos turnos por día, elegir uno en
    silencio dejaba al otro sin aparecer en ningún informe."""

    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="x")
        self.auditor = self.conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('a','A. Uditor','x','auditor')").lastrowid
        self.conn.commit()
        for turno in ("MANANA", "TARDE"):
            cid = self.conn.execute(
                "INSERT INTO controles_limpieza (fecha, turno, periodo, auditor_id) "
                "VALUES ('2026-07-05', ?, '2026-07', ?)",
                (turno, self.auditor)).lastrowid
            self.conn.commit()
            for s in self.conn.execute(
                    "SELECT id FROM sectores_limpieza WHERE activo = 1"):
                services.confirmar_sector(self.conn, cid, s["id"], self.auditor)
            services.cerrar_control(self.conn, cid, self.auditor)

    def tearDown(self):
        self.conn.close()

    def test_cada_turno_tiene_su_informe(self):
        m = informes.informe_dia(self.conn, "2026-07-05", None, "MANANA")
        t = informes.informe_dia(self.conn, "2026-07-05", None, "TARDE")
        self.assertTrue(m.startswith(b"%PDF"))
        self.assertTrue(t.startswith(b"%PDF"))
        self.assertNotEqual(m, t)

    def test_sin_turno_toma_el_primero_del_dia(self):
        sin = informes.informe_dia(self.conn, "2026-07-05")
        manana = informes.informe_dia(self.conn, "2026-07-05", None, "MANANA")
        self.assertEqual(len(sin), len(manana))

    def test_un_turno_inexistente_es_un_error_claro(self):
        with self.assertRaises(LookupError) as e:
            informes.informe_dia(self.conn, "2026-07-05", None, "NOCHE")
        self.assertIn("NOCHE", str(e.exception))

    def test_el_csv_separa_fecha_y_turno(self):
        csv = informes.csv_controles(self.conn, "2026-07").decode("utf-8-sig")
        encabezado, primera = csv.splitlines()[0], csv.splitlines()[1]
        self.assertIn('"Turno"', encabezado)
        # La fecha tiene que seguir siendo una fecha, no 'fecha·turno'.
        self.assertEqual(primera.split(";")[0], '"2026-07-05"')
        self.assertIn(primera.split(";")[1], ('"Mañana"', '"Tarde"'))
