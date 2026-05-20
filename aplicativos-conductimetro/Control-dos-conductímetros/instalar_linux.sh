#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
APP_NAME="Conductimetro"
DESKTOP_FILE_NAME="conductimetro.desktop"

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

if [ ! -f "run.py" ]; then
  echo "No se encontró run.py en la carpeta del proyecto."
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

echo "[7/7] Creando acceso directo..."
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/$DESKTOP_FILE_NAME" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Aplicación de conductímetro
Exec=bash -lc 'cd "$PROJECT_DIR" && source venv/bin/activate && python run.py'
Path=$PROJECT_DIR
Terminal=false
Categories=Education;Science;
EOF
chmod +x "$HOME/.local/share/applications/$DESKTOP_FILE_NAME"

if [ -d "$HOME/Escritorio" ]; then
  cp "$HOME/.local/share/applications/$DESKTOP_FILE_NAME" "$HOME/Escritorio/$DESKTOP_FILE_NAME"
elif [ -d "$HOME/Desktop" ]; then
  cp "$HOME/.local/share/applications/$DESKTOP_FILE_NAME" "$HOME/Desktop/$DESKTOP_FILE_NAME"
fi

echo "Instalación completada."
echo ""
echo "Acceso directo creado en el menú de aplicaciones."
echo "También puedes abrirlo desde el escritorio si existe la carpeta Escritorio/Desktop."
echo ""
echo "Para uso diario, ejecuta:"
echo "  ./ejecutar_linux.sh"

echo ""
echo "Si usarás puerto serial (ESP32), ejecuta una vez:"
echo "  sudo usermod -aG dialout \$USER"
echo "Luego cierra sesión y vuelve a entrar."
