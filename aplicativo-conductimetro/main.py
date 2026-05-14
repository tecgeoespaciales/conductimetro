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

from serial_reader import SerialReader
from data_logger import DataLogger
from lab_security import verify_password, LAB_CALIBRATION_CONFIG

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
            closest_ec = min(k_table.keys(), key=lambda x: abs(x - ec_poly))
            k_factor = k_table[closest_ec]
            ec_calibrated = ec_poly * k_factor
        else:
            ec_calibrated = ec_poly

        return round(ec_calibrated, 2)
    except Exception as e:
        print(f"Error calculating EC: {e}")
        return 0.0

def load_k_table_from_cfg():
    try:
        cfg_path = "calibration_ranges.cfg"
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
    
    def __init__(self, csv_data, filename):
        super().__init__()
        self.csv_data = csv_data
        self.filename = filename
        self.setup_ui()

    def setup_ui(self):
        """
        Construye la interfaz visual con gráficas interactivas y tabla de estadísticas.
        
        Configura dos gráficas de PyQtGraph (conductividad y temperatura) con grid,
        etiquetas de ejes y una tabla HTML con estadísticas calculadas.
        """
        self.setWindowTitle(f"Gráfica CSV - {os.path.basename(self.filename)}")
        self.resize(900, 700)

        layout = QVBoxLayout()

        plot_sensor = pg.PlotWidget(title="Conductividad (uS)")
        plot_sensor.showGrid(x=True, y=True)
        plot_sensor.setLabel('left', 'Conductividad', 'uS')
        plot_sensor.setLabel('bottom', 'Muestras')
        plot_sensor.getAxis('left').enableAutoSIPrefix(False)
        try:
            plot_sensor.getAxis('left').setTickSpacing(major=1.0, minor=0.1)
        except Exception:
            pass

        plot_temp = pg.PlotWidget(title="Temperatura (°C)")
        plot_temp.showGrid(x=True, y=True)
        plot_temp.setLabel('left', 'Temperatura', '°C')
        plot_temp.setLabel('bottom', 'Muestras')
        plot_temp.getAxis('left').enableAutoSIPrefix(False)
        try:
            plot_temp.getAxis('left').setTickSpacing(major=1.0, minor=0.1)
        except Exception:
            pass

        timestamps = list(range(len(self.csv_data['sensor'])))
        plot_sensor.plot(timestamps, [round(v, 1) for v in self.csv_data['sensor']], pen='r', symbol='o', symbolSize=3)
        plot_temp.plot(timestamps, [round(v, 1) for v in self.csv_data['temp']], pen='b', symbol='o', symbolSize=3)

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
ESTADÍSTICAS - {os.path.basename(self.filename)}
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
            <td>{min(sensor_data):.1f} uS</td>
        <td><b>Mínimo:</b></td>
            <td>{min(temp_data):.1f} °C</td>
        <td colspan="2"><b>Fecha de muestras:</b></td>
        <td colspan="2">{file_date}</td>
    </tr>
    <tr>
        <td><b>Máximo:</b></td>
            <td>{max(sensor_data):.1f} uS</td>
        <td><b>Máximo:</b></td>
            <td>{max(temp_data):.1f} °C</td>
        <td colspan="2"><b>Duración de la toma:</b></td>
        <td colspan="2">{duration_str}</td>
    </tr>
    <tr>
        <td><b>Promedio:</b></td>
            <td>{sum(sensor_data)/len(sensor_data):.1f} uS</td>
        <td><b>Promedio:</b></td>
            <td>{sum(temp_data)/len(temp_data):.1f} °C</td>
        <td colspan="2"><b>Total de puntos:</b></td>
        <td colspan="2">{len(sensor_data)} muestras</td>
    </tr>
</table>
</body>
</html>
"""
        return stats


# ═══════════════════════════════════════════════════════════════
#           DIÁLOGO DE SELECCIÓN DE MODO DE CALIBRACIÓN
# ═══════════════════════════════════════════════════════════════
class CalibrationInitialDialog(QDialog):
    MODE_KNOWN_VALUES = "known_values"
    MODE_LABORATORY = "laboratory"  # Se activa cuando usuario selecciona "Continuar sin Calibración"

    def __init__(self):
        super().__init__()
        self.selected_mode = None
        self.setWindowTitle("Calibración Inicial")
        self.setFixedSize(600, 500)
        self.setStyleSheet("""
            QDialog {
                background: #f5f5f5;
            }
            QPushButton {
                font-size: 13px;
                padding: 12px;
                border-radius: 6px;
                border: 2px solid #ddd;
                background: white;
            }
            QPushButton:hover {
                background: #f0f0f0;
                border: 2px solid #0066cc;
            }
            QPushButton:pressed {
                background: #e0e0e0;
            }
        """)
        self.init_ui()

    def init_ui(self):
        """
        Construye la interfaz con dos opciones principales de calibración.
        
        Crea dos panels con descripciones y botones para seleccionar entre:
        1. Calibración de Escala (usando soluciones de valores conocidos)
        2. Continuar sin Calibración (usa calibración de laboratorio preexistente)
        """
        main_layout = QVBoxLayout()
        main_layout.setSpacing(30)
        main_layout.setContentsMargins(30, 30, 30, 30)

        title = QLabel("Seleccione Modo de Calibración")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #000000; padding: 18px;")
        main_layout.addWidget(title)

        # Opción 1: Calibración de Valores Conocidos
        group1 = QGroupBox("Calibración de Escala")
        group1.setFont(QFont("Arial", 12, QFont.Bold))
        group1_layout = QVBoxLayout()
        group1_layout.setSpacing(12)

        desc1 = QLabel(
            "Cree una escala personalizada con diferentes soluciones de calibración conocida")
        desc1.setWordWrap(True)
        desc1.setStyleSheet("color: #333; padding: 10px; background: #c8e6c9; border-radius: 4px;")
        group1_layout.addWidget(desc1)

        self.btn_known = QPushButton("Valores Conocidos")
        self.btn_known.setMinimumHeight(50)
        self.btn_known.setStyleSheet("""
            QPushButton {
                background: #4caf50;
                border: 2px solid #388e3c;
                color: white;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background: #45a049;
                border: 2px solid #2e7d32;
            }
        """)
        self.btn_known.clicked.connect(self.select_known_values)
        group1_layout.addWidget(self.btn_known)

        group1.setLayout(group1_layout)
        main_layout.addWidget(group1)

        # Opción 2: Continuar sin Calibración (en realidad es Laboratorio)
        group2 = QGroupBox("Usar Calibración de Laboratorio")
        group2.setFont(QFont("Arial", 12, QFont.Bold))
        group2_layout = QVBoxLayout()
        group2_layout.setSpacing(12)

        desc2 = QLabel(
            "Utilice la calibración de laboratorio predefinida (0-10 mS)")
        desc2.setWordWrap(True)
        desc2.setStyleSheet("color: #333; padding: 10px; background: #b3e5fc; border-radius: 4px;")
        group2_layout.addWidget(desc2)

        self.btn_laboratory = QPushButton("Continuar sin Calibración")
        self.btn_laboratory.setMinimumHeight(50)
        self.btn_laboratory.setStyleSheet("""
            QPushButton {
                background: #0288d1;
                border: 2px solid #0277bd;
                color: white;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background: #0277bd;
                border: 2px solid #01579b;
            }
        """)
        self.btn_laboratory.clicked.connect(self.select_laboratory)
        group2_layout.addWidget(self.btn_laboratory)

        group2.setLayout(group2_layout)
        main_layout.addWidget(group2)

        main_layout.addStretch()
        self.setLayout(main_layout)

    def select_known_values(self):
        """
        Selecciona el modo de calibración con valores conocidos y cierra el diálogo.
        """
        self.selected_mode = self.MODE_KNOWN_VALUES
        self.accept()

    def select_laboratory(self):
        """
        Selecciona el modo de calibración de laboratorio (mostrado como \"Continuar sin Calibración\").
        Usa calibración preexistente en la tabla K precargada del sistema.
        """
        self.selected_mode = self.MODE_LABORATORY
        self.accept()


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
    
    def __init__(self, app, num_points=20):
        super().__init__()
        self.app = app
        self.serial_reader = None
        self.connected = False
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
                   "Botón Medir", "Voltaje @ 25°C (V)", "K", "Estimado (µS)"]
        self.table.setHorizontalHeaderLabels(headers)
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
    
    def measure_sample(self, row):
        """Inicia medición de una fila específica"""
        if not self.connected or not self.serial_reader:
            QMessageBox.warning(self, "Error", "No conectado al ESP32.")
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
        """Tick del timer de muestreo (100ms)"""
        try:
            raw_val = None
            temp_val = None
            if hasattr(self.app, 'current_reading') and self.app.current_reading:
                cr = self.app.current_reading
                if cr.get('raw') is not None:
                    raw_val = cr.get('raw')
                elif cr.get('signal') is not None:
                    raw_val = cr.get('signal')
                elif cr.get('sensor') is not None:
                    raw_val = cr.get('sensor')
                temp_val = cr.get('temp')
        except Exception:
            pass
        
        # FASE 1: ESTABILIZACIÓN
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
        
        # FASE 2: CAPTURA DE MUESTRAS
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
        """Finaliza medición y calcula K"""
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
        
        measured_value = self._find_mode_with_tolerance(samples)
        if measured_value is None or measured_value == 0:
            QMessageBox.warning(self, "Error", "El promedio de la señal cruda es 0. Revise la conexión.")
            return
        
        # Obtener temperatura capturada
        temp_read = (sum(temps) / len(temps)) if temps else 25.0
        
        if temp_read < -40 or temp_read > 125:
            temp_read = 25.0
        
        # ✓ COMPENSACIÓN DE TEMPERATURA EN CALIBRACIÓN DE LABORATORIO
        # Normalizar voltaje a 25°C (referencia de la tabla de calibración)
        # porque la tabla [LABORATORY_CALIBRATION] está referida a 25°C
        voltage_at_25c = normalize_voltage_to_reference_temp(
            measured_value, temp_read, ref_temp=25.0, coef=COEF_TEMP
        )
        
        # Aplicar polinomio DFRobot al voltaje NORMALIZADO a 25°C
        ec_measured = 133.42 * voltage_at_25c**3 - 255.86 * voltage_at_25c**2 + 857.39 * voltage_at_25c
        
        # K se calcula usando voltaje normalizado a 25°C
        k_value = known_cond / ec_measured if ec_measured > 0 else 0
        # El valor estimado es K aplicado al EC polinomio (debería ≈ known_cond)
        estimated_at_temp = k_value * ec_measured
        
        self.calibration_data[row] = {
            'known_cond': known_cond,
            'measured': voltage_at_25c,  # Guardar voltaje normalizado a 25°C
            'k': k_value,
            'estimated_at_temp': estimated_at_temp,
            'temp': temp_read
        }
        
        # Guardar en KNOWN_K_TABLE
        try:
            cfg_path = "calibration_ranges.cfg"
            cfg = configparser.ConfigParser()
            if os.path.exists(cfg_path):
                try:
                    cfg.read(cfg_path, encoding='utf-8')
                except Exception:
                    cfg.read(cfg_path)
            
            if 'CALIBRATION_K_TABLE' not in cfg:
                cfg.add_section('CALIBRATION_K_TABLE')
            
            # Guardar voltaje normalizado a 25°C como clave
            voltage_key = f"{voltage_at_25c:.6f}"
            cfg.set('CALIBRATION_K_TABLE', voltage_key, str(round(k_value, 6)))
            
            with open(cfg_path, 'w', encoding='utf-8') as f:
                cfg.write(f)
        except Exception:
            pass
        
        # Actualizar labels (mostrar voltaje normalizado a 25°C)
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
            cfg = configparser.ConfigParser()
            if os.path.exists(archivo_cfg):
                cfg.read(archivo_cfg, encoding='utf-8')
            
            if 'LABORATORY_CALIBRATION' not in cfg:
                cfg.add_section('LABORATORY_CALIBRATION')
            
            # Guardar datos de calibración y construir tabla de voltajes-K
            voltage_k_table = {}
            for row, data in sorted(self.calibration_data.items()):
                known_cond = data['known_cond']
                k_value = data['k']
                measured = data.get('measured')
                if measured is None:
                    cfg.set('LABORATORY_CALIBRATION', str(int(known_cond)), str(round(k_value, 6)))
                else:
                    cfg.set('LABORATORY_CALIBRATION', str(int(known_cond)), 
                           f"{round(k_value, 6)},{measured:.6f}")
                    # Usar voltaje medido como clave para tabla de rangos
                    voltage_k_table[float(measured)] = k_value
            
            with open(archivo_cfg, 'w', encoding='utf-8') as f:
                cfg.write(f)
            
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
        """Envía la tabla K calibrada al ESP32"""
        if not self.serial_reader or not self.connected:
            print("⚠️ No conectado al ESP32, no se envió calibración")
            return False
        
        try:
            # Construir tabla K desde calibration_data
            k_table = {}
            for row, data in self.calibration_data.items():
                known_cond = data['known_cond']
                k_value = data['k']
                k_table[known_cond] = k_value
            
            if not k_table:
                print("✗ No hay datos de calibración para enviar")
                return False
            
            # Enviar comando UPDATE_K_TABLE al ESP32
            k_json = json.dumps(k_table)
            self.serial_reader.send_command(f"UPDATE_K_TABLE:{k_json}")
            print(f"✓ Tabla K enviada al ESP32: {len(k_table)} puntos")
            
            # Enviar comando para usar calibración de laboratorio
            self.serial_reader.send_command("USE_LABORATORY_CALIBRATION")
            print("✓ USE_LABORATORY_CALIBRATION enviado al ESP32")
            
            return True
        except Exception as e:
            print(f"✗ Error al enviar calibración al ESP32: {e}")
            return False
    
    def finalize_calibration(self):
        """Finaliza la calibración y cierra el diálogo"""
        if not self.calibration_data:
            QMessageBox.warning(self, "Advertencia", 
                              "Debe medir al menos un punto antes de finalizar.")
            return
        
        self.save_laboratory_calibration()
        
        # Enviar tabla K al ESP32
        self._send_calibration_to_esp32()
        
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
#         DIÁLOGO DE CALIBRACIÓN CON VALORES CONOCIDOS
# ═══════════════════════════════════════════════════════════════
class KnownCalibrationDialog(QDialog):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.serial_reader = None
        self.connected = False
        self.setWindowTitle("Calibración - Valores Conocidos")
        self.setFixedSize(600, 750)
        self.calibration_data = {}
        self.capture_buttons = {}
        self._suppress_item_changes = False
        self._capture_phase = None  # 'stabilizing', 'capturing', None
        self._stabilize_remaining = 0
        self._samples_to_capture = 0
        self.setup_ui()

    def setup_ui(self):
        """
        Construye la interfaz de calibración con valores conocidos.
        
        Crea seccionespara configurar parámetros de captura, tabla de soluciones
        de calibración y botones de control.
        """
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        title = QLabel("Calibración con Valores Conocidos")
        title.setFont(QFont("Arial", 13, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        info = QLabel(
            "1. Configure parámetros de captura, la cantidad de muestras y tiempo de estabilización\n"
            "2. Ingrese el valor conocido de conductividad (µS) para cada solución\n"
            "3. Presione 'Medir' para capturar datos de esa solución, incluyendo la temperatura\n"
        )
        info.setWordWrap(True)
        info.setStyleSheet("background: #f9f9f9; padding: 10px; border-radius: 4px; border-left: 4px solid #88a02c;")
        layout.addWidget(info)

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
        layout.addWidget(config_group)

        input_group = QGroupBox("Agregar Soluciones")
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Conductividad conocida (µS):"))
        self.solution_input = QLineEdit()
        self.solution_input.setPlaceholderText("Ej: 100, 500, 1000...")
        self.solution_input.setValidator(QDoubleValidator(0, 5000, 2))
        input_layout.addWidget(self.solution_input)

        self.add_btn = QPushButton("Agregar")
        self.add_btn.setMinimumHeight(35)
        self.add_btn.setStyleSheet("""
            QPushButton {
                background: #88a02c;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background: #a0b356; }
        """)
        self.add_btn.clicked.connect(self.add_solution)
        input_layout.addWidget(self.add_btn)
        input_group.setLayout(input_layout)
        layout.addWidget(input_group)

        table_group = QGroupBox("Soluciones a Medir")
        table_layout = QVBoxLayout()
        self.solutions_table = QTableWidget()
        self.solutions_table.setColumnCount(4)
        self.solutions_table.setHorizontalHeaderLabels(["Conductividad (µS)", "Voltaje (V)", "Temperatura (°C) [Auto]", "Acción"])
        self.solutions_table.verticalHeader().setVisible(False)
        self.solutions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.solutions_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.solutions_table.setMinimumHeight(250)
        self.solutions_table.setStyleSheet("background: #fafafa; border: 1px solid #ddd;")
        self.solutions_table.itemChanged.connect(self.on_solution_item_changed)
        table_layout.addWidget(QLabel("Soluciones registradas:"))
        table_layout.addWidget(self.solutions_table)
        table_group.setLayout(table_layout)
        layout.addWidget(table_group)

        btn_layout = QHBoxLayout()
        self.remove_btn = QPushButton("Eliminar Seleccionada")
        self.remove_btn.setStyleSheet("""
            QPushButton {
                background: #f44336;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background: #d32f2f; }
        """)
        self.remove_btn.clicked.connect(self.remove_solution)
        btn_layout.addWidget(self.remove_btn)
        
        self.skip_btn = QPushButton("Continuar sin agregar dato")
        self.skip_btn.setMinimumHeight(40)
        self.skip_btn.setStyleSheet("""
            QPushButton {
                background: #ff9800;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background: #f57c00; }
        """)
        self.skip_btn.setToolTip("Continuar usando la calibración existente sin agregar nuevos puntos")
        self.skip_btn.clicked.connect(self.skip_and_use_existing)
        btn_layout.addWidget(self.skip_btn)
        
        btn_layout.addStretch()
        
        self.finalize_btn = QPushButton("Finalizar y Generar Rangos")
        self.finalize_btn.setMinimumHeight(40)
        self.finalize_btn.setStyleSheet("""
            QPushButton {
                background: #4caf50;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background: #45a049; }
        """)
        self.finalize_btn.clicked.connect(self.finalize_calibration)
        btn_layout.addWidget(self.finalize_btn)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def skip_and_use_existing(self):
        """
        Usa calibración previa sin agregar nuevos puntos de medición.
        
        Carga la tabla K existente del archivo de configuración y procede
        directamente sin capturar nuevas muestras.
        """
        try:
            self.app.load_user_calibration()
            
            if not self.app.user_calibration and not self.app.known_k_ranges:
                QMessageBox.warning(
                    self, 
                    "Sin calibración previa",
                    "No existe una calibración previa guardada.\n"
                    "Debe agregar al menos un punto de calibración."
                )
                return
            
            self.app.use_user_calibration = True
            
            cal_count = len(self.app.user_calibration) if self.app.user_calibration else 0
            ranges_count = len(self.app.known_k_ranges) if self.app.known_k_ranges else 0
            
            self.status_label.setText(
                f"✅ Usando calibración existente ({cal_count} puntos K, {ranges_count} rangos)"
            )
            self.status_label.setStyleSheet(
                "padding: 8px; background: #fff3e0; border-radius: 3px; border-left: 4px solid #ff9800;"
            )
            
            # Calcular EC calibrada y enviar a ESP32
            self._calculate_and_send_show_ec()
            
            # Enviar comando para actualizar OLED a pantalla de datos
            self._send_oled_cal_done()
            
            self.accept()
            
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"No se pudo cargar la calibración existente:\n{e}"
            )

    def _send_all_calibration_data(self):  # NUEVO: Envía TODA la estructura de calibración al ESP32
        """Lee TODAS las secciones del cfg y las envía en una sola estructura"""
        import json
        import configparser
        import os
        
        try:
            cfg_path = "calibration_ranges.cfg"
            
            if not os.path.exists(cfg_path):
                print("Error: No existe calibration_ranges.cfg")
                return
            
            cfg = configparser.ConfigParser()
            try:
                cfg.read(cfg_path, encoding='utf-8')
            except Exception:
                cfg.read(cfg_path)
            
            # Empaquetar TODAS las secciones de calibración
            calibration_data = {}
            
            # 1. KNOWN_K_TABLE
            if 'KNOWN_K_TABLE' in cfg:
                calibration_data['known_k_table'] = {}
                for ec_str, k_str in cfg.items('KNOWN_K_TABLE'):
                    try:
                        calibration_data['known_k_table'][float(ec_str)] = float(k_str)
                    except:
                        pass
                print(f"Empaquetado KNOWN_K_TABLE: {len(calibration_data['known_k_table'])} puntos")
            
            # 2. KNOWN_K_RANGES
            if 'KNOWN_K_RANGES' in cfg:
                calibration_data['known_k_ranges'] = {}
                for range_str, k_str in cfg.items('KNOWN_K_RANGES'):
                    try:
                        parts = [p.strip() for p in range_str.split(',')]
                        if len(parts) == 2:
                            min_ec = float(parts[0])
                            max_ec = float(parts[1])
                            k_val = float(k_str)
                            calibration_data['known_k_ranges'][f"{min_ec},{max_ec}"] = k_val
                    except:
                        pass
                print(f"Empaquetado KNOWN_K_RANGES: {len(calibration_data['known_k_ranges'])} rangos")
            
            # 3. USER_CALIBRATION
            if 'USER_CALIBRATION' in cfg:
                calibration_data['user_calibration'] = {}
                for ec_str, cal_str in cfg.items('USER_CALIBRATION'):
                    try:
                        calibration_data['user_calibration'][float(ec_str)] = cal_str
                    except:
                        pass
                print(f"Empaquetado USER_CALIBRATION: {len(calibration_data['user_calibration'])} puntos calibrados")
            
            # 4. CALIBRATION_K_TABLE
            if 'CALIBRATION_K_TABLE' in cfg:
                calibration_data['calibration_k_table'] = {}
                for ec_str, k_str in cfg.items('CALIBRATION_K_TABLE'):
                    try:
                        calibration_data['calibration_k_table'][float(ec_str)] = float(k_str)
                    except:
                        pass
                print(f"Empaquetado CALIBRATION_K_TABLE: {len(calibration_data['calibration_k_table'])} puntos")
            
            # Enviar TODA la estructura al ESP32
            if calibration_data and hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                cal_json = json.dumps(calibration_data)
                print(f"Enviando UPDATE_CALIBRATION_ALL con {len(calibration_data)} secciones...")
                self.serial_reader.send_command(f"UPDATE_CALIBRATION_ALL:{cal_json}")
                print(f"✓ TODA la estructura de calibración sincronizada al ESP32")
            else:
                print("Error: No conectado o sin serial_reader para enviar calibración")
        except Exception as e:
            print(f"Error en _send_all_calibration_data: {e}")
            import traceback
            traceback.print_exc()

    def _send_oled_cal_done(self):  # NUEVO: Envía comando de finalización de calibración a OLED
        """Notifica al ESP32 que calibración completó y OLED debe mostrar datos"""
        try:
            if hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self.serial_reader, 'send_command'):
                print("→ Enviando OLED_CAL_DONE para actualizar pantalla...")
                result = self.serial_reader.send_command("OLED_CAL_DONE")
                print(f"✓ OLED_CAL_DONE enviado (resultado: {result})")
            else:
                print(f"⚠ Advertencia: serial_reader no disponible - sr={hasattr(self, 'serial_reader')}, sr_val={getattr(self, 'serial_reader', None)}")
        except Exception as e:
            print(f"✗ Error al enviar OLED_CAL_DONE: {e}")
            import traceback
            traceback.print_exc()

    def add_solution(self):
        """
        Agrega una nueva solución de calibración a la lista de puntos.
        
        Añade una fila vacía a la tabla para ingresa un nuevo valor conocido
        de conductividad que se medirá posteriormente.
        """
        text = self.solution_input.text().strip()
        if not text:
            QMessageBox.warning(self, "Error", "Ingrese un valor de conductividad.")
            return

        try:
            known_value = float(text)
            if known_value < 0 or known_value > 5000:
                QMessageBox.warning(self, "Error", "El valor debe estar entre 0 y 5000 µS.")
                return
            
            if known_value in self.calibration_data:
                QMessageBox.warning(self, "Error", f"La solución de {known_value} µS ya existe.")
                return

            # La temperatura será capturada automáticamente del ESP32 durante la medición
            self.calibration_data[known_value] = {'ema': None, 'temp': None}
            self.update_solutions_list()
            self.solution_input.clear()
            self.status_label.setText(f"Solución de {known_value} µS agregada. Total: {len(self.calibration_data)}")
            self.status_label.setStyleSheet("padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;")

        except ValueError:
            QMessageBox.warning(self, "Error", "Ingrese un número válido.")

    def add_empty_row(self):
        """Agrega una fila vacía editable a la tabla de soluciones"""
        row_count = self.solutions_table.rowCount()
        self.solutions_table.insertRow(row_count)
        
        # Crear celda editable para conductividad
        item_cond = QTableWidgetItem("")
        item_cond.setTextAlignment(Qt.AlignCenter)
        item_cond.setFlags(item_cond.flags() | Qt.ItemIsEditable)
        self.solutions_table.setItem(row_count, 0, item_cond)
        
        # Celdas de voltaje y temperatura (inicialmente vacías)
        item_v = QTableWidgetItem("sin medir")
        item_v.setForeground(pg.mkColor('#ff9800'))
        item_v.setTextAlignment(Qt.AlignCenter)
        item_v.setFlags(item_v.flags() & ~Qt.ItemIsEditable)
        self.solutions_table.setItem(row_count, 1, item_v)
        
        item_t = QTableWidgetItem("--")
        item_t.setTextAlignment(Qt.AlignCenter)
        item_t.setFlags(item_t.flags() & ~Qt.ItemIsEditable)
        self.solutions_table.setItem(row_count, 2, item_t)
        
        # Botón de captura
        btn = QPushButton("Capturar ahora")
        btn.setMaximumWidth(140)
        btn.setStyleSheet("background:#2196f3;color:white;border-radius:3px;padding:5px;")
        btn.clicked.connect(lambda checked, row=row_count: self.send_capture_from_row(row))
        self.solutions_table.setCellWidget(row_count, 3, btn)
        
        self.solutions_table.resizeColumnsToContents()
        self.solutions_table.horizontalHeader().setStretchLastSection(False)
        self.solutions_table.setColumnWidth(0, 120)
        self.solutions_table.setColumnWidth(1, 160)
        self.solutions_table.setColumnWidth(2, 120)
        self.solutions_table.setColumnWidth(3, 150)
        
        self.status_label.setText(f"Fila vacía agregada. Total: {self.solutions_table.rowCount()} fila(s)")
        self.status_label.setStyleSheet("padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;")

    def remove_solution(self):
        """
        Elimina la solución seleccionada de la tabla de calibración.
        
        Remueve la fila seleccionada si hay al menos una fila restante.
        """
        current_row = self.solutions_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Error", "Seleccione una fila para eliminar.")
            return

        item = self.solutions_table.item(current_row, 0)
        if item is None:
            self.solutions_table.removeRow(current_row)
            return
        
        try:
            known_value = float(item.text())
            if known_value in self.calibration_data:
                del self.calibration_data[known_value]
        except Exception:
            pass
        
        self.solutions_table.removeRow(current_row)
        self.status_label.setText(f"Fila eliminada. Total: {self.solutions_table.rowCount()} fila(s)")
        self.status_label.setStyleSheet("padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;")

    def update_solutions_list(self):
        """Refresca la visualización de soluciones en la tabla.
        
        Sincroniza los datos almacenados con la representación visual,
        actualizando voltajes medidos, temperaturas y factores K de cada fila.
        """
        self._suppress_item_changes = True
        self.solutions_table.setRowCount(0)

        for row_idx, known_value in enumerate(sorted(self.calibration_data.keys())):
            val = self.calibration_data[known_value]
            ema_val = None
            temp_val = None
            if isinstance(val, dict):
                ema_val = val.get('ema')
                temp_val = val.get('temp')
            else:
                ema_val = val

            self.solutions_table.insertRow(row_idx)

            item_cond = QTableWidgetItem(str(int(known_value)))
            item_cond.setTextAlignment(Qt.AlignCenter)
            item_cond.setFlags(item_cond.flags() & ~Qt.ItemIsEditable)
            self.solutions_table.setItem(row_idx, 0, item_cond)

            if ema_val is not None:
                item_v = QTableWidgetItem(f"{float(ema_val):.1f}")
                item_v.setForeground(pg.mkColor('#2e7d32'))
            else:
                item_v = QTableWidgetItem("sin medir")
                item_v.setForeground(pg.mkColor('#ff9800'))
            item_v.setTextAlignment(Qt.AlignCenter)
            item_v.setFlags(item_v.flags() & ~Qt.ItemIsEditable)
            self.solutions_table.setItem(row_idx, 1, item_v)

            if temp_val is not None:
                item_t = QTableWidgetItem(f"{float(temp_val):.1f}")
            else:
                item_t = QTableWidgetItem("--")
            item_t.setTextAlignment(Qt.AlignCenter)
            item_t.setFlags(item_t.flags() & ~Qt.ItemIsEditable)  # Read-only (auto from ESP32)
            item_t.setToolTip("Se capturará automáticamente del sensor ESP32")
            self.solutions_table.setItem(row_idx, 2, item_t)

            btn = QPushButton("Capturar ahora")
            btn.setMaximumWidth(140)
            btn.setStyleSheet("background:#2196f3;color:white;border-radius:3px;padding:5px;")
            btn.clicked.connect(lambda checked, v=known_value: self.send_capture(v))
            self.solutions_table.setCellWidget(row_idx, 3, btn)

        self.solutions_table.resizeColumnsToContents()
        self.solutions_table.horizontalHeader().setStretchLastSection(False)
        self.solutions_table.setColumnWidth(0, 120)
        self.solutions_table.setColumnWidth(1, 160)
        self.solutions_table.setColumnWidth(2, 120)
        self.solutions_table.setColumnWidth(3, 150)

        self._suppress_item_changes = False

    def finalize_calibration(self):
        """Valida calibración y genera rangos de conductividad.
        
        Verifica que se hayan medido suficientes puntos, calcula factores K,
        genera rangos de voltaje sin solapamiento y guarda en la configuración.
        """
        unmeasured = [v for v, m in self.calibration_data.items() if (m is None) or (isinstance(m, dict) and m.get('ema') is None)]
        if unmeasured:
            unmeasured_str = ", ".join([f"{v} µS" for v in unmeasured])
            reply = QMessageBox.question(
                self,
                "Soluciones sin medir",
                f"Las siguientes soluciones aún no se han medido:\n{unmeasured_str}\n\n"
                "¿Desea continuar de todas formas?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        if not self.calibration_data:
            QMessageBox.warning(self, "Error", "Debe agregar al menos una solución.")
            return

        measured_count = sum(1 for m in self.calibration_data.values() if (m is not None and (not isinstance(m, dict) or m.get('ema') is not None)))
        if measured_count == 0:
            QMessageBox.warning(self, "Error", "Debe medir al menos una solución antes de finalizar.")
            return

        self.generate_and_save_ranges()
        
        # Calcular EC calibrada y enviar SHOW_EC al ESP32
        self._calculate_and_send_show_ec()
        
        # Enviar comando para actualizar OLED a pantalla de datos
        self._send_oled_cal_done()
        
        self.accept()

    def generate_and_save_ranges(self):
        """Calcula y guarda tabla K desde mediciones de conductividad.
        
        Aplica polinomio DFRobot a voltajes medidos, calcula K=EC_conocida/EC_polinomio,
        genera rangos de voltaje y persiste todo en calibration_ranges.cfg.
        """
        """
        Calcula K dinámico para cada punto de calibración como: K = EC_reference / EC_polynomial
        Guarda en [CALIBRATION_K_TABLE] section del archivo cfg
        """
        try:
            # Recopilar datos de calibración
            measured = []
            for known_ec, val in self.calibration_data.items():
                if val is None:
                    continue
                if isinstance(val, dict):
                    ema_v = val.get('ema')
                    temp_v = val.get('temp')
                else:
                    ema_v = val
                    temp_v = None
                if ema_v is not None:
                    measured.append((known_ec, float(ema_v), float(temp_v) if temp_v is not None else None))

            if not measured:
                QMessageBox.warning(self, "Error", "No hay soluciones medidas para generar calibración K.")
                return

            # Cargar configuración para obtener coeficientes de temperatura
            coef = 0.02
            temp_ref = 25.0
            try:
                cfgp = configparser.ConfigParser()
                if os.path.exists('calibration_ranges.cfg'):
                    try:
                        cfgp.read('calibration_ranges.cfg', encoding='utf-8')
                    except Exception:
                        cfgp.read('calibration_ranges.cfg')
                    if 'TEMPERATURE' in cfgp:
                        coef = float(cfgp.get('TEMPERATURE', 'coef_temp', fallback=str(coef)))
                        temp_ref = float(cfgp.get('TEMPERATURE', 'temp_ref', fallback=str(temp_ref)))
            except Exception:
                pass

            # Calcular K para cada punto medido - SIN COMPENSACION A 25°C
            # K se calibra a la temperatura ACTUAL del sensor, no a una referencia fija
            k_table = {}  # {ec_poly: k_factor}
            v_comp_table = {}  # {ec_poly: v_raw} para generar rangos de voltaje
            
            for known_ec, voltage_raw, temp_meas in measured:
                try:
                    if voltage_raw == 0:
                        continue

                    # Aplicar polinomio DFRobot DIRECTAMENTE al voltaje raw
                    # SIN compensación de temperatura - K captura la calibración a temp actual
                    v_comp = float(voltage_raw)  # Voltaje sin compensar
                    
                    # Polinomio DFRobot aplicado directamente
                    ec_poly = 133.42 * v_comp**3 - 255.86 * v_comp**2 + 857.39 * v_comp
                    if ec_poly < 0:
                        ec_poly = 0.0

                    # Calcular K: proporciona el factor de ajuste a temperatura ACTUAL
                    # K ya incorpora la temperatura a la que se calibró
                    if ec_poly > 0:
                        k_factor = float(known_ec) / float(ec_poly)
                    else:
                        k_factor = 1.0
                    
                    k_table[ec_poly] = k_factor
                    v_comp_table[ec_poly] = v_comp
                    print(f"K Calibration: EC_ref={known_ec}µS, V_raw={voltage_raw:.6f}V @ {temp_meas}°C → EC_poly={ec_poly:.2f}µS (sin compensar) → K={k_factor:.6f}")

                except Exception as e:
                    print(f"Error calculating K for {known_ec}: {e}")
                    continue

            if not k_table:
                QMessageBox.warning(self, "Error", "No se pudieron calcular factores K.")
                return

            # Guardar tabla K en calibration_ranges.cfg como [CALIBRATION_K_TABLE]
            try:
                cfg_path = "calibration_ranges.cfg"
                cfg = configparser.ConfigParser()
                if os.path.exists(cfg_path):
                    try:
                        cfg.read(cfg_path, encoding='utf-8')
                    except Exception:
                        cfg.read(cfg_path)

                # Reemplazar o crear sección [CALIBRATION_K_TABLE]
                if 'CALIBRATION_K_TABLE' in cfg:
                    cfg.remove_section('CALIBRATION_K_TABLE')
                cfg.add_section('CALIBRATION_K_TABLE')

                # Grabar cada entrada: EC_at_25C = K_factor
                for ec_key in sorted(k_table.keys()):
                    k_val = k_table[ec_key]
                    cfg.set('CALIBRATION_K_TABLE', f"{ec_key:.6f}", f"{k_val:.6f}")

                VOLTAGE_MARGIN = 0.1
                
                sorted_v_comp = sorted(v_comp_table.values())
                
                known_k_ranges = {}  # {(v_min, v_max): k_factor}
                
                # Generar rangos sin solapamiento usando puntos medios
                for i, v in enumerate(sorted_v_comp):
                    if i == 0:
                        # Primer rango: desde 0 hasta el punto medio entre este punto y el siguiente
                        v_min = 0.0
                        if len(sorted_v_comp) > 1:
                            # Punto medio entre este y el siguiente
                            v_max = (v + sorted_v_comp[i + 1]) / 2.0
                        else:
                            # Solo un punto, usar con margen
                            v_max = v + VOLTAGE_MARGIN
                    else:
                        # Rangos siguientes: desde el punto medio anterior hasta el punto medio siguiente (o hasta el final)
                        v_min = (sorted_v_comp[i - 1] + v) / 2.0
                        
                        if i < len(sorted_v_comp) - 1:
                            # No es el último punto, calcular punto medio con el siguiente
                            v_max = (v + sorted_v_comp[i + 1]) / 2.0
                        else:
                            # Último punto, extender hasta el final con margen
                            v_max = v + VOLTAGE_MARGIN
                    
                    # Obtener el K factor asociado a este voltaje
                    # Encontrar el K correspondiente buscando por EC (que mapea a este v_comp)
                    for ec_key, v_stored in v_comp_table.items():
                        if abs(v_stored - v) < 0.0001:  # Comparación con tolerancia
                            k_val = k_table[ec_key]
                            known_k_ranges[(v_min, v_max)] = k_val
                            print(f"KNOWN_K_RANGE: [{v_min:.6f}V, {v_max:.6f}V] → K={k_val:.6f}")
                            break
                
                # Guardar KNOWN_K_RANGES en el cfg
                if 'KNOWN_K_RANGES' in cfg:
                    cfg.remove_section('KNOWN_K_RANGES')
                cfg.add_section('KNOWN_K_RANGES')
                
                for (v_min, v_max), k_val in sorted(known_k_ranges.items()):
                    cfg.set('KNOWN_K_RANGES', f"{v_min:.6f},{v_max:.6f}", f"{k_val:.6f}")

                # Guardar archivo
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    cfg.write(f)

                print(f"K table y KNOWN_K_RANGES guardadas en {cfg_path}: {len(k_table)} puntos, {len(known_k_ranges)} rangos")

                # Enviar tabla K al ESP32 para sincronización
                try:
                    import json
                    if hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                        k_json = json.dumps(k_table)
                        self.serial_reader.send_command(f"UPDATE_K_TABLE:{k_json}")
                        print(f"Tabla K sincronizada al ESP32")
                except Exception as sync_err:
                    print(f"No se pudo sincronizar tabla K al ESP32: {sync_err}")

            except Exception as e:
                print(f"Error saving K table to cfg: {e}")
                QMessageBox.warning(self, "Error", f"Error al guardar tabla K: {e}")
                return

            # Mostrar resumen
            summary = "✅ Tabla K y Rangos de Calibración Generados:\n\n"
            summary += f"Puntos de calibración: {len(k_table)}\n"
            summary += f"Rangos de voltaje: {len(known_k_ranges)}\n"
            summary += f"Margen aplicado: 200mV (+0.2V)\n\n"
            summary += "Rangos de Voltaje:\n"
            for (v_min, v_max), k_val in sorted(known_k_ranges.items()):
                summary += f"  [{v_min:.4f}V - {v_max:.4f}V] → K={k_val:.6f}\n"
            summary += "\nPuntos K:\n"
            for ec_key in sorted(k_table.keys()):
                k_val = k_table[ec_key]
                summary += f"  EC_poly={ec_key:.2f}µS → K={k_val:.6f}\n"

            QMessageBox.information(self, "Éxito", summary)

            # Intenta recargar en la app si es posible
            try:
                if hasattr(self, 'app') and hasattr(self.app, 'load_known_k_table'):
                    self.app.load_known_k_table()
            except:
                pass

        except Exception as e:
            print(f"Error en generate_and_save_ranges: {e}")
            QMessageBox.critical(self, "Error", f"Error generando rangos K: {str(e)[:100]}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al generar rangos: {e}")
        self.setup_ui()

        try:
            self.app.calibration_message.connect(self.on_cal_message)
        except Exception:
            pass
        self.capture_timer = QTimer(self)
        self.capture_timer.setInterval(100)  # Tick cada 100ms para captura de muestras
        self.capture_timer.timeout.connect(self._capture_tick)
        self.capture_remaining = 0
        self._capture_samples = []
        self._capture_temps = []
        self._capture_target = None
        self._capture_phase = None
        self._stabilize_remaining = 0
        self._samples_to_capture = 0

    def _send_all_calibration_data(self):  # NUEVO: Envía TODA la estructura de calibración al ESP32
        """Lee TODAS las secciones del cfg y las envía en una sola estructura"""
        import json
        import configparser
        import os
        
        try:
            cfg_path = "calibration_ranges.cfg"
            
            if not os.path.exists(cfg_path):
                print("Error: No existe calibration_ranges.cfg")
                return
            
            cfg = configparser.ConfigParser()
            try:
                cfg.read(cfg_path, encoding='utf-8')
            except Exception:
                cfg.read(cfg_path)
            
            # Empaquetar TODAS las secciones de calibración
            calibration_data = {}
            
            # 1. KNOWN_K_TABLE
            if 'KNOWN_K_TABLE' in cfg:
                calibration_data['known_k_table'] = {}
                for ec_str, k_str in cfg.items('KNOWN_K_TABLE'):
                    try:
                        calibration_data['known_k_table'][float(ec_str)] = float(k_str)
                    except:
                        pass
                print(f"Empaquetado KNOWN_K_TABLE: {len(calibration_data['known_k_table'])} puntos")
            
            # 2. KNOWN_K_RANGES
            if 'KNOWN_K_RANGES' in cfg:
                calibration_data['known_k_ranges'] = {}
                for range_str, k_str in cfg.items('KNOWN_K_RANGES'):
                    try:
                        parts = [p.strip() for p in range_str.split(',')]
                        if len(parts) == 2:
                            min_ec = float(parts[0])
                            max_ec = float(parts[1])
                            k_val = float(k_str)
                            calibration_data['known_k_ranges'][f"{min_ec},{max_ec}"] = k_val
                    except:
                        pass
                print(f"Empaquetado KNOWN_K_RANGES: {len(calibration_data['known_k_ranges'])} rangos")
            
            # 3. USER_CALIBRATION
            if 'USER_CALIBRATION' in cfg:
                calibration_data['user_calibration'] = {}
                for ec_str, cal_str in cfg.items('USER_CALIBRATION'):
                    try:
                        calibration_data['user_calibration'][float(ec_str)] = cal_str
                    except:
                        pass
                print(f"Empaquetado USER_CALIBRATION: {len(calibration_data['user_calibration'])} puntos calibrados")
            
            # 4. CALIBRATION_K_TABLE
            if 'CALIBRATION_K_TABLE' in cfg:
                calibration_data['calibration_k_table'] = {}
                for ec_str, k_str in cfg.items('CALIBRATION_K_TABLE'):
                    try:
                        calibration_data['calibration_k_table'][float(ec_str)] = float(k_str)
                    except:
                        pass
                print(f"Empaquetado CALIBRATION_K_TABLE: {len(calibration_data['calibration_k_table'])} puntos")
            
            # Enviar TODA la estructura al ESP32
            if calibration_data and hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                cal_json = json.dumps(calibration_data)
                print(f"Enviando UPDATE_CALIBRATION_ALL con {len(calibration_data)} secciones...")
                self.serial_reader.send_command(f"UPDATE_CALIBRATION_ALL:{cal_json}")
                print(f"✓ TODA la estructura de calibración sincronizada al ESP32")
            else:
                print("Error: No conectado o sin serial_reader para enviar calibración")
        except Exception as e:
            print(f"Error en _send_all_calibration_data: {e}")
            import traceback
            traceback.print_exc()

    def _send_oled_cal_done(self):  # NUEVO: Envía comando de finalización de calibración a OLED
        """Notifica al ESP32 que calibración completó y OLED debe mostrar datos"""
        try:
            if hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self.serial_reader, 'send_command'):
                print("→ Enviando OLED_CAL_DONE para actualizar pantalla...")
                result = self.serial_reader.send_command("OLED_CAL_DONE")
                print(f"✓ OLED_CAL_DONE enviado (resultado: {result})")
            else:
                print(f"⚠ Advertencia: serial_reader no disponible - sr={hasattr(self, 'serial_reader')}, sr_val={getattr(self, 'serial_reader', None)}")
        except Exception as e:
            print(f"✗ Error al enviar OLED_CAL_DONE: {e}")
            import traceback
            traceback.print_exc()

    def _generate_and_send_k_table(self):  # Genera tabla K dinámica y la envía al ESP32
        """
        Calcula K dinámico para cada punto de calibración como: K = EC_reference / EC_polynomial
        Guarda en [CALIBRATION_K_TABLE] section del archivo cfg y envía al ESP32 (UPDATE_K_TABLE)
        """
        import json
        import configparser
        import os
        
        try:
            # Recopilar datos de calibración desde self.calibration_data
            # En KnownCalibrationDialog: {known_ec: {'ema': voltage, 'temp': temperature}}
            measured = []
            for known_ec, val in self.calibration_data.items():
                if val is None:
                    continue
                if isinstance(val, dict):
                    ema_v = val.get('ema')
                    temp_v = val.get('temp')
                else:
                    ema_v = val
                    temp_v = None
                if ema_v is not None:
                    measured.append((float(known_ec), float(ema_v), float(temp_v) if temp_v is not None else None))

            if not measured:
                return

            # Cargar configuración para obtener coeficientes de temperatura
            coef = 0.02
            temp_ref = 25.0
            try:
                cfgp = configparser.ConfigParser()
                if os.path.exists('calibration_ranges.cfg'):
                    try:
                        cfgp.read('calibration_ranges.cfg', encoding='utf-8')
                    except Exception:
                        cfgp.read('calibration_ranges.cfg')
                    if 'TEMPERATURE' in cfgp:
                        coef = float(cfgp.get('TEMPERATURE', 'coef_temp', fallback=str(coef)))
                        temp_ref = float(cfgp.get('TEMPERATURE', 'temp_ref', fallback=str(temp_ref)))
            except Exception:
                pass

            # Calcular K para cada punto medido
            k_table = {}  # {ec_conocida: k_factor} - SOLO valores calibrados
            
            for known_ec, voltage_raw, temp_meas in measured:
                try:
                    if voltage_raw == 0:
                        continue

                    # Temperatura para compensación
                    if temp_meas is not None:
                        factor_temp = 1.0 + float(coef) * (float(temp_meas) - float(temp_ref))
                        if factor_temp < 0.2:
                            factor_temp = 0.2
                        elif factor_temp > 2.0:
                            factor_temp = 2.0
                    else:
                        factor_temp = 1.0

                    # Voltaje compensado a 25°C
                    v_comp = float(voltage_raw) / factor_temp if factor_temp != 0 else float(voltage_raw)
                    
                    # Aplicar polinomio DFRobot: EC polynomial @ 25°C
                    ec_poly_25c = 133.42 * v_comp**3 - 255.86 * v_comp**2 + 857.39 * v_comp
                    if ec_poly_25c < 0:
                        ec_poly_25c = 0.0

                    # Calcular K: factor de ajuste calibración
                    if ec_poly_25c > 0:
                        k_factor = float(known_ec) / float(ec_poly_25c)
                    else:
                        k_factor = 1.0
                    
                    k_table[float(known_ec)] = k_factor
                    print(f"K Direct: EC_ref={known_ec}µS, V_raw={voltage_raw:.6f}V @ {temp_meas}°C → EC_poly={ec_poly_25c:.2f}µS → K={k_factor:.6f}")

                except Exception as e:
                    print(f"Error calculating K for {known_ec}: {e}")
                    continue

            if not k_table:
                return

            # Guardar tabla K en cfg
            try:
                cfg_path = "calibration_ranges.cfg"
                cfg = configparser.ConfigParser()
                if os.path.exists(cfg_path):
                    try:
                        cfg.read(cfg_path, encoding='utf-8')
                    except Exception:
                        cfg.read(cfg_path)

                # Reemplazar o crear sección [CALIBRATION_K_TABLE]
                if 'CALIBRATION_K_TABLE' in cfg:
                    cfg.remove_section('CALIBRATION_K_TABLE')
                cfg.add_section('CALIBRATION_K_TABLE')

                # Grabar cada entrada: EC_conocida = K_factor
                for ec_key in sorted(k_table.keys()):
                    k_val = k_table[ec_key]
                    cfg.set('CALIBRATION_K_TABLE', f"{ec_key:.6f}", f"{k_val:.6f}")

                # Guardar archivo
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    cfg.write(f)

                print(f"K table guardada en cfg: {len(k_table)} puntos DIRECTOS")

                # Enviar tabla K al ESP32 (UPDATE_K_TABLE - CORTO, ~100 bytes, NO causa saturación)
                try:
                    if hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                        k_json = json.dumps(k_table)
                        self.serial_reader.send_command(f"UPDATE_K_TABLE:{k_json}")
                        print(f"✓ K table enviada al ESP32 via UPDATE_K_TABLE: {len(k_table)} puntos")
                except Exception as sync_err:
                    print(f"No se pudo sincronizar tabla K al ESP32: {sync_err}")

            except Exception as e:
                print(f"Error saving K table to cfg: {e}")

        except Exception as e:
            print(f"Error en _generate_and_send_k_table: {e}")

    def _calculate_and_send_show_ec(self):
        """Sincroniza OLED con el mismo EC ya visible en la app (fuente única)."""
        try:
            if not hasattr(self, 'app') or not self.app:
                return
            cr = getattr(self.app, 'current_reading', {})
            ec_ui = cr.get('sensor')
            t = cr.get('temp') or 25.0
            if ec_ui is None:
                return
            if hasattr(self, 'serial_reader') and self.serial_reader:
                send_show_ec_to_esp32(self.serial_reader, t, float(ec_ui))
        except Exception as e:
            print(f"Error calc_show_ec: {e}")

    def _send_existing_k_table_from_cfg(self):  # Envía K_TABLE existente desde cfg al ESP32
        """Lee K_TABLE del cfg y la envía al ESP32 via UPDATE_K_TABLE (corto, sin saturar buffer)"""
        import json
        import configparser
        import os
        
        try:
            cfg_path = "calibration_ranges.cfg"
            if not os.path.exists(cfg_path):
                print("Advertencia: No existe calibration_ranges.cfg, no se envía K_TABLE")
                return
            
            cfg = configparser.ConfigParser()
            try:
                cfg.read(cfg_path, encoding='utf-8')
            except Exception:
                cfg.read(cfg_path)
            
            # Leer CALIBRATION_K_TABLE del cfg
            k_table = {}
            if 'CALIBRATION_K_TABLE' in cfg:
                for ec_str, k_str in cfg.items('CALIBRATION_K_TABLE'):
                    try:
                        k_table[float(ec_str)] = float(k_str)
                    except:
                        pass
            
            if not k_table:
                print("Advertencia: CALIBRATION_K_TABLE vacía o no existe")
                return
            
            # Enviar tabla K al ESP32 via UPDATE_K_TABLE (corto, seguro)
            try:
                if hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                    k_json = json.dumps(k_table)
                    self.serial_reader.send_command(f"UPDATE_K_TABLE:{k_json}")
                    print(f"✓ K_TABLE enviada al ESP32: {len(k_table)} puntos desde cfg")
                else:
                    print("Advertencia: serial_reader no disponible - K_TABLE no enviada")
            except Exception as sync_err:
                print(f"Error enviando K_TABLE al ESP32: {sync_err}")
                
        except Exception as e:
            print(f"Error en _send_existing_k_table_from_cfg: {e}")

    def _capture_tick(self):
        """Procesa un tick del temporizador de captura de muestras.
        
        Implementa dos fases: estabilización (sin capturar) y captura (recopilando
        muestras crudas). Decrementa contadores y cambia de fase según el estado.
        """
        try:
            # Obtener lectura cruda actual del ESP32
            try:
                if hasattr(self, 'app') and getattr(self.app, 'current_reading', None):
                    cr = self.app.current_reading
                    raw = None
                    if cr.get('raw') is not None:
                        raw = cr.get('raw')
                    elif cr.get('signal') is not None:
                        raw = cr.get('signal')
                    elif cr.get('sensor') is not None:
                        raw = cr.get('sensor')

                    temp_here = None
                    try:
                        if 'temp' in cr and cr.get('temp') is not None:
                            temp_here = float(cr.get('temp'))
                        elif 'temperature' in cr and cr.get('temperature') is not None:
                            temp_here = float(cr.get('temperature'))
                    except Exception:
                        temp_here = None

                    # FASE 1: ESTABILIZACIÓN - Esperar sin capturar
                    if self._capture_phase == 'stabilizing':
                        self._stabilize_remaining -= 0.1  # Decrementar por cada tick (100ms)
                        if self._stabilize_remaining <= 0:
                            # Cambiar a fase de captura
                            self._capture_phase = 'capturing'
                            self._capture_samples = []
                            self._capture_temps = []
                            self.status_label.setText(f"Capturando {self._samples_to_capture} muestras...")
                            self.status_label.setStyleSheet("padding: 8px; background: #e3f2fd; border-radius: 3px; border-left: 4px solid #2196f3;")
                        else:
                            # Mostrar progreso de estabilización
                            secs_left = max(0.0, self._stabilize_remaining)
                            self.status_label.setText(f"Estabilizando... {secs_left:.1f}s restantes")

                    # FASE 2: CAPTURA - Recopilar N muestras de señal cruda
                    elif self._capture_phase == 'capturing':
                        if len(self._capture_samples) < self._samples_to_capture:
                            # Capturar muestra cruda
                            if raw is not None:
                                try:
                                    self._capture_samples.append(float(raw))
                                except Exception:
                                    pass
                            if temp_here is not None:
                                if not hasattr(self, '_capture_temps'):
                                    self._capture_temps = []
                                self._capture_temps.append(float(temp_here))
                            
                            # Mostrar progreso
                            muestras_ok = len(self._capture_samples)
                            pct = int((muestras_ok / self._samples_to_capture) * 100)
                            self.status_label.setText(f"Capturando... {muestras_ok}/{self._samples_to_capture} muestras ({pct}%)")
                        
                        # Cuando se hayan capturado todas las muestras
                        if len(self._capture_samples) >= self._samples_to_capture:
                            self.capture_timer.stop()
                            self._capture_phase = None
                            
                            # Procesar muestras capturadas
                            samples = [s for s in self._capture_samples if s is not None]
                            if not samples:
                                self.status_label.setText("No se obtuvieron muestras durante la medición.")
                                self._capture_target = None
                                self._capture_samples = []
                                return

                            # Calcular máximo de las muestras crudas (método principal)
                            max_voltage = max(samples)
                            avg = sum(samples) / len(samples)
                            
                            # También calcular moda y mediana como alternativas
                            samples_sorted = sorted(samples)
                            median = samples_sorted[len(samples_sorted) // 2]
                            mode_val = self._find_mode_with_tolerance(samples, tolerance=0.002)
                            
                            mode_str = f"{mode_val:.6f}V" if mode_val else "N/A"
                            print(f"[CAL] Máximo: {max_voltage:.6f}V, Promedio: {avg:.6f}V, Moda: {mode_str}")

                            # Usar máximo como valor principal
                            final_voltage = max_voltage

                            # Obtener temperatura (promedio de capturas o valor por defecto)
                            temp_to_use = None
                            if self._capture_target is not None:
                                entry = self.calibration_data.get(self._capture_target)
                                if isinstance(entry, dict) and entry.get('temp') is not None:
                                    temp_to_use = float(entry.get('temp'))
                            
                            if temp_to_use is None:
                                try:
                                    temps = [t for t in getattr(self, '_capture_temps', []) if t is not None]
                                    if temps:
                                        temp_to_use = sum(temps) / len(temps)
                                except Exception:
                                    pass
                            
                            if temp_to_use is None:
                                temp_to_use = 25.0

                            # Calcular EC usando polinomio DFRobot - SIN COMPENSACIÓN
                            k_calc = None
                            ec_final_poly = None
                            if self._capture_target is not None and final_voltage > 0:
                                try:
                                    # Polinomio DFRobot aplicado DIRECTAMENTE al voltaje raw
                                    # SIN compensación - K captura la calibración a temp actual
                                    ec_final_poly = 133.42 * final_voltage**3 - 255.86 * final_voltage**2 + 857.39 * final_voltage
                                    if ec_final_poly < 0:
                                        ec_final_poly = 0.0
                                    
                                    # K = EC_known / EC_polynomial
                                    k_calc = float(self._capture_target) / ec_final_poly if ec_final_poly > 0 else 1.0
                                except Exception as e:
                                    print(f"Error calculating K: {e}")
                                    pass

                            # Guardar resultado
                            if self._capture_target is not None:
                                self.calibration_data[self._capture_target] = {
                                    'ema': float(final_voltage), 
                                    'temp': (float(temp_to_use) if temp_to_use is not None else None),
                                    'k_factor': (float(k_calc) if k_calc is not None else None),
                                    'ec_poly': (float(ec_final_poly) if ec_final_poly is not None else None)
                                }
                                self.update_solutions_list()
                                
                                ec_info = f", EC_poly≈{ec_final_poly:.1f}µS" if ec_final_poly is not None else ""
                                k_info = f", K={k_calc:.1f}" if k_calc is not None else ""
                                self.status_label.setText(f"✓ Medición guardada: {self._capture_target} µS → {final_voltage:.1f} V @ {temp_to_use:.1f}°C (muestras={len(samples)}){k_info}{ec_info}")
                                self.status_label.setStyleSheet("padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;")
                                
                                # Sincronizar OLED con el valor correcto
                                try:
                                    if self.serial_reader and self.connected and self._capture_target is not None:
                                        ec_to_show = round(float(self._capture_target), 2)
                                        send_show_ec_to_esp32(self.serial_reader, temp_to_use, ec_to_show)
                                except Exception:
                                    pass

                            self._capture_target = None
                            self._capture_samples = []
                            self._capture_temps = []

            except Exception:
                pass
        except Exception:
            try:
                self.capture_timer.stop()
            except Exception:
                pass

    def set_serial(self, serial_reader, connected, app=None):
        """Asigna instancia serial y referencias a la aplicación.
        
        Configura los objetos SerialReader y la referencia a la app principal
        para que el diálogo pueda enviar comandos y acceder a datos en vivo.
        """
        self.serial_reader = serial_reader
        self.connected = bool(connected)
        if app is not None:
            self.app = app

    def send_capture(self, value: float):
        """Inicia captura de muestras para una solución específica.
        
        Configura el timer de muestreo con fases de estabilización y captura,
        recopilando N muestras crudas de voltaje a intervalos de 100ms.
        """
        if not self.connected or not self.serial_reader:
            QMessageBox.warning(self, "Error", "No conectado al ESP32.")
            return

        if value not in self.calibration_data:
            QMessageBox.warning(self, "Error", "Solución no registrada.")
            return

        # Obtener parámetros de captura
        stabilize_seconds = self.stabilize_spinbox.value()
        num_samples = self.samples_spinbox.value()

        self.status_label.setText(f"Preparando captura para {value} µS...")
        self.status_label.setStyleSheet("padding: 8px; background: #fff3e0; border-radius: 3px; border-left: 4px solid #ff9800;")

        try:
            try:
                self.serial_reader.send_command(f"CAL_KNOWN:{value}")
            except Exception:
                pass

            self._capture_samples = []
            self._capture_temps = []
            self._capture_target = value
            self._stabilize_remaining = stabilize_seconds
            self._samples_to_capture = num_samples
            self._capture_phase = 'stabilizing'

            if stabilize_seconds > 0:
                self.status_label.setText(f"Estabilizando ({stabilize_seconds}s)... (no se capturan muestras)")
            else:
                self.status_label.setText(f"Capturando {num_samples} muestras...")
                self._capture_phase = 'capturing'
            
            self.capture_timer.start()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo iniciar captura: {e}")

    def _find_mode_with_tolerance(self, samples, tolerance=0.002):  # Calcula la moda con tolerancia
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
        most_frequent_cluster = clusters[0]

        mode_value = most_frequent_cluster[1]
        freq = most_frequent_cluster[2]

        return mode_value

    def on_solution_item_changed(self, item):
        """Maneja cambios en celdas de la tabla de soluciones.
        
        Procesa ediciones en la tabla de calibración, ignorando cambios suprimidos
        internamente y validando que las celdas de temperatura sean de sólo lectura.
        """
        if getattr(self, '_suppress_item_changes', False):
            return

        try:
            row = item.row()
            col = item.column()
            if col != 2:
                return

            # Columna de temperatura es read-only ahora, no hacer nada
            return

        except Exception:
            pass

    def on_cal_message(self, obj):
        """
        Procesa mensajes de calibración recibidos del ESP32.
        
        Decodifica JSON con datos de voltaje y temperatura, actualiza tabla
        de mediciones y habilita botones según el estado del mensaje.
        """
        try:
            t = obj.get('type')
            if t == 'cal_point_saved':
                cond = obj.get('cond')
                ema = obj.get('ema')
                temp_msg = obj.get('temp') or obj.get('temperature') or None
                if cond in self.calibration_data:
                    cur = self.calibration_data.get(cond)
                    prev_temp = (cur.get('temp') if isinstance(cur, dict) else None) if cur is not None else None
                    chosen_temp = temp_msg if temp_msg is not None else prev_temp
                    try:
                        self.calibration_data[cond] = {'ema': float(ema) if ema is not None else None,
                                                      'temp': float(chosen_temp) if chosen_temp is not None else None}
                    except Exception:
                        self.calibration_data[cond] = {'ema': ema, 'temp': chosen_temp}
                    self.update_solutions_list()
                    self.status_label.setText(f"Punto guardado: {cond} µS → {ema:.1f} V @ {chosen_temp if chosen_temp is not None else '--'}°C")
        except Exception:
            pass


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
#       TABLA DE CALIBRACIÓN CON VALORES DESCONOCIDOS
# ═══════════════════════════════════════════════════════════════
class UnknownCalibrationTableDialog(QDialog):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.sample_count = 1  # Iniciar con una sola fila, el usuario agregará más
        self.serial_reader = None
        self.connected = False
        self.calibration_data = {}

        self._sampling_timer = QTimer(self)
        self._sampling_timer.setInterval(100)  # Timer cada 100ms para captura de muestras
        self._sampling_timer.timeout.connect(self._sampling_tick)
        self._sampling_remaining = 0
        self._sampling_samples = []
        self._sampling_temps = []
        self._sampling_row = None
        self._sampling_known_cond = None
        self._sampling_phase = None  # 'stabilizing', 'capturing', None
        self._stabilize_remaining = 0
        self._samples_to_capture = 0
        
        self.setWindowTitle("Calibración - Valores Conocidos")
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
            QComboBox {
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 5px;
            }
            QSpinBox {
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 5px;
            }
        """)
        self.init_ui()

    def init_ui(self):
        """Construye interfaz de tabla de calibración con N muestras.
        
        Crea tabla editable con N filas para ingresar conductividades conocidas,
        botones de medición, campos para voltajes medidos, K y estimados de EC.
        """
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel(f"Calibración de Valores Conocidos")
        title.setFont(QFont("Arial", 13, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)

        info = QLabel(
            "Proceso:\n"
            "1. Configure parámetros de captura, la cantidad de muestras y tiempo de estabilización\n"
            "2. Ingrese el valor conocido de conductividad (µS) para cada solución\n"
            "3. Presione 'Medir' para capturar datos de esa solución, incluyendo la temperatura\n"
        )
        info.setWordWrap(True)
        info.setStyleSheet("background: #e3f2fd; padding: 10px; border-radius: 4px; border-left: 4px solid #2196f3;")
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

        self.table.setRowCount(self.sample_count)
        self.table.setColumnCount(6)
        expected_headers = ["Conductividad Conocida (µS)", "Temperatura (°C)", "Botón Medir", "Valor Medido (V)", "K", "Estimado (µS)"]
        self.table.setHorizontalHeaderLabels(expected_headers)
        if self.table.columnCount() != len(expected_headers):
            self.table.setColumnCount(len(expected_headers))
            self.table.setHorizontalHeaderLabels(expected_headers)
        self.table.setColumnHidden(4, True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 135)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 140)

        self.measure_buttons = {}
        self.known_cond_inputs = {}
        self.temp_labels = {}
        self.measured_labels = {}
        self.k_labels = {}
        self.estimated_labels = {}

        # Agregar las filas iniciales
        for row in range(self.sample_count):
            self.add_table_row(row)

        scroll.setWidget(self.table)
        main_layout.addWidget(scroll)

        # Botón para agregar más filas
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

        self.status_label = QLabel("Fila inicial lista. Ingrese conductividad para medir.")
        self.status_label.setStyleSheet("padding: 8px; background: #e3f2fd; border-radius: 3px; border-left: 4px solid #2196f3;")
        main_layout.addWidget(self.status_label)

        button_layout = QHBoxLayout()

        use_existing_btn = QPushButton("Usar Calibración Existente")
        use_existing_btn.setMinimumHeight(40)
        use_existing_btn.setToolTip("Continuar usando únicamente los valores guardados en USER_CALIBRATION")
        use_existing_btn.clicked.connect(self.use_existing_user_calibration)
        use_existing_btn.setStyleSheet("""
            QPushButton {
                background: #ff9800;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #f57c00;
            }
        """)
        button_layout.addWidget(use_existing_btn)
        
        button_layout.addStretch()
        
        close_btn = QPushButton("Finalizar Calibración")
        close_btn.setMinimumHeight(40)
        close_btn.clicked.connect(self.finalize_calibration)
        close_btn.setStyleSheet("""
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
        button_layout.addWidget(close_btn)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def add_table_row(self, row):
        """Agrega una fila a la tabla con los controles necesarios"""
        input_widget = QLineEdit()
        input_widget.setPlaceholderText("100")
        input_widget.setValidator(QDoubleValidator(0, 10000, 2))
        self.known_cond_inputs[row] = input_widget
        self.table.setCellWidget(row, 0, input_widget)

        # Temperatura como etiqueta read-only (se captura automáticamente)
        temp_label = QLabel("--")
        temp_label.setAlignment(Qt.AlignCenter)
        temp_label.setStyleSheet("background: #f0f0f0; border-radius: 3px;")
        self.temp_labels[row] = temp_label
        self.table.setCellWidget(row, 1, temp_label)

        measure_btn = QPushButton("Medir")
        measure_btn.setStyleSheet("""
            QPushButton {
                background: #a0b356;
                color: white;
            }
            QPushButton:hover {
                background: #88a02c;
            }
            QPushButton:pressed {
                background: #445016;
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
        """Agrega una nueva fila a la tabla tabla"""
        new_row = self.table.rowCount()
        self.table.insertRow(new_row)
        self.add_table_row(new_row)
        self.sample_count = self.table.rowCount()
        self.status_label.setText(f"Fila agregada. Total: {self.table.rowCount()} fila(s)")
        self.status_label.setStyleSheet("padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;")

    def load_cal_table(self, section_name, archivo_cfg="calibration_ranges.cfg"):
        """Carga tabla de rangos de calibración desde cfg.
        
        Lee sección especificada del archivo de configuración, extrayendo
        rangos de voltaje con sus factores K asociados.
        """
        try:
            rangos = []
            with open(archivo_cfg, 'r', encoding='utf-8') as f:
                en = False
                for linea in f:
                    linea = linea.strip()
                    if not linea or linea.startswith('#'):
                        continue
                    if linea.upper() == f'[{section_name.upper()}]':
                        en = True
                        continue
                    if linea.startswith('[') and en:
                        break
                    if en:
                        if '=' in linea and '-' in linea:
                            rng, kstr = linea.split('=', 1)
                            mn, mx = rng.split('-', 1)
                            try:
                                rangos.append((float(mn), float(mx), float(kstr)))
                            except Exception:
                                continue
            rangos.sort(key=lambda x: x[0])
            return rangos
        except Exception:
            return None

    def update_cfg_k(self, section_name, min_v, max_v, new_k, archivo_cfg="calibration_ranges.cfg"):
        """Actualiza factor K de un rango en el archivo cfg.
        
        Busca el rango especificado en la sección indicada y actualiza su
        factor K, persistiendo los cambios en el archivo de configuración.
        """
        try:
            with open(archivo_cfg, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            out = []
            en = False
            for line in lines:
                s = line.strip()
                if s.upper() == f'[{section_name.upper()}]':
                    en = True
                    out.append(line)
                    continue
                if en and s.startswith('['):
                    en = False
                if en and '=' in s and '-' in s:
                    rng = s.split('=')[0]
                    try:
                        mn, mx = [float(x) for x in rng.split('-', 1)]
                        if abs(mn - float(min_v)) < 1e-6 and abs(mx - float(max_v)) < 1e-6:
                            out.append(f"{mn}-{mx}={new_k}\n")
                            continue
                    except Exception:
                        pass
                out.append(line)
            with open(archivo_cfg, 'w', encoding='utf-8') as f:
                f.writelines(out)
            return True
        except Exception:
            return False

    def _find_mode_with_tolerance(self, samples, tolerance=0.002):  # Calcula la moda con tolerancia
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
        most_frequent_cluster = clusters[0]

        mode_value = most_frequent_cluster[1]
        freq = most_frequent_cluster[2]

        return mode_value

    def save_user_calibration(self, archivo_cfg="calibration_ranges.cfg"):
        """Guarda calibración de usuario en archivo cfg.
        
        Persiste datos de USER_CALIBRATION (conductividades conocidas con sus
        factores K y voltajes medidos) en la sección correspondiente del cfg.
        """
        try:
            import configparser, os
            cfg = configparser.ConfigParser()

            if os.path.exists(archivo_cfg):
                cfg.read(archivo_cfg, encoding='utf-8')

            if 'USER_CALIBRATION' not in cfg:
                cfg['USER_CALIBRATION'] = {}
            
            for row, data in self.calibration_data.items():
                known_cond = data['known_cond']
                k_value = data['k']
                measured = data.get('measured')
                if measured is None:
                    cfg['USER_CALIBRATION'][str(int(known_cond))] = str(round(k_value, 6))
                else:
                    cfg['USER_CALIBRATION'][str(int(known_cond))] = f"{round(k_value,6)},{float(measured):.6f}"
            
            all_items = list(cfg.items('USER_CALIBRATION'))
            for key in list(cfg['USER_CALIBRATION'].keys()):
                cfg.remove_option('USER_CALIBRATION', key)
            for key, val in sorted(all_items, key=lambda x: float(x[0]), reverse=True):
                cfg['USER_CALIBRATION'][key] = val
                
            with open(archivo_cfg, 'w', encoding='utf-8') as f:
                cfg.write(f)
            return True
        except Exception as e:
            print(f"Error al guardar calibración de usuario: {e}")
            return False
        
    def save_known_k(self, known_value, k_value, archivo_cfg="calibration_ranges.cfg"):
        """Guarda par conductividad/factor K en cfg.
        
        Almacena una entrada en la sección KNOWN_K_TABLE mapeando
        conductividad conocida a su factor K de calibración.
        """
        try:
            import configparser, os
            cfg = configparser.ConfigParser()
            if os.path.exists(archivo_cfg):
                try:
                    cfg.read(archivo_cfg, encoding='utf-8')
                except Exception:
                    pass
            if 'KNOWN_K_TABLE' not in cfg:
                cfg['KNOWN_K_TABLE'] = {}
            cfg['KNOWN_K_TABLE'][str(int(known_value))] = str(float(k_value))
            with open(archivo_cfg, 'w', encoding='utf-8') as f:
                cfg.write(f)
            return True
        except Exception:
            return False

    def set_serial(self, serial_reader, connected, app=None):  # Asigna conexión serial a la tabla
        self.serial_reader = serial_reader
        self.connected = bool(connected)
        if app is not None:
            self.app = app
    

    def measure_sample(self, row):
        """Inicia medición de fila específica con dos fases de captura.
        
        Configura timer para ejecutar estabilización (sin muestras) seguida de
        captura de N muestras de voltaje crudo a intervalos regulares.
        """
        if not self.connected or not self.serial_reader:
            QMessageBox.warning(self, "Error", "No conectado al ESP32.")
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

        # Obtener parámetros de captura desde spinboxes
        num_samples = self.samples_spinbox.value()
        stabilize_seconds = self.stabilize_spinbox.value()

        for btn in self.measure_buttons.values():
            btn.setEnabled(False)

        # Inicializar estado de muestreo con dos fases
        self._sampling_row = row
        self._sampling_known_cond = known_cond
        self._sampling_phase = 'stabilizing'  # Fase 1: estabilización
        self._stabilize_remaining = stabilize_seconds  # Tiempo restante en segundos (se decrementará por ticks de 100ms)
        self._samples_to_capture = num_samples  # Meta de muestras
        self._sampling_samples = []  # Muestras recopiladas
        self._sampling_temps = []  # Temperaturas recopiladas

        # Actualizar UI con indicadores
        self.measured_labels[row].setText("...")
        self.measured_labels[row].setStyleSheet("background: #fff3e0; border-radius: 3px;")
        self.k_labels[row].setText("...")
        self.k_labels[row].setStyleSheet("background: #fff3e0; border-radius: 3px;")
        self.estimated_labels[row].setText("...")
        self.estimated_labels[row].setStyleSheet("background: #fff3e0; border-radius: 3px;")

        # Mostrar estado inicial: estabilizando
        self.status_label.setText(f"Muestra {row + 1}: Estabilizando {self._stabilize_remaining}s...")
        self.status_label.setStyleSheet("padding: 8px; background: #fff3e0; border-radius: 3px; border-left: 4px solid #ff9800;")

        if not getattr(self, '_oled_solutions_sent', False):
            try:
                if self.serial_reader and self.connected:
                    sols = []
                    for r in range(self.table.rowCount()):
                        txt = self.known_cond_inputs[r].text().strip()
                        if txt:
                            sols.append(txt)
                    if sols:
                        self.serial_reader.send_command(f"OLED_SOLUTIONS:{','.join(sols)}")
                        self._oled_solutions_sent = True
            except Exception:
                pass

        self._sampling_timer.start()


    def _sampling_tick(self):
        """Procesa un tick del timer de muestreo (100ms).
        
        Alterna entre fase de estabilización (decrementando contador) y captura
        (recopilando muestras), actualizando estados y progreso de medición.
        """
        try:
            # Obtener lectura de sensor
            raw_val = None
            temp_val = None
            if hasattr(self.app, 'current_reading') and self.app.current_reading:
                cr = self.app.current_reading
                if cr.get('raw') is not None:
                    raw_val = cr.get('raw')
                elif cr.get('signal') is not None:
                    raw_val = cr.get('signal')
                elif cr.get('sensor') is not None:
                    raw_val = cr.get('sensor')
                temp_val = cr.get('temp')
        except Exception:
            pass

        # FASE 1: ESTABILIZACIÓN (sin capturar muestras)
        if self._sampling_phase == 'stabilizing':
            self._stabilize_remaining -= 0.1  # Cada tick es 100ms = 0.1s
            
            if self._stabilize_remaining > 0:
                # Aún estabilizando, mostrar contador regresivo
                self.status_label.setText(
                    f"Muestra {self._sampling_row + 1}: Estabilizando {self._stabilize_remaining:.1f}s..."
                )
            else:
                # Estabilización completa, cambiar a captura
                self._sampling_phase = 'capturing'
                self._sampling_samples = []  # Reiniciar buffer de muestras
                self._sampling_temps = []    # Reiniciar buffer de temperaturas
                self.status_label.setText(
                    f"Muestra {self._sampling_row + 1}: Capturando 0/{self._samples_to_capture} muestras..."
                )
                return  # No capturar en este tick, esperar al siguiente

        # FASE 2: CAPTURA DE MUESTRAS
        elif self._sampling_phase == 'capturing':
            # Capturar muestra actual
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
                # Captura completada
                self._sampling_timer.stop()
                self._sampling_phase = None
                self._finish_measurement()
            else:
                # Mostrar progreso
                self.status_label.setText(
                    f"Muestra {self._sampling_row + 1}: Capturando {num_samples}/{self._samples_to_capture} muestras..."
                )

    def _finish_measurement(self):
        """Finaliza medición calcaulando factor K de la muestra.
        
        Procesa muestras capturadas, calcula moda de voltaje, compensa temperatura,
        aplica polinomio DFRobot y calcula K=EC_conocida/EC_polinomio.
        """
        row = self._sampling_row
        known_cond = self._sampling_known_cond
        samples = [s for s in self._sampling_samples if s is not None]
        temps = [t for t in self._sampling_temps if t is not None]

        for btn in self.measure_buttons.values():
            btn.setEnabled(True)

        if not samples:
            QMessageBox.warning(
                self, "Error",
                f"No se obtuvieron muestras para la muestra {row + 1}."
            )
            self.status_label.setText(f"✗ No se pudo medir la muestra {row + 1} - sin datos")
            self.status_label.setStyleSheet(
                "padding: 8px; background: #ffebee; border-radius: 3px; border-left: 4px solid #f44336;"
            )
            self.measured_labels[row].setText("---")
            self.measured_labels[row].setStyleSheet("background: #f9f9f9; border-radius: 3px;")
            self.temp_labels[row].setText("---")
            self.temp_labels[row].setStyleSheet("background: #f9f9f9; border-radius: 3px;")
            self.k_labels[row].setText("---")
            self.k_labels[row].setStyleSheet("background: #f9f9f9; border-radius: 3px;")
            self.estimated_labels[row].setText("---")
            self.estimated_labels[row].setStyleSheet("background: #f9f9f9; border-radius: 3px;")
            return

        measured_value = self._find_mode_with_tolerance(samples)

        if measured_value is None or measured_value == 0:
            QMessageBox.warning(self, "Error", "El promedio de la señal cruda es 0. Revise la conexión.")
            self.status_label.setText(f"✗ Señal cruda = 0 en muestra {row + 1}")
            self.status_label.setStyleSheet(
                "padding: 8px; background: #ffebee; border-radius: 3px; border-left: 4px solid #f44336;"
            )
            return

        temp_ref = TEMP_REF
        
        # Temperatura capturada automáticamente del sensor ESP32
        temp_read = (sum(temps) / len(temps)) if temps else None
        if temp_read is None:
            try:
                cr = getattr(self.app, 'current_reading', {}) or {}
                temp_read = cr.get('temp', None)
            except Exception:
                temp_read = None
        if temp_read is None:
            temp_read = temp_ref
        
        # Validar rango de temperatura
        if temp_read < -40 or temp_read > 125:
            temp_read = temp_ref  # Usar valor por defecto si está fuera de rango

        # Valores conocidos: usar voltaje crudo medido (sin normalizar a 25°C).
        # La normalización a 25°C se aplica solo en calibración de laboratorio.
        ec_measured = POLY_A * measured_value**3 - POLY_B * measured_value**2 + POLY_C * measured_value
        
        # Calcular K directo de la medición actual (sin mezclar con tablas previas)
        k_direct = known_cond / ec_measured if ec_measured > 0 else 1.0
        
        # Verificación
        ec_verify = k_direct * ec_measured  # ≈ known_cond

        k_value = k_direct

        # Calcular estimados
        estimated = k_value * ec_measured  # EC estimada a temperatura actual (debería ≈ known_cond)

        self.calibration_data[row] = {
            'known_cond': known_cond,
            'measured': measured_value,
            'k': k_value,
            'estimated': estimated,
            'temp': temp_read
        }

        # Recalcular rangos completos desde cero en cada nuevo punto medido
        try:
            self.generate_calibration_ranges(show_popup=False)
        except Exception:
            pass

        try:
            selected_idx = getattr(self.app, 'default_cal_table', 1)
            section_name = f"KNOWN_K_TABLE"  # Guardar en la sección correcta que lee load_known_k_table
            
            # Guardar como: VOLTAJE (measured_value) = K_factor
            # Esto es necesario para que interpolate_k() pueda encontrar el valor por voltaje
            voltage_key = f"{measured_value:.6f}"
            try:
                cfg_path = "calibration_ranges.cfg"
                cfg = configparser.ConfigParser()
                if os.path.exists(cfg_path):
                    try:
                        cfg.read(cfg_path, encoding='utf-8')
                    except Exception:
                        cfg.read(cfg_path)
                
                if section_name not in cfg:
                    cfg.add_section(section_name)
                
                cfg.set(section_name, voltage_key, str(round(k_value, 6)))
                
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    cfg.write(f)
            except Exception:
                pass

            try:
                if hasattr(self, 'app') and getattr(self.app, 'load_known_k_table', None):
                    self.app.use_user_calibration = True
                    self.app.load_known_k_table(force_load=True)
            except Exception:
                pass
        except Exception:
            pass

        self.measured_labels[row].setText(f"{measured_value:.1f}")
        self.measured_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
        k_truncated = int(k_value * 100) / 100
        self.k_labels[row].setText(f"{k_truncated:.1f}")
        self.k_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
        self.estimated_labels[row].setText(f"~{estimated:.1f}")
        self.estimated_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")
        self.temp_labels[row].setText(f"{temp_read:.1f}°C")
        self.temp_labels[row].setStyleSheet("background: #e8f5e9; border-radius: 3px;")

        noise = max(samples) - min(samples) if len(samples) > 1 else 0.0
        self.status_label.setText(
            f"✓ Muestra {row + 1}: raw={measured_value:.1f}V "
            f"({len(samples)} tomas, ruido={noise:.1f}), "
            f"EC@{temp_read:.1f}°C={estimated:.1f}µS"
        )
        self.status_label.setStyleSheet(
            "padding: 8px; background: #e8f5e9; border-radius: 3px; border-left: 4px solid #4caf50;"
        )

        try:
            if self.serial_reader and self.connected:
                k_truncated = int(k_value * 100) / 100
                self.serial_reader.send_command(f"STORE_K:{known_cond},{k_truncated:.2f}")
        except Exception:
            pass

        try:
            if self.serial_reader and self.connected:
                send_show_ec_to_esp32(self.serial_reader, temp_read, estimated)
        except Exception:
            pass

        # Recalcular y actualizar el display en vivo con el nuevo K
        try:
            if hasattr(self, 'app') and hasattr(self.app, 'apply_local_calibration_and_update'):
                self.app.apply_local_calibration_and_update()
        except Exception:
            pass

    def use_existing_user_calibration(self):
        """Usa calibración previa guardada sin capturar nuevos puntos.
        
        Carga USER_CALIBRATION del cfg, actualiza tabla K, sincroniza OLED
        y finaliza el diálogo usando los datos existentes.
        """
        try:
            cal_data = self.app.load_user_calibration()

            if not cal_data:
                QMessageBox.warning(
                    self,
                    "Sin calibración previa",
                    "No se encontraron datos en USER_CALIBRATION.\n"
                    "Debe medir al menos una muestra antes de finalizar."
                )
                return

            self.app.use_user_calibration = True

            try:
                self.app.load_known_k_table(force_load=True)
            except Exception:
                pass

            try:
                if hasattr(self.app, 'apply_local_calibration_and_update'):
                    self.app.apply_local_calibration_and_update()
            except Exception:
                pass

            pts = ", ".join(f"{c} µS" for c, _ in sorted(cal_data.items()))
            self.status_label.setText(f"✅ Usando calibración existente: {pts}")
            self.status_label.setStyleSheet(
                "padding: 8px; background: #fff3e0; border-radius: 3px; border-left: 4px solid #ff9800;"
            )

            # Calcular EC calibrada y enviar SHOW_EC al ESP32
            self._calculate_and_send_show_ec()

            self.accept()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo cargar la calibración:\n{e}")

    def _send_existing_k_table(self):
        """Envía tabla K guardada al ESP32.
        
        Lee CALIBRATION_K_TABLE del cfg, la serializa en JSON,
        y la sincroniza con el microcontrolador mediante UPDATE_K_TABLE.
        """
        import json
        import configparser
        import os
        
        try:
            k_table = {}
            cfg_path = "calibration_ranges.cfg"
            
            if not os.path.exists(cfg_path):
                print("Error: No existe calibration_ranges.cfg")
                return
            
            cfg = configparser.ConfigParser()
            try:
                cfg.read(cfg_path, encoding='utf-8')
            except Exception:
                cfg.read(cfg_path)
            
            # Leer tabla K desde CALIBRATION_K_TABLE
            if 'CALIBRATION_K_TABLE' in cfg:
                for ec_str, k_str in cfg.items('CALIBRATION_K_TABLE'):
                    try:
                        ec_val = float(ec_str)
                        k_val = float(k_str)
                        k_table[ec_val] = k_val
                        print(f"Cargando K desde cfg: EC={ec_val:.2f}, K={k_val:.6f}")
                    except Exception as e:
                        print(f"Error parseando CALIBRATION_K_TABLE: {e}")
                        pass
            
            if not k_table:
                print("Advertencia: CALIBRATION_K_TABLE vacía, intentando regenerar desde USER_CALIBRATION")
                # Fallback: regenerar desde USER_CALIBRATION
                if 'USER_CALIBRATION' in cfg:
                    coef = 0.02
                    temp_ref = 25.0
                    if 'TEMPERATURE' in cfg:
                        coef = float(cfg.get('TEMPERATURE', 'coef_temp', fallback='0.02'))
                    
                    for key, val in cfg.items('USER_CALIBRATION'):
                        try:
                            known_ec = float(key)
                            parts = [p.strip() for p in val.split(',') if p.strip()]
                            if len(parts) >= 2:
                                voltage_raw = float(parts[1])
                                # Compensar temperatura
                                factor_temp = 1.0
                                v_comp = float(voltage_raw) / factor_temp if factor_temp != 0 else float(voltage_raw)
                                # Aplicar polinomio
                                ec_poly_25c = 133.42 * v_comp**3 - 255.86 * v_comp**2 + 857.39 * v_comp
                                if ec_poly_25c < 0:
                                    ec_poly_25c = 0.0
                                # Calcular K
                                if ec_poly_25c > 0:
                                    k_factor = float(known_ec) / float(ec_poly_25c)
                                else:
                                    k_factor = 1.0
                                k_table[ec_poly_25c] = k_factor
                                print(f"Regenerado K desde USER_CAL: EC_ref={known_ec}, V={voltage_raw:.6f} → EC_poly={ec_poly_25c:.2f} → K={k_factor:.6f}")
                        except Exception as e:
                            print(f"Error regenerando: {e}")
            
            # Enviar tabla K al ESP32
            if k_table and hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                k_json = json.dumps(k_table)
                print(f"Enviando UPDATE_K_TABLE: {k_json}")
                self.serial_reader.send_command(f"UPDATE_K_TABLE:{k_json}")
                print(f"Tabla K sincronizada al ESP32: {len(k_table)} puntos")
            else:
                if not k_table:
                    print("Error: No hay tabla K para enviar")
                elif not hasattr(self, 'serial_reader'):
                    print("Error: No hay serial_reader")
                elif not self.serial_reader:
                    print("Error: serial_reader es None")
                elif not self.connected:
                    print("Error: No conectado")
        except Exception as e:
            print(f"Error en _send_existing_k_table: {e}")
            import traceback
            traceback.print_exc()

    def _send_all_calibration_data(self):  # NUEVO: Envía TODA la estructura de calibración al ESP32
        """Lee TODAS las secciones del cfg y las envía en una sola estructura"""
        import json
        import configparser
        import os
        
        try:
            cfg_path = "calibration_ranges.cfg"
            
            if not os.path.exists(cfg_path):
                print("Error: No existe calibration_ranges.cfg")
                return
            
            cfg = configparser.ConfigParser()
            try:
                cfg.read(cfg_path, encoding='utf-8')
            except Exception:
                cfg.read(cfg_path)
            
            # Empaquetar TODAS las secciones de calibración
            calibration_data = {}
            
            # 1. KNOWN_K_TABLE
            if 'KNOWN_K_TABLE' in cfg:
                calibration_data['known_k_table'] = {}
                for ec_str, k_str in cfg.items('KNOWN_K_TABLE'):
                    try:
                        calibration_data['known_k_table'][float(ec_str)] = float(k_str)
                    except:
                        pass
                print(f"Empaquetado KNOWN_K_TABLE: {len(calibration_data['known_k_table'])} puntos")
            
            # 2. KNOWN_K_RANGES
            if 'KNOWN_K_RANGES' in cfg:
                calibration_data['known_k_ranges'] = {}
                for range_str, k_str in cfg.items('KNOWN_K_RANGES'):
                    try:
                        parts = [p.strip() for p in range_str.split(',')]
                        if len(parts) == 2:
                            min_ec = float(parts[0])
                            max_ec = float(parts[1])
                            k_val = float(k_str)
                            calibration_data['known_k_ranges'][f"{min_ec},{max_ec}"] = k_val
                    except:
                        pass
                print(f"Empaquetado KNOWN_K_RANGES: {len(calibration_data['known_k_ranges'])} rangos")
            
            # 3. USER_CALIBRATION
            if 'USER_CALIBRATION' in cfg:
                calibration_data['user_calibration'] = {}
                for ec_str, cal_str in cfg.items('USER_CALIBRATION'):
                    try:
                        calibration_data['user_calibration'][float(ec_str)] = cal_str
                    except:
                        pass
                print(f"Empaquetado USER_CALIBRATION: {len(calibration_data['user_calibration'])} puntos calibrados")
            
            # 4. CALIBRATION_K_TABLE
            if 'CALIBRATION_K_TABLE' in cfg:
                calibration_data['calibration_k_table'] = {}
                for ec_str, k_str in cfg.items('CALIBRATION_K_TABLE'):
                    try:
                        calibration_data['calibration_k_table'][float(ec_str)] = float(k_str)
                    except:
                        pass
                print(f"Empaquetado CALIBRATION_K_TABLE: {len(calibration_data['calibration_k_table'])} puntos")
            
            # Enviar TODA la estructura al ESP32
            if calibration_data and hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                cal_json = json.dumps(calibration_data)
                print(f"Enviando UPDATE_CALIBRATION_ALL con {len(calibration_data)} secciones...")
                self.serial_reader.send_command(f"UPDATE_CALIBRATION_ALL:{cal_json}")
                print(f"✓ TODA la estructura de calibración sincronizada al ESP32")
            else:
                print("Error: No conectado o sin serial_reader para enviar calibración")
        except Exception as e:
            print(f"Error en _send_all_calibration_data: {e}")
            import traceback
            traceback.print_exc()

    def _send_oled_cal_done(self):  # NUEVO: Envía comando de finalización de calibración a OLED
        """Notifica al ESP32 que calibración completó y OLED debe mostrar datos"""
        try:
            if hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self.serial_reader, 'send_command'):
                print("→ Enviando OLED_CAL_DONE para actualizar pantalla...")
                result = self.serial_reader.send_command("OLED_CAL_DONE")
                print(f"✓ OLED_CAL_DONE enviado (resultado: {result})")
            else:
                print(f"⚠ Advertencia: serial_reader no disponible - sr={hasattr(self, 'serial_reader')}, sr_val={getattr(self, 'serial_reader', None)}")
        except Exception as e:
            print(f"✗ Error al enviar OLED_CAL_DONE: {e}")
            import traceback
            traceback.print_exc()

    def finalize_calibration(self):  # Valida datos y genera rangos de calibración
        calibrated_count = len(self.calibration_data)
        
        if calibrated_count == 0:
            QMessageBox.warning(self, "Advertencia", "No ha medido ninguna muestra. Por favor, mida al menos una muestra antes de finalizar.")
            return
        
        total_ranges = self.generate_calibration_ranges(show_popup=False)

        self.save_user_calibration()

        # Generar tabla K y guardarla localmente
        self._generate_and_send_k_table()

        # Dar más tiempo para que ESP32 procese completamente
        import time
        time.sleep(1.0)

        # Enviar comando de finalización
        self._send_oled_cal_done()

        if hasattr(self.app, 'unknown_cal_data'):
            self.app.unknown_cal_data = self.calibration_data
        self.app.use_user_calibration = True
        self.app.load_user_calibration()

        try:
            self.app.load_known_k_table(force_load=True)
        except Exception:
            pass

        try:
            if hasattr(self.app, 'apply_local_calibration_and_update'):
                self.app.apply_local_calibration_and_update()
        except Exception:
            pass

        try:
            if hasattr(self, 'app') and self.app is not None:
                self.app.pending_known_ranges_count = int(total_ranges)
        except Exception:
            pass

        self.accept()

    def _generate_and_send_k_table(self):  # Genera tabla K dinámica y la envía al ESP32
        """
        Calcula K dinámico para cada punto de calibración como: K = EC_reference / EC_polynomial
        Guarda en [CALIBRATION_K_TABLE] section del archivo cfg y envía al ESP32
        """
        import json
        import configparser
        import os
        
        try:
            # Recopilar datos de calibración
            measured = []
            for row, data in self.calibration_data.items():
                if data and isinstance(data, dict):
                    known_ec = data.get('known_cond')
                    ema_v = data.get('measured')
                    temp_v = data.get('temp')
                    if known_ec is not None and ema_v is not None:
                        measured.append((float(known_ec), float(ema_v), float(temp_v) if temp_v is not None else None))

            if not measured:
                return

            # Cargar configuración para obtener coeficientes de temperatura
            coef = 0.02
            temp_ref = 25.0
            try:
                cfgp = configparser.ConfigParser()
                if os.path.exists('calibration_ranges.cfg'):
                    try:
                        cfgp.read('calibration_ranges.cfg', encoding='utf-8')
                    except Exception:
                        cfgp.read('calibration_ranges.cfg')
                    if 'TEMPERATURE' in cfgp:
                        coef = float(cfgp.get('TEMPERATURE', 'coef_temp', fallback=str(coef)))
                        temp_ref = float(cfgp.get('TEMPERATURE', 'temp_ref', fallback=str(temp_ref)))
            except Exception:
                pass

            # Calcular K para cada punto medido
            k_table = {}  # {ec_conocida: k_factor} - SOLO valores calibrados, sin interpolar
            
            for known_ec, voltage_raw, temp_meas in measured:
                try:
                    if voltage_raw == 0:
                        continue

                    # Temperatura para compensación
                    if temp_meas is not None:
                        factor_temp = 1.0 + float(coef) * (float(temp_meas) - float(temp_ref))
                        if factor_temp < 0.2:
                            factor_temp = 0.2
                        elif factor_temp > 2.0:
                            factor_temp = 2.0
                    else:
                        factor_temp = 1.0

                    # Voltaje compensado a 25°C
                    v_comp = float(voltage_raw) / factor_temp if factor_temp != 0 else float(voltage_raw)
                    
                    # Aplicar polinomio DFRobot: EC polynomial @ 25°C
                    ec_poly_25c = 133.42 * v_comp**3 - 255.86 * v_comp**2 + 857.39 * v_comp
                    if ec_poly_25c < 0:
                        ec_poly_25c = 0.0

                    # Calcular K: factor de ajuste calibración
                    # K transforma el polinomio (base) en la EC conocida (referencia)
                    if ec_poly_25c > 0:
                        k_factor = float(known_ec) / float(ec_poly_25c)
                    else:
                        k_factor = 1.0
                    
                    # CAMBIO: Guardar con EC_conocida como clave (solo valores que existen en calibración)
                    k_table[float(known_ec)] = k_factor
                    print(f"K Direct: EC_ref={known_ec}µS, V_raw={voltage_raw:.6f}V @ {temp_meas}°C → EC_poly={ec_poly_25c:.2f}µS → K={k_factor:.6f}")

                except Exception as e:
                    print(f"Error calculating K for {known_ec}: {e}")
                    continue

            if not k_table:
                return

            # Guardar tabla K en calibration_ranges.cfg como [CALIBRATION_K_TABLE]
            try:
                cfg_path = "calibration_ranges.cfg"
                cfg = configparser.ConfigParser()
                if os.path.exists(cfg_path):
                    try:
                        cfg.read(cfg_path, encoding='utf-8')
                    except Exception:
                        cfg.read(cfg_path)

                # Reemplazar o crear sección [CALIBRATION_K_TABLE]
                if 'CALIBRATION_K_TABLE' in cfg:
                    cfg.remove_section('CALIBRATION_K_TABLE')
                cfg.add_section('CALIBRATION_K_TABLE')

                # Grabar cada entrada: EC_conocida = K_factor
                for ec_key in sorted(k_table.keys()):
                    k_val = k_table[ec_key]
                    cfg.set('CALIBRATION_K_TABLE', f"{ec_key:.6f}", f"{k_val:.6f}")

                # Guardar archivo
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    cfg.write(f)

                print(f"K table guardada en {cfg_path}: {len(k_table)} puntos DIRECTOS (sin interpolar)")

                # Enviar tabla K al ESP32 para sincronización
                try:
                    if hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                        k_json = json.dumps(k_table)
                        self.serial_reader.send_command(f"UPDATE_K_TABLE:{k_json}")
                        print(f"Tabla K sincronizada al ESP32: {len(k_table)} puntos DIRECTOS")
                except Exception as sync_err:
                    print(f"No se pudo sincronizar tabla K al ESP32: {sync_err}")

            except Exception as e:
                print(f"Error saving K table to cfg: {e}")

        except Exception as e:
            print(f"Error en _generate_and_send_k_table: {e}")

    def _calculate_and_send_show_ec(self):
        """Sincroniza OLED con el mismo EC ya visible en la app (fuente única)."""
        try:
            if not hasattr(self, 'app') or not self.app:
                return
            cr = getattr(self.app, 'current_reading', {})
            ec_ui = cr.get('sensor')
            t = cr.get('temp') or 25.0
            if ec_ui is None:
               return
            if hasattr(self, 'serial_reader') and self.serial_reader:
                send_show_ec_to_esp32(self.serial_reader, t, float(ec_ui))
        except Exception as e:
            print(f"Error calc_show_ec: {e}")

    def _send_existing_k_table_from_cfg(self):  # Envía K_TABLE existente desde cfg al ESP32
        """Lee K_TABLE del cfg y la envía al ESP32 via UPDATE_K_TABLE (corto, sin saturar buffer)"""
        import json
        import configparser
        import os
        
        try:
            cfg_path = "calibration_ranges.cfg"
            if not os.path.exists(cfg_path):
                print("Advertencia: No existe calibration_ranges.cfg, no se envía K_TABLE")
                return
            
            cfg = configparser.ConfigParser()
            try:
                cfg.read(cfg_path, encoding='utf-8')
            except Exception:
                cfg.read(cfg_path)
            
            # Leer CALIBRATION_K_TABLE del cfg
            k_table = {}
            if 'CALIBRATION_K_TABLE' in cfg:
                for ec_str, k_str in cfg.items('CALIBRATION_K_TABLE'):
                    try:
                        k_table[float(ec_str)] = float(k_str)
                    except:
                        pass
            
            if not k_table:
                print("Advertencia: CALIBRATION_K_TABLE vacía o no existe")
                return
            
            # Enviar tabla K al ESP32 via UPDATE_K_TABLE (corto, seguro)
            try:
                if hasattr(self, 'serial_reader') and self.serial_reader and hasattr(self, 'connected') and self.connected:
                    k_json = json.dumps(k_table)
                    self.serial_reader.send_command(f"UPDATE_K_TABLE:{k_json}")
                    print(f"✓ K_TABLE enviada al ESP32: {len(k_table)} puntos desde cfg")
                else:
                    print("Advertencia: serial_reader no disponible - K_TABLE no enviada")
            except Exception as sync_err:
                print(f"Error enviando K_TABLE al ESP32: {sync_err}")
                
        except Exception as e:
            print(f"Error en _send_existing_k_table_from_cfg: {e}")

    def _collect_all_known_points(self, archivo_cfg="calibration_ranges.cfg"):
        """Combina puntos históricos y nuevos sin duplicados por conductividad."""
        merged_points = {}

        try:
            cfg = configparser.ConfigParser()
            if os.path.exists(archivo_cfg):
                cfg.read(archivo_cfg, encoding='utf-8')
                if 'USER_CALIBRATION' in cfg:
                    for key, val in cfg.items('USER_CALIBRATION'):
                        try:
                            cond = float(key)
                            parts = [p.strip() for p in val.split(',') if p.strip()]
                            k_val = float(parts[0])
                            raw_v = float(parts[1]) if len(parts) > 1 else None
                            if raw_v is not None:
                                merged_points[cond] = {
                                    'voltage': float(raw_v),
                                    'k': float(k_val),
                                    'cond': float(cond)
                                }
                        except Exception:
                            continue
        except Exception:
            pass

        for _, data in self.calibration_data.items():
            try:
                cond = float(data['known_cond'])
                measured_v = float(data['measured'])
                k_val = float(data['k'])
                merged_points[cond] = {
                    'voltage': measured_v,
                    'k': k_val,
                    'cond': cond
                }
            except Exception:
                continue

        return list(merged_points.values())

    def generate_calibration_ranges(self, show_popup=True):
        """Regenera todos los rangos desde cero usando históricos + nuevos."""
        if not self.app:
            return 0

        try:
            points = self._collect_all_known_points()
            if not points:
                return 0

            points_asc = sorted(points, key=lambda x: x['voltage'])
            voltage_ranges = []
            voltage_margin = 0.05

            if len(points_asc) == 1:
                single_v = points_asc[0]['voltage']
                voltage_ranges.append({
                    'v_min': 0.0,
                    'v_max': max(single_v + voltage_margin, single_v * 1.05),
                    'k': points_asc[0]['k'],
                    'cond': points_asc[0]['cond'],
                    'voltage': single_v
                })
            else:
                for i, point in enumerate(points_asc):
                    if i == 0:
                        v_min = 0.0
                        v_max = (points_asc[i]['voltage'] + points_asc[i + 1]['voltage']) / 2.0
                    elif i == len(points_asc) - 1:
                        v_min = (points_asc[i - 1]['voltage'] + points_asc[i]['voltage']) / 2.0
                        v_max = points_asc[i]['voltage'] + voltage_margin
                    else:
                        v_min = (points_asc[i - 1]['voltage'] + points_asc[i]['voltage']) / 2.0
                        v_max = (points_asc[i]['voltage'] + points_asc[i + 1]['voltage']) / 2.0

                    if v_max < v_min:
                        v_min, v_max = v_max, v_min

                    voltage_ranges.append({
                        'v_min': v_min,
                        'v_max': v_max,
                        'k': point['k'],
                        'cond': point['cond'],
                        'voltage': point['voltage']
                    })

            points_desc = sorted(points_asc, key=lambda x: x['voltage'], reverse=True)
            voltage_ranges_desc = sorted(voltage_ranges, key=lambda x: x['voltage'], reverse=True)

            self.app.known_k_ranges = {}
            for rng in voltage_ranges_desc:
                v_ref = (rng['v_min'] + rng['v_max']) / 2.0
                self.app.known_k_ranges[v_ref] = (rng['k'], rng['v_min'], rng['v_max'])

            self.app.known_k_table = {}
            for point in points_desc:
                self.app.known_k_table[point['voltage']] = point['k']

            self._save_voltage_ranges_to_cfg(voltage_ranges_desc, points_desc)

            total_ranges = len(voltage_ranges_desc)
            if show_popup:
                QMessageBox.information(
                    self,
                    "Rangos actualizados",
                    f"Total de rangos existentes: {total_ranges}"
                )

            return total_ranges
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Error", f"No se pudieron regenerar los rangos: {e}")
            return 0

    def _save_voltage_ranges_to_cfg(self, voltage_ranges, points, archivo_cfg="calibration_ranges.cfg"):
        """Guarda rangos de voltaje calibrados en cfg.
        
        Persiste KNOWN_K_RANGES (rangos de voltaje con K) y KNOWN_K_TABLE
        (tabla de puntos con K) en las secciones correspondientes del cfg.
        """
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read(archivo_cfg, encoding='utf-8')
            
            section = "KNOWN_K_RANGES"
            if section not in config:
                config.add_section(section)
            else:
                for key in list(config[section].keys()):
                    config.remove_option(section, key)
            
            for rng in sorted(voltage_ranges, key=lambda x: x.get('voltage', 0.0), reverse=True):
                key = f"{rng['v_min']:.6f},{rng['v_max']:.6f}"
                config.set(section, key, f"{rng['k']:.8f}")
            
            section_table = "KNOWN_K_TABLE"
            if section_table not in config:
                config.add_section(section_table)
            else:
                for key in list(config[section_table].keys()):
                    config.remove_option(section_table, key)
            
            for point in sorted(points, key=lambda x: x['voltage'], reverse=True):
                key = f"{point['voltage']:.6f}"
                config.set(section_table, key, f"{point['k']:.8f}")
            
            with open(archivo_cfg, 'w', encoding='utf-8') as f:
                config.write(f)
            
        except Exception:
            pass
    
    def sync_k_values_to_esp32(self):
        """Sincroniza rangos K al ESP32 mediante conexión serial.
        
        Construye payload con rangos de voltaje y factores K, empaqueándolos
        en comando SET_RANGES para actualizar calibración del dispositivo.
        """
        if not self.app or not self.app.connected or not self.app.serial_reader:
            return
        
        if not hasattr(self.app, 'known_k_ranges') or not self.app.known_k_ranges:
            return
        
        try:
            ranges_parts = []
            
            for conductivity, data in sorted(self.app.known_k_ranges.items()):
                try:
                    if isinstance(data, tuple) and len(data) == 3:
                        k_val, min_c, max_c = data
                        range_str = f"{min_c:.6f},{max_c:.6f},{k_val:.8f}"
                        ranges_parts.append(range_str)
                    elif isinstance(data, (int, float)):
                        range_str = f"0.0,{conductivity * 2.0:.6f},{float(data):.8f}"
                        ranges_parts.append(range_str)
                except Exception:
                    continue
            
            if not ranges_parts:
                return
            
            ranges_payload = ";".join(ranges_parts)
            cmd = f"SET_RANGES:{ranges_payload}"
            
            success = self.app.serial_reader.send_command(cmd)
            
            if success:
                time.sleep(0.3)
            else:
                QMessageBox.warning(self, "Advertencia", "No se pudo enviar el comando al ESP32.\nVerifique la conexión.")
        
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error al sincronizar con ESP32: {e}")

    def closeEvent(self, event):
        """Maneja evento de cierre del diálogo.
        
        Ejecuta limpieza de recursos cuando el usuario cierra la ventana,
        llamando a la implementación base de QDialog.
        """
        super().closeEvent(event)

    def on_cal_message(self, obj):
        """Procesa mensajes de calibración del ESP32.
        
        Slot para recibir notificaciones de calibración desde el dispositivo,
        permitiendo actualización dinámica durante procesos de medición.
        """
        pass


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
            cfg_path = "calibration_ranges.cfg"
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
            if os.path.exists('calibration_ranges.cfg'):
                cfg.read('calibration_ranges.cfg', encoding='utf-8')
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
            cfg_path = "calibration_ranges.cfg"
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
        """
        self.setWindowTitle("EMA - Conductividad y Temperatura")
        self.resize(1100, 780)

        main_layout = QVBoxLayout()
        controls_layout = QHBoxLayout()
        controls_layout2 = QHBoxLayout()
        buttons_layout = QHBoxLayout()

        self.status_label = QLabel("Estado: Desconectado")
        controls_layout.addWidget(self.status_label)
        controls_layout.addStretch()
        
        self.update_cal_btn = QPushButton("Actualizar Calibración")
        self.update_cal_btn.setMaximumWidth(190)
        self.update_cal_btn.setStyleSheet("""
            QPushButton {
                background: #ff9800;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 9pt;
                font-weight: bold;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background: #f57c00;
            }
        """)
        self.update_cal_btn.setToolTip(
            "Actualizar la tabla VOLTAGE_RANGES usando un conductímetro comercial en paralelo"
        )
        self.update_cal_btn.clicked.connect(self.open_calibration_update)
        controls_layout.addWidget(self.update_cal_btn)

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

        self.interval_label = QLabel("Intervalo (s):")
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 60.0)
        self.interval_spin.setValue(2.0)
        self.interval_btn = QPushButton("Aplicar")

        controls_layout2.addWidget(self.interval_label)
        controls_layout2.addWidget(self.interval_spin)
        controls_layout2.addWidget(self.interval_btn)
        controls_layout2.addSpacing(16)
        self.cal_mode_label = QLabel("Calibración: Laboratorio")
        self.cal_mode_label.setStyleSheet("color: #444; font-weight: bold;")
        self.cal_mode_switch = AnimatedToggle()
        self.cal_mode_switch_text = QLabel("Valores conocidos")
        self.cal_mode_switch_text.setStyleSheet("color: #333;")
        self.cal_mode_switch.setChecked(False)
        self.cal_mode_switch.setEnabled(False)
        self.cal_mode_switch.setToolTip("Desactivado: Laboratorio | Activado: Valores conocidos")
        controls_layout2.addWidget(self.cal_mode_label)
        controls_layout2.addWidget(self.cal_mode_switch)
        controls_layout2.addWidget(self.cal_mode_switch_text)
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
        
        self.led = QLabel()
        self.led.setFixedSize(16, 16)
        self.led.setStyleSheet("background: #b30000; border-radius: 8px;")

        layout_vp.addWidget(self.test_name_label)
        
        col_layout = QHBoxLayout()
        
        col1 = QVBoxLayout()
        col1.addWidget(sensor_label_text)
        col1.addWidget(self.value_sensor_label)
        
        col2 = QVBoxLayout()
        col2.addWidget(temp_label_text)
        col2.addWidget(self.value_temp_label)
        
        col3 = QVBoxLayout()
        col3.addWidget(k_label_text)
        col3.addWidget(self.k_label)
        
        col_layout.addLayout(col1)
        col_layout.addLayout(col2)
        col_layout.addLayout(col3)
        
        layout_vp.addLayout(col_layout)
        layout_vp.addStretch()
        layout_vp.addWidget(QLabel("Estado:"))
        layout_vp.addWidget(self.led)
        self.value_panel.setLayout(layout_vp)

        self.start_btn = QPushButton("Iniciar Lectura")
        self.stop_btn = QPushButton("Detener Lectura")
        self.download_btn = QPushButton("Descargar CSV")
        self.plot_csv_btn = QPushButton("Gráfica CSV")
        self.reset_factory_btn = QPushButton("Restablecer valores de fábrica")
        
        for btn in [self.start_btn, self.stop_btn, self.download_btn, self.plot_csv_btn]:
            btn.setMinimumHeight(40)
            btn.setFont(QFont("Arial", 10, QFont.Bold))
        
        self.reset_factory_btn.setMinimumHeight(40)
        self.reset_factory_btn.setFont(QFont("Arial", 10, QFont.Bold))
        self.reset_factory_btn.setStyleSheet("""
            QPushButton {
                background: #f0f0f0;
                color: #222;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #e8e8e8;
                border: 2px solid #888;
            }
        """)
        self.reset_factory_btn.clicked.connect(self.reset_factory_values)
        
        buttons_layout.addWidget(self.start_btn)
        buttons_layout.addWidget(self.stop_btn)
        buttons_layout.addWidget(self.download_btn)
        buttons_layout.addWidget(self.plot_csv_btn)
        buttons_layout.addWidget(self.reset_factory_btn)

        self.plot_sensor = pg.PlotWidget(title="Conductividad (uS) - Tiempo Real")
        self.plot_sensor.showGrid(x=True, y=True)
        self.plot_sensor.setLabel('left', 'Conductividad', 'uS')
        self.plot_sensor.setLabel('bottom', 'Muestras')
        self.plot_sensor.getAxis('left').enableAutoSIPrefix(False)
        try:
            self.plot_sensor.getAxis('left').setTickSpacing(major=1.0, minor=0.1)
        except Exception:
            pass
        self.plot_sensor.setBackground('w')
        self.curve_sensor = self.plot_sensor.plot(pen=pg.mkPen('r', width=2), symbol='o', symbolSize=5, symbolPen='r', symbolBrush='r')
        self.curve_sensor.setData([0])
        
        self.plot_temp = pg.PlotWidget(title="Temperatura (°C) - Tiempo Real")
        self.plot_temp.showGrid(x=True, y=True)
        self.plot_temp.setLabel('left', 'Temperatura', '°C')
        self.plot_temp.setLabel('bottom', 'Muestras')
        self.plot_temp.getAxis('left').enableAutoSIPrefix(False)
        try:
            self.plot_temp.getAxis('left').setTickSpacing(major=1.0, minor=0.1)
        except Exception:
            pass
        self.plot_temp.setBackground('w')
        self.curve_temp = self.plot_temp.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=5, symbolPen='b', symbolBrush='b')
        self.curve_temp.setData([0])

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

        main_layout.addLayout(controls_layout)
        main_layout.addLayout(controls_layout2)
        main_layout.addWidget(self.value_panel)
        main_layout.addLayout(buttons_layout)
        main_layout.addWidget(self.plot_sensor)
        main_layout.addWidget(self.plot_temp)
        main_layout.addWidget(self.stats_panel)
        self.setLayout(main_layout)

    def setup_variables(self):
        """Inicializa variables de estado, timers y estructuras de datos.
        
        Configura listas para muestras, timers de GUI y sincronización,
        estructuras de calibración y referencias a SerialReader.
        """
        self.sensor_data = []
        self.temp_data = []
        self.logger = DataLogger()
        self.available_ports = []
        self.serial_reader = None
        self.reading_active = False
        self.connected = False
        self.data_received = False
        self.last_data_time = time.time()
        self.current_test_name = ""
        self.test_start_time = None
        self.line_queue = deque()
        self.queue_lock = threading.Lock()
        self.current_reading = {'sensor': None, 'temp': None, 'k': None, 'ema': None, 'raw': None, 'signal': None}
        self.esp32_sensor_raw = None  # Guardar sensor raw del ESP32 para OLED (antes de recalcular)
        self._last_oled_sync_payload = None
        self.cal_dialog = None
        self.calibration_active = False
        self.prev_interval = None
        self.prev_mode_text = None
        self.cal_points = {}
        self.cal_m = None
        self.cal_b = None
        self.cal_points = {}
        self.cal_m = None
        self.default_cal_table = 1
        self.loaded_calibration = {}
        
        self.use_user_calibration = False
        self.user_calibration = {}
        self.user_calibration_raw = {}
        self.known_k_ranges = {}
        self.calibration_mode = "laboratory"
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
        
        self.debug_timer = QTimer(self)
        self.debug_timer.setInterval(5000)
        self.debug_timer.timeout.connect(self.check_serial_health)
        
        self.rtc_timer = QTimer(self)
        self.rtc_timer.setInterval(30_000)
        self.rtc_timer.timeout.connect(self._rtc_check_timer_timeout)
        self.rtc_sync_attempts = 0
        self.rtc_max_attempts = 3

    def open_calibration_update(self):
        """Abre diálogo de actualización de calibración con protección.
        
        Solicita contraseña de laboratorio, valida conexión y lectura de voltaje,
        abriendo LaboratoryCalibrationDialog si autorización es exitosa.
        """
        if not self.connected:
            QMessageBox.warning(
                self, "Sin conexión",
                "Debe estar conectado al ESP32 para actualizar la calibración.\n"
                "El sensor debe estar transmitiendo datos de voltaje crudo."
            )
            return

        raw = self.current_reading.get('raw')
        if raw is None:
            QMessageBox.warning(
                self, "Sin datos",
                "Aún no se han recibido datos del ESP32.\n"
                "Inicie la lectura y espere a recibir al menos un dato antes de calibrar."
            )
            return

        # Mostrar advertencia que esto es solo para laboratorio
        reply = QMessageBox.warning(
            self, "⚠️ Calibración de Laboratorio",
            "Esta calibración está reservada EXCLUSIVAMENTE para personal de laboratorio.\n\n"
            "Solo proceed si está autorizado para calibrar el equipo.",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel
        )
        
        if reply != QMessageBox.Ok:
            return
        
        # Pedir contraseña
        password_dialog = LaboratoryPasswordDialog()
        if password_dialog.exec_() == QDialog.Accepted:
            # Contraseña correcta, abrir calibración de laboratorio
            lab_dialog = LaboratoryCalibrationDialog(self, num_points=LAB_CALIBRATION_CONFIG['num_points'])
            lab_dialog.set_serial(self.serial_reader, self.connected, self)
            self.lab_dialog = lab_dialog
            
            lab_dialog.exec_()
            
            self.raise_()
            self.activateWindow()

    def reset_factory_values(self):
        """Restablece valores de fábrica limpiando calibraciones.
        
        Elimina USER_CALIBRATION, KNOWN_K_TABLE y datos de calibración personalizada
        del cfg local y envía comandos CAL_RESET al ESP32 para restaurar estado.
        """

        reply = QMessageBox.question(
            self, "Confirmar",
            "¿Está seguro de restablecer los valores de fábrica?\n\n"
            "Se usará la tabla de valores original.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.unknown_cal_data = {}
        self.user_calibration = {}
        self.user_calibration_raw = {}
        self.known_k_ranges = {}
        self.known_k_table = {}
        self.loaded_calibration = {}
        self.use_user_calibration = False
        self.cal_m = None
        self.cal_b = None
        self.cal_points = {}

        self.status_label.setText("Restableciendo valores de fábrica...")

        cfg_path = "calibration_ranges.cfg"
        try:
            if os.path.exists(cfg_path):
                cfg = configparser.ConfigParser()
                cfg.read(cfg_path, encoding='utf-8')

                changed = False
                if 'USER_CALIBRATION' in cfg:
                    cfg.remove_section('USER_CALIBRATION')
                    changed = True
                if 'KNOWN_K_TABLE' in cfg:
                    cfg.remove_section('KNOWN_K_TABLE')
                    changed = True
                if 'MEASURED_VOLTAGE_RANGES' in cfg:
                    cfg.remove_section('MEASURED_VOLTAGE_RANGES')
                    changed = True

                if changed:
                    with open(cfg_path, 'w', encoding='utf-8') as f:
                        cfg.write(f)
        except Exception:
            pass

        if self.connected and self.serial_reader:
            try:
                self.serial_reader.send_command("CAL_RESET")
                self.serial_reader.send_command("SET_K:0")
                time.sleep(0.2)
                self.serial_reader.send_command("LOAD_CAL_TABLE:1")
            except Exception:
                pass


        self.status_label.setText("Valores de fábrica restablecidos")

    def connect_signals(self):
        """Conecta señales de botones a sus métodos manejadores.
        
        Vincula eventos clicked de todos los botones principales con los slots
        correspondientes, configurando también estados iniciales de habilitación.
        """
        self.start_btn.clicked.connect(self.start_reading)
        self.stop_btn.clicked.connect(self.stop_reading)
        self.download_btn.clicked.connect(self.download_csv)
        self.plot_csv_btn.clicked.connect(self.plot_csv_data)
        self.interval_btn.clicked.connect(self.apply_interval)
        self.cal_mode_switch.toggled.connect(self.on_calibration_switch_toggled)
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

    def on_calibration_switch_toggled(self, checked: bool):
        if self._cal_mode_transition_in_progress or self._cal_mode_switch_internal_change:
            return

        current_known = (self.calibration_mode == "known")
        requested_known = bool(checked)

        if requested_known == current_known:
            return

        target_mode = "known" if requested_known else "laboratory"

        target_text = "Valores conocidos" if target_mode == "known" else "Valores de laboratorio"
        confirm = QMessageBox.question(
            self,
            "Confirmar cambio de modo",
            f"¿Desea cambiar al modo '{target_text}'?\n\n"
            "Se detendrá la medición actual.\n"
            "Para una nueva medida, presione 'Iniciar Lectura' y asigne un nuevo nombre.",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel
        )

        if confirm != QMessageBox.Ok:
            try:
                self._cal_mode_switch_internal_change = True
                self.cal_mode_switch.setChecked(current_known)
            finally:
                self._cal_mode_switch_internal_change = False
            return

        changed = self._transition_calibration_mode(target_mode)
        if changed:
            self.calibration_mode = target_mode
            self._is_known_mode_active = requested_known
        else:
            try:
                self._cal_mode_switch_internal_change = True
                self.cal_mode_switch.setChecked(current_known)
            finally:
                self._cal_mode_switch_internal_change = False

    def _transition_calibration_mode(self, target_mode: str):
        self._cal_mode_transition_in_progress = True
        try:
            # 1) Detener medición en curso
            if self.reading_active:
                self.finalize_test()

            # 2) Cambiar modo y vista lógica de tabla
            self.set_calibration_mode_ui(target_mode, send_to_device=True)
            try:
                self.status_label.setText(
                    "Conectado - Calibración: Valores conocidos" if target_mode == "known"
                    else "Conectado - Calibración: Valores de laboratorio"
                )
            except Exception:
                pass

            if target_mode == "known":
                self.use_user_calibration = True
                try:
                    self.load_known_k_table(force_load=True)
                except Exception:
                    pass
            else:
                self.use_user_calibration = False
                try:
                    self.load_known_k_table(force_load=True)
                except Exception:
                    pass
                try:
                    self.load_calibration_table()
                except Exception:
                    pass

            try:
                if hasattr(self, 'apply_local_calibration_and_update'):
                    self.apply_local_calibration_and_update()
            except Exception:
                pass

            # 3) No iniciar medición automáticamente: queda en estado detenido
            try:
                self.start_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
            except Exception:
                pass
            return True
        except Exception as e:
            try:
                self.status_label.setText(f"Conectado - Error al cambiar modo: {e}")
            except Exception:
                pass
            return False
        finally:
            self._cal_mode_transition_in_progress = False

    def set_calibration_mode_ui(self, mode: str, send_to_device: bool = True):
        mode = "known" if mode == "known" else "laboratory"
        checked = (mode == "known")
        try:
            self._cal_mode_switch_internal_change = True
            self.cal_mode_switch.blockSignals(True)
            self.cal_mode_switch.setChecked(checked)
        finally:
            self.cal_mode_switch.blockSignals(False)
            self._cal_mode_switch_internal_change = False

        self.cal_mode_label.setText(
            "Calibración: Valores conocidos" if checked else "Calibración: Laboratorio"
        )

        # Mantener estado local consistente en cada cambio confirmado
        self.calibration_mode = mode
        self._is_known_mode_active = checked

        if send_to_device:
            self._send_calibration_mode_command(mode, update_status=False)

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
            success = self.serial_reader.send_command(f"INTERVAL:{interval}")
            if success:
                self.status_label.setText(f"Conectado - Intervalo: {interval}s")
                try:
                    self.logger.set_save_interval(interval)
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
            if not os.path.exists('calibration_ranges.cfg'):
                return {}
            
            config = configparser.ConfigParser()
            config.read('calibration_ranges.cfg')
            
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
        """Carga tabla conocida de factores K y rangos de calibración.
        
        Intenta cargar desde múltiples fuentes en orden de precedencia:
        1. KNOWN_K_RANGES (cuando calibración de usuario está activa)
        2. LABORATORY_CALIBRATION_RANGES (si no hay calibración de usuario)
        3. KNOWN_K_TABLE
        4. USER_CALIBRATION
        5. Genera automáticamente rangos si es necesario
        """
        if not force_load and not self.use_user_calibration:
            self.known_k_ranges = {}
            self.known_k_table = {}
            return {}
        
        try:
            if not os.path.exists(archivo_cfg):
                self.known_k_ranges = {}
                self.known_k_table = {}
                return {}
            
            cfg = configparser.ConfigParser()
            try:
                cfg.read(archivo_cfg, encoding='utf-8')
            except Exception:
                cfg.read(archivo_cfg)

            # Prioridad de usuario: si está activa la calibración de usuario,
            # cargar primero sus rangos KNOWN_K_RANGES para evitar mezclar con laboratorio.
            if self.use_user_calibration and 'KNOWN_K_RANGES' in cfg and len(cfg.items('KNOWN_K_RANGES')) > 0:
                ranges_table = {}
                for key, val in cfg.items('KNOWN_K_RANGES'):
                    try:
                        parts = key.split(',')
                        if len(parts) == 2:
                            v_min = float(parts[0].strip())
                            v_max = float(parts[1].strip())
                            k_val = float(val.strip())
                            v_ref = (v_min + v_max) / 2.0
                            ranges_table[v_ref] = (k_val, v_min, v_max)
                    except Exception:
                        continue

                if ranges_table:
                    self.known_k_ranges = ranges_table
                    if 'KNOWN_K_TABLE' in cfg:
                        self._load_known_k_table_for_interpolation(cfg)
                    else:
                        self.known_k_table = {v_ref: data[0] for v_ref, data in ranges_table.items()}

                    self.use_user_calibration = True
                    return ranges_table

            # Prioridad de laboratorio
            if 'LABORATORY_CALIBRATION_RANGES' in cfg and len(cfg.items('LABORATORY_CALIBRATION_RANGES')) > 0:
                ranges_table = {}
                for key, val in cfg.items('LABORATORY_CALIBRATION_RANGES'):
                    try:
                        parts = key.split(',')
                        if len(parts) == 2:
                            v_min = float(parts[0].strip())
                            v_max = float(parts[1].strip())
                            k_val = float(val.strip())
                            v_ref = (v_min + v_max) / 2.0
                            ranges_table[v_ref] = (k_val, v_min, v_max)
                    except Exception:
                        continue
                
                if ranges_table:
                    self.known_k_ranges = ranges_table
                    if 'LABORATORY_CALIBRATION' in cfg:
                        # Cargar tabla K del laboratorio para interpolación
                        self.known_k_table = {}
                        for key, val in cfg.items('LABORATORY_CALIBRATION'):
                            try:
                                parts = val.split(',')
                                if len(parts) >= 2:
                                    k_val = float(parts[0].strip())
                                    measured_v = float(parts[1].strip())
                                    self.known_k_table[measured_v] = k_val
                                else:
                                    k_val = float(parts[0].strip())
                                    self.known_k_table[0.0] = k_val
                            except Exception:
                                continue
                    else:
                        self.known_k_table = {v_ref: data[0] for v_ref, data in ranges_table.items()}
                    
                    self.use_user_calibration = False
                    print(f"✓ Calibración de LABORATORIO cargada: {len(ranges_table)} rangos")
                    return ranges_table

            if 'KNOWN_K_RANGES' in cfg and len(cfg.items('KNOWN_K_RANGES')) > 0:
                ranges_table = {}
                for key, val in cfg.items('KNOWN_K_RANGES'):
                    try:
                        parts = key.split(',')
                        if len(parts) == 2:
                            v_min = float(parts[0].strip())
                            v_max = float(parts[1].strip())
                            k_val = float(val.strip())
                            v_ref = (v_min + v_max) / 2.0
                            ranges_table[v_ref] = (k_val, v_min, v_max)
                    except Exception:
                        continue
                
                if ranges_table:
                    self.known_k_ranges = ranges_table
                    if 'KNOWN_K_TABLE' in cfg:
                        self._load_known_k_table_for_interpolation(cfg)
                    else:
                        self.known_k_table = {v_ref: data[0] for v_ref, data in ranges_table.items()}
                    
                    self.use_user_calibration = True
                    return ranges_table

            if 'KNOWN_K_TABLE' in cfg and len(cfg.items('KNOWN_K_TABLE')) > 0:
                self._load_known_k_table_for_interpolation(cfg)
                if self.known_k_table:
                    self._generate_ranges_from_table()
                    self.use_user_calibration = True
                    return self.known_k_ranges

            if 'MEASURED_VOLTAGE_RANGES' in cfg:
                ranges_table = {}
                for key, val in cfg.items('MEASURED_VOLTAGE_RANGES'):
                    try:
                        known_cond = int(float(key))
                        parts = val.split(',')
                        if len(parts) >= 2:
                            k_val = float(parts[0].strip())
                            volt_range = parts[1].strip()
                            min_v, max_v = [float(x) for x in volt_range.split('-')]
                            ranges_table[known_cond] = (k_val, min_v, max_v)
                        else:
                            k_val = float(parts[0].strip())
                            ranges_table[known_cond] = (k_val, 0.0, 10.0)
                    except Exception:
                        continue
                
                self.known_k_ranges = ranges_table
                self.known_k_table = {k: v[0] for k, v in ranges_table.items()}
                self.use_user_calibration = True
                return ranges_table
            
            elif 'USER_CALIBRATION' in cfg:
                user_cal_data = {}
                for key, val in cfg.items('USER_CALIBRATION'):
                    try:
                        known_cond = int(float(key))
                        parts = val.split(',')
                        if len(parts) >= 2:
                            k_val = float(parts[0].strip())
                            raw_v = float(parts[1].strip())
                            user_cal_data[known_cond] = (k_val, raw_v)
                        else:
                            k_val = float(parts[0].strip())
                            user_cal_data[known_cond] = (k_val, None)
                    except Exception:
                        continue
                
                if user_cal_data:
                    sorted_points = sorted([(cond, raw_v) for cond, (k, raw_v) in user_cal_data.items() if raw_v is not None], 
                                          key=lambda x: x[1])
                    
                    ranges_table = {}
                    
                    for i, (cond, raw_v) in enumerate(sorted_points):
                        k_val, _ = user_cal_data[cond]
                        
                        if i == 0:
                            min_v = 0.0
                        else:
                            min_v = sorted_points[i - 1][1]
                        
                        if i == len(sorted_points) - 1:
                            max_v = raw_v * 2.0
                        else:
                            max_v = sorted_points[i + 1][1]
                        
                        ranges_table[cond] = (k_val, min_v, max_v)
                    
                    self.known_k_ranges = ranges_table
                    self.known_k_table = {k: v[0] for k, v in ranges_table.items()}
                    self.use_user_calibration = True
                    return ranges_table
                else:
                    self.known_k_ranges = {}
                    self.known_k_table = {}
                    return {}
            
            elif 'KNOWN_K_TABLE' in cfg:
                table = {}
                for key, val in cfg.items('KNOWN_K_TABLE'):
                    try:
                        known_cond = int(float(key))
                        k_val = float(val)
                        table[known_cond] = (k_val, 0.0, 10.0)
                    except Exception:
                        continue
                
                self.known_k_ranges = table
                self.known_k_table = {k: v[0] for k, v in table.items()}
                self.use_user_calibration = True
                return table
            
            else:
                self.known_k_ranges = {}
                self.known_k_table = {}
                self.use_user_calibration = False
                return {}
                
        except Exception as e:
            print(f"Error cargando calibraciones: {e}")
            self.known_k_ranges = {}
            self.known_k_table = {}
            self.use_user_calibration = False
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
        """Obtiene el K del rango que corresponde al voltaje medido"""
        if raw_voltage is None:
            return 0.0
        
        try:
            raw_voltage = float(raw_voltage)
            
            # Buscar en los rangos definidos
            if hasattr(self, 'known_k_ranges') and self.known_k_ranges:
                for v_ref, (k_val, v_min, v_max) in self.known_k_ranges.items():
                    if v_min <= raw_voltage <= v_max:
                        print(f"[CSV] Voltaje {raw_voltage:.6f}V cae en rango [{v_min:.6f}-{v_max:.6f}] → K={k_val:.6f}")
                        return k_val
            
            # Si no encontró en rangos, buscar en tabla de puntos
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

    def apply_local_calibration(self, sensor=None, temp=None, raw=None):
        """Reconvierte voltaje a EC usando calibración local.
        
        Aplica polinomio DFRobot al voltaje crudo y obtiene K por interpolación.
        NO aplica compensación de temperatura porque K ya captura la calibración
        a la temperatura real del sensor.
        """
        try:
            s = sensor
            t = temp
            r = raw

            if r is None:
                return None, None

            # Paso 1: Aplicar polinomio DFRobot directamente al voltaje raw (SIN compensación)
            r_float = float(r)
            ec_poly = 133.42 * r_float**3 - 255.86 * r_float**2 + 857.39 * r_float
            
            # Paso 2: Obtener K interpolado de la tabla usando el voltaje
            k_used = self.interpolate_k(r_float)
            
            # Si no hay K calibrado, intentar cargar la tabla nuevamente
            if k_used is None or k_used <= 0:
                try:
                    self.load_known_k_table(force_load=True)
                    k_used = self.interpolate_k(r_float)
                except Exception:
                    pass
            
            # Si aún no hay K, usar valor por defecto
            if k_used is None or k_used <= 0:
                k_used = 1.0
            
            # Paso 3: Calcular EC SIN compensación de temperatura
            # K ya incorpora la calibración a la temperatura del sensor
            sensor_calc = k_used * ec_poly

            return sensor_calc, k_used
        except Exception:
            return None, None

    def apply_local_calibration_and_update(self):
        """Recalcula conductividad calibrada y actualiza interfaz.
        
        Aplica calibración a lectura actual, actualiza gráfica y labels,
        envía nuevo K al ESP32 y retorna estado de éxito de operación.
        """
        try:
            cr = getattr(self, 'current_reading', {})
            s = cr.get('sensor')
            t = cr.get('temp')
            r = cr.get('raw')
            sensor_new, k_new = self.apply_local_calibration(s, t, r)
            if sensor_new is None:
                return False

            self.current_reading['sensor'] = sensor_new
            if k_new is not None:
                self.current_reading['k'] = k_new

            if self.reading_active and hasattr(self, 'sensor_data') and self.sensor_data:
                try:
                    self.sensor_data[-1] = sensor_new
                    self.curve_sensor.setData([round(v, 1) for v in self.sensor_data])
                except Exception:
                    pass

            try:
                if self.reading_active:
                    if sensor_new is not None:
                        self.value_sensor_label.setText(f"{sensor_new:.1f} µS")
                    if t is not None:
                        self.value_temp_label.setText(f"{t:.1f} °C")
                    if k_new is not None:
                        self.k_label.setText(f"K: {k_new:.1f}")
                else:
                    self.value_sensor_label.setText("-- µS")
                    self.value_temp_label.setText("-- °C")
                    self.k_label.setText("K: --")
                    self.curve_sensor.setData([0])
                    self.curve_temp.setData([0])
            except Exception:
                pass

            try:
                if k_new is not None and self.connected and self.serial_reader:
                    self.serial_reader.send_command(f"SET_K:{k_new:.6f}")
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

        self.value_sensor_label.setText("-- µS")
        self.value_temp_label.setText("-- °C")
        self.k_label.setText("K: --")
        self.stats_label.setText("Estadísticas: Aguardando datos...")

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

        print(f"✅ LECTURA INICIADA - Solución: {test_name}")
        return True

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
            self._last_oled_sync_payload = None
            
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
        """Parsea y procesa línea serial JSON o texto plano.
        
        Extrae valores sensor/temperatura/K de JSON o formatos alternativos,
        aplica calibración local, actualiza displays y registra en CSV si está activo.
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

        try:
            if line.startswith('{'):
                obj = json.loads(line)
                msg_type = obj.get('type', '')

                if msg_type and msg_type not in ('reading',):
                    return
            
                if obj.get('type') == 'reading':
                    s_val = obj.get('sensor')
                    sensor = float(s_val) if s_val is not None else None
                
                    t_val = obj.get('temp')
                    temp = float(t_val) if t_val is not None else None
                
                    k_val = obj.get('k')
                    k = float(k_val) if k_val is not None else None
                
                    raw_val = obj.get('raw_V')
                    raw = float(raw_val) if raw_val is not None else None
                    
                    # Si no hay raw en el mensaje actual pero hay raw anterior, usar ese para recalcular con nueva temperatura
                    if raw is None and temp is not None:
                        prev_raw = self.current_reading.get('raw')
                        if prev_raw is not None:
                            raw = prev_raw
                    # Fuente única durante lectura: usar exactamente valores enviados por ESP32.
                    # Esto mantiene coherencia total entre OLED (firmware) y UI (desktop).
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
                if t and (t.startswith('cal') or t in ('measured_raw', 'store_k_result')):
                    try:
                        self.calibration_message.emit(obj_tmp)
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
            if temp is not None:
                self.current_reading['temp'] = temp
            if k is not None:
                self.current_reading['k'] = k
            if raw is not None:
                self.current_reading['raw'] = raw
                self.current_reading['signal'] = raw


                try:
                    if k is not None and self.connected and self.serial_reader:
                        self.serial_reader.send_command(f"SET_K:{k:.6f}")
                except Exception:
                    pass
            
                if self.reading_active:
                    self.sensor_data.append(sensor)
                    self.temp_data.append(temp)
                    try:
                        self.curve_sensor.setData([round(v, 1) for v in self.sensor_data])
                        self.curve_temp.setData([round(v, 1) for v in self.temp_data])
                    except Exception:
                        pass
                    try:
                        self.update_stats()
                    except Exception:
                        pass

                    try:
                        if sensor is not None:
                            self.value_sensor_label.setText(f"{sensor:.1f} µS")
                        else:
                            self.value_sensor_label.setText("-- µS")
                        
                        if temp is not None:
                            self.value_temp_label.setText(f"{temp:.1f} °C")
                        else:
                            self.value_temp_label.setText("-- °C")
                        
                        if k is not None:
                            self.k_label.setText(f"K: {k:.1f}")
                        else:
                            self.k_label.setText("K: --")
                    except Exception:
                        pass

                    try:
                        self._sync_oled_with_ui_values()
                    except Exception:
                        pass

                    self.data_received = True
                    try:
                        # Obtener el K del rango que corresponde al voltaje medido
                        raw_voltage = self.current_reading.get('raw')
                        k_from_range = self.get_k_from_range(raw_voltage)
                        # Usar sensor del ESP32 (antes de procesarlo), no el recalculado
                        print(f"[CSV GUARDADO] Sensor={sensor:.1f}µS, Temp={temp:.1f}°C, K={k_from_range:.6f}, Voltaje={raw_voltage}")
                        self.logger.write_row(sensor, temp, k_from_range, self.current_test_name)
                    except Exception as e:
                        print(f"[CSV ERROR AL GUARDAR] {e}")
                
                # OLED se actualiza solo cuando se recibe OLED_CAL_DONE
                # No enviar OLED_READING durante ejecución
        except Exception:
            pass

    def download_csv(self):
        """Guarda copia del CSV en carpeta seleccionada por usuario.
        
        Ofrece diálogo para elegir directorio de destino, copia el archivo
        de datos CSV del logger y muestra confirmación con ruta guardada.
        """
        try:
            src = getattr(self.logger, 'filename', None)
            if not src or not os.path.exists(src):
                QMessageBox.warning(self, "Error", "No hay archivo CSV disponible para descargar.")
                return

            dest_dir = QFileDialog.getExistingDirectory(
                self, 
                "Seleccionar carpeta para guardar CSV",
                os.path.expanduser("~")
            )
            if not dest_dir:
                return

            dest = os.path.join(dest_dir, os.path.basename(src))
            try:
                shutil.copy(src, dest)
                
                info_msg = (
                    f"✅ CSV de PC guardado en:\n{dest}\n\n"
                    f"Datos en tiempo real: {len(self.sensor_data) if hasattr(self, 'sensor_data') else 0} muestras\n\n"
                )
                
                QMessageBox.information(self, "Descarga Exitosa", info_msg)
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"No se pudo guardar el archivo:\n{e}")
                return

        except Exception:
            pass


    def plot_csv_data(self):
        """Abre ventana de gráficas desde archivo CSV seleccionado.
        
        Permite usuario seleccionar CSV, extrae datos de conductividad/temperatura,
        crea ventana CSVPlotWindow con gráficas y estadísticas de los datos.
        """
        try:
            path, _ = QFileDialog.getOpenFileName(self, "Seleccionar CSV", "", "CSV Files (*.csv)")
            if not path:
                return
            
            csv_data = {'sensor': [], 'temp': [], 'timestamps': []}
            with open(path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                for i, row in enumerate(reader):
                    try:
                        sensor = float(row[2]) if len(row) > 2 else 0.0
                        temp = float(row[3]) if len(row) > 3 else 0.0
                        csv_data['sensor'].append(sensor)
                        csv_data['temp'].append(temp)
                        csv_data['timestamps'].append(i)
                    except Exception:
                        continue
            
            if csv_data['sensor'] or csv_data['temp']:
                win = CSVPlotWindow(csv_data, path)
                win.show()
                self.csv_plot_window = win
            else:
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


# ═══════════════════════════════════════════════════════════════
#                     PUNTO DE ENTRADA
# ═══════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    
    window = ESP32App()
    
    try:
        cal = KnownCalibrationDialog(window)
        cal.set_serial(None, False)
        window.cal_dialog = cal
    except Exception:
        window.cal_dialog = None

    window.show()
    app.processEvents()
    
    connected_ok = window.show_connection_dialog()
    if not connected_ok:
        sys.exit(0)

    calibration_selection = CalibrationInitialDialog()
    
    if calibration_selection.exec_() == QDialog.Accepted:
        selected_mode = calibration_selection.selected_mode
        
        if selected_mode == CalibrationInitialDialog.MODE_KNOWN_VALUES:
            # Usuario selecciona Valores Conocidos
            window.set_calibration_mode_ui("known", send_to_device=True)
            cal_table_dialog = UnknownCalibrationTableDialog(window)
            try:
                if hasattr(cal_table_dialog, 'port_combo'):
                    cal_table_dialog.port_combo.setVisible(False)
                if hasattr(cal_table_dialog, 'refresh_btn'):
                    cal_table_dialog.refresh_btn.setVisible(False)
                if hasattr(cal_table_dialog, 'connect_btn'):
                    cal_table_dialog.connect_btn.setVisible(False)
                if hasattr(cal_table_dialog, 'led'):
                    cal_table_dialog.led.setVisible(False)
                if hasattr(cal_table_dialog, 'connection_status'):
                    cal_table_dialog.connection_status.setVisible(False)
            except Exception:
                pass

            cal_table_dialog.set_serial(window.serial_reader, window.connected, window)
            window.cal_table_dialog = cal_table_dialog

            cal_table_dialog.exec_()
            
            window.raise_()
            window.activateWindow()
        
        elif selected_mode == CalibrationInitialDialog.MODE_LABORATORY:
            # Usuario selecciona "Continuar sin Calibración" 
            # Fuerza modo laboratorio al arrancar medición
            window.set_calibration_mode_ui("laboratory", send_to_device=True)
            
            window.raise_()
            window.activateWindow()
        
        sys.exit(app.exec_())
    else:
        sys.exit(0)

if __name__ == '__main__':
    main()
