"""Punto de entrada de la API en Vercel.

Vercel acepta como función un `handler` que subclase `BaseHTTPRequestHandler`,
que es exactamente lo que ya es `api.Handler`. Por eso el adaptador es esto y
nada más: no hay que reescribir el router ni los 73 endpoints, que siguen
viviendo en `app/backend/api.py` y corriendo igual en la Mac con `http.server`.

Lo único que cambia respecto del servidor propio: los archivos del frontend los
sirve el CDN de Vercel (ver `vercel.json`), así que esta función solo debería
recibir rutas `/api/...`. Si le llega otra cosa es un error de ruteo, y
responder 404 es preferible a leer del disco de la función.

Configuración por variables de entorno, en el panel de Vercel:

    IRJ_DB_URL            cadena de Postgres de Supabase — usar el POOLER
                          (puerto 6543, modo transaction). Cada invocación abre
                          su propia conexión: contra el puerto directo se agota
                          el límite de conexiones del proyecto.
    SUPABASE_URL          https://<proyecto>.supabase.co
    SUPABASE_SERVICE_KEY  clave de servicio. Nunca en el frontend.
    SUPABASE_BUCKET       bucket de la evidencia (default: 'evidencia')
"""

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "backend"))

import api  # noqa: E402


class handler(api.Handler):
    """La API tal cual, con el servido de estáticos desactivado."""

    def _servir_estatico(self, camino):
        self._responder(404, {"error": f"Ruta no encontrada: {camino}"})
