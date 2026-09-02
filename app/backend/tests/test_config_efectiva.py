"""Cada parámetro configurable tiene que mover el resultado.

Un parámetro que el admin puede editar y que el cálculo ignora es peor que un
bug: la pantalla dice "guardado", el número no cambia, y no hay forma de
notarlo desde la interfaz. Se descubre meses después, discutiendo una
certificación con el contratista.

La forma de cada test es siempre la misma: se mide con el valor por defecto,
se cambia el parámetro, se vuelve a medir, y se exige que el resultado sea
distinto. No importa cuánto cambie ni hacia dónde — importa que el parámetro
esté efectivamente conectado.

Ocho de los veinticuatro parámetros no aparecían en ninguna prueba cuando se
escribió este archivo. Los cinco que mueven números están acá.
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import basedeprueba  # noqa: E402
import calc          # noqa: E402
import db            # noqa: E402
import services      # noqa: E402

PERIODO = "2026-07"


class Base(unittest.TestCase):
    def setUp(self):
        self.conn, _ = basedeprueba.nueva()

    def tearDown(self):
        self.conn.close()


class TestElevacionHorasDia(Base):
    """`elevacion_horas_dia` — la base horaria sobre la que se mide la
    disponibilidad de los medios de elevación.

    Es el parámetro más delicado de los ocho: el propio código lo documenta
    como una decisión discutible (24 hs de calendario contra las 14 hs del
    horario operativo) y anticipa que el operador puede querer otra. Si no
    estuviera conectado, cambiarlo no haría nada y nadie se enteraría.
    """

    def _equipo_con_evento(self, horas):
        cur = self.conn.execute(
            "INSERT INTO medios_elevacion (nombre, redundancia, activo) "
            "VALUES ('Ascensor', 0, 1)")
        self.conn.execute(
            "INSERT INTO elevacion_eventos (equipo_id, periodo, inicio, horas) "
            "VALUES (?,?,?,?)", (cur.lastrowid, PERIODO, f"{PERIODO}-05T08:00", horas))
        self.conn.commit()

    def test_cambiar_la_base_horaria_cambia_la_disponibilidad(self):
        self._equipo_con_evento(40)
        con_24 = services.evaluar_elevacion(self.conn, PERIODO)

        db.set_config(self.conn, "elevacion_horas_dia", 14)
        self.conn.commit()
        con_14 = services.evaluar_elevacion(self.conn, PERIODO)

        self.assertNotEqual(con_24["equipos"][0]["disponibilidad"],
                            con_14["equipos"][0]["disponibilidad"])
        # Con menos horas base, las mismas 40 hs pesan más y hunden el ítem.
        self.assertTrue(con_24["cumple"])
        self.assertFalse(con_14["cumple"])


class TestElevacionUmbrales(Base):
    """`elevacion_umbrales` — los mínimos de disponibilidad y los topes."""

    def test_bajar_el_minimo_convierte_un_incumplimiento_en_cumplimiento(self):
        cur = self.conn.execute(
            "INSERT INTO medios_elevacion (nombre, redundancia, activo) "
            "VALUES ('Ascensor', 0, 1)")
        self.conn.execute(
            "INSERT INTO elevacion_eventos (equipo_id, periodo, inicio, horas) "
            "VALUES (?,?,?,40)", (cur.lastrowid, PERIODO, f"{PERIODO}-05T08:00"))
        self.conn.commit()
        db.set_config(self.conn, "elevacion_horas_dia", 14)
        self.conn.commit()
        self.assertFalse(services.evaluar_elevacion(self.conn, PERIODO)["cumple"])

        flojos = {"CON_REDUNDANCIA": {"disponibilidad_min": 0.50,
                                      "indisp_max_mensual_hs": 200},
                  "SIN_REDUNDANCIA": {"disponibilidad_min": 0.50,
                                      "indisp_max_mensual_hs": 200}}
        db.set_config(self.conn, "elevacion_umbrales", flojos)
        self.conn.commit()
        self.assertTrue(services.evaluar_elevacion(self.conn, PERIODO)["cumple"])


class TestGel(Base):
    """`gel_tiempos_conmutacion` y `gel_categoria_irj` — RAAC 154."""

    DATOS = {"pruebas": [{"fecha": "2026-07-10", "tiempo_s": 12}]}

    def test_bajar_el_tiempo_maximo_convierte_la_prueba_en_incumplimiento(self):
        self.assertTrue(services.evaluar_gel(self.conn, self.DATOS)["cumple"])

        db.set_config(self.conn, "gel_tiempos_conmutacion",
                      {"APROX_NO_PRECISION": 10, "APROX_PRECISION_CAT_I": 10,
                       "APROX_PRECISION_CAT_II_III": 1})
        self.conn.commit()
        self.assertFalse(services.evaluar_gel(self.conn, self.DATOS)["cumple"])

    def test_cambiar_la_categoria_del_aeropuerto_cambia_el_maximo_exigido(self):
        """IRJ es aproximación no precisión: 15 s. Bajo CAT II/III serían 1 s,
        y la misma medición de 12 s pasaría a incumplir."""
        self.assertTrue(services.evaluar_gel(self.conn, self.DATOS)["cumple"])

        db.set_config(self.conn, "gel_categoria_irj", "APROX_PRECISION_CAT_II_III")
        self.conn.commit()
        r = services.evaluar_gel(self.conn, self.DATOS)
        self.assertFalse(r["cumple"])
        self.assertEqual(r["pruebas"][0]["tiempo_maximo_s"], 1)


class TestEstaciones(Base):
    """`inicio_verano` e `inicio_invierno` — de qué rango de confort térmico
    se evalúa una medición.

    No es cosmético: verano exige 23–26 °C e invierno 19–25. La misma
    temperatura cumple en una estación y no en la otra.
    """

    def test_mover_el_inicio_del_invierno_cambia_la_estacion_de_un_dia(self):
        julio = date(2026, 7, 15)
        self.assertEqual(services.estacion_actual(self.conn, julio), "INVIERNO")

        # Si el invierno empieza en agosto, el 15 de julio cae en verano.
        db.set_config(self.conn, "inicio_invierno", "08-01")
        self.conn.commit()
        self.assertEqual(services.estacion_actual(self.conn, julio), "VERANO")

    def test_la_estacion_decide_si_una_temperatura_cumple(self):
        """21 °C cumple en invierno (19–25) y no en verano (23–26)."""
        self.assertTrue(calc.confort_termico(21.0, "INVIERNO")["cumple"])
        self.assertFalse(calc.confort_termico(21.0, "VERANO")["cumple"])


class TestLoginVentana(Base):
    """`login_ventana_minutos` — cuánto dura el bloqueo por intentos fallidos."""

    def test_la_ventana_configurada_es_la_que_se_usa(self):
        db.set_config(self.conn, "login_max_intentos", 3)
        db.set_config(self.conn, "login_ventana_minutos", 45)
        self.conn.commit()
        maximo, ventana = services._limites_login(self.conn)
        self.assertEqual(maximo, 3)
        self.assertEqual(ventana, 45)


class TestCoberturaMinima(Base):
    """`cobertura_minima_mes` — el umbral que decide si el mes es
    representativo, y con él el color de la tarjeta de Limpieza."""

    def test_subir_la_exigencia_vuelve_insuficiente_un_mes_que_alcanzaba(self):
        dias = calc.dias_del_mes(PERIODO)
        auditados = dias[:26]                       # 26 de 31 = 83,8%
        self.assertTrue(calc.completitud_mes(
            auditados, PERIODO, dias[-1], 0.80)["cobertura_suficiente"])
        self.assertFalse(calc.completitud_mes(
            auditados, PERIODO, dias[-1], 0.90)["cobertura_suficiente"])


class TestPesosCertificacion(Base):
    """`pesos` — la ponderación de los seis ítems del PCP 4.3."""

    def test_cambiar_los_pesos_cambia_el_porcentaje_a_certificar(self):
        items = {"documentacion": 1.0, "ley_19587": 1.0,
                 "programacion_trabajos": 1.0, "maquinarias": 0.0,
                 "insumos": 1.0, "calidad_servicio": 1.0}
        con_pliego = calc.certificacion_mensual(items)["porcentaje"]

        # Si maquinarias pesara la mitad del total, fallarlo dolería mucho más.
        otros = {"documentacion": 0.10, "ley_19587": 0.10,
                 "programacion_trabajos": 0.10, "maquinarias": 0.50,
                 "insumos": 0.10, "calidad_servicio": 0.10}
        self.assertNotEqual(con_pliego,
                            calc.certificacion_mensual(items, otros)["porcentaje"])


class TestCoherenciaEntreSeedYConstantes(Base):
    """Los valores del pliego que están escritos dos veces tienen que coincidir.

    Tres números del pliego viven a la vez en una constante de `calc.py` y en
    una fila sembrada de `config`. El cálculo usa siempre la constante, así que
    la fila es documentación — y una documentación que puede mentir sin que
    nadie lo note es peor que ninguna. Estos tests cierran la puerta: si
    alguien cambia uno de los dos lados, la suite falla y obliga a mirar el
    pliego para decidir cuál está bien.
    """

    def _cfg(self, clave):
        return db.get_config(self.conn, clave)

    def test_los_grados_que_cumplen_coinciden(self):
        self.assertEqual(set(self._cfg("infraestructura_grados_cumplen")),
                         calc.GRADOS_QUE_CUMPLEN)

    def test_el_minimo_de_asientos_coincide_con_la_regla_iata_sembrada(self):
        """38 no es un número suelto: sale de 76 pax por aeronave × 50%."""
        p = self._cfg("asientos_parametros_irj")
        derivado = int(p["pax_promedio_aeronave"] * p["porcentaje_minimo_sentados"])
        self.assertEqual(derivado, calc.ASIENTOS_MINIMOS_IRJ)

    def test_los_umbrales_de_llenado_coinciden(self):
        """A ≤50% · B 51-65% · C 66-80% · D >80%."""
        u = self._cfg("limpieza_terminal_umbrales_llenado")
        self.assertEqual(calc.grado_por_llenado(u["A"][1]), "A")
        self.assertEqual(calc.grado_por_llenado(u["B"][1]), "B")
        self.assertEqual(calc.grado_por_llenado(u["C"][1]), "C")
        self.assertEqual(calc.grado_por_llenado(u["D"][0]), "D")


class TestNingunParametroQuedaSuelto(Base):
    """Guarda contra el olvido.

    Cada clave sembrada está en exactamente una de las tres listas. Si mañana
    alguien agrega una y no la clasifica, este test falla y lo obliga a decidir
    qué hace: mover un cálculo, pintar una pantalla, o nada.
    """

    # Las lee el backend: cambiarlas cambia un número o una decisión.
    MUEVEN_EL_CALCULO = {
        "asientos_minimo", "banos_limpieza_objetivos", "banos_objetivo_nucleo",
        "banos_sectores_checklist", "cobertura_minima_mes", "confort_termico",
        "confort_zonas", "elevacion_horas_dia", "elevacion_indisp_max_evento_hs",
        "elevacion_umbrales", "foto_obligatoria_desvio", "gel_categoria_irj",
        "gel_tiempos_conmutacion", "horario_operativo_fin",
        "horario_operativo_inicio", "horas_operativas_dia",
        "iluminacion_horario_invierno", "iluminacion_horario_verano",
        "iluminacion_objetivo", "inicio_invierno", "inicio_verano",
        "liquidacion_dia_inicio", "login_max_intentos", "login_ventana_minutos",
        "pasarelas_aplica", "pci_pista", "pci_rodaje",
        "penalizacion_nc_activa", "penalizacion_nc_confirmada",
        "penalizacion_nc_tope", "penalizacion_nc_tope_activo",
        "penalizacion_por_nc", "periodicidad_control", "pesos",
        "tomas_por_100_pax",
        # Rótulos y enlaces que el backend sirve a la pantalla.
        "aeropuerto_categoria", "aeropuerto_codigo", "aeropuerto_nombre",
        "concesionario", "banos_link_checklist",
        "limpieza_terminal_link_checklist",
    }

    # Solo las lee el frontend: son escalas y etiquetas de formulario.
    SOLO_PANTALLA = {
        "infraestructura_escala", "infraestructura_subitems", "pci_escala",
    }

    # Sembradas y sin ningún lector. Son documentación del pliego que quedó
    # duplicada con una constante de `calc.py` (ver la clase de arriba) o texto
    # de ayuda que ninguna pantalla llegó a mostrar. No hacen daño, pero que
    # estén enumeradas evita que la lista crezca sin que nadie lo note.
    DOCUMENTACION_SIN_LECTOR = {
        "asientos_parametros_irj", "banos_criterios_hallazgo",
        "infraestructura_grados_cumplen", "limpieza_terminal_subitems",
        "limpieza_terminal_umbrales_llenado",
    }

    def test_todas_las_claves_sembradas_estan_clasificadas(self):
        sembradas = {f["clave"] for f in self.conn.execute("SELECT clave FROM config")}
        clasificadas = (self.MUEVEN_EL_CALCULO | self.SOLO_PANTALLA
                        | self.DOCUMENTACION_SIN_LECTOR)
        self.assertEqual(sembradas - clasificadas, set(),
                         "Hay claves de configuración nuevas sin clasificar")
        self.assertEqual(clasificadas - sembradas, set(),
                         "Hay claves clasificadas que ya no se siembran")


if __name__ == "__main__":
    unittest.main(verbosity=2)
