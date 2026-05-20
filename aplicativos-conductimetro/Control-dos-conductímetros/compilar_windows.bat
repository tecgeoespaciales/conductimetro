@echo off
setlocal enableextensions

cd /d "%~dp0"
echo ==========================================
echo   COMPILACION WINDOWS - CONDUCTIMETRO
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

if not exist "venv\Scripts\python.exe" (
    echo [1/6] Creando entorno virtual...
    %PY_LAUNCHER% -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo [1/6] Entorno virtual encontrado.
)

echo [2/6] Activando entorno virtual...
call "venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] No se pudo activar el entorno virtual.
    pause
    exit /b 1
)

echo [3/6] Actualizando herramientas base...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [ERROR] Fallo al actualizar pip/setuptools/wheel.
    pause
    exit /b 1
)

echo [4/6] Instalando dependencias y PyInstaller...
if not exist "requirements.txt" (
    echo [ERROR] No se encontro requirements.txt
    pause
    exit /b 1
)

python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [ERROR] Fallo instalando dependencias.
    pause
    exit /b 1
)

echo [5/6] Compilando ejecutable...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "Conductimetro.spec" del /q "Conductimetro.spec"

pyinstaller --noconfirm --clean --windowed --name "Conductimetro" main.py
if errorlevel 1 (
    echo [ERROR] La compilacion con PyInstaller fallo.
    pause
    exit /b 1
)

echo [6/6] Copiando archivos necesarios...
if exist "calibration_ranges.cfg" copy /y "calibration_ranges.cfg" "dist\Conductimetro\calibration_ranges.cfg" >nul
if exist "images" xcopy "images" "dist\Conductimetro\images" /e /i /y >nul

echo.
echo ==========================================
echo   LISTO
echo ==========================================
echo Ejecutable generado en:
echo   dist\Conductimetro\Conductimetro.exe
echo.
pause
exit /b 0
