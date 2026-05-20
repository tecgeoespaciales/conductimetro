# Aplicativos - Conductímetro

Conjunto de aplicaciones de escritorio basadas en **PyQt5** para monitoreo, lectura en tiempo real y calibración de conductividad eléctrica en sistemas con ESP32/Arduino.

---

## Estructura de carpetas

### 1. **Control-un-conductímetro**
Aplicación para monitoreo de **un único conductímetro**.

**Características:**
- Lectura en tiempo real de conductividad (0-10,000 µS) y temperatura
- Dos modos de calibración: Laboratorio y Valores Conocidos
- Gráficas interactivas con PyQtGraph
- Almacenamiento de datos en CSV con rotación por prueba
- Control de acceso protegido con contraseña
- Instaladores automáticos para Windows y Linux
- Compilación a ejecutable `.exe` standalone (Windows)

**Requisitos:**
- Python 3.7+ (recomendado 3.10+)
- Puerto USB para conexión ESP32/Arduino
- Windows 7+ o Linux (Ubuntu 18.04+)

**Instalación rápida:**

```bash
# Windows
.\instalar_windows.bat

# Linux
chmod +x instalar_linux.sh
./instalar_linux.sh
```

---

### 2. **Control-dos-conductímetros**
Aplicación para monitoreo de **dos conductímetros simultáneamente**.

**Características:**
- Lectura simultánea de dos dispositivos ESP32/Arduino
- Interfaz de dos paneles para visualización comparativa
- Calibración independiente para cada conductímetro
- Compensación de temperatura normalizada a 25°C
- Gráficas en tiempo real con pyqtgraph
- Registro de datos en CSV con timestamp
- Comunicación serial asíncrona
- Monitoreo automático de puertos disponibles

**Requisitos:**
- Python 3.7+
- Dos puertos USB disponibles
- PyQt5, pyserial, pyqtgraph, matplotlib, numpy

**Instalación:**

```bash
# Windows
.\instalar_windows.bat

# Linux
chmod +x instalar_linux.sh
./instalar_linux.sh
```

---

## Uso general

### Ejecutar aplicación

```bash
# Control de un conductímetro
python Control-un-conductimetro/main.py

# Control de dos conductímetros
python Control-dos-conductimetros/main.py
```

### Como módulo

```bash
# Control de un conductímetro
python -m Control-un-conductimetro

# Control de dos conductímetros
python -m Control-dos-conductimetros
```

### Compilar a ejecutable (Windows)

```powershell
# En la carpeta correspondiente
.\compilar_windows.bat

# Genera: dist/conductimetro.exe
```

---

## Archivos comunes en ambas aplicaciones

| Archivo | Descripción |
|---------|-------------|
| `main.py` | Interfaz principal (PyQt5) |
| `__main__.py` | Punto de entrada del módulo |
| `serial_reader.py` | Gestión de puerto serial asíncrono |
| `data_logger.py` | Registro de datos en CSV |
| `lab_security.py` | Autenticación y configuración de laboratorio |
| `calibration_ranges.cfg` | Archivo de configuración INI |
| `requirements.txt` | Dependencias Python |
| `instalar_windows.bat` | Instalador automático (Windows) |
| `instalar_linux.sh` | Instalador automático (Linux) |
| `compilar_windows.bat` | Compilador a ejecutable (Windows) |

---

## Configuración

El archivo `calibration_ranges.cfg` contiene:

- `[RANGES]` - Límites de operación (0-10,000 µS)
- `[LABORATORY_CALIBRATION]` - Puntos calibrados
- `[LABORATORY_CALIBRATION_RANGES]` - Rangos autogenerados
- `[KNOWN_K_TABLE]` - Tabla K personalizada
- `[TEMPERATURE]` - Coeficientes térmicos
- `[TIMINGS]` - Intervalos de lectura

**regenerar rangos automáticamente:**

```bash
# Solo disponible en Control-un-conductimetro
cd Control-un-conductimetro
python generate_lab_ranges.py
```

---

## Seguridad

- **Contraseña por defecto:** `L4b0r4t0r10`
- Funciones de calibración protegidas con contraseña
- Autenticación con SHA-256

---

## Protocolo serial

- **Puerto:** 115200 baud
- **Formato:** JSON

**Ejemplo de mensaje ESP32 → PC:**

```json
{"temp": 25.3, "ec": 450.2, "raw_voltage": 0.5432}
```

---

## Dependencias Python

```
PyQt5>=5.15.0
pyserial>=3.5
pyqtgraph>=0.12.0
matplotlib>=3.5.0
numpy>=1.20.0
```

Instala automáticamente con:

```bash
pip install -r requirements.txt
```

---

## Notas

- La **compensación de temperatura** normaliza las mediciones a 25°C con coeficiente 0.02
- Los cálculos de conductividad utilizan polinomios de 3er grado
- El sistema soporta **calibración multirrango** para mayor precisión
- Los datos se guardan en CSV con timestamp automático

---

## Soporte

Para problemas de conexión serial o instalación, consulta los archivos:
- `INSTALACION_WINDOWS_LINUX.txt` en cada subcarpeta
- `requirements.txt` para verificar dependencias
