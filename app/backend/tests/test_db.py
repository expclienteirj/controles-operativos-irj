"""Tests del esquema y del seed de configuración IRJ."""

import os
import sys
import sqlite3
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calc  # noqa: E402
import db    # noqa: E402
import basedeprueba  # noqa: E402
import seed  # noqa: E402


class BaseEnMemoria(unittest.TestCase):
    def setUp(self):
        self.conn, self.resumen = basedeprueba.nueva(admin_password="test1234")

    def tearDown(self):
        self.conn.close()


class TestSeed(BaseEnMemoria):
    def test_nueve_sectores_de_la_planilla(self):
        n = self.conn.execute("SELECT COUNT(*) c FROM sectores_limpieza").fetchone()["c"]
        self.assertEqual(n, 9)

    def test_cantidad_de_items_por_sector(self):
        # Incluye "Techo" en los 7 sectores interiores (alimenta telarañas y
        # polvo del LoS 3.8), los dispensers desdoblados en los baños, y
        # "Contenedores de basura" en estacionamiento.
        esperado = {"sala_embarque": 9, "sala_arribos": 15, "check_in": 7,
                    "hall_central": 18, "sanidad": 6, "banos_hall": 16,
                    "air_side": 4, "estacionamiento": 4, "oficinas_aa": 6}
        for clave, cant in esperado.items():
            n = self.conn.execute(
                "SELECT COUNT(*) c FROM items_limpieza i "
                "JOIN sectores_limpieza s ON s.id = i.sector_id WHERE s.clave = ?",
                (clave,)).fetchone()["c"]
            self.assertEqual(n, cant, f"sector {clave}")

    def test_once_items_los(self):
        n = self.conn.execute("SELECT COUNT(*) c FROM los_items").fetchone()["c"]
        self.assertEqual(n, 11)

    def test_pasarelas_no_aplica_en_irj(self):
        fila = self.conn.execute(
            "SELECT aplica FROM los_items WHERE clave = 'pasarelas'").fetchone()
        self.assertEqual(fila["aplica"], 0)

    def test_el_resto_de_los_items_aplica(self):
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM los_items WHERE aplica = 1").fetchone()["c"]
        # 11 ítems menos pasarelas y medios de elevación: IRJ no tiene
        # mangas ni ascensores/escaleras mecánicas.
        self.assertEqual(n, 9)

    def test_seis_equipos_de_limpieza_exigidos(self):
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM equipamiento_limpieza").fetchone()["c"]
        self.assertEqual(n, 6)

    def test_seed_es_idempotente(self):
        antes = self.conn.execute("SELECT COUNT(*) c FROM items_limpieza").fetchone()["c"]
        seed.aplicar_seed(self.conn)
        seed.aplicar_seed(self.conn)
        despues = self.conn.execute("SELECT COUNT(*) c FROM items_limpieza").fetchone()["c"]
        self.assertEqual(antes, despues)

    def test_seed_no_pisa_ediciones_del_admin(self):
        db.set_config(self.conn, "iluminacion_objetivo", 0.95)
        seed.aplicar_seed(self.conn)
        self.assertEqual(db.get_config(self.conn, "iluminacion_objetivo"), 0.95)


class TestConfigIRJ(BaseEnMemoria):
    def test_datos_generales(self):
        self.assertEqual(db.get_config(self.conn, "aeropuerto_codigo"), "IRJ")
        self.assertEqual(db.get_config(self.conn, "aeropuerto_categoria"), "G5")
        self.assertEqual(db.get_config(self.conn, "horario_operativo_inicio"), "07:00")
        self.assertEqual(db.get_config(self.conn, "horario_operativo_fin"), "21:00")
        self.assertEqual(db.get_config(self.conn, "horas_operativas_dia"), 14)

    def test_umbrales_los_coinciden_con_el_motor(self):
        """El seed y calc.py no pueden divergir: la API lee de config."""
        self.assertEqual(db.get_config(self.conn, "iluminacion_objetivo"), 0.90)
        self.assertEqual(db.get_config(self.conn, "asientos_minimo"),
                         calc.ASIENTOS_MINIMOS_IRJ)
        self.assertEqual(db.get_config(self.conn, "tomas_por_100_pax"),
                         calc.TOMAS_POR_100_PAX)
        self.assertEqual(db.get_config(self.conn, "confort_termico"), calc.CONFORT_IRJ)
        self.assertEqual(db.get_config(self.conn, "elevacion_umbrales"),
                         calc.ELEVACION_IRJ)
        self.assertEqual(db.get_config(self.conn, "pci_pista"), calc.PCI_PISTA)
        self.assertEqual(db.get_config(self.conn, "pci_rodaje"), calc.PCI_RODAJE)

    def test_confort_termico_irj_verano_b_invierno_c(self):
        c = db.get_config(self.conn, "confort_termico")
        self.assertEqual(c["VERANO"]["categoria"], "B")
        self.assertEqual(c["INVIERNO"]["categoria"], "C")

    def test_pesos_certificacion_suman_uno(self):
        pesos = db.get_config(self.conn, "pesos")
        self.assertAlmostEqual(sum(pesos.values()), 1.0)
        self.assertEqual(pesos["programacion_trabajos"], 0.40)
        self.assertEqual(pesos["calidad_servicio"], 0.20)

    def test_objetivos_de_banos(self):
        obj = db.get_config(self.conn, "banos_objetivo_nucleo")
        self.assertEqual(obj["DAMAS"], 0.80)
        self.assertEqual(obj["PMR"], 1.00)

    def test_pmr_y_bebes_sin_tolerancia_en_limpieza(self):
        obj = db.get_config(self.conn, "banos_limpieza_objetivos")
        self.assertTrue(all(v == 1.00 for v in obj["PMR"].values()))
        self.assertTrue(all(v == 1.00 for v in obj["RECINTO_BEBES"].values()))

    def test_espejos_y_pisos_al_100_dentro_de_bachas(self):
        obj = db.get_config(self.conn, "banos_limpieza_objetivos")["BACHAS"]
        self.assertEqual(obj["bachas"], 0.80)
        self.assertEqual(obj["espejos"], 1.00)
        self.assertEqual(obj["pisos"], 1.00)

    def test_config_editable_por_admin(self):
        db.set_config(self.conn, "asientos_minimo", 45)
        self.assertEqual(db.get_config(self.conn, "asientos_minimo"), 45)


class TestInventarioVacio(BaseEnMemoria):
    def test_inventario_nace_vacio(self):
        """Requisito 4.2: el inventario físico no viene precargado."""
        for tabla in ("nucleos_sanitarios", "luminarias_sector", "puertas_embarque",
                      "medios_elevacion", "secciones_pavimento"):
            n = self.conn.execute(f"SELECT COUNT(*) c FROM {tabla}").fetchone()["c"]
            self.assertEqual(n, 0, tabla)
        n = self.conn.execute(
            "SELECT instalados FROM asientos_preembarque WHERE id = 1").fetchone()
        self.assertEqual(n["instalados"], 0)

    def test_items_cuantitativos_reportados_como_pendientes(self):
        pend = {p["item"] for p in db.inventario_pendiente(self.conn)}
        # medios_elevacion no está: no aplica, así que no exige inventario.
        self.assertEqual(pend, {"banos", "iluminacion", "asientos_preembarque",
                                "puntos_carga", "pista_rodajes"})

    def test_item_deja_de_estar_pendiente_al_cargar_inventario(self):
        self.conn.execute(
            "INSERT INTO luminarias_sector (sector, cantidad) VALUES ('Hall central', 40)")
        self.conn.commit()
        pend = {p["item"] for p in db.inventario_pendiente(self.conn)}
        self.assertNotIn("iluminacion", pend)

    def test_asientos_pendiente_hasta_cargar_cantidad(self):
        self.conn.execute("UPDATE asientos_preembarque SET instalados = 60 WHERE id = 1")
        self.conn.commit()
        pend = {p["item"] for p in db.inventario_pendiente(self.conn)}
        self.assertNotIn("asientos_preembarque", pend)


class TestIntegridad(BaseEnMemoria):
    def _auditor(self):
        """Devuelve siempre el mismo auditor, creándolo la primera vez."""
        self.conn.execute(
            "INSERT OR IGNORE INTO usuarios (usuario, nombre, password_hash, rol) "
            "VALUES ('jperez','J. Pérez',?, 'auditor')", (db.hash_password("x"),))
        return self.conn.execute(
            "SELECT id FROM usuarios WHERE usuario = 'jperez'").fetchone()["id"]

    def _control(self, fecha="2026-07-01"):
        cur = self.conn.execute(
            "INSERT INTO controles_limpieza (fecha, periodo, auditor_id) VALUES (?,?,?)",
            (fecha, fecha[:7], self._auditor()))
        return cur.lastrowid

    def test_un_solo_control_por_dia(self):
        self._control("2026-07-01")
        with self.assertRaises(db.ERRORES_INTEGRIDAD):
            self.conn.execute(
                "INSERT INTO controles_limpieza (fecha, periodo, auditor_id) "
                "VALUES ('2026-07-01', '2026-07', 1)")

    def test_el_periodo_debe_coincidir_con_la_fecha(self):
        """Evita que un control quede contabilizado en el mes equivocado."""
        with self.assertRaises(db.ERRORES_INTEGRIDAD):
            self.conn.execute(
                "INSERT INTO controles_limpieza (fecha, periodo, auditor_id) "
                "VALUES ('2026-07-01', '2026-08', 1)")

    def test_dias_distintos_conviven(self):
        self._control("2026-07-01")
        self._control("2026-07-02")
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM controles_limpieza").fetchone()["c"]
        self.assertEqual(n, 2)

    def test_un_solo_desvio_por_item_y_control(self):
        control_id = self._control()
        item_id = self.conn.execute("SELECT id FROM items_limpieza LIMIT 1").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO desvios (control_id, item_id, estado, observacion, creado_por) "
            "VALUES (?,?,'DESVIO_TOTAL','Piso sucio',1)", (control_id, item_id))
        with self.assertRaises(db.ERRORES_INTEGRIDAD):
            self.conn.execute(
                "INSERT INTO desvios (control_id, item_id, estado, observacion, creado_por) "
                "VALUES (?,?,'DESVIO_PARCIAL','Otra cosa',1)", (control_id, item_id))

    def test_estado_de_desvio_invalido_es_rechazado(self):
        control_id = self._control()
        item_id = self.conn.execute("SELECT id FROM items_limpieza LIMIT 1").fetchone()["id"]
        with self.assertRaises(db.ERRORES_INTEGRIDAD):
            self.conn.execute(
                "INSERT INTO desvios (control_id, item_id, estado, observacion, creado_por) "
                "VALUES (?,?,'CUMPLE','x',1)", (control_id, item_id))

    def test_cumple_no_se_almacena_es_el_default(self):
        """Corolario del diseño por excepción: la ausencia de fila ya significa
        cumplimiento, guardar 'CUMPLE' sería redundante y contradictorio.

        Se lee la definición del CHECK del catálogo, que cada motor expone en
        su propia tabla de sistema."""
        if basedeprueba.usa_postgres():
            restricciones = " ".join(
                f["definicion"] for f in self.conn.execute(
                    "SELECT pg_get_constraintdef(oid) AS definicion "
                    "FROM pg_constraint "
                    "WHERE conrelid = 'desvios'::regclass AND contype = 'c'"))
        else:
            restricciones = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'desvios'").fetchone()["sql"]
        self.assertNotIn("'CUMPLE'", restricciones)

    def test_borrar_control_arrastra_sus_desvios(self):
        control_id = self._control()
        item_id = self.conn.execute("SELECT id FROM items_limpieza LIMIT 1").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO desvios (control_id, item_id, estado, observacion, creado_por) "
            "VALUES (?,?,'DESVIO_TOTAL','x',1)", (control_id, item_id))
        self.conn.execute("DELETE FROM controles_limpieza WHERE id = ?", (control_id,))
        n = self.conn.execute("SELECT COUNT(*) c FROM desvios").fetchone()["c"]
        self.assertEqual(n, 0)

    def test_rol_invalido_es_rechazado(self):
        with self.assertRaises(db.ERRORES_INTEGRIDAD):
            self.conn.execute(
                "INSERT INTO usuarios (usuario, nombre, password_hash, rol) "
                "VALUES ('x','X','h','supervisor')")

    def test_tipo_de_nucleo_invalido_es_rechazado(self):
        with self.assertRaises(db.ERRORES_INTEGRIDAD):
            self.conn.execute(
                "INSERT INTO nucleos_sanitarios (nombre, tipo) VALUES ('X','MIXTO')")


class TestUsuarios(BaseEnMemoria):
    def test_se_crea_admin_inicial(self):
        fila = self.conn.execute(
            "SELECT usuario, rol FROM usuarios WHERE rol = 'admin'").fetchone()
        self.assertEqual(fila["usuario"], "admin")

    def test_password_se_almacena_hasheada(self):
        fila = self.conn.execute(
            "SELECT password_hash FROM usuarios WHERE usuario = 'admin'").fetchone()
        self.assertNotIn("test1234", fila["password_hash"])
        self.assertTrue(fila["password_hash"].startswith("pbkdf2$"))

    def test_verificacion_de_password(self):
        h = db.hash_password("clave-secreta")
        self.assertTrue(db.verificar_password("clave-secreta", h))
        self.assertFalse(db.verificar_password("clave-secret", h))
        self.assertFalse(db.verificar_password("", h))

    def test_hash_usa_salt_distinto_cada_vez(self):
        self.assertNotEqual(db.hash_password("misma"), db.hash_password("misma"))

    def test_password_invalida_no_rompe(self):
        self.assertFalse(db.verificar_password("x", "formato-corrupto"))
        self.assertFalse(db.verificar_password("x", None))

    def test_admin_sin_password_explicita_recibe_una_aleatoria(self):
        conn, resumen = basedeprueba.nueva()
        self.assertIn("admin_password", resumen)
        self.assertGreaterEqual(len(resumen["admin_password"]), 12)
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestZonasConfortSeed(BaseEnMemoria):
    def test_las_cuatro_zonas_de_medicion(self):
        zonas = db.get_config(self.conn, "confort_zonas")
        self.assertEqual(zonas, ["Hall público", "Arribos", "Embarque", "Bar"])

    def test_son_editables_por_el_admin(self):
        db.set_config(self.conn, "confort_zonas", ["Hall público", "Bar"])
        self.assertEqual(db.get_config(self.conn, "confort_zonas"),
                         ["Hall público", "Bar"])


class TestMigracionTurnos(BaseEnMemoria):
    """La base vieja tenía un control por día con UNIQUE(fecha).

    Reconstruir esa tabla es la parte más riesgosa del cambio: si el DROP se
    hace con las foreign keys activas, se llevan puestos en cascada los desvíos
    y las confirmaciones de todos los controles ya cargados.
    """

    def _base_vieja(self):
        """Recrea el esquema anterior a los turnos, con datos cargados."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE usuarios (id INTEGER PRIMARY KEY, usuario TEXT,
                                   nombre TEXT, password_hash TEXT, rol TEXT);
            CREATE TABLE sectores_limpieza (id INTEGER PRIMARY KEY, clave TEXT,
                                            nombre TEXT, orden INTEGER, activo INTEGER);
            CREATE TABLE items_limpieza (id INTEGER PRIMARY KEY, sector_id INTEGER,
                                         clave TEXT, nombre TEXT, orden INTEGER,
                                         activo INTEGER);
            CREATE TABLE controles_limpieza (
                id INTEGER PRIMARY KEY,
                fecha TEXT NOT NULL UNIQUE,
                periodo TEXT NOT NULL,
                auditor_id INTEGER NOT NULL REFERENCES usuarios(id),
                iniciado_en TEXT, cerrado_en TEXT,
                estado TEXT NOT NULL DEFAULT 'ABIERTO');
            CREATE TABLE control_sectores (
                id INTEGER PRIMARY KEY,
                control_id INTEGER NOT NULL
                    REFERENCES controles_limpieza(id) ON DELETE CASCADE,
                sector_id INTEGER, confirmado INTEGER);
            CREATE TABLE desvios (
                id INTEGER PRIMARY KEY,
                control_id INTEGER NOT NULL
                    REFERENCES controles_limpieza(id) ON DELETE CASCADE,
                item_id INTEGER, estado TEXT, observacion TEXT);

            -- Tablas que tocan las otras migraciones del mismo lote.
            CREATE TABLE fotos (id INTEGER PRIMARY KEY, entidad TEXT,
                                entidad_id INTEGER, archivo TEXT, tomada_en TEXT);
            CREATE TABLE equipamiento_limpieza (id INTEGER PRIMARY KEY, clave TEXT,
                                                nombre TEXT, exigido INTEGER,
                                                orden INTEGER);
            CREATE TABLE equipamiento_faltante (
                id INTEGER PRIMARY KEY,
                control_id INTEGER NOT NULL
                    REFERENCES controles_limpieza(id) ON DELETE CASCADE,
                equipamiento_id INTEGER, observacion TEXT);
            CREATE TABLE periodo_datos (periodo TEXT PRIMARY KEY,
                                        monto_adjudicado REAL, cerrado INTEGER);
            CREATE TABLE los_items (id INTEGER PRIMARY KEY, clave TEXT UNIQUE,
                                    nombre TEXT, orden INTEGER, aplica INTEGER,
                                    requiere_inventario TEXT);
            CREATE TABLE relevamientos_los (id INTEGER PRIMARY KEY, periodo TEXT,
                                            fecha TEXT, auditor_id INTEGER,
                                            estado TEXT, cerrado_en TEXT);
            CREATE TABLE los_mediciones (
                id INTEGER PRIMARY KEY,
                relevamiento_id INTEGER NOT NULL
                    REFERENCES relevamientos_los(id) ON DELETE CASCADE,
                item_clave TEXT NOT NULL REFERENCES los_items(clave),
                datos TEXT NOT NULL, resultado TEXT, cumple INTEGER,
                observaciones TEXT, creado_en TEXT,
                UNIQUE (relevamiento_id, item_clave));
            CREATE TABLE no_conformidades (
                id INTEGER PRIMARY KEY, periodo TEXT NOT NULL, origen TEXT NOT NULL,
                sector TEXT, item TEXT, descripcion TEXT NOT NULL, prioridad TEXT,
                estado TEXT NOT NULL DEFAULT 'ABIERTA',
                desvio_id INTEGER REFERENCES desvios(id) ON DELETE SET NULL,
                creado_en TEXT NOT NULL DEFAULT (datetime('now')), resuelto_en TEXT);

            INSERT INTO usuarios (id, usuario, nombre, password_hash, rol)
                VALUES (1, 'a', 'Auditor', 'x', 'auditor');
            INSERT INTO controles_limpieza (id, fecha, periodo, auditor_id, estado)
                VALUES (1, '2026-06-01', '2026-06', 1, 'CERRADO'),
                       (2, '2026-06-02', '2026-06', 1, 'ABIERTO');
            INSERT INTO control_sectores (control_id, sector_id, confirmado)
                VALUES (1, 1, 1), (1, 2, 1);
            INSERT INTO desvios (id, control_id, item_id, estado, observacion)
                VALUES (5, 1, 7, 'DESVIO_TOTAL', 'Piso sucio');
            INSERT INTO equipamiento_faltante (control_id, equipamiento_id, observacion)
                VALUES (1, 3, 'Motor quemado');
            INSERT INTO no_conformidades (periodo, origen, descripcion, desvio_id,
                                          creado_en)
                VALUES ('2026-06', 'LIMPIEZA', 'Piso sucio', 5, '2026-06-01 10:00:00');
            INSERT INTO los_items (id, clave, nombre, orden, aplica)
                VALUES (1, 'iluminacion', 'Iluminación', 3, 1);
            INSERT INTO relevamientos_los (id, periodo, fecha, auditor_id, estado)
                VALUES (1, '2026-06', '2026-06-01', 1, 'CERRADO');
            INSERT INTO los_mediciones (relevamiento_id, item_clave, datos, cumple)
                VALUES (1, 'iluminacion', '{}', 1);
        """)
        conn.commit()
        return conn

    def test_no_borra_los_desvios_ni_las_confirmaciones(self):
        conn = self._base_vieja()
        db.migrar(conn)

        self.assertEqual(
            conn.execute("SELECT COUNT(*) c FROM desvios").fetchone()["c"], 1)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) c FROM control_sectores").fetchone()["c"], 2)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) c FROM equipamiento_faltante"
                         ).fetchone()["c"], 1)
        conn.close()

    def test_deduce_la_fecha_de_origen_de_las_nc_ya_cargadas(self):
        """Sin fecha_origen, una NC vieja no se le arrastraría a nadie."""
        conn = self._base_vieja()
        db.migrar(conn)

        fila = conn.execute(
            "SELECT fecha_origen FROM no_conformidades").fetchone()
        self.assertEqual(fila["fecha_origen"], "2026-06-01")
        conn.close()

    def test_los_controles_viejos_quedan_como_turno_manana(self):
        conn = self._base_vieja()
        db.migrar(conn)

        turnos = [f["turno"] for f in conn.execute(
            "SELECT turno FROM controles_limpieza ORDER BY fecha")]
        self.assertEqual(turnos, ["MANANA", "MANANA"])
        conn.close()

    def test_conserva_estado_y_fechas(self):
        conn = self._base_vieja()
        db.migrar(conn)

        filas = [dict(f) for f in conn.execute(
            "SELECT fecha, estado FROM controles_limpieza ORDER BY fecha")]
        self.assertEqual(filas, [{"fecha": "2026-06-01", "estado": "CERRADO"},
                                 {"fecha": "2026-06-02", "estado": "ABIERTO"}])
        conn.close()

    def test_despues_de_migrar_admite_los_dos_turnos_del_mismo_dia(self):
        conn = self._base_vieja()
        db.migrar(conn)

        conn.execute(
            "INSERT INTO controles_limpieza (fecha, turno, periodo, auditor_id, estado) "
            "VALUES ('2026-06-01', 'TARDE', '2026-06', 1, 'ABIERTO')")
        conn.commit()
        n = conn.execute("SELECT COUNT(*) c FROM controles_limpieza "
                         "WHERE fecha = '2026-06-01'").fetchone()["c"]
        self.assertEqual(n, 2)
        conn.close()

    def test_no_admite_dos_veces_el_mismo_turno(self):
        conn = self._base_vieja()
        db.migrar(conn)

        with self.assertRaises(db.ERRORES_INTEGRIDAD):
            conn.execute(
                "INSERT INTO controles_limpieza (fecha, turno, periodo, auditor_id) "
                "VALUES ('2026-06-01', 'MANANA', '2026-06', 1)")
        conn.close()

    def test_reaplicarla_no_hace_nada(self):
        """Toda migración tiene que poder correrse muchas veces."""
        conn = self._base_vieja()
        db.migrar(conn)
        aplicadas = db.migrar(conn)
        self.assertNotIn("controles_limpieza: alta de turno (mañana/tarde)", aplicadas)
        conn.close()

    def test_las_foreign_keys_quedan_activas(self):
        conn = self._base_vieja()
        db.migrar(conn)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_las_mediciones_los_conservan_su_valor_y_quedan_mensuales(self):
        conn = self._base_vieja()
        db.migrar(conn)

        fila = conn.execute(
            "SELECT fecha, cumple FROM los_mediciones").fetchone()
        self.assertEqual(fila["fecha"], "")
        self.assertEqual(fila["cumple"], 1)
        conn.close()

    def test_despues_de_migrar_un_item_admite_varias_fechas(self):
        """Es la razón de reconstruir la tabla: el UNIQUE viejo lo impedía."""
        conn = self._base_vieja()
        db.migrar(conn)

        for dia in ("2026-06-01", "2026-06-02"):
            conn.execute(
                "INSERT INTO los_mediciones (relevamiento_id, item_clave, fecha, "
                "datos, cumple) VALUES (1, 'iluminacion', ?, '{}', 1)", (dia,))
        conn.commit()
        n = conn.execute("SELECT COUNT(*) c FROM los_mediciones "
                         "WHERE fecha <> ''").fetchone()["c"]
        self.assertEqual(n, 2)
        conn.close()

    def test_no_admite_dos_mediciones_del_mismo_item_y_dia(self):
        conn = self._base_vieja()
        db.migrar(conn)

        conn.execute("INSERT INTO los_mediciones (relevamiento_id, item_clave, "
                     "fecha, datos, cumple) VALUES (1,'iluminacion','2026-06-01','{}',1)")
        with self.assertRaises(db.ERRORES_INTEGRIDAD):
            conn.execute("INSERT INTO los_mediciones (relevamiento_id, item_clave, "
                         "fecha, datos, cumple) "
                         "VALUES (1,'iluminacion','2026-06-01','{}',0)")
        conn.close()
