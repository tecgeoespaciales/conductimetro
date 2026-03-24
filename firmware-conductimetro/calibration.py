import json
import os


def _exists(path):
    """Verifica existencia de archivo usando `os.stat` compatible con MicroPython.

    Args:
        path: Ruta de archivo a validar.

    Returns:
        True si la ruta existe, False en caso contrario.
    """
    try:
        os.stat(path)
        return True
    except Exception:
        return False


def get_k_for_ec(ec_value, k_table):
    """Obtiene el factor K para una EC mediante interpolación lineal por tabla.

    Reglas:
    - Si no hay tabla, retorna 1.0 (identidad).
    - Si el valor cae fuera del dominio, usa extrapolación por borde (clamp).
    - Si hay dos puntos envolventes, interpola linealmente.

    Args:
        ec_value: Conductividad base calculada por polinomio (sin K).
        k_table: Diccionario `{ec_ref: k_factor}`.

    Returns:
        Factor K aplicable a `ec_value`.
    """
    if not k_table:
        return 1.0

    keys = sorted(k_table.keys())

    if len(keys) == 1:
        return float(k_table[keys[0]])

    if ec_value <= keys[0]:
        return float(k_table[keys[0]])
    if ec_value >= keys[-1]:
        return float(k_table[keys[-1]])

    for idx in range(len(keys) - 1):
        x0 = keys[idx]
        x1 = keys[idx + 1]
        if x0 <= ec_value <= x1:
            y0 = float(k_table[x0])
            y1 = float(k_table[x1])
            if x1 == x0:
                return y0
            t = (ec_value - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)

    return 1.0


def apply_k(ec_poly, k_table, enabled=True):
    """Aplica calibración K a una EC base.

    Args:
        ec_poly: Conductividad base (sin calibrar).
        k_table: Tabla de calibración K.
        enabled: Si False, deshabilita calibración y usa K=1.0.

    Returns:
        Tupla `(ec_calibrada, k_factor_usado)`.
    """
    k_factor = get_k_for_ec(ec_poly, k_table) if enabled else 1.0
    return ec_poly * k_factor, k_factor


def load_k_table(path):
    """Carga tabla K desde JSON persistido.

    Convierte claves a `float` para operar numéricamente durante interpolación.

    Args:
        path: Ruta del archivo JSON.

    Returns:
        Diccionario `{ec_ref: k_factor}` o `{}` ante error/formato inválido.
    """
    if not _exists(path):
        return {}

    try:
        with open(path, "r") as file_handle:
            data = json.load(file_handle)
        if not isinstance(data, dict):
            return {}
        return {float(key): float(value) for key, value in data.items()}
    except Exception:
        return {}


def save_k_table(path, k_table):
    """Guarda tabla K como JSON serializable.

    Args:
        path: Ruta de destino.
        k_table: Diccionario `{ec_ref: k_factor}`.

    Returns:
        True si el guardado fue exitoso; False en error de E/S o serialización.
    """
    try:
        serializable = {str(key): value for key, value in k_table.items()}
        with open(path, "w") as file_handle:
            json.dump(serializable, file_handle)
        return True
    except Exception:
        return False


def store_k_point(k_table, known_ec, k_value):
    """Inserta/actualiza un punto de calibración validado en memoria.

    Args:
        k_table: Tabla K mutable.
        known_ec: EC de referencia (> 0).
        k_value: Factor K asociado (> 0).

    Returns:
        La misma tabla actualizada.

    Raises:
        ValueError: Si `known_ec` o `k_value` no son positivos.
    """
    known = float(known_ec)
    kval = float(k_value)
    if known <= 0 or kval <= 0:
        raise ValueError("known_ec y k deben ser > 0")
    k_table[known] = kval
    return k_table
