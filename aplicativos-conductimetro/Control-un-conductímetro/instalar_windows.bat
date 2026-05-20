@echo off
setlocal enableextensions

cd /d "%~dp0"
echo ==========================================
echo   INSTALACION WINDOWS - CONDUCTIMETRO
echo ==========================================
echo.

set "PY_LAUNCHER="
where py >nul 2>nul && set "PY_LAUNCHER=py -3"
if not defined PY_LAUNCHER (
    where python >nul 2>nul && set "PY_LAUNCHER=python"
)

if not defined PY_LAUNCHER (
    echo [INFO] Python no esta instalado. Intentando instalar con winget...
    where winget >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] No se encontro winget para instalar Python automaticamente.
        echo Instala Python 3.11+ manualmente y vuelve a ejecutar este script.
        pause
        exit /b 1
    )

    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [ERROR] No se pudo instalar Python con winget.
        echo Instala Python manualmente y vuelve a ejecutar este script.
        pause
        exit /b 1
    )

    set "PY_LAUNCHER=py -3"
)

if not exist "requirements.txt" (
    echo [ERROR] No se encontro requirements.txt
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [1/5] Creando entorno virtual...
    %PY_LAUNCHER% -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo [1/5] Entorno virtual encontrado.
)

echo [2/5] Activando entorno virtual...
call "venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] No se pudo activar el entorno virtual.
    pause
    exit /b 1
)

echo [3/5] Actualizando pip...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [ERROR] Fallo actualizando pip.
    pause
    exit /b 1
)

echo [4/5] Instalando dependencias de la aplicacion...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Fallo instalando dependencias.
    pause
    exit /b 1
)

echo [5/5] Listo. Puedes ejecutar la app con:
echo   venv\Scripts\python.exe main.py

echo.
echo Para uso diario puedes ejecutar tambien:
echo   python main.py
echo ^(dentro del entorno virtual activado^)
echo.
pause
exit /b 0
