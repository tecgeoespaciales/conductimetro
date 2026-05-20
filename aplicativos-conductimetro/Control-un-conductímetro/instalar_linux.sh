#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/7] Verificando sistema..."
if ! command -v apt-get >/dev/null 2>&1; then
  echo "Este script está pensado para Debian/Ubuntu (apt-get)."
  echo "Instala manualmente: python3 python3-venv python3-pip"
  exit 1
fi

echo "[2/7] Instalando dependencias del sistema..."
sudo apt-get update
sudo apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  libxcb-xinerama0 \
  libxkbcommon-x11-0 \
  libgl1

echo "[3/7] Verificando archivos del proyecto..."
if [ ! -f "requirements.txt" ]; then
  echo "No se encontró requirements.txt en la carpeta del proyecto."
  exit 1
fi

if [ ! -f "main.py" ]; then
  echo "No se encontró main.py en la carpeta del proyecto."
  exit 1
fi

echo "[4/7] Creando entorno virtual..."
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi

echo "[5/7] Activando entorno..."
source venv/bin/activate

echo "[6/7] Instalando librerías de Python..."
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo "[7/7] Instalación completada."
echo ""
echo "Para uso diario, ejecuta:"
echo "  ./ejecutar_linux.sh"

echo ""
echo "Si usarás puerto serial (ESP32), ejecuta una vez:"
echo "  sudo usermod -aG dialout \$USER"
echo "Luego cierra sesión y vuelve a entrar."
