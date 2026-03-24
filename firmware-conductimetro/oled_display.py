from machine import I2C, Pin
import sh1106
import time

class SmallFont:
    def __init__(self, display):
        self.display = display
        self.font = {
            '0': [0x3E, 0x51, 0x49, 0x45, 0x3E, 0x00],
            '1': [0x00, 0x42, 0x7F, 0x40, 0x00, 0x00],
            '2': [0x42, 0x61, 0x51, 0x49, 0x46, 0x00],
            '3': [0x21, 0x41, 0x45, 0x4B, 0x31, 0x00],
            '4': [0x18, 0x14, 0x12, 0x7F, 0x10, 0x00],
            '5': [0x27, 0x45, 0x45, 0x45, 0x39, 0x00],
            '6': [0x3C, 0x4A, 0x49, 0x49, 0x30, 0x00],
            '7': [0x01, 0x71, 0x09, 0x05, 0x03, 0x00],
            '8': [0x36, 0x49, 0x49, 0x49, 0x36, 0x00],
            '9': [0x06, 0x49, 0x49, 0x29, 0x1E, 0x00],
            '/': [0x00, 0x10, 0x08, 0x04, 0x02, 0x00],
            ':': [0x00, 0x36, 0x36, 0x00, 0x00, 0x00],
            ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            'A': [0x7C, 0x12, 0x11, 0x12, 0x7C, 0x00],
            'B': [0x7F, 0x49, 0x49, 0x49, 0x36, 0x00],
            'C': [0x3E, 0x41, 0x41, 0x41, 0x22, 0x00],
            'D': [0x7F, 0x41, 0x41, 0x22, 0x1C, 0x00],
            'E': [0x7F, 0x49, 0x49, 0x49, 0x41, 0x00],
            'F': [0x7F, 0x09, 0x09, 0x09, 0x01, 0x00],
            'G': [0x3E, 0x41, 0x49, 0x49, 0x3A, 0x00],
            'H': [0x7F, 0x08, 0x08, 0x08, 0x7F, 0x00],
            'I': [0x00, 0x41, 0x7F, 0x41, 0x00, 0x00],
            'J': [0x20, 0x40, 0x41, 0x3F, 0x01, 0x00],
            'K': [0x7F, 0x08, 0x14, 0x22, 0x41, 0x00],
            'L': [0x7F, 0x40, 0x40, 0x40, 0x40, 0x00],
            'M': [0x7F, 0x02, 0x0C, 0x02, 0x7F, 0x00],
            'N': [0x7F, 0x04, 0x08, 0x10, 0x7F, 0x00],
            'O': [0x3E, 0x41, 0x41, 0x41, 0x3E, 0x00],
            'P': [0x7F, 0x09, 0x09, 0x09, 0x06, 0x00],
            'Q': [0x3E, 0x41, 0x51, 0x21, 0x5E, 0x00],
            'R': [0x7F, 0x09, 0x19, 0x29, 0x46, 0x00],
            'S': [0x46, 0x49, 0x49, 0x49, 0x31, 0x00],
            'T': [0x01, 0x01, 0x7F, 0x01, 0x01, 0x00],
            'U': [0x3F, 0x40, 0x40, 0x40, 0x3F, 0x00],
            'V': [0x1F, 0x20, 0x40, 0x20, 0x1F, 0x00],
            'W': [0x3F, 0x40, 0x38, 0x40, 0x3F, 0x00],
            'X': [0x63, 0x14, 0x08, 0x14, 0x63, 0x00],
            'Y': [0x07, 0x08, 0x70, 0x08, 0x07, 0x00],
            'Z': [0x61, 0x51, 0x49, 0x45, 0x43, 0x00],
            'a': [0x20, 0x54, 0x54, 0x54, 0x78, 0x00],
            'b': [0x7F, 0x48, 0x44, 0x44, 0x38, 0x00],
            'c': [0x38, 0x44, 0x44, 0x44, 0x20, 0x00],
            'd': [0x38, 0x44, 0x44, 0x48, 0x7F, 0x00],
            'e': [0x38, 0x54, 0x54, 0x54, 0x18, 0x00],
            'f': [0x08, 0x7E, 0x09, 0x01, 0x02, 0x00],
            'g': [0x0C, 0x52, 0x52, 0x52, 0x3E, 0x00],
            'h': [0x7F, 0x08, 0x04, 0x04, 0x78, 0x00],
            'i': [0x00, 0x44, 0x7D, 0x40, 0x00, 0x00],
            'j': [0x20, 0x40, 0x44, 0x3D, 0x00, 0x00],
            'k': [0x7F, 0x10, 0x28, 0x44, 0x00, 0x00],
            'l': [0x00, 0x41, 0x7F, 0x40, 0x00, 0x00],
            'm': [0x7C, 0x04, 0x18, 0x04, 0x78, 0x00],
            'n': [0x7C, 0x08, 0x04, 0x04, 0x78, 0x00],
            'o': [0x38, 0x44, 0x44, 0x44, 0x38, 0x00],
            'p': [0x7C, 0x14, 0x14, 0x14, 0x08, 0x00],
            'q': [0x08, 0x14, 0x14, 0x18, 0x7C, 0x00],
            'r': [0x7C, 0x08, 0x04, 0x04, 0x08, 0x00],
            's': [0x48, 0x54, 0x54, 0x54, 0x20, 0x00],
            't': [0x04, 0x3F, 0x44, 0x40, 0x20, 0x00],
            'u': [0x3C, 0x40, 0x40, 0x20, 0x7C, 0x00],
            'v': [0x1C, 0x20, 0x40, 0x20, 0x1C, 0x00],
            'w': [0x3C, 0x40, 0x30, 0x40, 0x3C, 0x00],
            'x': [0x44, 0x28, 0x10, 0x28, 0x44, 0x00],
            'y': [0x0C, 0x50, 0x50, 0x50, 0x3C, 0x00],
            'z': [0x44, 0x64, 0x54, 0x4C, 0x44, 0x00],
            '.': [0x00, 0x00, 0x40, 0x00, 0x00, 0x00],
            ',': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            '-': [0x00, 0x08, 0x08, 0x08, 0x00, 0x00],
            '+': [0x00, 0x08, 0x1C, 0x08, 0x00, 0x00],
            '=': [0x00, 0x14, 0x14, 0x14, 0x00, 0x00],
            '*': [0x00, 0x2A, 0x1C, 0x2A, 0x00, 0x00],
            '\\': [0x00, 0x02, 0x04, 0x08, 0x10, 0x00],
            '[': [0x00, 0x3E, 0x22, 0x00, 0x00, 0x00],
            ']': [0x00, 0x22, 0x3E, 0x00, 0x00, 0x00],
            '(': [0x00, 0x1C, 0x22, 0x00, 0x00, 0x00],
            ')': [0x00, 0x22, 0x1C, 0x00, 0x00, 0x00],
            '{': [0x00, 0x08, 0x36, 0x41, 0x00, 0x00],
            '}': [0x00, 0x41, 0x36, 0x08, 0x00, 0x00],
            '<': [0x00, 0x08, 0x14, 0x22, 0x00, 0x00],
            '>': [0x00, 0x22, 0x14, 0x08, 0x00, 0x00],
            '_': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            '|': [0x00, 0x00, 0x7F, 0x00, 0x00, 0x00],
            '!': [0x00, 0x00, 0x5F, 0x00, 0x00, 0x00],
            '?': [0x00, 0x07, 0x00, 0x07, 0x00, 0x00],
            '"': [0x00, 0x07, 0x00, 0x07, 0x00, 0x00],
            "'": [0x00, 0x00, 0x07, 0x00, 0x00, 0x00],
            '`': [0x00, 0x01, 0x02, 0x00, 0x00, 0x00],
            '~': [0x00, 0x04, 0x08, 0x04, 0x00, 0x00],
            '@': [0x3E, 0x41, 0x49, 0x55, 0x3E, 0x00],
            '#': [0x14, 0x7F, 0x14, 0x7F, 0x14, 0x00],
            '$': [0x24, 0x2A, 0x7F, 0x2A, 0x12, 0x00],
            '%': [0x23, 0x13, 0x08, 0x64, 0x62, 0x00],
            '&': [0x36, 0x49, 0x55, 0x22, 0x50, 0x00],
            '^': [0x00, 0x04, 0x02, 0x04, 0x00, 0x00],
            '°': [0x00, 0x06, 0x09, 0x06, 0x00, 0x00]
        }
    
    def draw_char(self, char, x, y):
        if char in self.font:
            char_data = self.font[char]
            for col in range(6): 
                col_data = char_data[col]
                for row in range(8):  
                    if col_data & (1 << row):
                        self.display.pixel(x + col, y + row, 1)
    
    def draw_text(self, text, x, y):
        for i, char in enumerate(text):
            self.draw_char(char, x + i * 7, y)

class OLEDDisplay:
    def __init__(self):
        self.i2c = I2C(0, scl=Pin(32), sda=Pin(33))
        self.display = sh1106.SH1106_I2C(128, 64, self.i2c, None, 0x3c)
        self.display.flip(True)
        self.display.sleep(False)
        self.display.contrast(200)
        self.small_font = SmallFont(self.display)

    def mostrar_bienvenida(self, logo_archivo='images/logo.pbm'):
        try:
            self.display.fill(0)
            ok = self.mostrar_logo(logo_archivo)
            if not ok:
                self.small_font.draw_text("BIENVENIDOS", 1, 1)
            else:
                self.small_font.draw_text("Servicio Geologico", 1, 50)
                self.small_font.draw_text("Colombiano", 30, 57)
                self.display.show()
                time.sleep(3)
                self.display.fill(0)
                self.small_font.draw_text("Medicion de", 25, 1)
                self.small_font.draw_text("Conductividad y", 8, 15)
                self.small_font.draw_text("Temperatura", 25, 30)
                self.small_font.draw_text("Bienvenid@s", 25, 50)
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_bienvenida: {e}")
            return False

    def mostrar_seleccionar_calibracion(self):
        return self.mostrar_bienvenida()

    # =================================================================
    # MODO MULTIPARAMETRO  (selección de valores conocidos)
    # =================================================================
    def mostrar_modo_multiparametro(self):
        try:
            self.display.fill(0)
            self.small_font.draw_text("Modo seleccion:", 5, 15)
            self.small_font.draw_text("Multiparametro", 8, 30)
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_modo_multiparametro: {e}")
            return False

    def mostrar_soluciones(self, soluciones):
        try:
            self.display.fill(0)
            self.small_font.draw_text("Soluciones:", 5, 0)
            y = 10
            for i, sol in enumerate(soluciones[:5]):
                self.small_font.draw_text(f"{i+1}. {int(sol)} uS", 10, y)
                y += 10
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_soluciones: {e}")
            return False

    def mostrar_valores_conocido_k(self, fecha, hora, temperatura, conductividad, logo_archivo='images/logo2.pbm'):
        """Muestra solo el valor a calibrar durante el proceso de calibracion"""
        try:
            self.display.fill(0)
            
            # Mostrar "Calibrando:" en la parte superior
            self.small_font.draw_text("Calibrando:", 10, 5)
            
            # Mostrar el valor conocido en grande en el centro
            valor_text = f"{float(conductividad):.0f}uS"
            self.small_font.draw_text(valor_text, 20, 25)
            
            # Mostrar la temperatura
            temp_text = f"T:{temperatura:.0f}C"
            self.small_font.draw_text(temp_text, 20, 40)
            
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_valores_conocido_k: {e}")
            return False

    def mostrar_calibracion_punto_a_punto(self):
        return self.mostrar_modo_multiparametro()

    def mostrar_calibracion_seleccionada(self):
        return self.mostrar_modo_multiparametro()

    # =================================================================
    # 3-a) MODO DE FABRICA  (sin calibración)
    # =================================================================
    def mostrar_modo_fabrica(self):
        try:
            self.display.fill(0)
            self.small_font.draw_text("Modo seleccion:", 5, 15)
            self.small_font.draw_text("De fabrica", 18, 30)
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_modo_fabrica: {e}")
            return False

    def mostrar_con_datos_fabrica(self):
        return self.mostrar_modo_fabrica()
    
    def mostrar_logo(self, archivo):
        try:
            with open(archivo, "rb") as f:
                data = f.read()

            partes = data.split(b"\n")

            idx = 1
            while idx < len(partes) and partes[idx].strip().startswith(b"#"):
                idx += 1

            ancho, alto = map(int, partes[idx].split())

            x_offset = (128-ancho) //2
            y_offset = 0
        

            self.display.fill(0)

            header = partes[0].strip() if partes else b""

            if header == b"P1":
                tokens = b" ".join(partes[idx+1:]).split()
                pos = 0
                for y in range(alto):
                    for x in range(ancho):
                        if pos >= len(tokens):
                            break
                        if tokens[pos] == b"1":
                            if 0 <= x + x_offset < 128 and 0 <= y + y_offset < 64:
                                self.display.pixel(x + x_offset, y + y_offset, 1)
                        pos += 1
            else:
                raw = b"\n".join(partes[idx+1:])
                bytes_por_fila = (ancho + 7) // 8
                pos = 0
                for y in range(alto):
                    if pos >= len(raw):
                        break
                    fila = raw[pos:pos + bytes_por_fila]
                    pos += bytes_por_fila
                    while pos < len(raw) and raw[pos] in (10, 13):
                        pos += 1
                    for x_byte, byte_val in enumerate(fila):
                        if isinstance(byte_val, int):
                            bv = byte_val
                        else:
                            bv = ord(chr(byte_val))
                        for bit in range(8):
                            x = x_byte * 8 + bit
                            if x < ancho and (y + y_offset) < 64:
                                if (bv >> (7 - bit)) & 1:
                                    self.display.pixel(x + x_offset, y + y_offset, 1)

            self.display.show()
            return True
        except:
            return False
    
    def mostrar_datos(self, fecha, hora, temperatura, conductividad, logo_archivo='images/logo2.pbm'):

        try:
            with open(logo_archivo, "rb") as f:
                data = f.read()

            partes = data.split(b"\n")
            idx = 1
            while idx < len(partes) and partes[idx].strip().startswith(b"#"):
                idx += 1

            ancho_logo, alto_logo = map(int, partes[idx].split())

            x_offset_logo = 5
            y_offset_logo = 30

            self.display.fill(0)

            header = partes[0].strip() if partes else b""
            if header == b"P1":
                tokens = b" ".join(partes[idx+1:]).split()
                pos = 0
                for y in range(alto_logo):
                    for x in range(ancho_logo):
                        if pos >= len(tokens):
                            break
                        if tokens[pos] == b"1":
                            if 0 <= x + x_offset_logo < 128 and 0 <= y + y_offset_logo < 64:
                                self.display.pixel(x + x_offset_logo, y + y_offset_logo, 1)
                        pos += 1
            else:
                raw = b"\n".join(partes[idx+1:])
                bytes_por_fila = (ancho_logo + 7) // 8
                pos = 0
                for y in range(alto_logo):
                    if pos >= len(raw):
                        break
                    fila = raw[pos:pos + bytes_por_fila]
                    pos += bytes_por_fila
                    while pos < len(raw) and raw[pos] in (10, 13):
                        pos += 1
                    for x_byte, byte_val in enumerate(fila):
                        if isinstance(byte_val, int):
                            bv = byte_val
                        else:
                            bv = ord(chr(byte_val))
                        for bit in range(8):
                            x = x_byte * 8 + bit
                            if x < ancho_logo and (y + y_offset_logo) < 64:
                                if (bv >> (7 - bit)) & 1:
                                    self.display.pixel(x + x_offset_logo, y + y_offset_logo, 1)
        
            x_texto = x_offset_logo + ancho_logo + 8
            self.small_font.draw_text(fecha, 30, 5)
            self.small_font.draw_text(hora, 40, 13)
            
            sensor_text = f"C:{float(conductividad):.0f}uS"
            self.small_font.draw_text(sensor_text, x_texto, 20)
            
            temp_text = f"T:{temperatura:.0f}C"
            self.small_font.draw_text(temp_text, x_texto, 32)
            
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_datos: {e}")
            return False

    def mostrar_lectura(self, fecha, hora, sensor, temp, logo_archivo='images/logo2.pbm'):
        """Muestra lectura en tiempo real - usa la misma estructura que mostrar_datos"""
        return self.mostrar_datos(fecha, hora, temp, sensor, logo_archivo)
        
    def limpiar(self):
        self.display.fill(0)
        self.display.show()

    def clear(self):
        try:
            self.display.fill(0)
            self.display.show()
            return True
        except Exception:
            return False
    
    def mostrar_modo_extraccion_activado(self):
        """Muestra en OLED: MODO EXTRACCIÓN DE DATOS ACTIVADO"""
        try:
            self.display.fill(0)
            self.small_font.draw_text("MODO", 40, 10)
            self.small_font.draw_text("EXTRACCION", 20, 25)
            self.small_font.draw_text("ACTIVADO", 30, 40)
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_modo_extraccion_activado: {e}")
            return False
    
    def mostrar_boton_presionado(self, segundos_restantes):
        """Muestra en OLED mientras se presiona el botón (con contador)"""
        try:
            self.display.fill(0)
            self.small_font.draw_text("BOTON", 35, 15)
            self.small_font.draw_text("PRESIONADO", 20, 30)
            self.small_font.draw_text(f"Espera: {segundos_restantes}s", 15, 50)
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_boton_presionado: {e}")
            return False
    
    def mostrar_esperando_salir(self, segundos_restantes=0):
        """Muestra en OLED mientras se espera presión para salir de extracción"""
        try:
            self.display.fill(0)
            self.small_font.draw_text("PRESIONA BOTON", 10, 15)
            self.small_font.draw_text("3 SEGUNDOS", 15, 30)
            self.small_font.draw_text("PARA SALIR", 20, 45)
            if segundos_restantes > 0:
                self.small_font.draw_text(f"Saliendo: {segundos_restantes}s", 10, 60)
            self.display.show()
            return True
        except Exception as e:
            print(f"Error mostrar_esperando_salir: {e}")
            return False

oled = OLEDDisplay()

