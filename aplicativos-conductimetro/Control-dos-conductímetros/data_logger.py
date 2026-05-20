import csv
import os
import sys
from datetime import datetime
import threading
import time


def get_base_dir():
    """Retorna el directorio base del ejecutable o script."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


class DataLogger:
    """Gestor de registro CSV en entorno de escritorio.

    Centraliza creación de archivo, rotación por nombre de prueba y escritura
    thread-safe de muestras en disco.
    """

    def __init__(self, filename="datos_esp32.csv"):
        """Inicializa el logger sin crear archivo CSV inmediato.

        Args:
            filename: Nombre del archivo CSV inicial.
        """
        self.base_dir = get_base_dir()
        self.default_filename = filename
        self.filename = None
        self.lock = threading.Lock()
        self.last_save_time = 0
        self.save_interval = 2.0
        self.test_name = ""

    def _create_csv_file(self, filepath):
        """Crea archivo CSV con cabecera estándar."""
        with open(filepath, "w", newline="", encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Fecha", "Hora", "Sensor", "Temperatura", "Nombre_Prueba"])
        print(f"Archivo CSV creado: {filepath}")

    def set_test_name(self, test_name):
        """Cambia la prueba activa y rota a un nuevo archivo CSV.

        Args:
            test_name: Identificador legible de la prueba.
        """
        self.test_name = test_name
        print(f"Nombre de prueba establecido: {test_name}")
        new_filename = f"datos_{test_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self.filename = os.path.join(self.base_dir, new_filename)
        try:
            self._create_csv_file(self.filename)
            print(f"✅ Nuevo archivo CSV creado: {self.filename}")
        except Exception as e:
            print(f"Error creando archivo CSV: {e}")

    def set_save_interval(self, interval):
        """Configura el intervalo mínimo entre escrituras periódicas.

        Args:
            interval: Segundos entre guardados.
        """
        self.save_interval = interval
        print(f"Intervalo de guardado establecido: {interval} segundos")

    def should_save(self):
        """Indica si ya se cumplió el intervalo de guardado.

        Returns:
            True cuando se puede realizar un nuevo guardado, False en caso contrario.
        """
        current_time = time.time()
        if current_time - self.last_save_time >= self.save_interval:
            self.last_save_time = current_time
            return True
        return False

    def write_row(self, sensor, temp, k, test_name=""):
        """Escribe una muestra en CSV usando exclusión mutua.

        Args:
            sensor: Conductividad en µS.
            temp: Temperatura en °C.
            k: Parámetro reservado para compatibilidad de llamadas; no se persiste.
            test_name: Etiqueta de la prueba asociada.

        Returns:
            True si la fila se guardó correctamente, False si ocurrió un error.
        """
        now = datetime.now()
        with self.lock:
            try:
                # Creación diferida: evita generar datos_esp32.csv vacío al iniciar la app.
                if not self.filename:
                    base_name = (test_name or self.test_name or "sesion").strip()
                    safe_name = "_".join(base_name.split())
                    lazy_filename = f"datos_{safe_name}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
                    self.filename = os.path.join(self.base_dir, lazy_filename)
                    self._create_csv_file(self.filename)

                with open(self.filename, "a", newline="", encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        now.strftime("%d/%m/%Y"),
                        now.strftime("%H:%M:%S"),
                        f"{sensor:.2f}",
                        f"{temp:.2f}",
                        test_name
                    ])
                print(f"Dato guardado: {sensor:.2f}uS, {temp:.2f}°C, Prueba: {test_name}")
                return True
            except Exception as e:
                print(f"Error escribiendo en CSV: {e}")
                return False
