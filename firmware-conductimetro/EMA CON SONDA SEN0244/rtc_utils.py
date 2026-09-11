import json


def weekday_from_ymd(year, month, day):
    table = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    y = year
    if month < 3:
        y -= 1
    return (y + y // 4 - y // 100 + y // 400 + table[month - 1] + day) % 7


def get_rtc_string(rtc):
    if rtc is None:
        return None
    try:
        dt = rtc.datetime()
        return "%04d-%02d-%02d %02d:%02d:%02d" % (dt[0], dt[1], dt[2], dt[4], dt[5], dt[6])
    except Exception:
        return None


def obtener_fecha_hora(rtc):
    if rtc is None:
        return "01/01/1970", "00:00:00"
    try:
        dt = rtc.datetime()
        return "%02d/%02d/%04d" % (dt[2], dt[1], dt[0]), "%02d:%02d:%02d" % (dt[4], dt[5], dt[6])
    except Exception:
        return "01/01/1970", "00:00:00"


def set_rtc_from_string(rtc, text):
    if rtc is None:
        return False, "RTC no disponible"

    try:
        value = text.strip().replace("T", " ")
        date_part, time_part = value.split(" ", 1)
        year, month, day = [int(x) for x in date_part.split("-")]
        hh, mm, ss = [int(x) for x in time_part.split(":")]
        wd = weekday_from_ymd(year, month, day)
        rtc.datetime((year, month, day, wd, hh, mm, ss, 0))
        return True, "RTC actualizado"
    except Exception as error:
        return False, "RTC parse/set error: %s" % str(error)[:40]


def apply_rtc_config_if_needed(rtc, config):
    if rtc is None:
        return False

    try:
        dt = rtc.datetime()
        year = dt[0]
    except Exception:
        return False

    if year >= 2020:
        return False

    if not getattr(config, "RTC_SET_ON_BOOT_IF_INVALID", False):
        return False

    value = getattr(config, "RTC_BOOT_DATETIME", "")
    if not value:
        return False

    ok, msg = set_rtc_from_string(rtc, value)
    if ok:
        print(json.dumps({"type": "rtc_cfg_applied", "datetime": get_rtc_string(rtc)}))
    else:
        print(json.dumps({"type": "rtc_cfg_error", "msg": msg}))
    return ok
