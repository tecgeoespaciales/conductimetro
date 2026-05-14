# Sistema de Monitoreo de Conductividad

Este repositorio centraliza el desarrollo de dispositivos IoT para la medición de parámetros fisicoquímicos en agua. El proyecto está dividido en dos vertientes principales según el hardware y el nivel de integración requeridos.

## Estructura de Ramas
El proyecto se organiza en dos ramas de desarrollo activas:

### 1. Rama: `comunitario` (Versión MicroPython Modular)
Esta versión prioriza la facilidad de aprendizaje y la flexibilidad de componentes.
* **Lenguaje:** MicroPython.
* **Hardware:** ESP32 genérico + sensores modulares.
* **Componentes clave:**
    * **RTC DS1307:** Reloj externo para registro offline.
    * **ADS1115:** Conversor ADC de 16-bits para máxima precisión en conductividad.
    * **Display OLED (SH1106):** Visualización local de datos en tiempo real.
    * **SD Card:** Registro de datos en formato CSV.
    * **Sensor de Conductividad (TDS):** Conexión analógica al ADS1115 (16 bits) para evitar el ruido eléctrico del ESP32.
    * **Sensor de Temperatura DS18B20:** Sensor digital sumergible con resolución de 12 bits.

### 2. Rama: `Profesional` (Versión Integrada)
Firmware de alta confiabilidad diseñado para la placa **Lilygo T-A7670G**.
* **Lenguaje:** C++ / Arduino.
* **Hardware:** Lilygo T-A7670G (ESP32 + Módem LTE).
* **Gestión de Energía:** * Uso de **Deep Sleep** con temporizador adaptativo para asegurar ciclos de medición exactos.
    * Retención de estado de pines del módem durante el sueño (RTC GPIO).
* **Conectividad Robusta:** * Soporte para redes **4G/LTE Cat-1**.
    * Protocolo MQTT con reintentos automáticos y sincronización de hora mediante la red celular (NTP/CCLK).
* **Almacenamiento:** Sistema de archivos con redundancia en tarjeta SD y memoria no volátil (NVS) para configuraciones de calibración.

## 🛠️ Cómo empezar
Selecciona la rama que se ajuste a tu hardware antes de compilar:
```bash
git checkout comunitario  # Para ESP32 + Módulos
# o
git checkout profesional  # Para Lilygo T-A7670G
