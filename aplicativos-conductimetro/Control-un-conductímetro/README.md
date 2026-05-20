
# Conductímetro - Control de escritorio

Aplicación PyQt5 para lectura en tiempo real, calibración y monitoreo de conductividad eléctrica (0-10,000 µS) y temperatura, con comunicación serial a ESP32/Arduino y almacenamiento de datos en CSV.

Tecnología: Python 3.7+ | PyQt5 | pyserial | pyqtgraph

---

## Características

- **Lectura tiempo real**: Conductividad y temperatura
- **Dos modos de calibración**: Laboratorio (0-10 mS) y Valores Conocidos
- **Gráficas interactivas**: PyQtGraph con estadísticas en vivo
- **Almacenamiento CSV**: Con rotación por prueba y throttling
- **Control de acceso**: Contraseña protegida para funciones de laboratorio
- **Multi-plataforma**: Windows + Linux con instaladores automáticos
- **Ejecutable compilado**: PyInstaller genera `.exe` standalone

## Requisitos

- Python 3.7+ (recomendado 3.10+)
- Puerto USB para conexión ESP32/Arduino
- Windows 7+ o Linux (Ubuntu 18.04+)

## Instalación rápida

### Windows
```powershell
# Ejecutar instalador automático
.\instalar_windows.bat

# O manual:
py -3 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

### Linux
```bash
chmod +x instalar_linux.sh
./instalar_linux.sh
# O ejecutar después:
./ejecutar_linux.sh
```

### Compilar a ejecutable (Windows)
```powershell
.\compilar_windows.bat
# Genera: dist/conductimetro.exe
```

## Uso

1. Conectar ESP32/Arduino vía USB
2. Ejecutar `python main.py`
3. Seleccionar puerto serial
4. Elegir modo calibración: Laboratorio o Valores Conocidos
5. [Iniciar Lectura] para comenzar adquisición
6. [Descargar CSV] para exportar datos
7. [Gráfica CSV] para visualizar históricos

## Configuración

calibration_ranges.cfg - Archivo de configuración INI con secciones:
- `[RANGES]` - Límites de operación (0-3000 µS)
- `[LABORATORY_CALIBRATION]` - Puntos calibrados (formato: `EC_µS = k_factor, voltage`)
- `[LABORATORY_CALIBRATION_RANGES]` - Rangos autogenerados (formato: `v_min,v_max = k_factor`)
- `[KNOWN_K_TABLE]` - Tabla K personalizada
- `[TEMPERATURE]` - Coeficientes térmicos (coef_temp, tds_factor)
- `[TIMINGS]` - Intervalos (read_interval, uart_interval, etc.)

Regenerar rangos después de editar calibración:
```bash
python generate_lab_ranges.py
```

## Estructura de archivos

| Archivo | Descripción |
|---|---|
| `main.py` | Aplicación principal (PyQt5) - 9 clases de UI |
| `serial_reader.py` | Comunicación serial asíncrona con threading |
| `data_logger.py` | Logger CSV thread-safe con rotación |
| `lab_security.py` | Autenticación y constantes de laboratorio |
| `calibration_ranges.cfg` | Configuración y tablas de calibración (INI) |
| `generate_lab_ranges.py` | Generador de rangos automático |
| `requirements.txt` | Dependencias (PyQt5, pyserial, pyqtgraph, matplotlib, numpy) |
| `instalar_windows.bat` / `instalar_linux.sh` | Instaladores |
| `compilar_windows.bat` | Compilador a ejecutable .exe |
| `ejecutar_linux.sh` | Lanzador Linux |

## Protocolo serial

Puerto: 115200 baud | Formato: JSON

ESP32 → PC:
```json
{"temp": 25.3, "ec": 450.2, "raw_voltage": 0.5432}
```

PC → ESP32:
```
UPDATE_K_TABLE:{json}
UPDATE_CALIBRATION_ALL:{json}
SHOW_EC:{"temp": 25.3, "ec": 450.2}
SET_RANGES:{json}
OLED_CAL_DONE
USE_LABORATORY_CALIBRATION
```

## Arquitectura de calibración

Modelo polinómico:
```
EC(V) = 133.42*V³ - 255.86*V² + 857.39*V  [µS]
EC_calibrado = K × EC(V)
V_25°C = V_medido / (1 + 0.02 × (T - 25))
```

Flujo:
1. Captura N muestras + temperatura
2. Normaliza voltaje a 25°C
3. Calcula K = EC_conocido / EC_polinomio(V_25°C)
4. Regenera rangos automáticamente

## Seguridad

- Contraseña: `L4b0r4t0r10` (SHA-256) en `lab_security.py`
- Protege acceso a calibración de laboratorio
- Cambiar: Editar hash en `lab_security.py`

## Notas técnicas

- Threading: `SerialReader` en thread background; UI siempre responsiva
- CSV: Throttle cada 2s; rotación por nombre de prueba
- Gráficas: PyQtGraph para tiempo real; matplotlib para análisis histórico
- Configuración: INI persistente; cambios se cargan al iniciar app
- Estabilidad: Timeout serie 1s; manejo excepciones en threads

