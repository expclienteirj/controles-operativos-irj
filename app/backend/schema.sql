-- Esquema — Controles Operativos IRJ
--
-- Convenciones:
--   * Toda la configuración maestra vive acá (no en el frontend).
--   * El inventario físico (4.2) se crea VACÍO: lo carga el admin en el onboarding.
--   * Los controles cerrados son inmutables; solo un admin puede reabrirlos y
--     queda registrado en auditoria_log.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- usuarios --
CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY,
    usuario       TEXT NOT NULL UNIQUE,
    nombre        TEXT NOT NULL,
    password_hash TEXT NOT NULL,       -- PBKDF2-SHA256, ver auth.py
    rol           TEXT NOT NULL CHECK (rol IN ('auditor', 'admin')),
    activo        INTEGER NOT NULL DEFAULT 1,
    creado_en     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sesiones persistidas en la base, no en memoria del proceso.
-- Motivo: la tablet trabaja offline y sincroniza más tarde. Si los tokens
-- vivieran en memoria, cualquier reinicio del servidor (deploy, corte, crash)
-- invalidaría la sesión de todas las tablets y el trabajo encolado quedaría
-- sin poder subirse hasta que alguien volviera a iniciar sesión a mano.
CREATE TABLE IF NOT EXISTS sesiones (
    token      TEXT PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    creado_en  TEXT NOT NULL DEFAULT (datetime('now')),
    ultimo_uso TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------- configuración --
-- Parámetros generales del aeropuerto y umbrales editables (clave/valor JSON).
CREATE TABLE IF NOT EXISTS config (
    clave       TEXT PRIMARY KEY,
    valor       TEXT NOT NULL,          -- JSON
    grupo       TEXT NOT NULL,          -- 'general' | 'los' | 'certificacion'
    descripcion TEXT,
    editable    INTEGER NOT NULL DEFAULT 1,
    actualizado TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ======================================================= MÓDULO LIMPIEZA ====

CREATE TABLE IF NOT EXISTS sectores_limpieza (
    id      INTEGER PRIMARY KEY,
    clave   TEXT NOT NULL UNIQUE,
    nombre  TEXT NOT NULL,
    orden   INTEGER NOT NULL DEFAULT 0,
    activo  INTEGER NOT NULL DEFAULT 1   -- 0 = NO PROCEDE (excluido del cálculo)
);

CREATE TABLE IF NOT EXISTS items_limpieza (
    id         INTEGER PRIMARY KEY,
    sector_id  INTEGER NOT NULL REFERENCES sectores_limpieza(id) ON DELETE CASCADE,
    clave      TEXT NOT NULL,
    nombre     TEXT NOT NULL,
    orden      INTEGER NOT NULL DEFAULT 0,
    activo     INTEGER NOT NULL DEFAULT 1,
    UNIQUE (sector_id, clave)
);

-- Un control = un turno de un día. Se exigen dos recorridas diarias (mañana y
-- tarde) con exactamente el mismo check-list.
--
-- El mes se evalúa sobre los turnos efectivamente auditados: si un día se hizo
-- la mañana y no la tarde, la tarde no computa en el promedio (no cuenta como
-- 0%), pero sí figura como faltante en la completitud, porque las dos son
-- exigibles al auditor. Ver calc.sector_mensual y calc.completitud_mes.
CREATE TABLE IF NOT EXISTS controles_limpieza (
    id          INTEGER PRIMARY KEY,
    fecha       TEXT NOT NULL,           -- 'YYYY-MM-DD'
    turno       TEXT NOT NULL DEFAULT 'MANANA'
                CHECK (turno IN ('MANANA', 'TARDE')),
    periodo     TEXT NOT NULL,           -- 'YYYY-MM', derivado de fecha
    auditor_id  INTEGER NOT NULL REFERENCES usuarios(id),
    iniciado_en TEXT NOT NULL DEFAULT (datetime('now')),
    cerrado_en  TEXT,
    estado      TEXT NOT NULL DEFAULT 'ABIERTO'
                CHECK (estado IN ('ABIERTO', 'CERRADO')),
    UNIQUE (fecha, turno),
    CHECK (periodo = substr(fecha, 1, 7))
);

-- Confirmación explícita del auditor por sector (trazabilidad anti-"todo OK").
-- Sin fila confirmada, el sector es "Sin datos" y NO promedia como 100%.
CREATE TABLE IF NOT EXISTS control_sectores (
    id            INTEGER PRIMARY KEY,
    control_id    INTEGER NOT NULL REFERENCES controles_limpieza(id) ON DELETE CASCADE,
    sector_id     INTEGER NOT NULL REFERENCES sectores_limpieza(id),
    confirmado    INTEGER NOT NULL DEFAULT 0,
    confirmado_en TEXT,
    confirmado_por INTEGER REFERENCES usuarios(id),
    UNIQUE (control_id, sector_id)
);

-- Única acción de carga del auditor: el desvío. Sin filas acá ⇒ 100%.
CREATE TABLE IF NOT EXISTS desvios (
    id          INTEGER PRIMARY KEY,
    control_id  INTEGER NOT NULL REFERENCES controles_limpieza(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL REFERENCES items_limpieza(id),
    estado      TEXT NOT NULL CHECK (estado IN
                  ('DESVIO_PARCIAL', 'DESVIO_TOTAL', 'NO_VERIFICABLE')),
    observacion TEXT NOT NULL,           -- obligatoria (incluye el motivo si es NO_VERIFICABLE)
    creado_en   TEXT NOT NULL DEFAULT (datetime('now')),
    creado_por  INTEGER NOT NULL REFERENCES usuarios(id),
    UNIQUE (control_id, item_id)
);

CREATE TABLE IF NOT EXISTS equipamiento_limpieza (
    id      INTEGER PRIMARY KEY,
    clave   TEXT NOT NULL UNIQUE,
    nombre  TEXT NOT NULL,
    exigido INTEGER NOT NULL DEFAULT 1,
    orden   INTEGER NOT NULL DEFAULT 0
);

-- Por excepción: solo se registran los equipos faltantes / fuera de servicio.
CREATE TABLE IF NOT EXISTS equipamiento_faltante (
    id             INTEGER PRIMARY KEY,
    control_id     INTEGER NOT NULL REFERENCES controles_limpieza(id) ON DELETE CASCADE,
    equipamiento_id INTEGER NOT NULL REFERENCES equipamiento_limpieza(id),
    observacion    TEXT,
    registrado_en  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (control_id, equipamiento_id)
);

-- Baja de una máquina por un tramo de días, cargada desde el control diario.
-- Existe además de equipamiento_faltante porque marcar día por día una máquina
-- rota dos semanas era inviable, y porque un día sin control cerrado hacía
-- desaparecer la baja del cálculo. `hasta` NULL = sigue de baja.
-- Alimenta directamente el ítem 4 de la certificación (merma del pago).
CREATE TABLE IF NOT EXISTS equipamiento_baja (
    id              INTEGER PRIMARY KEY,
    equipamiento_id INTEGER NOT NULL REFERENCES equipamiento_limpieza(id)
                    ON DELETE CASCADE,
    desde           TEXT NOT NULL,          -- 'YYYY-MM-DD' inclusive
    hasta           TEXT,                   -- último día FUERA de servicio (inclusive); NULL = abierta
    motivo          TEXT NOT NULL,
    control_id      INTEGER REFERENCES controles_limpieza(id) ON DELETE SET NULL,
    registrado_por  INTEGER REFERENCES usuarios(id),
    registrado_en   TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (hasta IS NULL OR hasta >= desde)
);

-- Artefacto sanitario clausurado por un tramo de días, cargado desde el
-- control diario. Mismo modelo que equipamiento_baja y por la misma razón: un
-- inodoro fuera de servicio dos semanas no se puede estar marcando catorce
-- veces, y un día sin control cerrado no puede hacerlo desaparecer.
--
-- Vive en el módulo de limpieza y no en LoS porque es una observación de
-- recorrida: el auditor lo ve cuando entra al baño a controlar la limpieza.
-- Alimenta el ítem 3.1.a del manual (artefactos en servicio), que el
-- check-list no puede deducir — un inodoro clausurado se ve igual de limpio.
CREATE TABLE IF NOT EXISTS artefacto_baja (
    id          INTEGER PRIMARY KEY,
    nucleo_id   INTEGER NOT NULL REFERENCES nucleos_sanitarios(id) ON DELETE CASCADE,
    equipo      TEXT NOT NULL,          -- inodoros | mingitorios | bachas
    cantidad    INTEGER NOT NULL DEFAULT 1,
    desde       TEXT NOT NULL,          -- 'YYYY-MM-DD' inclusive
    hasta       TEXT,                   -- último día clausurado (inclusive); NULL = sigue
    motivo      TEXT NOT NULL,
    control_id  INTEGER REFERENCES controles_limpieza(id) ON DELETE SET NULL,
    registrado_por INTEGER REFERENCES usuarios(id),
    registrado_en  TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (cantidad > 0),
    CHECK (hasta IS NULL OR hasta >= desde)
);

-- Qué equipos rigen en cada período. La lista base vive en
-- equipamiento_limpieza (el pliego); acá se confirma mes a mes cuáles se
-- exigen, por si alguno no aplica a ese período. Sin filas para un período,
-- el cálculo cae a los marcados como exigidos en la configuración.
CREATE TABLE IF NOT EXISTS periodo_equipamiento (
    id              INTEGER PRIMARY KEY,
    periodo         TEXT NOT NULL,
    equipamiento_id INTEGER NOT NULL REFERENCES equipamiento_limpieza(id)
                    ON DELETE CASCADE,
    exigido         INTEGER NOT NULL DEFAULT 1,
    UNIQUE (periodo, equipamiento_id)
);

-- ------------------------------------------------------ no conformidades ----
-- Una NC penaliza en el momento en que se releva. Resolverla NO devuelve el
-- punto perdido: el seguimiento sirve para medir el tiempo de resolución del
-- contratista, no para revertir la penalización (ver calc.item_calidad_servicio).
CREATE TABLE IF NOT EXISTS no_conformidades (
    id             INTEGER PRIMARY KEY,
    periodo        TEXT NOT NULL,
    -- Día en que se relevó. Sin esto el arrastre entre auditorías se limitaría
    -- al mes calendario y una NC del día 31 no llegaría al día 1 siguiente.
    fecha_origen   TEXT,
    origen         TEXT NOT NULL CHECK (origen IN ('LIMPIEZA', 'LOS')),
    sector         TEXT,
    item           TEXT,
    descripcion    TEXT NOT NULL,
    prioridad      TEXT CHECK (prioridad IN ('PROGRAMADA', 'INMEDIATA')),
    estado         TEXT NOT NULL DEFAULT 'ABIERTA'
                   CHECK (estado IN ('ABIERTA', 'RESUELTA')),
    desvio_id      INTEGER REFERENCES desvios(id) ON DELETE SET NULL,
    creado_en      TEXT NOT NULL DEFAULT (datetime('now')),
    resuelto_en    TEXT,
    resuelto_por   INTEGER REFERENCES usuarios(id),
    resolucion     TEXT                    -- qué constató el auditor que la cerró
);

-- =================================================== CERTIFICACIÓN (2.3) ====

CREATE TABLE IF NOT EXISTS periodo_datos (
    periodo               TEXT PRIMARY KEY,     -- 'YYYY-MM'
    horas_hombre_programadas  REAL,
    horas_hombre_perdidas     REAL NOT NULL DEFAULT 0,
    -- Los ítems 1 y 2 son binarios: cero hallazgos ⇒ 100%. Para que "nadie los
    -- revisó" no se confunda con "se revisaron y estaban bien", exigen una
    -- confirmación explícita del admin. Sin ella el ítem es Sin datos, igual
    -- que un sector no confirmado en el check-list.
    documentacion_verificada  INTEGER NOT NULL DEFAULT 0,
    hallazgos_documentacion   INTEGER NOT NULL DEFAULT 0,
    ley_19587_verificada      INTEGER NOT NULL DEFAULT 0,
    hallazgos_ley_19587       INTEGER NOT NULL DEFAULT 0,
    -- El ítem 4 (maquinarias) se mide sobre el inventario de equipos, no sobre
    -- horas: ver calc.item_maquinarias. Las columnas de horas máquina se
    -- eliminaron porque no eran exigibles ni medibles.
    monto_adjudicado          REAL,
    cerrado                   INTEGER NOT NULL DEFAULT 0
);

-- Catálogo de insumos con punto de pedido; persiste de mes a mes.
CREATE TABLE IF NOT EXISTS insumos (
    id           INTEGER PRIMARY KEY,
    nombre       TEXT NOT NULL UNIQUE,
    punto_pedido REAL NOT NULL DEFAULT 0,
    unidad       TEXT,
    activo       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS insumo_stock (
    id         INTEGER PRIMARY KEY,
    periodo    TEXT NOT NULL,
    insumo_id  INTEGER NOT NULL REFERENCES insumos(id) ON DELETE CASCADE,
    stock      REAL NOT NULL,
    relevado_en TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (periodo, insumo_id)
);

-- ============================================ INVENTARIO FÍSICO (4.2) ======
-- ⚠ Todas estas tablas nacen VACÍAS. El admin las completa en el onboarding.
-- Mientras estén vacías, los ítems LoS cuantitativos se muestran como
-- "Requiere configuración" y devuelven Sin datos (nunca 100%).

CREATE TABLE IF NOT EXISTS nucleos_sanitarios (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    tipo   TEXT NOT NULL CHECK (tipo IN
             ('DAMAS', 'CABALLEROS', 'PMR', 'RECINTO_BEBES')),
    ubicacion TEXT,
    activo INTEGER NOT NULL DEFAULT 1
);

-- Cantidad instalada por tipo de artefacto/equipo en cada núcleo.
CREATE TABLE IF NOT EXISTS nucleo_equipos (
    id         INTEGER PRIMARY KEY,
    nucleo_id  INTEGER NOT NULL REFERENCES nucleos_sanitarios(id) ON DELETE CASCADE,
    equipo     TEXT NOT NULL,          -- inodoros, mingitorios, bachas, jaboneras,
                                       -- toalleros, cestos, espejos, cambiadores
    instalados INTEGER NOT NULL DEFAULT 0,
    UNIQUE (nucleo_id, equipo)
);

CREATE TABLE IF NOT EXISTS luminarias_sector (
    id        INTEGER PRIMARY KEY,
    sector    TEXT NOT NULL UNIQUE,
    cantidad  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS puertas_embarque (
    id         INTEGER PRIMARY KEY,
    nombre     TEXT NOT NULL UNIQUE,
    php        INTEGER NOT NULL DEFAULT 0,   -- pasajeros hora pico de referencia
    instaladas INTEGER NOT NULL DEFAULT 0    -- tomas instaladas
);

CREATE TABLE IF NOT EXISTS medios_elevacion (
    id          INTEGER PRIMARY KEY,
    nombre      TEXT NOT NULL UNIQUE,
    tipo        TEXT,                        -- ascensor / escalera mecánica / plataforma
    redundancia INTEGER NOT NULL DEFAULT 0,
    activo      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS elevacion_eventos (
    id        INTEGER PRIMARY KEY,
    equipo_id INTEGER NOT NULL REFERENCES medios_elevacion(id) ON DELETE CASCADE,
    periodo   TEXT NOT NULL,
    inicio    TEXT NOT NULL,
    fin       TEXT,
    horas     REAL,                          -- calculado al cerrar el evento
    motivo    TEXT
);

CREATE TABLE IF NOT EXISTS secciones_pavimento (
    id      INTEGER PRIMARY KEY,
    identificador TEXT NOT NULL UNIQUE,
    tipo    TEXT NOT NULL CHECK (tipo IN ('PISTA', 'RODAJE')),
    activo  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS asientos_preembarque (
    id         INTEGER PRIMARY KEY CHECK (id = 1),   -- fila única
    instalados INTEGER NOT NULL DEFAULT 0
);

-- ============================================================ MÓDULO LoS ===

-- periodicidad: con qué frecuencia se releva cada ítem. No todos son
-- diarizables y forzarlos a serlo multiplicaría la carga manual sin agregar
-- información:
--   DIARIO     observación de recorrida (luminarias, infraestructura, asientos,
--              puntos de carga). Se releva por fecha, como el check-list.
--   MENSUAL    medición puntual con instrumental o índice (confort, GEL, PCI).
--   POR_EVENTO se acumula a lo largo del mes (medios de elevación).
--   DERIVADO   se calcula del check-list diario y no se carga a mano
--              (baños, limpieza de terminal).
CREATE TABLE IF NOT EXISTS los_items (
    id      INTEGER PRIMARY KEY,
    clave   TEXT NOT NULL UNIQUE,
    nombre  TEXT NOT NULL,
    orden   INTEGER NOT NULL DEFAULT 0,
    aplica  INTEGER NOT NULL DEFAULT 1,   -- pasarelas: 0 en IRJ
    periodicidad TEXT NOT NULL DEFAULT 'MENSUAL'
                 CHECK (periodicidad IN ('DIARIO', 'MENSUAL',
                                         'POR_EVENTO', 'DERIVADO')),
    requiere_inventario TEXT               -- tabla que debe estar cargada
);

CREATE TABLE IF NOT EXISTS relevamientos_los (
    id         INTEGER PRIMARY KEY,
    periodo    TEXT NOT NULL,
    fecha      TEXT NOT NULL DEFAULT (datetime('now')),
    auditor_id INTEGER NOT NULL REFERENCES usuarios(id),
    estado     TEXT NOT NULL DEFAULT 'ABIERTO'
               CHECK (estado IN ('ABIERTO', 'CERRADO')),
    cerrado_en TEXT
);

-- Una fila por ítem LoS relevado. `datos` guarda el payload específico del
-- ítem en JSON (hallazgos, mediciones, secciones con PCI, etc.) y `resultado`
-- el cálculo que devolvió el motor, para que el informe sea reproducible.
-- `fecha` distingue las mediciones de los ítems DIARIO, que tienen una por día
-- del mes. Los ítems MENSUAL y POR_EVENTO guardan una sola fila con fecha ''
-- (cadena vacía y no NULL: en SQLite dos NULL no colisionan y el UNIQUE dejaría
-- entrar duplicados del mismo ítem mensual).
CREATE TABLE IF NOT EXISTS los_mediciones (
    id              INTEGER PRIMARY KEY,
    relevamiento_id INTEGER NOT NULL REFERENCES relevamientos_los(id) ON DELETE CASCADE,
    item_clave      TEXT NOT NULL REFERENCES los_items(clave),
    fecha           TEXT NOT NULL DEFAULT '',   -- 'YYYY-MM-DD' si es DIARIO
    datos           TEXT NOT NULL,       -- JSON de entrada
    resultado       TEXT,                -- JSON calculado por calc.py
    cumple          INTEGER,             -- 1 / 0 / NULL = sin datos
    observaciones   TEXT,
    creado_en       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (relevamiento_id, item_clave, fecha)
);

-- ================================================================ comunes ==

CREATE TABLE IF NOT EXISTS fotos (
    id          INTEGER PRIMARY KEY,
    entidad     TEXT NOT NULL,           -- 'desvio' | 'los_medicion' | 'no_conformidad'
    entidad_id  INTEGER NOT NULL,
    -- Qué se está fotografiando dentro de la medición. Un ítem LoS tiene
    -- varios sub-ítems (cielorraso, vidrios, puertas…) y sin esto una foto
    -- suelta no dice a cuál corresponde.
    subitem     TEXT,
    archivo     TEXT NOT NULL,           -- ruta relativa en uploads/
    tomada_en   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS firmas (
    id          INTEGER PRIMARY KEY,
    entidad     TEXT NOT NULL,           -- 'control_limpieza' | 'relevamiento_los'
    entidad_id  INTEGER NOT NULL,
    rol_firmante TEXT NOT NULL,          -- 'CONTRATISTA' | 'OPERACIONES_AA'
    nombre      TEXT NOT NULL,
    trazo       TEXT NOT NULL,           -- data URL del canvas de firma
    firmado_en  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS auditoria_log (
    id         INTEGER PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios(id),
    accion     TEXT NOT NULL,
    entidad    TEXT,
    entidad_id INTEGER,
    detalle    TEXT,
    fecha      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Cola de sincronización: la PWA envía cada operación con un uuid propio para
-- que un reintento offline no duplique registros.
CREATE TABLE IF NOT EXISTS sync_operaciones (
    uuid        TEXT PRIMARY KEY,
    recibido_en TEXT NOT NULL DEFAULT (datetime('now')),
    resultado   TEXT
);

-- Intentos de login fallidos, para frenar la prueba de contraseñas.
--
-- Vive en la base y no en memoria porque en serverless cada request corre en
-- un proceso nuevo: un contador en RAM se reiniciaría en cada intento y no
-- frenaría nada. Solo se registran los fallos; un login exitoso borra los del
-- usuario y los de su IP.
CREATE TABLE IF NOT EXISTS intentos_login (
    id      INTEGER PRIMARY KEY,
    clave   TEXT NOT NULL,          -- 'usuario:jperez' | 'ip:190.1.2.3'
    momento TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_intentos_login   ON intentos_login(clave, momento);
CREATE INDEX IF NOT EXISTS idx_controles_periodo ON controles_limpieza(periodo);
CREATE INDEX IF NOT EXISTS idx_desvios_control  ON desvios(control_id);
CREATE INDEX IF NOT EXISTS idx_items_sector     ON items_limpieza(sector_id);
CREATE INDEX IF NOT EXISTS idx_nc_periodo       ON no_conformidades(periodo, estado);
CREATE INDEX IF NOT EXISTS idx_los_med_relev    ON los_mediciones(relevamiento_id);
CREATE INDEX IF NOT EXISTS idx_fotos_entidad    ON fotos(entidad, entidad_id);
CREATE INDEX IF NOT EXISTS idx_elev_periodo     ON elevacion_eventos(periodo);
CREATE INDEX IF NOT EXISTS idx_equip_baja       ON equipamiento_baja(equipamiento_id, desde);
CREATE INDEX IF NOT EXISTS idx_artefacto_baja   ON artefacto_baja(nucleo_id, desde);
