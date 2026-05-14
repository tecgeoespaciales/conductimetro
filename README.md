# Conductímetro modular - versión comunitaria (MicroPython)

Este repositorio contiene el firmware desarrollado en MicroPython para el sistema de monitoreo de agua. Esta versión modular está diseñada para ser flexible, permitiendo el uso de componentes estándar fáciles de conseguir.

## Especificaciones de hardware
El sistema está configurado por defecto para los siguientes pines y componentes:

* **Microcontrolador:** ESP32.
* **Conversor ADC (ADS1115):** * Dirección I2C: `0x48`.
    * Canal de lectura: canal 2 (configurable en `app_config.py`).
* **Sensor de temperatura (DS18B20):** conectado al GPIO 26.
* **Botón de extracción:** conectado al GPIO 34.
* **Pantalla OLED (SH1106):** conectada vía I2C (SDA: pin 21, SCL: pin 22).
* **Almacenamiento:** módulo de tarjeta SD para registro de datos fuera de línea (datalogging offline).

## Funcionalidades destacadas
* **Servidor web de extracción:** al mantener presionado el botón (GPIO 34) por 3 segundos, el ESP32 crea una red wifi propia (`ESP32-EXTRACCION`) para descargar los datos directamente desde el navegador.
* **Compensación de temperatura:** ajuste dinámico de la conductividad referenciada a 25 °C mediante un coeficiente de temperatura de 0.02.
* **Calibración multirrango:** soporta tablas de calibración K personalizadas (`k_table.json`) para garantizar precisión en diferentes niveles de salinidad.
* **Interfaz JSON:** comunicación serial profesional mediante mensajes JSON para integración con software externo.

## Estructura del firmware
* `firmware.py`: corazón del sistema y bucle principal.
* `app_config.py`: gestión centralizada de constantes y carga de archivos `.cfg`.
* `extraccion.py`: lógica del servidor web y portal de descarga de archivos.
* `boton_extraccion.py`: gestor de rebote (debouncing) y detección de presión sostenida.
* `oled_display.py`: controlador de la interfaz gráfica local.

## Configuración inicial
1. Crea un archivo llamado `app_config.py` basado en el código base (o usa el existente si ya está configurado).
2. Verifica que el archivo de calibración `calibration_ranges.cfg` esté presente en la memoria flash o SD.
3. El sistema buscará automáticamente archivos de registro en `/sd/lecturas.csv`.

## Uso del modo extracción
1. Con el equipo encendido, presiona el botón físico por 3 segundos.
2. La pantalla OLED mostrará el mensaje: «Modo extracción activado».
3. Conéctate desde tu teléfono móvil o computador a la red wifi: `ESP32-EXTRACCION` (clave: `12345678`).
4. Abre el navegador en la dirección `http://192.168.4.1` para descargar los archivos.
