import json
from calibration import save_k_table, store_k_point
from rtc_utils import get_rtc_string, set_rtc_from_string


class CommandContext:
    """Estado compartido por el dispatcher de comandos seriales.

    Agrupa configuración, estado de medición y recursos opcionales (SD) para
    que `handle_command` pueda operar sin dependencias globales.
    """

    def __init__(self, config, state, k_table):
        """Inicializa el contexto con valores por defecto operativos.

        Args:
            config: Configuración global del firmware.
            state: Diccionario de estado de lectura actual.
            k_table: Tabla de calibración K en memoria.
        """
        self.config = config
        self.state = state
        self.k_table = k_table
        self.use_k = True
        self.calibration_mode = "laboratory"
        self.lab_ranges = []
        self.known_ranges = []
        self.storage_manager = None
        self.sd_logging = False


def _print_json(msg_type, **kwargs):
    """Emite respuesta JSON uniforme al host.

    Args:
        msg_type: Tipo lógico del mensaje.
        **kwargs: Campos adicionales del payload.
    """
    payload = {"type": msg_type}
    payload.update(kwargs)
    print(json.dumps(payload))


def handle_command(line, ctx):
    """Despacha comandos de control recibidos por serial.

    Estrategia:
    - Comandos de estado/diagnóstico (`PING`, `GET_STATE`).
    - Comandos de calibración (`STORE_K`, `GET_K_TABLE`, `CAL_RESET`).
    - Comandos de aplicación de K (`USE_K_ON`, `USE_K_OFF`, modos de calibración).
    - Comandos de logging en SD (`LOG_START`, `LOG_STOP`).

    Args:
        line: Línea de comando cruda.
        ctx: Instancia de `CommandContext`.

    Returns:
        None. La función responde por stdout con JSON.
    """
    command = line.strip()
    if not command:
        return

    if command == "PING":
        _print_json("pong")
        return

    if command == "GET_STATE":
        _print_json(
            "device_state",
            use_k=ctx.use_k,
            calibration_mode=ctx.calibration_mode,
            lab_ranges=len(ctx.lab_ranges),
            known_ranges=len(ctx.known_ranges),
            k_points=len(ctx.k_table),
            sd_logging=ctx.sd_logging,
            sd_ready=ctx.storage_manager is not None,
        )
        return

    if command == "USE_LABORATORY_CALIBRATION":
        ctx.calibration_mode = "laboratory"
        _print_json("calibration_mode", mode=ctx.calibration_mode, ok=True)
        return

    if command == "USE_KNOWN_CALIBRATION":
        if ctx.known_ranges:
            ctx.calibration_mode = "known"
            _print_json("calibration_mode", mode=ctx.calibration_mode, ok=True)
        else:
            _print_json("calibration_mode", mode=ctx.calibration_mode, ok=False, msg="known_empty")
        return

    if command.startswith("STORE_K:"):
        try:
            payload = command.split(":", 1)[1]
            parts = [part.strip() for part in payload.split(",") if part.strip()]
            if len(parts) < 2:
                raise ValueError("Formato: STORE_K:EC,K")

            known_ec = float(parts[0])
            k_value = float(parts[1])
            # Validación y actualización atómica del punto en la tabla en memoria.
            store_k_point(ctx.k_table, known_ec, k_value)

            # Persistencia inmediata para evitar pérdida ante reinicios del equipo.
            ok = save_k_table(ctx.config.K_FILE, ctx.k_table)
            _print_json("store_k_result", ok=ok, known=known_ec, k=round(k_value, 8), count=len(ctx.k_table))
        except Exception as error:
            _print_json("store_k_result", ok=False, msg=str(error)[:60])
        return

    if command == "GET_K_TABLE":
        serializable = {str(key): value for key, value in ctx.k_table.items()}
        _print_json("k_table", count=len(serializable), data=serializable)
        return

    if command == "CAL_RESET":
        ctx.k_table.clear()
        save_k_table(ctx.config.K_FILE, ctx.k_table)
        _print_json("cal_reset", ok=True)
        return

    if command == "USE_K_ON":
        ctx.use_k = True
        _print_json("use_k", enabled=True)
        return

    if command == "USE_K_OFF":
        ctx.use_k = False
        _print_json("use_k", enabled=False)
        return

    if command.startswith("INTERVAL:"):
        try:
            interval = float(command.split(":", 1)[1].strip())
            if interval <= 0:
                raise ValueError("interval_must_be_positive")
            ctx.state["interval"] = interval
            _print_json("interval", ok=True, value=interval)
        except Exception as error:
            _print_json("interval", ok=False, msg=str(error)[:60])
        return

    if command == "SYNC_RTC":
        rtc = getattr(ctx, "rtc", None)
        rtc_text = get_rtc_string(rtc)
        if rtc_text:
            _print_json("rtc", ok=True, datetime=rtc_text)
        else:
            _print_json("rtc", ok=False, msg="RTC no disponible")
        return

    if command.startswith("RTC_TIME:") or command.startswith("SET_RTC:"):
        try:
            rtc = getattr(ctx, "rtc", None)
            datetime_text = command.split(":", 1)[1].strip()
            ok, msg = set_rtc_from_string(rtc, datetime_text)
            current_rtc = get_rtc_string(rtc)
            _print_json("rtc_set", ok=ok, msg=msg, datetime=current_rtc)
            _print_json("rtc_sync_result", ok=ok, msg=msg, datetime=current_rtc)
        except Exception as error:
            _print_json("rtc_set", ok=False, msg=str(error)[:60], datetime=None)
        return

    if command.startswith("SET_K:"):
        try:
            value = float(command.split(":", 1)[1].strip())
            ctx.state["k"] = round(value, 6)
            _print_json("set_k", ok=True, k=ctx.state["k"])
        except Exception as error:
            _print_json("set_k", ok=False, msg=str(error)[:60])
        return

    if command.startswith("SHOW_EC:"):
        try:
            payload_text = command.split(":", 1)[1].strip()
            payload = json.loads(payload_text) if payload_text else {}

            temp_value = payload.get("temp")
            ec_value = payload.get("ec")

            if temp_value is not None:
                ctx.state["temp"] = round(float(temp_value), 2)
            if ec_value is not None:
                ctx.state["sensor"] = round(float(ec_value), 2)

            _print_json("show_ec", ok=True)
        except Exception as error:
            _print_json("show_ec", ok=False, msg=str(error)[:60])
        return

    if command == "LOG_START":
        if ctx.storage_manager is None:
            from storage import StorageManager
            ctx.storage_manager = StorageManager(ctx.config)
            ok = ctx.storage_manager.init_sd()
            if not ok:
                _print_json("log_start", ok=False, msg="No se pudo inicializar SD")
                return
        ctx.sd_logging = True
        _print_json("log_start", ok=True)
        return

    if command == "LOG_STOP":
        ctx.sd_logging = False
        _print_json("log_stop", ok=True)
        return

    _print_json("unknown_command", cmd=command[:40])
