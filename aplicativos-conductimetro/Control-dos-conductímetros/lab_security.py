# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE SEGURIDAD - LABORATORIO
# ═══════════════════════════════════════════════════════════════
# Este archivo contiene la configuración de seguridad para acceso
# a funciones restringidas de laboratorio.

import hashlib

# Contraseña hasheada (SHA-256)
# Original: L4b0r4t0r10
PASSWORD_HASH = hashlib.sha256(b"L4b0r4t0r10").hexdigest()

def verify_password(password_input):
    """Verifica si la contraseña ingresada es correcta"""
    input_hash = hashlib.sha256(password_input.encode()).hexdigest()
    return input_hash == PASSWORD_HASH

# Configuración de calibración de laboratorio
LAB_CALIBRATION_CONFIG = {
    "range_min": 0,           # µS
    "range_max": 10000,       # µS (10 mS)
    "step": 50,            # µS (cada 50 µS)
    "num_points": 200, # Total de puntos (10000/100)
    "samples_per_point": 30,  # Muestras por punto
    "stabilization_time": 5,  # Segundos de estabilización
}
