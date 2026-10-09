-- Marcas de tiempo en hora de Argentina (UTC-3).
--
-- Generado desde app/backend/db.py (`sql_hora_local_postgres`). No editar a mano.
-- Pegar entero en el SQL Editor de Supabase y ejecutar UNA vez.
--
-- Es seguro repetirlo: si la migración ya quedó registrada, no corrige nada.
-- Lo que sí hace siempre es reasegurar la función `datetime()`, que es
-- idempotente.

BEGIN;

CREATE OR REPLACE FUNCTION datetime(momento text)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT to_char(
        CASE WHEN momento = 'now' THEN (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')
             ELSE momento::timestamp END,
        'YYYY-MM-DD HH24:MI:SS')
$$;

CREATE OR REPLACE FUNCTION datetime(momento text, modificador text)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT to_char(
        (CASE WHEN momento = 'now' THEN (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')
              ELSE momento::timestamp END) + modificador::interval,
        'YYYY-MM-DD HH24:MI:SS')
$$;

CREATE TABLE IF NOT EXISTS migraciones_aplicadas (
    nombre      TEXT PRIMARY KEY,
    aplicada_en TEXT NOT NULL DEFAULT (datetime('now'))
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM migraciones_aplicadas
               WHERE nombre = 'marcas_en_hora_local') THEN
        RAISE NOTICE 'Ya estaba aplicada: no se corrigió ninguna fila.';
        RETURN;
    END IF;

    UPDATE usuarios SET creado_en = to_char(creado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(creado_en) = 19;
    UPDATE sesiones SET creado_en = to_char(creado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(creado_en) = 19;
    UPDATE sesiones SET ultimo_uso = to_char(ultimo_uso::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(ultimo_uso) = 19;
    UPDATE config SET actualizado = to_char(actualizado::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(actualizado) = 19;
    UPDATE controles_limpieza SET iniciado_en = to_char(iniciado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(iniciado_en) = 19;
    UPDATE controles_limpieza SET cerrado_en = to_char(cerrado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(cerrado_en) = 19;
    UPDATE control_sectores SET confirmado_en = to_char(confirmado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(confirmado_en) = 19;
    UPDATE desvios SET creado_en = to_char(creado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(creado_en) = 19;
    UPDATE equipamiento_faltante SET registrado_en = to_char(registrado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(registrado_en) = 19;
    UPDATE equipamiento_baja SET registrado_en = to_char(registrado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(registrado_en) = 19;
    UPDATE artefacto_baja SET registrado_en = to_char(registrado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(registrado_en) = 19;
    UPDATE no_conformidades SET creado_en = to_char(creado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(creado_en) = 19;
    UPDATE no_conformidades SET resuelto_en = to_char(resuelto_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(resuelto_en) = 19;
    UPDATE insumo_stock SET relevado_en = to_char(relevado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(relevado_en) = 19;
    UPDATE relevamientos_los SET fecha = to_char(fecha::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(fecha) = 19;
    UPDATE relevamientos_los SET cerrado_en = to_char(cerrado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(cerrado_en) = 19;
    UPDATE los_mediciones SET creado_en = to_char(creado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(creado_en) = 19;
    UPDATE fotos SET tomada_en = to_char(tomada_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(tomada_en) = 19;
    UPDATE firmas SET firmado_en = to_char(firmado_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(firmado_en) = 19;
    UPDATE auditoria_log SET fecha = to_char(fecha::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(fecha) = 19;
    UPDATE sync_operaciones SET recibido_en = to_char(recibido_en::timestamp - interval '3 hours', 'YYYY-MM-DD HH24:MI:SS') WHERE length(recibido_en) = 19;

    DELETE FROM intentos_login;

    INSERT INTO migraciones_aplicadas (nombre) VALUES ('marcas_en_hora_local');
END $$;

COMMIT;
