# ═══════════════════════════════════════════════════════════════
#                         IMPORTACIONES
# ═══════════════════════════════════════════════════════════════
import os
import sys
import time
import shutil
import threading
import configparser
import math
from collections import deque
from datetime import datetime
import csv

from matplotlib.pyplot import table, title
from pyparsing import line
import serial.tools.list_ports
from PyQt5.QtWidgets import *
from PyQt5.QtGui import QFont, QDoubleValidator, QColor, QPainter
from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QPropertyAnimation, pyqtProperty, QEasingCurve, QSize
import pyqtgraph as pg
import json
import re

try:
    from .serial_reader import SerialReader
    from .data_logger import DataLogger
    from .lab_security import verify_password, LAB_CALIBRATION_CONFIG
except Exception:
    # Allow running Aplicación/main.py directly (no package context)
    from serial_reader import SerialReader
    from data_logger import DataLogger
    from lab_security import verify_password, LAB_CALIBRATION_CONFIG


APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_CALIBRATION_CFG = os.path.join(APP_DIR, "calibration_ranges.cfg")

# ═══════════════════════════════════════════════════════════════
#            CONSTANTES Y FUNCIONES GLOBALES
# ═══════════════════════════════════════════════════════════════

POLY_A = 133.42
POLY_B = 255.86
POLY_C = 857.39
TEMP_REF = 25.0
COEF_TEMP = 0.02

def normalize_voltage_to_reference_temp(voltage, current_temp, ref_temp=25.0, coef=0.02):
    if current_temp is None or current_temp < -40 or current_temp > 125:
        current_temp = ref_temp

    if abs(current_temp - ref_temp) < 0.1:
        return voltage

    try:
        denominator = 1.0 + coef * (current_temp - ref_temp)
        if denominator > 0:
            return voltage / denominator
        return voltage
    except Exception:
        return voltage

def calculate_ec_from_voltage(voltage, temperature=25.0, k_table=None):
    try:
        if voltage == 0 or voltage is None:
            return 0.0

        ec_poly = POLY_A * float(voltage)**3 - POLY_B * float(voltage)**2 + POLY_C * float(voltage)
        if ec_poly < 0:
            ec_poly = 0.0

        if k_table and isinstance(k_table, dict) and len(k_table) > 0:
            closest_voltage = min(k_table.keys(), key=lambda x: abs(x - float(voltage)))
            k_factor = k_table[closest_voltage]
            ec_calibrated = ec_poly * k_factor
        else:
            ec_calibrated = ec_poly

        return round(ec_calibrated, 2)
    except Exception as e:
        print(f"Error calculating EC: {e}")
        return 0.0

def load_k_table_from_cfg():
    try:
        cfg_path = APP_CALIBRATION_CFG
        if not os.path.exists(cfg_path):
            return {}

        cfg = configparser.ConfigParser()
        try:
            cfg.read(cfg_path, encoding='utf-8')
        except Exception:
            cfg.read(cfg_path)

        k_table = {}
        if 'LABORATORY_CALIBRATION' in cfg:
            for _, calibration_data in cfg.items('LABORATORY_CALIBRATION'):
                try:
                    parts = calibration_data.split(',')
                    if len(parts) >= 2:
                        k_value = float(parts[0].strip())
                        voltage_at_25c = float(parts[1].strip())
                        k_table[voltage_at_25c] = k_value
                except Exception:
                    pass

        return k_table
    except Exception as e:
        print(f"Error loading K table: {e}")
        return {}

def send_show_ec_to_esp32(serial_reader, temperature, ec):
    try:
        if serial_reader and hasattr(serial_reader, 'send_command'):
            data = {"temp": round(temperature, 1), "ec": round(ec, 1)}
            command = f"SHOW_EC:{json.dumps(data)}"
            result = serial_reader.send_command(command)
            print(f"✓ SHOW_EC enviado: {data} (resultado: {result})")
            return result
        else:
            print("✗ serial_reader no disponible")
            return False
    except Exception as e:
        print(f"✗ Error enviando SHOW_EC: {e}")
        return False


class AnimatedToggle(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = 0.0
        self._margin = 3
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(56, 28)

        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self.stateChanged.connect(self._start_animation)

    def sizeHint(self):
        return QSize(56, 28)

    def _start_animation(self, _):
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if self.isChecked() else 0.0)
        self._animation.start()

    def get_offset(self):
        return self._offset

    def set_offset(self, value):
        self._offset = float(value)
        self.update()

    offset = pyqtProperty(float, fget=get_offset, fset=set_offset)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(1, 1, -1, -1)
        radius = rect.height() / 2

        track_color = QColor("#4caf50") if self.isChecked() else QColor("#bdbdbd")
        painter.setPen(Qt.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, radius, radius)

        handle_d = rect.height() - 2 * self._margin
        handle_x = rect.x() + self._margin + (rect.width() - 2 * self._margin - handle_d) * self._offset
        handle_y = rect.y() + self._margin

        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(int(handle_x), int(handle_y), int(handle_d), int(handle_d))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setChecked(not self.isChecked())
            event.accept()
            return
        super().mouseReleaseEvent(event)

# ═══════════════════════════════════════════════════════════════
#              VENTANA DE GRÁFICA CSV
# ═══════════════════════════════════════════════════════════════
class CSVPlotWindow(QWidget):
    """
    Ventana para visualizar gráficas de datos de conductividad y temperatura desde archivos CSV.
    
    Crea dos gráficas (conductividad y temperatura) en tiempo real con estadísticas detalladas
    incluyendo valores mínimos, máximos, promedios y duración total de muestreo.
    """
    
    def __init__(self, csv_data, filename, device_label="Dispositivo 1"):
        super().__init__()
        self.csv_data = csv_data
        self.filename = filename
        self.device_label = device_label
        self.setup_ui()

    def setup_ui(self):
        """
        Construye la interfaz visual con gráficas interactivas y tabla de estadísticas.
        
        Configura dos gráficas de PyQtGraph (conductividad y temperatura) con grid,
        self.setWindowTitle(f"Gráfica CSV ({self.device_label})")
        self.resize(900, 700)"""

        layout = QVBoxLayout()

        plot_sensor = pg.PlotWidget(title="Conductividad (uS)")
        plot_sensor.showGrid(x=True, y=True)
        plot_sensor.setLabel('left', 'Conductividad', 'uS')
        plot_sensor.setLabel('bottom', 'Muestras')
        plot_sensor.getAxis('left').enableAutoSIPrefix(False)
        try:
            plot_sensor.getAxis('left').setTickSpacing(major=100.0, minor=20.0)
        except Exception:
            pass

        plot_temp = pg.PlotWidget(title="Temperatura (°C)")
        plot_temp.showGrid(x=True, y=True)
        plot_temp.setLabel('left', 'Temperatura', '°C')
        plot_temp.setLabel('bottom', 'Muestras')
        plot_temp.getAxis('left').enableAutoSIPrefix(False)
        try:
            plot_temp.getAxis('left').setTickSpacing(major=2.0, minor=0.5)
        except Exception:
            pass

        timestamps = list(range(len(self.csv_data['sensor'])))
        sensor_values = [round(v, 1) for v in self.csv_data['sensor']]
        temp_values = [round(v, 1) for v in self.csv_data['temp']]
        plot_sensor.plot(timestamps, sensor_values, pen='r', symbol='o', symbolSize=3)
        plot_temp.plot(timestamps, temp_values, pen='b', symbol='o', symbolSize=3)

        try:
            if sensor_values:
                sensor_span = max(max(sensor_values) - min(sensor_values), 1.0)
                sensor_major = max(1.0, round(sensor_span / 6.0, 1))
                sensor_minor = max(0.2, round(sensor_major / 5.0, 1))
                plot_sensor.getAxis('left').setTickSpacing(major=sensor_major, minor=sensor_minor)
        except Exception:
            pass

        try:
            if temp_values:
                temp_span = max(max(temp_values) - min(temp_values), 0.5)
                temp_major = max(0.2, round(temp_span / 6.0, 1))
                temp_minor = max(0.1, round(temp_major / 2.0, 1))
                plot_temp.getAxis('left').setTickSpacing(major=temp_major, minor=temp_minor)
        except Exception:
            pass

        stats_text = self.calculate_stats()
        stats_label = QLabel()
        stats_label.setTextFormat(Qt.RichText)
        stats_label.setText(stats_text)
        stats_label.setStyleSheet("background: #f0f0f0; padding: 10px; border: 1px solid #ccc; font-family: Arial;")
        stats_label.setFont(QFont("Arial", 10))

        close_btn = QPushButton("Cerrar Gráfica")
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet("padding: 8px; background: #ff6b6b; color: white; border: none; border-radius: 4px;")

        layout.addWidget(plot_sensor)
        layout.addWidget(plot_temp)
        layout.addWidget(stats_label)
        layout.addWidget(close_btn)

        self.setLayout(layout)

    def calculate_stats(self):
        """
        Calcula y formatea estadísticas de los datos CSV en tabla HTML.
        
        Calcula valores mínimo, máximo, promedio, desviación estándar y
        duración total de muestreo. Retorna tabla HTML formateada con colores.
        
        Returns:
            str: HTML con tabla de estadísticas formateada con secciones coloreadas
        """
        sensor_data = self.csv_data['sensor']
        temp_data = self.csv_data['temp']

        if not sensor_data or not temp_data:
            return "No hay datos para mostrar"

        file_date = "No disponible"
        try:
            filename = os.path.basename(self.filename)
            if 'datos_esp32_' in filename:
                date_part = filename.split('datos_esp32_')[1].split('.')[0]
                file_date = datetime.strptime(date_part, '%Y%m%d_%H%M%S').strftime('%Y-%m-%d %H:%M:%S')
            else:
                file_stats = os.stat(self.filename)
                file_date = datetime.fromtimestamp(file_stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        except:
            file_date = "No disponible"

        num_samples = len(sensor_data)
        total_seconds = max(num_samples, 1) * 2
        if total_seconds >= 3600:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            duration_str = f"{hours}h {minutes}m (estimado)"
        elif total_seconds >= 60:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            duration_str = f"{minutes}m {seconds}s (estimado)"
        else:
            duration_str = f"{total_seconds}s (estimado)"

        sensor_min = min(sensor_data)
        sensor_max = max(sensor_data)
        sensor_avg = sum(sensor_data) / len(sensor_data)
        temp_min = min(temp_data)
        temp_max = max(temp_data)
        temp_avg = sum(temp_data) / len(temp_data)

        stats = f"""
<html>
<head>
<style>
table {{ border-collapse: collapse; width: 100%; font-family: Arial; margin: 0 auto; }}
th {{ background-color: #e0e0e0; padding: 8px; text-align: center; border: 1px solid #ccc; }}
td {{ padding: 8px; border: 1px solid #ccc; text-align: center; }}
.section-header {{ background-color: #4CAF50; color: white; font-weight: bold; text-align: center; }}
.conductividad-header {{ background-color: #ff4444; color: white; text-align: center; }}
.temperatura-header {{ background-color: #4444ff; color: white; text-align: center; }}
.datos-header {{ background-color: #888888; color: white; text-align: center; }}
</style>
</head>
<body>
<div style="text-align: center; font-weight: bold; font-size: 14px; margin-bottom: 10px;">
ESTADÍSTICAS
</div>
<table>
    <tr>
        <th class="section-header" colspan="4">ESTADÍSTICAS DE DATOS</th>
        <th class="section-header" colspan="4">DATOS DEL DOCUMENTO CSV</th>
    </tr>
    <tr>
        <th class="conductividad-header" colspan="2">CONDUCTIVIDAD</th>
        <th class="temperatura-header" colspan="2">TEMPERATURA</th>
        <th class="datos-header" colspan="2">MÉTRICAS</th>
        <th class="datos-header" colspan="2">DATOS</th>
    </tr>
    <tr>
        <td><b>Mínimo:</b></td>
        <td>{sensor_min:.1f} uS</td>
        <td><b>Mínimo:</b></td>
        <td>{temp_min:.1f} °C</td>
        <td colspan="2"><b>Fecha de muestras:</b></td>
        <td colspan="2">{file_date}</td>
    </tr>
    <tr>
        <td><b>Máximo:</b></td>
        <td>{sensor_max:.1f} uS</td>
        <td><b>Máximo:</b></td>
        <td>{temp_max:.1f} °C</td>
        <td colspan="2"><b>Duración de la toma:</b></td>
        <td colspan="2">{duration_str}</td>
    </tr>
    <tr>
        <td><b>Promedio:</b></td>
        <td>{sensor_avg:.1f} uS</td>
        <td><b>Promedio:</b></td>
        <td>{temp_avg:.1f} °C</td>
        <td colspan="2"><b>Total de puntos:</b></td>
        <td colspan="2">{len(sensor_data)} muestras</td>
    </tr>
</table>
</body>
</html>
"""
        return stats


# ═══════════════════════════════════════════════════════════════
#         DIÁLOGO DE CONTRASEÑA PARA LABORATORIO
# ═══════════════════════════════════════════════════════════════
class LaboratoryPasswordDialog(QDialog):
    """
    Diálogo de contraseña para acceder a calibración de laboratorio.
    
    Valida el acceso a funcionalidades restringidas usando el sistema de
    autenticación de lab_security.
    """
    
    def __init__(self):
        super().__init__()
        self.password_input = None
        self.setup_ui()
    
    def setup_ui(self):
        self.setWindowTitle("Acceso - Calibración de Laboratorio")
        self.setFixedSize(400, 200)
        self.setStyleSheet("""
            QDialog {
                background: #f5f5f5;
            }
            QLineEdit {
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 8px;
                font-size: 12px;
            }
            QPushButton {
                border: none;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton#acceptBtn {
                background: #4caf50;
                color: white;
            }
            QPushButton#acceptBtn:hover {
                background: #45a049;
            }
            QPushButton#rejectBtn {
                background: #f44336;
                color: white;
            }
            QPushButton#rejectBtn:hover {
                background: #da190b;
            }
        """)
        
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Advertencia
        warning_label = QLabel("⚠️ ACCESO RESTRINGIDO")
        warning_label.setFont(QFont("Arial", 12, QFont.Bold))
        warning_label.setStyleSheet("color: #d32f2f;")
        warning_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(warning_label)
        
        desc_label = QLabel("Esta calibración solo puede ser realizada por personal de laboratorio.\n\n"
                           "Ingrese la contraseña:")
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #333;")
        layout.addWidget(desc_label)
        
        # Input de contraseña
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Contraseña...")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.returnPressed.connect(self.verify_password_input)
        layout.addWidget(self.password_input)
        
        # Botones
        button_layout = QHBoxLayout()
        
        accept_btn = QPushButton("Aceptar")
        accept_btn.setObjectName("acceptBtn")
        accept_btn.setMinimumHeight(35)
        accept_btn.clicked.connect(self.verify_password_input)
        button_layout.addWidget(accept_btn)
        
        reject_btn = QPushButton("Cancelar")
        reject_btn.setObjectName("rejectBtn")
        reject_btn.setMinimumHeight(35)
        reject_btn.clicked.connect(self.reject)
        button_layout.addWidget(reject_btn)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
        
        self.password_input.setFocus()
    
    def verify_password_input(self):
        """Valida la contraseña ingresada mediante el sistema de autenticación.
        
        Compara la entrada contra la contraseña configurada en lab_security.
        Si es correcta, acepta el diálogo; si no, muestra error y limpia el campo.
        """
        if verify_password(self.password_input.text()):
            self.accept()
        else:
            QMessageBox.warning(self, "Error", "Contraseña incorrecta.")
            self.password_input.clear()
            self.password_input.setFocus()
            self.password_input.clear()
            self.password_input.setFocus()


# ═══════════════════════════════════════════════════════════════
#    DIÁLOGO DE CALIBRACIÓN DE LABORATORIO (0-10 mS)
# ═══════════════════════════════════════════════════════════════
class LaboratoryCalibrationDialog(QDialog):
    """
    Diálogo para calibración de laboratorio en rango 0-10,000 µS.
    
    Inicia con una fila editable y permite agregar más según sea necesario.
    El usuario ingresa los puntos de calibración directamente.
    """
    
    def __init__(self, app, num_points=20, replace_existing=False):
        super().__init__()
        self.app = app
        self.serial_reader = None
        self.connected = False
        self.replace_existing = bool(replace_existing)
        self.num_points = 1  # Iniciar con 1, agregar más según sea necesario
        self.calibration_data = {}
        self.measure_buttons = {}
        self.known_cond_inputs = {}
        self.temp_labels = {}
        self.measured_labels = {}
        self.k_labels = {}
        self.estimated_labels = {}
        
        self._sampling_timer = QTimer(self)
        self._sampling_timer.setInterval(100)
        self._sampling_timer.timeout.connect(self._sampling_tick)
        self._sampling_row = None
        self._sampling_known_cond = None
        self._sampling_phase = None  # 'stabilizing', 'capturing', None
        self._stabilize_remaining = 0
        self._samples_to_capture = 0
        self._sampling_samples = []
        self._sampling_temps = []
        
        self.setWindowTitle("Calibración de Laboratorio (0-10 mS)")
        self.setFixedSize(1000, 650)
        self.setStyleSheet("""
            QDialog {
                background: #f5f5f5;
            }
            QTableWidget {
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QLineEdit {
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 5px;
            }
            QPushButton {
                border: none;
                border-radius: 3px;
                padding: 6px;
                font-weight: bold;
            }
            QSpinBox {
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 5px;
            }
        """)
        self.init_ui()
    
    def init_ui(self):
        """Construye la interfaz de calibración de laboratorio"""
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("Calibración de Laboratorio - Editable (0-10 mS)")
        title.setFont(QFont("Arial", 13, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)
        
        info = QLabel(
            "Proceso de Calibración de Laboratorio:\n"
            "1. Ingrese la conductividad conocida de cada solución (µS)\n"
            "2. Configure parámetros de captura (muestras y estabilización)\n"
            "3. Presione 'Medir' para capturar datos de cada solución\n"
            "4. La temperatura se capturará automáticamente del sensor ESP32\n"
            "5. Los voltajes se normalizarán a 25°C (referencia de calibración)\n"
            "6. Se calculará automáticamente el factor K para cada punto\n"
            "7. Agregue filas adicionales con el botón '+ Agregar Fila'\n"
            "8. Los datos se guardarán en ESP32 al finalizar"
        )
        info.setWordWrap(True)
        info.setStyleSheet("background: #b3e5fc; padding: 10px; border-radius: 4px; border-left: 4px solid #0288d1;")
        main_layout.addWidget(info)
        
        # Configuración de captura
        config_group = QGroupBox("Configuración de Captura")
        config_layout = QHBoxLayout()
        config_layout.addWidget(QLabel("Muestras a capturar:"))
        self.samples_spinbox = QSpinBox()
        self.samples_spinbox.setRange(10, 500)
        self.samples_spinbox.setValue(30)
        self.samples_spinbox.setMinimumWidth(70)
        config_layout.addWidget(self.samples_spinbox)
        
        config_layout.addWidget(QLabel("Estabilización (s):"))
        self.stabilize_spinbox = QSpinBox()
        self.stabilize_spinbox.setRange(0, 30)
        self.stabilize_spinbox.setValue(5)
        self.stabilize_spinbox.setMinimumWidth(70)
        config_layout.addWidget(self.stabilize_spinbox)
        config_layout.addStretch()
        config_group.setLayout(config_layout)
        main_layout.addWidget(config_group)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        self.table = QTableWidget()
        self.table.setRowCount(self.num_points)
        self.table.setColumnCount(6)
        headers = ["Conductividad Conocida (µS)", "Temperatura (°C) [Auto]", 
               "Botón Medir", "Voltaje @ 25°C (V)", "", "Estimado (µS)"]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setColumnHidden(4, True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 135)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 140)
        
        # Crear filas iniciales (editable, comienza con 1)
        for row in range(self.num_points):
            self.add_table_row(row)
        
        scroll.setWidget(self.table)
        main_layout.addWidget(scroll)
        
        # Botón para agregar filas
        add_row_btn = QPushButton("+ Agregar Fila")
        add_row_btn.setMinimumHeight(35)
        add_row_btn.setStyleSheet("""
            QPushButton {
                background: #4caf50;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #45a049;
            }
        """)
        add_row_btn.clicked.connect(self.add_new_row)
        main_layout.addWidget(add_row_btn)
        
        self.status_label = QLabel("Esperando calibración de laboratorio...")
        self.status_label.setStyleSheet("padding: 8px; background: #b3e5fc; border-radius: 3px; border-left: 4px solid #0288d1;")
        main_layout.addWidget(self.status_label)
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        close_btn = QPushButton("Finalizar Calibración de Laboratorio")
        close_btn.setMinimumHeight(40)
        close_btn.clicked.connect(self.finalize_calibration)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #0288d1;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #0277bd;
            }
        """)
        button_layout.addWidget(close_btn)
        main_layout.addLayout(button_layout)
        
        self.setLayout(main_layout)
    
    def add_table_row(self, row):
        """Agrega una fila a la tabla con todos los controles necesarios"""
        # Campo editable para la conductividad conocida
        cond_input = QLineEdit()
        cond_input.setAlignment(Qt.AlignCenter)
        cond_input.setPlaceholderText("Ingrese µS")
        validator = QDoubleValidator(0.0, 10000.0, 2)
        validator.setNotation(QDoubleValidator.StandardNotation)
        cond_input.setValidator(validator)
        cond_input.setStyleSheet("background: #ffffff; border-radius: 3px; padding: 8px;")
        self.known_cond_inputs[row] = cond_input
        self.table.setCellWidget(row, 0, cond_input)
        
        temp_label = QLabel("--")
        temp_label.setAlignment(Qt.AlignCenter)
        temp_label.setStyleSheet("background: #f0f0f0; border-radius: 3px;")
        self.temp_labels[row] = temp_label
        self.table.setCellWidget(row, 1, temp_label)
        
        measure_btn = QPushButton("Medir")
        measure_btn.setStyleSheet("""
            QPushButton {
                background: #0288d1;
                color: white;
            }
            QPushButton:hover {
                background: #0277bd;
            }
            QPushButton:pressed {
                background: #01579b;
            }
        """)
        measure_btn.clicked.connect(lambda checked, r=row: self.measure_sample(r))
        self.measure_buttons[row] = measure_btn
        self.table.setCellWidget(row, 2, measure_btn)
        
        measured_label = QLabel("---")
        measured_label.setAlignment(Qt.AlignCenter)
        measured_label.setStyleSheet("background: #f9f9f9; border-radius: 3px;")
        self.measured_labels[row] = measured_label
        self.table.setCellWidget(row, 3, measured_label)
        
        k_label = QLabel("---")
        k_label.setAlignment(Qt.AlignCenter)
        k_label.setStyleSheet("background: #f9f9f9; border-radius: 3px;")
        self.k_labels[row] = k_label
        self.table.setCellWidget(row, 4, k_label)
        
        estimated_label = QLabel("---")
        estimated_label.setAlignment(Qt.AlignCenter)
        estimated_label.setStyleSheet("background: #f9f9f9; border-radius: 3px;")
        self.estimated_labels[row] = estimated_label
        self.table.setCellWidget(row, 5, estimated_label)
    
    def add_new_row(self):
        """Agrega una nueva fila a la tabla"""
        new_row = self.table.rowCount()
        self.table.insertRow(new_row)
        self.add_table_row(new_row)
        self.num_points += 1
        self.status_label.setText(f"Fila agregada. Total de filas: {self.num_points}")
    
    def _find_mode_with_tolerance(self, samples, tolerance=0.002):
        """Calcula la moda con tolerancia"""
        if not samples:
            return None
        
        samples_sorted = sorted(samples)
        clusters = []
        current_cluster = [samples_sorted[0]]
        
        for val in samples_sorted[1:]:
            if abs(val - current_cluster[-1]) <= tolerance:
                current_cluster.append(val)
            else:
                if current_cluster:
                    center = sum(current_cluster) / len(current_cluster)
                    clusters.append((current_cluster[:], center, len(current_cluster)))
                current_cluster = [val]
        
        if current_cluster:
            center = sum(current_cluster) / len(current_cluster)
            clusters.append((current_cluster[:], center, len(current_cluster)))
        
        if not clusters:
            return None
        
        clusters.sort(key=lambda x: x[2], reverse=True)
        mode_value = clusters[0][1]
        return mode_value
    
    def set_serial(self, serial_reader, connected, app=None):
        """Asigna conexión serial"""
        self.serial_reader = serial_reader
        self.connected = bool(connected)
        if app is not None:
            self.app = app

    def _wait_for_live_raw(self, timeout_ms=3000):
        """Espera una lectura válida de voltaje crudo (raw/signal) sin bloquear la UI."""
        end_time = time.time() + (timeout_ms / 1000.0)
        while time.time() < end_time:
            try:
                cr = getattr(self.app, 'current_reading', {}) or {}
                raw_val = cr.get('raw')
                if raw_val is None:
                    raw_val = cr.get('signal')
                if raw_val is not None:
                    return True
                # Si ya llegaron lecturas recientes aunque raw todavía no esté listo,
                # no bloquear el flujo de calibración con una falsa negativa.
                if cr.get('sensor') is not None or cr.get('temp') is not None:
                    return True
            except Exception:
                pass

            try:
                QApplication.processEvents()
            except Exception:
                pass
            try:
                time.sleep(0.05)
            except Exception:
                pass

        return False
    
    def measure_sample(self, row):
        """Inicia medición de una fila específica"""
        if not self.connected or not self.serial_reader:
            QMessageBox.warning(self, "Error", "No conectado al ESP32.")
            return

        # Asegurar que exista al menos una lectura cruda reciente antes de capturar.
        self.status_label.setText("Esperando datos en vivo del ESP32 para iniciar captura...")
        self.status_label.setStyleSheet("padding: 8px; background: #fff3e0; border-radius: 3px; border-left: 4px solid #ff9800;")
        try:
            self.serial_reader.send_command("LOG_START")
        except Exception:
            pass
        if not self._wait_for_live_raw(timeout_ms=3000):
            QMessageBox.warning(
                self,
                "Sin datos en vivo",
                "No llegan lecturas del ESP32.\n"
                "Verifique conexión serial y que el dispositivo esté enviando lecturas."
            )
            self.status_label.setText("✗ No se pudo iniciar la captura: sin datos crudos")
            self.status_label.setStyleSheet("padding: 8px; background: #ffebee; border-radius: 3px; border-left: 4px solid #f44336;")
            return
        
        known_cond_text = self.known_cond_inputs[row].text().strip()
        if not known_cond_text:
            QMessageBox.warning(self, "Error", "Ingrese la conductividad conocida para esta muestra.")
            self.known_cond_inputs[row].setFocus()
            return
        
        try:
            known_cond = float(known_cond_text)
        except ValueError:
            QMessageBox.warning(self, "Error", "La conductividad debe ser un número válido.")
            return
        
        # Obtener parámetros de captura
        num_samples = self.samples_spinbox.value()
        stabilize_seconds = self.stabilize_spinbox.value()
        
        for btn in self.measure_buttons.values():
            btn.setEnabled(False)
        
        # Inicializar estado de muestreo
        self._sampling_row = row
        self._sampling_known_cond = known_cond
        self._sampling_phase = 'stabilizing'
        self._stabilize_remaining = stabilize_seconds
        self._samples_to_capture = num_samples
        self._sampling_samples = []
        self._sampling_temps = []
        
        # Actualizar UI
        self.measured_labels[row].setText("...")
        self.measured_labels[row].setStyleSheet("background: #fff3e0; border-radius: 3px;")
        self.k_labels[row].setText("...")
        self.k_labels[row].setStyleSheet("background: #fff3e0; border-radius: 3px;")
        self.estimated_labels[row].setText("...")
        self.estimated_labels[row].setStyleSheet("background: #fff3e0; border-radius: 3px;")
        
        self.status_label.setText(f"Punto {row + 1}: Estabilizando {self._stabilize_remaining}s...")
        self.status_label.setStyleSheet("padding: 8px; background: #fff3e0; border-radius: 3px; border-left: 4px solid #ff9800;")
        
        self._sampling_timer.start()
    
    def _sampling_tick(self):
        """Ejecuta un ciclo de muestreo para calibración de laboratorio.

        Alterna entre estabilización y captura en ventanas de 100 ms, leyendo
        señal cruda/temperatura desde `current_reading` y actualizando progreso.
        Cuando completa la cuota de muestras, delega en `_finish_measurement`.
        """
        try:
            raw_val = None
            temp_val = None
            if hasattr(self.app, 'current_reading') and self.app.current_reading:
                cr = self.app.current_reading
                if cr.get('raw') is not None:
                    raw_val = cr.get('raw')
                elif cr.get('signal') is not None:
                    raw_val = cr.get('signal')
                temp_val = cr.get('temp')
        except Exception:
            pass
        
        # Fase de estabilización: espera antes de capturar para reducir transitorios.
        if self._sampling_phase == 'stabilizing':
            self._stabilize_remaining -= 0.1
            
            if self._stabilize_remaining > 0:
                self.status_label.setText(
                    f"Punto {self._sampling_row + 1}: Estabilizando {self._stabilize_remaining:.1f}s..."
                )
            else:
                self._sampling_phase = 'capturing'
                self._sampling_samples = []
                self._sampling_temps = []
                self.status_label.setText(
                    f"Punto {self._sampling_row + 1}: Capturando 0/{self._samples_to_capture} muestras..."
                )
                return
        
        # Fase de captura: acumula muestras válidas para cálculo robusto.
        elif self._sampling_phase == 'capturing':
            if raw_val is not None:
                try:
                    self._sampling_samples.append(float(raw_val))
                except Exception:
                    pass
            if temp_val is not None:
                try:
                    self._sampling_temps.append(float(temp_val))
                except Exception:
                    pass
            
            num_samples = len(self._sampling_samples)
            
            if num_samples >= self._samples_to_capture:
                self._sampling_timer.stop()
                self._sampling_phase = None
                self._finish_measurement()
            else:
                self.status_label.setText(
                    f"Punto {self._sampling_row + 1}: Capturando {num_samples}/{self._samples_to_capture} muestras..."
                )
    
    def _finish_measurement(self):
        """Consolida el punto de calibración medido en modo laboratorio.

        Flujo:
        1) Obtiene voltaje representativo como PROMEDIO de todas las muestras.
        2) Normaliza voltaje a 25°C para mantener referencia homogénea.
        3) Calcula K y persiste par voltaje@25°C -> K en configuración.
        4) Refresca estado visual y estructura `calibration_data`.
        """
        row = self._sampling_row
        known_cond = self._sampling_known_cond
        samples = [s for s in self._sampling_samples if s is not None]
        temps = [t for t in self._sampling_temps if t is not None]
        
        for btn in self.measure_buttons.values():
            btn.setEnabled(True)
        
        if not samples:
            QMessageBox.warning(self, "Error", f"No se obtuvieron muestras para el punto {row + 1}.")
            self.status_label.setText(f"✗ No se pudo medir el punto {row + 1}")
            return
        
        # Usar el promedio de TODAS las muestras capturadas
        measured_value = sum(samples) / len(samples) if samples else 0
        if measured_value is None or measured_value == 0:
            QMessageBox.warning(self, "Error", "El promedio de la señal cruda es 0. Revise la conexión.")
            return
        
        # Temperatura efectiva de la captura; si no hay muestras válidas usa 25°C.
        temp_read = (sum(temps) / len(temps)) if temps else 25.0
        
        if temp_read < -40 or temp_read > 125:
            temp_read = 25.0
        
        # Se normaliza a 25°C para que todos los puntos queden en el mismo marco térmico.
        voltage_at_25c = normalize_voltage_to_reference_temp(
            measured_value, temp_read, ref_temp=25.0, coef=COEF_TEMP
        )
        
        # EC base evaluada sobre voltaje ya normalizado.
        ec_measured = 133.42 * voltage_at_25c**3 - 255.86 * voltage_at_25c**2 + 857.39 * voltage_at_25c
        
        k_value = known_cond / ec_measured if ec_measured > 0 else 0
        estimated_at_temp = k_value * ec_measured
        
        self.calibration_data[row] = {
            'known_cond': known_cond,
            'measured': voltage_at_25c,
            'k': k_value,
            'estimated_at_temp': estimated_at_temp,
            'temp': temp_read
        }
        
        try:
            cfg_path = APP_CALIBRATION_CFG
            cfg = configparser.ConfigParser()
            if os.path.exists(cfg_path):
                try:
                    cfg.read(cfg_path, encoding='utf-8')
                except Exception:
                    cfg.read(cfg_path)
            
            if 'CALIBRATION_K_TABLE' not in cfg:
                cfg.add_section('CALIBRATION_K_TABLE')
            
            voltage_key = f"{voltage_at_25c:.6f}"
            cfg.set('CALIBRATION_K_TABLE', voltage_key, str(round(k_value, 6)))
            
            with open(cfg_path, 'w', encoding='utf-8') as f:
                cfg.write(f)
        except Exception:
            pass
        
        # Refleja resultado consolidado en la fila medida.
        self.measured_labels[row].setText(f"{voltage_at_25c:.1f}")
        self.measured_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
        k_truncated = int(k_value * 100) / 100
        self.k_labels[row].setText(f"{k_truncated:.1f}")
        self.k_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
        self.estimated_labels[row].setText(f"~{estimated_at_temp:.1f}")
        self.estimated_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
        self.temp_labels[row].setText(f"{temp_read:.1f}°C")
        self.temp_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
        
        k_truncated = int(k_value * 100) / 100
        self.status_label.setText(
            f"✓ Punto {row + 1}: K={k_truncated:.2f}, EC@{temp_read:.1f}°C={estimated_at_temp:.1f}µS"
        )
        self.status_label.setStyleSheet(
            "padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;"
        )
    
    def regenerate_laboratory_ranges(self, archivo_cfg="calibration_ranges.cfg"):
        """
        Lee [LABORATORY_CALIBRATION] del archivo y regenera automáticamente
        [LABORATORY_CALIBRATION_RANGES] basándose en los voltajes medidos.
        
        Esta función funciona incluso cuando los datos se han añadido manualmente al archivo.
        """
        try:
            if archivo_cfg == "calibration_ranges.cfg":
                archivo_cfg = APP_CALIBRATION_CFG
            if not os.path.exists(archivo_cfg):
                print(f"❌ Archivo {archivo_cfg} no encontrado")
                return False
            
            cfg = configparser.ConfigParser()
            cfg.read(archivo_cfg, encoding='utf-8')
            
            if 'LABORATORY_CALIBRATION' not in cfg:
                print("❌ No existe sección [LABORATORY_CALIBRATION]")
                return False
            
            # Extraer voltajes medidos y valores de K desde el archivo
            calibration_points = {}
            for cond_str, cal_data in cfg.items('LABORATORY_CALIBRATION'):
                try:
                    known_cond = float(cond_str)
                    parts = cal_data.split(',')
                    
                    if len(parts) >= 2:
                        k_value = float(parts[0].strip())
                        measured_voltage = float(parts[1].strip())
                        calibration_points[measured_voltage] = {
                            'k': k_value,
                            'cond': known_cond
                        }
                except Exception:
                    continue
            
            if len(calibration_points) < 1:
                print("❌ No hay puntos de calibración válidos con voltaje medido")
                return False
            
            # Crear o limpiar sección de rangos
            if 'LABORATORY_CALIBRATION_RANGES' not in cfg:
                cfg.add_section('LABORATORY_CALIBRATION_RANGES')
            else:
                for key in list(cfg.options('LABORATORY_CALIBRATION_RANGES')):
                    cfg.remove_option('LABORATORY_CALIBRATION_RANGES', key)
            
            # Ordenar puntos por voltaje
            sorted_points = sorted(calibration_points.items())
            
            # Generar rangos usando puntos medios entre voltajes
            for i, (voltage, data) in enumerate(sorted_points):
                if i == 0:
                    v_min = 0.0
                    if len(sorted_points) > 1:
                        next_voltage = sorted_points[i + 1][0]
                        v_max = (voltage + next_voltage) / 2.0
                    else:
                        v_max = voltage * 2.0
                elif i == len(sorted_points) - 1:
                    prev_voltage = sorted_points[i - 1][0]
                    v_min = (prev_voltage + voltage) / 2.0
                    v_max = max(voltage * 2.0, 10.0)
                else:
                    prev_voltage = sorted_points[i - 1][0]
                    next_voltage = sorted_points[i + 1][0]
                    v_min = (prev_voltage + voltage) / 2.0
                    v_max = (voltage + next_voltage) / 2.0
                
                range_key = f"{v_min:.6f},{v_max:.6f}"
                k_val = data['k']
                cfg.set('LABORATORY_CALIBRATION_RANGES', range_key, str(round(k_val, 6)))
            
            # Guardar configuración
            with open(archivo_cfg, 'w', encoding='utf-8') as f:
                cfg.write(f)
            
            print(f"✓ {len(sorted_points)} rangos de calibración regenerados en [LABORATORY_CALIBRATION_RANGES]")
            return True
        
        except Exception as e:
            print(f"❌ Error regenerando rangos de calibración: {e}")
            return False
    
    def save_laboratory_calibration(self, archivo_cfg="calibration_ranges.cfg"):
        """Guarda calibración de laboratorio en cfg y genera rangos automáticamente"""
        try:
            if archivo_cfg == "calibration_ranges.cfg":
                archivo_cfg = APP_CALIBRATION_CFG
            # Si replace_existing=True, PRIMERO enviar comando de reset al microcontrolador
            if self.replace_existing:
                print(f"🗑️  BORRANDO datos anteriores (replace_existing={self.replace_existing})")
                if self.serial_reader and self.connected:
                    print("📤 Enviando comando CAL_RESET al microcontrolador...")
                    try:
                        self.serial_reader.send_command("CAL_RESET")
                        print("✓ CAL_RESET enviado al microcontrolador")
                    except Exception as e:
                        print(f"⚠️  No se pudo enviar CAL_RESET: {e}")
            else:
                print(f"➕ AGREGANDO a calibración existente (replace_existing={self.replace_existing})")
            
            cfg = configparser.ConfigParser()
            if os.path.exists(archivo_cfg):
                cfg.read(archivo_cfg, encoding='utf-8')

            # LIMPIAR SECCIONES SI SE SOLICITA REEMPLAZAR
            if self.replace_existing:
                if cfg.has_section('LABORATORY_CALIBRATION'):
                    cfg.remove_section('LABORATORY_CALIBRATION')
                if cfg.has_section('LABORATORY_CALIBRATION_RANGES'):
                    cfg.remove_section('LABORATORY_CALIBRATION_RANGES')
            
            if 'LABORATORY_CALIBRATION' not in cfg:
                cfg.add_section('LABORATORY_CALIBRATION')
            
            # Guardar datos de calibración y construir tabla de voltajes-K
            for row, data in sorted(self.calibration_data.items()):
                known_cond = data['known_cond']
                k_value = data['k']
                measured = data.get('measured')
                if measured is None:
                    cfg.set('LABORATORY_CALIBRATION', str(int(known_cond)), str(round(k_value, 6)))
                else:
                    cfg.set('LABORATORY_CALIBRATION', str(int(known_cond)), 
                           f"{round(k_value, 6)},{measured:.6f}")

            # Guardar también la tabla auxiliar en el formato que espera la app al cargar.
            if 'CALIBRATION_K_TABLE' not in cfg:
                cfg.add_section('CALIBRATION_K_TABLE')
            else:
                for key in list(cfg.options('CALIBRATION_K_TABLE')):
                    cfg.remove_option('CALIBRATION_K_TABLE', key)
            for row, data in sorted(self.calibration_data.items()):
                measured = data.get('measured')
                k_value = data['k']
                if measured is not None:
                    cfg.set('CALIBRATION_K_TABLE', f"{float(measured):.6f}", str(round(k_value, 6)))
            
            with open(archivo_cfg, 'w', encoding='utf-8') as f:
                cfg.write(f)
            print(f"✓ Datos guardados: {len(self.calibration_data)} puntos en {archivo_cfg}")
            
            # Regenerar rangos automáticamente
            self.regenerate_laboratory_ranges(archivo_cfg)
            return True
        except Exception as e:
            print(f"Error al guardar calibración de laboratorio: {e}")
            return False
    
    def check_and_regenerate_lab_ranges(self, archivo_cfg="calibration_ranges.cfg"):
        """
        Verifica automáticamente si hay cambios en [LABORATORY_CALIBRATION]
        que requieran regeneración de [LABORATORY_CALIBRATION_RANGES].
        
        Se ejecuta al iniciar la aplicación para asegurar consistencia.
        """
        try:
            if not os.path.exists(archivo_cfg):
                return False
            
            cfg = configparser.ConfigParser()
            cfg.read(archivo_cfg, encoding='utf-8')
            
            if 'LABORATORY_CALIBRATION' not in cfg:
                return False
            
            # Contar puntos de calibración
            lab_points = len(cfg.items('LABORATORY_CALIBRATION'))
            
            # Contar rangos existentes
            ranges_count = 0
            if 'LABORATORY_CALIBRATION_RANGES' in cfg:
                ranges_count = len(cfg.items('LABORATORY_CALIBRATION_RANGES'))
            
            # Si no coinciden o no hay rangos, regenerar
            if lab_points > 0 and (ranges_count != lab_points or ranges_count == 0):
                print(f"ℹ️  Detectados {lab_points} puntos de calibración pero solo {ranges_count} rangos")
                print("🔄 Regenerando rangos automáticamente...")
                self.regenerate_laboratory_ranges(archivo_cfg)
                return True
            
            return False
        except Exception as e:
            print(f"ℹ️  Info: Error al verificar rangos: {e}")
            return False
    
    def _send_calibration_to_esp32(self):
        """No envía calibración al ESP32; la calibración queda solo en el archivo local."""
        print("ℹ️ Calibración guardada solo en la aplicación; no se envía al ESP32")
        return True

    def _auto_seed_point_from_current_reading(self):
        """Intenta crear un punto de calibración con la lectura actual cuando no hubo captura por timer."""
        try:
            row = 0
            cond_input = self.known_cond_inputs.get(row)
            if cond_input is None:
                return False

            known_text = cond_input.text().strip()
            if not known_text:
                return False
            known_cond = float(known_text)

            cr = getattr(self.app, 'current_reading', {}) or {}
            raw_val = cr.get('raw')
            if raw_val is None:
                raw_val = cr.get('signal')
            if raw_val is None:
                raw_val = cr.get('sensor')
            if raw_val is None:
                return False

            temp_read = cr.get('temp')
            try:
                temp_read = float(temp_read) if temp_read is not None else 25.0
            except Exception:
                temp_read = 25.0

            measured_value = float(raw_val)
            voltage_at_25c = normalize_voltage_to_reference_temp(
                measured_value, temp_read, ref_temp=25.0, coef=COEF_TEMP
            )
            ec_measured = 133.42 * voltage_at_25c**3 - 255.86 * voltage_at_25c**2 + 857.39 * voltage_at_25c
            if ec_measured <= 0:
                return False

            k_value = known_cond / ec_measured
            estimated_at_temp = k_value * ec_measured

            self.calibration_data[row] = {
                'known_cond': known_cond,
                'measured': voltage_at_25c,
                'k': k_value,
                'estimated_at_temp': estimated_at_temp,
                'temp': temp_read,
            }

            if row in self.measured_labels:
                self.measured_labels[row].setText(f"{voltage_at_25c:.4f}")
                self.measured_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
            if row in self.k_labels:
                self.k_labels[row].setText(f"{k_value:.2f}")
                self.k_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
            if row in self.estimated_labels:
                self.estimated_labels[row].setText(f"~{estimated_at_temp:.1f}")
                self.estimated_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
            if row in self.temp_labels:
                self.temp_labels[row].setText(f"{temp_read:.1f}°C")
                self.temp_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")

            self.status_label.setText("✓ Se usó la lectura actual para crear 1 punto de calibración")
            self.status_label.setStyleSheet(
                "padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;"
            )
            return True
        except Exception:
            return False
    
    def finalize_calibration(self):
        """Finaliza la calibración y cierra el diálogo"""
        if not self.calibration_data:
            seeded = self._auto_seed_point_from_current_reading()
            if not seeded:
                QMessageBox.warning(self, "Advertencia", 
                                  "Debe medir al menos un punto antes de finalizar.")
                return
        
        self.save_laboratory_calibration()
        
        try:
            if hasattr(self.app, 'use_user_calibration'):
                self.app.use_user_calibration = True
            if hasattr(self.app, 'load_known_k_table'):
                self.app.load_known_k_table(force_load=True)
            if hasattr(self.app, 'apply_local_calibration_and_update'):
                self.app.apply_local_calibration_and_update()
        except Exception:
            pass
        
        self.accept()


# ═══════════════════════════════════════════════════════════════
#         DIÁLOGO DE CALIBRACIÓN CON VALORES DESCONOCIDOS
# ═══════════════════════════════════════════════════════════════
class UnknownCalibrationDialog(QDialog):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.serial_reader = None
        self.connected = False
        self.setWindowTitle("Calibración - Escala")
        self.setFixedSize(550, 800)
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Generación de Escala de Mediciones")
        title.setFont(QFont("Arial", 13, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #000000; padding: 10px;")
        layout.addWidget(title)

        info = QLabel(
            "Capturar un rango de mediciones para establecer la escala:\n"
            "1. Prepare soluciones con diferentes conductividades\n"
            "2. Presione 'Capturar Punto' para cada solución\n"
            "3. El sistema medirá automáticamente sin necesidad de valores conocidos\n"
            "4. Acumule varios puntos para crear el rango de calibración"
        )
        info.setWordWrap(True)
        info.setStyleSheet("background: #f9f9f9; padding: 10px; border-radius: 4px; border-left: 4px solid #88a02c;")
        layout.addWidget(info)

        capture_group = QGroupBox("Capturar Puntos de Medición")
        capture_group.setFont(QFont("Arial", 11, QFont.Bold))
        capture_layout = QVBoxLayout()
        capture_layout.setSpacing(10)

        capture_controls = QHBoxLayout()
        capture_controls.addWidget(QLabel("Muestras por punto:"))
        self.sample_count = QSpinBox()
        self.sample_count.setRange(1, 500)
        self.sample_count.setValue(40)
        self.sample_count.setMinimumWidth(80)
        capture_controls.addWidget(self.sample_count)
        capture_controls.addStretch()

        self.capture_btn = QPushButton("Capturar Punto")
        self.capture_btn.setMinimumHeight(40)
        self.capture_btn.setStyleSheet("""
            QPushButton {
                background: #a0b356;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #88a02c;
            }
            QPushButton:pressed {
                background: #445016;
            }
        """)
        capture_controls.addWidget(self.capture_btn)

        capture_layout.addLayout(capture_controls)

        self.signal_label = QLabel("Última señal: ---")
        self.signal_label.setStyleSheet("padding: 8px; background: #111; color: #88a02c; border-radius: 3px; font-family: monospace; font-weight: bold;")
        self.signal_label.setAlignment(Qt.AlignCenter)
        self.signal_label.setMinimumHeight(35)
        capture_layout.addWidget(self.signal_label)

        capture_group.setLayout(capture_layout)
        layout.addWidget(capture_group)

        range_group = QGroupBox("Rango de Mediciones Capturadas")
        range_group.setFont(QFont("Arial", 11, QFont.Bold))
        range_layout = QVBoxLayout()
        range_layout.setSpacing(10)

        self.points_list = QListWidget()
        self.points_list.setMinimumHeight(100)
        self.points_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 3px;
                background: #fafafa;
            }
            QListWidget::item:selected {
                background: #ff9800;
                color: white;
            }
        """)
        range_layout.addWidget(QLabel("Puntos capturados:"))
        range_layout.addWidget(self.points_list)

        stats_layout = QHBoxLayout()
        stats_layout.addWidget(QLabel("Total de puntos:"))
        self.count_label = QLabel("0")
        self.count_label.setStyleSheet("font-weight: bold; color: #88a02c;")
        stats_layout.addWidget(self.count_label)
        stats_layout.addSpacing(20)
        stats_layout.addWidget(QLabel("Mín - Máx:"))
        self.range_label = QLabel("--- - ---")
        self.range_label.setStyleSheet("font-weight: bold; color: #88a02c;")
        stats_layout.addWidget(self.range_label)
        stats_layout.addStretch()
        range_layout.addLayout(stats_layout)

        range_group.setLayout(range_layout)
        layout.addWidget(range_group)

        self.status_label = QLabel("Esperando captura de puntos...")
        self.status_label.setStyleSheet("padding: 8px; background: #e3f2fd; border-radius: 3px; border-left: 4px solid #2196f3;")
        layout.addWidget(self.status_label)

        close_btn = QPushButton("Finalizar Calibración")
        close_btn.clicked.connect(self.accept)
        close_btn.setMinimumHeight(35)
        layout.addWidget(close_btn)

        self.setLayout(layout)

        self.capture_btn.clicked.connect(self.capture_point)

        self.captured_signals = []

    def set_serial(self, serial_reader, connected, app=None):
        """Asigna conexión serial y referencias de aplicación.
        
        Configura SerialReader, estado de conexión y referencia a app
        para permitir que este diálogo envíe comandos y acceda a lecturas.
        """
        self.serial_reader = serial_reader
        self.connected = bool(connected)
        if app is not None:
            self.app = app

    def capture_point(self):
        """Captura una muestra de voltaje de conductividad.
        
        Obtiene el voltaje crudo actual del sensor, lo almacena en la lista
        de puntos capturados y actualiza la visualización de estadísticas.
        """
        if not self.connected or not self.serial_reader:
            QMessageBox.warning(self, "Error", "No conectado al ESP32.")
            return

        self.capture_btn.setEnabled(False)
        self.status_label.setText("Capturando punto de medición...")
        self.status_label.setStyleSheet("padding: 8px; background: #fff3e0; border-radius: 3px; border-left: 4px solid #ff9800;")

        try:
            current_sensor = self.app.current_reading.get('sensor')
            if current_sensor is not None:
                signal_value = float(current_sensor)
                self.captured_signals.append(signal_value)
                
                self.signal_label.setText(f"Última señal: {signal_value:.1f}")
                
                point_num = len(self.captured_signals)
                item_text = f"Punto {point_num}: {signal_value:.1f}"
                self.points_list.addItem(item_text)
                
                self.update_stats()
                
                self.status_label.setText(f"✓ Punto {point_num} capturado: {signal_value:.1f}")
                self.status_label.setStyleSheet("padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;")
            else:
                QMessageBox.warning(self, "Error", "No hay lectura de sensor disponible. Asegúrese de estar recibiendo datos.")
                self.status_label.setText("✗ No se pudo capturar el punto")
                self.status_label.setStyleSheet("padding: 8px; background: #ffebee; border-radius: 3px; border-left: 4px solid #f44336;")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al capturar punto: {e}")
            self.status_label.setText(f"✗ Error: {str(e)[:50]}")
            
        finally:
            self.capture_btn.setEnabled(True)

    def update_stats(self):
        """Actualiza estadísticas de puntos capturados.
        
        Calcula mín, máx, promedio y cantidad total de muestras,
        mostrándolos en los labels del rango de mediciones.
        """
        if not self.captured_signals:
            self.count_label.setText("0")
            self.range_label.setText("--- - ---")
        else:
            min_val = min(self.captured_signals)
            max_val = max(self.captured_signals)
            self.count_label.setText(str(len(self.captured_signals)))
            self.range_label.setText(f"{min_val:.1f} - {max_val:.1f}")

# ═══════════════════════════════════════════════════════════════
#            DIÁLOGO PARA CANTIDAD DE MUESTRAS
# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
#           DIÁLOGO DE CONEXIÓN SERIAL (ESP32)
# ═══════════════════════════════════════════════════════════════
class ConnectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conexión ESP32")
        self.setFixedSize(420, 140)
        layout = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(QLabel("Puerto:"))
        self.port_combo = QComboBox()
        row.addWidget(self.port_combo)
        self.refresh_btn = QPushButton("Actualizar")
        self.refresh_btn.setMaximumWidth(120)
        row.addWidget(self.refresh_btn)
        layout.addLayout(row)

        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Conectar")
        self.cancel_btn = QPushButton("Cancelar")
        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.connect_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

        self.refresh_btn.clicked.connect(self.update_ports)
        self.connect_btn.clicked.connect(self.on_connect)
        self.cancel_btn.clicked.connect(self.reject)

        self.selected_port = None
        self.update_ports()

    def update_ports(self):
        """Refresca lista de puertos COM disponibles.
        
        Escanea puertos seriales conectados y actualiza el combo box
        con dispositivos disponibles, mostrando puerto y descripción.
        """
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.port_combo.addItem(f"{p.device} - {p.description}")
        if not ports:
            self.port_combo.addItem("No se detectan puertos")

    def on_connect(self):
        """Valida y acepta puerto seleccionado para conexión.
        
        Extrae el nombre del puerto del combo box, valida que sea válido,
        lo almacena en selected_port y cierra el diálogo con aceptación.
        """
        if "No se detectan" in self.port_combo.currentText():
            QMessageBox.warning(self, "Error", "No hay puertos disponibles.")
            return
        self.selected_port = self.port_combo.currentText().split(" - ")[0]
        self.accept()


# ═══════════════════════════════════════════════════════════════
#    DIÁLOGO DE ACTUALIZACIÓN DE CALIBRACIÓN (CONDUCTÍMETRO COMERCIAL)
# ═══════════════════════════════════════════════════════════════
class CalibrationUpdateDialog(QDialog):
    """
    Diálogo para actualizar calibración usando un conductímetro comercial.
    
    Funcionamiento:
    1. Captura 10 muestras de voltaje en 10 segundos
    2. Calcula la moda (valor más frecuente) de las muestras
    3. Busca el rango correspondiente en VOLTAGE_RANGES
    4. Calcula el nuevo K con la conductividad conocida
    5. Permite actualizar el K de ese rango específico
    """
    
    MIN_POINTS_TO_SAVE = 3  # Mínimo de puntos para permitir guardar cambios

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("Actualización de Calibración")
        self.setFixedSize(800, 700)
        self.update_points = []
        self.voltage_ranges = {}  # Rangos actuales de VOLTAGE_RANGES
        
        # Para captura de 10 muestras
        self._capture_samples = []
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(1000)  # 1 segundo entre muestras
        self._capture_timer.timeout.connect(self._capture_sample)
        self._capture_remaining = 0
        
        # Timer para actualizar voltaje en vivo
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(300)
        self._live_timer.timeout.connect(self._refresh_live_voltage)
        
        self._load_current_ranges()
        self.setup_ui()
        self._live_timer.start()

    def _load_current_ranges(self):
        """Carga los rangos actuales de VOLTAGE_RANGES desde el archivo cfg"""
        try:
            cfg_path = APP_CALIBRATION_CFG
            if not os.path.exists(cfg_path):
                return
            
            cfg = configparser.ConfigParser()
            cfg.read(cfg_path, encoding='utf-8')
            
            if 'VOLTAGE_RANGES' not in cfg:
                return
            
            for key, val in cfg.items('VOLTAGE_RANGES'):
                try:
                    parts = key.split('-')
                    if len(parts) == 2:
                        mn = float(parts[0])
                        mx = float(parts[1])
                        k_val = float(val)
                        if k_val > 0:
                            self.voltage_ranges[(mn, mx)] = k_val
                except Exception:
                    continue
        except Exception:
            pass

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        title = QLabel("Actualización de Calibración con Conductímetro Comercial")
        title.setFont(QFont("Arial", 13, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        info = QLabel(
            f"1. Conecte el sensor del ESP32 y un conductímetro comercial en la misma solución\n"
            f"2. Ingrese la conductividad (µS) y temperatura (°C) del conductímetro comercial\n"
            f"3. Presione \"Capturar (10s)\" para tomar 10 muestras en 10 segundos\n"
            f"4. El sistema calculará la MODA y buscará el rango correspondiente en VOLTAGE_RANGES\n"
            f"5. Repita para distintos rangos (mínimo {self.MIN_POINTS_TO_SAVE} puntos para guardar)\n"
            f"6. Presione \"Guardar Cambios\" para actualizar los valores de K en los rangos capturados"
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "background: #f9f9f9; padding: 10px; border-radius: 4px; "
            "border-left: 4px solid #2196f3;"
        )
        layout.addWidget(info)

        # Panel de lectura en vivo
        live_group = QGroupBox("Lectura en Vivo del ESP32")
        live_layout = QHBoxLayout()
        live_layout.addWidget(QLabel("Voltaje crudo (raw_V):"))
        self.live_voltage_label = QLabel("-- V")
        self.live_voltage_label.setFont(QFont("Arial", 16, QFont.Bold))
        self.live_voltage_label.setStyleSheet(
            "color: #1565c0; background: #e3f2fd; padding: 8px; "
            "border: 2px solid #1565c0; border-radius: 4px;"
        )
        self.live_voltage_label.setAlignment(Qt.AlignCenter)
        live_layout.addWidget(self.live_voltage_label)
        
        live_layout.addWidget(QLabel("Rango actual:"))
        self.current_range_label = QLabel("--")
        self.current_range_label.setFont(QFont("Arial", 10))
        self.current_range_label.setStyleSheet(
            "color: #666; background: #f5f5f5; padding: 8px; "
            "border: 1px solid #ccc; border-radius: 4px;"
        )
        live_layout.addWidget(self.current_range_label)
        
        live_group.setLayout(live_layout)
        layout.addWidget(live_group)

        # Panel de entrada de datos
        input_group = QGroupBox("Datos del Conductímetro Comercial")
        input_layout = QHBoxLayout()

        input_layout.addWidget(QLabel("Conductividad (µS):"))
        self.cond_input = QLineEdit()
        self.cond_input.setPlaceholderText("Ej: 1413")
        self.cond_input.setValidator(QDoubleValidator(0, 100000, 2))
        input_layout.addWidget(self.cond_input)

        input_layout.addWidget(QLabel("Temperatura (°C):"))
        self.temp_input = QLineEdit()
        self.temp_input.setPlaceholderText("Ej: 25.0")
        self.temp_input.setValidator(QDoubleValidator(-40.0, 125.0, 2))
        self.temp_input.setMaximumWidth(90)
        input_layout.addWidget(self.temp_input)

        self.capture_btn = QPushButton("Capturar (10s)")
        self.capture_btn.setMinimumHeight(38)
        self.capture_btn.setStyleSheet(
            "QPushButton { background: #2196f3; color: white; border: none; "
            "border-radius: 4px; font-weight: bold; padding: 0 15px; }"
            "QPushButton:hover { background: #1976d2; }"
            "QPushButton:disabled { background: #ccc; }"
        )
        self.capture_btn.clicked.connect(self.start_capture)
        input_layout.addWidget(self.capture_btn)

        input_group.setLayout(input_layout)
        layout.addWidget(input_group)

        # Barra de progreso de captura
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 10)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Esperando captura...")
        self.progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #ccc; border-radius: 4px; text-align: center; }"
            "QProgressBar::chunk { background: #4caf50; }"
        )
        layout.addWidget(self.progress_bar)

        # Tabla de puntos capturados
        table_group = QGroupBox("Puntos Capturados (Rangos a Actualizar)")
        table_layout = QVBoxLayout()
        self.points_table = QTableWidget()
        self.points_table.setColumnCount(6)
        self.points_table.setHorizontalHeaderLabels([
            "Rango (V)", "K Actual", "K Nuevo", "Δ K", "Voltaje Moda", "Acción"
        ])
        self.points_table.verticalHeader().setVisible(False)
        self.points_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.points_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.points_table.setMinimumHeight(200)
        self.points_table.setStyleSheet("background: #fafafa; border: 1px solid #ddd;")
        table_layout.addWidget(self.points_table)
        table_group.setLayout(table_layout)
        layout.addWidget(table_group)

        # Label de estado
        self.status_label = QLabel(f"Esperando capturar puntos (mínimo {self.MIN_POINTS_TO_SAVE} para guardar)...")
        self.status_label.setStyleSheet(
            "padding: 8px; background: #e3f2fd; border-radius: 3px; "
            "border-left: 4px solid #2196f3;"
        )
        layout.addWidget(self.status_label)

        # Botones de acción
        btn_layout = QHBoxLayout()

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.setStyleSheet(
            "QPushButton { background: #f44336; color: white; border: none; "
            "border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #d32f2f; }"
        )
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()

        self.save_btn = QPushButton(f"Guardar Cambios (mín. {self.MIN_POINTS_TO_SAVE} puntos)")
        self.save_btn.setMinimumHeight(40)
        self.save_btn.setEnabled(False)
        self.save_btn.setStyleSheet(
            "QPushButton { background: #4caf50; color: white; border: none; "
            "border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #45a049; }"
            "QPushButton:disabled { background: #ccc; color: #666; }"
        )
        self.save_btn.clicked.connect(self.save_and_update)
        btn_layout.addWidget(self.save_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def _refresh_live_voltage(self):
        """Actualiza el voltaje en vivo y muestra el rango correspondiente"""
        try:
            raw = self.app.current_reading.get('raw')
            if raw is not None:
                raw_v = float(raw)
                self.live_voltage_label.setText(f"{raw_v:.1f} V")
                
                # Buscar el rango correspondiente
                range_info = self._find_range_for_voltage(raw_v)
                if range_info:
                    mn, mx, k = range_info
                    self.current_range_label.setText(f"[{mn:.1f} - {mx:.1f}] V, K={k:.1f}")
                else:
                    self.current_range_label.setText("Sin rango definido")
            else:
                self.live_voltage_label.setText("-- V (sin datos)")
                self.current_range_label.setText("--")
        except Exception:
            self.live_voltage_label.setText("-- V")
            self.current_range_label.setText("--")

    def _find_range_for_voltage(self, voltage):
        """Encuentra el rango de VOLTAGE_RANGES para un voltaje dado"""
        for (mn, mx), k in self.voltage_ranges.items():
            if mn <= voltage < mx:
                return (mn, mx, k)
        # Si no encuentra exacto, buscar el más cercano
        if self.voltage_ranges:
            best = None
            min_dist = float('inf')
            for (mn, mx), k in self.voltage_ranges.items():
                mid = (mn + mx) / 2
                dist = abs(voltage - mid)
                if dist < min_dist:
                    min_dist = dist
                    best = (mn, mx, k)
            return best
        return None

    def start_capture(self):
        """Inicia la captura de 10 muestras en 10 segundos"""
        # Validar entradas
        cond_text = self.cond_input.text().strip()
        if not cond_text:
            QMessageBox.warning(self, "Error", "Ingrese la conductividad del conductímetro comercial.")
            return
        try:
            self._capture_cond = float(cond_text)
            if self._capture_cond <= 0:
                raise ValueError("Debe ser > 0")
        except ValueError:
            QMessageBox.warning(self, "Error", "Conductividad inválida. Debe ser un número positivo.")
            return

        temp_text = self.temp_input.text().strip()
        if not temp_text:
            QMessageBox.warning(self, "Error", "Ingrese la temperatura del conductímetro comercial.")
            return
        try:
            self._capture_temp = float(temp_text)
        except ValueError:
            QMessageBox.warning(self, "Error", "Temperatura inválida.")
            return

        # Iniciar captura
        self._capture_samples = []
        self._capture_remaining = 10
        self.capture_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Capturando... 0/10")
        self.status_label.setText("⏳ Capturando muestras, mantenga el sensor estable...")
        self.status_label.setStyleSheet(
            "padding: 8px; background: #fff3e0; border-radius: 3px; "
            "border-left: 4px solid #ff9800;"
        )
        self._capture_timer.start()

    def _capture_sample(self):
        """Captura una muestra de voltaje"""
        try:
            raw = self.app.current_reading.get('raw')
            if raw is not None:
                self._capture_samples.append(float(raw))
        except Exception:
            pass

        self._capture_remaining -= 1
        samples_taken = 10 - self._capture_remaining
        self.progress_bar.setValue(samples_taken)
        self.progress_bar.setFormat(f"Capturando... {samples_taken}/10")

        if self._capture_remaining <= 0:
            self._capture_timer.stop()
            self._process_capture()

    def _calculate_mode(self, values, decimals=4):
        """Calcula la moda de una lista de valores (redondeados para agrupar)"""
        if not values:
            return None
        
        # Redondear para agrupar valores similares
        rounded = [round(v, decimals) for v in values]
        
        # Contar frecuencias
        freq = {}
        for v in rounded:
            freq[v] = freq.get(v, 0) + 1
        
        # Encontrar el valor con mayor frecuencia
        max_count = 0
        mode_value = None
        for v, count in freq.items():
            if count > max_count:
                max_count = count
                mode_value = v
        
        return mode_value

    def _process_capture(self):
        """Procesa las muestras capturadas: calcula moda, busca rango, calcula K"""
        self.capture_btn.setEnabled(True)
        
        if len(self._capture_samples) < 5:
            self.status_label.setText("❌ Error: No se capturaron suficientes muestras válidas")
            self.status_label.setStyleSheet(
                "padding: 8px; background: #ffebee; border-radius: 3px; "
                "border-left: 4px solid #f44336;"
            )
            self.progress_bar.setFormat("Error en captura")
            return

        # Calcular la moda de los voltajes
        mode_voltage = self._calculate_mode(self._capture_samples)
        if mode_voltage is None or mode_voltage <= 0:
            self.status_label.setText("❌ Error: Voltaje inválido (moda = 0 o negativa)")
            return

        # Buscar el rango correspondiente
        range_info = self._find_range_for_voltage(mode_voltage)
        if range_info is None:
            self.status_label.setText(f"❌ Error: No se encontró rango para voltaje {mode_voltage:.1f} V")
            return

        mn, mx, k_actual = range_info

        # Calcular el nuevo K con compensación de temperatura
        coef = 0.02
        temp_ref = 25.0
        try:
            cfg = configparser.ConfigParser()
            if os.path.exists(APP_CALIBRATION_CFG):
                cfg.read(APP_CALIBRATION_CFG, encoding='utf-8')
                if 'TEMPERATURE' in cfg:
                    coef = float(cfg.get('TEMPERATURE', 'coef_temp', fallback='0.02'))
                    temp_ref = float(cfg.get('TEMPERATURE', 'temp_ref', fallback='25.0'))
        except Exception:
            pass

        factor_temp = 1.0 + coef * (self._capture_temp - temp_ref)
        factor_temp = max(0.2, min(2.0, factor_temp))

        # K = (conductividad * factor_temp) / voltaje
        k_nuevo = (self._capture_cond * factor_temp) / mode_voltage

        # Verificar si ya existe un punto para este rango
        existing_idx = None
        for idx, pt in enumerate(self.update_points):
            if pt['range_key'] == (mn, mx):
                existing_idx = idx
                break

        point = {
            'range_key': (mn, mx),
            'range_str': f"{mn:.1f}-{mx:.1f}",
            'k_actual': k_actual,
            'k_nuevo': k_nuevo,
            'mode_voltage': mode_voltage,
            'cond': self._capture_cond,
            'temp': self._capture_temp,
            'factor_temp': factor_temp,
            'samples': self._capture_samples.copy(),
        }

        if existing_idx is not None:
            # Actualizar punto existente
            self.update_points[existing_idx] = point
            self.status_label.setText(
                f"🔄 Rango [{mn:.1f}-{mx:.1f}] actualizado: K={k_actual:.1f} → {k_nuevo:.1f} "
                f"(Δ={k_nuevo - k_actual:+.1f})"
            )
        else:
            # Agregar nuevo punto
            self.update_points.append(point)
            self.status_label.setText(
                f"✅ Rango [{mn:.1f}-{mx:.1f}] capturado: K={k_actual:.1f} → {k_nuevo:.1f} "
                f"(Δ={k_nuevo - k_actual:+.1f})"
            )

        self.status_label.setStyleSheet(
            "padding: 8px; background: #e8f5e9; border-radius: 3px; "
            "border-left: 4px solid #4caf50;"
        )
        self.progress_bar.setFormat(f"Captura completada - Moda: {mode_voltage:.1f} V")

        # Actualizar tabla y botón guardar
        self._refresh_table()
        self._update_save_button()

        # Limpiar inputs
        self.cond_input.clear()
        self.temp_input.clear()

    def _refresh_table(self):
        """Actualiza la tabla de puntos capturados"""
        self.points_table.setRowCount(0)
        for idx, pt in enumerate(self.update_points):
            row = self.points_table.rowCount()
            self.points_table.insertRow(row)

            delta_k = pt['k_nuevo'] - pt['k_actual']
            delta_color = "#4caf50" if abs(delta_k) < 50 else "#ff9800" if abs(delta_k) < 100 else "#f44336"

            self.points_table.setItem(row, 0, self._centered_item(pt['range_str']))
            self.points_table.setItem(row, 1, self._centered_item(f"{pt['k_actual']:.1f}"))
            self.points_table.setItem(row, 2, self._centered_item(f"{pt['k_nuevo']:.1f}"))
            
            delta_item = self._centered_item(f"{delta_k:+.1f}")
            delta_item.setForeground(QColor(delta_color))
            self.points_table.setItem(row, 3, delta_item)
            
            self.points_table.setItem(row, 4, self._centered_item(f"{pt['mode_voltage']:.1f}"))

            btn = QPushButton("Eliminar")
            btn.setStyleSheet(
                "background: #f44336; color: white; border-radius: 3px; padding: 4px;"
            )
            btn.clicked.connect(lambda checked, i=idx: self._remove_point(i))
            self.points_table.setCellWidget(row, 5, btn)

        self.points_table.resizeColumnsToContents()

    def _update_save_button(self):
        """Habilita/deshabilita el botón guardar según cantidad de puntos"""
        can_save = len(self.update_points) >= self.MIN_POINTS_TO_SAVE
        self.save_btn.setEnabled(can_save)
        if can_save:
            self.save_btn.setText(f"Guardar Cambios ({len(self.update_points)} puntos)")
        else:
            remaining = self.MIN_POINTS_TO_SAVE - len(self.update_points)
            self.save_btn.setText(f"Guardar Cambios (faltan {remaining} puntos)")

    @staticmethod
    def _centered_item(text):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def _remove_point(self, index):
        """Elimina un punto de la lista"""
        if 0 <= index < len(self.update_points):
            removed = self.update_points.pop(index)
            self._refresh_table()
            self._update_save_button()
            self.status_label.setText(
                f"Punto eliminado (rango {removed['range_str']}). "
                f"Quedan {len(self.update_points)} punto(s)."
            )

    def save_and_update(self):
        """Guarda los cambios actualizando solo los rangos capturados"""
        if len(self.update_points) < self.MIN_POINTS_TO_SAVE:
            QMessageBox.warning(
                self, "Error",
                f"Debe capturar al menos {self.MIN_POINTS_TO_SAVE} puntos para guardar cambios."
            )
            return

        # Mostrar resumen de cambios
        summary = "Se actualizarán los siguientes rangos:\n\n"
        for pt in self.update_points:
            delta = pt['k_nuevo'] - pt['k_actual']
            summary += f"• [{pt['range_str']}] V: K={pt['k_actual']:.1f} → {pt['k_nuevo']:.1f} (Δ={delta:+.1f})\n"
        
        summary += f"\n¿Desea continuar?"

        reply = QMessageBox.question(
            self, "Confirmar Actualización",
            summary,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            cfg_path = APP_CALIBRATION_CFG
            cfg = configparser.ConfigParser()
            if os.path.exists(cfg_path):
                cfg.read(cfg_path, encoding='utf-8')

            section = "VOLTAGE_RANGES"
            if section not in cfg:
                QMessageBox.critical(self, "Error", "No se encontró la sección VOLTAGE_RANGES en el archivo de configuración.")
                return

            # Actualizar solo los rangos capturados
            updated_count = 0
            for pt in self.update_points:
                mn, mx = pt['range_key']
                key = f"{mn}-{mx}"
                
                # Verificar si el key existe con formato diferente
                found_key = None
                for existing_key in cfg.options(section):
                    try:
                        parts = existing_key.split('-')
                        if len(parts) == 2:
                            e_mn = float(parts[0])
                            e_mx = float(parts[1])
                            if abs(e_mn - mn) < 0.0001 and abs(e_mx - mx) < 0.0001:
                                found_key = existing_key
                                break
                    except Exception:
                        continue

                if found_key:
                    cfg.set(section, found_key, str(round(pt['k_nuevo'], 2)))
                    updated_count += 1

            # Guardar archivo
            with open(cfg_path, 'w', encoding='utf-8') as f:
                cfg.write(f)
            
            QMessageBox.information(
                self, "Actualización Exitosa",
                f"Se actualizaron {updated_count} rangos en VOLTAGE_RANGES.\n\n"
                "Los nuevos valores de K están activos."
            )
            self.accept()

        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Error al guardar cambios:\n{e}")

    def closeEvent(self, event):
        """Detiene timers al cerrar"""
        self._capture_timer.stop()
        self._live_timer.stop()
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════
#                 APLICACIÓN PRINCIPAL (ESP32App)
# ═══════════════════════════════════════════════════════════════
class ESP32App(QWidget):
    calibration_message = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setup_ui()
        self.setup_variables()
        self.connect_signals()
        self.update_ports()
        self.csv_plot_window = None
        self.unknown_cal_data = {}
        # Verificar y regenerar rangos de calibración automáticamente
        self.check_and_regenerate_lab_ranges()

    def setup_ui(self):
        """Construye interfaz principal de la aplicación.
        
        Crea widgets para controles, gráficas en tiempo real, paneles de valores
        y botones de acción para lectura, calibración y descarga de datos.
        Soporta 2 dispositivos simultáneamente lado a lado.
        """
        self.setWindowTitle("EMA - Conductividad y Temperatura (Dual)")
        self.resize(1600, 1000)

        main_layout = QVBoxLayout()
        controls_layout = QHBoxLayout()
        controls_layout2 = QHBoxLayout()
        buttons_layout = QHBoxLayout()

        self.status_label = QLabel("Estado: Desconectado")
        controls_layout.addWidget(self.status_label)
        controls_layout.addStretch()
        
        self.disconnect_btn = QPushButton("Desconectar")
        self.disconnect_btn.setMaximumWidth(120)
        self.disconnect_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #666;
                border: 1px solid #999;
                border-radius: 4px;
                font-size: 9pt;
                padding: 4px 10px;
            }
            QPushButton:hover {
                color: #333;
                border: 1px solid #555;
                background: #f0f0f0;
            }
        """)
        self.disconnect_btn.clicked.connect(self.disconnect_from_esp32)
        controls_layout.addWidget(self.disconnect_btn)

        self.connect_dev2_btn = QPushButton("Conectar Dev 2")
        self.connect_dev2_btn.setMaximumWidth(150)
        self.connect_dev2_btn.setStyleSheet("""
            QPushButton {
                background: #4caf50;
                color: white;
                border: 1px solid #388e3c;
                border-radius: 4px;
                font-size: 9pt;
                font-weight: bold;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background: #45a049;
                border: 1px solid #2e7d32;
            }
        """)
        self.connect_dev2_btn.clicked.connect(self.show_device2_connection_dialog)
        controls_layout.addWidget(self.connect_dev2_btn)

        self.disconnect_dev2_btn = QPushButton("Desconectar Dev 2")
        self.disconnect_dev2_btn.setMaximumWidth(150)
        self.disconnect_dev2_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #666;
                border: 1px solid #999;
                border-radius: 4px;
                font-size: 9pt;
                padding: 4px 10px;
            }
            QPushButton:hover {
                color: #333;
                border: 1px solid #555;
                background: #f0f0f0;
            }
        """)
        self.disconnect_dev2_btn.clicked.connect(self.disconnect_from_esp32_2)
        self.disconnect_dev2_btn.setEnabled(False)
        controls_layout.addWidget(self.disconnect_dev2_btn)

        self.interval_label = QLabel("Intervalo (s):")
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 60.0)
        self.interval_spin.setValue(2.0)
        self.interval_btn = QPushButton("Aplicar")

        controls_layout2.addWidget(self.interval_label)
        controls_layout2.addWidget(self.interval_spin)
        controls_layout2.addWidget(self.interval_btn)
        controls_layout2.addSpacing(16)
        controls_layout2.addStretch()

        self.value_panel = QFrame()
        self.value_panel.setFrameShape(QFrame.StyledPanel)
        layout_vp = QHBoxLayout()

        big_font = QFont()
        big_font.setPointSize(22)
        self.test_name_label = QLabel("Prueba: ---")
        self.test_name_label.setFont(QFont("Arial", 12))
        self.test_name_label.setStyleSheet("border:1px solid #ccc; padding:5px; background: #e8f4fd; color: #0066cc; border-radius:4px;")
        
        self.value_sensor_label = QLabel("-- µS")
        self.value_sensor_label.setFont(big_font)
        self.value_sensor_label.setAlignment(Qt.AlignCenter)
        self.value_sensor_label.setStyleSheet("border:2px solid #ffd700; padding:15px; background: #1a1a1a; color: #ffd700; border-radius:6px; font-weight: bold;")
        sensor_label_text = QLabel("Conductividad")
        sensor_label_text.setAlignment(Qt.AlignCenter)
        sensor_label_text.setStyleSheet("color: #ffd700; font-weight: bold;")
        
        self.value_temp_label = QLabel("-- °C")
        self.value_temp_label.setFont(big_font)
        self.value_temp_label.setAlignment(Qt.AlignCenter)
        self.value_temp_label.setStyleSheet("border:2px solid #ff6b6b; padding:15px; background: #1a1a1a; color: #ff6b6b; border-radius:6px; font-weight: bold;")
        temp_label_text = QLabel("Temperatura")
        temp_label_text.setAlignment(Qt.AlignCenter)
        temp_label_text.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        
        self.k_label = QLabel("K: --")
        self.k_label.setFont(big_font)
        self.k_label.setAlignment(Qt.AlignCenter)
        self.k_label.setStyleSheet("border:2px solid #4ecdc4; padding:15px; background: #1a1a1a; color: #4ecdc4; border-radius:6px; font-weight: bold;")
        k_label_text = QLabel("Factor K")
        k_label_text.setAlignment(Qt.AlignCenter)
        k_label_text.setStyleSheet("color: #4ecdc4; font-weight: bold;")
        self.k_label.setVisible(False)
        k_label_text.setVisible(False)
        
        self.led = QLabel()
        self.led.setFixedSize(16, 16)
        self.led.setStyleSheet("background: #b30000; border-radius: 8px;")

        self.test_name_label.setVisible(False)
        
        col_layout = QHBoxLayout()
        
        col1 = QVBoxLayout()
        col1.addWidget(sensor_label_text)
        col1.addWidget(self.value_sensor_label)
        
        col2 = QVBoxLayout()
        col2.addWidget(temp_label_text)
        col2.addWidget(self.value_temp_label)

        col3 = QVBoxLayout()
        raw_label_text = QLabel("Voltaje")
        raw_label_text.setAlignment(Qt.AlignCenter)
        raw_label_text.setStyleSheet("color: #8ecae6; font-weight: bold;")
        self.raw_label = QLabel("-- V")
        self.raw_label.setFont(big_font)
        self.raw_label.setAlignment(Qt.AlignCenter)
        self.raw_label.setStyleSheet("border:2px solid #8ecae6; padding:15px; background: #1a1a1a; color: #8ecae6; border-radius:6px; font-weight: bold;")
        col3.addWidget(raw_label_text)
        col3.addWidget(self.raw_label)
        
        col4 = QVBoxLayout()
        col4.addWidget(k_label_text)
        col4.addWidget(self.k_label)
        
        col_layout.addLayout(col1)
        col_layout.addLayout(col2)
        col_layout.addLayout(col3)
        col_layout.addLayout(col4)
        
        layout_vp.addLayout(col_layout)
        layout_vp.addStretch()
        layout_vp.addWidget(QLabel("Estado:"))
        layout_vp.addWidget(self.led)
        self.value_panel.setLayout(layout_vp)

        # Segundo panel de valores para el segundo dispositivo
        self.value_panel_2 = QFrame()
        self.value_panel_2.setFrameShape(QFrame.StyledPanel)
        layout_vp_2 = QHBoxLayout()
        
        self.test_name_label_2 = QLabel("Prueba: ---")
        self.test_name_label_2.setFont(QFont("Arial", 12))
        self.test_name_label_2.setStyleSheet("border:1px solid #ccc; padding:5px; background: #e8f4fd; color: #0066cc; border-radius:4px;")
        
        self.value_sensor_label_2 = QLabel("-- µS")
        self.value_sensor_label_2.setFont(big_font)
        self.value_sensor_label_2.setAlignment(Qt.AlignCenter)
        self.value_sensor_label_2.setStyleSheet("border:2px solid #ffd700; padding:15px; background: #1a1a1a; color: #ffd700; border-radius:6px; font-weight: bold;")
        sensor_label_text_2 = QLabel("Conductividad")
        sensor_label_text_2.setAlignment(Qt.AlignCenter)
        sensor_label_text_2.setStyleSheet("color: #ffd700; font-weight: bold;")
        
        self.value_temp_label_2 = QLabel("-- °C")
        self.value_temp_label_2.setFont(big_font)
        self.value_temp_label_2.setAlignment(Qt.AlignCenter)
        self.value_temp_label_2.setStyleSheet("border:2px solid #ff6b6b; padding:15px; background: #1a1a1a; color: #ff6b6b; border-radius:6px; font-weight: bold;")
        temp_label_text_2 = QLabel("Temperatura")
        temp_label_text_2.setAlignment(Qt.AlignCenter)
        temp_label_text_2.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        
        self.k_label_2 = QLabel("K: --")
        self.k_label_2.setFont(big_font)
        self.k_label_2.setAlignment(Qt.AlignCenter)
        self.k_label_2.setStyleSheet("border:2px solid #4ecdc4; padding:15px; background: #1a1a1a; color: #4ecdc4; border-radius:6px; font-weight: bold;")
        k_label_text_2 = QLabel("Factor K")
        k_label_text_2.setAlignment(Qt.AlignCenter)
        k_label_text_2.setStyleSheet("color: #4ecdc4; font-weight: bold;")
        self.k_label_2.setVisible(False)
        k_label_text_2.setVisible(False)
        
        self.led_2 = QLabel()
        self.led_2.setFixedSize(16, 16)
        self.led_2.setStyleSheet("background: #b30000; border-radius: 8px;")

        self.test_name_label_2.setVisible(False)
        
        col_layout_2 = QHBoxLayout()
        
        col1_2 = QVBoxLayout()
        col1_2.addWidget(sensor_label_text_2)
        col1_2.addWidget(self.value_sensor_label_2)
        
        col2_2 = QVBoxLayout()
        col2_2.addWidget(temp_label_text_2)
        col2_2.addWidget(self.value_temp_label_2)

        col3_2 = QVBoxLayout()
        raw_label_text_2 = QLabel("Voltaje")
        raw_label_text_2.setAlignment(Qt.AlignCenter)
        raw_label_text_2.setStyleSheet("color: #8ecae6; font-weight: bold;")
        self.raw_label_2 = QLabel("-- V")
        self.raw_label_2.setFont(big_font)
        self.raw_label_2.setAlignment(Qt.AlignCenter)
        self.raw_label_2.setStyleSheet("border:2px solid #8ecae6; padding:15px; background: #1a1a1a; color: #8ecae6; border-radius:6px; font-weight: bold;")
        col3_2.addWidget(raw_label_text_2)
        col3_2.addWidget(self.raw_label_2)
        
        col4_2 = QVBoxLayout()
        col4_2.addWidget(k_label_text_2)
        col4_2.addWidget(self.k_label_2)
        
        col_layout_2.addLayout(col1_2)
        col_layout_2.addLayout(col2_2)
        col_layout_2.addLayout(col3_2)
        col_layout_2.addLayout(col4_2)
        
        layout_vp_2.addLayout(col_layout_2)
        layout_vp_2.addStretch()
        layout_vp_2.addWidget(QLabel("Estado:"))
        layout_vp_2.addWidget(self.led_2)
        self.value_panel_2.setLayout(layout_vp_2)

        self.start_btn = QPushButton("Iniciar Lectura")
        self.stop_btn = QPushButton("Detener Lectura")
        self.download_btn = QPushButton("Descargar CSV")
        self.plot_csv_btn = QPushButton("Gráfica CSV")
        self.calibration_update_btn = QPushButton("Actualizar Calibración")
        
        for btn in [self.start_btn, self.stop_btn, self.download_btn, self.plot_csv_btn, self.calibration_update_btn]:
            btn.setMinimumHeight(40)
            btn.setFont(QFont("Arial", 10, QFont.Bold))
        
        buttons_layout.addWidget(self.start_btn)
        buttons_layout.addWidget(self.stop_btn)
        buttons_layout.addWidget(self.download_btn)
        buttons_layout.addWidget(self.plot_csv_btn)
        buttons_layout.addWidget(self.calibration_update_btn)

        self.plot_sensor = pg.PlotWidget(title="Conductividad (uS) - Tiempo Real - Dispositivo 1")
        self.plot_sensor.showGrid(x=True, y=True)
        self.plot_sensor.setLabel('left', 'Conductividad', 'uS')
        self.plot_sensor.setLabel('bottom', 'Tiempo', 's')
        self.plot_sensor.getAxis('left').enableAutoSIPrefix(False)
        try:
            self.plot_sensor.getAxis('left').setTickSpacing(major=100.0, minor=20.0)
        except Exception:
            pass
        try:
            self.plot_sensor.getAxis('bottom').setTickSpacing(major=10.0, minor=2.0)
        except Exception:
            pass
        self.plot_sensor.setBackground('w')
        self.curve_sensor = self.plot_sensor.plot(pen=pg.mkPen('r', width=2), symbol='o', symbolSize=5, symbolPen='r', symbolBrush='r')
        self.curve_sensor.setData([0])
        
        self.plot_sensor_2 = pg.PlotWidget(title="Conductividad (uS) - Tiempo Real - Dispositivo 2")
        self.plot_sensor_2.showGrid(x=True, y=True)
        self.plot_sensor_2.setLabel('left', 'Conductividad', 'uS')
        self.plot_sensor_2.setLabel('bottom', 'Tiempo', 's')
        self.plot_sensor_2.getAxis('left').enableAutoSIPrefix(False)
        try:
            self.plot_sensor_2.getAxis('left').setTickSpacing(major=100.0, minor=20.0)
        except Exception:
            pass
        try:
            self.plot_sensor_2.getAxis('bottom').setTickSpacing(major=10.0, minor=2.0)
        except Exception:
            pass
        self.plot_sensor_2.setBackground('w')
        self.curve_sensor_2 = self.plot_sensor_2.plot(pen=pg.mkPen('r', width=2), symbol='o', symbolSize=5, symbolPen='r', symbolBrush='r')
        self.curve_sensor_2.setData([0])
        
        self.plot_temp = pg.PlotWidget(title="Temperatura (°C) - Tiempo Real - Dispositivo 1")
        self.plot_temp.showGrid(x=True, y=True)
        self.plot_temp.setLabel('left', 'Temperatura', '°C')
        self.plot_temp.setLabel('bottom', 'Tiempo', 's')
        self.plot_temp.getAxis('left').enableAutoSIPrefix(False)
        try:
            self.plot_temp.getAxis('left').setTickSpacing(major=2.0, minor=0.5)
        except Exception:
            pass
        try:
            self.plot_temp.getAxis('bottom').setTickSpacing(major=10.0, minor=2.0)
        except Exception:
            pass
        self.plot_temp.setBackground('w')
        self.curve_temp = self.plot_temp.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=5, symbolPen='b', symbolBrush='b')
        self.curve_temp.setData([0])
        
        self.plot_temp_2 = pg.PlotWidget(title="Temperatura (°C) - Tiempo Real - Dispositivo 2")
        self.plot_temp_2.showGrid(x=True, y=True)
        self.plot_temp_2.setLabel('left', 'Temperatura', '°C')
        self.plot_temp_2.setLabel('bottom', 'Tiempo', 's')
        self.plot_temp_2.getAxis('left').enableAutoSIPrefix(False)
        try:
            self.plot_temp_2.getAxis('left').setTickSpacing(major=2.0, minor=0.5)
        except Exception:
            pass
        try:
            self.plot_temp_2.getAxis('bottom').setTickSpacing(major=10.0, minor=2.0)
        except Exception:
            pass
        self.plot_temp_2.setBackground('w')
        self.curve_temp_2 = self.plot_temp_2.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=5, symbolPen='b', symbolBrush='b')
        self.curve_temp_2.setData([0])

        self.stats_panel = QFrame()
        self.stats_panel.setFrameShape(QFrame.StyledPanel)
        self.stats_panel.setStyleSheet("border: 1px solid #ddd; background: #f9f9f9; border-radius: 4px;")
        stats_layout = QHBoxLayout()
        
        self.stats_label = QLabel("Estadísticas: Aguardando datos...")
        self.stats_label.setFont(QFont("Arial", 10))
        self.stats_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.stats_label.setStyleSheet("color: #333;")
        
        stats_layout.addWidget(self.stats_label)
        self.stats_panel.setLayout(stats_layout)

        # Panel estadísticas para segundo dispositivo
        self.stats_panel_2 = QFrame()
        self.stats_panel_2.setFrameShape(QFrame.StyledPanel)
        self.stats_panel_2.setStyleSheet("border: 1px solid #ddd; background: #f9f9f9; border-radius: 4px;")
        stats_layout_2 = QHBoxLayout()
        
        self.stats_label_2 = QLabel("Estadísticas (Dev 2): Aguardando datos...")
        self.stats_label_2.setFont(QFont("Arial", 10))
        self.stats_label_2.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.stats_label_2.setStyleSheet("color: #333;")
        
        stats_layout_2.addWidget(self.stats_label_2)
        self.stats_panel_2.setLayout(stats_layout_2)

        # Crear layout para valores lado a lado
        values_layout = QHBoxLayout()
        values_layout.addWidget(self.value_panel, 1)
        values_layout.addWidget(self.value_panel_2, 1)

        # Crear layout para gráficas de conductividad lado a lado
        sensor_layout = QHBoxLayout()
        sensor_layout.addWidget(self.plot_sensor, 1)
        sensor_layout.addWidget(self.plot_sensor_2, 1)

        # Crear layout para gráficas de temperatura lado a lado
        temp_layout = QHBoxLayout()
        temp_layout.addWidget(self.plot_temp, 1)
        temp_layout.addWidget(self.plot_temp_2, 1)

        # Crear layout para estadísticas lado a lado
        stats_row_layout = QHBoxLayout()
        stats_row_layout.addWidget(self.stats_panel, 1)
        stats_row_layout.addWidget(self.stats_panel_2, 1)

        main_layout.addLayout(controls_layout)
        main_layout.addLayout(controls_layout2)
        main_layout.addLayout(values_layout)
        main_layout.addLayout(buttons_layout)
        main_layout.addLayout(sensor_layout)
        main_layout.addLayout(temp_layout)
        main_layout.addLayout(stats_row_layout)
        self.setLayout(main_layout)

    def setup_variables(self):
        """Inicializa variables de estado, timers y estructuras de datos.
        
        Configura listas para muestras, timers de GUI y sincronización,
        estructuras de calibración y referencias a SerialReader.
        """
        self.sensor_data = []
        self.temp_data = []
        self.sensor_data_2 = []
        self.temp_data_2 = []
        self.logger = DataLogger()
        self.available_ports = []
        self.serial_reader = None
        self.serial_reader_2 = None
        self.reading_active = False
        self.reading_active_2 = False
        self.connected = False
        self.connected_2 = False
        self.data_received = False
        self.data_received_2 = False
        self.last_data_time = time.time()
        self.current_test_name = ""
        self.current_test_name_2 = ""
        self.test_start_time = None
        self.test_start_time_2 = None
        self.line_queue = deque()
        self.line_queue_2 = deque()
        self.queue_lock = threading.Lock()
        self.queue_lock_2 = threading.Lock()
        self.current_reading = {'sensor': None, 'temp': None, 'k': None, 'ema': None, 'raw': None, 'signal': None}
        self.current_reading_2 = {'sensor': None, 'temp': None, 'k': None, 'ema': None, 'raw': None, 'signal': None}
        self.pending_sample_1 = None
        self.pending_sample_2 = None
        self.last_synced_sample_1 = None
        self.last_synced_sample_2 = None
        self.esp32_sensor_raw = None  # Guardar sensor raw del ESP32 para OLED (antes de recalcular)
        self.esp32_sensor_raw_2 = None
        self._last_oled_sync_payload = None
        self._last_oled_sync_payload_2 = None
        self.cal_dialog = None
        self.calibration_active = False
        self.calibration_active_2 = False
        # Último ACK recibido para UPDATE_K_TABLE
        self.last_update_k_table = None
        self.prev_interval = None
        self.prev_interval_2 = None
        self.prev_mode_text = None
        self.prev_mode_text_2 = None
        self.cal_points = {}
        self.cal_points_2 = {}
        self.cal_m = None
        self.cal_m_2 = None
        self.cal_b = None
        self.cal_b_2 = None
        self.cal_points = {}
        self.cal_m = None
        self.default_cal_table = 1
        self.loaded_calibration = {}
        
        self.use_user_calibration = False
        self.use_user_calibration_2 = False
        self.user_calibration = {}
        self.user_calibration_2 = {}
        self.user_calibration_raw = {}
        self.user_calibration_raw_2 = {}
        self.known_k_ranges = {}
        self.known_k_ranges_2 = {}
        self.known_k_table = {}
        self.known_k_table_2 = {}
        self.calibration_mode = "laboratory"
        self.calibration_mode_2 = "laboratory"
        self._cal_mode_transition_in_progress = False
        self._cal_mode_switch_internal_change = False
        self._is_known_mode_active = False

        try:
            self.load_known_k_table(force_load=True)
        except Exception:
            pass

        self.gui_timer = QTimer(self)
        self.gui_timer.setInterval(100)
        self.gui_timer.timeout.connect(self.flush_queue)
        self.gui_timer.start()
        
        self.gui_timer_2 = QTimer(self)
        self.gui_timer_2.setInterval(100)
        self.gui_timer_2.timeout.connect(self.flush_queue_2)
        self.gui_timer_2.start()

        self.sync_timer = QTimer(self)
        self.sync_timer.setSingleShot(False)
        self.sync_timer.timeout.connect(self._flush_synced_dual_sample)
        self._update_sync_timer_interval()
        
        self.debug_timer = QTimer(self)
        self.debug_timer.setInterval(5000)
        self.debug_timer.timeout.connect(self.check_serial_health)
        
        self.rtc_timer = QTimer(self)
        self.rtc_timer.setInterval(30_000)
        self.rtc_timer.timeout.connect(self._rtc_check_timer_timeout)
        self.rtc_sync_attempts = 0
        self.rtc_max_attempts = 3

    def connect_signals(self):
        """Conecta señales de botones a sus métodos manejadores.
        
        Vincula eventos clicked de todos los botones principales con los slots
        correspondientes, configurando también estados iniciales de habilitación.
        """
        self.start_btn.clicked.connect(self.start_reading)
        self.stop_btn.clicked.connect(self.stop_reading)
        self.download_btn.clicked.connect(self.download_csv)
        self.plot_csv_btn.clicked.connect(self.plot_csv_data)
        self.calibration_update_btn.clicked.connect(self.open_calibration_update_dialog)
        self.interval_btn.clicked.connect(self.apply_interval)
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

    def check_and_regenerate_lab_ranges(self, archivo_cfg="calibration_ranges.cfg"):
        """
        Verifica automáticamente si hay cambios en [LABORATORY_CALIBRATION]
        que requieran regeneración de [LABORATORY_CALIBRATION_RANGES].
        
        Se ejecuta al iniciar la aplicación para asegurar consistencia.
        """
        try:
            if not os.path.exists(archivo_cfg):
                return False
            
            cfg = configparser.ConfigParser()
            cfg.read(archivo_cfg, encoding='utf-8')
            
            if 'LABORATORY_CALIBRATION' not in cfg:
                return False
            
            # Contar puntos de calibración
            lab_points = len(cfg.items('LABORATORY_CALIBRATION'))
            
            # Contar rangos existentes
            ranges_count = 0
            if 'LABORATORY_CALIBRATION_RANGES' in cfg:
                ranges_count = len(cfg.items('LABORATORY_CALIBRATION_RANGES'))
            
            # Si no coinciden o no hay rangos, regenerar
            if lab_points > 0 and (ranges_count != lab_points or ranges_count == 0):
                print(f"ℹ️  Detectados {lab_points} puntos de calibración pero solo {ranges_count} rangos")
                print("🔄 Regenerando rangos automáticamente...")
                self.regenerate_laboratory_ranges(archivo_cfg)
                return True
            
            return False
        except Exception as e:
            print(f"ℹ️  Info: Error al verificar rangos: {e}")
            return False

    def regenerate_laboratory_ranges(self, archivo_cfg="calibration_ranges.cfg"):
        """
        Lee [LABORATORY_CALIBRATION] del archivo y regenera automáticamente
        [LABORATORY_CALIBRATION_RANGES] basándose en los voltajes medidos.
        
        Esta función funciona incluso cuando los datos se han añadido manualmente al archivo.
        """
        try:
            if not os.path.exists(archivo_cfg):
                print(f"❌ Archivo {archivo_cfg} no encontrado")
                return False
            
            cfg = configparser.ConfigParser()
            cfg.read(archivo_cfg, encoding='utf-8')
            
            if 'LABORATORY_CALIBRATION' not in cfg:
                print("❌ No existe sección [LABORATORY_CALIBRATION]")
                return False
            
            # Extraer voltajes medidos y valores de K desde el archivo
            calibration_points = {}
            for cond_str, cal_data in cfg.items('LABORATORY_CALIBRATION'):
                try:
                    known_cond = float(cond_str)
                    parts = cal_data.split(',')
                    
                    if len(parts) >= 2:
                        k_value = float(parts[0].strip())
                        measured_voltage = float(parts[1].strip())
                        calibration_points[measured_voltage] = {
                            'k': k_value,
                            'cond': known_cond
                        }
                except Exception:
                    continue
            
            if len(calibration_points) < 1:
                print("❌ No hay puntos de calibración válidos con voltaje medido")
                return False
            
            # Crear o limpiar sección de rangos
            if 'LABORATORY_CALIBRATION_RANGES' not in cfg:
                cfg.add_section('LABORATORY_CALIBRATION_RANGES')
            else:
                for key in list(cfg.options('LABORATORY_CALIBRATION_RANGES')):
                    cfg.remove_option('LABORATORY_CALIBRATION_RANGES', key)
            
            # Ordenar puntos por voltaje
            sorted_points = sorted(calibration_points.items())
            
            # Generar rangos usando puntos medios entre voltajes
            for i, (voltage, data) in enumerate(sorted_points):
                if i == 0:
                    v_min = 0.0
                    if len(sorted_points) > 1:
                        next_voltage = sorted_points[i + 1][0]
                        v_max = (voltage + next_voltage) / 2.0
                    else:
                        v_max = voltage * 2.0
                elif i == len(sorted_points) - 1:
                    prev_voltage = sorted_points[i - 1][0]
                    v_min = (prev_voltage + voltage) / 2.0
                    v_max = max(voltage * 2.0, 10.0)
                else:
                    prev_voltage = sorted_points[i - 1][0]
                    next_voltage = sorted_points[i + 1][0]
                    v_min = (prev_voltage + voltage) / 2.0
                    v_max = (voltage + next_voltage) / 2.0
                
                range_key = f"{v_min:.6f},{v_max:.6f}"
                k_val = data['k']
                cfg.set('LABORATORY_CALIBRATION_RANGES', range_key, str(round(k_val, 6)))
            
            # Guardar configuración
            with open(archivo_cfg, 'w', encoding='utf-8') as f:
                cfg.write(f)
            
            print(f"✓ {len(sorted_points)} rangos de calibración regenerados en [LABORATORY_CALIBRATION_RANGES]")
            return True
        
        except Exception as e:
            print(f"❌ Error regenerando rangos de calibración: {e}")
            return False

    def update_ports(self):
        """Refresca lista de puertos COM disponibles.
        
        Escanea dispositivos seriales conectados y actualiza lista interna
        y combo box con puertos disponibles y sus descripciones.
        """
        ports = serial.tools.list_ports.comports()
        self.available_ports = [p.device for p in ports]
        try:
            if hasattr(self, 'port_combo'):
                self.port_combo.clear()
                for p in ports:
                    self.port_combo.addItem(f"{p.device} - {p.description}")
                if not ports:
                    self.port_combo.addItem("No se detectan puertos")
        except Exception:
            pass

    def toggle_connection(self):
        """Alterna estado de conexión con ESP32.
        
        Si está desconectado, inicia proceso de conexión; si conectado,
        cierra la conexión serial y desactiva operaciones dependientes.
        """
        if not self.serial_reader:
            self.connect_to_esp32()
        else:
            self.disconnect_from_esp32()

    def connect_to_esp32(self):
        """Conecta al ESP32 usando puerto serial actual.
        
        Detecta puerto disponible, crea SerialReader, inicia lectura de datos,
        sincroniza RTC y actualiza interfaz al estado conectado.
        """
        if self.connected and self.serial_reader:
            return
        
        port = None
        try:
            if hasattr(self, 'port_combo') and "No se detectan" not in self.port_combo.currentText():
                port = self.port_combo.currentText().split(" - ")[0]
        except Exception:
            port = None

        if not port:
            try:
                self.update_ports()
                if getattr(self, 'available_ports', None):
                    port = self.available_ports[0]
            except Exception:
                port = None

        if not port:
            QMessageBox.warning(self, "Error", "No hay puertos disponibles.")
            return
        self.serial_reader = SerialReader(port, 115200, callback=self.process_data)
        if self.serial_reader.start():
            self.connected = True
            self.update_ui_connected(port)
            self.calibration_mode = "laboratory"
            self.use_user_calibration = False
            self.set_calibration_mode_ui(self.calibration_mode, send_to_device=True)
            try:
                self.load_calibration_table()
                self.load_known_k_table(force_load=True)
            except Exception:
                pass
            time.sleep(0.5)
            self.apply_interval()
            self.debug_timer.start()
            try:
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                threading.Thread(target=self._send_rtc_with_retries, args=(now,), daemon=True).start()
            except Exception:
                try:
                    self.serial_reader.send_command(f"RTC_TIME:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                except Exception:
                    pass
            try:
                self.rtc_timer.start()
            except Exception:
                pass
            QMessageBox.information(self, "Éxito", f"Conectado a {port}")
            self.sensor_data.clear()
            self.temp_data.clear()
            self.curve_sensor.setData([0])
            self.curve_temp.setData([0])
            self.data_received = False
            self.reset_display()
            if self.cal_dialog:
                self.cal_dialog.set_serial(self.serial_reader, True)
            # La lectura no debe iniciar automáticamente al conectar.
        else:
            QMessageBox.critical(self, "Error", "No se pudo abrir el puerto serial.")
            self.serial_reader = None

    def connect_to_port(self, port: str):
        """Conecta al ESP32 en puerto serial especificado.
        
        Crea SerialReader para puerto dado, inicia hilo de lectura,
        sincroniza hora RTC y actualiza UI con estado de conexión.
        """
        try:
            if not port:
                return False
            self.serial_reader = SerialReader(port, 115200, callback=self.process_data)
            if self.serial_reader.start():
                self.connected = True
                self.update_ui_connected(port)
                self.calibration_mode = "laboratory"
                self.use_user_calibration = False
                self.set_calibration_mode_ui(self.calibration_mode, send_to_device=True)
                try:
                    self.load_calibration_table()
                    self.load_known_k_table(force_load=True)
                except Exception:
                    pass
                time.sleep(0.5)
                self.apply_interval()
                self.debug_timer.start()
                try:
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    threading.Thread(target=self._send_rtc_with_retries, args=(now,), daemon=True).start()
                except Exception:
                    try:
                        self.serial_reader.send_command(f"RTC_TIME:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    except Exception:
                        pass
                try:
                    self.rtc_timer.start()
                except Exception:
                    pass
                QMessageBox.information(self, "Éxito", f"Conectado a {port}")
                self.sensor_data.clear()
                self.temp_data.clear()
                self.curve_sensor.setData([0])
                self.curve_temp.setData([0])
                self.data_received = False
                self.reset_display()
                if self.cal_dialog:
                    self.cal_dialog.set_serial(self.serial_reader, True)
                # Intento rápido: solicitar al dispositivo que inicie el log de datos
                # para que la UI reciba al menos una lectura cruda (no activa la prueba).
                try:
                    if self.serial_reader:
                        self.serial_reader.send_command("LOG_START")
                        self.status_label.setText("Solicitando transmisión de datos al ESP32...")
                except Exception:
                    pass
                # La lectura no debe iniciar automáticamente al conectar.
                return True
            else:
                self.serial_reader = None
                QMessageBox.critical(self, "Error", "No se pudo abrir el puerto serial.")
                return False
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo conectar: {e}")
            self.serial_reader = None
            return False

    def disconnect_from_esp32(self):
        """Desconecta del ESP32 y cierra la aplicación.
        
        Detiene lectura serial, cierra diálogos abiertos, limpia recursos
        y termina la aplicación mostrando mensaje de desconexión.
        """
        self.connected = False
        self.reading_active = False
        
        try:
            self.debug_timer.stop()
        except Exception:
            pass
        
        try:
            self.rtc_timer.stop()
        except Exception:
            pass
        
        if self.serial_reader:
            try:
                self.serial_reader.stop()
            except Exception:
                pass
            self.serial_reader = None
        
        try:
            if self.cal_dialog:
                self.cal_dialog.close()
                self.cal_dialog = None
        except Exception:
            pass
        
        try:
            if self.csv_plot_window:
                self.csv_plot_window.close()
                self.csv_plot_window = None
        except Exception:
            pass
        
        self.update_ui_disconnected()
        QMessageBox.information(self, "Desconectado", "Se ha desconectado de la ESP32.\nLa aplicación se cerrará.")
        
        QApplication.quit()

    def reset_display(self):
        """Limpia todos los displays de la interfaz.
        
        Resetea labels de valores, limpia gráficas y datos de prueba actual,
        retornando la UI a estado inicial sin mediciones.
        """
        self.value_sensor_label.setText("-- uS")
        self.value_temp_label.setText("-- °C")
        self.k_label.setText("K: --")
        self.test_name_label.setText("Prueba: ---")
        self.curve_sensor.setData([0])
        self.curve_temp.setData([0])
        self.current_test_name = ""
        self.test_start_time = None
        self._last_oled_sync_payload = None

    def _sync_oled_with_ui_values(self):
        """Sincroniza OLED con los mismos valores ya mostrados en la UI.

        Usa current_reading como fuente única de verdad para evitar diferencias
        entre cálculos locales de la app y visualización en el ESP32.
        """
        try:
            if not (self.connected and self.serial_reader):
                return

            sensor_ui = self.current_reading.get('sensor')
            temp_ui = self.current_reading.get('temp')
            if sensor_ui is None or temp_ui is None:
                return

            payload = (round(float(temp_ui), 1), round(float(sensor_ui), 1))
            if payload == self._last_oled_sync_payload:
                return

            ok = send_show_ec_to_esp32(self.serial_reader, payload[0], payload[1])
            if ok:
                self._last_oled_sync_payload = payload
        except Exception:
            pass

    def update_ui_connected(self, port):
        """Actualiza interfaz al estado conectado.
        
        Habilita botones de lectura, muestra estado conectado en label,
        y prepara controles para operaciones de medición y calibración.
        """
        try:
            self.status_label.setText(f"Conectado a {port} - Listo")
        except Exception:
            pass
        try:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        except Exception:
            pass
        try:
            self.set_k_controls_enabled(True)
        except Exception:
            pass

    def update_ui_disconnected(self):
        """Actualiza interfaz al estado desconectado.
        
        Deshabilita botones de lectura, muestra estado desconectado
        y limpia pantalla de valores y gráficas.
        """
        try:
            self.status_label.setText("Desconectado")
        except Exception:
            pass
        try:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
        except Exception:
            pass
        try:
            self.set_k_controls_enabled(False)
        except Exception:
            pass
        self.reset_display()

    def show_connection_dialog(self):
        """Muestra diálogo de conexión con reintentos automáticos.
        
        Permite usuario seleccionar puerto, intenta conectar hasta 5 veces,
        ofreciendo reintento en caso de fallo y mostrando mensajes de estado.
        """
        max_attempts = 5
        attempt = 0
        
        while attempt < max_attempts and not self.connected:
            attempt += 1
            conn_dialog = ConnectionDialog()
            
            if conn_dialog.exec_() == QDialog.Accepted:
                port = conn_dialog.selected_port
                if port:
                    connected = self.connect_to_port(port)
                    if connected:
                        return True
                    else:
                        if attempt < max_attempts:
                            retry = QMessageBox.question(
                                self, 
                                "Conexión Fallida",
                                f"No se pudo conectar a {port}.\n\nIntento {attempt}/{max_attempts}\n\n¿Desea reintentar?",
                                QMessageBox.Retry | QMessageBox.Cancel
                            )
                            if retry != QMessageBox.Retry:
                                break
                        else:
                            QMessageBox.critical(
                                self,
                                "Error de Conexión",
                                "No se pudo conectar después de varios intentos.\nPor favor, verifique la conexión del dispositivo."
                            )
                            break
            else:
                QMessageBox.information(self, "Cancelado", "Conexión cancelada.")
                break
        
        return False

    def set_k_controls_enabled(self, enabled: bool):
        """Habilita o deshabilita controles de factor K manual.
        
        Activa/desactiva spinbox y combo box de configuración de K
        según estado de conexión del dispositivo.
        """
        try:
            if hasattr(self, 'mode_combo') and self.mode_combo is not None:
                try:
                    self.mode_combo.setEnabled(enabled)
                except Exception:
                    pass
            if hasattr(self, 'manual_k_spin') and self.manual_k_spin is not None:
                try:
                    self.manual_k_spin.setEnabled(enabled)
                except Exception:
                    pass
            if hasattr(self, 'cal_mode_switch') and self.cal_mode_switch is not None:
                try:
                    self.cal_mode_switch.setEnabled(enabled)
                except Exception:
                    pass
        except Exception:
            pass

    def _send_calibration_mode_command(self, mode: str, update_status: bool = True):
        mode = "known" if mode == "known" else "laboratory"

        if not (self.connected and self.serial_reader):
            self.calibration_mode = mode
            return False

        cmd = "USE_KNOWN_CALIBRATION" if mode == "known" else "USE_LABORATORY_CALIBRATION"
        try:
            ok = bool(self.serial_reader.send_command(cmd))
        except Exception:
            ok = False

        if ok:
            self.calibration_mode = mode
            if update_status:
                self.status_label.setText(
                    "Conectado - Calibración: Valores conocidos" if mode == "known"
                    else "Conectado - Calibración: Laboratorio"
                )
        elif update_status:
            self.status_label.setText("Conectado - Error al cambiar modo de calibración")

        return ok

    def set_calibration_mode_ui(self, mode: str, send_to_device: bool = True):
        """Configura modo de calibración (siempre en modo Laboratorio)."""
        # Forzar siempre modo laboratorio
        self.calibration_mode = "laboratory"
        self.use_user_calibration = False
        self._is_known_mode_active = False
        
        if send_to_device:
            self._send_calibration_mode_command("laboratory", update_status=False)

    def _send_rtc_with_retries(self, timestr: str, retries: int = 3, delay: float = 0.6):
        """Envía hora RTC al ESP32 con reintentos automáticos.
        
        Intenta sincronizar reloj del dispositivo hasta N veces con delays,
        actualizando estado en label y retornando éxito o fallo.
        """
        for attempt in range(1, retries + 1):
            try:
                if not self.serial_reader:
                    break
                sent = self.serial_reader.send_command(f"RTC_TIME:{timestr}")
                if sent:
                    QTimer.singleShot(0, lambda: self.status_label.setText(f"RTC sincronizado: {timestr} (intento {attempt})"))
                    return True
                else:
                    time.sleep(int(delay * 1000) / 1000.0)
            except Exception:
                try:
                    time.sleep(int(delay * 1000) / 1000.0)
                except Exception:
                    pass

        QTimer.singleShot(0, lambda: self.status_label.setText("Fallo sincronización RTC (intentos agotados)"))
        return False

    def _rtc_check_timer_timeout(self):
        """Solicita hora RTC al ESP32 periódicamente.
        
        Ejecutado por timer cada 30 segundos para validar sincronización
        de reloj del dispositivo y detectar desviaciones temporales.
        """
        try:
            if self.connected and self.serial_reader:
                self.serial_reader.send_command("GET_RTC")
        except Exception:
            pass

    def _force_set_rtc(self, timestr: str, retries: int = 2, delay: float = 0.7):
        """Fuerza escritura de hora RTC en ESP32 con reintentos.
        
        Envía comando SET_RTC hasta N veces con delays, verificando
        después si la sincronización fue exitosa usando GET_RTC.
        """
        for attempt in range(1, retries + 1):
            try:
                if not self.serial_reader:
                    break
                sent = self.serial_reader.send_command(f"SET_RTC:{timestr}")
                if sent:
                    QTimer.singleShot(0, lambda: self.status_label.setText(f"SET_RTC enviado: {timestr} (intento {attempt})"))
                    try:
                        time.sleep(0.2)
                    except Exception:
                        pass
                    try:
                        self.serial_reader.send_command("GET_RTC")
                    except Exception:
                        pass
                    return True
                else:
                    time.sleep(int(delay * 1000) / 1000.0)
            except Exception:
                try:
                    time.sleep(int(delay * 1000) / 1000.0)
                except Exception:
                    pass

        QTimer.singleShot(0, lambda: self.status_label.setText("Fallo SET_RTC (intentos agotados)"))
        return False

    def apply_interval(self):
        """Envía intervalo de lectura configurado al ESP32.
        
        Lee valor del spinbox de intervalo, lo envía mediante INTERVAL,
        actualiza estado en label y configura DataLogger con ese intervalo.
        """
        if self.connected and self.serial_reader:
            interval = self.interval_spin.value()
            # Formatea el intervalo de forma robusta: entero si es entero, else con 2 decimales
            try:
                if float(interval).is_integer():
                    payload = str(int(round(interval)))
                else:
                    payload = f"{float(interval):.2f}"
            except Exception:
                payload = str(interval)

            # Enviar dos veces con pequeño retardo para mejorar recepción en ESP32
            success = self.serial_reader.send_command(f"INTERVAL:{payload}")
            try:
                time.sleep(0.05)
            except Exception:
                pass
            try:
                self.serial_reader.send_command(f"INTERVAL:{payload}")
            except Exception:
                pass

            if success:
                self.status_label.setText(f"Conectado - Intervalo: {interval}s")
                try:
                    self.logger.set_save_interval(interval)
                except Exception:
                    pass
        try:
            if self.connected_2 and self.serial_reader_2:
                # Enviar con mismo formato/robustez al segundo dispositivo
                try:
                    if float(self.interval_spin.value()).is_integer():
                        payload2 = str(int(round(self.interval_spin.value())))
                    else:
                        payload2 = f"{float(self.interval_spin.value()):.2f}"
                except Exception:
                    payload2 = str(self.interval_spin.value())

                self.serial_reader_2.send_command(f"INTERVAL:{payload2}")
                try:
                    time.sleep(0.03)
                except Exception:
                    pass
                try:
                    self.serial_reader_2.send_command(f"INTERVAL:{payload2}")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._update_sync_timer_interval()
        except Exception:
            pass

    def _is_dual_sync_active(self):
        return bool(
            self.connected and self.serial_reader and
            self.connected_2 and self.serial_reader_2 and
            self.reading_active and self.reading_active_2
        )

    def _update_sync_timer_interval(self):
        try:
            interval_ms = int(max(100, float(self.interval_spin.value()) * 1000.0))
        except Exception:
            interval_ms = 2000
        self.sync_timer.setInterval(interval_ms)

    def _flush_synced_dual_sample(self):
        """Agrega una muestra conjunta de ambos equipos en el mismo tick de tiempo."""
        if not self._is_dual_sync_active():
            return

        sample_1 = self.pending_sample_1 if self.pending_sample_1 is not None else self.last_synced_sample_1
        sample_2 = self.pending_sample_2 if self.pending_sample_2 is not None else self.last_synced_sample_2

        if sample_1 is None or sample_2 is None:
            return

        s1, t1, k1, raw1 = sample_1
        s2, t2, k2, raw2 = sample_2

        self.pending_sample_1 = None
        self.pending_sample_2 = None
        self.last_synced_sample_1 = sample_1
        self.last_synced_sample_2 = sample_2

        self.sensor_data.append(s1)
        self.temp_data.append(t1)
        self.sensor_data_2.append(s2)
        self.temp_data_2.append(t2)
        self.data_received = True
        self.data_received_2 = True

        try:
            x_values = self._build_time_axis(len(self.sensor_data))
            self.curve_sensor.setData(x_values, [round(v, 1) for v in self.sensor_data])
            self.curve_temp.setData(x_values, [round(v, 1) for v in self.temp_data])
            self.curve_sensor_2.setData(x_values, [round(v, 1) for v in self.sensor_data_2])
            self.curve_temp_2.setData(x_values, [round(v, 1) for v in self.temp_data_2])
            self._apply_time_axis_spacing(len(self.sensor_data))
            self._apply_y_axis_spacing()
            self._apply_time_axis_spacing_2(len(self.sensor_data_2))
            self._apply_y_axis_spacing_2()
        except Exception:
            pass

        try:
            self.update_stats()
        except Exception:
            pass
        try:
            self.update_stats_2()
        except Exception:
            pass

        try:
            self._sync_oled_with_ui_values()
        except Exception:
            pass

        try:
            raw_voltage = raw1 if raw1 is not None else self.current_reading.get('raw')
            if k1 is not None:
                k_from_range = float(k1)
            elif self.current_reading.get('k') is not None:
                k_from_range = float(self.current_reading.get('k'))
            elif raw_voltage is not None:
                k_from_range = self.get_k_from_range(raw_voltage)
            else:
                k_from_range = 1.0
            self.logger.write_row(s1, t1, k_from_range, self.current_test_name)
        except Exception:
            pass

    def begin_calibration(self):
        """Inicia modo de calibración pausando lectura normal.
        
        Guarda intervalo y modo previos, finaliza prueba actual,
        envía CAL_START al ESP32 y deshabilita controles de lectura.
        """
        if self.calibration_active:
            return
        self.calibration_active = True
        self.prev_interval = self.interval_spin.value()
        self.prev_mode_text = self.mode_combo.currentText()
        self.finalize_test()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Estado: Calibración en progreso")
        if self.connected and self.serial_reader:
            self.serial_reader.send_command("CAL_START")

    def end_calibration(self):
        """Finaliza modo de calibración restaurando estado previo.
        
        Restaura intervalo y modo anteriores, envía CAL_END al ESP32,
        rehabilita controles de lectura y muestra confirmación.
        """
        if not self.calibration_active:
            return
        self.calibration_active = False
        if self.connected and self.serial_reader:
            if self.prev_interval is not None:
                self.serial_reader.send_command(f"INTERVAL:{self.prev_interval}")
            if self.prev_mode_text == "Manual":
                k_value = self.manual_k_spin.value()
                self.serial_reader.send_command(f"MODE:{k_value}")
            else:
                self.serial_reader.send_command("MODE:0")
            self.serial_reader.send_command("CAL_END")

        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Estado: Conectado - Calibración finalizada")

    def load_user_calibration(self, archivo_cfg="calibration_ranges.cfg"):
        """Carga calibración personalizada del usuario desde cfg.
        
        Lee sección USER_CALIBRATION con conductividades conocidas y sus
        factores K, aplicándolos localmente para futuras mediciones.
        """
        try:
            if archivo_cfg == "calibration_ranges.cfg":
                archivo_cfg = APP_CALIBRATION_CFG
            if not os.path.exists(archivo_cfg):
                self.user_calibration = {}
                return {}
        
            cfg = configparser.ConfigParser()
            cfg.read(archivo_cfg, encoding='utf-8')
        
            if 'USER_CALIBRATION' not in cfg:
                self.user_calibration = {}
                return {}
        
            table = {}
            for key, val in cfg.items('USER_CALIBRATION'):
                try:
                    cond = int(float(key))
                    raw_v = None
                    if isinstance(val, str) and ',' in val:
                        parts = [p.strip() for p in val.split(',') if p.strip()]
                        try:
                            k = float(parts[0]) if parts else float(val)
                        except Exception:
                            k = float(val.split(',')[0]) if val else 0.0
                        try:
                            if len(parts) > 1:
                                raw_v = float(parts[1])
                        except Exception:
                            raw_v = None
                    else:
                        k = float(val)

                    table[cond] = k
                    try:
                        if raw_v is not None:
                            self.user_calibration_raw[cond] = float(raw_v)
                    except Exception:
                        pass
                except Exception:
                    continue
            
            self.user_calibration = table
            try:
                if hasattr(self, 'apply_local_calibration_and_update'):
                    self.apply_local_calibration_and_update()
            except Exception:
                pass
            return table
        except Exception:
            self.user_calibration = {}
            return {}
        
    def load_calibration_table(self):
        """Carga tabla de calibración seleccionada actualmente.
        
        Lee configuración para obtener tabla CAL_TABLE_N con rangos de voltaje
        y factores K, almacenándolos para aplicación durante mediciones.
        """
        try:
            cfg_path = APP_CALIBRATION_CFG
            if not os.path.exists(cfg_path):
                return {}
            
            config = configparser.ConfigParser()
            config.read(cfg_path)
            
            selected_idx = getattr(self, 'default_cal_table', 1)
            section_name = f"CAL_TABLE_{selected_idx}"
            
            if section_name not in config:
                return {}
            
            calibration_dict = {}
            for key, value in config.items(section_name):
                try:
                    min_v, max_v = map(float, key.split('-'))
                    k_value = float(value)
                    calibration_dict[(min_v, max_v)] = k_value
                except ValueError:
                    continue
            
            self.loaded_calibration = calibration_dict
            try:
                if hasattr(self, 'apply_local_calibration_and_update'):
                    self.apply_local_calibration_and_update()
            except Exception:
                pass
            return calibration_dict
        except Exception as e:
            print(f"Error cargando tabla de calibración: {e}")
            self.loaded_calibration = {}
            return {}

    def load_known_k_table(self, archivo_cfg="calibration_ranges.cfg", force_load=False):
        """Carga rangos/tabla K de forma determinista según modo de calibración.

        Reglas de aislamiento de fuentes:
        - Modo `laboratory`: usa únicamente LABORATORY_CALIBRATION(_RANGES).
        - Modo `known`: usa únicamente KNOWN_K_* / USER_CALIBRATION / MEASURED_VOLTAGE_RANGES.
        """
        try:
            if archivo_cfg == "calibration_ranges.cfg":
                archivo_cfg = APP_CALIBRATION_CFG
            mode = "known" if getattr(self, 'calibration_mode', 'laboratory') == "known" else "laboratory"

            if not os.path.exists(archivo_cfg):
                self.known_k_ranges = {}
                self.known_k_table = {}
                return {}

            cfg = configparser.ConfigParser()
            try:
                cfg.read(archivo_cfg, encoding='utf-8')
            except Exception:
                cfg.read(archivo_cfg)

            self.known_k_ranges = {}
            self.known_k_table = {}

            if mode == "laboratory":
                if 'LABORATORY_CALIBRATION_RANGES' not in cfg or len(cfg.items('LABORATORY_CALIBRATION_RANGES')) == 0:
                    self.use_user_calibration = False
                    return {}

                ranges_table = {}
                for key, val in cfg.items('LABORATORY_CALIBRATION_RANGES'):
                    try:
                        parts = key.split(',')
                        if len(parts) == 2:
                            v_min = float(parts[0].strip())
                            v_max = float(parts[1].strip())
                            k_val = float(str(val).split('#', 1)[0].strip())
                            v_ref = (v_min + v_max) / 2.0
                            ranges_table[v_ref] = (k_val, v_min, v_max)
                    except Exception:
                        continue

                if not ranges_table:
                    self.use_user_calibration = False
                    return {}

                self.known_k_ranges = ranges_table

                if 'LABORATORY_CALIBRATION' in cfg:
                    for _, val in cfg.items('LABORATORY_CALIBRATION'):
                        try:
                            parts = str(val).split(',')
                            if len(parts) >= 2:
                                k_val = float(parts[0].split('#', 1)[0].strip())
                                measured_v = float(parts[1].split('#', 1)[0].strip())
                                self.known_k_table[measured_v] = k_val
                        except Exception:
                            continue

                if not self.known_k_table:
                    self.known_k_table = {v_ref: data[0] for v_ref, data in ranges_table.items()}

                self.use_user_calibration = False
                print(f"✓ Calibración de LABORATORIO cargada: {len(ranges_table)} rangos")
                return ranges_table

            # mode == "known"
            if 'KNOWN_K_RANGES' in cfg and len(cfg.items('KNOWN_K_RANGES')) > 0:
                ranges_table = {}
                for key, val in cfg.items('KNOWN_K_RANGES'):
                    try:
                        parts = key.split(',')
                        if len(parts) == 2:
                            v_min = float(parts[0].strip())
                            v_max = float(parts[1].strip())
                            k_val = float(str(val).split('#', 1)[0].strip())
                            v_ref = (v_min + v_max) / 2.0
                            ranges_table[v_ref] = (k_val, v_min, v_max)
                    except Exception:
                        continue

                if ranges_table:
                    self.known_k_ranges = ranges_table
                    if 'KNOWN_K_TABLE' in cfg and len(cfg.items('KNOWN_K_TABLE')) > 0:
                        self._load_known_k_table_for_interpolation(cfg)
                    if not self.known_k_table:
                        self.known_k_table = {v_ref: data[0] for v_ref, data in ranges_table.items()}
                    self.use_user_calibration = True
                    return ranges_table

            if 'KNOWN_K_TABLE' in cfg and len(cfg.items('KNOWN_K_TABLE')) > 0:
                self._load_known_k_table_for_interpolation(cfg)
                if self.known_k_table:
                    self._generate_ranges_from_table()
                    self.use_user_calibration = True
                    return self.known_k_ranges

            if 'USER_CALIBRATION' in cfg:
                user_cal_data = {}
                for key, val in cfg.items('USER_CALIBRATION'):
                    try:
                        known_cond = int(float(key))
                        parts = str(val).split(',')
                        if len(parts) >= 2:
                            k_val = float(parts[0].split('#', 1)[0].strip())
                            raw_v = float(parts[1].split('#', 1)[0].strip())
                            user_cal_data[known_cond] = (k_val, raw_v)
                        else:
                            k_val = float(parts[0].split('#', 1)[0].strip())
                            user_cal_data[known_cond] = (k_val, None)
                    except Exception:
                        continue

                if user_cal_data:
                    sorted_points = sorted(
                        [(cond, raw_v) for cond, (_, raw_v) in user_cal_data.items() if raw_v is not None],
                        key=lambda x: x[1]
                    )

                    ranges_table = {}
                    for i, (cond, raw_v) in enumerate(sorted_points):
                        k_val, _ = user_cal_data[cond]
                        min_v = 0.0 if i == 0 else sorted_points[i - 1][1]
                        max_v = raw_v * 2.0 if i == len(sorted_points) - 1 else sorted_points[i + 1][1]
                        ranges_table[cond] = (k_val, min_v, max_v)

                    self.known_k_ranges = ranges_table
                    self.known_k_table = {k: v[0] for k, v in ranges_table.items()}
                    self.use_user_calibration = True
                    return ranges_table

            if 'MEASURED_VOLTAGE_RANGES' in cfg:
                ranges_table = {}
                for key, val in cfg.items('MEASURED_VOLTAGE_RANGES'):
                    try:
                        known_cond = int(float(key))
                        parts = str(val).split(',')
                        if len(parts) >= 2:
                            k_val = float(parts[0].split('#', 1)[0].strip())
                            volt_range = parts[1].strip()
                            min_v, max_v = [float(x) for x in volt_range.split('-')]
                            ranges_table[known_cond] = (k_val, min_v, max_v)
                        else:
                            k_val = float(parts[0].split('#', 1)[0].strip())
                            ranges_table[known_cond] = (k_val, 0.0, 10.0)
                    except Exception:
                        continue

                if ranges_table:
                    self.known_k_ranges = ranges_table
                    self.known_k_table = {k: v[0] for k, v in ranges_table.items()}
                    self.use_user_calibration = True
                    return ranges_table

            self.use_user_calibration = True
            return {}

        except Exception as e:
            print(f"Error cargando calibraciones: {e}")
            self.known_k_ranges = {}
            self.known_k_table = {}
            self.use_user_calibration = (getattr(self, 'calibration_mode', 'laboratory') == 'known')
            return {}

    def _load_known_k_table_for_interpolation(self, cfg):
        """Carga tabla K de puntos para interpolación.
        
        Lee KNOWN_K_TABLE con voltajes y factores K, preparando datos
        para interpolar K en voltajes intermedios no medidos.
        """
        self.known_k_table = {}
        try:
            if 'KNOWN_K_TABLE' in cfg:
                for key, val in cfg.items('KNOWN_K_TABLE'):
                    try:
                        voltage = float(key.strip())
                        k_val = float(val.strip())
                        self.known_k_table[voltage] = k_val
                    except Exception:
                        continue
        except Exception:
            pass

    def _generate_ranges_from_table(self):
        """Genera rangos K desde tabla de puntos de calibración.
        
        Crea intervalos de voltaje sin solapamiento usando puntos medios,
        asociando a cada rango su factor K correspondiente de la tabla.
        """
        if not self.known_k_table:
            self.known_k_ranges = {}
            return
        
        sorted_points = sorted(self.known_k_table.items(), key=lambda x: x[0])
        
        self.known_k_ranges = {}
        for i, (voltage, k_val) in enumerate(sorted_points):
            if i == 0:
                v_min = 0.0
                if len(sorted_points) > 1:
                    v_max = (voltage + sorted_points[i + 1][0]) / 2.0
                else:
                    v_max = voltage * 2.0
            elif i == len(sorted_points) - 1:
                v_min = (sorted_points[i - 1][0] + voltage) / 2.0
                v_max = max(voltage * 2.0, 10.0)
            else:
                v_min = (sorted_points[i - 1][0] + voltage) / 2.0
                v_max = (voltage + sorted_points[i + 1][0]) / 2.0
            
            self.known_k_ranges[voltage] = (k_val, v_min, v_max)

    def get_k_from_range(self, raw_voltage):
        """Resuelve K a partir de voltaje usando rangos y fallback por tabla.

        Prioridad:
        1) Rangos precomputados en `known_k_ranges`.
        2) Tabla de puntos en `known_k_table` con selección por vecindad.

        Args:
            raw_voltage: Voltaje de entrada para consulta.

        Returns:
            Factor K estimado para el voltaje. Retorna 0.0 si no hay datos.
        """
        if raw_voltage is None:
            return 0.0
        
        try:
            raw_voltage = float(raw_voltage)
            
            if hasattr(self, 'known_k_ranges') and self.known_k_ranges:
                for v_ref, (k_val, v_min, v_max) in self.known_k_ranges.items():
                    if v_min <= raw_voltage <= v_max:
                        print(f"[CSV] Voltaje {raw_voltage:.6f}V cae en rango [{v_min:.6f}-{v_max:.6f}] → K={k_val:.6f}")
                        return k_val
            
            if hasattr(self, 'known_k_table') and self.known_k_table:
                sorted_points = sorted(self.known_k_table.items(), key=lambda x: x[0])
                if len(sorted_points) == 1:
                    print(f"[CSV] Voltaje {raw_voltage:.6f}V usando tabla (1 punto) → K={sorted_points[0][1]:.6f}")
                    return sorted_points[0][1]
                
                for i, (v, k) in enumerate(sorted_points):
                    if raw_voltage <= v:
                        if i == 0:
                            print(f"[CSV] Voltaje {raw_voltage:.6f}V usando tabla (< primer punto) → K={sorted_points[0][1]:.6f}")
                            return sorted_points[0][1]
                        else:
                            print(f"[CSV] Voltaje {raw_voltage:.6f}V usando tabla (entre puntos) → K={sorted_points[i - 1][1]:.6f}")
                            return sorted_points[i - 1][1]
                print(f"[CSV] Voltaje {raw_voltage:.6f}V usando tabla (> último punto) → K={sorted_points[-1][1]:.6f}")
                return sorted_points[-1][1]
        except Exception as e:
            print(f"[CSV ERROR] get_k_from_range({raw_voltage}): {e}")
        
        return 0.0

    def interpolate_k(self, voltage):
        """Obtiene factor K por interpolación o rangos definidos.
        
        Busca K del rango que contiene el voltaje, o interpola linealmente
        entre puntos de la tabla K si no hay rangos definidos.
        """
        # Primero intenta usar los rangos definidos en KNOWN_K_RANGES
        if hasattr(self, 'known_k_ranges') and self.known_k_ranges:
            for v_ref, (k_val, v_min, v_max) in self.known_k_ranges.items():
                if v_min <= voltage <= v_max:
                    return k_val
        
        # Si no hay rangos, interpola usando la tabla de puntos (fallback)
        if not self.known_k_table:
            return None
        
        sorted_points = sorted(self.known_k_table.items(), key=lambda x: x[0])
        
        if len(sorted_points) == 1:
            return sorted_points[0][1]
        
        for i, (v, k) in enumerate(sorted_points):
            if voltage <= v:
                if i == 0:
                    return sorted_points[0][1]
                else:
                    v_prev, k_prev = sorted_points[i - 1]
                    if v - v_prev == 0:
                        return k
                    factor = (voltage - v_prev) / (v - v_prev)
                    k_interp = k_prev + (k - k_prev) * factor
                    return k_interp
        
        return sorted_points[-1][1]

    @staticmethod
    def _dfrobot_ec(voltage):
        """Polinomio DFRobot: voltaje compensado → EC (µS/cm)."""
        v = float(voltage)
        ec = 133.42 * v * v * v - 255.86 * v * v + 857.39 * v
        return max(ec, 0.0)

    def _build_time_axis(self, samples_count):
        try:
            interval = float(self.interval_spin.value())
            if interval <= 0:
                interval = 1.0
        except Exception:
            interval = 1.0
        return [round(i * interval, 2) for i in range(samples_count)]

    def _apply_time_axis_spacing(self, samples_count):
        try:
            if samples_count <= 1:
                return
            interval = float(self.interval_spin.value())
            if interval <= 0:
                interval = 1.0
            total_time = (samples_count - 1) * interval
            major = max(1.0, round(total_time / 8.0, 1))
            minor = max(0.2, round(major / 5.0, 1))
            self.plot_sensor.getAxis('bottom').setTickSpacing(major=major, minor=minor)
            self.plot_temp.getAxis('bottom').setTickSpacing(major=major, minor=minor)
        except Exception:
            pass

    def _apply_y_axis_spacing(self):
        try:
            if self.sensor_data:
                sensor_min = min(self.sensor_data)
                sensor_max = max(self.sensor_data)
                sensor_span = max(sensor_max - sensor_min, 1.0)
                sensor_major = max(1.0, round(sensor_span / 6.0, 1))
                sensor_minor = max(0.2, round(sensor_major / 5.0, 1))
                self.plot_sensor.getAxis('left').setTickSpacing(major=sensor_major, minor=sensor_minor)

            if self.temp_data:
                temp_min = min(self.temp_data)
                temp_max = max(self.temp_data)
                temp_span = max(temp_max - temp_min, 0.5)
                temp_major = max(0.2, round(temp_span / 6.0, 1))
                temp_minor = max(0.1, round(temp_major / 2.0, 1))
                self.plot_temp.getAxis('left').setTickSpacing(major=temp_major, minor=temp_minor)
        except Exception:
            pass

    def apply_local_calibration(self, sensor=None, temp=None, raw=None):
        """Devuelve la lectura ya calculada por el ESP32 sin reinterpretarla localmente."""
        try:
            if sensor is None:
                return None, None

            current_k = None
            try:
                current_k = self.current_reading.get('k')
            except Exception:
                pass

            if current_k is not None:
                try:
                    current_k = float(current_k)
                except Exception:
                    current_k = None

            return float(sensor), current_k
        except Exception:
            return None, None

    def apply_local_calibration_and_update(self):
        """Refresca la interfaz con la última lectura recibida sin recalcularla localmente."""
        try:
            cr = getattr(self, 'current_reading', {})
            s = cr.get('sensor')
            t = cr.get('temp')
            k_new = cr.get('k')
            if s is None:
                return False

            sensor_value = float(s)

            if self.reading_active and hasattr(self, 'sensor_data') and self.sensor_data:
                try:
                    self.sensor_data[-1] = sensor_value
                    x_values = self._build_time_axis(len(self.sensor_data))
                    self.curve_sensor.setData(x_values, [round(v, 1) for v in self.sensor_data])
                    self._apply_time_axis_spacing(len(self.sensor_data))
                    self._apply_y_axis_spacing()
                except Exception:
                    pass

            try:
                if self.reading_active:
                    self.value_sensor_label.setText(f"{sensor_value:.1f} µS")
                    if t is not None:
                        self.value_temp_label.setText(f"{t:.1f} °C")
                    if k_new is not None:
                        self.k_label.setText(f"K: {float(k_new):.1f}")
                else:
                    self.value_sensor_label.setText("-- µS")
                    self.value_temp_label.setText("-- °C")
                    self.k_label.setText("K: --")
                    self.curve_sensor.setData([0])
                    self.curve_temp.setData([0])
            except Exception:
                pass

            try:
                if self.reading_active:
                    self._sync_oled_with_ui_values()
            except Exception:
                pass

            return True
        except Exception:
            return False

    def _begin_reading_session(self, test_name: str):
        test_name = (test_name or "").strip()
        if not test_name:
            return False

        self.current_test_name = test_name
        self.test_start_time = datetime.now()
        self._last_oled_sync_payload = None
        self.test_name_label.setText(f"Prueba: {test_name}")
        self.logger.set_test_name(test_name)

        self.sensor_data.clear()
        self.temp_data.clear()
        self.data_received = False
        self.curve_sensor.setData([])
        self.curve_temp.setData([])
        self.pending_sample_1 = None
        self.pending_sample_2 = None
        self.last_synced_sample_1 = None
        self.last_synced_sample_2 = None

        self.value_sensor_label.setText("-- µS")
        self.value_temp_label.setText("-- °C")
        self.raw_label.setText("-- V")
        self.k_label.setText("K: --")
        self.stats_label.setText("Estadísticas: Aguardando datos...")

        # Limpia también el panel del segundo dispositivo para evitar mostrar valores viejos
        # antes de que llegue la primera muestra real de la nueva sesión.
        self.sensor_data_2.clear()
        self.temp_data_2.clear()
        self.data_received_2 = False
        self.curve_sensor_2.setData([])
        self.curve_temp_2.setData([])
        self.current_reading_2 = {'sensor': None, 'temp': None, 'k': None, 'ema': None, 'raw': None, 'signal': None}
        self.pending_sample_1 = None
        self.pending_sample_2 = None
        self.last_synced_sample_1 = None
        self.last_synced_sample_2 = None
        self.value_sensor_label_2.setText("-- µS")
        self.value_temp_label_2.setText("-- °C")
        self.raw_label_2.setText("-- V")
        self.k_label_2.setText("K: --")
        self.stats_label_2.setText("Estadísticas (Dev 2): Aguardando datos...")

        self.reading_active = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText(f"Estado: Lectura activa - {test_name}")
        self.apply_interval()

        try:
            if self.connected and self.serial_reader:
                self.serial_reader.send_command("LOG_START")
        except Exception:
            pass

        # El segundo dispositivo sigue el mismo ciclo de lectura cuando está conectado.
        try:
            if self.connected_2 and self.serial_reader_2:
                self.reading_active_2 = True
                self.serial_reader_2.send_command("LOG_START")
                self.pending_sample_1 = None
                self.pending_sample_2 = None
                self.sync_timer.start()
            else:
                self.sync_timer.stop()
        except Exception:
            pass

        print(f"✅ LECTURA INICIADA - Solución: {test_name}")
        return True

    def _auto_start_synced_reading_if_ready(self):
        """Inicia lectura sincronizada automáticamente cuando ambos equipos están conectados."""
        # Deshabilitado: la lectura solo inicia al presionar el botón correspondiente.
        return False

    def start_reading(self):
        """Inicia lectura y registro de datos del sensor.
        
        Solicita nombre de prueba, limpia buffers de datos, inicia timer de GUI,
        envía comando LOG_START al ESP32 y actualiza UI para modo lectura activa.
        """
        if not self.connected:
            QMessageBox.warning(self, "Error", "Conéctate primero al puerto serial.")
            return
        
        try:
            self.load_known_k_table(force_load=True)
        except Exception:
            pass
        self.load_calibration_table()

        # Mostrar visualización de rangos únicamente al iniciar lectura
        try:
            pending_count = getattr(self, 'pending_known_ranges_count', None)
            if pending_count is not None:
                QMessageBox.information(
                    self,
                    "Rangos actualizados",
                    f"Total de rangos existentes: {int(pending_count)}"
                )
                self.pending_known_ranges_count = None
        except Exception:
            pass
        
        name, ok = QInputDialog.getText(self, "Nombre de Solución", "Ingrese el nombre de la solución:")
        if not ok or not name.strip():
            QMessageBox.warning(self, "Advertencia", "Debe ingresar un nombre para la solución.")
            return

        self._begin_reading_session(name.strip())

    def stop_reading(self):
        """Detiene lectura activa del sensor.
        
        Finaliza prueba actual, pausando captura de datos y limpiando
        estado de lectura para permitir nueva sesión.
        """
        self.finalize_test()

    def finalize_test(self):
        """Finaliza prueba actual limpiando datos y estado.
        
        Envía LOG_STOP al ESP32, limpia buffers de muestras, resetea displays,
        establece estado de lectura inactiva y muestra confirmación en status.
        """
        if self.reading_active:
            self.reading_active = False

            try:
                if self.connected and self.serial_reader:
                    self.serial_reader.send_command("LOG_STOP")
            except Exception:
                pass

            try:
                if self.connected_2 and self.serial_reader_2:
                    self.serial_reader_2.send_command("LOG_STOP")
            except Exception:
                pass

            self.reading_active_2 = False

            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            
            completed_test_name = self.current_test_name
            
            self.sensor_data.clear()
            self.temp_data.clear()
            self.value_sensor_label.setText("-- µS")
            self.value_temp_label.setText("-- °C")
            self.k_label.setText("K: --")
            self.curve_sensor.setData([0])
            self.curve_temp.setData([0])
            self.stats_label.setText("Estadísticas: Aguardando datos...")
            self.test_name_label.setText("Prueba: ---")
            
            self.current_test_name = ""
            self.test_start_time = None
            self.data_received = False
            self.data_received_2 = False
            self._last_oled_sync_payload = None
            self.pending_sample_1 = None
            self.pending_sample_2 = None
            self.sync_timer.stop()
            
            if completed_test_name:
                self.status_label.setText(f"Estado: Conectado - Lectura detenida ('{completed_test_name}' guardada)")
                print(f"✅ LECTURA DETENIDA - Solución '{completed_test_name}' finalizada y datos guardados")
            else:
                self.status_label.setText("Estado: Conectado - Lectura detenida")

    def update_stats(self):
        """Actualiza panel de estadísticas de la prueba actual.
        
        Calcula mín, máx, promedio de conductividad y temperatura,
        mostrando resumen en formato HTML en el label de estadísticas.
        """
        if not self.sensor_data or not self.temp_data:
            self.stats_label.setText("Estadísticas: Aguardando datos...")
            return
        
        try:
            sensor_min = min(self.sensor_data)
            sensor_max = max(self.sensor_data)
            sensor_avg = sum(self.sensor_data) / len(self.sensor_data)
        
            temp_min = min(self.temp_data)
            temp_max = max(self.temp_data)
            temp_avg = sum(self.temp_data) / len(self.temp_data)
        
            num_samples = len(self.sensor_data)
        
            stats_text = (
                f"<b>Conductividad:</b> Min={sensor_min:.1f} | Máx={sensor_max:.1f} | Prom={sensor_avg:.1f} uS  |  "
                f"<b>Temperatura:</b> Min={temp_min:.1f} | Máx={temp_max:.1f} | Prom={temp_avg:.1f} °C  |  "
                f"<b>Muestras:</b> {num_samples}"
            )
            self.stats_label.setText(stats_text)
        except Exception as e:
            self.stats_label.setText(f"Error calculando estadísticas: {str(e)[:50]}")

    def process_data(self, line):
        """Recibe línea serial y la encola para procesamiento en GUI.
        
        Callback del SerialReader que almacena líneas entrantes en cola thread-safe
        para ser procesadas en el hilo de la GUI.
        """
        line = line.strip()
        if not line:
            return
        with self.queue_lock:
            self.line_queue.append(line)

    def flush_queue(self):
        """Procesa líneas encoladas en el hilo de la GUI.
        
        Extrae líneas de la cola thread-safe y las pasa a handle_serial_line,
        ejecutándose periódicamente por timer de 100ms.
        """
        try:
            lines = []
            with self.queue_lock:
                while self.line_queue:
                    lines.append(self.line_queue.popleft())
            for line in lines:
                try:
                    self.handle_serial_line(line)
                except Exception:
                    pass
        except KeyboardInterrupt:
            try:
                QApplication.quit()
            except Exception:
                pass
        except Exception as e:
            print("Error en flush_queue:", e)

    def handle_serial_line(self, line: str):
        """Procesa una línea serial y actualiza estado operativo de la UI.

        Entradas:
            line: Texto recibido por puerto serial (JSON o formato plano legacy).

        Comportamiento:
            - Filtra ruido de boot/logs no útiles.
            - Prioriza payload JSON tipo `reading` como fuente de verdad.
            - Aplica fallback de parsing para compatibilidad con formatos antiguos.
            - En modo lectura activa, actualiza gráficas/labels y persiste muestra CSV.

        Salida:
            No retorna valor; produce efectos sobre estado, UI y telemetría.
        """
        line = (line or "").strip()
        if not line:
            return

        _boot_prefixes = (
            'ets ', 'rst:', 'configsip:', 'clk_drv:', 'mode:', 'load:',
            'ho ', 'entry ', 'E (', 'W (', 'I (', 'D (',
            '[SD ', '[DEBUG', '[ERROR', '[INFO', '[WARN',
        )
        if any(line.startswith(p) for p in _boot_prefixes):
            return

        sensor = None
        temp = None
        k = None
        raw = None
        raw_ch = None

        # Formato de firmware .ino:
        # YYYY-MM-DD HH:MM:SS TEMP EC
        # PROM30 YYYY-MM-DD HH:MM:SS TEMP EC
        # Evita que el parser genérico tome "2026" como valor de sensor.
        try:
            ts_match = re.match(
                r'^(?:PROM\d+\s+)?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(NaN|[-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s*$',
                line,
                flags=re.IGNORECASE,
            )
            if ts_match:
                temp_token = ts_match.group(1)
                ec_token = ts_match.group(2)
                sensor = float(ec_token)
                temp = None if str(temp_token).lower() == 'nan' else float(temp_token)
        except Exception:
            pass

        try:
            if sensor is None and line.startswith('{'):
                obj = json.loads(line)
                msg_type = obj.get('type', '')

                if msg_type and msg_type not in ('reading',):
                    # No cortar el flujo aquí: más abajo se procesan ACKs/JSON de control.
                    pass
            
                if obj.get('type') == 'reading':
                    s_val = obj.get('sensor')
                    sensor = float(s_val) if s_val is not None else None
                
                    t_val = obj.get('temp')
                    temp = float(t_val) if t_val is not None else None
                
                    k_val = obj.get('k')
                    k = float(k_val) if k_val is not None else None
                
                    raw_val = obj.get('raw_V')
                    raw = float(raw_val) if raw_val is not None else None
                    
                    # Reusa raw previo cuando llega solo temperatura para mantener continuidad.
                    if raw is None and temp is not None:
                        prev_raw = self.current_reading.get('raw')
                        if prev_raw is not None:
                            raw = prev_raw
                else:
                    cond = obj.get('conductivity', {})
                    s_val = cond.get('ec_uS')  
                    sensor = float(s_val) if s_val is not None else None
                
                    temp_obj = obj.get('temperature', {})
                    t_val = temp_obj.get('temp_C')
                    temp = float(t_val) if t_val is not None else None
                
                    k_val = obj.get('k_factor')
                    k = float(k_val) if k_val is not None else None
                
                    if sensor is None:
                        s_val = obj.get('sensor') if 'sensor' in obj else obj.get('sensor_ec') if 'sensor_ec' in obj else obj.get('sensor_tds')
                        if s_val is None:
                            s_val = obj.get('S') if 'S' in obj else obj.get('c')
                        sensor = float(s_val) if s_val is not None else None

                    if temp is None:
                        t_val = obj.get('temp') if 'temp' in obj else obj.get('T')
                        temp = float(t_val) if t_val is not None else None

                    if k is None:
                        k_val = obj.get('k') if 'k' in obj else obj.get('K') if 'K' in obj else obj.get('k_value')
                        k = float(k_val) if k_val is not None else None
        except Exception:
            sensor = temp = k = None

        if sensor is None or temp is None:
            try:
                # Prioridad alta: formato monitor_serial.py -> "idx,valor".
                if sensor is None and ',' in line:
                    csv_parts = [p.strip() for p in line.split(',') if p.strip()]
                    if len(csv_parts) >= 2:
                        try:
                            int(csv_parts[0])
                            sensor = float(csv_parts[1])
                            if len(csv_parts) >= 3 and temp is None:
                                temp = float(csv_parts[2])
                        except Exception:
                            pass

                parts = [p.strip() for p in re.split('[,;]', line) if p.strip()]
                for p in parts:
                    if ':' in p:
                        key, val = p.split(':', 1)
                        key = key.strip().lower()
                        val = val.strip()
                        if ('sensor' in key or key == 's') and sensor is None:
                            try:
                                sensor = float(val)
                                continue
                            except Exception:
                                pass
                        if ('temp' in key or 'temperature' in key or key == 't') and temp is None:
                            try:
                                temp = float(val)
                                continue
                            except Exception:
                                pass
                        if key.startswith('k') and k is None:
                            try:
                                k = float(val)
                                continue
                            except Exception:
                                pass
                    else:
                        nums = re.findall(r'[-+]?[0-9]*\.?[0-9]+', p)
                        if len(nums) >= 2 and sensor is None and temp is None:
                            sensor = float(nums[0])
                            temp = float(nums[1])
                            if len(nums) >= 3:
                                k = float(nums[2])
                if (sensor is None or temp is None) and not parts:
                    nums = re.findall(r'[-+]?[0-9]*\.?[0-9]+', line)
                    if nums:
                        if sensor is None and len(nums) >= 1:
                            sensor = float(nums[0])
                        if temp is None and len(nums) >= 2:
                            temp = float(nums[1])
                        if k is None and len(nums) >= 3:
                            k = float(nums[2])
            except Exception:
                pass

        try:
            obj_tmp = json.loads(line) if line.startswith('{') else None
            if obj_tmp and isinstance(obj_tmp, dict):
                t = obj_tmp.get('type')
                if t == 'identify':
                    try:
                        dev_type = obj_tmp.get('device_type')
                        dev_id = obj_tmp.get('device_id')
                        fw = obj_tmp.get('firmware')
                        cfg_name = obj_tmp.get('cfg')
                        proto = obj_tmp.get('protocol')
                        self.device_info = {'device_type': dev_type, 'device_id': dev_id, 'firmware': fw, 'cfg': cfg_name, 'protocol': proto}
                        print(f"[IDENTIFY] Device connected: {dev_type} {dev_id} fw={fw} cfg={cfg_name} proto={proto}")
                        try:
                            self.status_label.setText(f"Conectado: {dev_type} {dev_id} ({fw})")
                        except Exception:
                            pass
                    except Exception:
                        pass
                if t and (t.startswith('cal') or t in ('measured_raw', 'store_k_result')):
                    try:
                        self.calibration_message.emit(obj_tmp)
                    except Exception:
                        pass
                # ACK for update_k_table should also notify calibration flows
                elif t == 'update_k_table':
                    try:
                        # store last ack for synchronous waits
                        self.last_update_k_table = obj_tmp
                        try:
                            self.calibration_message.emit(obj_tmp)
                        except Exception:
                            pass
                    except Exception:
                        pass
                elif t == 'rtc_sync_request' or t == 'rtc_invalid':
                    try:
                        if self.connected and self.serial_reader:
                            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            try:
                                threading.Thread(target=self._send_rtc_with_retries, args=(now,), daemon=True).start()
                            except Exception:
                                sent = self.serial_reader.send_command(f"RTC_TIME:{now}")
                                if sent:
                                    self.status_label.setText(f"RTC sincronizado: {now}")
                                else:
                                    self.status_label.setText("Error enviando RTC_TIME al dispositivo")
                    except Exception:
                        pass
                elif t == 'rtc':
                    try:
                        ok = obj_tmp.get('ok', False)
                        dt = obj_tmp.get('datetime')
                        if ok and dt:
                            try:
                                dev_dt = datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
                                now = datetime.now()
                                diff = abs((now - dev_dt).total_seconds())
                                self.rtc_sync_attempts = 0
                                self.status_label.setText(f"RTC: {dt} (Δ {int(diff)}s)")
                                if diff > 5:
                                    timestr = now.strftime('%Y-%m-%d %H:%M:%S')
                                    try:
                                        threading.Thread(target=self._force_set_rtc, args=(timestr,), daemon=True).start()
                                    except Exception:
                                        try:
                                            self.serial_reader.send_command(f"SET_RTC:{timestr}")
                                        except Exception:
                                            pass
                            except Exception:
                                self.status_label.setText(f"RTC: {dt}")
                        else:
                            if self.rtc_sync_attempts < self.rtc_max_attempts:
                                self.rtc_sync_attempts += 1
                                try:
                                    self.serial_reader.send_command("SYNC_RTC")
                                except Exception:
                                    pass
                    except Exception:
                        pass
                elif t == 'rtc_sync_result' or t == 'rtc_set':
                    try:
                        ok = obj_tmp.get('ok', False)
                        msg = obj_tmp.get('msg', '')
                        dt = obj_tmp.get('datetime')
                        if ok:
                            self.rtc_sync_attempts = 0
                            self.status_label.setText(f"RTC sincronizado: {dt}")
                        else:
                            if self.rtc_sync_attempts < self.rtc_max_attempts:
                                self.rtc_sync_attempts += 1
                                QTimer.singleShot(1500, lambda: self.serial_reader.send_command("SYNC_RTC"))
                            else:
                                self.status_label.setText(f"RTC no sincronizado: {msg}")
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            self.last_data_time = time.time()
        except Exception:
            pass

        try:
            if sensor is not None:
                self.current_reading['sensor'] = sensor
                self.esp32_sensor_raw = sensor  # Guardar sensor raw del ESP32
            if temp is None and sensor is not None:
                prev_temp = self.current_reading.get('temp')
                temp = prev_temp if prev_temp is not None else 25.0
            if temp is not None:
                self.current_reading['temp'] = temp
            if k is not None:
                self.current_reading['k'] = k
            if raw is not None:
                self.current_reading['raw'] = raw
                self.current_reading['signal'] = raw
            if sensor is not None or raw is not None:
                self.data_received = True

            try:
                if self.connected:
                    if sensor is not None and temp is not None:
                        self.value_sensor_label.setText(f"{sensor:.1f} µS")
                        self.value_temp_label.setText(f"{temp:.1f} °C")
                        if raw is not None:
                            self.raw_label.setText(f"{raw:.6f} V")
                        if k is not None:
                            self.k_label.setText(f"K: {k:.1f}")
                        else:
                            self.k_label.setText("K: --")
                        if not self.reading_active and raw is not None:
                            self.status_label.setText(
                                f"Conectado - Leyendo sensor: {sensor:.1f} µS | {raw:.6f} V"
                            )
                else:
                    self.value_sensor_label.setText("-- µS")
                    self.value_temp_label.setText("-- °C")
                    self.raw_label.setText("-- V")
                    self.k_label.setText("K: --")
            except Exception:
                pass

            if self._is_dual_sync_active() and sensor is not None and temp is not None:
                self.pending_sample_1 = (sensor, temp, k, raw)
                self.last_synced_sample_1 = self.pending_sample_1
            elif self.reading_active and sensor is not None and temp is not None:
                self.sensor_data.append(sensor)
                self.temp_data.append(temp)
                try:
                    x_values = self._build_time_axis(len(self.sensor_data))
                    self.curve_sensor.setData(x_values, [round(v, 1) for v in self.sensor_data])
                    self.curve_temp.setData(x_values, [round(v, 1) for v in self.temp_data])
                    self._apply_time_axis_spacing(len(self.sensor_data))
                    self._apply_y_axis_spacing()
                except Exception:
                    pass
                try:
                    self.update_stats()
                except Exception:
                    pass

                try:
                    self._sync_oled_with_ui_values()
                except Exception:
                    pass

                self.data_received = True
                try:
                    raw_voltage = self.current_reading.get('raw')
                    if self.current_reading.get('k') is not None:
                        k_from_range = float(self.current_reading.get('k'))
                    elif raw_voltage is not None:
                        k_from_range = self.get_k_from_range(raw_voltage)
                    else:
                        k_from_range = 1.0
                    print(f"[CSV GUARDADO] Sensor={sensor:.1f}µS, Temp={temp:.1f}°C, K={k_from_range:.6f}, Voltaje={raw_voltage}")
                    self.logger.write_row(sensor, temp, k_from_range, self.current_test_name)
                except Exception as e:
                    print(f"[CSV ERROR AL GUARDAR] {e}")

        except Exception:
            pass

    def download_csv(self):
        """Exporta un CSV consolidado con datos de ambos conductímetros."""
        try:
            sensor_1 = list(getattr(self, 'sensor_data', []))
            temp_1 = list(getattr(self, 'temp_data', []))
            sensor_2 = list(getattr(self, 'sensor_data_2', []))
            temp_2 = list(getattr(self, 'temp_data_2', []))

            total_samples = max(len(sensor_1), len(temp_1), len(sensor_2), len(temp_2))
            if total_samples == 0:
                QMessageBox.warning(self, "Error", "No hay datos para exportar en ningún conductímetro.")
                return

            dest_dir = QFileDialog.getExistingDirectory(
                self, 
                "Seleccionar carpeta para guardar CSV",
                os.path.expanduser("~")
            )
            if not dest_dir:
                return

            filename = f"datos_consolidado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            dest = os.path.join(dest_dir, filename)
            try:
                time_axis = self._build_time_axis(total_samples)

                with open(dest, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "Muestra",
                        "Tiempo_s",
                        "Conductividad_Dev1_uS",
                        "Temperatura_Dev1_C",
                        "Conductividad_Dev2_uS",
                        "Temperatura_Dev2_C",
                    ])

                    for i in range(total_samples):
                        writer.writerow([
                            i + 1,
                            time_axis[i] if i < len(time_axis) else "",
                            f"{sensor_1[i]:.2f}" if i < len(sensor_1) else "",
                            f"{temp_1[i]:.2f}" if i < len(temp_1) else "",
                            f"{sensor_2[i]:.2f}" if i < len(sensor_2) else "",
                            f"{temp_2[i]:.2f}" if i < len(temp_2) else "",
                        ])
                
                info_msg = (
                    f"✅ CSV consolidado guardado en:\n{dest}\n\n"
                    f"Muestras Dev 1: {len(sensor_1)}\n"
                    f"Muestras Dev 2: {len(sensor_2)}\n"
                    f"Total filas exportadas: {total_samples}\n"
                )
                
                QMessageBox.information(self, "Descarga Exitosa", info_msg)
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"No se pudo guardar el archivo:\n{e}")
                return

        except Exception:
            pass


    def plot_csv_data(self):
        """Carga un CSV y abre una o dos ventanas según los conductímetros detectados."""
        try:
            path, _ = QFileDialog.getOpenFileName(self, "Seleccionar CSV", "", "CSV Files (*.csv)")
            if not path:
                return

            csv_data_dev1 = {'sensor': [], 'temp': [], 'timestamps': []}
            csv_data_dev2 = {'sensor': [], 'temp': [], 'timestamps': []}

            def _to_float(value):
                try:
                    text = str(value).strip().replace(',', '.')
                    if not text:
                        return None
                    return float(text)
                except Exception:
                    return None

            def _pick_index(normalized_headers, candidates):
                for c in candidates:
                    if c in normalized_headers:
                        return normalized_headers[c]
                return None

            with open(path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                normalized_headers = {}

                if headers:
                    for idx, h in enumerate(headers):
                        normalized_headers[str(h).strip().lower()] = idx

                idx_dev1_sensor = _pick_index(
                    normalized_headers,
                    ['conductividad_dev1_us', 'sensor_dev1', 'sensor', 'conductividad', 'ec_u_s', 'ec_us']
                )
                idx_dev1_temp = _pick_index(
                    normalized_headers,
                    ['temperatura_dev1_c', 'temp_dev1', 'temperatura', 'temp', 'temperature']
                )
                idx_dev2_sensor = _pick_index(
                    normalized_headers,
                    ['conductividad_dev2_us', 'sensor_dev2', 'conductividad_2', 'ec_dev2_us']
                )
                idx_dev2_temp = _pick_index(
                    normalized_headers,
                    ['temperatura_dev2_c', 'temp_dev2', 'temperatura_2', 'temperature_dev2']
                )

                # Compatibilidad con formato legacy: Fecha, Hora, Sensor, Temperatura, ...
                if idx_dev1_sensor is None:
                    idx_dev1_sensor = 2
                if idx_dev1_temp is None:
                    idx_dev1_temp = 3

                for i, row in enumerate(reader):
                    try:
                        sensor_1 = _to_float(row[idx_dev1_sensor]) if idx_dev1_sensor is not None and len(row) > idx_dev1_sensor else None
                        temp_1 = _to_float(row[idx_dev1_temp]) if idx_dev1_temp is not None and len(row) > idx_dev1_temp else None

                        if sensor_1 is not None and temp_1 is not None:
                            csv_data_dev1['sensor'].append(sensor_1)
                            csv_data_dev1['temp'].append(temp_1)
                            csv_data_dev1['timestamps'].append(i)

                        sensor_2 = _to_float(row[idx_dev2_sensor]) if idx_dev2_sensor is not None and len(row) > idx_dev2_sensor else None
                        temp_2 = _to_float(row[idx_dev2_temp]) if idx_dev2_temp is not None and len(row) > idx_dev2_temp else None

                        if sensor_2 is not None and temp_2 is not None:
                            csv_data_dev2['sensor'].append(sensor_2)
                            csv_data_dev2['temp'].append(temp_2)
                            csv_data_dev2['timestamps'].append(i)
                    except Exception:
                        continue

            windows_opened = 0
            self.csv_plot_windows = []

            if csv_data_dev1['sensor'] and csv_data_dev1['temp']:
                win_1 = CSVPlotWindow(csv_data_dev1, path, "Dispositivo 1")
                win_1.show()
                self.csv_plot_windows.append(win_1)
                windows_opened += 1

            if csv_data_dev2['sensor'] and csv_data_dev2['temp']:
                win_2 = CSVPlotWindow(csv_data_dev2, path, "Dispositivo 2")
                win_2.show()
                self.csv_plot_windows.append(win_2)
                windows_opened += 1

            if windows_opened == 0:
                QMessageBox.warning(self, "Error", "El archivo CSV no contiene datos válidos.")
        except Exception as e:
            print("Error en plot_csv_data:", e)
            QMessageBox.critical(self, "Error", f"Error al abrir el CSV: {e}")

    def check_serial_health(self):
        """Verifica salud de conexión serial con ESP32.
        
        Revisa tiempo desde último dato recibido, actualizando LED indicador
        y estado en pantalla (verde si reciente, rojo si antiguo).
        """
        try:
            if not self.connected:
                self.led.setStyleSheet("background: #b30000; border-radius: 8px;")
                return
            age = time.time() - getattr(self, 'last_data_time', 0)
            if age > 10:
                self.led.setStyleSheet("background: #b30000; border-radius: 8px;")
                self.status_label.setText("Conectado - Sin datos recientes")
            else:
                self.led.setStyleSheet("background: #00a000; border-radius: 8px;")
        except Exception as e:
            print("Error en check_serial_health:", e)


    def show_device2_connection_dialog(self):
        """Muestra diálogo para conectar el dispositivo 2."""
        ports = serial.tools.list_ports.comports()
        port_list = [f"{p.device} - {p.description}" for p in ports]
        
        if not port_list:
            QMessageBox.warning(self, "Error", "No hay puertos disponibles.")
            return
        
        port, ok = QInputDialog.getItem(
            self, 
            "Seleccionar Puerto para Dispositivo 2",
            "Puerto COM:",
            port_list,
            0,
            False
        )
        
        if ok and port:
            selected_port = port.split(" - ")[0]
            if self.connect_to_esp32_2(selected_port):
                self.connect_dev2_btn.setEnabled(False)
                self.disconnect_dev2_btn.setEnabled(True)
                QMessageBox.information(self, "Éxito", f"Conectado a {selected_port}")
            else:
                QMessageBox.critical(self, "Error", f"No se pudo conectar a {selected_port}")

# ═══════════════════════════════════════════════════════════════
#                 MÉTODOS DEL SEGUNDO DISPOSITIVO
# ═══════════════════════════════════════════════════════════════

    def process_data_2(self, line):
        """Recibe línea serial del dispositivo 2 y la encola."""
        line = line.strip()
        if not line:
            return
        with self.queue_lock_2:
            self.line_queue_2.append(line)

    def flush_queue_2(self):
        """Procesa líneas encoladas del dispositivo 2."""
        try:
            lines = []
            with self.queue_lock_2:
                while self.line_queue_2:
                    lines.append(self.line_queue_2.popleft())
            for line in lines:
                try:
                    self.handle_serial_line_2(line)
                except Exception:
                    pass
        except Exception:
            pass

    def handle_serial_line_2(self, line: str):
        """Procesa una línea serial del dispositivo 2."""
        line = (line or "").strip()
        if not line:
            return

        _boot_prefixes = (
            'ets ', 'rst:', 'configsip:', 'clk_drv:', 'mode:', 'load:',
            'ho ', 'entry ', 'E (', 'W (', 'I (', 'D (',
            '[SD ', '[DEBUG', '[ERROR', '[INFO', '[WARN',
        )
        if any(line.startswith(p) for p in _boot_prefixes):
            return

        sensor = None
        temp = None
        k = None
        raw = None

        try:
            ts_match = re.match(
                r'^(?:PROM\d+\s+)?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(NaN|[-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s*$',
                line,
                flags=re.IGNORECASE,
            )
            if ts_match:
                temp_token = ts_match.group(1)
                ec_token = ts_match.group(2)
                sensor = float(ec_token)
                temp = None if str(temp_token).lower() == 'nan' else float(temp_token)
        except Exception:
            pass

        try:
            if sensor is None and line.startswith('{'):
                obj = json.loads(line)
                if obj.get('type') == 'identify':
                    try:
                        dev_type = obj.get('device_type')
                        dev_id = obj.get('device_id')
                        fw = obj.get('firmware')
                        cfg_name = obj.get('cfg')
                        proto = obj.get('protocol')
                        self.device_info_2 = {'device_type': dev_type, 'device_id': dev_id, 'firmware': fw, 'cfg': cfg_name, 'protocol': proto}
                        print(f"[IDENTIFY dev2] {dev_type} {dev_id} fw={fw}")
                        try:
                            self.status_label.setText(f"Conectado: Dev 1 ✓ | Dev 2 ✓ ({dev_type})")
                        except Exception:
                            pass
                    except Exception:
                        pass
                elif obj.get('type') == 'reading':
                    sensor = float(obj.get('sensor')) if obj.get('sensor') is not None else None
                    temp = float(obj.get('temp')) if obj.get('temp') is not None else None
                    k = float(obj.get('k')) if obj.get('k') is not None else None
                    raw = float(obj.get('raw_V')) if obj.get('raw_V') is not None else None
                    raw_ch = int(obj.get('raw_ch')) if obj.get('raw_ch') is not None else None
        except Exception:
            sensor = temp = k = None

        if sensor is None or temp is None:
            try:
                parts = [p.strip() for p in re.split('[,;]', line) if p.strip()]
                for p in parts:
                    if ':' in p:
                        key, val = p.split(':', 1)
                        key = key.strip().lower()
                        val = val.strip()
                        if ('sensor' in key or key == 's') and sensor is None:
                            try:
                                sensor = float(val)
                            except Exception:
                                pass
                        if ('temp' in key or 'temperature' in key or key == 't') and temp is None:
                            try:
                                temp = float(val)
                            except Exception:
                                pass
                        if key.startswith('k') and k is None:
                            try:
                                k = float(val)
                            except Exception:
                                pass

                if sensor is None:
                    csv_parts = [p.strip() for p in line.split(',') if p.strip()]
                    if len(csv_parts) >= 2:
                        try:
                            int(csv_parts[0])
                            sensor = float(csv_parts[1])
                            if len(csv_parts) >= 3 and temp is None:
                                temp = float(csv_parts[2])
                        except Exception:
                            pass
                    if sensor is None:
                        nums = re.findall(r'[-+]?[0-9]*\.?[0-9]+', line)
                        if nums:
                            # Formato esperado del conductimetro 2: "idx,valor_uS".
                            if len(nums) >= 2 and (',' in line or ';' in line):
                                sensor = float(nums[1])
                                if len(nums) >= 3 and temp is None:
                                    temp = float(nums[2])
                            else:
                                sensor = float(nums[0])
            except Exception:
                pass

        if sensor is not None:
            prev_temp = self.current_reading_2.get('temp')
            temp_for_ui = temp if temp is not None else (prev_temp if prev_temp is not None else 25.0)
            self.current_reading_2 = {'sensor': sensor, 'temp': temp_for_ui, 'k': k, 'raw': raw, 'raw_ch': raw_ch}

            try:
                if self.reading_active_2:
                    self.value_sensor_label_2.setText(f"{sensor:.1f} µS")
                    self.value_temp_label_2.setText(f"{temp_for_ui:.1f} °C")
                    if raw is not None:
                        if raw_ch is None:
                            self.raw_label_2.setText(f"{raw:.6f} V")
                        else:
                            self.raw_label_2.setText(f"{raw:.6f} V (ch{raw_ch})")
                    if k is not None:
                        self.k_label_2.setText(f"K: {k:.1f}")
                    else:
                        self.k_label_2.setText("K: --")
                elif not self.reading_active_2:
                    self.value_sensor_label_2.setText("-- µS")
                    self.value_temp_label_2.setText("-- °C")
                    self.raw_label_2.setText("-- V")
                    self.k_label_2.setText("K: --")
            except Exception:
                pass
            
            if self._is_dual_sync_active() and temp_for_ui is not None:
                self.pending_sample_2 = (sensor, temp_for_ui, k, raw)
                self.last_synced_sample_2 = self.pending_sample_2
            elif self.reading_active_2:
                self.sensor_data_2.append(sensor)
                self.temp_data_2.append(temp_for_ui)
                self.data_received_2 = True

                try:
                    x_values = self._build_time_axis(len(self.sensor_data_2))
                    self.curve_sensor_2.setData(x_values, [round(v, 1) for v in self.sensor_data_2])
                    self.curve_temp_2.setData(x_values, [round(v, 1) for v in self.temp_data_2])
                    self._apply_time_axis_spacing_2(len(self.sensor_data_2))
                    self._apply_y_axis_spacing_2()
                except Exception:
                    pass

                try:
                    self.update_stats_2()
                except Exception:
                    pass

    def open_calibration_update_dialog(self):
        """Abre el diálogo para actualizar calibración con conductímetro comercial."""
        if not self.connected:
            QMessageBox.warning(self, "Error", "Debe conectar el ESP32 primero.")
            return
        
        update_dialog = CalibrationUpdateDialog(self)
        update_dialog.set_serial(self.serial_reader, self.connected, self)
        update_dialog.exec_()

    def start_reading_2(self):
        """Inicia lectura del dispositivo 2."""
        if not self.connected_2:
            QMessageBox.warning(self, "Error", "Dispositivo 2 no conectado.")
            return
        
        name, ok = QInputDialog.getText(self, "Dispositivo 2 - Nombre de Solución", "Ingrese el nombre:")
        if not ok or not name.strip():
            return

        self.current_test_name_2 = name.strip()
        self.test_start_time_2 = datetime.now()
        self.test_name_label_2.setText(f"Prueba: {name.strip()}")
        self.logger.set_test_name(f"{name.strip()}_2")

        self.sensor_data_2.clear()
        self.temp_data_2.clear()
        self.data_received_2 = False
        self.curve_sensor_2.setData([])
        self.curve_temp_2.setData([])

        self.value_sensor_label_2.setText("-- µS")
        self.value_temp_label_2.setText("-- °C")
        self.k_label_2.setText("K: --")
        self.stats_label_2.setText("Estadísticas (Dev 2): Aguardando datos...")

        self.reading_active_2 = True
        self.status_label.setText(f"Dev 2: Lectura activa - {name.strip()}")

        try:
            if self.connected_2 and self.serial_reader_2:
                self.serial_reader_2.send_command("LOG_START")
        except Exception:
            pass

    def stop_reading_2(self):
        """Detiene lectura del dispositivo 2."""
        self.finalize_test_2()

    def finalize_test_2(self):
        """Finaliza prueba en dispositivo 2."""
        if self.reading_active_2:
            self.reading_active_2 = False

            try:
                if self.connected_2 and self.serial_reader_2:
                    self.serial_reader_2.send_command("LOG_STOP")
            except Exception:
                pass
            
            self.sensor_data_2.clear()
            self.temp_data_2.clear()
            self.value_sensor_label_2.setText("-- µS")
            self.value_temp_label_2.setText("-- °C")
            self.raw_label_2.setText("-- V")
            self.k_label_2.setText("K: --")
            self.curve_sensor_2.setData([0])
            self.curve_temp_2.setData([0])
            self.stats_label_2.setText("Estadísticas (Dev 2): Aguardando datos...")
            self.test_name_label_2.setText("Prueba: ---")
            
            self.current_test_name_2 = ""
            self.test_start_time_2 = None
            self.data_received_2 = False

    def update_stats_2(self):
        """Actualiza estadísticas del dispositivo 2."""
        if not self.sensor_data_2 or not self.temp_data_2:
            self.stats_label_2.setText("Estadísticas (Dev 2): Aguardando datos...")
            return
        
        try:
            sensor_min = min(self.sensor_data_2)
            sensor_max = max(self.sensor_data_2)
            sensor_avg = sum(self.sensor_data_2) / len(self.sensor_data_2)
        
            temp_min = min(self.temp_data_2)
            temp_max = max(self.temp_data_2)
            temp_avg = sum(self.temp_data_2) / len(self.temp_data_2)
        
            num_samples = len(self.sensor_data_2)
        
            stats_text = (
                f"<b>Dev 2</b> - Cond: Min={sensor_min:.1f} Máx={sensor_max:.1f} Prom={sensor_avg:.1f} uS | "
                f"Temp: Min={temp_min:.1f} Máx={temp_max:.1f} Prom={temp_avg:.1f} °C | "
                f"Muestras: {num_samples}"
            )
            self.stats_label_2.setText(stats_text)
        except Exception:
            pass

    def _apply_time_axis_spacing_2(self, samples_count):
        """Espaciado del eje X para dispositivo 2."""
        try:
            if samples_count <= 1:
                return
            interval = float(self.interval_spin.value())
            if interval <= 0:
                interval = 1.0
            total_time = (samples_count - 1) * interval
            major = max(1.0, round(total_time / 8.0, 1))
            minor = max(0.2, round(major / 5.0, 1))
            self.plot_sensor_2.getAxis('bottom').setTickSpacing(major=major, minor=minor)
            self.plot_temp_2.getAxis('bottom').setTickSpacing(major=major, minor=minor)
        except Exception:
            pass

    def _apply_y_axis_spacing_2(self):
        """Espaciado del eje Y para dispositivo 2."""
        try:
            if self.sensor_data_2:
                sensor_min = min(self.sensor_data_2)
                sensor_max = max(self.sensor_data_2)
                sensor_span = max(sensor_max - sensor_min, 1.0)
                sensor_major = max(1.0, round(sensor_span / 6.0, 1))
                sensor_minor = max(0.2, round(sensor_major / 5.0, 1))
                self.plot_sensor_2.getAxis('left').setTickSpacing(major=sensor_major, minor=sensor_minor)

            if self.temp_data_2:
                temp_min = min(self.temp_data_2)
                temp_max = max(self.temp_data_2)
                temp_span = max(temp_max - temp_min, 0.5)
                temp_major = max(0.2, round(temp_span / 6.0, 1))
                temp_minor = max(0.1, round(temp_major / 2.0, 1))
                self.plot_temp_2.getAxis('left').setTickSpacing(major=temp_major, minor=temp_minor)
        except Exception:
            pass

    def connect_to_esp32_2(self, port: str):
        """Conecta al segundo ESP32."""
        try:
            if not port:
                return False
            self.serial_reader_2 = SerialReader(port, 115200, callback=self.process_data_2)
            if self.serial_reader_2.start():
                self.connected_2 = True
                self.status_label.setText(f"Conectado: Dev 1 ✓ | Dev 2 ✓ ({port})")
                # La lectura no debe iniciar automáticamente al conectar.
                return True
            else:
                self.serial_reader_2 = None
                return False
        except Exception as e:
            self.serial_reader_2 = None
            return False

    def disconnect_from_esp32_2(self):
        """Desconecta del segundo ESP32."""
        self.connected_2 = False
        self.reading_active_2 = False
        
        if self.serial_reader_2:
            try:
                self.serial_reader_2.stop()
            except Exception:
                pass
            self.serial_reader_2 = None

        try:
            self.pending_sample_1 = None
            self.pending_sample_2 = None
            self.sync_timer.stop()
        except Exception:
            pass
        
        self.update_ui_disconnected_2()

    def update_ui_disconnected_2(self):
        """Actualiza UI cuando dispositivo 2 se desconecta."""
        self.connect_dev2_btn.setEnabled(True)
        self.disconnect_dev2_btn.setEnabled(False)
        self.value_sensor_label_2.setText("-- µS")
        self.value_temp_label_2.setText("-- °C")
        self.raw_label_2.setText("-- V")
        self.k_label_2.setText("K: --")
        self.curve_sensor_2.setData([0])
        self.curve_temp_2.setData([0])
        self.test_name_label_2.setText("Prueba: ---")
        self.stats_label_2.setText("Estadísticas (Dev 2): Dispositivo desconectado")

# ═══════════════════════════════════════════════════════════════
#                     PUNTO DE ENTRADA
# ═══════════════════════════════════════════════════════════════
def main():
    """Punto de entrada de la aplicación.
    
    Inicializa la ventana principal ESP32App, conecta con el puerto serial,
    y configura siempre el modo de calibración en laboratorio.
    """
    app = QApplication(sys.argv)
    
    window = ESP32App()
    
    # Inicializar modo de calibración en laboratorio
    window.cal_dialog = None

    # Mostrar ventana principal
    window.show()
    app.processEvents()
    
    # Conectar con puerto serial
    connected_ok = window.show_connection_dialog()
    if not connected_ok:
        sys.exit(0)

    # Configurar siempre en modo laboratorio
    window.set_calibration_mode_ui("laboratory", send_to_device=True)
    
    # Mostrar ventana y ejecutar aplicación
    window.raise_()
    window.activateWindow()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
