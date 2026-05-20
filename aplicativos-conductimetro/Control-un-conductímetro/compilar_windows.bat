@echo off
setlocal enableextensions

cd /d "%~dp0"
echo ==========================================
echo   COMPILACION WINDOWS - CONDUCTIMETRO
echo ==========================================
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] No se encontro el lanzador de Python ^(py^).
    echo Instala Python 3 para Windows y marca "Add Python to PATH".
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [1/6] Creando entorno virtual...
    py -3 -m venv venv
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
