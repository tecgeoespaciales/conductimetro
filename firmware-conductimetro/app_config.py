import os


class Config:
    LED_PIN = 2

    ADS_CHANNEL = 2
    ADS_ADDRESS = 0x48
    ADS_GAIN = 1
    ADS_RATE = 4

    TEMP_SENSOR_PIN = 26
    TEMP_REF = 25.0
    TEMP_OFFSET = 0.2
    TEMP_CONV_TIME = 0.5

    READ_INTERVAL = 0.2
    UART_INTERVAL = 0.8
    OLED_INTERVAL = 1.2

    I2C_ID = 1
    I2C_SCL = 22
    I2C_SDA = 21

    POLY_A = 133.42
    POLY_B = 255.86
    POLY_C = 857.39

    COEF_TEMP = 0.02
    TDS_FACTOR = 0.5
    NOISE_THRESHOLD = 0.0

    CFG_FILE = "calibration_ranges.cfg"
    K_FILE = "k_table.json"

    SD_MOUNT = "/sd"
    SD_FILE = "/sd/lecturas.csv"

    RTC_SET_ON_BOOT_IF_INVALID = True
    RTC_BOOT_DATETIME = ""

    # Tipo de botón de extracción:
    # - "NC": reposo=1, presionado=0 (común en pull-up)
    # - "NA": reposo=0, presionado=1
    BUTTON_TYPE = "NC"


def _to_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def _exists(path):
    try:
        os.stat(path)
        return True
    except Exception:
        return False


def load_config_from_file(k_table=None):
    if k_table is None:
        k_table = {}

    path = Config.CFG_FILE
    if not _exists(path):
        return k_table

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception:
        return k_table

    section = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].upper()
            continue

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()

        try:
            if section == "SENSING":
                if key == "ads_channel":
                    channel = int(value)
                    if 0 <= channel <= 3:
                        Config.ADS_CHANNEL = channel
                elif key == "ads_rate":
                    Config.ADS_RATE = int(value)
                elif key == "noise_threshold":
                    threshold = float(value)
                    if threshold >= 0:
                        Config.NOISE_THRESHOLD = threshold

            elif section == "TIMINGS":
                if key == "read_interval":
                    Config.READ_INTERVAL = float(value)
                elif key == "uart_interval":
                    Config.UART_INTERVAL = float(value)
                elif key == "temp_conv_time":
                    Config.TEMP_CONV_TIME = float(value)
                elif key == "temp_offset":
                    Config.TEMP_OFFSET = float(value)
                elif key == "temp_ref":
                    Config.TEMP_REF = float(value)

            elif section == "TEMPERATURE":
                if key == "coef_temp":
                    Config.COEF_TEMP = float(value)

            elif section == "RTC":
                if key == "set_on_boot_if_invalid":
                    Config.RTC_SET_ON_BOOT_IF_INVALID = _to_bool(value)
                elif key == "boot_datetime":
                    Config.RTC_BOOT_DATETIME = value

            elif section in ("EXTRACCION", "EXTRACTION", "BUTTON"):
                if key in ("button_type", "boton_tipo", "tipo_boton"):
                    button_type = str(value).strip().upper()
                    if button_type in ("NA", "NC"):
                        Config.BUTTON_TYPE = button_type

            elif section == "CALIBRATION_K_TABLE":
                k_table[float(key)] = float(value)
        except Exception:
            pass

    return k_table


def load_k_ranges_from_file(section_name):
    ranges = []
    path = Config.CFG_FILE
    if not _exists(path):
        return ranges

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception:
        return ranges

    section = None
    target = str(section_name or "").strip().upper()

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].upper()
            continue

        if section != target or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.split("#", 1)[0].strip()

        if "," not in key:
            continue

        try:
            min_v_str, max_v_str = key.split(",", 1)
            min_v = float(min_v_str.strip())
            max_v = float(max_v_str.strip())
            k_val = float(value)

            if max_v < min_v:
                min_v, max_v = max_v, min_v

            ranges.append((min_v, max_v, k_val))
        except Exception:
            continue

    ranges.sort(key=lambda item: item[0])
    return ranges
