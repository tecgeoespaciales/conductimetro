import json
import os
import sys
import time
import uselect

from app_config import Config, load_config_from_file, load_k_ranges_from_file

ENABLE_OLED = True
if ENABLE_OLED:
    try:
        from oled_display import oled
    except Exception:
        oled = None
else:
    oled = None


def _json(msg_type, **kwargs):
    payload = {"type": msg_type}
    payload.update(kwargs)
    print(json.dumps(payload))


EXTRACCION_BOOT_FLAG = "boot_extraccion.flag"


def _set_extraccion_boot_flag():
    try:
        with open(EXTRACCION_BOOT_FLAG, "w") as file_handle:
            file_handle.write("1")
        return True
    except Exception:
        return False


def _consume_extraccion_boot_flag():
    try:
        os.stat(EXTRACCION_BOOT_FLAG)
    except Exception:
        return False

    try:
        os.remove(EXTRACCION_BOOT_FLAG)
    except Exception:
        pass
    return True


def _safe_mode_requested():
    try:
        from machine import Pin
        boot = Pin(0, Pin.IN, Pin.PULL_UP)
        return boot.value() == 0
    except Exception:
        return False


def _init_extraction_support():
    boton = None
    extraccion_module = None

    try:
        from boton_extraccion import BotonExtraccion
        button_type = getattr(Config, "BUTTON_TYPE", "NC")
        boton = BotonExtraccion(
            pin_numero=34,
            tiempo_debounce_ms=200,
            tiempo_presion_minimo_ms=3000,
            tipo_boton=button_type,
        )
        _json("button", status="ok", pin=34, mode=button_type)
    except Exception as error:
        _json("button", status="error", msg=str(error)[:40])

    try:
        import extraccion as extraccion_module
        _json("extraccion", status="ok")
    except Exception as error:
        extraccion_module = None
        _json("extraccion", status="error", msg=str(error)[:40])

    return boton, extraccion_module


state = {
    "fecha": "01/01/1970",
    "hora": "00:00:00",
    "temp": 25.0,
    "raw_V": 0.0,
    "ec_poly": 0.0,
    "k": 1.0,
    "sensor": 0.0,
}

k_table = {}
LOCAL_LOG_FILE = "lecturas_local.txt"
TARGET_SAMPLES_PER_SEC = 30
AVG_WINDOW_MS = 1000
TEMP_REFRESH_MS = 1000


def _ensure_local_log_file():
    """Crea el archivo de log local si no existe.

    El log local permite respaldo mínimo incluso cuando la SD no está disponible.
    """
    try:
        os.stat(LOCAL_LOG_FILE)
    except Exception:
        try:
            with open(LOCAL_LOG_FILE, "w") as file_handle:
                file_handle.write("Fecha,Hora,Temperatura,Conductividad\n")
        except Exception:
            pass


def _append_local_log(current_state):
    """Persistencia local compacta de una lectura promediada.

    Args:
        current_state: Diccionario de estado con fecha, hora, temperatura y EC final.
    """
    try:
        with open(LOCAL_LOG_FILE, "a") as file_handle:
            file_handle.write(
                "%s,%s,%.2f,%.1f\n" % (
                    current_state["fecha"],
                    current_state["hora"],
                    current_state["temp"],
                    current_state["sensor"],
                )
            )
    except Exception:
        pass


def _k_from_ranges(signal_value, ranges):
    """Resuelve el factor K por búsqueda de rangos y extrapolación por borde.

    Args:
        signal_value: Valor de señal usado para consultar rangos (voltaje).
        ranges: Lista de tuplas (min, max, k).

    Returns:
        K interpolado por rango si existe configuración, o None cuando no hay rangos.
    """
    if not ranges:
        return None

    for min_v, max_v, k_val in ranges:
        if min_v <= signal_value <= max_v:
            return float(k_val)

    if signal_value < ranges[0][0]:
        return float(ranges[0][2])

    return float(ranges[-1][2])


def _apply_selected_k(ec_poly, signal_value, ctx):
    """Selecciona la fuente de calibración activa y aplica K.

    Política de selección:
    1) Si `use_k` está deshabilitado, retorna identidad.
    2) En modo `known`, selecciona K desde `known_ranges` usando voltaje.
    3) En modo laboratorio, selecciona K desde `lab_ranges` usando voltaje.

    Args:
        ec_poly: EC calculada por polinomio base (sin K).
        signal_value: Voltaje usado para consultar los rangos de K.
        ctx: Contexto de comandos con modo y tablas de calibración.

    Returns:
        Tupla `(ec_final, k_factor)`.
    """
    if not ctx.use_k:
        return ec_poly, 1.0

    if ctx.calibration_mode == "known":
        k_known = _k_from_ranges(signal_value, ctx.known_ranges)
        if k_known is not None:
            return ec_poly * k_known, k_known
        # Modo conocido: no usar tabla de laboratorio ni tabla genérica.
        return ec_poly, 1.0

    k_lab = _k_from_ranges(signal_value, ctx.lab_ranges)
    if k_lab is not None:
        return ec_poly * k_lab, k_lab

    # Modo laboratorio: no usar tabla de valores conocidos ni tabla genérica.
    return ec_poly, 1.0

_json("boot", msg="firmware modular iniciando")

if _safe_mode_requested():
    _json("safe_mode", msg="BOOT presionado, bucle principal omitido")
else:
    if _consume_extraccion_boot_flag():
        _json("extraccion", status="boot_mode")
        try:
            import extraccion as extraccion_module_boot
            extraccion_module_boot.modo_extraccion()
        except Exception as error:
            _json("extraccion", status="boot_mode_error", msg=str(error)[:60])

    from sensors import SensorSystem
    from calibration import load_k_table, apply_k
    from rtc_utils import obtener_fecha_hora, apply_rtc_config_if_needed

    k_table = load_config_from_file(k_table)
    persisted = load_k_table(Config.K_FILE)
    if persisted:
        k_table.update(persisted)

    _ensure_local_log_file()

    sensors = SensorSystem(Config)
    sensors.init_hw()
    try:
        state["temp"] = round(float(sensors.last_temp), 2)
    except Exception:
        state["temp"] = Config.TEMP_REF

    apply_rtc_config_if_needed(sensors.rtc, Config)

    if oled is not None:
        try:
            oled.mostrar_bienvenida()
            _json("oled", status="ok")
        except KeyboardInterrupt:
            _json("oled", status="interrupted")
        except Exception as error:
            _json("oled", status="error", msg=str(error)[:40])

    _json("startup", msg="listo", k_points=len(k_table))

    from commands import CommandContext
    command_ctx = CommandContext(Config, state, k_table)
    command_ctx.rtc = sensors.rtc
    command_ctx.lab_ranges = load_k_ranges_from_file("LABORATORY_CALIBRATION_RANGES")
    command_ctx.known_ranges = load_k_ranges_from_file("KNOWN_K_RANGES")
    command_ctx.calibration_mode = "laboratory"
    _json(
        "calibration_mode",
        mode=command_ctx.calibration_mode,
        lab_ranges=len(command_ctx.lab_ranges),
        known_ranges=len(command_ctx.known_ranges),
    )

    # Se habilita guardado SD en modo automático para reducir dependencia del cliente PC.
    last_sd_retry = time.ticks_ms()
    try:
        from storage import StorageManager
        command_ctx.storage_manager = StorageManager(Config)
        sd_ok = command_ctx.storage_manager.init_sd()
        command_ctx.sd_logging = bool(sd_ok)
        _json("sd_logging", mode="auto", enabled=command_ctx.sd_logging)
    except Exception as error:
        command_ctx.storage_manager = None
        command_ctx.sd_logging = False
        _json("sd_logging", mode="auto", enabled=False, msg=str(error)[:40])

    boton_extraccion, extraccion_module = _init_extraction_support()

    poll = uselect.poll()
    poll.register(sys.stdin, uselect.POLLIN)

    sample_interval_ms = int(1000 / TARGET_SAMPLES_PER_SEC)
    if sample_interval_ms < 1:
        sample_interval_ms = 1

    last_read = time.ticks_ms()
    last_uart = time.ticks_ms()
    last_oled = time.ticks_ms()
    last_temp_refresh = time.ticks_ms()

    sum_voltage = 0.0
    sum_ec_poly = 0.0
    sum_ec_final = 0.0
    sum_k = 0.0
    sample_count = 0

    while True:
        now = time.ticks_ms()

        if boton_extraccion is not None:
            try:
                if boton_extraccion.detectar_presion_sostenida():
                    _json("extraccion", status="restart_to_boot_mode")
                    if oled is not None:
                        try:
                            oled.mostrar_modo_extraccion_activado()
                        except Exception:
                            pass

                    flag_ok = _set_extraccion_boot_flag()
                    if not flag_ok:
                        _json("extraccion", status="flag_error")
                    try:
                        from machine import reset  # type: ignore
                        time.sleep_ms(200)
                        reset()
                    except Exception as error:
                        _json("extraccion", status="reset_error", msg=str(error)[:40])
            except Exception as error:
                _json("button", status="monitor_error", msg=str(error)[:40])

        while time.ticks_diff(now, last_read) >= sample_interval_ms:
            voltage = sensors.adc_read_voltage()
            ec_poly = sensors.voltage_to_ec(voltage)
            ec_final, k_factor = _apply_selected_k(ec_poly, voltage, command_ctx)

            sum_voltage += voltage
            sum_ec_poly += ec_poly
            sum_ec_final += ec_final
            sum_k += k_factor
            sample_count += 1

            last_read = time.ticks_add(last_read, sample_interval_ms)

        if time.ticks_diff(now, last_temp_refresh) >= TEMP_REFRESH_MS:
            try:
                state["temp"] = round(sensors.read_temp(), 2)
            except Exception:
                pass
            last_temp_refresh = now

        if time.ticks_diff(now, last_uart) >= AVG_WINDOW_MS:
            fecha, hora = obtener_fecha_hora(sensors.rtc)

            if sample_count > 0:
                avg_voltage = sum_voltage / sample_count
                avg_ec_poly = sum_ec_poly / sample_count
                avg_ec_final = sum_ec_final / sample_count
                avg_k = sum_k / sample_count
            else:
                avg_voltage = 0.0
                avg_ec_poly = 0.0
                avg_ec_final = 0.0
                avg_k = state["k"]

            state["fecha"] = fecha
            state["hora"] = hora
            state["raw_V"] = round(avg_voltage, 6)
            state["ec_poly"] = round(avg_ec_poly, 1)
            state["k"] = round(avg_k, 6)
            state["sensor"] = round(avg_ec_final, 1)

            _append_local_log(state)

            _json(
                "reading",
                fecha=state["fecha"],
                hora=state["hora"],
                temp=state["temp"],
                raw_V=state["raw_V"],
                ec_poly=state["ec_poly"],
                k=state["k"],
                sensor=state["sensor"],
                samples=sample_count,
            )

            if command_ctx.sd_logging and command_ctx.storage_manager is not None:
                ok_sd_write = command_ctx.storage_manager.log_to_sd(
                    state["fecha"],
                    state["hora"],
                    state["temp"],
                    state["sensor"],
                    state["k"],
                )
                if not ok_sd_write:
                    command_ctx.sd_logging = False
                    _json("sd_logging", mode="auto", enabled=False, msg="write_failed")

            last_uart = now
            sum_voltage = 0.0
            sum_ec_poly = 0.0
            sum_ec_final = 0.0
            sum_k = 0.0
            sample_count = 0

        if (not command_ctx.sd_logging) and time.ticks_diff(now, last_sd_retry) >= 5000:
            last_sd_retry = now
            try:
                if command_ctx.storage_manager is None:
                    from storage import StorageManager
                    command_ctx.storage_manager = StorageManager(Config)
                sd_ok = command_ctx.storage_manager.init_sd()
                if sd_ok:
                    command_ctx.sd_logging = True
                    _json("sd_logging", mode="auto", enabled=True, msg="retry_ok")
            except Exception:
                pass

        if oled is not None and time.ticks_diff(now, last_oled) >= int(Config.OLED_INTERVAL * 1000):
            try:
                oled.mostrar_datos(
                    state["fecha"],
                    state["hora"],
                    state["temp"],
                    state["sensor"],
                )
            except Exception:
                pass
            last_oled = now

        if poll.poll(0):
            try:
                line = sys.stdin.readline().strip()
            except Exception:
                line = ""

            if line:
                from commands import handle_command
                handle_command(line, command_ctx)

        time.sleep_ms(5)
