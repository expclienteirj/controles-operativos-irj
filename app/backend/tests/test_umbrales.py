"""Barrido de los umbrales del pliego: el borde exacto de cada ítem.

Por qué existe aparte de `test_calc.py`: aquellos tests verifican que cada
fórmula haga lo suyo. Estos verifican una sola cosa, la misma para todos, y es
la que decide plata: **que el cambio de "cumple" a "no cumple" ocurra
exactamente donde el pliego lo pone, ni un punto antes ni uno después.**

Un `>` donde iba `>=` no rompe nada, no tira error y no se nota leyendo el
código. Solo hace que un mes que cumplía justo pase a incumplir, o al revés.
Cada caso de acá prueba tres puntos: justo abajo del umbral, exactamente en el
umbral, y justo arriba.

El valor del umbral está escrito a mano en cada test, no importado de `calc`.
Es deliberado: si alguien cambia la constante, el test tiene que fallar y
obligar a volver al pliego. Un test que importa el valor que verifica no
verifica nada.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calc  # noqa: E402


class TestIluminacion(unittest.TestCase):
    """LoS 3.3 — 90% de luminarias encendidas por sector."""

    def test_el_borde_es_exactamente_90_por_ciento(self):
        # 50 luminarias: 5 quemadas dejan 90% justo; 6 dejan 88%.
        self.assertTrue(calc.iluminacion_sector(50, 5)["cumple"])
        self.assertFalse(calc.iluminacion_sector(50, 6)["cumple"])

    def test_el_borde_resiste_la_aritmetica_binaria(self):
        """3 quemadas de 30 dan 0,8999999999999999 en coma flotante.

        Es 90% exacto en la realidad y el pliego lo admite. Sin la tolerancia
        de `calc.EPS`, este mes incumpliría por un error de representación.
        """
        self.assertTrue(calc.iluminacion_sector(30, 3)["cumple"])

    def test_sin_inventario_no_opina(self):
        self.assertIsNone(calc.iluminacion_sector(0, 0)["cumple"])


class TestBanosEnServicio(unittest.TestCase):
    """LoS 3.1.a — 80% en baños comunes, 100% en PMR y recinto de bebés."""

    def _nucleo(self, tipo, instalados, fuera):
        return {"nombre": "N", "tipo": tipo,
                "artefactos": {"inodoros": {"instalados": instalados,
                                            "fuera_servicio": fuera}}}

    def test_el_borde_de_un_bano_comun_es_80_por_ciento(self):
        # 5 inodoros: 1 fuera de servicio deja 80% justo; 2 dejan 60%.
        self.assertTrue(calc.banos_en_servicio(self._nucleo("DAMAS", 5, 1))["cumple"])
        self.assertFalse(calc.banos_en_servicio(self._nucleo("DAMAS", 5, 2))["cumple"])

    def test_pmr_no_admite_ni_uno_fuera_de_servicio(self):
        """El 100% es literal: un solo artefacto caído incumple el ítem."""
        self.assertTrue(calc.banos_en_servicio(self._nucleo("PMR", 4, 0))["cumple"])
        self.assertFalse(calc.banos_en_servicio(self._nucleo("PMR", 4, 1))["cumple"])

    def test_el_recinto_de_bebes_exige_lo_mismo_que_pmr(self):
        self.assertTrue(
            calc.banos_en_servicio(self._nucleo("RECINTO_BEBES", 2, 0))["cumple"])
        self.assertFalse(
            calc.banos_en_servicio(self._nucleo("RECINTO_BEBES", 2, 1))["cumple"])


class TestAsientos(unittest.TestCase):
    """LoS 3.5 — mínimo 38 asientos utilizables en preembarque (IRJ)."""

    def test_el_borde_es_exactamente_38_utilizables(self):
        self.assertFalse(calc.asientos_preembarque(40, 3)["cumple"])   # 37
        self.assertTrue(calc.asientos_preembarque(40, 2)["cumple"])    # 38
        self.assertTrue(calc.asientos_preembarque(40, 1)["cumple"])    # 39

    def test_sin_inventario_no_opina(self):
        self.assertIsNone(calc.asientos_preembarque(0)["cumple"])


class TestPuntosDeCarga(unittest.TestCase):
    """LoS 3.6 — 25 tomas cada 100 pasajeros en hora pico, por puerta."""

    def test_el_requerido_redondea_para_arriba(self):
        """76 PHP piden 19 tomas exactas; 77 piden 19,25 y hay que instalar 20.

        Redondear para abajo dejaría puertas por debajo del estándar sin que
        el cálculo lo marque.
        """
        self.assertEqual(calc.tomas_requeridas(76), 19)
        self.assertEqual(calc.tomas_requeridas(77), 20)
        self.assertEqual(calc.tomas_requeridas(100), 25)

    def test_el_borde_es_tener_exactamente_las_requeridas(self):
        puerta = lambda inst: {"nombre": "1", "php": 100, "instaladas": inst,
                               "fuera_servicio": 0}
        self.assertFalse(calc.puntos_de_carga(puerta(24))["cumple"])
        self.assertTrue(calc.puntos_de_carga(puerta(25))["cumple"])
        self.assertTrue(calc.puntos_de_carga(puerta(26))["cumple"])

    def test_una_toma_fuera_de_servicio_no_cuenta_como_instalada(self):
        self.assertFalse(calc.puntos_de_carga(
            {"nombre": "1", "php": 100, "instaladas": 25, "fuera_servicio": 1})["cumple"])


class TestConfortTermico(unittest.TestCase):
    """LoS 3.2 — verano 23,0–26,0 °C (cat. B); invierno 19,0–25,0 °C (cat. C)."""

    def test_los_bordes_de_verano(self):
        self.assertFalse(calc.confort_termico(22.9, "VERANO")["cumple"])
        self.assertTrue(calc.confort_termico(23.0, "VERANO")["cumple"])
        self.assertTrue(calc.confort_termico(26.0, "VERANO")["cumple"])
        self.assertFalse(calc.confort_termico(26.1, "VERANO")["cumple"])

    def test_los_bordes_de_invierno(self):
        self.assertFalse(calc.confort_termico(18.9, "INVIERNO")["cumple"])
        self.assertTrue(calc.confort_termico(19.0, "INVIERNO")["cumple"])
        self.assertTrue(calc.confort_termico(25.0, "INVIERNO")["cumple"])
        self.assertFalse(calc.confort_termico(25.1, "INVIERNO")["cumple"])

    def test_el_borde_de_velocidad_de_aire(self):
        """0,19 m/s en verano, 0,21 en invierno. La temperatura puede estar
        perfecta y el ítem incumplir igual por corriente de aire."""
        self.assertTrue(calc.confort_termico(24.5, "VERANO", 0.19)["cumple"])
        self.assertFalse(calc.confort_termico(24.5, "VERANO", 0.20)["cumple"])
        self.assertTrue(calc.confort_termico(22.0, "INVIERNO", 0.21)["cumple"])
        self.assertFalse(calc.confort_termico(22.0, "INVIERNO", 0.22)["cumple"])


class TestGel(unittest.TestCase):
    """LoS 3.9 — conmutación del grupo electrógeno: 15 s en IRJ (RAAC 154)."""

    def test_el_borde_es_exactamente_15_segundos(self):
        self.assertTrue(calc.prueba_gel(14.9)["cumple"])
        self.assertTrue(calc.prueba_gel(15.0)["cumple"])
        self.assertFalse(calc.prueba_gel(15.1)["cumple"])

    def test_categoria_ii_iii_exige_un_segundo(self):
        self.assertTrue(calc.prueba_gel(1.0, "APROX_PRECISION_CAT_II_III")["cumple"])
        self.assertFalse(calc.prueba_gel(1.1, "APROX_PRECISION_CAT_II_III")["cumple"])

    def test_sin_medicion_no_opina(self):
        self.assertIsNone(calc.prueba_gel(None)["cumple"])


class TestPci(unittest.TestCase):
    """LoS 3.10 — pista: PCI > 70 en el 85% de las secciones.
    Rodaje: PCI > 60 en el 70%."""

    def _secciones(self, pcis):
        return [{"identificador": f"S{i}", "pci": p} for i, p in enumerate(pcis)]

    def test_el_borde_de_proporcion_en_pista_es_85_por_ciento(self):
        # 20 secciones: 17 sobre el umbral son 85% justo; 16 son 80%.
        self.assertTrue(calc.pci_secciones(
            self._secciones([80] * 17 + [50] * 3), "PISTA")["cumple"])
        self.assertFalse(calc.pci_secciones(
            self._secciones([80] * 16 + [50] * 4), "PISTA")["cumple"])

    def test_el_borde_de_proporcion_en_rodaje_es_70_por_ciento(self):
        self.assertTrue(calc.pci_secciones(
            self._secciones([70] * 14 + [50] * 6), "RODAJE")["cumple"])
        self.assertFalse(calc.pci_secciones(
            self._secciones([70] * 13 + [50] * 7), "RODAJE")["cumple"])

    def test_el_umbral_de_pci_de_pista_es_mayor_a_70_no_70(self):
        """El pliego dice PCI > 70. Una sección en 70 exacto NO alcanza."""
        una = lambda pci: calc.pci_secciones(self._secciones([pci]), "PISTA")
        self.assertFalse(una(70)["cumple"])
        self.assertTrue(una(71)["cumple"])

    def test_el_umbral_de_pci_de_rodaje_es_mayor_a_60_no_60(self):
        una = lambda pci: calc.pci_secciones(self._secciones([pci]), "RODAJE")
        self.assertFalse(una(60)["cumple"])
        self.assertTrue(una(61)["cumple"])


class TestElevacion(unittest.TestCase):
    """LoS 3.7 — el ítem tiene TRES umbrales y basta que falle uno.

    Disponibilidad mínima (91,66% con redundancia, 93% sin), tope de horas
    indisponibles del mes (60 y 48), y tope por evento individual (48 hs para
    los dos). El tercero es fácil de perder de vista: un equipo puede estar por
    encima del mínimo mensual y aun así incumplir por una única parada larga.
    """

    def _equipo(self, redundancia, eventos_hs):
        return {"nombre": "Ascensor", "redundancia": redundancia,
                "eventos": [{"horas": h} for h in eventos_hs]}

    def test_el_borde_de_disponibilidad_con_redundancia(self):
        """60 hs sobre 720 (30 días × 24) es exactamente 91,66%.

        Repartidas en dos paradas para no chocar con el tope por evento, que
        es la otra regla y se prueba aparte.
        """
        self.assertTrue(
            calc.medio_elevacion(self._equipo(True, [30, 30]), 30)["cumple"])
        self.assertFalse(
            calc.medio_elevacion(self._equipo(True, [30, 31]), 30)["cumple"])

    def test_el_borde_de_disponibilidad_sin_redundancia_es_mas_exigente(self):
        """Sin redundancia el tope mensual baja a 48 hs: 24+24 pasa, 24+25 no."""
        self.assertTrue(
            calc.medio_elevacion(self._equipo(False, [24, 24]), 30)["cumple"])
        self.assertFalse(
            calc.medio_elevacion(self._equipo(False, [24, 25]), 30)["cumple"])

    def test_el_borde_por_evento_individual_es_48_horas(self):
        """Una sola parada de 49 hs incumple aunque el mes entero esté holgado.

        Con redundancia el tope mensual es 60 hs, así que 49 hs de una sola vez
        deja la disponibilidad y el acumulado en regla: lo único que falla es
        el evento. Sin este caso, cambiar el tope por evento no rompería nada.
        """
        ok = calc.medio_elevacion(self._equipo(True, [48]), 30)
        self.assertTrue(ok["cumple"])
        mal = calc.medio_elevacion(self._equipo(True, [49]), 30)
        self.assertFalse(mal["cumple"])
        # La disponibilidad y el acumulado siguen bien: lo que falla es el evento.
        self.assertTrue(mal["disponibilidad_ok"])
        self.assertTrue(mal["mensual_ok"])
        self.assertFalse(mal["evento_ok"])


class TestGradosPorLlenado(unittest.TestCase):
    """LoS — cestos y contenedores: A ≤50% · B 51-65% · C 66-80% · D >80%.
    A y B cumplen; C y D generan no conformidad."""

    def test_los_tres_saltos_de_grado(self):
        self.assertEqual(calc.grado_por_llenado(50), "A")
        self.assertEqual(calc.grado_por_llenado(50.1), "B")
        self.assertEqual(calc.grado_por_llenado(65), "B")
        self.assertEqual(calc.grado_por_llenado(65.1), "C")
        self.assertEqual(calc.grado_por_llenado(80), "C")
        self.assertEqual(calc.grado_por_llenado(80.1), "D")

    def test_la_frontera_entre_cumplir_y_no_cumplir_esta_entre_b_y_c(self):
        self.assertIn(calc.grado_por_llenado(65), calc.GRADOS_QUE_CUMPLEN)
        self.assertNotIn(calc.grado_por_llenado(65.1), calc.GRADOS_QUE_CUMPLEN)


class TestCoberturaDelMes(unittest.TestCase):
    """Certificación — la cobertura mínima para considerar el mes
    representativo es 80%."""

    def test_el_borde_es_exactamente_80_por_ciento(self):
        dias = calc.dias_del_mes("2026-07")          # 31 días
        # 25 de 31 = 80,6% (alcanza); 24 de 31 = 77,4% (no alcanza).
        self.assertTrue(
            calc.completitud_mes(dias[:25], "2026-07", dias[-1])["cobertura_suficiente"])
        self.assertFalse(
            calc.completitud_mes(dias[:24], "2026-07", dias[-1])["cobertura_suficiente"])


class TestCertificacionMensual(unittest.TestCase):
    """Certificación — pesos 10/10/40/10/10/20 (PCP 4.3)."""

    def test_los_pesos_del_pliego_suman_uno_y_son_los_seis(self):
        pesos = calc.PESOS_CERTIFICACION_DEFAULT
        self.assertAlmostEqual(sum(pesos.values()), 1.0)
        self.assertEqual(pesos["documentacion"], 0.10)
        self.assertEqual(pesos["ley_19587"], 0.10)
        self.assertEqual(pesos["programacion_trabajos"], 0.40)
        self.assertEqual(pesos["maquinarias"], 0.10)
        self.assertEqual(pesos["insumos"], 0.10)
        self.assertEqual(pesos["calidad_servicio"], 0.20)

    def test_un_hallazgo_anula_el_item_binario_entero(self):
        """Documentación y Ley 19587 no admiten cumplimiento parcial: un solo
        hallazgo se lleva el ítem completo (PET cláusula 7, ítems 1 y 2)."""
        self.assertEqual(calc.item_binario(0), 1.0)
        self.assertEqual(calc.item_binario(1), 0.0)
        self.assertEqual(calc.item_binario(9), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
