"""Tests de la capa de servicios (base de datos + motor de cálculo)."""

import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import basedeprueba  # noqa: E402
import services    # noqa: E402

PERIODO = "2026-07"


class Base(unittest.TestCase):
    def setUp(self):
        self.conn, _ = basedeprueba.nueva(admin_password="x")
        cur = self.conn.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('jperez','J. Pérez',?, 'auditor')", (db.hash_password("x"),))
        self.auditor = cur.lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _control(self, dia=1, turno="MANANA"):
        """Abre el control de un turno de un día del período de prueba."""
        fecha = f"{PERIODO}-{dia:02d}"
        cur = self.conn.execute(
            "INSERT INTO controles_limpieza (fecha, turno, periodo, auditor_id) "
            "VALUES (?,?,?,?)", (fecha, turno, PERIODO, self.auditor))
        self.conn.commit()
        return cur.lastrowid

    def _sectores(self):
        return {f["clave"]: f["id"] for f in self.conn.execute(
            "SELECT id, clave FROM sectores_limpieza")}

    def _item(self, sector_clave, pos=0):
        return [f["id"] for f in self.conn.execute(
            "SELECT i.id FROM items_limpieza i JOIN sectores_limpieza s "
            "ON s.id = i.sector_id WHERE s.clave = ? ORDER BY i.orden",
            (sector_clave,))][pos]

    def _confirmar_todos(self, control_id):
        for sid in self._sectores().values():
            services.confirmar_sector(self.conn, control_id, sid, self.auditor)

    def _datos_obligatorios(self, **extra):
        """Carga los tres ítems que el contrato exige sí o sí para certificar.

        Sin ellos `certificacion()` devuelve None a propósito —su peso no se
        redistribuye, es un requisito duro del pliego—, así que un test que mide
        otra cosa (penalización por NC, equipamiento, cobertura) necesita
        tenerlos cargados para llegar a un porcentaje.
        """
        datos = {"documentacion_verificada": 1, "ley_19587_verificada": 1,
                 "horas_hombre_programadas": 1000,
                 # Explícito: vacío ya no cuenta como "no se perdió ninguna".
                 "horas_hombre_perdidas": 0}
        datos.update(extra)
        self.conn.execute(
            "INSERT OR IGNORE INTO periodo_datos (periodo) VALUES (?)", (PERIODO,))
        self.conn.execute(
            f"UPDATE periodo_datos SET {', '.join(f'{c} = ?' for c in datos)} "
            "WHERE periodo = ?", (*datos.values(), PERIODO))
        self.conn.commit()


class TestControlLimpieza(Base):
    def test_control_nuevo_esta_todo_pendiente(self):
        est = services.estado_control(self.conn, self._control())
        self.assertIsNone(est["porcentaje_general"])
        self.assertEqual(len(est["sectores_pendientes"]), 9)
        self.assertTrue(all(s["porcentaje"] is None for s in est["sectores"]))

    def test_confirmar_todo_sin_desvios_da_100(self):
        c = self._control()
        self._confirmar_todos(c)
        est = services.estado_control(self.conn, c)
        self.assertEqual(est["porcentaje_general"], 1.0)
        self.assertEqual(est["sectores_pendientes"], [])

    def test_un_sector_sin_confirmar_no_cuenta_como_100(self):
        """El modo de falla central del diseño por excepción."""
        c = self._control()
        sectores = self._sectores()
        for clave, sid in sectores.items():
            if clave != "sanidad":
                services.confirmar_sector(self.conn, c, sid, self.auditor)
        est = services.estado_control(self.conn, c)
        self.assertEqual(est["sectores_pendientes"], ["sanidad"])
        sanidad = next(s for s in est["sectores"] if s["clave"] == "sanidad")
        self.assertIsNone(sanidad["porcentaje"])
        self.assertEqual(sanidad["estado"], "PENDIENTE")

    def test_desvio_descuenta_del_sector(self):
        c = self._control()
        self._confirmar_todos(c)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso con residuos", self.auditor)
        est = services.estado_control(self.conn, c)
        sanidad = next(s for s in est["sectores"] if s["clave"] == "sanidad")
        self.assertAlmostEqual(sanidad["porcentaje"], 5 / 6)   # 6 ítems, 1 en cero
        self.assertEqual(sanidad["estado"], "CON_DESVIOS")

    def test_desvio_parcial_descuenta_la_mitad(self):
        c = self._control()
        self._confirmar_todos(c)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_PARCIAL", "Piso apenas sucio", self.auditor)
        est = services.estado_control(self.conn, c)
        sanidad = next(s for s in est["sectores"] if s["clave"] == "sanidad")
        self.assertAlmostEqual(sanidad["porcentaje"], 5.5 / 6)

    def test_desconfirmar_devuelve_el_sector_a_sin_verificar(self):
        """Deshacer el atajo "TODO OK" no declara un incumplimiento:
        el sector vuelve a Sin datos, no a 0%."""
        c = self._control()
        sanidad = self._sectores()["sanidad"]
        services.confirmar_sector(self.conn, c, sanidad, self.auditor)
        services.desconfirmar_sector(self.conn, c, sanidad, self.auditor)

        est = services.estado_control(self.conn, c)
        s = next(x for x in est["sectores"] if x["clave"] == "sanidad")
        self.assertFalse(s["confirmado"])
        self.assertIsNone(s["porcentaje"])
        self.assertEqual(s["estado"], "PENDIENTE")
        self.assertIn("sanidad", est["sectores_pendientes"])

    def test_desconfirmar_no_borra_los_desvios_cargados(self):
        c = self._control()
        sanidad = self._sectores()["sanidad"]
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso sucio", self.auditor)
        services.confirmar_sector(self.conn, c, sanidad, self.auditor)
        services.desconfirmar_sector(self.conn, c, sanidad, self.auditor)

        n = self.conn.execute("SELECT COUNT(*) c FROM desvios").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_desconfirmar_queda_logueado(self):
        c = self._control()
        sanidad = self._sectores()["sanidad"]
        services.confirmar_sector(self.conn, c, sanidad, self.auditor)
        services.desconfirmar_sector(self.conn, c, sanidad, self.auditor)
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM auditoria_log WHERE accion = 'DESCONFIRMAR_SECTOR'"
        ).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_no_se_desconfirma_un_control_cerrado(self):
        c = self._control()
        self._confirmar_todos(c)
        services.cerrar_control(self.conn, c, self.auditor)
        with self.assertRaises(PermissionError):
            services.desconfirmar_sector(self.conn, c, self._sectores()["sanidad"],
                                         self.auditor)

    def test_desconfirmar_reabre_la_exigencia_de_cierre(self):
        """El atajo no puede convertirse en una vía para cerrar de más."""
        c = self._control()
        self._confirmar_todos(c)
        services.desconfirmar_sector(self.conn, c, self._sectores()["air_side"],
                                     self.auditor)
        with self.assertRaises(ValueError):
            services.cerrar_control(self.conn, c, self.auditor)

    def test_observacion_obligatoria(self):
        c = self._control()
        with self.assertRaises(ValueError):
            services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                      "DESVIO_TOTAL", "   ", self.auditor)

    def test_desvio_genera_no_conformidad(self):
        c = self._control()
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso con residuos", self.auditor)
        nc = self.conn.execute("SELECT * FROM no_conformidades").fetchall()
        self.assertEqual(len(nc), 1)
        self.assertEqual(nc[0]["prioridad"], "INMEDIATA")
        self.assertEqual(nc[0]["origen"], "LIMPIEZA")

    def test_desvio_parcial_genera_nc_programada(self):
        c = self._control()
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_PARCIAL", "Leve", self.auditor)
        nc = self.conn.execute("SELECT prioridad FROM no_conformidades").fetchone()
        self.assertEqual(nc["prioridad"], "PROGRAMADA")

    def test_no_verificable_no_genera_nc_ni_descuenta(self):
        c = self._control()
        self._confirmar_todos(c)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "NO_VERIFICABLE", "Sector cerrado por obra",
                                  self.auditor)
        est = services.estado_control(self.conn, c)
        sanidad = next(s for s in est["sectores"] if s["clave"] == "sanidad")
        self.assertEqual(sanidad["porcentaje"], 1.0)   # 4 ítems restantes, todos OK
        n = self.conn.execute("SELECT COUNT(*) c FROM no_conformidades").fetchone()["c"]
        self.assertEqual(n, 0)

    def test_corregir_un_desvio_no_duplica_la_nc(self):
        c = self._control()
        item = self._item("sanidad")
        services.registrar_desvio(self.conn, c, item, "DESVIO_TOTAL", "Mal", self.auditor)
        services.registrar_desvio(self.conn, c, item, "DESVIO_PARCIAL", "Menos mal",
                                  self.auditor)
        n = self.conn.execute("SELECT COUNT(*) c FROM no_conformidades").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_no_se_cierra_con_sectores_pendientes(self):
        c = self._control()
        with self.assertRaises(ValueError) as ctx:
            services.cerrar_control(self.conn, c, self.auditor)
        self.assertIn("Faltan confirmar", str(ctx.exception))

    def test_cierre_exitoso(self):
        c = self._control()
        self._confirmar_todos(c)
        est = services.cerrar_control(self.conn, c, self.auditor)
        self.assertEqual(est["porcentaje_general"], 1.0)
        fila = self.conn.execute(
            "SELECT estado, cerrado_en FROM controles_limpieza WHERE id = ?", (c,)).fetchone()
        self.assertEqual(fila["estado"], "CERRADO")
        self.assertIsNotNone(fila["cerrado_en"])

    def test_control_cerrado_es_inmutable(self):
        c = self._control()
        self._confirmar_todos(c)
        services.cerrar_control(self.conn, c, self.auditor)
        with self.assertRaises(PermissionError):
            services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                      "DESVIO_TOTAL", "Tarde", self.auditor)
        with self.assertRaises(PermissionError):
            services.confirmar_sector(self.conn, c, self._sectores()["sanidad"],
                                      self.auditor)

    def test_reabrir_exige_motivo_y_queda_logueado(self):
        c = self._control()
        self._confirmar_todos(c)
        services.cerrar_control(self.conn, c, self.auditor)
        with self.assertRaises(ValueError):
            services.reabrir_control(self.conn, c, self.auditor, "")
        services.reabrir_control(self.conn, c, self.auditor, "Carga tardía de evidencia")
        log = self.conn.execute(
            "SELECT accion, detalle FROM auditoria_log WHERE accion = 'REABRIR_CONTROL'"
        ).fetchone()
        self.assertIn("Carga tardía", log["detalle"])

    def test_confirmacion_queda_logueada(self):
        c = self._control()
        services.confirmar_sector(self.conn, c, self._sectores()["sanidad"], self.auditor)
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM auditoria_log WHERE accion = 'CONFIRMAR_SECTOR'"
        ).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_equipamiento_por_excepcion(self):
        c = self._control()
        est = services.estado_control(self.conn, c)
        self.assertEqual(est["equipamiento"]["porcentaje"], 1.0)

        eq = self.conn.execute("SELECT id FROM equipamiento_limpieza LIMIT 1").fetchone()
        self.conn.execute(
            "INSERT INTO equipamiento_faltante (control_id, equipamiento_id, observacion) "
            "VALUES (?,?,'Fuera de servicio')", (c, eq["id"]))
        self.conn.commit()
        est = services.estado_control(self.conn, c)
        self.assertAlmostEqual(est["equipamiento"]["porcentaje"], 5 / 6)


class TestResumenMensual(Base):
    def test_sin_controles_es_sin_datos(self):
        r = services.resumen_mensual_limpieza(self.conn, PERIODO)
        self.assertIsNone(r["porcentaje_general"])
        self.assertEqual(r["dias_considerados"], 0)

    def test_promedia_solo_los_dias_auditados(self):
        for dia in (1, 2):
            c = self._control(dia)
            self._confirmar_todos(c)
            if dia == 1:
                services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                          "DESVIO_TOTAL", "Sucio", self.auditor)
            services.cerrar_control(self.conn, c, self.auditor)

        r = services.resumen_mensual_limpieza(self.conn, PERIODO)
        self.assertEqual(r["dias_considerados"], 2)
        self.assertEqual(r["turnos_considerados"], 2)
        self.assertEqual(r["dias_del_mes"], 31)
        sanidad = next(s for s in r["sectores"] if s["clave"] == "sanidad")
        self.assertAlmostEqual(sanidad["dias"][f"{PERIODO}-01·MANANA"], 5 / 6)
        self.assertEqual(sanidad["dias"][f"{PERIODO}-02·MANANA"], 1.0)
        # El promedio mensual sale de las 2 recorridas auditadas, no de los 31 días.
        self.assertAlmostEqual(sanidad["mensual"], (5 / 6 + 1) / 2)

    def test_los_dos_turnos_del_dia_promedian_por_separado(self):
        """Cada recorrida es una medición: la tarde no pisa a la mañana."""
        manana = self._control(1, "MANANA")
        self._confirmar_todos(manana)
        services.registrar_desvio(self.conn, manana, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Sucio", self.auditor)
        services.cerrar_control(self.conn, manana, self.auditor)

        tarde = self._control(1, "TARDE")
        self._confirmar_todos(tarde)
        services.cerrar_control(self.conn, tarde, self.auditor)

        r = services.resumen_mensual_limpieza(self.conn, PERIODO)
        self.assertEqual(r["turnos_considerados"], 2)
        self.assertEqual(r["dias_considerados"], 1)
        sanidad = next(s for s in r["sectores"] if s["clave"] == "sanidad")
        self.assertAlmostEqual(sanidad["dias"][f"{PERIODO}-01·MANANA"], 5 / 6)
        self.assertEqual(sanidad["dias"][f"{PERIODO}-01·TARDE"], 1.0)
        self.assertAlmostEqual(sanidad["mensual"], (5 / 6 + 1) / 2)

    def test_un_turno_no_hecho_no_promedia_como_cero(self):
        """Si solo se hizo la mañana, la tarde no baja el porcentaje: no hay
        recorrida de la cual afirmar que el servicio estuvo mal."""
        manana = self._control(1, "MANANA")
        self._confirmar_todos(manana)
        services.cerrar_control(self.conn, manana, self.auditor)

        r = services.resumen_mensual_limpieza(self.conn, PERIODO)
        sanidad = next(s for s in r["sectores"] if s["clave"] == "sanidad")
        self.assertEqual(sanidad["mensual"], 1.0)
        # Pero sí queda registrado como turno faltante.
        turnos = r["completitud"]["turnos"]
        self.assertIn({"fecha": f"{PERIODO}-01", "hechos": ["MANANA"],
                       "faltan": ["TARDE"]}, turnos["dias_parciales"])

    def test_solo_entran_los_controles_cerrados(self):
        c = self._control(1)
        self._confirmar_todos(c)          # abierto, no cerrado
        r = services.resumen_mensual_limpieza(self.conn, PERIODO)
        self.assertEqual(r["dias_considerados"], 0)
        self.assertIsNone(r["porcentaje_general"])

    def test_expone_la_serie_diaria_para_la_tendencia(self):
        for dia in (1, 2):
            c = self._control(dia)
            self._confirmar_todos(c)
            services.cerrar_control(self.conn, c, self.auditor)
        r = services.resumen_mensual_limpieza(self.conn, PERIODO)
        self.assertEqual(sorted(r["serie_diaria"]),
                         [f"{PERIODO}-01·MANANA", f"{PERIODO}-02·MANANA"])


class TestControlDelDia(Base):
    def test_devuelve_el_control_de_la_fecha(self):
        r = services.control_del_dia(self.conn, date(2026, 7, 3))
        self.assertEqual(r["fecha"], "2026-07-03")
        self.assertEqual(r["periodo"], "2026-07")

    def _turno(self, r, turno):
        return next(t["control"] for t in r["turnos"] if t["turno"] == turno)

    def test_devuelve_siempre_los_dos_turnos_exigidos(self):
        r = services.control_del_dia(self.conn, date(2026, 7, 3))
        self.assertEqual([t["turno"] for t in r["turnos"]], ["MANANA", "TARDE"])

    def test_sin_control_iniciado_lo_reporta_vacio(self):
        r = services.control_del_dia(self.conn, date(2026, 7, 3))
        self.assertIsNone(self._turno(r, "MANANA"))
        self.assertIsNone(self._turno(r, "TARDE"))
        self.assertEqual(len(r["mes"]["dias_faltantes"]), 31)

    def test_con_control_en_curso_muestra_su_avance(self):
        c = self._control(3)
        services.confirmar_sector(self.conn, c, self._sectores()["sanidad"], self.auditor)
        r = services.control_del_dia(self.conn, date(2026, 7, 3))
        manana = self._turno(r, "MANANA")
        self.assertEqual(manana["fecha"], "2026-07-03")
        self.assertEqual(manana["turno"], "MANANA")
        self.assertEqual(manana["sectores_confirmados"], 1)
        self.assertEqual(manana["sectores_totales"], 9)
        self.assertEqual(manana["estado"], "ABIERTO")

    def test_un_turno_hecho_no_da_por_hecho_el_otro(self):
        """La exigencia son dos recorridas: haber hecho la mañana no cubre
        la tarde."""
        self._control(3, "MANANA")
        r = services.control_del_dia(self.conn, date(2026, 7, 3))
        self.assertIsNotNone(self._turno(r, "MANANA"))
        self.assertIsNone(self._turno(r, "TARDE"))

    def test_el_control_de_ayer_no_es_el_de_hoy(self):
        """Cada día tiene sus propios controles: no se arrastran los anteriores."""
        self._control(2)
        r = services.control_del_dia(self.conn, date(2026, 7, 3))
        self.assertIsNone(self._turno(r, "MANANA"))
        self.assertIsNone(self._turno(r, "TARDE"))


class TestCompletitudMes(Base):
    def _cerrar(self, dia):
        c = self._control(dia)
        self._confirmar_todos(c)
        services.cerrar_control(self.conn, c, self.auditor)
        return c

    def test_mes_vacio(self):
        r = services.completitud_periodo(self.conn, PERIODO, date(2026, 7, 31))
        self.assertFalse(r["completo"])
        self.assertEqual(len(r["dias_faltantes"]), 31)
        self.assertEqual(r["cobertura"], 0.0)
        self.assertFalse(r["cobertura_suficiente"])

    def test_mes_completo(self):
        for dia in range(1, 32):
            self._cerrar(dia)
        r = services.completitud_periodo(self.conn, PERIODO, date(2026, 8, 1))
        self.assertTrue(r["completo"])
        self.assertEqual(r["dias_vencidos_sin_control"], [])
        self.assertEqual(r["cobertura"], 1.0)

    def test_distingue_lo_que_falta_de_lo_que_todavia_esta_en_plazo(self):
        """A mitad de mes, los días que no llegaron no son un incumplimiento."""
        self._cerrar(1)
        r = services.completitud_periodo(self.conn, PERIODO, date(2026, 7, 5))
        self.assertEqual(r["dias_vencidos_sin_control"],
                         ["2026-07-02", "2026-07-03", "2026-07-04"])
        self.assertEqual(len(r["dias_faltantes"]), 30)

    def test_el_dia_de_hoy_no_esta_vencido(self):
        r = services.completitud_periodo(self.conn, PERIODO, date(2026, 7, 5))
        self.assertNotIn("2026-07-05", r["dias_vencidos_sin_control"])

    def test_mes_pasado_vence_todo(self):
        self._cerrar(1)
        r = services.completitud_periodo(self.conn, PERIODO, date(2026, 9, 1))
        self.assertEqual(len(r["dias_vencidos_sin_control"]), 30)

    def test_mes_futuro_no_vence_nada(self):
        r = services.completitud_periodo(self.conn, "2027-01", date(2026, 7, 16))
        self.assertEqual(r["dias_vencidos_sin_control"], [])

    def test_control_abierto_no_cuenta_como_cerrado(self):
        self._control(1)   # iniciado pero no cerrado
        r = services.completitud_periodo(self.conn, PERIODO, date(2026, 7, 16))
        self.assertEqual(r["dias_iniciados"], ["2026-07-01"])
        self.assertEqual(r["dias_cerrados"], [])
        self.assertEqual(r["dias_abiertos"], ["2026-07-01"])
        self.assertFalse(r["completo"])

    def test_cobertura_suficiente_con_la_mayoria_de_los_dias(self):
        for dia in range(1, 27):        # 26 de 31 = 84%
            self._cerrar(dia)
        r = services.completitud_periodo(self.conn, PERIODO, date(2026, 8, 1))
        self.assertTrue(r["cobertura_suficiente"])
        self.assertFalse(r["completo"])

    def test_cobertura_minima_configurable(self):
        for dia in range(1, 17):        # 16 de 31 = 51,6%
            self._cerrar(dia)
        self.assertFalse(services.completitud_periodo(
            self.conn, PERIODO, date(2026, 8, 1))["cobertura_suficiente"])
        db.set_config(self.conn, "cobertura_minima_mes", 0.5)
        self.assertTrue(services.completitud_periodo(
            self.conn, PERIODO, date(2026, 8, 1))["cobertura_suficiente"])

    def test_el_resumen_mensual_expone_la_completitud(self):
        self._cerrar(1)
        r = services.resumen_mensual_limpieza(self.conn, PERIODO)
        self.assertFalse(r["completitud"]["completo"])
        self.assertEqual(r["dias_considerados"], 1)


class TestEquipamiento(Base):
    """Ítem 4: se mide sobre el inventario de equipos, no sobre horas máquina."""

    def _equipos(self):
        return {f["clave"]: f["id"] for f in self.conn.execute(
            "SELECT id, clave FROM equipamiento_limpieza")}

    def _dia_con_equipo_roto(self, dia, equipamiento_id=None):
        c = self._control(dia)
        self._confirmar_todos(c)
        if equipamiento_id:
            self.conn.execute(
                "INSERT INTO equipamiento_faltante (control_id, equipamiento_id, "
                "observacion) VALUES (?,?,'Fuera de servicio')",
                (c, equipamiento_id))
        services.cerrar_control(self.conn, c, self.auditor)
        return c

    def test_los_seis_equipos_del_pliego_rigen_por_defecto(self):
        equipos = services.equipos_exigidos(self.conn, PERIODO)
        self.assertEqual(len(equipos), 6)
        self.assertTrue(all(e["exigido"] for e in equipos))

    def test_sin_dias_auditados_es_sin_datos(self):
        """Sin ninguna auditoría ni baja cargada no hay evidencia: Sin datos,
        nunca 100%."""
        r = services.equipamiento_mensual(self.conn, PERIODO)
        self.assertIsNone(r["porcentaje"])
        self.assertEqual(r["dias_considerados"], 0)

    def test_todos_disponibles_da_100(self):
        for d in (1, 2, 3):
            self._dia_con_equipo_roto(d)
        r = services.equipamiento_mensual(self.conn, PERIODO)
        self.assertEqual(r["porcentaje"], 1.0)
        self.assertEqual(r["equipos_con_faltas"], [])

    def test_mide_sobre_los_dias_del_periodo_no_sobre_los_auditados(self):
        """Una rotura de 1 día se mide contra los días transcurridos del mes,
        no contra los días que alcanzó a auditarse: si el denominador fueran
        los días auditados, auditar menos mejoraría la disponibilidad."""
        hidro = self._equipos()["hidrolavadora"]
        self._dia_con_equipo_roto(1, hidro)
        for d in (2, 3, 4):
            self._dia_con_equipo_roto(d)

        r = services.equipamiento_mensual(self.conn, PERIODO,
                                          hoy=date(2026, 7, 10))
        self.assertEqual(r["dias_considerados"], 10)
        # 5 equipos enteros + la hidrolavadora 9 de 10 días.
        self.assertAlmostEqual(r["porcentaje"], (5 + 9 / 10) / 6)

    def test_baja_por_tramo_descuenta_dias_sin_control_cerrado(self):
        """El caso que el marcado día por día no cubría: una máquina de baja
        dos semanas descuenta los 14 días aunque solo se haya auditado uno."""
        hidro = self._equipos()["hidrolavadora"]
        self._dia_con_equipo_roto(1)
        services.registrar_baja_equipo(
            self.conn, hidro, "2026-07-01", "2026-07-14",
            "Motor quemado", self.auditor)

        r = services.equipamiento_mensual(self.conn, PERIODO,
                                          hoy=date(2026, 7, 20))
        falta = [e for e in r["equipos_con_faltas"]
                 if e["equipamiento_id"] == hidro][0]
        self.assertEqual(falta["dias_fuera_servicio"], 14)
        self.assertAlmostEqual(r["porcentaje"], (5 + 6 / 20) / 6)

    def test_baja_abierta_sigue_descontando_hasta_el_dia_medido(self):
        """Sin fecha de reposición la máquina sigue fuera de servicio."""
        hidro = self._equipos()["hidrolavadora"]
        self._dia_con_equipo_roto(1)
        services.registrar_baja_equipo(
            self.conn, hidro, "2026-07-05", None, "En reparación", self.auditor)

        r = services.equipamiento_mensual(self.conn, PERIODO,
                                          hoy=date(2026, 7, 10))
        falta = [e for e in r["equipos_con_faltas"]
                 if e["equipamiento_id"] == hidro][0]
        self.assertEqual(falta["dias_fuera_servicio"], 6)   # del 5 al 10

    def test_bajas_superpuestas_no_descuentan_dos_veces(self):
        """Dos turnos pueden cargar la misma baja: se cuentan días únicos."""
        hidro = self._equipos()["hidrolavadora"]
        self._dia_con_equipo_roto(1)
        services.registrar_baja_equipo(self.conn, hidro, "2026-07-02",
                                       "2026-07-06", "Turno mañana", self.auditor)
        services.registrar_baja_equipo(self.conn, hidro, "2026-07-04",
                                       "2026-07-08", "Turno tarde", self.auditor)

        r = services.equipamiento_mensual(self.conn, PERIODO,
                                          hoy=date(2026, 7, 10))
        falta = [e for e in r["equipos_con_faltas"]
                 if e["equipamiento_id"] == hidro][0]
        self.assertEqual(falta["dias_fuera_servicio"], 7)   # del 2 al 8, no 10

    def test_informa_que_equipo_falto_y_cuantos_dias(self):
        hidro = self._equipos()["hidrolavadora"]
        for d in (1, 2):
            self._dia_con_equipo_roto(d, hidro)
        self._dia_con_equipo_roto(3)

        r = services.equipamiento_mensual(self.conn, PERIODO)
        falta = r["equipos_con_faltas"][0]
        self.assertIn("Hidrolavadora", falta["nombre"])
        self.assertEqual(falta["dias_fuera_servicio"], 2)

    def test_los_controles_abiertos_no_alcanzan_como_evidencia(self):
        """Un control sin cerrar no prueba que se haya mirado el equipamiento:
        sin ningún control cerrado ni baja cargada, el ítem es Sin datos."""
        self._control(1)          # abierto
        r = services.equipamiento_mensual(self.conn, PERIODO)
        self.assertIsNone(r["porcentaje"])
        self.assertEqual(r["dias_considerados"], 0)

    def test_equipo_no_exigido_en_el_periodo_no_descuenta(self):
        hidro = self._equipos()["hidrolavadora"]
        # El admin declara que este mes no se exige la hidrolavadora.
        for eid in self._equipos().values():
            self.conn.execute(
                "INSERT INTO periodo_equipamiento (periodo, equipamiento_id, exigido) "
                "VALUES (?,?,?)", (PERIODO, eid, 0 if eid == hidro else 1))
        self.conn.commit()

        self._dia_con_equipo_roto(1, hidro)
        r = services.equipamiento_mensual(self.conn, PERIODO)
        self.assertEqual(r["exigidos"], 5)
        self.assertEqual(r["porcentaje"], 1.0)   # no se exigía, no descuenta

    def test_alimenta_el_item_4_de_la_certificacion(self):
        hidro = self._equipos()["hidrolavadora"]
        self._dia_con_equipo_roto(1, hidro)
        for d in range(2, 26):
            self._dia_con_equipo_roto(d)

        self._datos_obligatorios()
        cert = services.certificacion(self.conn, PERIODO)
        esperado = services.equipamiento_mensual(self.conn, PERIODO)["porcentaje"]
        self.assertAlmostEqual(cert["detalle"]["maquinarias"]["valor"], esperado)
        self.assertEqual(cert["equipamiento"]["exigidos"], 6)


class TestCertificacion(Base):
    def _activar_penalizacion(self):
        """La penalización por NC viene desactivada: no surge del pliego.

        Los tests que miden su mecánica la activan a propósito. Sin esto el
        descuento es cero y las aserciones sobre el efecto de las NC en el
        importe quedan vacías en lugar de fallar.
        """
        db.set_config(self.conn, "penalizacion_nc_activa", True)

    def _mes_completo(self, dias=31):
        """Cierra `dias` días del período (por defecto el mes entero)."""
        for dia in range(1, dias + 1):
            c = self._control(dia)
            self._confirmar_todos(c)
            services.cerrar_control(self.conn, c, self.auditor)

    def test_mes_perfecto_con_datos_cargados_certifica_100(self):
        self._mes_completo()
        self.conn.execute(
            "INSERT INTO periodo_datos (periodo, horas_hombre_programadas, "
            "horas_hombre_perdidas, monto_adjudicado, "
            "documentacion_verificada, ley_19587_verificada) "
            "VALUES (?,1000,0,1000000,1,1)", (PERIODO,))
        cur = self.conn.execute(
            "INSERT INTO insumos (nombre, punto_pedido) VALUES ('Detergente', 10)")
        self.conn.execute(
            "INSERT INTO insumo_stock (periodo, insumo_id, stock) VALUES (?,?,50)",
            (PERIODO, cur.lastrowid))
        self.conn.commit()

        r = services.certificacion(self.conn, PERIODO)
        self.assertAlmostEqual(r["porcentaje"], 1.0)
        self.assertEqual(r["importe"], 1_000_000.0)
        self.assertEqual(r["items_sin_datos"], [])

    def test_falta_un_item_obligatorio_y_no_certifica(self):
        """Requisito duro del contrato: sin los tres ítems obligatorios no hay
        certificación. Su peso NO se redistribuye entre los demás — antes sí, y
        eso permitía cerrar un importe con el 40% del cálculo sin cargar."""
        self._mes_completo()
        r = services.certificacion(self.conn, PERIODO)
        self.assertIsNone(r["porcentaje"])
        self.assertIsNone(r["importe"])
        self.assertEqual(r["detalle"], {})
        for clave in ("documentacion", "ley_19587", "programacion_trabajos"):
            self.assertIn(clave, r["items_obligatorios_faltantes"])

    def test_falta_uno_solo_de_los_obligatorios_y_tampoco_certifica(self):
        self._mes_completo()
        self._datos_obligatorios(horas_hombre_programadas=None)
        r = services.certificacion(self.conn, PERIODO)
        self.assertIsNone(r["porcentaje"])
        self.assertEqual(r["items_obligatorios_faltantes"],
                         ["programacion_trabajos"])

    def test_item_no_obligatorio_sin_datos_si_redistribuye(self):
        """La redistribución sigue vigente para el resto: insumos sin stock
        cargado no bloquea, solo reparte su peso e informa."""
        self._mes_completo()
        self._datos_obligatorios()
        r = services.certificacion(self.conn, PERIODO)
        self.assertIn("insumos", r["items_sin_datos"])
        self.assertEqual(r.get("items_obligatorios_faltantes", []), [])
        self.assertIsNotNone(r["porcentaje"])
        self.assertLess(r["peso_evaluado"], 1.0)

    def test_no_conformidades_bajan_la_calidad(self):
        self._activar_penalizacion()
        self._mes_completo()
        self._datos_obligatorios()
        base = services.certificacion(self.conn, PERIODO)["porcentaje"]
        c1 = self.conn.execute(
            "SELECT id FROM controles_limpieza WHERE fecha = ?",
            (f"{PERIODO}-01",)).fetchone()["id"]
        services.reabrir_control(self.conn, c1, self.auditor, "test")
        services.registrar_desvio(self.conn, c1, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Sucio", self.auditor)
        con_nc = services.certificacion(self.conn, PERIODO)["porcentaje"]
        self.assertLess(con_nc, base)

    def test_hallazgo_de_documentacion_anula_ese_item(self):
        self._mes_completo()
        self._datos_obligatorios(hallazgos_documentacion=1)
        r = services.certificacion(self.conn, PERIODO)
        self.assertEqual(r["detalle"]["documentacion"]["valor"], 0.0)

    def test_periodo_vacio_no_certifica_nada(self):
        """Sin ningún dato cargado, la certificación es Sin datos.

        Los ítems binarios daban 100% con cero hallazgos aunque nadie los
        hubiera revisado: un mes sin auditar habría certificado al 100%.
        """
        r = services.certificacion(self.conn, "2099-12")
        self.assertIsNone(r["porcentaje"])
        self.assertEqual(len(r["items_sin_datos"]), 6)

    def test_items_binarios_exigen_verificacion_explicita(self):
        self._mes_completo()
        r = services.certificacion(self.conn, PERIODO)
        self.assertIn("documentacion", r["items_sin_datos"])
        self.assertIn("ley_19587", r["items_sin_datos"])

        self._datos_obligatorios()
        r = services.certificacion(self.conn, PERIODO)
        self.assertEqual(r["detalle"]["documentacion"]["valor"], 1.0)
        self.assertEqual(r["detalle"]["ley_19587"]["valor"], 1.0)

    def test_advierte_que_la_penalizacion_por_nc_es_provisoria(self):
        """El valor no surge del pliego: debe avisarse hasta que se confirme."""
        self._activar_penalizacion()
        self._mes_completo()
        r = services.certificacion(self.conn, PERIODO)
        aviso = next(a for a in r["advertencias"]
                     if a["codigo"] == "PENALIZACION_NC_NO_CONFIRMADA")
        self.assertIn("NO surge del pliego", aviso["mensaje"])

    def test_la_advertencia_sube_de_nivel_si_hay_nc_abiertas(self):
        self._activar_penalizacion()
        self._mes_completo()
        r = services.certificacion(self.conn, PERIODO)
        self.assertEqual(
            next(a for a in r["advertencias"]
                 if a["codigo"] == "PENALIZACION_NC_NO_CONFIRMADA")["nivel"], "INFO")

        c1 = self.conn.execute(
            "SELECT id FROM controles_limpieza WHERE fecha = ?",
            (f"{PERIODO}-01",)).fetchone()["id"]
        services.reabrir_control(self.conn, c1, self.auditor, "test")
        services.registrar_desvio(self.conn, c1, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Sucio", self.auditor)
        r = services.certificacion(self.conn, PERIODO)
        aviso = next(a for a in r["advertencias"]
                     if a["codigo"] == "PENALIZACION_NC_NO_CONFIRMADA")
        self.assertEqual(aviso["nivel"], "ADVERTENCIA")
        self.assertIn("1 NC abierta", aviso["mensaje"])

    def test_la_advertencia_desaparece_al_confirmar_el_criterio(self):
        self._mes_completo()
        db.set_config(self.conn, "penalizacion_nc_confirmada", True)
        r = services.certificacion(self.conn, PERIODO)
        codigos = [a["codigo"] for a in r["advertencias"]]
        self.assertNotIn("PENALIZACION_NC_NO_CONFIRMADA", codigos)

    def test_penalizacion_configurable_cambia_el_resultado(self):
        self._activar_penalizacion()
        self._mes_completo()
        self._datos_obligatorios()
        c1 = self.conn.execute(
            "SELECT id FROM controles_limpieza WHERE fecha = ?",
            (f"{PERIODO}-01",)).fetchone()["id"]
        services.reabrir_control(self.conn, c1, self.auditor, "test")
        services.registrar_desvio(self.conn, c1, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Sucio", self.auditor)

        base = services.certificacion(self.conn, PERIODO)
        db.set_config(self.conn, "penalizacion_por_nc", 0.05)
        subida = services.certificacion(self.conn, PERIODO)
        self.assertEqual(subida["penalizacion_nc"]["por_nc"], 0.05)
        self.assertLess(subida["porcentaje"], base["porcentaje"])

        # Penalización en cero: el ítem calidad no descuenta por NC.
        db.set_config(self.conn, "penalizacion_por_nc", 0)
        sin_pen = services.certificacion(self.conn, PERIODO)
        self.assertEqual(sin_pen["penalizacion_nc"]["descuento_aplicado"], 0)
        self.assertGreater(sin_pen["porcentaje"], base["porcentaje"])

    def test_el_tope_es_configurable(self):
        self._mes_completo()
        db.set_config(self.conn, "penalizacion_nc_tope", 0.05)
        r = services.certificacion(self.conn, PERIODO)
        self.assertEqual(r["penalizacion_nc"]["tope"], 0.05)

    def test_advierte_si_la_cobertura_es_insuficiente(self):
        """Riesgo central: certificar un pago sobre un mes a medio auditar."""
        self._mes_completo(dias=5)          # 5 de 31 = 16%

        r = services.certificacion(self.conn, PERIODO)
        aviso = next(a for a in r["advertencias"]
                     if a["codigo"] == "COBERTURA_INSUFICIENTE")
        self.assertEqual(aviso["nivel"], "ADVERTENCIA")
        self.assertEqual(aviso["dias_auditados"], 5)
        self.assertEqual(aviso["dias_esperados"], 31)
        self.assertIn("poco representativo", aviso["mensaje"])

    def test_cobertura_alta_pero_incompleta_solo_informa(self):
        self._mes_completo(dias=28)         # 28 de 31 = 90%
        r = services.certificacion(self.conn, PERIODO)
        codigos = [a["codigo"] for a in r["advertencias"]]
        self.assertNotIn("COBERTURA_INSUFICIENTE", codigos)
        aviso = next(a for a in r["advertencias"] if a["codigo"] == "MES_INCOMPLETO")
        self.assertEqual(aviso["nivel"], "INFO")
        self.assertIn("no computan ni penalizan", aviso["mensaje"])

    def test_mes_completo_no_genera_advertencia_de_cobertura(self):
        self._mes_completo()
        r = services.certificacion(self.conn, PERIODO)
        codigos = [a["codigo"] for a in r["advertencias"]]
        self.assertNotIn("COBERTURA_INSUFICIENTE", codigos)
        self.assertNotIn("MES_INCOMPLETO", codigos)
        self.assertTrue(r["completitud"]["completo"])

    def test_advierte_controles_abiertos_sin_cerrar(self):
        self._mes_completo()
        c1 = self.conn.execute(
            "SELECT id FROM controles_limpieza WHERE fecha = ?",
            (f"{PERIODO}-01",)).fetchone()["id"]
        services.reabrir_control(self.conn, c1, self.auditor, "test")

        r = services.certificacion(self.conn, PERIODO)
        aviso = next(a for a in r["advertencias"] if a["codigo"] == "CONTROLES_ABIERTOS")
        self.assertIn(f"{PERIODO}-01", aviso["mensaje"])

    def test_dias_sin_auditar_no_penalizan_pero_quedan_expuestos(self):
        """Decisión acordada: el día no auditado no computa ni penaliza.

        Un solo día perfecto da 100%, pero el resultado tiene que dejar
        constancia de que salió de 1 día sobre 31."""
        self._mes_completo(dias=1)

        resumen = services.resumen_mensual_limpieza(self.conn, PERIODO)
        self.assertEqual(resumen["porcentaje_general"], 1.0)
        self.assertEqual(resumen["dias_considerados"], 1)
        self.assertEqual(resumen["dias_del_mes"], 31)
        self.assertFalse(resumen["completitud"]["completo"])
        self.assertAlmostEqual(resumen["completitud"]["cobertura"], 1 / 31)

        self._datos_obligatorios()
        cert = services.certificacion(self.conn, PERIODO)
        self.assertEqual(cert["detalle"]["calidad_servicio"]["valor"], 1.0)
        self.assertIn("COBERTURA_INSUFICIENTE",
                      [a["codigo"] for a in cert["advertencias"]])

    def test_advierte_los_items_sin_datos(self):
        self._mes_completo()
        self._datos_obligatorios()
        r = services.certificacion(self.conn, PERIODO)
        aviso = next(a for a in r["advertencias"] if a["codigo"] == "ITEMS_SIN_DATOS")
        self.assertIn("insumos", aviso["mensaje"])

    def test_el_aviso_de_obligatorios_es_bloqueante_y_tapa_al_otro(self):
        """Faltando un obligatorio, el aviso que importa es que no se puede
        certificar — no que se redistribuyó un peso que no se redistribuyó."""
        self._mes_completo()
        r = services.certificacion(self.conn, PERIODO)
        codigos = [a["codigo"] for a in r["advertencias"]]
        self.assertIn("ITEMS_OBLIGATORIOS_FALTANTES", codigos)
        self.assertNotIn("ITEMS_SIN_DATOS", codigos)
        aviso = next(a for a in r["advertencias"]
                     if a["codigo"] == "ITEMS_OBLIGATORIOS_FALTANTES")
        self.assertEqual(aviso["nivel"], "BLOQUEANTE")
        self.assertIn("Documentación obligatoria", aviso["mensaje"])

    def test_sin_monto_no_hay_importe(self):
        self._mes_completo()
        r = services.certificacion(self.conn, PERIODO)
        self.assertIsNone(r["importe"])


class TestLoS(Base):
    def _relevamiento(self):
        cur = self.conn.execute(
            "INSERT INTO relevamientos_los (periodo, auditor_id) VALUES (?,?)",
            (PERIODO, self.auditor))
        self.conn.commit()
        return cur.lastrowid

    def _nucleo(self, nombre, tipo, equipos):
        cur = self.conn.execute(
            "INSERT INTO nucleos_sanitarios (nombre, tipo) VALUES (?,?)", (nombre, tipo))
        for equipo, n in equipos.items():
            self.conn.execute(
                "INSERT INTO nucleo_equipos (nucleo_id, equipo, instalados) VALUES (?,?,?)",
                (cur.lastrowid, equipo, n))
        self.conn.commit()
        return cur.lastrowid

    # -- inventario faltante ------------------------------------------------
    def test_sin_inventario_es_sin_datos_no_cumple(self):
        for item in ("banos", "iluminacion", "asientos_preembarque",
                     "puntos_carga", "medios_elevacion", "pista_rodajes"):
            r = services.evaluar_item_los(self.conn, item, {}, PERIODO)
            self.assertIsNone(r["cumple"], item)
            self.assertIn("onfigurac", r["motivo"], item)

    def test_pasarelas_no_aplica(self):
        r = services.evaluar_item_los(self.conn, "pasarelas", {}, PERIODO)
        self.assertTrue(r["no_aplica"])
        self.assertIsNone(r["cumple"])

    # -- 3.1 baños: la limpieza sale del check-list diario ------------------

    def _dia_con_item(self, dia, sector=None, item_pos=None, estado=None, slug=None):
        """Cierra un día del check-list, opcionalmente con un desvío.

        Cuando se pasa `slug`, el desvío se marca en TODOS los sectores de baño
        que tengan ese ítem: el LoS de un equipo cubre los baños del aeropuerto,
        no un núcleo puntual.
        """
        c = self._control(dia)
        self._confirmar_todos(c)
        if estado:
            if slug:
                items = [f["id"] for f in self.conn.execute(
                    "SELECT i.id FROM items_limpieza i JOIN sectores_limpieza s "
                    "ON s.id = i.sector_id WHERE i.clave = ? AND s.clave IN "
                    "('sala_arribos','banos_hall','sanidad')", (slug,))]
            else:
                items = [self._item(sector, item_pos or 0)]
            for item in items:
                services.registrar_desvio(self.conn, c, item, estado,
                                          "Hallazgo", self.auditor)
        services.cerrar_control(self.conn, c, self.auditor)
        return c

    def test_banos_sin_hallazgos_cumple(self):
        self._nucleo("Damas Hall", "DAMAS",
                     {"inodoros": 5, "bachas": 5, "espejos": 5, "cestos": 5})
        for d in range(1, 6):
            self._dia_con_item(d, "banos_hall")
        r = services.evaluar_item_los(self.conn, "banos", {}, PERIODO)
        self.assertTrue(r["cumple"])
        self.assertEqual(r["limpieza"]["origen"], "checklist")

    def test_sin_dias_relevados_es_sin_datos(self):
        """No relevar no es cumplir: sin check-list cerrado no hay resultado."""
        self._nucleo("Damas Hall", "DAMAS", {"bachas": 5})
        r = services.evaluar_item_los(self.conn, "banos", {}, PERIODO)
        self.assertIsNone(r["limpieza"]["cumple"])

    def test_un_dia_malo_de_cinco_tolera_el_objetivo_de_80(self):
        """Lavabos (bachas) exige 80%: 4 de 5 días bien da 80% justo."""
        self._nucleo("Damas Hall", "DAMAS", {"bachas": 5})
        self._dia_con_item(1, estado="DESVIO_TOTAL", slug="lavabos")
        for d in range(2, 6):
            self._dia_con_item(d, "banos_hall")
        r = services.evaluar_banos_desde_checklist(self.conn, PERIODO)
        self.assertAlmostEqual(r["equipos"]["bachas"]["porcentaje"], 0.8)
        self.assertTrue(r["equipos"]["bachas"]["cumple"])

    def test_dos_dias_malos_de_cinco_incumplen(self):
        self._nucleo("Damas Hall", "DAMAS", {"bachas": 5})
        for d in (1, 2):
            self._dia_con_item(d, estado="DESVIO_TOTAL", slug="lavabos")
        for d in range(3, 6):
            self._dia_con_item(d, "banos_hall")
        r = services.evaluar_banos_desde_checklist(self.conn, PERIODO)
        self.assertAlmostEqual(r["equipos"]["bachas"]["porcentaje"], 0.6)
        self.assertFalse(r["equipos"]["bachas"]["cumple"])

    def test_los_equipos_al_100_no_toleran_ningun_dia_malo(self):
        """Espejos exige 100%: un solo desvío parcial en el mes hace incumplir.

        Es la calidad de servicio contratada, no un efecto colateral."""
        self._nucleo("Damas Hall", "DAMAS", {"espejos": 5})
        self._dia_con_item(1, estado="DESVIO_PARCIAL", slug="espejos")
        for d in range(2, 11):
            self._dia_con_item(d, "banos_hall")
        r = services.evaluar_banos_desde_checklist(self.conn, PERIODO)
        espejos = r["equipos"]["espejos"]
        self.assertEqual(espejos["objetivo"], 1.0)
        self.assertLess(espejos["porcentaje"], 1.0)
        self.assertFalse(espejos["cumple"])

    def test_un_equipo_que_incumple_arrastra_el_item(self):
        self._nucleo("Damas Hall", "DAMAS", {"bachas": 5, "espejos": 5})
        self._dia_con_item(1, estado="DESVIO_TOTAL", slug="espejos")
        for d in range(2, 6):
            self._dia_con_item(d, "banos_hall")
        r = services.evaluar_item_los(self.conn, "banos", {}, PERIODO)
        self.assertFalse(r["cumple"])

    def test_los_dispensers_se_miden_por_separado(self):
        """Jabonera y toallero son ítems distintos del check-list, así que un
        problema de jabón no arrastra al papel."""
        self._nucleo("Damas Hall", "DAMAS", {"jaboneras": 2, "toalleros": 2})
        for d in range(1, 6):
            self._dia_con_item(d, estado="DESVIO_TOTAL", slug="jabonera")
        r = services.evaluar_banos_desde_checklist(self.conn, PERIODO)
        self.assertEqual(r["equipos"]["jabonera"]["porcentaje"], 0.0)
        self.assertEqual(r["equipos"]["toallero"]["porcentaje"], 1.0)

    def test_artefacto_fuera_de_servicio_en_pmr_incumple(self):
        """El PMR exige 100%: un solo inodoro clausurado ya incumple.

        La clausura se carga desde el control diario (artefacto_baja), no desde
        LoS: el auditor la ve al entrar al baño y el check-list no puede
        deducirla.
        """
        n = self._nucleo("PMR", "PMR", {"inodoros": 1})
        services.registrar_baja_artefacto(
            self.conn, n, "inodoros", 1, f"{PERIODO}-05", f"{PERIODO}-07",
            "Clausurado por rotura", self.auditor)

        r = services.evaluar_banos(self.conn, {}, PERIODO, hoy=date(2026, 7, 10))
        self.assertFalse(r["cumple"])
        self.assertEqual(r["en_servicio"]["dias_incumplen"],
                         [f"{PERIODO}-05", f"{PERIODO}-06", f"{PERIODO}-07"])

    def test_sin_clausuras_ni_controles_los_banos_son_sin_datos(self):
        """Nunca 100% por ausencia de datos: si nadie miró, no se afirma nada."""
        self._nucleo("PMR", "PMR", {"inodoros": 1})
        r = services.evaluar_banos(self.conn, {}, PERIODO, hoy=date(2026, 7, 10))
        self.assertIsNone(r["cumple"])

    def test_una_clausura_ya_repuesta_no_arrastra_al_resto_del_mes(self):
        n = self._nucleo("PMR", "PMR", {"inodoros": 1})
        bid = services.registrar_baja_artefacto(
            self.conn, n, "inodoros", 1, f"{PERIODO}-05", None,
            "Clausurado", self.auditor)["baja_id"]
        services.editar_baja_artefacto(self.conn, bid, self.auditor,
                                       hasta=f"{PERIODO}-06")
        r = services.evaluar_banos(self.conn, {}, PERIODO, hoy=date(2026, 7, 20))
        self.assertEqual(len(r["en_servicio"]["dias_incumplen"]), 2)

    def test_no_se_pueden_clausurar_mas_artefactos_de_los_instalados(self):
        n = self._nucleo("PMR", "PMR", {"inodoros": 1})
        with self.assertRaises(ValueError):
            services.registrar_baja_artefacto(
                self.conn, n, "inodoros", 3, f"{PERIODO}-05", None, "x", self.auditor)

    def test_solo_se_clausuran_artefactos_con_medicion_de_servicio(self):
        """Jaboneras y espejos se miden por limpieza, no por servicio."""
        n = self._nucleo("Damas", "DAMAS", {"jaboneras": 2})
        with self.assertRaises(ValueError):
            services.registrar_baja_artefacto(
                self.conn, n, "jaboneras", 1, f"{PERIODO}-05", None, "x", self.auditor)

    # -- 3.3 iluminación ----------------------------------------------------
    def test_iluminacion_90_justo_cumple(self):
        self.conn.execute(
            "INSERT INTO luminarias_sector (sector, cantidad) VALUES ('Hall', 50)")
        self.conn.commit()
        r = services.evaluar_item_los(
            self.conn, "iluminacion", {"quemadas": {"Hall": 5}}, PERIODO)
        self.assertTrue(r["cumple"])

    def test_iluminacion_consecutivas_incumple_pese_al_umbral(self):
        self.conn.execute(
            "INSERT INTO luminarias_sector (sector, cantidad) VALUES ('Hall', 100)")
        self.conn.commit()
        r = services.evaluar_item_los(
            self.conn, "iluminacion",
            {"quemadas": {"Hall": 2}, "consecutivas_mismo_cono": {"Hall": True}}, PERIODO)
        self.assertFalse(r["cumple"])

    # -- 3.5 / 3.6 ----------------------------------------------------------
    def test_asientos(self):
        self.conn.execute("UPDATE asientos_preembarque SET instalados = 40 WHERE id = 1")
        self.conn.commit()
        self.assertTrue(services.evaluar_item_los(
            self.conn, "asientos_preembarque", {"inutilizables": 2}, PERIODO)["cumple"])
        self.assertFalse(services.evaluar_item_los(
            self.conn, "asientos_preembarque", {"inutilizables": 3}, PERIODO)["cumple"])

    def test_puntos_de_carga(self):
        cur = self.conn.execute(
            "INSERT INTO puertas_embarque (nombre, php, instaladas) VALUES ('P1',76,20)")
        self.conn.commit()
        pid = str(cur.lastrowid)
        self.assertTrue(services.evaluar_item_los(
            self.conn, "puntos_carga", {}, PERIODO)["cumple"])
        self.assertFalse(services.evaluar_item_los(
            self.conn, "puntos_carga", {"fuera_servicio": {pid: 2}}, PERIODO)["cumple"])

    # -- 3.7 elevación ------------------------------------------------------
    def _habilitar_elevacion(self):
        """IRJ no tiene ascensores ni escaleras, así que el ítem viene NO APLICA.

        Estos tests miden la matemática del ítem —acumulado de horas contra el
        tope mensual—, no su aplicabilidad, así que lo habilitan a propósito.
        Sin esto `evaluar_item_los` corta antes y devuelve cumple=None, que
        vuelve vacías las aserciones en lugar de fallar.
        """
        self.conn.execute(
            "UPDATE los_items SET aplica = 1 WHERE clave = 'medios_elevacion'")
        self.conn.commit()

    def test_elevacion_acumula_eventos_del_mes(self):
        self._habilitar_elevacion()
        cur = self.conn.execute(
            "INSERT INTO medios_elevacion (nombre, redundancia) VALUES ('Ascensor 1', 0)")
        eid = cur.lastrowid
        for horas in (20, 20):
            self.conn.execute(
                "INSERT INTO elevacion_eventos (equipo_id, periodo, inicio, horas) "
                "VALUES (?,?,'2026-07-01 08:00',?)", (eid, PERIODO, horas))
        self.conn.commit()
        r = services.evaluar_item_los(self.conn, "medios_elevacion", {}, PERIODO)
        self.assertEqual(r["equipos"][0]["horas_indisponible"], 40)
        self.assertTrue(r["cumple"])

    def test_elevacion_supera_tope_mensual(self):
        self._habilitar_elevacion()
        cur = self.conn.execute(
            "INSERT INTO medios_elevacion (nombre, redundancia) VALUES ('Ascensor 1', 0)")
        self.conn.execute(
            "INSERT INTO elevacion_eventos (equipo_id, periodo, inicio, horas) "
            "VALUES (?,?,'2026-07-01 08:00',60)", (cur.lastrowid, PERIODO))
        self.conn.commit()
        r = services.evaluar_item_los(self.conn, "medios_elevacion", {}, PERIODO)
        # `assertIs(..., False)` y no `assertFalse`: un None también pasaría el
        # segundo, y un ítem que no se evalúa devuelve exactamente None. Con el
        # ítem en NO APLICA esta aserción quedó vacía sin que nadie lo notara.
        self.assertIs(r["cumple"], False)

    def test_elevacion_que_no_aplica_no_se_evalua(self):
        """Con el ítem en NO APLICA no hay condición que verificar, ni siquiera
        con eventos cargados: no es que cumpla, es que no corresponde."""
        cur = self.conn.execute(
            "INSERT INTO medios_elevacion (nombre, redundancia) VALUES ('Ascensor 1', 0)")
        self.conn.execute(
            "INSERT INTO elevacion_eventos (equipo_id, periodo, inicio, horas) "
            "VALUES (?,?,'2026-07-01 08:00',600)", (cur.lastrowid, PERIODO))
        self.conn.commit()
        r = services.evaluar_item_los(self.conn, "medios_elevacion", {}, PERIODO)
        self.assertIsNone(r["cumple"])
        self.assertTrue(r["no_aplica"])

    # -- 3.2 / 3.9 mediciones obligatorias ---------------------------------
    def test_confort_sin_medicion_es_sin_datos(self):
        r = services.evaluar_item_los(self.conn, "confort_termico", {}, PERIODO)
        self.assertIsNone(r["cumple"])

    def test_confort_evalua_contra_el_rango_estacional(self):
        r = services.evaluar_item_los(self.conn, "confort_termico", {
            "estacion": "VERANO",
            "mediciones": [{"zona": "Check-in", "temperatura": 24.5}]}, PERIODO)
        self.assertTrue(r["cumple"])
        r = services.evaluar_item_los(self.conn, "confort_termico", {
            "estacion": "VERANO",
            "mediciones": [{"zona": "Check-in", "temperatura": 27.0}]}, PERIODO)
        self.assertFalse(r["cumple"])

    def test_misma_temperatura_cumple_en_invierno_y_no_en_verano(self):
        med = [{"zona": "Arribos", "temperatura": 20.0}]
        self.assertTrue(services.evaluar_item_los(
            self.conn, "confort_termico",
            {"estacion": "INVIERNO", "mediciones": med}, PERIODO)["cumple"])
        self.assertFalse(services.evaluar_item_los(
            self.conn, "confort_termico",
            {"estacion": "VERANO", "mediciones": med}, PERIODO)["cumple"])

    def test_gel(self):
        self.assertIsNone(services.evaluar_item_los(self.conn, "gel", {}, PERIODO)["cumple"])
        self.assertTrue(services.evaluar_item_los(self.conn, "gel", {
            "pruebas": [{"ayuda_luminosa": "PAPI", "tiempo_s": 12}]}, PERIODO)["cumple"])
        self.assertFalse(services.evaluar_item_los(self.conn, "gel", {
            "pruebas": [{"ayuda_luminosa": "PAPI", "tiempo_s": 16}]}, PERIODO)["cumple"])

    # -- 3.4 / 3.8 ----------------------------------------------------------
    def test_infraestructura_genera_nc_al_guardar(self):
        r_id = self._relevamiento()
        # Infraestructura es diaria: la fecha va explícita para que el test no
        # dependa de que hoy caiga dentro de PERIODO.
        services.guardar_medicion_los(self.conn, r_id, "infraestructura", {
            "subitems": {"cielorraso": "A", "alfombras_manchas": "D"}},
            fecha=f"{PERIODO}-05")
        nc = self.conn.execute(
            "SELECT * FROM no_conformidades WHERE origen = 'LOS'").fetchall()
        self.assertEqual(len(nc), 1)
        self.assertEqual(nc[0]["prioridad"], "INMEDIATA")

    def test_limpieza_terminal_convierte_llenado_en_grado(self):
        r = services.evaluar_item_los(self.conn, "limpieza_terminal",
                                      {"llenado": {"cestos_interiores": 85}}, PERIODO)
        self.assertFalse(r["cumple"])
        r = services.evaluar_item_los(self.conn, "limpieza_terminal",
                                      {"llenado": {"cestos_interiores": 60}}, PERIODO)
        self.assertTrue(r["cumple"])

    # -- 3.10 ---------------------------------------------------------------
    def test_pista_pci(self):
        ids = []
        for i in range(10):
            cur = self.conn.execute(
                "INSERT INTO secciones_pavimento (identificador, tipo) VALUES (?, 'PISTA')",
                (f"P{i}",))
            ids.append(cur.lastrowid)
        self.conn.commit()
        pci = {str(i): 80 for i in ids[:9]}
        pci[str(ids[9])] = 50
        r = services.evaluar_item_los(self.conn, "pista_rodajes", {"pci": pci}, PERIODO)
        self.assertTrue(r["pista"]["cumple"])
        self.assertTrue(r["cumple"])

    def test_indisponibilidad_no_programada_incumple(self):
        self.conn.execute(
            "INSERT INTO secciones_pavimento (identificador, tipo) VALUES ('P1','PISTA')")
        self.conn.commit()
        r = services.evaluar_item_los(
            self.conn, "pista_rodajes",
            {"indisponibilidades_no_programadas": 1}, PERIODO)
        self.assertFalse(r["cumple"])

    # -- persistencia -------------------------------------------------------
    def test_guardar_medicion_persiste_entrada_y_resultado(self):
        r_id = self._relevamiento()
        services.guardar_medicion_los(self.conn, r_id, "confort_termico", {
            "estacion": "VERANO", "mediciones": [{"zona": "Check-in", "temperatura": 24.0}]})
        fila = self.conn.execute(
            "SELECT datos, resultado, cumple FROM los_mediciones "
            "WHERE relevamiento_id = ?", (r_id,)).fetchone()
        self.assertEqual(fila["cumple"], 1)
        self.assertIn("Check-in", fila["datos"])
        self.assertIn("cumple", fila["resultado"])

    def test_relevamiento_cerrado_es_inmutable(self):
        r_id = self._relevamiento()
        self.conn.execute(
            "UPDATE relevamientos_los SET estado = 'CERRADO' WHERE id = ?", (r_id,))
        self.conn.commit()
        with self.assertRaises(PermissionError):
            services.guardar_medicion_los(self.conn, r_id, "infraestructura",
                                          {"subitems": {"vidrios": "A"}})

    # -- 3.12 dashboard -----------------------------------------------------
    def test_dashboard_sin_relevamientos(self):
        d = services.dashboard_los(self.conn, PERIODO)
        self.assertIsNone(d["porcentaje"])
        # IRJ no tiene mangas ni medios de elevación.
        self.assertEqual(d["no_aplica"], ["medios_elevacion", "pasarelas"])
        self.assertEqual(d["items_aplicables"], 9)
        self.assertEqual(len(d["requieren_configuracion"]), 5)

    def test_dashboard_calcula_porcentaje_global(self):
        r_id = self._relevamiento()
        services.guardar_medicion_los(self.conn, r_id, "infraestructura",
                                      {"subitems": {"vidrios": "A"}},
                                      fecha=f"{PERIODO}-05")
        services.guardar_medicion_los(self.conn, r_id, "gel", {
            "pruebas": [{"ayuda_luminosa": "PAPI", "tiempo_s": 20}]})   # incumple
        d = services.dashboard_los(self.conn, PERIODO)
        self.assertEqual(d["items_evaluados"], 2)
        self.assertEqual(d["items_cumplen"], 1)
        self.assertAlmostEqual(d["porcentaje"], 0.5)

    def test_dashboard_marca_los_que_requieren_configuracion(self):
        d = services.dashboard_los(self.conn, PERIODO)
        banos = next(i for i in d["items"] if i["clave"] == "banos")
        self.assertTrue(banos["requiere_configuracion"])
        self.assertEqual(banos["estado"], "SIN_DATOS")

    # -- relevamiento: get-or-create y prellenado ---------------------------

    def test_sin_relevamiento_abierto_devuelve_none(self):
        self.assertIsNone(services.relevamiento_los_actual(self.conn, PERIODO))

    def test_obtener_o_crear_no_duplica(self):
        id1 = services.obtener_o_crear_relevamiento_los(self.conn, PERIODO, self.auditor)
        id2 = services.obtener_o_crear_relevamiento_los(self.conn, PERIODO, self.auditor)
        self.assertEqual(id1, id2)
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM relevamientos_los WHERE periodo = ?",
            (PERIODO,)).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_cerrar_y_volver_a_pedir_crea_uno_nuevo(self):
        id1 = services.obtener_o_crear_relevamiento_los(self.conn, PERIODO, self.auditor)
        self.conn.execute(
            "UPDATE relevamientos_los SET estado = 'CERRADO' WHERE id = ?", (id1,))
        self.conn.commit()
        id2 = services.obtener_o_crear_relevamiento_los(self.conn, PERIODO, self.auditor)
        self.assertNotEqual(id1, id2)

    def test_mediciones_relevamiento_prellena_datos_y_resultado(self):
        r_id = self._relevamiento()
        services.guardar_medicion_los(self.conn, r_id, "gel", {
            "pruebas": [{"ayuda_luminosa": "PAPI", "tiempo_s": 12}]}, "Todo OK")
        med = services.mediciones_relevamiento(self.conn, r_id)
        self.assertIn("gel", med)
        self.assertEqual(med["gel"]["datos"]["pruebas"][0]["tiempo_s"], 12)
        self.assertTrue(med["gel"]["resultado"]["cumple"])
        self.assertEqual(med["gel"]["observaciones"], "Todo OK")
        self.assertEqual(med["gel"]["fotos"], [])

    def test_reabrir_relevamiento_exige_motivo(self):
        r_id = self._relevamiento()
        self.conn.execute(
            "UPDATE relevamientos_los SET estado = 'CERRADO' WHERE id = ?", (r_id,))
        self.conn.commit()
        with self.assertRaises(ValueError):
            services.reabrir_relevamiento_los(self.conn, r_id, self.auditor, "")
        services.reabrir_relevamiento_los(self.conn, r_id, self.auditor, "Dato faltante")
        estado = self.conn.execute(
            "SELECT estado FROM relevamientos_los WHERE id = ?", (r_id,)).fetchone()
        self.assertEqual(estado["estado"], "ABIERTO")

    def test_reabrir_queda_logueado(self):
        r_id = self._relevamiento()
        services.reabrir_relevamiento_los(self.conn, r_id, self.auditor, "motivo")
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM auditoria_log "
            "WHERE accion = 'REABRIR_RELEVAMIENTO_LOS'").fetchone()["c"]
        self.assertEqual(n, 1)

    # -- eventos de elevación -------------------------------------------------

    def test_eventos_elevacion_trae_el_nombre_del_equipo(self):
        cur = self.conn.execute(
            "INSERT INTO medios_elevacion (nombre, redundancia) VALUES ('Ascensor 1', 0)")
        eid = cur.lastrowid
        self.conn.execute(
            "INSERT INTO elevacion_eventos (equipo_id, periodo, inicio, horas) "
            "VALUES (?,?,'2026-07-01 08:00',12)", (eid, PERIODO))
        self.conn.commit()
        eventos = services.eventos_elevacion(self.conn, PERIODO)
        self.assertEqual(eventos[0]["equipo_nombre"], "Ascensor 1")

    def test_eventos_elevacion_filtra_por_equipo(self):
        e1 = self.conn.execute(
            "INSERT INTO medios_elevacion (nombre, redundancia) VALUES ('A1', 0)").lastrowid
        e2 = self.conn.execute(
            "INSERT INTO medios_elevacion (nombre, redundancia) VALUES ('A2', 0)").lastrowid
        for eid in (e1, e2):
            self.conn.execute(
                "INSERT INTO elevacion_eventos (equipo_id, periodo, inicio, horas) "
                "VALUES (?,?,'2026-07-01 08:00',5)", (eid, PERIODO))
        self.conn.commit()
        self.assertEqual(len(services.eventos_elevacion(self.conn, PERIODO)), 2)
        self.assertEqual(len(services.eventos_elevacion(self.conn, PERIODO, e1)), 1)


class TestEstaciones(Base):
    def test_verano_cruza_el_fin_de_ano(self):
        self.assertEqual(services.estacion_actual(self.conn, date(2026, 12, 15)), "VERANO")
        self.assertEqual(services.estacion_actual(self.conn, date(2026, 1, 15)), "VERANO")
        self.assertEqual(services.estacion_actual(self.conn, date(2026, 10, 1)), "VERANO")

    def test_invierno(self):
        self.assertEqual(services.estacion_actual(self.conn, date(2026, 4, 1)), "INVIERNO")
        self.assertEqual(services.estacion_actual(self.conn, date(2026, 7, 15)), "INVIERNO")
        self.assertEqual(services.estacion_actual(self.conn, date(2026, 9, 30)), "INVIERNO")

    def test_fechas_configurables(self):
        db.set_config(self.conn, "inicio_verano", "11-01")
        self.assertEqual(services.estacion_actual(self.conn, date(2026, 10, 15)), "INVIERNO")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCorreccionDeBajas(Base):
    """Los días de baja descuentan del pago: una fecha mal cargada tiene que
    poder corregirse, y el cambio tiene que quedar asentado."""

    def _equipo(self):
        return self.conn.execute(
            "SELECT id FROM equipamiento_limpieza LIMIT 1").fetchone()["id"]

    def _baja(self, desde="2026-07-10", hasta="2026-07-14"):
        return services.registrar_baja_equipo(
            self.conn, self._equipo(), desde, hasta, "Motor quemado",
            self.auditor)["baja_id"]

    def test_corregir_la_fecha_cambia_los_dias_descontados(self):
        bid = self._baja()
        antes = services.equipamiento_mensual(self.conn, PERIODO,
                                              hoy=date(2026, 7, 31))
        services.editar_baja_equipo(self.conn, bid, self.auditor,
                                    hasta="2026-07-12")
        despues = services.equipamiento_mensual(self.conn, PERIODO,
                                                hoy=date(2026, 7, 31))
        self.assertGreater(despues["porcentaje"], antes["porcentaje"])
        falta = despues["equipos_con_faltas"][0]
        self.assertEqual(falta["dias_fuera_servicio"], 3)   # 10, 11 y 12

    def test_la_correccion_queda_en_el_log_con_los_valores_previos(self):
        bid = self._baja()
        services.editar_baja_equipo(self.conn, bid, self.auditor,
                                    hasta="2026-07-12")
        fila = self.conn.execute(
            "SELECT detalle FROM auditoria_log WHERE accion = 'EDITAR_BAJA_EQUIPO'"
        ).fetchone()
        detalle = json.loads(fila["detalle"])
        self.assertEqual(detalle["antes"]["hasta"], "2026-07-14")
        self.assertEqual(detalle["ahora"]["hasta"], "2026-07-12")

    def test_reabrir_una_baja_la_deja_sin_reposicion(self):
        bid = self._baja()
        services.editar_baja_equipo(self.conn, bid, self.auditor, reabrir=True)
        fila = self.conn.execute(
            "SELECT hasta FROM equipamiento_baja WHERE id = ?", (bid,)).fetchone()
        self.assertIsNone(fila["hasta"])

    def test_no_admite_reposicion_anterior_a_la_baja(self):
        bid = self._baja()
        with self.assertRaises(ValueError):
            services.editar_baja_equipo(self.conn, bid, self.auditor,
                                        hasta="2026-07-01")

    def test_no_admite_quedarse_sin_motivo(self):
        bid = self._baja()
        with self.assertRaises(ValueError):
            services.editar_baja_equipo(self.conn, bid, self.auditor, motivo="   ")

    def test_borrar_la_baja_devuelve_la_disponibilidad(self):
        bid = self._baja()
        services.borrar_baja_equipo(self.conn, bid, self.auditor)
        r = services.equipamiento_mensual(self.conn, PERIODO, hoy=date(2026, 7, 31))
        self.assertEqual(r["equipos_con_faltas"], [])


class TestEstadoModulos(Base):
    """Semáforo de las tarjetas de inicio. Binario y con ventana: gris hasta el
    día en que arranca la liquidación, y desde ahí verde o rojo."""

    def _todo_cargado(self):
        """Deja el período con todo lo que la certificación exige."""
        self.conn.execute(
            "INSERT INTO periodo_datos (periodo, documentacion_verificada, "
            "ley_19587_verificada, horas_hombre_programadas, "
            "horas_hombre_perdidas, monto_adjudicado) "
            "VALUES (?,1,1,1000,0,500000)", (PERIODO,))
        # Insumos del mes: sin stock relevado el ítem 5 queda sin datos.
        for f in self.conn.execute("SELECT id FROM insumos WHERE activo = 1"):
            self.conn.execute(
                "INSERT INTO insumo_stock (periodo, insumo_id, stock) VALUES (?,?,?)",
                (PERIODO, f["id"], 100))
        # Inventario físico: mientras un bloque esté vacío su ítem LoS queda en
        # "Requiere configuración", y eso también traba la liquidación.
        self.conn.execute("INSERT INTO nucleos_sanitarios (nombre, tipo) "
                          "VALUES ('Damas hall', 'DAMAS')")
        self.conn.execute("INSERT INTO luminarias_sector (sector, cantidad) "
                          "VALUES ('hall', 20)")
        self.conn.execute("UPDATE asientos_preembarque SET instalados = 40 WHERE id = 1")
        self.conn.execute("INSERT INTO puertas_embarque (nombre, php, instaladas) "
                          "VALUES ('1', 76, 25)")
        self.conn.execute("INSERT INTO secciones_pavimento (identificador, tipo) "
                          "VALUES ('PISTA-01', 'PISTA')")
        self.conn.commit()

    def _auditar(self, dias):
        for dia in dias:
            for turno in ("MANANA", "TARDE"):
                c = self._control(dia, turno)
                self._confirmar_todos(c)
                services.cerrar_control(self.conn, c, self.auditor)

    def test_antes_del_26_las_cuatro_estan_en_gris(self):
        """Liquidar el día 8 no es una tarea pendiente: la pregunta no aplica."""
        r = services.estado_modulos(self.conn, date(2026, 7, 25))
        self.assertFalse(r["en_ventana"])
        for modulo in ("limpieza", "los", "informes", "config"):
            self.assertEqual(r[modulo], services.ESTADO_SIN_VENTANA, modulo)

    def test_desde_el_26_el_semaforo_opina(self):
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertTrue(r["en_ventana"])
        self.assertEqual(r["informes"], services.ESTADO_FALTANTE)

    def test_el_dia_de_inicio_es_configurable(self):
        db.set_config(self.conn, "liquidacion_dia_inicio", 10)
        self.assertTrue(services.estado_modulos(self.conn, date(2026, 7, 10))["en_ventana"])
        self.assertFalse(services.estado_modulos(self.conn, date(2026, 7, 9))["en_ventana"])

    def test_no_hay_estado_intermedio(self):
        """La liquidación es binaria: o están todos los datos o no están."""
        posibles = set()
        for dia in (25, 26, 31):
            r = services.estado_modulos(self.conn, date(2026, 7, dia))
            posibles.update(r[m] for m in ("limpieza", "los", "informes", "config"))
        self.assertFalse(posibles - {services.ESTADO_SIN_VENTANA,
                                     services.ESTADO_AL_DIA,
                                     services.ESTADO_FALTANTE})

    def test_el_verde_es_alcanzable_sin_haber_auditado_el_mes_perfecto(self):
        """Con la regla de oro un día salteado no se recupera nunca. Si el
        verde exigiera el mes completo, saltear una jornada lo dejaría fuera de
        alcance para siempre y el semáforo solo podría estar en rojo."""
        self._todo_cargado()
        # 26 de 31 días: por encima del 80% mínimo, pero lejos del mes perfecto.
        self._auditar(range(1, 27))
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertEqual(r["limpieza"], services.ESTADO_AL_DIA)
        self.assertEqual(r["config"], services.ESTADO_AL_DIA)

    def test_informes_tambien_espera_a_los(self):
        """La pantalla emite los dos PDF, el de limpieza y el de LoS: con los
        ítems del manual sin relevar, el informe del mes sale a medias."""
        self._todo_cargado()
        self._auditar(range(1, 27))
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertEqual(r["informes"], services.ESTADO_FALTANTE)

    def test_la_cobertura_por_debajo_del_minimo_es_rojo(self):
        self._todo_cargado()
        self._auditar(range(1, 6))          # 5 de 31: 16%
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertEqual(r["limpieza"], services.ESTADO_FALTANTE)
        self.assertEqual(r["informes"], services.ESTADO_FALTANTE)

    def test_falta_el_monto_adjudicado_y_ya_es_rojo(self):
        """Sin monto hay porcentaje pero no hay importe, que es lo único que el
        contratista cobra."""
        self._todo_cargado()
        self._auditar(range(1, 27))
        self.conn.execute(
            "UPDATE periodo_datos SET monto_adjudicado = NULL WHERE periodo = ?",
            (PERIODO,))
        self.conn.commit()
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertEqual(r["config"], services.ESTADO_FALTANTE)
        self.assertEqual(r["informes"], services.ESTADO_FALTANTE)

    def test_la_recorrida_de_hoy_no_cuenta_como_faltante(self):
        """El 27 a la mañana quedan las horas del día para recorrer: exigirla ya
        sería marcar como falta algo que todavía está en plazo."""
        self._todo_cargado()
        self._auditar(range(1, 27))         # hasta el 26; el 27 sin tocar
        r = services.estado_modulos(self.conn, date(2026, 7, 27))
        self.assertEqual(r["limpieza"], services.ESTADO_AL_DIA)

    def test_el_rojo_dice_por_que(self):
        """El color solo avisaba que algo faltaba y había que salir a buscarlo."""
        self._todo_cargado()
        self._auditar(range(1, 27))
        self.conn.execute(
            "UPDATE periodo_datos SET monto_adjudicado = NULL WHERE periodo = ?",
            (PERIODO,))
        self.conn.commit()
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertEqual(r["motivos"]["config"], "Falta el monto adjudicado")
        # Informes no se arregla desde Informes: señala de dónde viene la falta.
        self.assertIn("Configuración", r["motivos"]["informes"])

    def test_lo_que_esta_en_verde_no_lleva_motivo(self):
        self._todo_cargado()
        self._auditar(range(1, 27))
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertEqual(r["limpieza"], services.ESTADO_AL_DIA)
        self.assertIsNone(r["motivos"]["limpieza"])

    def test_en_gris_tampoco_hay_motivo_pero_la_clave_existe(self):
        """La pantalla lee `motivos[clave]` sin preguntar si la clave está."""
        r = services.estado_modulos(self.conn, date(2026, 7, 25))
        self.assertFalse(r["en_ventana"])
        for modulo in ("limpieza", "los", "informes", "config"):
            self.assertIsNone(r["motivos"][modulo], modulo)

    def test_el_motivo_de_limpieza_cuenta_los_dias_sin_auditar(self):
        self._todo_cargado()
        self._auditar(range(1, 6))          # 5 de 31
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertEqual(r["limpieza"], services.ESTADO_FALTANTE)
        # El título dice qué traba; el detalle, por qué no tiene arreglo.
        pend = r["pendientes"]["limpieza"][0]
        self.assertIn("Cobertura", pend["titulo"])
        self.assertIn("sin recorrida", pend["detalle"])

    def test_el_detalle_lista_los_items_de_los_para_ir_directo(self):
        """El aviso del módulo no puede decir "3 ítems" y hacer buscar cuáles."""
        self._todo_cargado()
        self._auditar(range(1, 27))
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        items = r["pendientes"]["los"][0]["items"]
        self.assertTrue(items)
        for i in items:
            self.assertTrue(i["nombre"])
            self.assertTrue(i["ruta"].startswith("/los/"))

    def test_el_resumen_de_la_tarjeta_sale_del_mismo_detalle(self):
        """Tarjeta y aviso no pueden decir cosas distintas."""
        self._todo_cargado()
        self._auditar(range(1, 27))
        self.conn.execute(
            "UPDATE periodo_datos SET monto_adjudicado = NULL WHERE periodo = ?",
            (PERIODO,))
        self.conn.commit()
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        for modulo in ("limpieza", "los", "config"):
            pend = r["pendientes"][modulo]
            if pend:
                self.assertEqual(r["motivos"][modulo],
                                 " · ".join(p["titulo"] for p in pend), modulo)
            else:
                self.assertIsNone(r["motivos"][modulo], modulo)

    def test_informes_remite_a_los_modulos_y_no_repite_su_detalle(self):
        """Informes no se arregla desde Informes."""
        self._todo_cargado()
        self._auditar(range(1, 27))
        self.conn.execute(
            "UPDATE periodo_datos SET monto_adjudicado = NULL WHERE periodo = ?",
            (PERIODO,))
        self.conn.commit()
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        rutas = [i["ruta"] for p in r["pendientes"]["informes"] for i in p["items"]]
        self.assertIn("/config", rutas)
        self.assertIn("/los", rutas)
        # El resumen de la tarjeta no encadena un "Faltan datos en" por módulo.
        self.assertEqual(r["motivos"]["informes"].count("Faltan datos en"), 1)

    def test_los_distingue_sin_relevar_de_no_cumple(self):
        """Los dos frenan la liquidación, pero uno se resuelve cargando el dato
        y el otro reclamándole al contratista."""
        self._todo_cargado()
        self._auditar(range(1, 27))
        r = services.estado_modulos(self.conn, date(2026, 7, 26))
        self.assertEqual(r["los"], services.ESTADO_FALTANTE)
        self.assertIn("sin relevar", r["motivos"]["los"])
        self.assertNotIn("no cumplen", r["motivos"]["los"])

    def test_el_semaforo_no_depende_del_rol(self):
        """Hay datos que solo carga el admin, pero el auditor tiene que ver que
        faltan: los ve en el resultado del mes de todos modos."""
        import inspect
        self.assertNotIn("es_admin",
                         inspect.signature(services.estado_modulos).parameters)


class TestEvidenciaFotografica(Base):
    """La evidencia tiene que poder volver a la pantalla que la generó.

    Se guardaba bien desde siempre, pero ninguna respuesta de la API la
    devolvía fuera de LoS, así que una foto sacada en una recorrida no se podía
    volver a ver salvo abriendo el PDF del mes.
    """

    def _foto(self, entidad, entidad_id, archivo, subitem=None):
        self.conn.execute(
            "INSERT INTO fotos (entidad, entidad_id, subitem, archivo) "
            "VALUES (?,?,?,?)", (entidad, entidad_id, subitem, archivo))
        self.conn.commit()

    def test_agrupa_por_entidad_en_una_sola_consulta(self):
        self._foto("desvio", 1, "a.jpg", "piso")
        self._foto("desvio", 1, "b.jpg", "zócalo")
        self._foto("desvio", 2, "c.jpg")
        fotos = services.fotos_por_entidad(self.conn, "desvio", [1, 2, 3])
        self.assertEqual([f["archivo"] for f in fotos[1]], ["a.jpg", "b.jpg"])
        self.assertEqual(fotos[1][0]["subitem"], "piso")
        self.assertEqual([f["archivo"] for f in fotos[2]], ["c.jpg"])
        self.assertNotIn(3, fotos)          # sin fotos, sin entrada

    def test_no_confunde_entidades_distintas_con_el_mismo_id(self):
        self._foto("desvio", 7, "del-desvio.jpg")
        self._foto("los_medicion", 7, "de-los.jpg")
        fotos = services.fotos_por_entidad(self.conn, "desvio", [7])
        self.assertEqual([f["archivo"] for f in fotos[7]], ["del-desvio.jpg"])

    def test_sin_ids_no_consulta(self):
        self.assertEqual(services.fotos_por_entidad(self.conn, "desvio", []), {})
        # Los nulos vienen de las NC de LoS, que no cuelgan de ningún desvío.
        self.assertEqual(services.fotos_por_entidad(self.conn, "desvio", [None]), {})

    def test_la_nc_pendiente_lleva_la_foto_de_su_desvio(self):
        c = self._control(dia=1)
        r = services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                      "DESVIO_TOTAL", "Piso con residuos",
                                      self.auditor)
        self._foto("desvio", r["desvio_id"], "2026-07/limpieza/x.jpg", "piso")

        pend = services.nc_pendientes_anteriores(self.conn, f"{PERIODO}-05")
        self.assertEqual(len(pend), 1)
        self.assertEqual([f["archivo"] for f in pend[0]["fotos"]],
                         ["2026-07/limpieza/x.jpg"])

    def test_la_nc_sin_evidencia_trae_la_lista_vacia_y_no_falta_la_clave(self):
        c = self._control(dia=1)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso con residuos", self.auditor)
        pend = services.nc_pendientes_anteriores(self.conn, f"{PERIODO}-05")
        self.assertEqual(pend[0]["fotos"], [])


class TestNovedades(Base):
    """El centro de novedades se calcula al vuelo: no puede desfasarse."""

    def _cerrar_dia(self, dia, turno="MANANA"):
        c = self._control(dia, turno)
        self._confirmar_todos(c)
        services.cerrar_control(self.conn, c, self.auditor)
        return c

    def test_mes_al_dia_no_genera_ruido(self):
        """Sin hallazgos no hay novedades: si avisara igual, se aprendería a
        ignorar el aviso."""
        for dia in (1, 2):
            for turno in ("MANANA", "TARDE"):
                self._cerrar_dia(dia, turno)
        r = services.novedades(self.conn, date(2026, 7, 2))
        self.assertEqual([n["clave"] for n in r["novedades"]
                          if n["clave"] == "nc_demoradas"], [])

    def test_una_nc_vieja_es_critica(self):
        c = self._control(1)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso sucio", self.auditor)
        r = services.novedades(self.conn, date(2026, 7, 20))
        nov = next(n for n in r["novedades"] if n["clave"] == "nc_demoradas")
        self.assertEqual(nov["criticidad"], services.CRITICIDAD_ALTA)
        self.assertEqual(nov["cantidad"], 1)

    def test_una_nc_reciente_de_resolucion_programada_no_es_critica(self):
        c = self._control(1)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_PARCIAL", "Manchas", self.auditor)
        r = services.novedades(self.conn, date(2026, 7, 2))
        nov = next(n for n in r["novedades"] if n["clave"] == "nc_pendientes")
        self.assertEqual(nov["criticidad"], services.CRITICIDAD_MEDIA)

    def test_una_nc_inmediata_del_mismo_dia_ya_es_critica(self):
        """El turno tarde tiene que ver lo que cargó el turno mañana: filtrar
        por "días anteriores" dejaba invisible la novedad del propio día."""
        c = self._control(1)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso sucio", self.auditor)
        r = services.novedades(self.conn, date(2026, 7, 1))
        nov = next(n for n in r["novedades"] if n["clave"] == "nc_inmediatas")
        self.assertEqual(nov["criticidad"], services.CRITICIDAD_ALTA)
        self.assertEqual(nov["cantidad"], 1)

    def test_una_nc_no_se_cuenta_en_dos_categorias(self):
        """Demorada e inmediata a la vez cuenta una sola vez, en la más grave."""
        c = self._control(1)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso sucio", self.auditor)
        r = services.novedades(self.conn, date(2026, 7, 20))
        claves = [n["clave"] for n in r["novedades"]]
        self.assertIn("nc_demoradas", claves)
        self.assertNotIn("nc_inmediatas", claves)

    def test_una_maquina_sin_reposicion_avisa_y_es_critica(self):
        equipo = self.conn.execute(
            "SELECT id FROM equipamiento_limpieza LIMIT 1").fetchone()["id"]
        services.registrar_baja_equipo(self.conn, equipo, "2026-07-05", None,
                                       "Motor quemado", self.auditor)
        r = services.novedades(self.conn, date(2026, 7, 12))
        nov = next(n for n in r["novedades"] if n["clave"] == "maquinaria_baja")
        self.assertEqual(nov["criticidad"], services.CRITICIDAD_ALTA)
        self.assertIn("7 día(s)", nov["detalle"])

    def test_una_maquina_ya_repuesta_deja_de_avisar(self):
        equipo = self.conn.execute(
            "SELECT id FROM equipamiento_limpieza LIMIT 1").fetchone()["id"]
        bid = services.registrar_baja_equipo(
            self.conn, equipo, "2026-07-05", None, "Motor quemado",
            self.auditor)["baja_id"]
        services.editar_baja_equipo(self.conn, bid, self.auditor,
                                    hasta="2026-07-08")
        r = services.novedades(self.conn, date(2026, 7, 12))
        self.assertNotIn("maquinaria_baja", [n["clave"] for n in r["novedades"]])

    def test_los_dias_con_una_sola_recorrida_avisan(self):
        self._cerrar_dia(1, "MANANA")
        r = services.novedades(self.conn, date(2026, 7, 2))
        nov = next(n for n in r["novedades"] if n["clave"] == "turnos_faltantes")
        self.assertEqual(nov["cantidad"], 1)

    def test_el_inventario_pendiente_solo_le_aparece_al_admin(self):
        claves = lambda es_admin: [   # noqa: E731
            n["clave"] for n in
            services.novedades(self.conn, date(2026, 7, 2), es_admin)["novedades"]]
        self.assertIn("inventario", claves(True))
        self.assertNotIn("inventario", claves(False))

    def test_las_nc_viajan_con_la_novedad_para_resolverse_sin_navegar(self):
        """La acción de una NC pendiente es cerrarla, y eso se hace en la misma
        hoja: si la novedad no trajera las NC, el botón tendría que mandar al
        listado de días, donde no se las menciona."""
        c = self._control(1)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso sucio", self.auditor)
        r = services.novedades(self.conn, date(2026, 7, 20))
        nov = next(n for n in r["novedades"] if n["clave"] == "nc_demoradas")
        self.assertEqual(nov["accion"], services.ACCION_RESOLVER_NC)
        self.assertEqual(len(nov["datos"]["no_conformidades"]), 1)
        self.assertEqual(nov["datos"]["no_conformidades"][0]["descripcion"],
                         "Piso sucio")

    def test_un_dia_perdido_no_ofrece_ninguna_accion(self):
        """No se puede abrir el control de un día pasado ni siendo admin, así
        que un botón ahí prometería una consecuencia inexistente."""
        self._cerrar_dia(1, "MANANA")
        self._cerrar_dia(1, "TARDE")
        r = services.novedades(self.conn, date(2026, 7, 5))
        nov = next(n for n in r["novedades"] if n["clave"] == "dias_sin_control")
        self.assertIsNone(nov["accion"])
        self.assertIsNone(nov["ruta"])
        # Tampoco es crítica: la cuenta de críticas es lo que hay que hacer hoy.
        self.assertEqual(nov["criticidad"], services.CRITICIDAD_MEDIA)

    def test_la_cobertura_baja_es_informativa_y_no_tiene_boton(self):
        self._cerrar_dia(1, "MANANA")
        r = services.novedades(self.conn, date(2026, 7, 20))
        nov = next(n for n in r["novedades"] if n["clave"] == "cobertura")
        self.assertIsNone(nov["accion"])

    def test_el_turno_que_falta_hoy_se_puede_iniciar(self):
        """Lo único del plan de auditoría que todavía se puede cumplir."""
        self._cerrar_dia(3, "MANANA")
        r = services.novedades(self.conn, date(2026, 7, 3))
        nov = next(n for n in r["novedades"] if n["clave"] == "turno_hoy")
        self.assertEqual(nov["accion"], services.ACCION_INICIAR_TURNO)
        self.assertEqual(nov["datos"], {"fecha": "2026-07-03", "turno": "TARDE"})

    def test_un_turno_de_hoy_ya_abierto_no_se_ofrece_iniciar(self):
        """Existe pero sin cerrar: `POST /api/controles` daría 409, así que el
        botón fallaría. Es trabajo en curso, no un faltante."""
        self._cerrar_dia(3, "MANANA")
        self._control(3, "TARDE")          # abierto, sin cerrar
        r = services.novedades(self.conn, date(2026, 7, 3))
        self.assertNotIn("turno_hoy", [n["clave"] for n in r["novedades"]])

    def test_la_maquina_de_baja_viaja_con_su_id_para_darla_de_alta(self):
        equipo = self.conn.execute(
            "SELECT id FROM equipamiento_limpieza LIMIT 1").fetchone()["id"]
        bid = services.registrar_baja_equipo(
            self.conn, equipo, "2026-07-05", None, "Motor quemado",
            self.auditor)["baja_id"]
        r = services.novedades(self.conn, date(2026, 7, 12))
        nov = next(n for n in r["novedades"] if n["clave"] == "maquinaria_baja")
        self.assertEqual(nov["accion"], services.ACCION_ALTA_MAQUINA)
        self.assertEqual([b["id"] for b in nov["datos"]["bajas"]], [bid])

    def test_un_unico_item_de_los_lleva_directo_a_ese_item(self):
        """Con uno solo no hay ambigüedad: entrar al tablero obligaría a
        buscarlo entre los once."""
        rid = services.obtener_o_crear_relevamiento_los(
            self.conn, "2026-07", self.auditor)
        services.guardar_medicion_los(self.conn, rid, "gel", {
            "pruebas": [{"ayuda_luminosa": "PAPI", "tiempo_s": 20}]})
        r = services.novedades(self.conn, date(2026, 7, 12))
        nov = next(n for n in r["novedades"] if n["clave"] == "los_no_cumple")
        self.assertEqual(nov["accion"], services.ACCION_IR)
        self.assertEqual(nov["ruta"], "/los/gel")

    def test_varios_items_de_los_llevan_al_tablero(self):
        """Elegir uno de tres sería arbitrario y escondería los otros dos."""
        rid = services.obtener_o_crear_relevamiento_los(
            self.conn, "2026-07", self.auditor)
        services.guardar_medicion_los(self.conn, rid, "gel", {
            "pruebas": [{"ayuda_luminosa": "PAPI", "tiempo_s": 20}]})
        services.guardar_medicion_los(self.conn, rid, "infraestructura",
                                      {"subitems": {"vidrios": "D"}},
                                      fecha="2026-07-05")
        r = services.novedades(self.conn, date(2026, 7, 12))
        nov = next(n for n in r["novedades"] if n["clave"] == "los_no_cumple")
        self.assertEqual(nov["cantidad"], 2)
        self.assertEqual(nov["ruta"], "/los")

    def test_el_inventario_pendiente_lleva_a_su_pestana(self):
        r = services.novedades(self.conn, date(2026, 7, 2), es_admin=True)
        nov = next(n for n in r["novedades"] if n["clave"] == "inventario")
        self.assertEqual(nov["ruta"], "/config/inventario")

    def test_las_criticas_van_primero(self):
        c = self._control(1)
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso sucio", self.auditor)
        r = services.novedades(self.conn, date(2026, 7, 20), es_admin=True)
        criticidades = [n["criticidad"] for n in r["novedades"]]
        self.assertEqual(criticidades, sorted(
            criticidades, key=lambda c: 0 if c == services.CRITICIDAD_ALTA else 1))
        self.assertGreaterEqual(r["criticas"], 1)


class TestNovedadesDelMismoDia(Base):
    """El caso que se escapaba: lo cargado hoy no producía ninguna novedad."""

    def test_una_nc_y_una_maquina_cargadas_hoy_generan_novedades(self):
        c = self._control(1, "TARDE")
        services.registrar_desvio(self.conn, c, self._item("sanidad"),
                                  "DESVIO_TOTAL", "Piso sucio", self.auditor)
        equipo = self.conn.execute(
            "SELECT id FROM equipamiento_limpieza LIMIT 1").fetchone()["id"]
        services.registrar_baja_equipo(self.conn, equipo, f"{PERIODO}-01", None,
                                       "Motor quemado", self.auditor)

        r = services.novedades(self.conn, date(2026, 7, 1))
        claves = {n["clave"] for n in r["novedades"]}
        self.assertIn("nc_inmediatas", claves)
        self.assertIn("maquinaria_baja", claves)

    def test_la_marca_suelta_del_modelo_anterior_tambien_avisa(self):
        """Una tablet con el frontend viejo cacheado sigue usando
        equipamiento_faltante; esa máquina no puede quedar sin aviso."""
        c = self._control(1)
        equipo = self.conn.execute(
            "SELECT id FROM equipamiento_limpieza LIMIT 1").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO equipamiento_faltante (control_id, equipamiento_id, "
            "observacion) VALUES (?,?,'Fuera de servicio')", (c, equipo))
        self.conn.commit()

        r = services.novedades(self.conn, date(2026, 7, 1))
        nov = next(n for n in r["novedades"] if n["clave"] == "maquinaria_baja")
        self.assertEqual(nov["cantidad"], 1)

    def test_no_se_cuenta_dos_veces_la_misma_maquina(self):
        """Cargada por los dos caminos, sigue siendo una sola máquina."""
        c = self._control(1)
        equipo = self.conn.execute(
            "SELECT id FROM equipamiento_limpieza LIMIT 1").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO equipamiento_faltante (control_id, equipamiento_id, "
            "observacion) VALUES (?,?,'Fuera de servicio')", (c, equipo))
        self.conn.commit()
        services.registrar_baja_equipo(self.conn, equipo, f"{PERIODO}-01", None,
                                       "Motor quemado", self.auditor)

        r = services.novedades(self.conn, date(2026, 7, 1))
        nov = next(n for n in r["novedades"] if n["clave"] == "maquinaria_baja")
        self.assertEqual(nov["cantidad"], 1)
