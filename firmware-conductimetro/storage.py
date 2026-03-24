import os
from machine import SDCard


class StorageManager:
    """Gestiona inicialización de SD y persistencia de lecturas en MicroPython."""

    def __init__(self, config):
        """Configura rutas y estado inicial del almacenamiento.

        Args:
            config: Objeto de configuración con `SD_FILE` y `SD_MOUNT`.
        """
        self.config = config
        self.sd_mounted = False
        self.sd_log_filename = config.SD_FILE

    def init_sd(self):
        """Intenta montar la SD usando varias combinaciones de slot/frecuencia.

        Returns:
            True si la SD quedó montada y lista para escritura, False en caso contrario.
        """
        cfg = self.config
        configs = [
            {"slot": 1, "freq": 1320000},
            {"slot": 1, "freq": None},
            {"slot": 2, "freq": 1320000},
            {"slot": 2, "freq": None},
        ]

        for item in configs:
            try:
                if item["freq"] is None:
                    sd = SDCard(slot=item["slot"])
                else:
                    sd = SDCard(slot=item["slot"], freq=item["freq"])

                try:
                    os.mount(sd, cfg.SD_MOUNT)
                except Exception:
                    pass

                self.sd_mounted = True

                try:
                    os.stat(self.sd_log_filename)
                except Exception:
                    with open(self.sd_log_filename, "w") as file_handle:
                        file_handle.write("Fecha,Hora,Temperatura,Conductividad\n")

                return True
            except Exception:
                continue

        self.sd_mounted = False
        return False

    def log_to_sd(self, fecha, hora, temp, ec, k_factor=None):
        """Agrega una lectura al archivo CSV de la SD.

        Args:
            fecha: Fecha formateada por RTC.
            hora: Hora formateada por RTC.
            temp: Temperatura en °C.
            ec: Conductividad en µS.
            k_factor: Parámetro mantenido por compatibilidad; no se guarda en CSV.

        Returns:
            True cuando la escritura fue exitosa; False si falla o SD no está montada.
        """
        if not self.sd_mounted:
            return False

        try:
            with open(self.sd_log_filename, "a") as file_handle:
                file_handle.write("%s,%s,%.2f,%.1f\n" % (fecha, hora, temp, ec))
            return True
        except Exception:
            self.sd_mounted = False
            return False
