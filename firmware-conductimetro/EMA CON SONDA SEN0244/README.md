# ESP32 - Firmware de conductividad y temperatura

## Descripción general

Firmware para ESP32 que mide conductividad eléctrica y temperatura. Se comunica con la aplicación GUI para:

- Medir conductividad del agua
- Medir temperatura
- Calibrar y guardar configuración
- Sincronizar hora
- Guardar datos en tarjeta SD
- Mostrar información en pantalla (opcional)

## Requisitos de hardware

### Componentes principales
- Microcontrolador ESP32
- Sensor de conductividad ADS1115
- Sensor de temperatura DS18B20
- Reloj RTC (DS1307)
- Pantalla OLED (opcional)
- Tarjeta SD para almacenamiento

Todos los pines pueden configurarse en `app_config.py`.

## Archivos principales

- `firmware.py` - Programa principal del ESP32
- `sensors.py` - Control de sensores
- `calibration.py` - Sistema de calibración
- `commands.py` - Recibe comandos desde la aplicación
- `app_config.py` - Configuración del dispositivo
- `oled_display.py` - Control de pantalla (opcional)
- `storage.py` - Gestión de tarjeta SD

## Instalación

1. Descarga MicroPython desde https://micropython.org/download/esp32/
2. Instala las herramientas: `pip install esptool mpremote`
3. Copia los archivos del firmware al ESP32
4. Reinicia el dispositivo

Para más detalles, consulta la documentación de MicroPython.

## Configuración

Edita los archivos de configuración:

- `calibration_ranges.cfg` - Puntos de calibración
- `app_config.py` - Parámetros del dispositivo (pines, velocidades, etc.)

Cada opción tiene comentarios explicativos.

## Comunicación

El ESP32 se comunica con la aplicación GUI mediante mensajes JSON por puerto serial:

- Envía mediciones de conductividad y temperatura
- Recibe comandos para calibración y configuración
- Responde con confirmación de comandos

Para más detalles, consulta la documentación de los comandos en `commands.py`.

## ¿Cómo funciona?

1. El dispositivo se inicia y carga la configuración
2. Inicializa todos los sensores disponibles
3. Lee conductividad y temperatura continuamente
4. Envía las mediciones a la aplicación GUI
5. Recibe comandos de calibración desde la GUI
6. Guarda datos en la tarjeta SD (si está disponible)
7. Muestra información en pantalla (si OLED está disponible)

## Solución de problemas

**El dispositivo no responde:**
- Verifica la conexión USB
- Comprueba que el puerto COM es correcto
- Reinicia el ESP32

**Lecturas inconsistentes:**
- Verifica las conexiones de los sensores
- Aumenta el número de muestras en la configuración
- Espera a que se estabilice después del encendido

**No se aplican cambios de calibración:**
- Verifica que el archivo de configuración existe
- Reinicia el dispositivo después de cambios

## Diagnóstico

Utiliza `diagnose_cfg.py` para verificar que todos los componentes funcionen correctamente. Te mostrará qué sensores se detectaron y si hay problemas de configuración.

## Notas de seguridad

- Los datos se envían por serial sin encriptación
- No hay autenticación de comandos remotos
- Asegúrate de que solo usuarios autorizados tengan acceso al puerto serial

El dispositivo realiza validaciones básicas de datos para evitar lecturas incorrectas.

## Ejemplos de uso

Para usar el firmware:

1. Edita `app_config.py` con tus parámetros
2. Edita `calibration_ranges.cfg` con tus puntos de calibración
3. Copia los archivos al ESP32
4. Inicia la aplicación GUI y conecta el dispositivo
5. Las mediciones aparecerán automáticamente

Consulta `firmware.py` para ver cómo funciona el loop principal.

## Contribuciones

Si encuentras problemas o tienes sugerencias, puedes:
1. Abrir un issue en GitHub
2. Contactar al equipo de desarrollo
3. Hacer un fork y proponer cambios

## Soporte

Para más información:
- Consulta los comentarios en los archivos `.py`
- Revisa el archivo de configuración `app_config.py`
- Comprueba la salida serial para mensajes de error

---

**Versión**: 1.0.0  
**Actualizado**: 20 de Mayo de 2026  
