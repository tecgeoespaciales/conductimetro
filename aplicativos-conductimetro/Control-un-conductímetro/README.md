
# 📊 Conductímetro - Control de Escritorio (EMA)

**Sistema integrado de medición, calibración y monitoreo de conductividad eléctrica y temperatura para laboratorio**

[![Python 3.7+](https://img.shields.io/badge/Python-3.7%2B-blue)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/PyQt5-5.15%2B-green)](https://www.riverbankcomputing.com/software/pyqt/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey)](https://github.com)

---

## 🎯 Descripción General

**Conductímetro - Control de Escritorio** es una aplicación profesional de Python + PyQt5 diseñada para:

- 📈 **Lectura en tiempo real** de conductividad eléctrica (0-10,000 µS) y temperatura (-40 a +125°C)
- 🔧 **Calibración multi-modo**: Laboratorio (0-10 mS), Valores Conocidos personalizado
- 💾 **Captura y almacenamiento** de datos en CSV con marca de tiempo
- 📊 **Gráficas interactivas** con análisis estadístico
- 🔐 **Control de acceso** restringido a funciones de laboratorio (contraseña protegida)
- 🖥️ **Comunicación serial** bidireccional con ESP32/Arduino vía JSON

**Caso de uso**: Laboratorios de análisis de agua, investigación de salinidad, monitoreo de calidad ambiental, procesos industriales de purificación.

---

## ✨ Características Principales

| Característica | Descripción |
|---|---|
| **Interfaz Multi-ventana** | PyQt5 con diseño modular, paneles de control, gráficas integradas |
| **Adquisición Asíncrona** | Threading para lectura no-bloqueante del puerto serial |
| **Normalización Térmica** | Corrección automática de lecturas según temperatura de referencia (25°C) |
| **Tablas K Dinámicas** | Factores de calibración almacenados en `calibration_ranges.cfg` |
| **CSV con Rotación** | Archivos de datos con timestamp, organización por prueba |
| **Análisis Estadístico** | Cálculo de min/máx/promedio/desviación estándar en tiempo real |
| **Regeneración Automática** | Reconstrucción de rangos de calibración al agregar puntos |
| **Ejecutables Compilados** | PyInstaller genera `.exe` para distribución sin Python |
| **Multi-plataforma** | Windows (PowerShell), Linux (Bash) con scripts nativos |
| **Protección por Contraseña** | Acceso restringido a calibración de laboratorio (SHA-256) |

---

## 🏗️ Arquitectura del Proyecto

```
Control-un-conductímetro/
│
├── 🖥️ APLICACIÓN PRINCIPAL
│   ├── main.py                       [~5000 líneas]
│   │   ├── ESP32App (QWidget)         ▶ Ventana principal, UI, timers
│   │   ├── CSVPlotWindow              ▶ Visualización de históricos
│   │   ├── CalibrationInitialDialog   ▶ Selector de modo
│   │   ├── LaboratoryCalibrationDialog ▶ Editor 0-10 mS
│   │   ├── KnownCalibrationDialog     ▶ Calibración personalizada
│   │   └── AnimatedToggle             ▶ UI: Control deslizante
│   │
│   ├── serial_reader.py              [~150 líneas]
│   │   └── SerialReader              ▶ Comunicación asíncrona USB
│   │
│   ├── data_logger.py                [~150 líneas]
│   │   └── DataLogger                ▶ Escritura CSV thread-safe
│   │
│   └── lab_security.py               [~50 líneas]
│       ├── verify_password()          ▶ Autenticación SHA-256
│       └── LAB_CALIBRATION_CONFIG    ▶ Constantes laboratorio
│
├── ⚙️ CONFIGURACIÓN Y DATOS
│   ├── calibration_ranges.cfg        [~300 líneas, INI]
│   │   ├── [RANGES]                  ▶ Límites 0-3000 µS
│   │   ├── [LABORATORY_CALIBRATION]  ▶ Puntos 0-10 mS con K
│   │   ├── [LABORATORY_CALIBRATION_RANGES] ▶ Rangos por voltaje
│   │   ├── [KNOWN_K_TABLE]           ▶ Tabla K "Valores Conocidos"
│   │   └── [TEMPERATURE]             ▶ Coeficientes térmicos
│   │
│   ├── generate_lab_ranges.py        [~150 líneas]
│   │   └── regenerate_laboratory_ranges() ▶ Reconstruir rangos
│   │
│   └── requirements.txt
│       ├── PyQt5>=5.15.0
│       ├── pyserial>=3.5
│       ├── pyqtgraph>=0.12.0
│       ├── matplotlib>=3.5.0
│       └── numpy>=1.20.0
│
├── 🔨 INSTALACIÓN Y COMPILACIÓN
│   ├── instalar_windows.bat          ▶ Setup automático Windows
│   ├── compilar_windows.bat          ▶ PyInstaller → .exe
│   ├── instalar_linux.sh             ▶ Setup automático Linux
│   ├── ejecutar_linux.sh             ▶ Launch Linux
│   │
│   └── INSTALACION_WINDOWS_LINUX.txt ▶ Instrucciones manuales
│
├── 📦 RUNTIME
│   ├── venv/                         ▶ Entorno virtual (generado)
│   │   ├── Scripts/python.exe        ▶ Windows
│   │   └── bin/python                ▶ Linux
│   │
│   ├── datos_*.csv                   ▶ Archivos de muestras (runtime)
│   │
│   └── README.md                     ▶ Documentación
│
└── 🔗 HARDWARE (Externo)
    └── ESP32 / Arduino + Sensores
        ├── Sensor EC (conductividad)
        ├── Sensor Temp (termistor)
        └── Firmware MicroPython
```

---

## 🛠️ Tecnologías Utilizadas

### **Backend**
| Componente | Librería | Versión | Función |
|---|---|---|---|
| GUI | PyQt5 | ≥5.15.0 | Interfaz gráfica multiplataforma |
| Serial | pyserial | ≥3.5 | Comunicación USB con ESP32 |
| Gráficas | pyqtgraph | ≥0.12.0 | Plots interactivos en tiempo real |
| Estadística | matplotlib + numpy | ≥3.5.0, ≥1.20.0 | Análisis de datos históricos |
| Configuración | configparser | Stdlib | Lectura/escritura INI |
| Threading | threading | Stdlib | Ejecución asíncrona |
| Seguridad | hashlib | Stdlib | Hash SHA-256 |

### **Distribución**
| Herramienta | Propósito |
|---|---|
| PyInstaller | Empaquetado → ejecutable standalone .exe |
| setuptools | Gestión de dependencias |
| pip | Instalador de paquetes Python |

### **Protocolos**
- **Serial**: RS-232 @ 115200 baud
- **Datos**: JSON (comunicación ESP32 ↔ PC)
- **Configuración**: INI (configparser Python)

---

## 📋 Requisitos

### **Hardware Mínimo**
- **PC/Laptop**: Windows 7+ o Linux (Ubuntu 18.04+)
- **Puerto USB**: Para conexión con ESP32/Arduino
- **ESP32 o Arduino**: Con sensor de conductividad + termistor
- **Sensor EC**: Sonda de conductividad 0-10 mS (ej: Analog Devices ADS1256)

### **Software**
- **Python**: 3.7+ (recomendado 3.10+)
- **pip**: Gestor de paquetes
- **Permisos**: Acceso a puerto serial (sin sudo en Windows, con grupo `dialout` en Linux)

### **Verificación Pre-instalación**

**Windows**:
```powershell
py --version                    # Debe ser 3.7+
where pip                       # Debe existir
```

**Linux**:
```bash
python3 --version               # Debe ser 3.7+
which pip3                      # Debe existir
```

---

## 🚀 Instalación

### **Opción A: Windows (Automático)**

```powershell
# 1. Descargar proyecto
git clone <repo-url>
cd Control-un-conductimetro

# 2. Ejecutar instalador
.\instalar_windows.bat

# 3. Verificar éxito (creó venv/ e instaló dependencias)
dir venv\Scripts\python.exe  # Debe existir
```

**¿Qué hace `instalar_windows.bat`?**
1. Verifica Python disponible
2. Crea entorno virtual `venv/`
3. Actualiza pip/setuptools
4. Instala dependencias desde `requirements.txt`
5. Muestra confirmación

### **Opción B: Windows (Manual)**

```powershell
# En PowerShell (como administrador si es primera vez)
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned  # Solo 1ª vez

# Crear venv
py -3 -m venv venv

# Activar
venv\Scripts\Activate.ps1

# Instalar
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# Verificar
pip list | grep PyQt5  # Debe listarse
```

### **Opción C: Linux (Automático)**

```bash
# Descargar proyecto
git clone <repo-url>
cd Control-un-conductimetro

# Dar permisos de ejecución
chmod +x instalar_linux.sh

# Ejecutar instalador (pedirá sudo)
./instalar_linux.sh
```

**¿Qué hace `instalar_linux.sh`?**
1. Verifica Ubuntu/Debian (apt-get)
2. Instala dependencias del sistema: python3, libgl1, libxkbcommon-x11-0, etc.
3. Crea venv
4. Instala pip packages
5. Genera `ejecutar_linux.sh`

### **Opción D: Linux (Manual)**

```bash
# Dependencias del sistema
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip \
  libxcb-xinerama0 libxkbcommon-x11-0 libgl1

# Crear venv
python3 -m venv venv
source venv/bin/activate

# Instalar Python packages
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# Verificar
pip list | grep PyQt5
```

---

## 🎮 Uso del Proyecto

### **Ejecución Normal**

**Windows**:
```powershell
# Activar venv (si no está activo)
venv\Scripts\Activate.ps1

# Ejecutar
python main.py

# O directamente desde cmd
cd venv\Scripts & Activate.bat & cd ..\.. & python main.py
```

**Linux**:
```bash
# Opción 1: Ejecutar script
./ejecutar_linux.sh

# Opción 2: Manual
source venv/bin/activate
python main.py
```

### **Compilar a Ejecutable (Windows)**

```powershell
# Instalar PyInstaller
pip install pyinstaller

# Ejecutar compilador
.\compilar_windows.bat

# Resultado
# → dist\conductimetro.exe  (ejecutable standalone, ~120-150 MB)
```

**Distribución**: Copiar `dist/conductimetro.exe` a otro PC sin Python; ejecutar directamente.

---

## 📖 Flujo de Operación Típica

### **Escenario: Calibración y Medición**

```
STARTUP
   ├─ Cargar config desde calibration_ranges.cfg
   ├─ Verificar/Regenerar rangos de laboratorio
   └─ Mostrar pantalla de selección de puerto
        │
        ├─ [Puerto COM seleccionado]
        │
        ▼
   DIALOG: "Seleccione Modo de Calibración"
   ┌──────────────────────────────────────┐
   │ A) Calibración de Escala (V. Conocidos)
   │ B) Continuar sin Calibración (Laboratorio)
   └──────────────────────────────────────┘
        │
        ├─ [Opción A] → KnownCalibrationDialog
        │   ├─ 1. Ingresar soluciones: 100, 500, 1000 µS
        │   ├─ 2. "Medir" cada solución (captura N muestras)
        │   ├─ 3. Sistema calcula K para cada punto
        │   ├─ 4. Guardar calibración personalizada
        │   └─ → Ir a MONITOR
        │
        ├─ [Opción B] → Usa LABORATORY_CALIBRATION_RANGES
        │   ├─ [Requiere contraseña si acceso restringido]
        │   └─ → Ir a MONITOR
        │
        ▼
   MONITOR (Pantalla Principal)
   ┌──────────────────────────────────────┐
   │ Estado: ✓ Conectado                  │
   │                                      │
   │ CONDUCTIVIDAD: 450.2 µS              │
   │ TEMPERATURA:    25.3 °C              │
   │ FACTOR K:        1.23                │
   │                                      │
   │ [Iniciar] [Detener] [Descargar]     │
   └──────────────────────────────────────┘
        │
        ├─ [Iniciar Lectura]
        │   ├─ Timer comienza (intervalo 2s)
        │   ├─ Lee ESP32 cada tick
        │   ├─ Actualiza gráficas en tiempo real
        │   ├─ Escribe CSV c/ throttling (cada 2s)
        │   └─ Estadísticas en vivo
        │
        ├─ [Detener Lectura]
        │   └─ Pausa adquisición (sin cerrar conexión)
        │
        ├─ [Descargar CSV]
        │   ├─ File dialog → Guardar como
        │   ├─ Archivo: datos_Mi_Prueba_20260317_123045.csv
        │   └─ Contiene: Fecha, Hora, EC, Temp, Nombre_Prueba
        │
        └─ [Gráfica CSV]
            ├─ Carga datos de archivo histórico
            ├─ Plotea EC vs tiempo, Temp vs tiempo
            └─ Muestra tabla de estadísticas
```

### **Comando a Nivel de Usuario**

| Acción | Pasos |
|---|---|
| **Iniciar medición** | Puerto → Modo Calibración → [Iniciar Lectura] |
| **Pausar lectura** | [Detener Lectura] |
| **Cambiar intervalo** | Spinner "Intervalo (s)" → [Aplicar] |
| **Recalibrar** | [Actualizar Calibración] → Dialog |
| **Guardar datos** | [Descargar CSV] → nombrearchivo.csv |
| **Ver gráfica histórica** | [Gráfica CSV] → Seleccionar CSV anterior |
| **Resetear fábrica** | [Restablecer valores de fábrica] (requiere confirmación) |

---

## ⚙️ Configuración Detallada

### **calibration_ranges.cfg**

#### **[RANGES]** — Límites de operación
```ini
[RANGES]
max_range = 3000.0                # Máximo rango conductividad (µS)
increment = 5.0                   # Granularidad de incremento
```

#### **[CALIBRATION_POINTS]** — Puntos recomendados
```ini
[CALIBRATION_POINTS]
recommended_points = 100, 300, 500, 750, 1000, 1500, 2000, 2500, 3000
```

#### **[TEMPERATURE]** — Coeficientes termométricos
```ini
[TEMPERATURE]
coef_temp = 0.020                 # Coef. normalización: (T - 25°C) × 0.02
tds_factor = 0.64                 # Factor TDS (Total Dissolved Solids)
```

#### **[LABORATORY_CALIBRATION]** — Tabla de laboratorio (0-10 mS)
Formato: `conductividad_conocida_µS = k_factor, voltage_a_25C`

```ini
[LABORATORY_CALIBRATION]
49 = 0.858724,0.069094            # 49 µS/cm: K=0.858724, V@25°C=0.069094V
80 = 0.6811442,0.142602           # 80 µS/cm: K=0.681144, V@25°C=0.142602V
308 = 1.0248965,0.386020          # 308 µS/cm
1413 = 1.2636213,1.394492         # 1.413 mS/cm
```

**Edición manual**: Agregar nuevos puntos respetando formato; luego ejecutar:
```bash
python generate_lab_ranges.py
```

#### **[LABORATORY_CALIBRATION_RANGES]** — Rangos generados automáticamente
Formato: `voltage_min, voltage_max = k_factor`

```ini
[LABORATORY_CALIBRATION_RANGES]
0.000000,0.105848 = 0.858724      # Si V ∈ [0, 0.1058], usar K=0.858724
0.105848,0.186937 = 0.681144      # Si V ∈ [0.1058, 0.1869], usar K=0.681144
1.204168,1.662699 = 1.263621      # ...
```

**Generación**: Automática al finalizar calibración de laboratorio; manual con:
```bash
python generate_lab_ranges.py
```

#### **[KNOWN_K_TABLE]** — Tabla personalizada (Valores Conocidos)
```ini
[KNOWN_K_TABLE]
2.375644 = 1.25961843             # V=2.3756V → K=1.2596
1.507775 = 1.21957600             # V=1.5078V → K=1.2196
0.115391 = 0.90794200             # V=0.1154V → K=0.9079
```

#### **[SENSING]** — Parámetros del sensor ADC
```ini
[SENSING]
ads_channel = 2                   # Canal ADS1256
adc_avg_samples = 5               # Promedio de N muestras por lectura
cal_capture_samples = 40          # Muestras para calibración
adc_avg_sleep_ms = 1              # Delay entre muestras (ms)
noise_threshold = 0.0             # Umbral de ruido (µV)
ema_scale = 1000.0                # Escala EMA (filtro exponencial)
```

#### **[TIMINGS]** — Intervalos de tiempo
```ini
[TIMINGS]
read_interval = 0.1               # Intervalo lectura ADC (s)
uart_interval = 1.0               # Intervalo envío UART (s)
temp_conv_time = 0.75             # Tiempo conversión temperatura (s)
temp_offset = 0.2                 # Offset de temperatura (°C)
```

---

## 📡 Protocolo de Comunicación Serial

### **Configuración Física**
- **Puerto**: COM[N] (Windows) o /dev/ttyUSB[N] (Linux)
- **Baudrate**: 115200 bps
- **Timeout**: 1 segundo

### **Formato de Datos**

**ESP32 → PC (Recepción)**:
```json
{"temp": 25.3, "ec": 450.2, "raw_voltage": 0.5432}
```
- `temp`: Temperatura en °C (float)
- `ec`: Conductividad en µS (float)
- `raw_voltage`: Voltaje bruto del ADC (float, 0-10V)

**PC → ESP32 (Envío)**:

```
UPDATE_K_TABLE:{k_table_json}
{
  "100": 1.234,
  "500": 1.256,
  "1000": 1.289
}

UPDATE_CALIBRATION_ALL:{calibration_json}
{
  "poly_a": 133.42,
  "poly_b": 255.86,
  "poly_c": 857.39,
  "k_table": {...}
}

SHOW_EC:{"temp": 25.3, "ec": 450.2}
SET_RANGES:[{"v_min": 0, "v_max": 0.5, "k": 0.858}, ...]
OLED_CAL_DONE
USE_LABORATORY_CALIBRATION
```

---

## 🔍 Descripción Detallada de Archivos

### **main.py** (Aplicación Principal)
| Aspecto | Detalles |
|---|---|
| **Líneas** | ~5000+ |
| **Clases** | 9 (ESP32App, 5+ Dialogs, 2 Widgets) |
| **Threading** | 1 (SerialReader en background) |

**Clases principales**:
1. **ESP32App** - Ventana principal, timers, manejo eventos, gráficas tiempo real
2. **CSVPlotWindow** - Visualización históricos con estadísticas HTML
3. **CalibrationInitialDialog** - Selector entre "Valores Conocidos" y "Laboratorio"
4. **LaboratoryCalibrationDialog** - Editor tabla 0-10 mS con captura automática
5. **KnownCalibrationDialog** - Calibración personalizada
6. **LaboratoryPasswordDialog** - Validación contraseña
7. **AnimatedToggle** - Control deslizante animado

**Funciones globales**:
- `normalize_voltage_to_reference_temp()` - Normaliza V a 25°C
- `calculate_ec_from_voltage()` - Calcula EC = P(V) × K
- `load_k_table_from_cfg()` - Carga tabla K desde config
- `send_show_ec_to_esp32()` - Envía SHOW_EC JSON

### **serial_reader.py** (Comunicación Serial)
**Líneas**: ~150 | **Clase**: SerialReader
- Lectura no bloqueante con threading
- Métodos: `start()`, `read_data()`, `send_command()`, `stop()`
- Callback por línea recibida
- Mutex (threading.Lock) para envíos sincronizados

### **data_logger.py** (Logging CSV)
**Líneas**: ~150 | **Clase**: DataLogger
- Thread-safe escritura CSV
- Rotación por nombre de prueba
- Throttling de escritura (intervalo configurable)
- Estructura CSV: Fecha, Hora, Sensor, Temperatura, Nombre_Prueba

### **lab_security.py** (Autenticación)
**Líneas**: ~50
- Verificación contraseña SHA-256
- Contraseña por defecto: `L4b0r4t0r10`
- Constantes `LAB_CALIBRATION_CONFIG`

### **calibration_ranges.cfg** (Configuración)
**Formato**: INI | **Secciones**: 8+ (RANGES, LABORATORY_CALIBRATION, etc.)

### **generate_lab_ranges.py** (Regenerador)
Lee `[LABORATORY_CALIBRATION]`, genera `[LABORATORY_CALIBRATION_RANGES]` calculando rangos automáticamente.

```bash
python generate_lab_ranges.py
```

### **Scripts de Instalación/Compilación**
- `instalar_windows.bat` - Setup automático Windows
- `compilar_windows.bat` - PyInstaller → .exe
- `instalar_linux.sh` - Setup automático Linux
- `ejecutar_linux.sh` - Launch Linux

---

## 🐛 Problemas Detectados (Auditoría)

### **Críticos**
1. **Contraseña en código fuente** - `lab_security.py`: Contraseña visible en comentario
2. **Falta de reconexión serial** - Si desconecta USB, no se reconecta automáticamente
3. **Sin tests unitarios** - Imposible validar cambios de forma segura

### **Altos**
4. **Falta de validación entrada** - Campos numéricos sin rango (ej: -100 µS)
5. **Manejo excepciones en threads** - Las excepciones mueren silenciosamente

### **Medios**
6. **Configuración hardcodeada** - POLY_A, POLY_B en código, no en config
7. **Sin logging de auditoría** - Imposible auditar cambios de calibración
8. **Falta cleanup de recursos** - No hay `closeEvent()` en ESP32App
9. **Sin límites de archivo CSV** - Pueden crecer indefinidamente

### **Bajos**
10. **Sin type hints** - Dificulta mantenimiento y detección IDE
11. **Código duplicado en dialogs** - Captura/procesamiento repetido
12. **Dependencias muy permisivas** - `>=` en requirements.txt

---

## 📊 Resumen de Calidad

| Métrica | Evaluación | Notas |
|---|---|---|
| Arquitectura | ⭐⭐⭐⭐ | Separación clara: UI, Serial, Logger |
| Documentación | ⭐⭐⭐ | Docstrings en clases; falta en funciones |
| Manejo Errores | ⭐⭐⭐ | Try-except básicos; falta global |
| Testing | ⭐ CRÍTICO | Sin pruebas unitarias |
| Seguridad | ⭐⭐ CRÍTICO | Contraseña visible; sin validación |
| Portabilidad | ⭐⭐⭐⭐ | Scripts Windows/Linux; ejecutable |
| Performance | ⭐⭐⭐⭐ | Threading correcto; PyQtGraph eficiente |

---

## 🎓 Mejoras Futuras (Roadmap)

**Fase 1**: Logging auditoría, validación entrada, reconexión automática
**Fase 2**: Tests pytest, type hints, GitHub Actions CI/CD
**Fase 3**: WiFi remoto, base de datos SQLite, exportación Excel
**Fase 4**: Dashboard web, i18n (ES/EN/PT), interfaz configuración avanzada
**Fase 5**: API REST, integración LIMS, app móvil

---

## 📜 Licencia

Actualmente sin especificar. Recomendado: MIT (permisivo, uso comercial libre)

---

**Documento generado**: Marzo 2026 | **Estado**: Producción-ready con mejoras pendientes

