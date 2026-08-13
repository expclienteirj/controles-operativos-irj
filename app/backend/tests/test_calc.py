"""Tests del motor de cálculo. Ejecutar: python3 -m unittest discover -s app/backend"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calc  # noqa: E402


# ---------------------------------------------------------------------------
# Sección 0 — lógica por excepción
# ---------------------------------------------------------------------------

class TestLogicaPorExcepcion(unittest.TestCase):
    ITEMS = ["vidriera", "piso", "mostrador", "papeleros"]

    def test_sector_confirmado_sin_desvios_es_100(self):
        self.assertEqual(calc.sector_limpieza(self.ITEMS, {}, confirmado=True), 1.0)

    def test_sector_sin_confirmar_es_sin_datos_no_100(self):
        """El riesgo central del diseño por excepción: no verificar != todo OK."""
        self.assertIsNone(calc.sector_limpieza(self.ITEMS, {}, confirmado=False))

    def test_desvio_parcial_vale_medio_punto(self):
        r = calc.sector_limpieza(self.ITEMS, {"piso": "DESVIO_PARCIAL"}, True)
        self.assertAlmostEqual(r, (1 + 0.5 + 1 + 1) / 4)

    def test_desvio_total_vale_cero(self):
        r = calc.sector_limpieza(self.ITEMS, {"piso": "DESVIO_TOTAL"}, True)
        self.assertAlmostEqual(r, 0.75)

    def test_no_verificable_se_excluye_del_denominador(self):
        r = calc.sector_limpieza(self.ITEMS, {"piso": calc.NO_VERIFICABLE}, True)
        self.assertAlmostEqual(r, 1.0)  # 3 ítems, todos cumplen

    def test_no_verificable_no_infla_el_resultado(self):
        r = calc.sector_limpieza(
            self.ITEMS, {"piso": calc.NO_VERIFICABLE, "vidriera": "DESVIO_TOTAL"}, True)
        self.assertAlmostEqual(r, 2 / 3)

    def test_todos_no_verificables_es_sin_datos(self):
        desvios = {i: calc.NO_VERIFICABLE for i in self.ITEMS}
        self.assertIsNone(calc.sector_limpieza(self.ITEMS, desvios, True))

    def test_promedio_ignora_sin_datos(self):
        self.assertAlmostEqual(calc.sector_mensual([1.0, 0.5, None, None]), 0.75)
        self.assertIsNone(calc.sector_mensual([None, None]))

    def test_estado_general_promedia_sectores(self):
        self.assertAlmostEqual(
            calc.estado_general_limpieza([1.0, 0.8, 0.6, None]), 0.8)


class TestPeriodicidadDiaria(unittest.TestCase):
    def test_dias_del_mes(self):
        self.assertEqual(len(calc.dias_del_mes("2026-07")), 31)
        self.assertEqual(len(calc.dias_del_mes("2026-06")), 30)
        self.assertEqual(len(calc.dias_del_mes("2026-02")), 28)
        self.assertEqual(len(calc.dias_del_mes("2028-02")), 29)   # bisiesto
        self.assertEqual(calc.dias_del_mes("2026-07")[0], "2026-07-01")
        self.assertEqual(calc.dias_del_mes("2026-07")[-1], "2026-07-31")

    def test_mes_completo(self):
        r = calc.completitud_mes(calc.dias_del_mes("2026-06"), "2026-06")
        self.assertTrue(r["completo"])
        self.assertEqual(r["cobertura"], 1.0)
        self.assertTrue(r["cobertura_suficiente"])
        self.assertEqual(r["dias_faltantes"], [])

    def test_mes_vacio(self):
        r = calc.completitud_mes([], "2026-06")
        self.assertEqual(r["cobertura"], 0.0)
        self.assertFalse(r["cobertura_suficiente"])
        self.assertEqual(len(r["dias_faltantes"]), 30)

    def test_cobertura_parcial(self):
        dias = calc.dias_del_mes("2026-06")[:24]      # 24 de 30
        r = calc.completitud_mes(dias, "2026-06")
        self.assertAlmostEqual(r["cobertura"], 0.8)
        self.assertTrue(r["cobertura_suficiente"])    # justo en el mínimo

    def test_cobertura_por_debajo_del_minimo(self):
        dias = calc.dias_del_mes("2026-06")[:20]      # 20 de 30 = 66,7%
        r = calc.completitud_mes(dias, "2026-06")
        self.assertFalse(r["cobertura_suficiente"])

    def test_cobertura_minima_configurable(self):
        dias = calc.dias_del_mes("2026-06")[:15]      # 50%
        self.assertFalse(calc.completitud_mes(dias, "2026-06")["cobertura_suficiente"])
        self.assertTrue(calc.completitud_mes(
            dias, "2026-06", cobertura_minima=0.5)["cobertura_suficiente"])

    def test_dias_futuros_no_son_faltantes_vencidos(self):
        """A mitad de mes, los días que no llegaron no son un incumplimiento."""
        r = calc.completitud_mes(["2026-06-01", "2026-06-02"], "2026-06",
                                 hasta="2026-06-05")
        self.assertEqual(r["dias_vencidos_sin_control"], ["2026-06-03", "2026-06-04"])
        self.assertEqual(len(r["dias_faltantes"]), 28)

    def test_ignora_dias_de_otro_mes(self):
        r = calc.completitud_mes(["2026-06-01", "2026-05-30"], "2026-06")
        self.assertEqual(r["dias_auditados"], ["2026-06-01"])

    def test_no_cuenta_duplicados(self):
        r = calc.completitud_mes(["2026-06-01", "2026-06-01"], "2026-06")
        self.assertEqual(r["dias_auditados"], ["2026-06-01"])

    def test_dias_sin_auditar_no_penalizan_el_promedio(self):
        """Decisión de diseño: no medir no es medir mal.

        La protección contra un mes poco representativo es la cobertura,
        no un castigo silencioso en el número."""
        self.assertEqual(calc.sector_mensual([0.9, None, None, None]), 0.9)
        r = calc.completitud_mes(["2026-06-01"], "2026-06")
        self.assertFalse(r["cobertura_suficiente"])


class TestEquipamiento(unittest.TestCase):
    def test_sin_faltantes_es_100(self):
        self.assertEqual(calc.cumplimiento_equipamiento(6, 0), 1.0)

    def test_dos_faltantes_de_seis(self):
        self.assertAlmostEqual(calc.cumplimiento_equipamiento(6, 2), 4 / 6)

    def test_sin_inventario_es_sin_datos(self):
        self.assertIsNone(calc.cumplimiento_equipamiento(0, 0))


# ---------------------------------------------------------------------------
# 2.3 — Certificación mensual
# ---------------------------------------------------------------------------

class TestCertificacion(unittest.TestCase):
    def test_items_binarios(self):
        self.assertEqual(calc.item_binario(0), 1.0)
        self.assertEqual(calc.item_binario(1), 0.0)   # un solo hallazgo anula el ítem
        self.assertEqual(calc.item_binario(5), 0.0)

    def test_programacion_horas_hombre(self):
        self.assertAlmostEqual(calc.item_programacion(1000, 50), 0.95)
        self.assertEqual(calc.item_programacion(1000, 0), 1.0)

    def test_programacion_no_baja_de_cero(self):
        self.assertEqual(calc.item_programacion(100, 500), 0.0)

    def test_programacion_sin_plan_cargado(self):
        self.assertIsNone(calc.item_programacion(0, 10))

    def test_maquinarias_promedia_por_equipo(self):
        """6 equipos exigidos, uno de baja 5 de 25 días: solo ese equipo baja
        su disponibilidad, y el promedio la reparte entre los 6."""
        self.assertAlmostEqual(calc.item_maquinarias(25, [5, 0, 0, 0, 0, 0]),
                               (5 * 1.0 + (20 / 25)) / 6)

    def test_maquinarias_todos_disponibles(self):
        self.assertEqual(calc.item_maquinarias(30, [0] * 6), 1.0)

    def test_maquinarias_sin_equipos_configurados(self):
        self.assertIsNone(calc.item_maquinarias(30, []))

    def test_maquinarias_sin_periodo_medible(self):
        """Sin días sobre los que medir no hay nada que promediar: Sin datos."""
        self.assertIsNone(calc.item_maquinarias(0, [0, 0]))

    def test_maquinarias_una_baja_corta_pesa_menos_que_una_larga(self):
        un_dia = calc.item_maquinarias(25, [1] + [0] * 5)
        todo_el_mes = calc.item_maquinarias(25, [25] + [0] * 5)
        self.assertGreater(un_dia, todo_el_mes)
        self.assertAlmostEqual(todo_el_mes, 5 / 6)

    def test_maquinarias_descuenta_por_dia_equipo(self):
        """El descuento es proporcional al total de días-equipo perdidos: una
        máquina de baja todo el mes pesa igual que seis de baja cinco días.
        Está bien que así sea —es la misma cantidad de servicio no prestado—
        y se deja fijado para que un cambio de fórmula no pase inadvertido."""
        una_larga = calc.item_maquinarias(30, [30, 0, 0, 0, 0, 0])
        muchas_cortas = calc.item_maquinarias(30, [5] * 6)
        self.assertAlmostEqual(una_larga, muchas_cortas)

    def test_maquinarias_baja_mas_larga_que_el_periodo_no_pasa_de_cero(self):
        self.assertEqual(calc.item_maquinarias(30, [45]), 0.0)

    def test_insumos(self):
        insumos = [{"stock": 10, "punto_pedido": 5},
                   {"stock": 5, "punto_pedido": 5},    # igual al punto de pedido: OK
                   {"stock": 2, "punto_pedido": 5},
                   {"stock": 0, "punto_pedido": 1}]
        self.assertAlmostEqual(calc.item_insumos(insumos), 0.5)

    def test_calidad_se_alimenta_del_checklist_y_descuenta_nc(self):
        self.assertAlmostEqual(calc.item_calidad_servicio(0.95, nc=0), 0.95)
        self.assertAlmostEqual(calc.item_calidad_servicio(0.95, nc=3), 0.92)

    def test_sin_tope_la_penalizacion_sigue_creciendo(self):
        """El default: cada NC descuenta, sin techo.

        Con tope, en cuanto se lo alcanza la penalización deja de distinguir un
        mes de otro. Como cada desvío genera una NC, cualquier mes real lo
        alcanza, así que el tope se volvía un descuento fijo disfrazado.
        """
        self.assertAlmostEqual(calc.item_calidad_servicio(1.0, nc=30), 0.70)
        self.assertAlmostEqual(calc.item_calidad_servicio(1.0, nc=78), 0.22)
        # 30 y 78 NC no pueden dar lo mismo: era exactamente el problema.
        self.assertNotAlmostEqual(calc.item_calidad_servicio(1.0, nc=30),
                                  calc.item_calidad_servicio(1.0, nc=78))

    def test_el_tope_rige_solo_si_se_lo_pasa(self):
        self.assertAlmostEqual(
            calc.item_calidad_servicio(1.0, nc=100, tope=0.20), 0.80)
        self.assertAlmostEqual(calc.descuento_por_nc(100, 0.01, tope=0.20), 0.20)
        self.assertAlmostEqual(calc.descuento_por_nc(100, 0.01, tope=None), 1.0)

    def test_el_descuento_nunca_pasa_del_100(self):
        """Sin tope hay que acotar igual: descontar 300% no significa nada."""
        self.assertAlmostEqual(calc.descuento_por_nc(300, 0.01), 1.0)
        self.assertAlmostEqual(calc.item_calidad_servicio(0.9, nc=300), 0.0)

    def test_certificacion_todo_perfecto_es_100(self):
        items = {k: 1.0 for k in calc.PESOS_CERTIFICACION_DEFAULT}
        self.assertAlmostEqual(calc.certificacion_mensual(items)["porcentaje"], 1.0)

    def test_certificacion_ponderada(self):
        items = {"documentacion": 0.0,        # 10%
                 "ley_19587": 1.0,            # 10%
                 "programacion_trabajos": 0.95,  # 40%
                 "maquinarias": 0.90,         # 10%
                 "insumos": 0.80,             # 10%
                 "calidad_servicio": 0.92}    # 20%
        esperado = (0.0 * .10 + 1.0 * .10 + 0.95 * .40 +
                    0.90 * .10 + 0.80 * .10 + 0.92 * .20)
        self.assertAlmostEqual(
            calc.certificacion_mensual(items)["porcentaje"], esperado)

    def test_pesos_default_suman_uno(self):
        self.assertAlmostEqual(sum(calc.PESOS_CERTIFICACION_DEFAULT.values()), 1.0)

    def test_item_sin_datos_redistribuye_peso(self):
        """Sin horas máquina cargadas, el 10% se reparte entre los demás."""
        items = {"documentacion": 1.0, "ley_19587": 1.0,
                 "programacion_trabajos": 0.50, "maquinarias": None,
                 "insumos": 1.0, "calidad_servicio": 1.0}
        r = calc.certificacion_mensual(items)
        self.assertEqual(r["items_sin_datos"], ["maquinarias"])
        self.assertAlmostEqual(r["peso_evaluado"], 0.90)
        self.assertAlmostEqual(r["porcentaje"], (0.5 * .40 + .10 + .10 + .10 + .20) / .90)

    def test_certificacion_sin_ningun_dato(self):
        items = {k: None for k in calc.PESOS_CERTIFICACION_DEFAULT}
        self.assertIsNone(calc.certificacion_mensual(items)["porcentaje"])

    def test_pesos_editables_por_admin(self):
        pesos = dict(calc.PESOS_CERTIFICACION_DEFAULT,
                     programacion_trabajos=0.30, calidad_servicio=0.30)
        items = {k: 1.0 for k in pesos}
        items["calidad_servicio"] = 0.0
        self.assertAlmostEqual(calc.certificacion_mensual(items, pesos)["porcentaje"], 0.70)

    def test_importe_a_certificar(self):
        self.assertEqual(calc.importe_a_certificar(0.95, 1_000_000), 950_000.0)
        self.assertIsNone(calc.importe_a_certificar(0.95, None))

    def test_certificacion_bloquea_si_falta_documentacion(self):
        """Requisito contractual: documentacion es obligatoria."""
        items = {"documentacion": None, "ley_19587": 1.0,
                 "programacion_trabajos": 1.0, "maquinarias": 1.0,
                 "insumos": 1.0, "calidad_servicio": 1.0}
        r = calc.certificacion_mensual(items)
        self.assertIsNone(r["porcentaje"])
        self.assertIn("documentacion", r.get("items_obligatorios_faltantes", []))

    def test_certificacion_bloquea_si_falta_ley_19587(self):
        """Requisito contractual: ley_19587 es obligatoria."""
        items = {"documentacion": 1.0, "ley_19587": None,
                 "programacion_trabajos": 1.0, "maquinarias": 1.0,
                 "insumos": 1.0, "calidad_servicio": 1.0}
        r = calc.certificacion_mensual(items)
        self.assertIsNone(r["porcentaje"])
        self.assertIn("ley_19587", r.get("items_obligatorios_faltantes", []))

    def test_certificacion_bloquea_si_falta_programacion_trabajos(self):
        """Requisito contractual: programacion_trabajos es obligatoria."""
        items = {"documentacion": 1.0, "ley_19587": 1.0,
                 "programacion_trabajos": None, "maquinarias": 1.0,
                 "insumos": 1.0, "calidad_servicio": 1.0}
        r = calc.certificacion_mensual(items)
        self.assertIsNone(r["porcentaje"])
        self.assertIn("programacion_trabajos", r.get("items_obligatorios_faltantes", []))


# ---------------------------------------------------------------------------
# 3.1 — Baños
# ---------------------------------------------------------------------------

class TestBanosEnServicio(unittest.TestCase):
    def test_damas_80_por_ciento_justo_cumple(self):
        r = calc.banos_en_servicio({"nombre": "Damas Hall", "tipo": "DAMAS",
            "artefactos": {"inodoros": {"instalados": 5, "fuera_servicio": 1}}})
        self.assertAlmostEqual(r["grupos"]["inodoros"]["porcentaje"], 0.80)
        self.assertTrue(r["cumple"])

    def test_damas_bajo_umbral_no_cumple(self):
        r = calc.banos_en_servicio({"nombre": "Damas Hall", "tipo": "DAMAS",
            "artefactos": {"inodoros": {"instalados": 5, "fuera_servicio": 2}}})
        self.assertFalse(r["cumple"])

    def test_pmr_exige_100_un_artefacto_fuera_ya_incumple(self):
        r = calc.banos_en_servicio({"nombre": "PMR", "tipo": "PMR",
            "artefactos": {"inodoros": {"instalados": 1, "fuera_servicio": 1},
                           "bachas": {"instalados": 1, "fuera_servicio": 0}}})
        self.assertFalse(r["cumple"])

    def test_pmr_todo_en_servicio_cumple(self):
        r = calc.banos_en_servicio({"nombre": "PMR", "tipo": "PMR",
            "artefactos": {"inodoros": {"instalados": 1, "fuera_servicio": 0},
                           "bachas": {"instalados": 1, "fuera_servicio": 0}}})
        self.assertTrue(r["cumple"])

    def test_un_grupo_por_debajo_arrastra_el_nucleo(self):
        r = calc.banos_en_servicio({"nombre": "Caballeros", "tipo": "CABALLEROS",
            "artefactos": {"inodoros": {"instalados": 4, "fuera_servicio": 0},
                           "mingitorios": {"instalados": 4, "fuera_servicio": 2},
                           "bachas": {"instalados": 4, "fuera_servicio": 0}}})
        self.assertFalse(r["cumple"])
        self.assertTrue(r["grupos"]["inodoros"]["cumple"])

    def test_sin_inventario_es_sin_datos(self):
        r = calc.banos_en_servicio({"nombre": "X", "tipo": "DAMAS",
            "artefactos": {"inodoros": {"instalados": 0, "fuera_servicio": 0}}})
        self.assertIsNone(r["cumple"])


class TestLimpiezaBanos(unittest.TestCase):
    def test_cinco_bachas_admiten_una_con_hallazgo(self):
        """Caso textual del pliego: con 5 bachas se admite solo 1 fuera de estándar."""
        r = calc.limpieza_banos({"sector": "BACHAS", "equipos": {
            "bachas": {"total": 5, "con_hallazgo": 1, "objetivo": 0.80}}})
        self.assertTrue(r["equipos"]["bachas"]["cumple"])

    def test_cinco_bachas_con_dos_hallazgos_incumple(self):
        r = calc.limpieza_banos({"sector": "BACHAS", "equipos": {
            "bachas": {"total": 5, "con_hallazgo": 2, "objetivo": 0.80}}})
        self.assertFalse(r["equipos"]["bachas"]["cumple"])

    def test_espejos_y_pisos_exigen_100_dentro_del_sector_bachas(self):
        r = calc.limpieza_banos({"sector": "BACHAS", "equipos": {
            "bachas": {"total": 5, "con_hallazgo": 1, "objetivo": 0.80},
            "espejos": {"total": 5, "con_hallazgo": 1, "objetivo": 1.00}}})
        self.assertTrue(r["equipos"]["bachas"]["cumple"])
        self.assertFalse(r["equipos"]["espejos"]["cumple"])
        self.assertFalse(r["cumple"])

    def test_sector_sin_hallazgos_es_cumple(self):
        r = calc.limpieza_banos({"sector": "MINGITORIOS", "equipos": {
            "mingitorios": {"total": 3, "con_hallazgo": 0, "objetivo": 0.80}}})
        self.assertTrue(r["cumple"])

    def test_pmr_no_admite_ningun_hallazgo(self):
        r = calc.limpieza_banos({"sector": "PMR", "equipos": {
            "cestos": {"total": 2, "con_hallazgo": 1, "objetivo": 1.00}}})
        self.assertFalse(r["cumple"])

    def test_recinto_bebes_no_admite_ningun_hallazgo(self):
        r = calc.limpieza_banos({"sector": "RECINTO_BEBES", "equipos": {
            "cambiador": {"total": 1, "con_hallazgo": 1, "objetivo": 1.00}}})
        self.assertFalse(r["cumple"])


# ---------------------------------------------------------------------------
# 3.2 — Confort térmico
# ---------------------------------------------------------------------------

class TestConfortTermico(unittest.TestCase):
    def test_verano_categoria_b_dentro_de_rango(self):
        r = calc.confort_termico(24.5, "VERANO")
        self.assertTrue(r["cumple"])
        self.assertEqual(r["categoria"], "B")

    def test_verano_limites_inclusivos(self):
        self.assertTrue(calc.confort_termico(23.0, "VERANO")["cumple"])
        self.assertTrue(calc.confort_termico(26.0, "VERANO")["cumple"])

    def test_verano_fuera_de_rango(self):
        self.assertFalse(calc.confort_termico(26.1, "VERANO")["cumple"])
        self.assertFalse(calc.confort_termico(22.9, "VERANO")["cumple"])

    def test_invierno_categoria_c_rango_mas_amplio(self):
        r = calc.confort_termico(19.5, "INVIERNO")
        self.assertTrue(r["cumple"])
        self.assertEqual(r["categoria"], "C")
        # 19.5 sería incumplimiento en verano
        self.assertFalse(calc.confort_termico(19.5, "VERANO")["cumple"])

    def test_invierno_fuera_de_rango(self):
        self.assertFalse(calc.confort_termico(18.5, "INVIERNO")["cumple"])
        self.assertFalse(calc.confort_termico(25.5, "INVIERNO")["cumple"])

    def test_velocidad_de_aire_excesiva_incumple(self):
        r = calc.confort_termico(24.5, "VERANO", velocidad_aire=0.25)
        self.assertTrue(r["temperatura_ok"])
        self.assertFalse(r["cumple"])

    def test_velocidad_de_aire_en_el_limite(self):
        self.assertTrue(calc.confort_termico(24.5, "VERANO", 0.19)["cumple"])

    def test_sin_medicion_es_sin_datos(self):
        self.assertIsNone(calc.confort_termico(None, "VERANO")["cumple"])


# ---------------------------------------------------------------------------
# 3.3 — Iluminación
# ---------------------------------------------------------------------------

class TestIluminacion(unittest.TestCase):
    def test_sin_quemadas_es_100(self):
        r = calc.iluminacion_sector(50, 0)
        self.assertEqual(r["porcentaje"], 1.0)
        self.assertTrue(r["cumple"])

    def test_noventa_por_ciento_justo_cumple(self):
        r = calc.iluminacion_sector(50, 5)
        self.assertAlmostEqual(r["porcentaje"], 0.90)
        self.assertTrue(r["cumple"])

    def test_bajo_noventa_incumple(self):
        self.assertFalse(calc.iluminacion_sector(50, 6)["cumple"])

    def test_consecutivas_en_el_mismo_cono_incumplen_pese_a_superar_el_umbral(self):
        r = calc.iluminacion_sector(100, 2, consecutivas_mismo_cono=True)
        self.assertAlmostEqual(r["porcentaje"], 0.98)
        self.assertTrue(r["umbral_ok"])
        self.assertFalse(r["cumple"])
        self.assertIn("cono de luz", r["motivo"])

    def test_sin_inventario_es_sin_datos(self):
        self.assertIsNone(calc.iluminacion_sector(0, 0)["cumple"])


# ---------------------------------------------------------------------------
# 3.4 — Infraestructura
# ---------------------------------------------------------------------------

class TestInfraestructura(unittest.TestCase):
    def test_a_y_b_cumplen_c_y_d_no(self):
        self.assertTrue(calc.evaluar_grado("A")["cumple"])
        self.assertTrue(calc.evaluar_grado("B")["cumple"])
        self.assertFalse(calc.evaluar_grado("C")["cumple"])
        self.assertFalse(calc.evaluar_grado("D")["cumple"])

    def test_prioridad_de_no_conformidad(self):
        self.assertEqual(calc.evaluar_grado("C")["prioridad_nc"], "PROGRAMADA")
        self.assertEqual(calc.evaluar_grado("D")["prioridad_nc"], "INMEDIATA")
        self.assertIsNone(calc.evaluar_grado("A")["prioridad_nc"])

    def test_grado_invalido_falla(self):
        with self.assertRaises(ValueError):
            calc.evaluar_grado("E")

    def test_default_por_excepcion_todo_cumple(self):
        r = calc.infraestructura({"cielorraso": "A", "vidrios": "A", "puertas_frenos": "B"})
        self.assertTrue(r["cumple"])
        self.assertEqual(r["no_conformidades"], [])

    def test_un_subitem_degradado_genera_nc(self):
        r = calc.infraestructura({"cielorraso": "A", "alfombras_manchas": "D"})
        self.assertFalse(r["cumple"])
        self.assertEqual(len(r["no_conformidades"]), 1)
        self.assertEqual(r["no_conformidades"][0]["prioridad"], "INMEDIATA")


# ---------------------------------------------------------------------------
# 3.5 / 3.6 — Asientos y puntos de carga
# ---------------------------------------------------------------------------

class TestAsientos(unittest.TestCase):
    def test_minimo_irj_es_38(self):
        self.assertEqual(calc.ASIENTOS_MINIMOS_IRJ, 38)

    def test_justo_en_el_minimo_cumple(self):
        self.assertTrue(calc.asientos_preembarque(40, inutilizables=2)["cumple"])

    def test_uno_menos_del_minimo_incumple(self):
        self.assertFalse(calc.asientos_preembarque(40, inutilizables=3)["cumple"])

    def test_sin_reportes_usa_el_total_instalado(self):
        r = calc.asientos_preembarque(60)
        self.assertEqual(r["utilizables"], 60)
        self.assertTrue(r["cumple"])

    def test_sin_inventario_es_sin_datos(self):
        self.assertIsNone(calc.asientos_preembarque(0)["cumple"])


class TestPuntosDeCarga(unittest.TestCase):
    def test_requeridas_redondea_hacia_arriba(self):
        self.assertEqual(calc.tomas_requeridas(100), 25)
        self.assertEqual(calc.tomas_requeridas(76), 19)    # 19.0
        self.assertEqual(calc.tomas_requeridas(77), 20)    # 19.25 -> 20

    def test_puerta_cumple(self):
        r = calc.puntos_de_carga({"nombre": "Puerta 1", "php": 76, "instaladas": 20})
        self.assertEqual(r["requeridas"], 19)
        self.assertTrue(r["cumple"])

    def test_tomas_fuera_de_servicio_pueden_hacer_incumplir(self):
        r = calc.puntos_de_carga({"nombre": "Puerta 1", "php": 76,
                                  "instaladas": 20, "fuera_servicio": 2})
        self.assertEqual(r["operativas"], 18)
        self.assertFalse(r["cumple"])

    def test_sin_inventario_es_sin_datos(self):
        self.assertIsNone(calc.puntos_de_carga({"nombre": "P1", "php": 76})["cumple"])


# ---------------------------------------------------------------------------
# 3.7 — Medios de elevación
# ---------------------------------------------------------------------------

class TestMediosElevacion(unittest.TestCase):
    def test_base_horaria_es_calendario(self):
        self.assertEqual(calc.horas_base_mes(30), 720.0)   # 30 días x 24 hs

    def test_topes_horarios_y_disponibilidad_son_coherentes(self):
        """Con base calendario, 60 hs equivalen exactamente al 91,66% exigido.
        Con base 14 hs (horario operativo) los dos criterios se contradicen."""
        base = calc.horas_base_mes(30)
        p = calc.ELEVACION_IRJ["CON_REDUNDANCIA"]
        self.assertAlmostEqual(
            # el manual publica 0,9166 truncado; el cociente exacto es 0,91666…
            (base - p["indisp_max_mensual_hs"]) / base, p["disponibilidad_min"], places=3)

    def test_sin_eventos_es_100(self):
        r = calc.medio_elevacion({"nombre": "Ascensor 1", "redundancia": False}, 30)
        self.assertEqual(r["disponibilidad"], 1.0)
        self.assertTrue(r["cumple"])

    def test_sin_redundancia_dentro_de_umbral(self):
        r = calc.medio_elevacion(
            {"nombre": "A1", "redundancia": False, "eventos": [{"horas": 24}]}, 30)
        self.assertAlmostEqual(r["disponibilidad"], (720 - 24) / 720)
        self.assertTrue(r["cumple"])

    def test_base_horaria_configurable(self):
        """Si el operador decide medir sobre el horario operativo, el mismo
        evento pesa mucho más y puede hacer incumplir."""
        r = calc.medio_elevacion(
            {"nombre": "A1", "redundancia": False, "eventos": [{"horas": 40}]},
            30, horas_operativas_dia=14.0)
        self.assertAlmostEqual(r["disponibilidad"], (420 - 40) / 420)
        self.assertFalse(r["cumple"])

    def test_sin_redundancia_supera_48hs_mensuales(self):
        r = calc.medio_elevacion(
            {"nombre": "A1", "redundancia": False,
             "eventos": [{"horas": 30}, {"horas": 25}]}, 30)
        self.assertFalse(r["mensual_ok"])
        self.assertFalse(r["cumple"])

    def test_con_redundancia_tolera_hasta_60hs(self):
        r = calc.medio_elevacion(
            {"nombre": "A2", "redundancia": True,
             "eventos": [{"horas": 30}, {"horas": 25}]}, 30)
        self.assertTrue(r["mensual_ok"])
        self.assertTrue(r["cumple"])

    def test_evento_unico_mayor_a_48hs_incumple(self):
        """Con redundancia el total (55hs) entra en el tope mensual de 60,
        pero un solo evento de 55 hs supera el máximo de 48 hs por evento."""
        r = calc.medio_elevacion(
            {"nombre": "A2", "redundancia": True, "eventos": [{"horas": 55}]}, 30)
        self.assertTrue(r["mensual_ok"])
        self.assertFalse(r["evento_ok"])
        self.assertFalse(r["cumple"])

    def test_umbrales_de_disponibilidad_irj(self):
        self.assertEqual(calc.ELEVACION_IRJ["CON_REDUNDANCIA"]["disponibilidad_min"], 0.9166)
        self.assertEqual(calc.ELEVACION_IRJ["SIN_REDUNDANCIA"]["disponibilidad_min"], 0.93)


# ---------------------------------------------------------------------------
# 3.8 — Limpieza de terminal
# ---------------------------------------------------------------------------

class TestLimpiezaTerminal(unittest.TestCase):
    def test_umbrales_de_llenado(self):
        self.assertEqual(calc.grado_por_llenado(0), "A")
        self.assertEqual(calc.grado_por_llenado(50), "A")
        self.assertEqual(calc.grado_por_llenado(51), "B")
        self.assertEqual(calc.grado_por_llenado(65), "B")
        self.assertEqual(calc.grado_por_llenado(66), "C")
        self.assertEqual(calc.grado_por_llenado(80), "C")
        self.assertEqual(calc.grado_por_llenado(81), "D")
        self.assertEqual(calc.grado_por_llenado(100), "D")

    def test_a_y_b_cumplen(self):
        r = calc.limpieza_terminal({
            "contenedores": calc.grado_por_llenado(45),
            "cestos_interiores": calc.grado_por_llenado(60),
            "telaranias": "A", "vidrios": "B", "corredores": "A"})
        self.assertTrue(r["cumple"])

    def test_cesto_al_85_por_ciento_incumple(self):
        r = calc.limpieza_terminal({"cestos_interiores": calc.grado_por_llenado(85)})
        self.assertFalse(r["cumple"])
        self.assertEqual(r["no_conformidades"][0]["prioridad"], "INMEDIATA")


# ---------------------------------------------------------------------------
# 3.9 — GEL
# ---------------------------------------------------------------------------

class TestGEL(unittest.TestCase):
    def test_dentro_del_maximo(self):
        r = calc.prueba_gel(12.0)
        self.assertEqual(r["tiempo_maximo_s"], 15)
        self.assertTrue(r["cumple"])

    def test_justo_en_el_maximo_cumple(self):
        self.assertTrue(calc.prueba_gel(15.0)["cumple"])

    def test_supera_el_maximo(self):
        self.assertFalse(calc.prueba_gel(15.5)["cumple"])

    def test_categoria_exigente(self):
        self.assertFalse(calc.prueba_gel(2.0, "APROX_PRECISION_CAT_II_III")["cumple"])

    def test_sin_medicion_es_sin_datos(self):
        self.assertIsNone(calc.prueba_gel(None)["cumple"])


# ---------------------------------------------------------------------------
# 3.10 — Pista y rodajes
# ---------------------------------------------------------------------------

class TestPistaYRodajes(unittest.TestCase):
    def test_disponibilidad_exige_cero_eventos_no_programados(self):
        self.assertTrue(calc.disponibilidad_pista(0)["cumple"])
        self.assertFalse(calc.disponibilidad_pista(1)["cumple"])

    def test_pista_85_por_ciento_sobre_pci_70(self):
        secciones = [{"id": f"P{i}", "pci": 80} for i in range(17)] + \
                    [{"id": "P18", "pci": 65}, {"id": "P19", "pci": 60},
                     {"id": "P20", "pci": 55}]
        r = calc.pci_secciones(secciones, "PISTA")
        self.assertAlmostEqual(r["proporcion"], 0.85)
        self.assertTrue(r["cumple"])

    def test_pista_bajo_85_incumple(self):
        secciones = [{"id": f"P{i}", "pci": 80} for i in range(8)] + \
                    [{"id": "P9", "pci": 50}, {"id": "P10", "pci": 50}]
        self.assertFalse(calc.pci_secciones(secciones, "PISTA")["cumple"])

    def test_pci_exactamente_en_el_umbral_no_cuenta(self):
        """El manual exige PCI > 70 (estricto), no >= 70."""
        r = calc.pci_secciones([{"id": "P1", "pci": 70}], "PISTA")
        self.assertEqual(r["secciones_sobre_umbral"], 0)

    def test_rodaje_umbral_mas_permisivo(self):
        secciones = [{"id": f"R{i}", "pci": 65} for i in range(7)] + \
                    [{"id": f"R{i}", "pci": 50} for i in range(7, 10)]
        r = calc.pci_secciones(secciones, "RODAJE")
        self.assertAlmostEqual(r["proporcion"], 0.70)
        self.assertTrue(r["cumple"])
        # los mismos valores no alcanzarían el criterio de pista
        self.assertFalse(calc.pci_secciones(secciones, "PISTA")["cumple"])

    def test_sin_secciones_cargadas_es_sin_datos(self):
        self.assertIsNone(calc.pci_secciones([], "PISTA")["cumple"])

    def test_escala_pci(self):
        self.assertEqual(calc.clasificar_pci(95), "Bueno")
        self.assertEqual(calc.clasificar_pci(78), "Satisfactorio")
        self.assertEqual(calc.clasificar_pci(60), "Razonable")
        self.assertEqual(calc.clasificar_pci(45), "Malo")
        self.assertEqual(calc.clasificar_pci(5), "Nefasto")


# ---------------------------------------------------------------------------
# 3.12 — Resultado global LoS
# ---------------------------------------------------------------------------

class TestResultadoLoS(unittest.TestCase):
    def _items(self):
        return [
            calc.ItemLoS("banos", "Baños", cumple=True, valor=0.9, objetivo=0.8),
            calc.ItemLoS("confort", "Confort térmico", cumple=True),
            calc.ItemLoS("iluminacion", "Iluminación", cumple=False, valor=0.85, objetivo=0.90),
            calc.ItemLoS("infraestructura", "Infraestructura", cumple=True),
            calc.ItemLoS("asientos", "Asientos preembarque", cumple=True),
            calc.ItemLoS("carga", "Puntos de carga", cumple=True),
            calc.ItemLoS("elevacion", "Medios de elevación", cumple=True),
            calc.ItemLoS("limpieza_terminal", "Limpieza de terminal", cumple=True),
            calc.ItemLoS("gel", "Grupos electrógenos", cumple=True),
            calc.ItemLoS("pista", "Pista y rodajes", cumple=True),
            calc.ItemLoS("pasarelas", "Pasarelas telescópicas", aplica=False),
        ]

    def test_pasarelas_no_aplican_en_irj(self):
        r = calc.resultado_los(self._items())
        self.assertEqual(r["no_aplica"], ["pasarelas"])
        self.assertEqual(r["items_aplicables"], 10)

    def test_cada_item_expone_si_aplica(self):
        """La UI decide con este campo si el ítem es relevable.

        Sin él, un `undefined` del lado del cliente se lee como falso y los 11
        ítems aparecen como 'No aplica'."""
        r = calc.resultado_los(self._items())
        for item in r["items"]:
            self.assertIn("aplica", item, item["clave"])
        aplica = {i["clave"]: i["aplica"] for i in r["items"]}
        self.assertFalse(aplica["pasarelas"])
        self.assertTrue(aplica["banos"])

    def test_porcentaje_global_excluye_no_aplica(self):
        r = calc.resultado_los(self._items())
        self.assertAlmostEqual(r["porcentaje"], 9 / 10)

    def test_sin_datos_no_cuenta_como_incumplimiento_pero_se_informa(self):
        items = self._items()
        items[1].cumple = None
        r = calc.resultado_los(items)
        self.assertIn("confort", r["items_sin_datos"])
        self.assertEqual(r["items_evaluados"], 9)
        self.assertAlmostEqual(r["porcentaje"], 8 / 9)

    def test_estados(self):
        self.assertEqual(calc.ItemLoS("x", "X", aplica=False).estado, "NO_APLICA")
        self.assertEqual(calc.ItemLoS("x", "X").estado, "SIN_DATOS")
        self.assertEqual(calc.ItemLoS("x", "X", cumple=True).estado, "CUMPLE")
        self.assertEqual(calc.ItemLoS("x", "X", cumple=False).estado, "NO_CUMPLE")

    def test_semaforo_amarillo_en_el_limite(self):
        item = calc.ItemLoS("ilu", "Iluminación", cumple=True, valor=0.90, objetivo=0.90)
        self.assertEqual(item.semaforo, "AMARILLO")

    def test_semaforo_amarillo_con_nc_abiertas(self):
        item = calc.ItemLoS("inf", "Infra", cumple=True, valor=1.0, objetivo=0.8, nc_abiertas=2)
        self.assertEqual(item.semaforo, "AMARILLO")

    def test_semaforo_verde_y_rojo(self):
        self.assertEqual(calc.ItemLoS("a", "A", cumple=True, valor=0.99,
                                      objetivo=0.80).semaforo, "VERDE")
        self.assertEqual(calc.ItemLoS("a", "A", cumple=False).semaforo, "ROJO")


# ---------------------------------------------------------------------------
# Integración: mes completo
# ---------------------------------------------------------------------------

class TestMesCompleto(unittest.TestCase):
    def test_mes_sin_desvios_certifica_100(self):
        sectores = {"embarque": ["vidriera", "piso"], "arribos": ["pisos", "lavabos"]}
        mensuales = [
            calc.sector_mensual([calc.sector_limpieza(items, {}, True) for _ in range(4)])
            for items in sectores.values()]
        general = calc.estado_general_limpieza(mensuales)
        self.assertEqual(general, 1.0)

        cert = calc.certificacion_mensual({
            "documentacion": calc.item_binario(0),
            "ley_19587": calc.item_binario(0),
            "programacion_trabajos": calc.item_programacion(1000, 0),
            "maquinarias": calc.item_maquinarias(6, [0, 0, 0]),
            "insumos": calc.item_insumos([{"stock": 10, "punto_pedido": 5}]),
            "calidad_servicio": calc.item_calidad_servicio(general, 0)})
        self.assertAlmostEqual(cert["porcentaje"], 1.0)

    def test_mes_con_desvios_propaga_hasta_el_importe(self):
        items = ["vidriera", "piso", "mostrador", "papeleros"]
        q = [calc.sector_limpieza(items, {"piso": "DESVIO_TOTAL"}, True),
             calc.sector_limpieza(items, {}, True),
             calc.sector_limpieza(items, {"vidriera": "DESVIO_PARCIAL"}, True),
             calc.sector_limpieza(items, {}, True)]
        general = calc.estado_general_limpieza([calc.sector_mensual(q)])
        self.assertAlmostEqual(general, (0.75 + 1.0 + 0.875 + 1.0) / 4)

        cert = calc.certificacion_mensual({
            "documentacion": calc.item_binario(0),
            "ley_19587": calc.item_binario(1),          # hallazgo: anula el ítem
            "programacion_trabajos": calc.item_programacion(1000, 80),
            "maquinarias": calc.item_maquinarias(6, [0, 1, 0]),
            "insumos": calc.item_insumos([{"stock": 1, "punto_pedido": 5},
                                          {"stock": 9, "punto_pedido": 5}]),
            "calidad_servicio": calc.item_calidad_servicio(general, nc=2)})
        self.assertLess(cert["porcentaje"], 1.0)
        self.assertEqual(
            calc.importe_a_certificar(cert["porcentaje"], 1_000_000),
            round(cert["porcentaje"] * 1_000_000, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
