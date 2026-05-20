import serial
import threading
import time


class SerialReader:
    """Abstracción de puerto serial con lectura asíncrona en hilo dedicado.

    La clase separa recepción continua (background thread) de envío de comandos
    sincronizado mediante lock para evitar tramas intercaladas.
    """

    def __init__(self, port, baudrate=115200, callback=None):
        """Configura parámetros de conexión y callback de recepción.

        Args:
            port: Puerto serial (ej. `COM5`).
            baudrate: Velocidad de transmisión.
            callback: Función invocada por cada línea recibida.
        """
        self.port = port
        self.baudrate = baudrate
        self.callback = callback
        self.running = False
        self.thread = None
        self.ser = None
        self.command_lock = threading.Lock()

    def start(self):
        """Abre el puerto e inicia el hilo de lectura.

        Returns:
            True si el puerto se abrió correctamente, False en error.
        """
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            self.running = True
            self.thread = threading.Thread(target=self.read_data, daemon=True)
            self.thread.start()
            print(f"[SERIAL] Opened {self.port} @ {self.baudrate}")
            # Proactively request device identity (non-blocking)
            try:
                self.send_command("IDENTIFY")
            except Exception:
                pass
            return True
        except serial.SerialException as e:
            print(f"Error abriendo puerto serial: {e}")
            if str(e).find("PermissionError") >= 0 or "denegado" in str(e):
                import traceback
                print("[SERIAL] Stack trace for PermissionError:")
                traceback.print_stack()
            return False

    def read_data(self):
        """Bucle de recepción no bloqueante ejecutado en hilo secundario.

        Lee líneas completas y las entrega al callback del consumidor.
        """
        while self.running:
            try:
                if self.ser and self.ser.in_waiting > 0:
                    line = self.ser.readline().decode(errors='ignore').strip()
                    if line:
                        print(f"[SERIAL RX] {line}")
                        if self.callback:
                            self.callback(line)
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"Error lectura serial: {e}")
                time.sleep(0.1)

    def send_command(self, command):
        """Envía un comando al dispositivo garantizando exclusión mutua.

        Args:
            command: Texto de comando sin salto de línea final.

        Returns:
            True en envío exitoso, False si no hay conexión o ocurre error.
        """
        if not self.running or not self.ser:
            return False

        with self.command_lock:
            try:
                full_command = command.strip() + '\n'
                self.ser.write(full_command.encode('utf-8'))
                self.ser.flush()
                print(f"[SERIAL TX] {command}")
                return True
            except Exception as e:
                print(f"Error enviando comando {command}: {e}")
                return False

    def stop(self):
        """Detiene lectura y cierra recursos seriales de forma segura."""
        self.running = False
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        try:
            if self.thread:
                self.thread.join(timeout=1.0)
        except Exception:
            pass
