"""Tests de la validación de configuración e inventario.

Estos valores alimentan el motor que produce el porcentaje a certificar: un
umbral mal cargado no rompe la app, produce un número plausible pero incorrecto.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calc         # noqa: E402
import validacion   # noqa: E402

Error = validacion.ErrorValidacion


class TestProporciones(unittest.TestCase):
    def test_acepta_fracciones_validas(self):
        self.assertEqual(validacion.validar("iluminacion_objetivo", 0.9), 0.9)
        self.assertEqual(validacion.validar("iluminacion_objetivo", 0), 0.0)
        self.assertEqual(validacion.validar("iluminacion_objetivo", 1), 1.0)

    def test_rechaza_porcentaje_cargado_como_entero(self):
        """El error de carga más probable: escribir 90 en vez de 0,90."""
        with self.assertRaises(Error) as ctx:
            validacion.validar("iluminacion_objetivo", 90)
        self.assertIn("0,90 es 90%", str(ctx.exception))

    def test_rechaza_negativos(self):
        with self.assertRaises(Error):
            validacion.validar("cobertura_minima_mes", -0.1)

    def test_rechaza_texto(self):
        with self.assertRaises(Error):
            validacion.validar("iluminacion_objetivo", "0.9")

    def test_rechaza_booleano_como_numero(self):
        with self.assertRaises(Error):
            validacion.validar("iluminacion_objetivo", True)


class TestPesosCertificacion(unittest.TestCase):
    def test_acepta_los_pesos_por_defecto(self):
        self.assertEqual(validacion.validar("pesos", calc.PESOS_CERTIFICACION_DEFAULT),
                         calc.PESOS_CERTIFICACION_DEFAULT)

    def test_acepta_una_redistribucion_que_cierra(self):
        pesos = dict(calc.PESOS_CERTIFICACION_DEFAULT,
                     programacion_trabajos=0.30, calidad_servicio=0.30)
        self.assertEqual(validacion.validar("pesos", pesos), pesos)

    def test_rechaza_pesos_que_no_suman_cien(self):
        """Si suman 90%, todo el mundo cobra 10% de menos sin que nadie lo note."""
        pesos = dict(calc.PESOS_CERTIFICACION_DEFAULT, calidad_servicio=0.10)
        with self.assertRaises(Error) as ctx:
            validacion.validar("pesos", pesos)
        self.assertIn("suman 90", str(ctx.exception))

    def test_rechaza_si_falta_un_item(self):
        pesos = dict(calc.PESOS_CERTIFICACION_DEFAULT)
        del pesos["insumos"]
        with self.assertRaises(Error) as ctx:
            validacion.validar("pesos", pesos)
        self.assertIn("faltan: insumos", str(ctx.exception))

    def test_rechaza_items_desconocidos(self):
        pesos = dict(calc.PESOS_CERTIFICACION_DEFAULT, item_inventado=0.0)
        with self.assertRaises(Error) as ctx:
            validacion.validar("pesos", pesos)
        self.assertIn("no reconocidos", str(ctx.exception))

    def test_rechaza_peso_negativo(self):
        pesos = dict(calc.PESOS_CERTIFICACION_DEFAULT,
                     insumos=-0.10, calidad_servicio=0.40)
        with self.assertRaises(Error):
            validacion.validar("pesos", pesos)


class TestConfortTermico(unittest.TestCase):
    def test_acepta_los_parametros_de_irj(self):
        self.assertEqual(validacion.validar("confort_termico", calc.CONFORT_IRJ),
                         calc.CONFORT_IRJ)

    def test_rechaza_rango_invertido(self):
        """Un rango invertido haría que ninguna medición cumpla nunca."""
        malo = {"VERANO": {"min": 26.0, "max": 23.0, "vel_aire_max": 0.19},
                "INVIERNO": calc.CONFORT_IRJ["INVIERNO"]}
        with self.assertRaises(Error) as ctx:
            validacion.validar("confort_termico", malo)
        self.assertIn("menor", str(ctx.exception))

    def test_rechaza_temperaturas_absurdas(self):
        malo = {"VERANO": {"min": 100.0, "max": 200.0, "vel_aire_max": 0.19},
                "INVIERNO": calc.CONFORT_IRJ["INVIERNO"]}
        with self.assertRaises(Error):
            validacion.validar("confort_termico", malo)

    def test_rechaza_si_falta_una_estacion(self):
        with self.assertRaises(Error):
            validacion.validar("confort_termico",
                               {"VERANO": calc.CONFORT_IRJ["VERANO"]})


class TestElevacion(unittest.TestCase):
    def test_acepta_los_umbrales_de_irj(self):
        self.assertEqual(validacion.validar("elevacion_umbrales", calc.ELEVACION_IRJ),
                         calc.ELEVACION_IRJ)

    def test_rechaza_horas_imposibles_en_un_mes(self):
        malo = {"CON_REDUNDANCIA": {"disponibilidad_min": 0.9,
                                    "indisp_max_mensual_hs": 1000},
                "SIN_REDUNDANCIA": calc.ELEVACION_IRJ["SIN_REDUNDANCIA"]}
        with self.assertRaises(Error):
            validacion.validar("elevacion_umbrales", malo)

    def test_rechaza_disponibilidad_mayor_a_uno(self):
        malo = {"CON_REDUNDANCIA": {"disponibilidad_min": 91.66,
                                    "indisp_max_mensual_hs": 60},
                "SIN_REDUNDANCIA": calc.ELEVACION_IRJ["SIN_REDUNDANCIA"]}
        with self.assertRaises(Error):
            validacion.validar("elevacion_umbrales", malo)


class TestOtrosParametros(unittest.TestCase):
    def test_pci(self):
        self.assertEqual(validacion.validar("pci_pista", calc.PCI_PISTA),
                         calc.PCI_PISTA)
        with self.assertRaises(Error):
            validacion.validar("pci_pista", {"umbral": 150, "proporcion_min": 0.85})

    def test_asientos_minimo_entero_positivo(self):
        self.assertEqual(validacion.validar("asientos_minimo", 38), 38)
        with self.assertRaises(Error):
            validacion.validar("asientos_minimo", 0)
        with self.assertRaises(Error):
            validacion.validar("asientos_minimo", 38.5)

    def test_horas(self):
        self.assertEqual(validacion.validar("horario_operativo_inicio", "7:00"), "07:00")
        with self.assertRaises(Error):
            validacion.validar("horario_operativo_inicio", "25:00")
        with self.assertRaises(Error):
            validacion.validar("horario_operativo_inicio", "mañana")

    def test_fechas_de_estacion(self):
        self.assertEqual(validacion.validar("inicio_verano", "10-1"), "10-01")
        with self.assertRaises(Error):
            validacion.validar("inicio_verano", "13-01")

    def test_booleanos(self):
        self.assertTrue(validacion.validar("pasarelas_aplica", True))
        with self.assertRaises(Error):
            validacion.validar("pasarelas_aplica", "si")

    def test_horas_operativas_dentro_del_dia(self):
        self.assertEqual(validacion.validar("horas_operativas_dia", 14), 14.0)
        with self.assertRaises(Error):
            validacion.validar("horas_operativas_dia", 30)

    def test_clave_sin_validador_pasa_sin_control(self):
        """Textos de ayuda y escalas de referencia no alimentan cálculos."""
        valor = {"A": "Satisfactorio"}
        self.assertEqual(validacion.validar("infraestructura_escala", valor), valor)


class TestInventario(unittest.TestCase):
    def test_nucleo_valido(self):
        datos = {"nombre": "Damas Hall", "tipo": "DAMAS",
                 "equipos": {"inodoros": 5, "bachas": 5}}
        self.assertEqual(validacion.validar_inventario("nucleos", datos), datos)

    def test_nucleo_sin_artefactos_se_rechaza(self):
        """Sin cantidades el núcleo no puede evaluarse: sería Sin datos para siempre."""
        with self.assertRaises(Error) as ctx:
            validacion.validar_inventario(
                "nucleos", {"nombre": "X", "tipo": "DAMAS", "equipos": {}})
        self.assertIn("al menos un artefacto", str(ctx.exception))

    def test_nucleo_con_todo_en_cero_se_rechaza(self):
        with self.assertRaises(Error):
            validacion.validar_inventario(
                "nucleos", {"nombre": "X", "tipo": "DAMAS",
                            "equipos": {"inodoros": 0, "bachas": 0}})

    def test_equipo_desconocido_se_rechaza(self):
        with self.assertRaises(Error) as ctx:
            validacion.validar_inventario(
                "nucleos", {"nombre": "X", "tipo": "DAMAS",
                            "equipos": {"bidet": 2}})
        self.assertIn("bidet", str(ctx.exception))

    def test_cantidad_negativa_se_rechaza(self):
        with self.assertRaises(Error):
            validacion.validar_inventario(
                "nucleos", {"nombre": "X", "tipo": "DAMAS",
                            "equipos": {"inodoros": -1}})

    def test_mingitorios_en_damas_se_rechaza(self):
        """Casi siempre es un error de carga y distorsionaría el % del núcleo."""
        with self.assertRaises(Error) as ctx:
            validacion.validar_inventario(
                "nucleos", {"nombre": "Damas", "tipo": "DAMAS",
                            "equipos": {"inodoros": 4, "mingitorios": 2}})
        self.assertIn("Damas", str(ctx.exception))

    def test_mingitorios_en_caballeros_es_valido(self):
        datos = {"nombre": "Caballeros", "tipo": "CABALLEROS",
                 "equipos": {"inodoros": 2, "mingitorios": 3}}
        self.assertEqual(validacion.validar_inventario("nucleos", datos), datos)

    def test_luminarias(self):
        self.assertTrue(validacion.validar_inventario(
            "luminarias", {"sector": "Hall", "cantidad": 40}))
        with self.assertRaises(Error):
            validacion.validar_inventario("luminarias", {"sector": "", "cantidad": 40})
        with self.assertRaises(Error):
            validacion.validar_inventario("luminarias", {"sector": "Hall", "cantidad": 0})

    def test_puerta_de_embarque(self):
        self.assertTrue(validacion.validar_inventario(
            "puertas", {"nombre": "P1", "php": 76, "instaladas": 20}))
        with self.assertRaises(Error):
            validacion.validar_inventario(
                "puertas", {"nombre": "P1", "php": 0, "instaladas": 20})

    def test_puerta_sin_tomas_es_valida(self):
        """Una puerta sin tomas instaladas es un incumplimiento real, no un
        error de carga: tiene que poder registrarse."""
        self.assertTrue(validacion.validar_inventario(
            "puertas", {"nombre": "P2", "php": 76, "instaladas": 0}))

    def test_seccion_de_pavimento(self):
        self.assertTrue(validacion.validar_inventario(
            "secciones", {"identificador": "P-01", "tipo": "PISTA"}))
        with self.assertRaises(Error) as ctx:
            validacion.validar_inventario(
                "secciones", {"identificador": "P-01", "tipo": "CALLE"})
        self.assertIn("PISTA", str(ctx.exception))

    def test_recurso_sin_validador_pasa(self):
        self.assertEqual(validacion.validar_inventario("otro", {"x": 1}), {"x": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestZonasConfort(unittest.TestCase):
    """Las zonas de medición térmica son configurables, no código."""

    def test_acepta_las_cuatro_zonas_de_irj(self):
        zonas = ["Hall público", "Arribos", "Embarque", "Bar"]
        self.assertEqual(validacion.validar("confort_zonas", zonas), zonas)

    def test_recorta_espacios(self):
        self.assertEqual(
            validacion.validar("confort_zonas", ["  Bar  ", "Embarque"]),
            ["Bar", "Embarque"])

    def test_rechaza_lista_vacia(self):
        """Sin zonas no habría dónde medir: el ítem quedaría inservible."""
        with self.assertRaises(Error):
            validacion.validar("confort_zonas", [])

    def test_rechaza_nombres_vacios(self):
        with self.assertRaises(Error):
            validacion.validar("confort_zonas", ["Bar", "   "])

    def test_rechaza_repetidas(self):
        """Dos zonas con el mismo nombre serían indistinguibles en el informe."""
        with self.assertRaises(Error) as ctx:
            validacion.validar("confort_zonas", ["Bar", "Embarque", "Bar"])
        self.assertIn("repetidos", str(ctx.exception))

    def test_rechaza_lo_que_no_es_lista(self):
        with self.assertRaises(Error):
            validacion.validar("confort_zonas", "Bar, Embarque")
