# Sistema de monitoreo de conductividad

Este repositorio centraliza el desarrollo de dispositivos IoT para la medición de parámetros fisicoquímicos en agua. El proyecto está dividido en dos vertientes principales según el hardware y el nivel de integración requeridos.

## Estructura de ramas
El proyecto se organiza en dos ramas de desarrollo activas:

### 1. Rama: `comunitario` (versión MicroPython modular)
Esta versión prioriza la facilidad de aprendizaje y la flexibilidad de componentes.
* **Lenguaje:** MicroPython.
* **Hardware:** ESP32 + sensores modulares.
* **Componentes clave:**
    * **RTC DS1307:** Reloj externo para registro fuera de línea (*offline*).
    * **ADS1115:** Conversor ADC de 16 bits para máxima precisión en conductividad.
    * **Pantalla OLED (SH1106):** Visualización local de datos en tiempo real.
    * **Tarjeta SD:** Registro de datos en formato CSV.
    * **Sensor de conductividad (TDS):** Conexión analógica al ADS1115 (16 bits) para evitar el ruido eléctrico del ESP32.
    * **Sensor de temperatura DS18B20:** Sensor digital sumergible con resolución de 12 bits.

### 2. Rama: `profesional` (versión integrada)
Firmware de alta confiabilidad diseñado para la placa **Lilygo T-A7670G**. Esta versión está optimizada para la estabilidad en transmisiones de larga distancia y eficiencia energética.

* **Lenguaje:** C++ / Arduino.
* **Hardware base:** Lilygo T-A7670G (ESP32 + módem LTE Cat-1).
* **Componentes y sensores:**
    * **Módem SIM7600/7670:** Conectividad 4G LTE para transmisión de datos en áreas sin WiFi.
    * **ADS1115 (vía I2C):** Conversor analógico-digital externo para una lectura de conductividad libre de ruido.
    * **Sensor de temperatura DS18B20:** Para compensación térmica automática (ATC) de la muestra.
    * **Sensor de conductividad (TDS):** Sonda industrial conectada al ADS1115.
    * **Tarjeta SD:** Almacenamiento local de respaldo con sistema de archivos redundante.
    * **Gestión de energía:** Monitoreo de nivel de batería LiPo mediante divisor de voltaje integrado.
* **Gestión de energía:**
    * Uso de **deep sleep** con temporizador adaptativo para asegurar ciclos de medición exactos.
    * Retención de estado de pines del módem durante el sueño (RTC GPIO).
* **Conectividad robusta:**
    * Soporte para redes **4G/LTE Cat-1**.
    * Protocolo MQTT con reintentos automáticos y sincronización de hora mediante la red celular (NTP/CCLK).
* **Almacenamiento:** Sistema de archivos con redundancia en tarjeta SD y memoria no volátil (NVS) para configuraciones de calibración.

## Cómo empezar
Selecciona la rama que se ajuste a tu hardware antes de compilar:

```bash
git checkout comunitario  # Para ESP32 + módulos
# o
git checkout profesional  # Para Lilygo T-A7670G
