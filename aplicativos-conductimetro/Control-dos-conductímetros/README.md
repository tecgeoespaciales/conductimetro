# Aplicación - Conductímetro GUI

Interfaz gráfica de escritorio (PyQt5) para monitoreo y calibración de un conductímetro digital basado en ESP32. La aplicación realiza cálculos de conductividad eléctrica en tiempo real, gestiona calibraciones de laboratorio y registra datos en CSV.

---

## Características principales

- **Lectura en Tiempo Real**: Comunicación serial asíncrona con dispositivo ESP32
- **Cálculo de Conductividad**: Conversión de voltaje a µS mediante polinomios y tablas de calibración
- **Compensación de Temperatura**: Normalización de mediciones según temperatura ambiente
- **Calibración de Laboratorio**: Función protegida con contraseña para calibración multirrango
- **Registro de Datos**: Exportación automática a archivos CSV con timestamp
- **Visualización Gráfica**: Gráficos en tiempo real con pyqtgraph
- **Monitoreo de Puerto Serial**: Detección y conexión automática de dispositivos

---

## Estructura de archivos

```
Aplicación/
├── main.py                    # Interfaz principal (PyQt5)
├── __main__.py               # Punto de entrada del módulo
├── serial_reader.py          # Gestión de puerto serial asíncrono
├── data_logger.py            # Registro de datos en CSV
├── lab_security.py           # Autenticación y configuración de laboratorio
├── monitor_serial.py         # Monitoreo de puertos disponibles
├── calibration_ranges.cfg    # Archivo de configuración de calibración
├── requirements.txt          # Dependencias Python
└── __init__.py              # Inicialización del paquete
```

---

## Dependencias

```
PyQt5>=5.15.0          # Interfaz gráfica
pyserial>=3.5          # Comunicación serial
pyqtgraph>=0.12.0      # Gráficos en tiempo real
matplotlib>=3.5.0      # Visualización de datos
numpy>=1.20.0          # Operaciones numéricas
```

### Instalación de Dependencias

```bash
pip install -r requirements.txt
```

---

## Uso

### Ejecución Directa
```bash
python Aplicación/main.py
```

### Ejecución como Módulo
```bash
python -m Aplicación
```

---

## Módulos principales

### `main.py`
Interfaz gráfica principal. Contiene:
- **Cálculo de EC**: Función polinómica de 3er grado para convertir voltaje a conductividad
  - Coeficientes: `A=133.42`, `B=255.86`, `C=857.39`
- **Compensación Térmica**: Normalización de voltaje según temperatura
  - Referencia: 25°C
  - Coeficiente: 0.02
- **Carga de Tabla K**: Lee factores de calibración desde `calibration_ranges.cfg`

### `serial_reader.py`
Gestión de comunicación con ESP32:
- Lectura asíncrona en thread dedicado
- Sincronización de comandos con locks
- Callback para procesar datos recibidos
- Manejo de desconexiones

### `data_logger.py`
Sistema de registro centralizado:
- Creación automática de archivos CSV
- Rotación de archivos por nombre de prueba
- Thread-safe para escritura concurrente
- Intervalo de guardado configurable

### `lab_security.py`
Seguridad y configuración:
- Autenticación con contraseña hasheada (SHA-256)
- Contraseña por defecto: `L4b0r4t0r10`
- Configuración de calibración multirrango:
  - Rango: 0 - 10000 µS
  - Paso: 50 µS
  - Puntos: 200
  - Muestras por punto: 30

### `monitor_serial.py`
Monitoreo de puertos disponibles:
- Detección de dispositivos conectados
- Información de puerto y descripción

---

## Configuración

### Archivo `calibration_ranges.cfg`
Almacena factores de calibración K por rango de voltaje:
```ini
[LABORATORY_CALIBRATION]
voltage_1 = k_factor_1
voltage_2 = k_factor_2
...
```

---

## Seguridad

Funciones de laboratorio protegidas con contraseña SHA-256:
- **Contraseña**: `L4b0r4t0r10`
- **Hash**: Verificado en `lab_security.verify_password()`

Para cambiar la contraseña, modificar `PASSWORD_HASH` en `lab_security.py`.

---

## Formato de datos CSV

Los archivos generados contienen:
```
Fecha,Hora,Sensor,Temperatura,Nombre_Prueba
2026-05-20,14:30:45,EC,25.3,Test_Calibration_1
...
```

---

## Flujo de datos

```
ESP32 (Serial)
     ↓
[SerialReader] → Lectura asíncrona en thread
     ↓
[main.py] → Procesamiento y cálculo de EC
     ↓
[DataLogger] → Guardado en CSV
     ↓
[GUI] → Visualización y gráficos
```

---

## Solución de problemas

| Problema | Solución |
|----------|----------|
| No detecta puerto serial | Verificar conexión USB, revisar permisos del puerto |
| Valores EC incorrectos | Revisar tabla de calibración en `calibration_ranges.cfg` |
| Archivo CSV no se crea | Verificar permisos de escritura en directorio |
| Error de contraseña | Usar `L4b0r4t0r10` o modificar en `lab_security.py` |

---

## Notas de desarrollo

- La aplicación soporta tanto ejecución como módulo como script directo
- Las importaciones están configuradas para ambos casos (`.serial_reader` y `serial_reader`)
- Todos los cálculos usan punto flotante de doble precisión
- Los valores EC se redondean a 2 decimales
- La compensación térmica ignora temperaturas fuera del rango [-40°C, 125°C]

---

## Licencia

Proyecto de la Universidad Distrital Francisco José de Caldas

---

## Contacto

Para preguntas o reportes de errores, contactar al equipo de desarrollo del Conductímetro.
