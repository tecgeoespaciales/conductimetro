# Conductímetro integrado - versión profesional

Este repositorio contiene el firmware desarrollado en C++/Arduino para la placa Lilygo T-A7670G. Esta versión profesional está optimizada para la estabilidad en transmisiones de larga distancia, eficiencia energética y despliegues industriales en campo.

## Especificaciones de hardware
El sistema utiliza una arquitectura integrada con los siguientes componentes:

* **Microcontrolador:** ESP32 (integrado en la placa Lilygo).
* **Módem LTE:** A7670G integrado para conectividad 4G Cat-1.
* **Conversor ADC (ADS1115):** conectado vía I2C para una lectura de alta precisión (16 bits) del sensor de conductividad.
* **Sensor de temperatura (DS18B20):** sensor digital sumergible conectado al GPIO 33 para compensación térmica.
* **Almacenamiento:** módulo de tarjeta SD integrado (bus SPI) para redundancia de datos.
* **Reloj de tiempo real (RTC):** sincronización híbrida entre el módulo físico DS3231 y la red celular (NTP/CCLK).
* **Gestión de energía:** monitoreo de batería LiPo mediante divisor de voltaje en el GPIO 35.

## Funcionalidades destacadas
* **Conectividad robusta:** soporte nativo para protocolos MQTT sobre redes 4G/LTE con reconexión automática.
* **Gestión de energía avanzada:** implementación de un ciclo de *deep sleep* con temporizador adaptativo para garantizar intervalos de medición exactos y ahorro de batería.
* **Sincronización horaria:** sistema que prioriza la hora de la red celular para asegurar que el registro de datos en la tarjeta SD sea preciso.
* **Calibración por rangos:** algoritmo avanzado que permite el uso de constantes de celda (K) diferenciadas según el nivel de conductividad detectado.
* **Portal cautivo de configuración:** modo de punto de acceso (AP) activable mediante el botón físico (GPIO 0) para realizar calibraciones en campo y descargar datos sin cables.

## Estructura del firmware
* **lilygo_ta7670g.ino:** código principal que gestiona el flujo del programa y los estados del sistema.
* **secrets.h.example:** plantilla para la configuración segura de credenciales (APN, MQTT y wifi).
* **platformio.ini:** configuración del entorno de desarrollo y gestión de librerías dependientes.

## Configuración de seguridad
Por motivos de seguridad, las credenciales reales no están incluidas en el repositorio. Para compilar el proyecto:
1. Copie el archivo `secrets.h.example`.
2. Renombre la copia como `secrets.h`.
3. Complete sus datos reales (usuario MQTT, contraseñas, etc.) en dicho archivo.

## Uso del portal de configuración
1. Durante el arranque, mantenga presionado el botón conectado al **GPIO 0**.
2. El dispositivo activará la red wifi: `CONDUCTIMETRO_CONFIG`.
3. Acceda desde un navegador a la dirección `http://192.168.4.1`.
4. Utilice la interfaz para calibrar el sensor, configurar ventanas de envío LTE o descargar las mediciones almacenadas.
