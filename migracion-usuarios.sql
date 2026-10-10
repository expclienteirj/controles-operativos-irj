-- Ingreso con PIN y tokens de sesión con hash.
--
-- Generado desde app/backend/db.py (`sql_usuarios_postgres`). No editar a mano.
-- Pegar entero en el SQL Editor de Supabase y ejecutar UNA vez, ANTES de
-- publicar el código que lo usa.
--
-- Es seguro repetirlo: la columna usa IF NOT EXISTS y el resto queda registrado
-- en `migraciones_aplicadas`.
--
-- Efecto visible: se cierran las sesiones abiertas (guardaban el token en
-- claro) y cada persona vuelve a ingresar una vez.

BEGIN;

ALTER TABLE usuarios
    ADD COLUMN IF NOT EXISTS credencial TEXT NOT NULL DEFAULT 'password';

CREATE TABLE IF NOT EXISTS migraciones_aplicadas (
    nombre      TEXT PRIMARY KEY,
    aplicada_en TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Como todas las tablas: RLS activado y sin políticas, o sea que nadie la toca
-- por la API pública de Supabase. La app entra por conexión directa, que no
-- pasa por RLS.
ALTER TABLE migraciones_aplicadas ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM migraciones_aplicadas
               WHERE nombre = 'sesiones_con_hash') THEN
        RAISE NOTICE 'Ya estaba aplicada: no se tocó nada.';
        RETURN;
    END IF;

    DELETE FROM sesiones;

    -- El PIN es de cuatro dígitos: el freno pasa de 10 a 5 fallos por cuarto
    -- de hora. Solo si nadie lo tocó, que es cuando vale el default.
    UPDATE config SET valor = '5'
     WHERE clave = 'login_max_intentos' AND valor = '10';

    INSERT INTO migraciones_aplicadas (nombre)
    VALUES ('sesiones_con_hash');
END $$;

COMMIT;
