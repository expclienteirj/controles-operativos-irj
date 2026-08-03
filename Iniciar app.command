#!/bin/bash
# Arranca la app "Controles Operativos IRJ".
# Doble clic en este archivo desde el Finder.
# Para detenerla: cerrá esta ventana de Terminal o apretá Ctrl+C.

cd "$(dirname "$0")/app/backend" || exit 1

echo "════════════════════════════════════════════════════════"
echo "  CONTROLES OPERATIVOS IRJ"
echo "════════════════════════════════════════════════════════"
echo

# IP de esta computadora en la red local, para entrar desde la tablet.
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)

echo "  En esta computadora:   http://localhost:8080"
if [ -n "$IP" ]; then
  echo "  Desde la tablet:       http://$IP:8080"
  echo "                         (la tablet tiene que estar en la misma red wifi)"
else
  echo "  Sin red wifi detectada: solo funciona en esta computadora."
fi
echo
echo "  Para detener: cerrá esta ventana o apretá Ctrl+C"
echo "════════════════════════════════════════════════════════"
echo

PORT=8080 python3 api.py
