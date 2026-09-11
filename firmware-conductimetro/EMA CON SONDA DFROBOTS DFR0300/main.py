import sh1106
from machine import I2C, Pin, SDCard, UART
import ads1x15
from ds18x20_single import DS18X20Single
from onewire import OneWire
import time
import framebuf
import ds1307
import os
import json
import sys
import gc

try:
    import uselect
except Exception:
    uselect = None

# --- 1. CONFIGURACION GENERAL ---
SD_MOUNT = "/sd"
SD_FILE_PATH = "/sd/temp.csv"
LOCAL_FILE_PATH = "/lectura.csv"
LOGO_PBM_PATH = "images/logo.pbm"
SPLASH_PBM_PATH = "images/a.pbm"
MUESTREO_SEG = 2
TEMP_FALLBACK = 25.0
# Canal por defecto; se sincroniza con [SENSING].ads_channel del cfg.
ADS_CHANNEL = 0
ADS_RATE = 4
ADS_CHANNELS = (0, 1, 2, 3)
# Igual que firmware ESP32/sensors.py: estabilizacion por muestras.
ADS_STABLE_SAMPLES = 7
ADS_STABLE_DELAY_MS = 0
# Piso del ruido: descarta voltajes por debajo de este valor (V)
# Tu ruido está ~0.0074V, así que 0.01V debería filtrarlo
NOISE_THRESHOLD = 0.0
# Igual que firmware ESP32/calibration.py: interpolacion lineal de K-factor.
K_FILE = "k_table.json"

def _buscar_archivo_cfg():
    """Busca calibration_ranges.cfg en la memoria del microcontrolador (LilyGO)."""
    # En dispositivo MicroPython: buscar en raíz de memoria o tarjeta SD
    for path in ("calibration_ranges.cfg", "/sd/calibration_ranges.cfg"):
        try:
            import os
            os.stat(path)
            return path
        except:
            pass
    # Por defecto, guardar en raíz del dispositivo
    return "calibration_ranges.cfg"

CFG_FILE_PATH = _buscar_archivo_cfg()

def _cfg_paths_for_write():
    """Devuelve rutas de cfg candidatas para persistir cambios (raíz y SD)."""
    paths = []
    for p in (CFG_FILE_PATH, "calibration_ranges.cfg", "/sd/calibration_ranges.cfg"):
        if p in paths:
            continue
        try:
            import os
            os.stat(p)
            paths.append(p)
        except:
            pass
    if not paths:
        paths = [CFG_FILE_PATH]
    return paths

# --- 2. CALIBRACION (MODO LABORATORIO) ---
# --- 2. CALIBRACION (MODO LABORATORIO) ---

def depurar_puntos_criticos(puntos_laboratorio):
    """
    Detecta la zona de solapamiento por temperatura/ruido (0.045V a 0.115V) 
    y promedia las K para evitar rangos cruzados no monótonos.
    """
    if not puntos_laboratorio:
        return []
    
    puntos_limpios = []
    valores_k_zona_critica = []
    
    # Separar puntos estables de los conflictivos
    for v, k in puntos_laboratorio:
        if 0.045 <= v <= 0.115:
            valores_k_zona_critica.append(k)
        else:
            puntos_limpios.append((v, k))
            
    # Si se encontraron puntos en la zona de conflicto, unificar con su promedio
    if valores_k_zona_critica:
        k_promedio = sum(valores_k_zona_critica) / len(valores_k_zona_critica)
        # Inyectar fronteras fijas para aplanar la respuesta en ese tramo
        puntos_limpios.append((0.045000, k_promedio))
        puntos_limpios.append((0.115000, k_promedio))
        
    puntos_limpios.sort(key=lambda p: p[0])
    return puntos_limpios

# --- K TABLE PERSISTENCE (simula comportamiento ESP32) ---
def load_k_table(path):
    """Carga K table desde JSON. Convierte claves a float."""
    try:
        if not _existe_archivo(path):
            return {}
        with open(path, "r") as f:
            data = json.load(f)
        out = {}
        for k, v in data.items():
            try:
                out[float(k)] = float(v)
            except Exception:
                try:
                    out[float(k)] = v
                except Exception:
                    continue
        return out
    except Exception:
        return {}


def save_k_table(path, k_table=None):
    """Guarda K table en JSON. Devuelve True si OK."""
    try:
        kt = k_table if k_table is not None else K_TABLE
        serializable = {"{:.6f}".format(k): float(v) for k, v in kt.items()}
        with open(path, "w") as f:
            json.dump(serializable, f)
        return True
    except Exception as e:
        print("Error guardando K_TABLE:", e)
        return False


def store_k_point(k_table, known_ec, k_value):
    """Almacena un punto (ec_conocida -> k) en la tabla en memoria."""
    try:
        k_table[float(known_ec)] = float(k_value)
        return True
    except Exception:
        return False

# Inicializar K_TABLE desde archivo si existe
K_TABLE = load_k_table(K_FILE)


def _rebuild_lab_points_from_k_table():
    """Reconstruye LAB_POINTS a partir de K_TABLE usando voltaje->k."""
    points = []

    def _ec_poly(v):
        return (133.42 * v**3) - (255.86 * v**2) + (857.39 * v)

    def _invert_ec_poly(target_ec, v_min=0.0, v_max=3.5, tol=1e-6, max_iter=60):
        # Busca v en [v_min, v_max] tal que ec_poly(v) ~= target_ec
        lo, hi = float(v_min), float(v_max)
        f_lo = _ec_poly(lo) - target_ec
        f_hi = _ec_poly(hi) - target_ec
        if f_lo == 0.0:
            return lo
        if f_hi == 0.0:
            return hi
        # Si la función no cambia de signo en el intervalo, no hay raíz útil
        if f_lo * f_hi > 0:
            return None

        for _ in range(int(max_iter)):
            mid = (lo + hi) / 2.0
            f_mid = _ec_poly(mid) - target_ec
            if abs(f_mid) <= tol:
                return mid
            if f_mid * f_lo < 0:
                hi = mid
                f_hi = f_mid
            else:
                lo = mid
                f_lo = f_mid
        return (lo + hi) / 2.0

    try:
        for key, k_val in K_TABLE.items():
            try:
                k = float(k_val)
            except Exception:
                continue

            try:
                key_f = float(key)
            except Exception:
                continue

            # Si la clave parece un voltaje válido, usar directamente
            if 0.0 <= key_f <= 5.0:
                points.append((key_f, k))
                continue

            # Si la clave es mayor (probablemente una conductividad conocida en uS/cm),
            # convertir a voltaje resolviendo ec_poly(v) * k = known_ec
            if key_f > 5.0:
                known_ec = key_f
                try:
                    target_ec_poly = float(known_ec) / float(k) if k != 0 else None
                except Exception:
                    target_ec_poly = None

                if target_ec_poly is None:
                    continue

                v_est = _invert_ec_poly(target_ec_poly)
                if v_est is not None and 0.0 <= v_est <= 5.0:
                    points.append((v_est, k))
    except Exception:
        pass

    points.sort(key=lambda item: item[0])
    return points


def _existe_archivo(path):
    """Verifica existencia de archivo usando os.stat (compatible con MicroPython)."""
    try:
        os.stat(path)
        return True
    except Exception:
        return False


def _existe_seccion(path, section_name):
    """Verifica si una sección existe dentro del cfg."""
    if not _existe_archivo(path):
        return False

    target = str(section_name or "").strip().upper()
    if not target:
        return False

    try:
        with open(path, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                if line.startswith("[") and line.endswith("]"):
                    if line[1:-1].strip().upper() == target:
                        return True
    except Exception:
        pass

    return False


def cargar_puntos_laboratorio(path):
    """Carga pares (voltaje, k) desde [LABORATORY_CALIBRATION].
    
    Estructura esperada en calibration_ranges.cfg:
        [LABORATORY_CALIBRATION]
        conductividad_conocida = k_value,voltage

    Retorna lista de tuplas (voltage, k_val) ordenadas por voltaje.
    """
    points = []
    section = None
    
    if not _existe_archivo(path):
        return []
    
    try:
        with open(path, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                
                if not line or line.startswith("#"):
                    continue

                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].upper()
                    continue

                if section == "LABORATORY_CALIBRATION":
                    if "=" not in line:
                        continue
                    
                    try:
                        _known, value = line.split("=", 1)
                        value = value.split("#", 1)[0].strip()
                        if "," not in value:
                            continue

                        k_str, voltage_str = value.split(",", 1)
                        k_val = float(k_str.strip())
                        voltage = float(voltage_str.strip())
                        points.append((voltage, k_val))
                    except Exception:
                        pass
    except Exception as e:
        print("Error abriendo {}: {}".format(path, e))
        return []

    points.sort(key=lambda item: item[0])
    return points


def cargar_rangos_k(path, section_name):
    """Carga rangos de K desde calibration_ranges.cfg.
    
    Estructura esperada:
        [section_name]
        min_voltage,max_voltage = k_value
    
    Retorna lista de tuplas (min_v, max_v, k_val) ordenadas por min_v.
    """
    ranges = []
    
    if not _existe_archivo(path):
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


def _generar_rangos_desde_tabla(path, source_section, target_section):
    """Genera una sección de rangos a partir de una tabla key = value."""
    if not _existe_archivo(path):
        return False, "Archivo no encontrado", 0

    try:
        with open(path, "r") as f:
            lineas = f.readlines()
    except Exception as e:
        return False, "Error leyendo archivo: {}".format(str(e)), 0

    source_target = str(source_section or "").strip().upper()
    target_target = str(target_section or "").strip().upper()
    puntos = []
    section = None
    lineas_reconstruidas = []

    for raw_line in lineas:
        line = raw_line.strip()

        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].upper()
            lineas_reconstruidas.append(raw_line)
            continue

        if section == source_target and "=" in line:
            try:
                key, value = line.split("=", 1)
                key = key.split("#", 1)[0].strip()
                value = value.split("#", 1)[0].strip()
                puntos.append((float(key), float(value)))
            except Exception:
                pass

        lineas_reconstruidas.append(raw_line)

    if len(puntos) < 1:
        return False, "No hay puntos de calibración válidos", 0

    puntos.sort(key=lambda item: item[0])
    ranges_dict = {}

    for i, data in enumerate(puntos):
        x_val = data[0]
        y_val = data[1]

        if i == 0:
            v_min = 0.0
            if len(puntos) > 1:
                next_x = puntos[i + 1][0]
                v_max = (x_val + next_x) / 2.0
            else:
                v_max = max(x_val * 2.0, 10.0)
        elif i == len(puntos) - 1:
            prev_x = puntos[i - 1][0]
            v_min = (prev_x + x_val) / 2.0
            v_max = max(x_val * 2.0, 10.0)
        else:
            prev_x = puntos[i - 1][0]
            next_x = puntos[i + 1][0]
            v_min = (prev_x + x_val) / 2.0
            v_max = (x_val + next_x) / 2.0

        if v_min > v_max:
            v_min, v_max = v_max, v_min

        ranges_dict["{:.6f},{:.6f}".format(v_min, v_max)] = "{:.6f}".format(y_val)

    # Reconstruir el archivo preservando todas las secciones, reemplazando
    # únicamente la sección objetivo si existe, o agregándola al final.
    # Parsear el archivo en secciones (preservando líneas tal cual).
    sections = []  # list of (section_name_or_None, lines_list)
    current_name = None
    current_lines = []

    for raw_line in lineas_reconstruidas:
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            # flush previous
            if current_lines or current_name is not None:
                sections.append((current_name, current_lines))
            current_name = stripped[1:-1].upper()
            current_lines = [raw_line]
        else:
            current_lines.append(raw_line)

    # append last
    if current_lines or current_name is not None:
        sections.append((current_name, current_lines))

    new_lines = []
    wrote_target = False

    for sec_name, sec_lines in sections:
        if sec_name == target_target:
            # replace this section with generated ranges
            new_lines.append("[{}]\n".format(target_target))
            for range_key, value_str in sorted(ranges_dict.items()):
                new_lines.append("{} = {}\n".format(range_key, value_str))
            wrote_target = True
        else:
            new_lines.extend(sec_lines)

    if not wrote_target:
        # preserve trailing newline if present in file
        if not (len(new_lines) and new_lines[-1].endswith("\n")):
            new_lines.append("\n")
        new_lines.append("[{}]\n".format(target_target))
        for range_key, value_str in sorted(ranges_dict.items()):
            new_lines.append("{} = {}\n".format(range_key, value_str))

    # Safety: don't overwrite file with empty content; create a backup first
    if not new_lines or all((not l.strip()) for l in new_lines):
        return False, "Contenido generado vacío; no se sobreescribe.", 0

    try:
        # Backup original
        try:
            with open(path + ".bak", "w") as fb:
                fb.writelines(lineas_reconstruidas)
        except Exception:
            pass

        with open(path, "w") as f:
            f.writelines(new_lines)
        return True, "Rangos generados exitosamente", len(ranges_dict)
    except Exception as e:
        return False, "Error guardando archivo: {}".format(str(e)), 0


def generar_rangos_calibration_k(path):
    """Genera [LABORATORY_CALIBRATION_RANGES] a partir de [CALIBRATION_K_TABLE].

    Formato de entrada  → [CALIBRATION_K_TABLE]:  voltaje = k_value
    Formato de salida   → [LABORATORY_CALIBRATION_RANGES]: min_v,max_v = k_value

    Los límites de cada rango son los puntos medios entre voltajes vecinos.
    El primer rango empieza en 0.0 y el último termina en 10.0.
    """
    if not _existe_archivo(path):
        return False, "Archivo no encontrado", 0

    try:
        with open(path, "r") as f:
            lineas = f.readlines()
    except Exception as e:
        return False, "Error leyendo archivo: {}".format(str(e)), 0

    # 1. Extraer puntos de [CALIBRATION_K_TABLE]: voltaje -> k
    puntos = []
    section = None
    for raw_line in lineas:
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].upper()
            continue
        if section == "CALIBRATION_K_TABLE" and "=" in line and not line.startswith("#"):
            try:
                key, value = line.split("=", 1)
                voltaje = float(key.strip())
                k_val = float(value.split("#", 1)[0].strip())
                puntos.append((voltaje, k_val))
            except Exception:
                continue

    if not puntos:
        return False, "No hay puntos en CALIBRATION_K_TABLE", 0

    # 2. Ordenar por voltaje ascendente
    puntos.sort(key=lambda p: p[0])

    # 3. Generar rangos por punto medio entre voltajes vecinos
    ranges_dict = {}
    for i, (v, k) in enumerate(puntos):
        if i == 0:
            v_min = 0.0
            v_max = (v + puntos[i + 1][0]) / 2.0 if len(puntos) > 1 else max(v * 2.0, 10.0)
        elif i == len(puntos) - 1:
            v_min = (puntos[i - 1][0] + v) / 2.0
            v_max = 10.0
        else:
            v_min = (puntos[i - 1][0] + v) / 2.0
            v_max = (v + puntos[i + 1][0]) / 2.0

        if v_min > v_max:
            v_min, v_max = v_max, v_min

        ranges_dict["{:.6f},{:.6f}".format(v_min, v_max)] = "{:.6f}".format(k)

    # 4. Reescribir el archivo: parsear en secciones y reemplazar/añadir
    sections = []
    current_name = None
    current_lines = []

    for raw_line in lineas:
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current_lines or current_name is not None:
                sections.append((current_name, current_lines))
            current_name = stripped[1:-1].upper()
            current_lines = [raw_line]
        else:
            current_lines.append(raw_line)

    if current_lines or current_name is not None:
        sections.append((current_name, current_lines))

    new_lines = []
    wrote = False
    for sec_name, sec_lines in sections:
        if sec_name == "LABORATORY_CALIBRATION_RANGES":
            new_lines.append("[LABORATORY_CALIBRATION_RANGES]\n")
            for rk, rv in sorted(ranges_dict.items()):
                new_lines.append("{} = {}\n".format(rk, rv))
            wrote = True
        else:
            new_lines.extend(sec_lines)

    if not wrote:
        if not (len(new_lines) and new_lines[-1].endswith("\n")):
            new_lines.append("\n")
        new_lines.append("[LABORATORY_CALIBRATION_RANGES]\n")
        for rk, rv in sorted(ranges_dict.items()):
            new_lines.append("{} = {}\n".format(rk, rv))

    # Safety: avoid writing if output empty
    if not new_lines or all((not l.strip()) for l in new_lines):
        return False, "Contenido generado vacío; no se sobreescribe.", 0

    try:
        # backup original file
        try:
            with open(path + ".bak", "w") as bfd:
                bfd.writelines(lineas)
        except Exception:
            pass

        with open(path, "w") as f:
            f.writelines(new_lines)
        return True, "Rangos generados exitosamente", len(ranges_dict)
    except Exception as e:
        return False, "Error guardando archivo: {}".format(str(e)), 0


def generar_rangos_k_laboratorio(path):
    """Genera automáticamente [LABORATORY_CALIBRATION_RANGES] a partir de [LABORATORY_CALIBRATION].
    
    Replicas exactamente el proceso que hace generate_lab_ranges.py:
    1. Lee los puntos de [LABORATORY_CALIBRATION]
    2. Ordena por conductividad conocida (µS)
    3. Calcula rangos de voltaje por punto medio entre vecinos
    4. Guarda en [LABORATORY_CALIBRATION_RANGES]
    
    Args:
        path: Ruta del archivo calibration_ranges.cfg
    
    Returns:
        Tupla (éxito, mensaje, num_rangos_generados)
    """
    if not _existe_archivo(path):
        return False, "Archivo no encontrado", 0
    
    try:
        # 1. Leer archivo completo
        with open(path, "r") as f:
            lineas = f.readlines()
    except Exception as e:
        return False, "Error leyendo archivo: {}".format(str(e)), 0
    
    # 2. Parsear y extraer puntos de [LABORATORY_CALIBRATION]
    calibration_points = []
    section = None
    lineas_reconstruidas = []
    
    for raw_line in lineas:
        line = raw_line.strip()
        
        # Determinar sección actual
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].upper()
            lineas_reconstruidas.append(raw_line)
            continue
        
        # Extraer puntos de laboratorio
        if section == "LABORATORY_CALIBRATION" and "=" in line:
            # Formato: conductividad_conocida = k_value,voltage
            try:
                key, value = line.split("=", 1)
                key_str = key.strip()
                value_str = value.split("#", 1)[0].strip()
                
                if "," not in value_str:
                    lineas_reconstruidas.append(raw_line)
                    continue
                
                known_cond = float(key_str)
                k_str, voltage_str = value_str.split(",", 1)
                k_value = float(k_str.strip())
                measured_voltage = float(voltage_str.strip())
                
                calibration_points.append({
                    'k': k_value,
                    'cond': known_cond,
                    'voltage': measured_voltage,
                })
                lineas_reconstruidas.append(raw_line)
            except Exception:
                lineas_reconstruidas.append(raw_line)
        else:
            lineas_reconstruidas.append(raw_line)
    
    if len(calibration_points) < 1:
        return False, "No hay puntos de calibración válidos", 0
    
    # 3. Ordenar por voltaje
    sorted_points = sorted(calibration_points, key=lambda item: item['voltage'])
    
    # 4. Generar rangos por punto medio entre voltajes vecinos
    ranges_dict = {}
    limites_voltaje = [0.0]  # El primer rango inicia estrictamente en 0V
    
    # Calcular todos los puntos medios ordenados primero
    for i in range(len(sorted_points) - 1):
        punto_medio = (sorted_points[i]['voltage'] + sorted_points[i+1]['voltage']) / 2.0
        limites_voltaje.append(punto_medio)
    limites_voltaje.append(10.0)  # El último rango cierra en 10V (Techo lógico)

    # Construir el diccionario usando los límites encadenados secuencialmente
    for i, data in enumerate(sorted_points):
        v_min = limites_voltaje[i]
        v_max = limites_voltaje[i+1]
        k_val = data['k']
        
        range_key = "{:.6f},{:.6f}".format(v_min, v_max)
        ranges_dict[range_key] = "{:.6f}".format(k_val)
    
    # 5. Construir líneas de la nueva sección [LABORATORY_CALIBRATION_RANGES]
    output_lines = []
    in_ranges_section = False
    ranges_section_written = False

    for raw_line in lineas_reconstruidas:
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].upper()
            if section_name == "LABORATORY_CALIBRATION_RANGES":
                if ranges_section_written:
                    # Ya se escribió; saltar este header duplicado
                    in_ranges_section = True
                    continue
                # Reemplazar la sección existente por la nueva
                in_ranges_section = True
                output_lines.append("\n[LABORATORY_CALIBRATION_RANGES]\n")
                for range_key, k_str in sorted(ranges_dict.items()):
                    output_lines.append("{} = {}\n".format(range_key, k_str))
                ranges_section_written = True
                continue
            else:
                in_ranges_section = False
                output_lines.append(raw_line)
                continue
        if in_ranges_section:
            continue  # Saltar líneas viejas de la sección de rangos
        output_lines.append(raw_line)

    # Si la sección no existía, agregarla al final
    if not ranges_section_written:
        output_lines.append("\n[LABORATORY_CALIBRATION_RANGES]\n")
        for range_key, k_str in sorted(ranges_dict.items()):
            output_lines.append("{} = {}\n".format(range_key, k_str))
    
    # 6. Guardar archivo
    # Safety: do not overwrite with empty content
    if not output_lines or all((not l.strip()) for l in output_lines):
        return False, "Contenido generado vacío; no se sobreescribe.", 0

    try:
        # Backup original
        try:
            with open(path + ".bak", "w") as fb:
                fb.writelines(lineas)
        except Exception:
            pass

        with open(path, "w") as f:
            f.writelines(output_lines)
        return True, "Rangos generados exitosamente", len(ranges_dict)
    except Exception as e:
        return False, "Error guardando archivo: {}".format(str(e)), 0


# Cargar puntos de calibración de laboratorio y rangos de K
LAB_POINTS = cargar_puntos_laboratorio(CFG_FILE_PATH)
if LAB_POINTS:
    LAB_POINTS = depurar_puntos_criticos(LAB_POINTS)
LAB_RANGES = cargar_rangos_k(CFG_FILE_PATH, "LABORATORY_CALIBRATION_RANGES")
CALIBRATION_K_RANGES = cargar_rangos_k(CFG_FILE_PATH, "CALIBRATION_K_RANGES")

# Si no hay puntos en cfg, intentar reconstruir desde K_TABLE (voltaje->k).
if not LAB_POINTS and K_TABLE:
    LAB_POINTS = _rebuild_lab_points_from_k_table()

# Si hay puntos de laboratorio pero no hay rangos, generarlos automáticamente.
if LAB_POINTS and not LAB_RANGES:
    generar_rangos_k_laboratorio(CFG_FILE_PATH)
    LAB_RANGES = cargar_rangos_k(CFG_FILE_PATH, "LABORATORY_CALIBRATION_RANGES")

# Si hay CALIBRATION_K_TABLE y no hay LAB_RANGES, generar LABORATORY_CALIBRATION_RANGES.
if not LAB_RANGES and _existe_seccion(CFG_FILE_PATH, "CALIBRATION_K_TABLE"):
    ok, msg, n = generar_rangos_calibration_k(CFG_FILE_PATH)
    print("generar_rangos_calibration_k: {} - {} rangos".format(msg, n))
    LAB_RANGES = cargar_rangos_k(CFG_FILE_PATH, "LABORATORY_CALIBRATION_RANGES")


def _cargar_parametro_desde_cfg(path, section, key, default_value, value_type=str):
    """Lee un parámetro específico desde [section] del archivo cfg.
    
    Args:
        path: Ruta del archivo de configuración.
        section: Nombre de la sección entre corchetes.
        key: Nombre de la clave a buscar.
        default_value: Valor por defecto si no se encuentra.
        value_type: Tipo de conversión (int, float, str, bool).
    
    Returns:
        El valor convertido o default_value.
    """
    if not _existe_archivo(path):
        return default_value
    
    section_upper = section.upper()
    key_lower = key.lower()
    
    try:
        with open(path, "r") as f:
            current_section = None
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.startswith("[") and line.endswith("]"):
                    current_section = line[1:-1].upper()
                    continue

                if current_section != section_upper or "=" not in line:
                    continue

                parsed_key, value = line.split("=", 1)
                if parsed_key.strip().lower() != key_lower:
                    continue

                value = value.split("#", 1)[0].strip()
                
                try:
                    if value_type == bool:
                        return value.lower() in ("1", "true", "yes", "on", "si", "sí")
                    elif value_type == int:
                        return int(value)
                    elif value_type == float:
                        return float(value)
                    else:
                        return value
                except Exception:
                    return default_value
    except Exception:
        pass

    return default_value


def _cargar_ads_channel_desde_cfg(path, default_channel):
    """Lee ads_channel de [SENSING] si existe en calibration_ranges.cfg."""
    section = None
    try:
        with open(path, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].upper()
                    continue

                if section != "SENSING" or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                if key.strip().lower() != "ads_channel":
                    continue

                channel = int(value.split("#", 1)[0].strip())
                if channel in ADS_CHANNELS:
                    return channel
    except Exception:
        pass

    return default_channel


ADS_CHANNEL = _cargar_ads_channel_desde_cfg(CFG_FILE_PATH, ADS_CHANNEL)

COEF_TEMP = _cargar_parametro_desde_cfg(CFG_FILE_PATH, "TEMPERATURE", "coef_temp", 0.020, float)
TDS_FACTOR = _cargar_parametro_desde_cfg(CFG_FILE_PATH, "TEMPERATURE", "tds_factor", 0.64, float)
TEMP_OFFSET = _cargar_parametro_desde_cfg(CFG_FILE_PATH, "TIMINGS", "temp_offset", 0.2, float)
TEMP_REF = 25.0

ADS_LAST_CHANNEL = ADS_CHANNEL

# --- 3. INICIALIZACION DE HARDWARE ---
bus = I2C(1, scl=Pin(22), sda=Pin(21), freq=100000)

# ADS1115: Usamos gain=1 (Rango hasta 4.096V) para evitar que se sature en 2.04V
adc = ads1x15.ADS1115(bus, address=0x48, gain=1)

# Pantalla OLED SH1106
try:
    oled = sh1106.SH1106_I2C(128, 64, bus)
    oled.sleep(False)
    oled.fill(0)
except:
    oled = None

# Reloj RTC DS1307
try:
    ds = ds1307.DS1307(bus)
    ds.halt(False)
except:
    ds = None

# Sensor de Temperatura DS18B20 (Pin 26)
try:
    ow = OneWire(Pin(26))
    temp_sensor = DS18X20Single(ow)
    temp_sensor_available = True
except:
    temp_sensor_available = False

# Tarjeta SD
sd = None
try:
    sd = SDCard(slot=2, freq=1320000)
    os.mount(sd, SD_MOUNT)
except:
    pass


# --- UART para recibir comandos de la app ---
# FIX: se deshabilita el UART físico (pines TX=1/RX=3). En el ESP32 esos son los
# MISMOS pines que usa la consola REPL/USB-serial (por donde ya viajan print() y
# stdin). Tener ambos canales activos hacía que cada JSON de lectura y cada ACK de
# comando se transmitiera DUPLICADO y mezclado con los prints de depuración
# ([MAIN], AVISO:, etc.) en el mismo cable — esto es lo que producía líneas
# corruptas/incompletas que la app no podía parsear, mostrándose como "0"
# intercalado con el dato real. El conductímetro 1 (que funciona bien) SOLO usa
# print()/stdin, sin UART de hardware adicional. Se replica ese mismo esquema aquí.
uart = None

# Poll no bloqueante para comandos recibidos por USB-serial (stdin).
try:
    if uselect:
        _stdin_poll = uselect.poll()
        _stdin_poll.register(sys.stdin, uselect.POLLIN)
    else:
        _stdin_poll = None
except Exception:
    _stdin_poll = None

# Variables globales para almacenar valores mostrados por SHOW_EC
show_ec_temp = None
show_ec_valor_uS = None
show_ec_time = 0

# Bandera de transmisión de lecturas activada por la app.
READING_ACTIVE = True
interval_sec_global = 2.0

# Bandera para pausar mediciones durante actualización de calibración
UPDATING_CALIBRATION = False


def cargar_imagen_pbm(path, width, height):
    try:
        with open(path, "rb") as f:
            # Encabezado PBM binario (P4) con posible linea de comentario
            magic = f.readline()
            if not magic.startswith(b"P4"):
                return None

            line = f.readline()
            while line.startswith(b"#"):
                line = f.readline()

            data = bytearray(f.read())
        return framebuf.FrameBuffer(data, width, height, framebuf.MONO_HLSB)
    except Exception:
        return None


logo_img = cargar_imagen_pbm(LOGO_PBM_PATH, 64, 51)
splash_img = cargar_imagen_pbm(SPLASH_PBM_PATH, 128, 64)


def mostrar_bienvenida(oled):
    if not oled:
        return

    # Pantalla principal de bienvenida
    oled.fill(0)
    oled.text("Calidad", 0, 5)
    oled.text("de agua", 0, 13)
    oled.text("en", 20, 21)
    oled.text("uS x cm", 0, 29)
    oled.text("--------", 0, 37)
    oled.text("Bienvenid@!", 0, 56)
    if logo_img:
        oled.blit(logo_img, 64, 1)
    oled.show()
    time.sleep(4)

    # Segunda pantalla de arranque (si existe la imagen)
    if splash_img:
        oled.invert(True)
        oled.fill(0)
        oled.blit(splash_img, 0, 0)
        oled.show()
        time.sleep(2)
        oled.invert(False)


def procesar_comando(cmd_str):
    """Procesa comandos recibidos por UART desde la app."""
    global show_ec_temp, show_ec_valor_uS, show_ec_time
    global READING_ACTIVE
    global LAB_POINTS, LAB_RANGES, UPDATING_CALIBRATION
    
    try:
        # Formatos aceptados: "COMANDO" o "COMANDO:payload"
        if ":" in cmd_str:
            comando, payload = cmd_str.split(":", 1)
            comando = comando.strip()
            payload = payload.strip()
        else:
            comando = cmd_str.strip()
            payload = ""
        
        if comando == "SHOW_EC":
            # Esperamos JSON: {"temp":X, "ec":Y}
            data = json.loads(payload)
            show_ec_temp = float(data.get("temp", 25.0))
            show_ec_valor_uS = float(data.get("ec", 0.0))
            show_ec_time = time.time()
            print("OK:SHOW_EC")
            if uart:
                uart.write("OK:SHOW_EC\r\n")
            return True
        
        elif comando.startswith("INTERVAL:"):
            try:
                global interval_sec_global
                interval_val = float(comando.split(":", 1)[1].strip())
                if interval_val > 0:
                    interval_sec_global = interval_val
                print('{"type": "interval", "value": ' + str(interval_sec_global) + ', "ok": true}')
                if uart:
                    uart.write('{"type": "interval", "value": ' + str(interval_sec_global) + ', "ok": true}\r\n')
            except Exception as e:
                pass
            return True

        elif comando == "LOG_START":
            READING_ACTIVE = True
            print("OK:LOG_START")
            if uart:
                uart.write("OK:LOG_START\r\n")
            return True

        elif comando == "LOG_STOP":
            READING_ACTIVE = False
            print("OK:LOG_STOP")
            if uart:
                uart.write("OK:LOG_STOP\r\n")
            return True
        
        elif comando == "GET_RTC":
            # Responde con la hora actual
            try:
                h = ds.datetime()
                respuesta = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(h[0], h[1], h[2], h[4], h[5], h[6])
                if uart:
                    uart.write("RTC:" + respuesta + "\r\n")
                return True
            except:
                if uart:
                    uart.write("ERR:GET_RTC\r\n")
                return False
        elif comando == "PING":
            if uart:
                uart.write("pong\r\n")
            return True

        elif comando == "GET_K_TABLE":
            try:
                # Enviar tabla K como JSON serializable
                serializable = {str(key): value for key, value in K_TABLE.items()}
                resp = json.dumps({"type": "k_table", "count": len(serializable), "data": serializable})
                if uart:
                    uart.write(resp + "\r\n")
                print(resp)
                return True
            except Exception:
                if uart:
                    uart.write("ERR:GET_K_TABLE\r\n")
                return False

        elif comando == "CAL_RESET":
            try:
                print("✓ CAL_RESET recibido en firmware")
                # Limpiar tabla en memoria y archivo JSON
                K_TABLE.clear()
                save_k_table(K_FILE)
                LAB_POINTS = []
                LAB_RANGES = []

                # También eliminar secciones de calibración en todos los cfg disponibles (raíz/SD)
                try:
                    for cfg_path in _cfg_paths_for_write():
                        if not _existe_archivo(cfg_path):
                            continue
                        with open(cfg_path, 'r') as f:
                            lines = f.readlines()

                        out_lines = []
                        in_section = None
                        skip_sections = ('LABORATORY_CALIBRATION', 'LABORATORY_CALIBRATION_RANGES')

                        for raw in lines:
                            s = raw.strip()
                            if s.startswith('[') and s.endswith(']'):
                                in_section = s[1:-1].upper()
                                if in_section in skip_sections:
                                    continue
                                out_lines.append(raw)
                            else:
                                if in_section and in_section in skip_sections:
                                    continue
                                out_lines.append(raw)

                        with open(cfg_path, 'w') as f:
                            for _line in out_lines:
                                f.write(_line)

                        # Forzar recarga de puntos y rangos en memoria
                        try:
                            LAB_POINTS = cargar_puntos_laboratorio(CFG_FILE_PATH)
                            LAB_RANGES = cargar_rangos_k(CFG_FILE_PATH, 'LABORATORY_CALIBRATION_RANGES')
                        except Exception:
                            pass
                except Exception as e:
                    print('⚠️  CAL_RESET: no se pudo limpiar cfg:', e)

                if uart:
                    uart.write('{"type":"cal_reset", "ok":true}\r\n')
                return True
            except Exception:
                if uart:
                    uart.write("ERR:CAL_RESET\r\n")
                return False

        elif comando == "STORE_K":
            try:
                parts = [part.strip() for part in payload.split(",") if part.strip()]
                if len(parts) < 2:
                    raise ValueError("Formato: STORE_K:EC,K")

                known_ec = float(parts[0])
                k_value = float(parts[1])
                store_k_point(K_TABLE, known_ec, k_value)
                ok = save_k_table(K_FILE)
                
                resp = json.dumps({
                    "type": "store_k_result",
                    "ok": ok,
                    "known": known_ec,
                    "k": round(k_value, 8),
                    "count": len(K_TABLE),
                })
                if uart:
                    uart.write(resp + "\r\n")
                print("✓ STORE_K: EC={}, K={}, Total={}".format(known_ec, round(k_value, 8), len(K_TABLE)))
                return True
            except Exception as e:
                if uart:
                    uart.write(("{\"type\":\"store_k_result\", \"ok\":false, \"msg\":\"%s\"}\r\n") % str(e)[:60])
                print("✗ STORE_K error:", str(e)[:60])
                return False

        elif comando == "USE_LABORATORY_CALIBRATION":
            # Cambiar a modo de calibración de laboratorio y recargar puntos desde cfg
            try:
                LAB_POINTS = cargar_puntos_laboratorio(CFG_FILE_PATH)
                LAB_RANGES = cargar_rangos_k(CFG_FILE_PATH, "LABORATORY_CALIBRATION_RANGES")
                if not LAB_POINTS and K_TABLE:
                    LAB_POINTS = _rebuild_lab_points_from_k_table()
                print("✓ Modo LABORATORY: cargados {} puntos, {} rangos".format(len(LAB_POINTS), len(LAB_RANGES)))
            except Exception as e:
                print("✗ Error recargando LAB_POINTS:", e)
            
            if uart:
                uart.write("{\"type\":\"calibration_mode\", \"mode\":\"laboratory\", \"ok\":true, \"points\":" + str(len(LAB_POINTS)) + "}\r\n")
            return True
        
        elif comando == "UPDATE_K_TABLE":
            # Recibir tabla K completa desde la app (formato JSON)
            print("[UPDATE_K_TABLE] Entrando en handler")
            try:
                UPDATING_CALIBRATION = True  # Pausar mediciones
                print("[UPDATE_K_TABLE] Payload len: {}".format(len(payload)))
                if payload:
                    k_data = json.loads(payload)
                    # Esperamos que la app envíe voltaje_at_25c -> K
                    K_TABLE.clear()
                    for key_str, k_val in k_data.items():
                        try:
                            v_measured = float(key_str)
                            k_f = float(k_val)
                            # Guardar en K_TABLE como voltaje->k
                            K_TABLE[float(v_measured)] = float(k_f)
                        except Exception:
                            # Si la clave no es voltaje, intentar parsear como conductividad
                            try:
                                known_ec = float(key_str)
                                K_TABLE[known_ec] = float(k_val)
                            except Exception:
                                continue

                    # Guardar en K_TABLE.json
                    ok = save_k_table(K_FILE)

                    # Construir sección [LABORATORY_CALIBRATION] a partir de K_TABLE
                    # Formato: conductividad_conocida = k_value,voltage
                    try:
                        cfg_path = CFG_FILE_PATH
                        # Leer archivo existente (si no existe, crear plantilla)
                        existing_lines = []
                        if _existe_archivo(cfg_path):
                            with open(cfg_path, 'r') as f:
                                existing_lines = f.readlines()

                        # Eliminar sección antigua LABORATORY_CALIBRATION y LABORATORY_CALIBRATION_RANGES
                        out_lines = []
                        in_skip = None
                        for raw in existing_lines:
                            s = raw.strip()
                            if s.startswith('[') and s.endswith(']'):
                                sec = s[1:-1].upper()
                                if sec in ('LABORATORY_CALIBRATION', 'LABORATORY_CALIBRATION_RANGES'):
                                    in_skip = sec
                                    continue
                                else:
                                    in_skip = None
                                    out_lines.append(raw)
                            else:
                                if in_skip and in_skip in ('LABORATORY_CALIBRATION', 'LABORATORY_CALIBRATION_RANGES'):
                                    continue
                                out_lines.append(raw)

                        # Añadir nueva sección LABORATORY_CALIBRATION
                        out_lines.append('\n[LABORATORY_CALIBRATION]\n')

                        def _ec_poly(v):
                            return (133.42 * v**3) - (255.86 * v**2) + (857.39 * v)

                        # Para cada entrada en K_TABLE interpretada como voltaje->k, calcular conductividad conocida
                        added = 0
                        for v_key, k_val in sorted(K_TABLE.items()):
                            try:
                                # Si la clave parece voltaje (0..5V), convertir a conductividad conocida
                                if 0.0 <= float(v_key) <= 5.0:
                                    v = float(v_key)
                                    kf = float(k_val)
                                    ec_known = _ec_poly(v) * kf
                                    out_lines.append("{:.6f} = {:.6f},{:.6f}\n".format(ec_known, kf, v))
                                    added += 1
                                else:
                                    # Si la clave es mayor que rango voltaje, la interpretamos como conductividad ya
                                    ec_known = float(v_key)
                                    kf = float(k_val)
                                    # No conocemos voltaje, dejar 0.0 como marcador
                                    out_lines.append("{:.6f} = {:.6f},0.000000\n".format(ec_known, kf))
                                    added += 1
                            except Exception:
                                continue

                        # Guardar archivo en todas las rutas cfg disponibles
                        for target_cfg in _cfg_paths_for_write():
                            try:
                                with open(target_cfg, 'w') as f:
                                    for _line in out_lines:
                                        f.write(_line)
                            except Exception:
                                pass

                        # Regenerar rangos y recargar puntos
                        try:
                            generar_rangos_k_laboratorio(cfg_path)
                        except Exception:
                            pass

                        # Forzar recarga en memoria
                        LAB_POINTS = cargar_puntos_laboratorio(CFG_FILE_PATH)
                        if LAB_POINTS:
                            LAB_POINTS = depurar_puntos_criticos(LAB_POINTS)
                        LAB_RANGES = cargar_rangos_k(CFG_FILE_PATH, 'LABORATORY_CALIBRATION_RANGES')

                        print("✓ UPDATE_K_TABLE: {} puntos K guardados en cfg y K_TABLE.json".format(len(K_TABLE)))
                    except Exception as cfg_error:
                        print("⚠️  UPDATE_K_TABLE: Guardado en archivo falló: {}".format(str(cfg_error)[:80]))

                    ack_msg = "{\"type\":\"update_k_table\", \"ok\":" + ("true" if ok else "false") + ", \"count\":" + str(len(K_TABLE)) + "}"
                    # Enviar ACK por ambos canales: stdout (USB-CDC) y UART físico.
                    print(ack_msg)
                    if uart:
                        uart.write(ack_msg + "\r\n")
                    UPDATING_CALIBRATION = False  # Reanudar mediciones
                    return True
                else:
                    raise ValueError("Payload vacío")
            except Exception as e:
                print("✗ UPDATE_K_TABLE error:", str(e)[:60])
                err_ack = "{\"type\":\"update_k_table\", \"ok\":false, \"msg\":\"" + str(e)[:40] + "\"}"
                print(err_ack)
                if uart:
                    uart.write(err_ack + "\r\n")
                UPDATING_CALIBRATION = False  # Reanudar mediciones
                return False
        
        elif comando == "IDENTIFY":
            try:
                info = {
                    "type": "identify",
                    "device_type": "conductimetro2",
                    "firmware": "v0.1",
                    "cfg": "calibration_ranges.cfg",
                    "protocol": "lab_mode",
                    "device_id": "COND2"
                }
                resp = json.dumps(info)
                if uart:
                    uart.write(resp + "\r\n")
                print(resp)
                return True
            except Exception:
                if uart:
                    uart.write('{"type":"identify","error":true}\r\n')
                return False
        else:
            # Comando desconocido
            if uart:
                uart.write("ERR:UNKNOWN\r\n")
            return False
    
    except Exception as e:
        print("Error procesando comando:", e)
        if uart:
            uart.write("ERR:PARSE\r\n")
        return False


def dibujar_pantalla(oled, tempi, valor_uS, v_sensor, hora_str, fecha_str):
    if not oled:
        return

    # Si existe una imagen de fondo, se usa como plantilla visual.
    # En caso contrario, se limpia la pantalla.
    if splash_img:
        oled.blit(splash_img, 0, 0)
    else:
        oled.fill(0)

    # Solo variables requeridas por el usuario: T y EC.
    # Coordenadas ajustadas para parecerse a la maqueta de referencia.
    oled.text("{:.1f}uS".format(valor_uS), 45, 10)
    oled.text("{:.1f}C".format(tempi), 40, 34)
  
    oled.show()


def _sleep_with_uart(total_seconds):
    """Espera en pasos cortos atendiendo comandos UART durante la pausa."""
    try:
        total_ms = int(float(total_seconds) * 1000)
    except Exception:
        total_ms = 0

    if total_ms <= 0:
        return

    step_ms = 25
    elapsed = 0
    while elapsed < total_ms:
        # Procesar comandos pendientes para evitar latencia de control.
        try:
            cmd = _leer_comando_host()
            if cmd:
                procesar_comando(cmd)
        except Exception:
            pass

        remaining = total_ms - elapsed
        wait_ms = step_ms if remaining > step_ms else remaining
        try:
            time.sleep_ms(wait_ms)
        except AttributeError:
            time.sleep(wait_ms / 1000.0)
        elapsed += wait_ms


def _leer_comando_host():
    """Lee un comando entrante desde UART hardware o stdin USB-CDC (no bloqueante)."""
    # 1) Intentar leer desde stdin (USB-CDC, mapeado a COM10 en PC) con poll
    if _stdin_poll:
        try:
            # Poll stdin con timeout 0 (no bloqueante)
            if _stdin_poll.poll(0):
                # Hay datos, leer línea completa
                try:
                    line = sys.stdin.readline()
                    if line:
                        cmd = line.strip()
                        if cmd:  # Si no es solo whitespace
                            print("[STDIN_POLL] Recibido: {}".format(cmd[:60]))
                            return cmd
                except Exception as e:
                                print("[STDIN_READLINE] Error: {}".format(e))
        except Exception as e:
                        print("[STDIN_POLL_ERROR] {}".format(e))
    
    # 2) Fallback: leer desde UART hardware (pin RX3) si está disponible
    try:
        if uart and uart.any():
            nbytes = uart.any()
            if nbytes > 0:
                line = uart.read(min(nbytes, 256))
            else:
                line = None
            if line:
                if isinstance(line, bytes):
                    cmd = line.decode("utf-8").strip()
                else:
                    cmd = str(line).strip()
                if cmd:
                    print("[UART_HW] Recibido: {}".format(cmd[:60]))
                    return cmd
    except Exception as e:
        print("[UART_HW_ERROR] {}".format(e))
        pass

    return ""

# --- 4. FUNCIONES DE CALCULO Y LOG ---

_last_k_lab_range_index = -1

def _k_lab_por_voltaje(v_sensor):
    """Obtiene K-factor mediante interpolación lineal (igual que ESP32)."""
    v = float(v_sensor)

    # FORZAR PRIORIDAD: Usar interpolación lineal pura sobre LAB_POINTS (como ESP32)
    if LAB_POINTS:
        sorted_points = sorted(LAB_POINTS, key=lambda p: p[0])

        if len(sorted_points) == 1:
            return float(sorted_points[0][1])
        if v <= sorted_points[0][0]:
            return float(sorted_points[0][1])
        if v >= sorted_points[-1][0]:
            return float(sorted_points[-1][1])

        for idx in range(len(sorted_points) - 1):
            v0, k0 = sorted_points[idx]
            v1, k1 = sorted_points[idx + 1]
            if v0 <= v <= v1:
                k = k0 + (k1 - k0) * (v - v0) / (v1 - v0)
                return float(k)

        return float(sorted_points[-1][1])

    # Fallback si no hay LAB_POINTS: usar LAB_RANGES con Histéresis
    global _last_k_lab_range_index
    if LAB_RANGES:
        margin = 0.02
        if 0 <= _last_k_lab_range_index < len(LAB_RANGES):
            min_v, max_v, k_val = LAB_RANGES[_last_k_lab_range_index]
            if (min_v - margin) <= v <= (max_v + margin):
                return float(k_val)
        
        for i, (min_v, max_v, k_val) in enumerate(LAB_RANGES):
            if min_v <= v <= max_v:
                _last_k_lab_range_index = i
                return float(k_val)
        
        if v < LAB_RANGES[0][0]:
            _last_k_lab_range_index = 0
            return float(LAB_RANGES[0][2])
        _last_k_lab_range_index = len(LAB_RANGES) - 1
        return float(LAB_RANGES[-1][2])

    return 1.0


def _leer_voltaje_ads(adc_obj):
    """Replica ESP32/sensors.py: read(rate,channel)+raw_to_v+promedio robusto."""
    global ADS_LAST_CHANNEL
    ADS_LAST_CHANNEL = ADS_CHANNEL
    try:
        samples = int(ADS_STABLE_SAMPLES)
        if samples < 1:
            samples = 1

        delay_ms = int(ADS_STABLE_DELAY_MS)
        if delay_ms < 0:
            delay_ms = 0

        values = []
        for _ in range(samples):
            raw = adc_obj.read(ADS_RATE, ADS_CHANNEL)
            if raw is None:
                continue

            voltage = adc_obj.raw_to_v(raw)
            if voltage is None:
                continue

            value = float(voltage)
            if value < 0.0 or value > 4.5:
                continue

            # Descartar voltajes por debajo del piso del ruido
            if value < NOISE_THRESHOLD:
                continue

            values.append(value)

            if delay_ms > 0:
                try:
                    time.sleep_ms(delay_ms)
                except AttributeError:
                    time.sleep(delay_ms / 1000.0)

        if not values:
            return 0.0

        if len(values) >= 3:
            values.sort()
            values = values[1:-1]
            if not values:
                return 0.0

        return sum(values) / len(values)
    except Exception:
        return 0.0


def calcular_us_cm(v_sensor, temp_agua):
    """Replica el firmware principal: polinomio + K por calibracion de laboratorio.
    
    Sincronizado con ESP32/calibration.py:
    - Polinomio EC idéntico
    - K-factor por interpolación lineal (igual a ESP32)
    """
    if v_sensor < 0.005:
        return 0.0

    ec_poly = (133.42 * v_sensor**3) - (255.86 * v_sensor**2) + (857.39 * v_sensor)
    if ec_poly < 0.0:
        ec_poly = 0.0

    k_factor = _k_lab_por_voltaje(v_sensor)
    ec_bruta = ec_poly * k_factor
    # Compensación por temperatura: EC sube cuando la temperatura sube, por lo que compensar a 25C divide
    denominador = 1.0 + COEF_TEMP * (temp_agua - TEMP_REF)
    if denominador < 0.1:
        denominador = 0.1
    ec_25 = ec_bruta / denominador
    return ec_25


def guardar_datos(fecha, hora, valor):
    linea = "{},{},{}\r\n".format(fecha, hora, valor)
    # Guardar en memoria interna del ESP32
    try:
        with open(LOCAL_FILE_PATH, "a") as f:
            f.write(linea)
    except:
        pass

    # Guardar en SD si existe
    if sd:
        try:
            with open(SD_FILE_PATH, "a") as f:
                f.write(linea)
        except:
            pass


# --- 5. BUCLE PRINCIPAL ---
idx = 0
mostrar_bienvenida(oled)
valor_uS_filtrado = -1.0
alpha_filtro = 0.15

last_reading_time = time.ticks_ms()

# FIX: el conductímetro 2 no tenía watchdog (a diferencia del conductímetro 1, que
# sí lo usa). Sin WDT, si el ESP32 se cuelga (I2C, ADS1115, DS18B20, UART) se queda
# congelado indefinidamente sin generar datos hasta que alguien lo reconecta a mano.
# Esto es lo más probable detrás de la gran diferencia de cantidad de muestras entre
# ambos sensores en mediciones de varios días.
try:
    from machine import WDT  # type: ignore
    wdt = WDT(timeout=20000)  # 20s, igual que en el conductímetro 1
except Exception:
    wdt = None
tempi_anterior = TEMP_FALLBACK

while True:
    if wdt is not None:
        try:
            wdt.feed()
        except Exception:
            pass

    # A. Leer Tiempo
    try:
        if ds is not None:
            h = ds.datetime()
            fecha_str = "{:02d}/{:02d}/{}".format(h[2], h[1], h[0])
            hora_str = "{:02d}:{:02d}:{:02d}".format(h[4], h[5], h[6])
        else:
            fecha_str, hora_str = "00/00/00", "00:00:00"
    except:
        fecha_str, hora_str = "00/00/00", "00:00:00"

    # A.5 Recibir comandos del host (UART físico o USB stdin), no bloqueante.
    try:
        cmd_str = _leer_comando_host()
        if cmd_str:
            print("[MAIN] cmd_str recibido, len={}, contenido: {}".format(len(cmd_str), cmd_str[:80]))
            print("[MAIN] Llamando procesar_comando()...")
            try:
                result = procesar_comando(cmd_str)
                print("[MAIN] procesar_comando() returned: {}".format(result))
            except Exception as e:
                print("[MAIN] procesar_comando() EXCEPCIÓN: {}".format(e))
        else:
            pass  # cmd_str está vacío, no hacer nada
    except Exception as e:
        print("Error leyendo comando host:", e)

    # A.7 Si SHOW_EC activo, usar esos valores por 5 segundos
    usar_show_ec = False
    if show_ec_temp is not None and show_ec_valor_uS is not None:
        if time.time() - show_ec_time < 5.0:
            usar_show_ec = True

    # B. Leer Temperatura (siempre, independientemente de usar_show_ec)
    if temp_sensor_available:
        try:
            temp_sensor.convert_temp()
            time.sleep_ms(800)  # sleep duro sin interrupciones UART
            raw_temp = temp_sensor.read_temp()
            if raw_temp is None or raw_temp < -10.0 or raw_temp > 85.0:
                tempi = tempi_anterior  # <--- Usa la memoria en vez de 25.0
            else:
                tempi = raw_temp + TEMP_OFFSET
                tempi_anterior = tempi  # <--- Guarda el valor si fue exitoso
        except:
            tempi = tempi_anterior  # <--- Usa la memoria en vez de 25.0
    else:
        tempi = TEMP_FALLBACK
        
    # C. Leer Voltaje y calcular Conductividad
    if READING_ACTIVE and not UPDATING_CALIBRATION:
        try:
            v_sensor = _leer_voltaje_ads(adc)
            if v_sensor > 3.2:
                print("AVISO: Voltaje en canal ADS saturado. Revisa conexion TDS.")
                
            valor_uS_crudo = calcular_us_cm(v_sensor, tempi)
            
            if valor_uS_filtrado < 0:
                valor_uS_filtrado = valor_uS_crudo
            else:
                valor_uS_filtrado = (alpha_filtro * valor_uS_crudo) + ((1.0 - alpha_filtro) * valor_uS_filtrado)
            
            valor_uS = valor_uS_filtrado
            
        except Exception as e:
            print("Error en ADC:", e)
            v_sensor = 0.0
            valor_uS = 0.0
    else:
        v_sensor = 0.0
        valor_uS = 0.0

    # D. Mostrar en OLED (solo si no estamos actualizando calibración)
    if READING_ACTIVE and not UPDATING_CALIBRATION:
        # SHOW_EC solo afecta lo que se visualiza, no lo que se transmite.
        display_val = show_ec_valor_uS if usar_show_ec and show_ec_valor_uS is not None else valor_uS
        display_temp = show_ec_temp if usar_show_ec and show_ec_temp is not None else tempi
        dibujar_pantalla(oled, display_temp, display_val, v_sensor, hora_str, fecha_str)

    # E. Guardar y Enviar Datos
    if READING_ACTIVE and not UPDATING_CALIBRATION:
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, last_reading_time) >= int(interval_sec_global * 1000):
            last_reading_time = time.ticks_add(last_reading_time, int(interval_sec_global * 1000))
            val_formateado = "{:.1f}".format(valor_uS)
            guardar_datos(fecha_str, hora_str, val_formateado)
            # FIX: liberar memoria periódicamente. En corridas de varios días sin
            # gc.collect(), la fragmentación de memoria en el ESP32 puede ir
            # ralentizando el bucle o provocar errores intermitentes.
            try:
                gc.collect()
            except Exception:
                pass

            # Enviar medición a la app por UART en formato JSON (a menos que esté actualizando calibración)
            try:
                data = {
                    "type": "reading",
                    "sensor": round(valor_uS, 1),
                    "temp": round(tempi, 1),
                    "k": round(_k_lab_por_voltaje(v_sensor), 6),
                    "raw_V": round(v_sensor, 4),
                    "raw_ch": int(ADS_LAST_CHANNEL)
                }
                json_str = json.dumps(data)
                if uart:
                    uart.write(json_str + "\r\n")
                print(json_str)
            except Exception as e:
                if uart:
                    uart.write("ERR:JSON\r\n")

    idx += 1
    _sleep_with_uart(0.05)
