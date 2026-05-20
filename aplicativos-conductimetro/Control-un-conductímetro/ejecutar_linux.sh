#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
	echo "No existe el entorno virtual. Ejecuta primero: ./instalar_linux.sh"
	exit 1
fi

if [ ! -f "main.py" ]; then
	echo "No se encontró main.py en la carpeta del proyecto."
	exit 1
fi

echo "Activando entorno virtual..."
source venv/bin/activate

echo "Iniciando aplicación..."
python main.py
