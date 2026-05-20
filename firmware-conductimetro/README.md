# Firmware Conductímetro - LilyGO T-A7670G

Firmware basado en Arduino para ESP32 con módulo LilyGO T-A7670G. Sistema de medición de conductividad eléctrica con almacenamiento en SD, transmisión LTE/MQTT, WiFi y deep sleep para bajo consumo energético.

---

## Características principales

- **Medición de Conductividad**: Lectura de voltaje vía ADC (ADS1115) convertida a conductividad (µS)
- **Sensor de Temperatura**: DS18B20 con soporte para alimentación parasitaria
- **Almacenamiento Local**: Tarjeta microSD con registro de mediciones
- **Conectividad LTE**: Módulo SIM7600 para transmisión MQTT de datos
- **WiFi Portal de Configuración**: Portal de acceso AP para gestionar parámetros
- **Deep Sleep**: Ahorro de energía con ciclos de sueño configurable
- **RTC Integrado**: Reloj en tiempo real DS3231 para timestamps precisos
- **Calibración Multirrango**: Sistema de calibración de laboratorio con tabla K
- **Compensación Térmica**: Normalización de mediciones según temperatura

---

## Hardware

### Componentes principales
- **Placa**: LilyGO T-A7670G (ESP32 + Modem LTE SIM7600)
- **Sensor de Temperatura**: DS18B20 (One-Wire)
- **Convertidor ADC**: ADS1115 (I2C) - para lectura de voltaje del sensor
- **RTC**: DS3231 (I2C) - reloj en tiempo real
- **Almacenamiento**: Tarjeta microSD (SPI)
- **Batería**: Lectura de voltaje vía GPIO 35

### Pines configurados
| Componente | Pin | Función |
|-----------|-----|---------|
| I2C SDA | 21 | Sensores digitales (ADS1115, DS3231) |
| I2C SCL | 22 | Sensores digitales (ADS1115, DS3231) |
| One-Wire | 33 | Sensor de temperatura DS18B20 |
| SD MISO | 2 | Tarjeta microSD |
| SD MOSI | 15 | Tarjeta microSD |
| SD SCLK | 14 | Tarjeta microSD |
| SD CS | 13 | Tarjeta microSD |
| Modem TX | 26 | UART módulo LTE |
| Modem RX | 27 | UART módulo LTE |
| Modem Power | 12 | Control de alimentación del modem |
| Modem PWRKEY | 4 | Control de encendido del modem |
| Battery ADC | 35 | Lectura de voltaje de batería |
| AP Button Primary | 0 | Botón para salir de modo AP |
| AP Button Fallback | 34 | Botón alternativo para salir de modo AP |

---

## Estructura del código

```
lilygo_ta7670g.ino
├── Configuración de hardware (GPIO, ADC, pines)
├── Calibración de conductividad (curvas polinómicas, tablas K)
├── Estructuras de datos (RtcDateTime, KRange, MinuteSample, etc.)
├── Estado global y almacenamiento RTC
├── Lectura de sensores
├── Cálculo de conductividad
├── Gestión de energía (deep sleep)
├── Portal WiFi de configuración
├── Almacenamiento en SD
├── Transmisión MQTT via LTE
└── Manejo de calibración
```

---

## Configuración

### Archivo `secrets.h` (REQUERIDO)
Debe crear este archivo en la misma carpeta con las credenciales:

```cpp
#ifndef SECRETS_H
#define SECRETS_H

#define REAL_AP_PASSWORD "tu_contraseña_ap"
#define REAL_MODEM_APN "apn_tu_operadora"
#define REAL_MQTT_SERVER "servidor.mqtt.com"
#define REAL_MQTT_USER "usuario_mqtt"
#define REAL_MQTT_PASS "contraseña_mqtt"
#define REAL_MQTT_TOPIC "casa/sensor/conductimetro"
#define REAL_MQTT_CLIENT_ID "conductimetro_001"

#endif
```

### Parámetros de funcionamiento

| Parámetro | Valor por defecto | Descripción |
|-----------|------------------|-------------|
| `DEFAULT_SAMPLE_INTERVAL_MS` | 60000 | Intervalo entre ciclos (milisegundos) |
| `DEFAULT_SAMPLES_PER_WAKE` | 30 | Muestras por ciclo de medición |
| `DEFAULT_SD_SAVE_WINDOW_MIN` | 1 | Guardar promedio en SD cada N minutos |
| `DEFAULT_LTE_SEND_WINDOW_MIN` | 60 | Enviar datos por MQTT cada N minutos |
| `MQTT_PORT` | 1883 | Puerto del servidor MQTT |
| `SERIAL_BAUD` | 115200 | Velocidad de comunicación serial |
| `AP_AUTO_EXIT_MS` | 60000 | Timeout para salir de modo AP (milisegundos) |

### Constantes de calibración

**Conversión polinómica (Voltaje → Conductividad):**
```
EC(µS) = A·V³ + B·V² + C·V
```
- A = 133.42
- B = 255.86
- C = 857.39

**Compensación térmica:**
- Temperatura referencia: 25°C
- Coeficiente: 0.02
- Rango válido: -40°C a 125°C

**Tabla de calibración K (19 rangos):**
La tabla `LAB_K_RANGES` contiene factores K por rango de voltaje para calibración multirrango de laboratorio.

---

## Instalación y compilación

### Requisitos
- Visual Studio Code con extensión **PlatformIO**
- Arduino IDE compatible (opcional)
- Drivers USB CH340/CH341 instalados

### Pasos de compilación

1. **Clonar/descargar el repositorio**
   ```bash
   git clone <repositorio>
   cd conductimetro/lilygo_ta7670g
   ```

2. **Crear archivo `secrets.h`**
   ```bash
   cp secrets.example.h secrets.h
   # Editar secrets.h con tus credenciales
   ```

3. **Compilar y cargar con PlatformIO**
   ```bash
   platformio run --target upload
   ```

   O en VS Code: `PlatformIO: Build` → `PlatformIO: Upload`

### Monitoreo serial
```bash
platformio device monitor --baud 115200
```

---

## Funcionamiento

### Ciclo de medición
1. ESP32 se despierta cada `DEFAULT_SAMPLE_INTERVAL_MS`
2. Lee temperatura del sensor DS18B20
3. Lee voltaje del sensor de conductividad (ADC)
4. Calcula conductividad usando polinomio y tabla K
5. Acumula datos en memoria RTC
6. Al completarse la ventana SD (1 minuto), guarda promedio en tarjeta
7. Al completarse la ventana LTE (60 minutos), envía promedio por MQTT
8. Vuelve a deep sleep para conservar energía

### Portal WiFi de configuración
Cuando el dispositivo inicia o está inactivo, crea una red WiFi:
- **SSID**: `CONDUCTIMETRO_CONFIG`
- **Contraseña**: Definida en `secrets.h` (`REAL_AP_PASSWORD`)
- **IP**: 192.168.4.1
- **Timeout**: 60 segundos (sale automáticamente si no hay conexión)
- **Botón de salida**: GPIO 0 (primario) o GPIO 34 (alternativo)

Accede a `http://192.168.4.1` para configurar parámetros.

### Almacenamiento en SD
Archivo: `/mediciones.txt`
- Formato: Líneas de texto con timestamp, temperatura, conductividad
- Promedio de cada ventana SD (1 minuto por defecto)
- Rotación automática por nombre de prueba

### Transmisión MQTT
- **Servidor**: Configurado en `secrets.h`
- **Tópico**: Configurado en `secrets.h`
- **Datos**: JSON con timestamp, temperatura promedio, conductividad promedio
- **Frecuencia**: Cada 60 minutos por defecto
- **Modem**: Encendido solo durante transmisión para ahorrar batería

---

## Modos de calibración

### 1. Modo Laboratorio (por defecto)
- Usa tabla de 19 rangos (`LAB_K_RANGES`)
- Factores K específicos por rango de voltaje
- Ideal para calibraciones precisas en laboratorio

### 2. Modo Conocido
- Calibración con puntos conocidos definidos por usuario
- Permite ajuste manual de la relación voltaje-conductividad
- Accesible mediante portal WiFi

---

## Gestión de energía

### Deep Sleep
- Dispositivo duerme entre ciclos de medición
- Consumo en reposo: ~5-10 mA
- Despierta automáticamente por RTC

### Modem LTE
- Activado solo durante transmisión MQTT
- Se apaga automáticamente después de completar envío
- Ahorra ~100 mA cuando está inactivo

### Batería
- Lectura de voltaje vía GPIO 35 con divisor
- Rango: 3.2V (vacío) a 4.2V (lleno)
- Factor de calibración: 1.097

---

## Solución de problemas

| Problema | Posible causa | Solución |
|----------|--------------|----------|
| No se detecta el puerto | Drivers USB no instalados | Instalar drivers CH340/CH341 |
| Error de compilación en `secrets.h` | Archivo no existe | Crear `secrets.h` con credenciales |
| No lee temperatura | Sensor desconectado o GPIO 33 incorrecto | Verificar conexión One-Wire |
| Valores de conductividad incorrectos | Calibración desactualizada | Ejecutar recalibración vía portal WiFi |
| No conecta a WiFi | Contraseña incorrecta en `secrets.h` | Verificar `REAL_AP_PASSWORD` |
| Modem LTE no responde | Puerto serial desconectado | Verificar conexión GPIO 26/27 |
| SD no guarda datos | Tarjeta no insertada o corrompida | Formatear como FAT32 |

---

## Dependencias

Especificadas en `platformio.ini`:
```ini
lib_deps =
  adafruit/RTClib @ ^2.1.4
  paulstoffregen/OneWire @ ^2.3.8
  milesburton/DallasTemperature @ ^4.0.4
```

- **RTClib**: Gestión del reloj RTC DS3231
- **OneWire**: Protocolo One-Wire para DS18B20
- **DallasTemperature**: Lectura de temperatura DS18B20

---

## Desarrollo y contribuciones

Para modificaciones del firmware:
1. Editar `lilygo_ta7670g.ino`
2. Recompilar con PlatformIO
3. Probar en dispositivo físico
4. Documentar cambios

### Variables globales clave
- `RTC_DATA_ATTR`: Datos persistentes durante deep sleep
- `sdBatchCount`: Contador de muestras para ventana SD
- `lteBatchCount`: Contador de muestras para ventana LTE
- `calibrationMode`: Modo activo (laboratorio o conocido)

---

## Notas de diseño

- **Precisión**: Redondeo a 2 decimales en mediciones
- **Ruido**: Umbral de ruido de voltaje = 0.0V, EC = 5.0 µS
- **Validación**: Rango EC máximo = 3000 µS
- **Almacenamiento**: Memoria RTC mantiene estado entre deep sleep
- **Sincronización**: Hora se valida contra rango 2024-2069
