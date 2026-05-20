#!/usr/bin/env python3
"""
Script para regenerar automáticamente los rangos de calibración de laboratorio
cada vez que se agregan nuevos puntos de calibración a [LABORATORY_CALIBRATION]

Uso:
    python generate_lab_ranges.py
"""

import configparser
import os
import sys

def regenerate_laboratory_ranges(config_file="calibration_ranges.cfg"):
    """
    Lee [LABORATORY_CALIBRATION] del archivo y regenera automáticamente
    [LABORATORY_CALIBRATION_RANGES] basándose en los voltajes medidos.
    """
    
    if not os.path.exists(config_file):
        print(f"❌ Archivo {config_file} no encontrado")
        return False
    
    print("\n" + "="*60)
    print("REGENERADOR DE RANGOS DE CALIBRACIÓN DE LABORATORIO")
    print("="*60 + "\n")
    
    cfg = configparser.ConfigParser()
    try:
        cfg.read(config_file, encoding='utf-8')
    except Exception as e:
        cfg.read(config_file)
    
    if 'LABORATORY_CALIBRATION' not in cfg:
        print("❌ No existe sección [LABORATORY_CALIBRATION]")
        return False
    
    # Extraer puntos en el orden de la tabla (normalmente ascendente en µS/cm)
    # para evitar reordenar por voltaje cuando la curva no es monótona.
    calibration_points = []
    
    for cond_str, cal_data in cfg.items('LABORATORY_CALIBRATION'):
        try:
            known_cond = float(cond_str)
            parts = cal_data.split(',')
            
            if len(parts) >= 2:
                k_value = float(parts[0].strip())
                measured_voltage = float(parts[1].strip())
                calibration_points.append({
                    'k': k_value,
                    'cond': known_cond,
                    'voltage': measured_voltage,
                })
                print(f"📊 Punto: {known_cond:.0f} µS/cm @ {measured_voltage:.6f}V (k={k_value:.6f})")
            else:
                print(f"⚠️  Punto {cond_str} - formato inválido, debe ser 'k,voltage'")
        except Exception as e:
            print(f"❌ Error procesando {cond_str}: {e}")
            continue
    
    if len(calibration_points) < 1:
        print("❌ No hay puntos de calibración válidos")
        return False
    
    print(f"\n✓ Se encontraron {len(calibration_points)} puntos de calibración\n")
    
    # Ordenar por conductividad conocida para mantener el orden de calibración.
    sorted_points = sorted(calibration_points, key=lambda item: item['cond'])
    
    # Crear o limpiar sección de rangos
    if 'LABORATORY_CALIBRATION_RANGES' not in cfg:
        cfg.add_section('LABORATORY_CALIBRATION_RANGES')
    else:
        # Limpiar rangos previos
        for key in list(cfg.options('LABORATORY_CALIBRATION_RANGES')):
            cfg.remove_option('LABORATORY_CALIBRATION_RANGES', key)
    
    # Generar rangos por punto medio entre voltajes vecinos del orden de
    # calibración (µS/cm). Esto evita que el archivo quede reordenado por voltaje.
    #
    # Nota: si la curva V-EC no es monótona, pueden aparecer rangos solapados;
    # en ese caso el firmware en modo laboratorio usa puntos (no rangos).
    print("="*60)
    print("RANGOS GENERADOS:")
    print("="*60 + "\n")
    
    for i, data in enumerate(sorted_points):
        voltage = data['voltage']

        if i == 0:
            v_min = 0.0
            if len(sorted_points) > 1:
                next_voltage = sorted_points[i + 1]['voltage']
                v_max = (voltage + next_voltage) / 2.0
            else:
                v_max = max(voltage * 2.0, 10.0)
        elif i == len(sorted_points) - 1:
            prev_voltage = sorted_points[i - 1]['voltage']
            v_min = (prev_voltage + voltage) / 2.0
            v_max = max(voltage * 2.0, 10.0)
        else:
            prev_voltage = sorted_points[i - 1]['voltage']
            next_voltage = sorted_points[i + 1]['voltage']
            v_min = (prev_voltage + voltage) / 2.0
            v_max = (voltage + next_voltage) / 2.0

        if v_min > v_max:
            v_min, v_max = v_max, v_min
        
        range_key = f"{v_min:.6f},{v_max:.6f}"
        k_val = data['k']
        cfg.set('LABORATORY_CALIBRATION_RANGES', range_key, str(round(k_val, 6)))
        
        print(f"📍 Rango {i+1}: [{v_min:.6f}, {v_max:.6f}]")
        print(f"   → Valor K = {k_val:.6f}")
        print(f"   → Para solución: {data['cond']:.0f} µS/cm\n")
    
    # Guardar configuración
    print("="*60)
    print("GUARDANDO CONFIGURACIÓN...")
    print("="*60 + "\n")
    
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            cfg.write(f)
        print(f"✅ Configuración guardada correctamente")
        print(f"✅ Se generaron {len(sorted_points)} rangos de calibración\n")
        return True
    except Exception as e:
        print(f"❌ Error guardando configuración: {e}\n")
        return False

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "calibration_ranges.cfg")
    
    success = regenerate_laboratory_ranges(config_path)
    
    if success:
        print("✅ Rangos regenerados. El ESP32 ahora usará los valores")
        print("   de K correctos según el rango de voltaje medido.\n")
        sys.exit(0)
    else:
        print("❌ Hubo un error regenerando los rangos.\n")
        sys.exit(1)
