"""
Gestor de botón de extracción con debouncing seguro en ESP32.
Compatible con MicroPython.
Presión sostenida (>= 3000ms) del botón activa modo de extracción.
"""

import time
from machine import Pin

def prueba_boton_en_tiempo_real(pin_numero=34, duracion=10):
    """
    Prueba en tiempo real el estado del pin durante 10 segundos.
    Presiona y suelta el botón mientras se ejecuta para ver los cambios.
    """
    print("\n" + "="*60)
    print("PRUEBA EN TIEMPO REAL DEL BOTON (10 segundos)")
    print("="*60)
    print("Presiona y suelta el botón para ver los cambios\n")
    
    # Para GPIO34 (ESP32) y montaje con resistencia externa,
    # usar entrada simple sin pull interno.
    pin = Pin(pin_numero, Pin.IN)
    tiempo_inicio = time.time()
    ultima_lectura = None
    
    while time.time() - tiempo_inicio < duracion:
        lectura_actual = pin.value()
        
        # Solo imprimir si cambió
        if lectura_actual != ultima_lectura:
            tiempo_transcurrido = time.time() - tiempo_inicio
            estado = "ALTO (1)" if lectura_actual == 1 else "BAJO (0)"
            print(f"[{tiempo_transcurrido:.1f}s] value() = {estado}")
            ultima_lectura = lectura_actual
        
        time.sleep(0.05)
    
    print("\n" + "="*60)
    print("RECOMENDACIÓN:")
    print("- Si value() = 0 normalmente y 1 al presionar (pull-down externo) → usa tipo='NA'")
    print("- Si value() = 1 normalmente y 0 al presionar → usa tipo='NC'")
    print("="*60 + "\n")



class BotonExtraccion:
    """
    Maneja el botón de extracción (GPIO 34) con debouncing y sincronización.
    Requiere presión sostenida de 3 segundos para activar modo de extracción.
    
    Compatible con:
    - Pulsador NORMALMENTE ABIERTO (NA): value() = 0 normal, 1 al presionar
    - Pulsador NORMALMENTE CERRADO (NC): value() = 1 normal, 0 al presionar
    """
    
    def __init__(self, pin_numero=34, tiempo_debounce_ms=50, tiempo_presion_minimo_ms=3000, tipo_boton="NA"):
        """
        Args:
            pin_numero: GPIO del botón (34 default)
            tiempo_debounce_ms: Tiempo de debouncing (50ms)
            tiempo_presion_minimo_ms: Tiempo mínimo para detectar activación (3000ms = 3s default)
            tipo_boton: "NA" (normalmente abierto) o "NC" (normalmente cerrado)
        """
        # GPIO34 en ESP32 es solo entrada y no siempre soporta pull interno.
        # Se usa entrada simple para evitar estados inválidos por configuración de pull-up.
        self.pin = Pin(pin_numero, Pin.IN)
        self.debounce_time_ms = tiempo_debounce_ms
        self.press_time_ms = tiempo_presion_minimo_ms
        self.tipo_boton = tipo_boton.upper()
        
        self.tiempo_presion_inicio = None
        self.ultimo_valor_raw = None
        self.estado_estable = None
        self.estado_candidato = None
        self.tiempo_cambio_candidato = None
        self.ultimo_segundo_reportado = -1
        
    def esta_presionado(self):
        """
        Retorna True si el botón está presionado actualmente.
        Usa contador de confirmaciones: necesita 10 lecturas iguales para confirmar estado.
        Resiste rebotes severos del contacto.
        NO BLOQUEA - se llama cada iteración del loop.
        """
        valor_pin_raw = self.pin.value()
        
        # Convertir valor raw a booleano
        if self.tipo_boton == "NA":
            estado_nuevo = (valor_pin_raw == 1)  # NA: presionado = 1
        else:  # NC
            estado_nuevo = (valor_pin_raw == 0)  # NC: presionado = 0
        
        ahora_ms = time.ticks_ms()

        # Primera lectura
        if self.estado_estable is None:
            self.ultimo_valor_raw = valor_pin_raw
            self.estado_estable = estado_nuevo
            self.estado_candidato = estado_nuevo
            self.tiempo_cambio_candidato = ahora_ms
            return estado_nuevo

        # Si aparece un nuevo candidato de estado, iniciar ventana de debounce
        if estado_nuevo != self.estado_candidato:
            self.estado_candidato = estado_nuevo
            self.tiempo_cambio_candidato = ahora_ms
        else:
            # Si el candidato se mantiene por el tiempo de debounce, confirmar
            if self.tiempo_cambio_candidato is not None:
                transcurrido = time.ticks_diff(ahora_ms, self.tiempo_cambio_candidato)
                if transcurrido >= self.debounce_time_ms:
                    self.estado_estable = self.estado_candidato

        self.ultimo_valor_raw = valor_pin_raw
        return self.estado_estable
    
    def detectar_presion_sostenida(self):
        """
        Detecta presión sostenida del botón (>= 3 segundos).
        Apenas detecta presión, comienza a contar 3 segundos.
        Si el botón permanece presionado 3s → retorna True (ACTIVA EXTRACCIÓN).
        No-bloqueante - puede ser llamada cada 100ms en el loop principal.
        """
        presionado_ahora = self.esta_presionado()
        
        if presionado_ahora:
            if self.tiempo_presion_inicio is None:
                # INICIO: Botón acaba de presionarse, comenzar contador
                self.tiempo_presion_inicio = time.ticks_ms()
                self.ultimo_segundo_reportado = -1
                print("[BOTON] ✓ Presionado detectado - CONTANDO 3 SEGUNDOS...")
            
            # Calcular cuánto tiempo lleva presionado
            tiempo_transcurrido_ms = time.ticks_diff(time.ticks_ms(), self.tiempo_presion_inicio)
            tiempo_transcurrido_s = tiempo_transcurrido_ms / 1000.0
            
            # Mostrar progreso cada 1 segundo
            segundos_enteros = int(tiempo_transcurrido_s)
            if segundos_enteros > 0 and segundos_enteros <= 3 and segundos_enteros != self.ultimo_segundo_reportado:
                self.ultimo_segundo_reportado = segundos_enteros
                print(f"[BOTON] Presionado {segundos_enteros}s de 3s...")
            
            # Verificar si llegó a 3 segundos
            if tiempo_transcurrido_ms >= self.press_time_ms:
                print(f"[BOTON] ✓✓✓ ¡PRESIÓN SOSTENIDA DETECTADA! ({tiempo_transcurrido_s:.1f}s) - ACTIVANDO EXTRACCIÓN")
                self.tiempo_presion_inicio = None
                self.ultimo_segundo_reportado = -1
                return True
        else:
            # BOTÓN SOLTADO
            if self.tiempo_presion_inicio is not None:
                tiempo_transcurrido_ms = time.ticks_diff(time.ticks_ms(), self.tiempo_presion_inicio)
                tiempo_transcurrido_s = tiempo_transcurrido_ms / 1000.0
                
                if tiempo_transcurrido_ms < self.press_time_ms:
                    print(f"[BOTON] ✗ Cancelado - presionado {tiempo_transcurrido_s:.2f}s (se necesitan 3.0s)")
                
                self.tiempo_presion_inicio = None
                self.ultimo_segundo_reportado = -1
        
        return False


def monitorear_boton(boton_obj):
    """
    Función para monitorear botón (compatibilidad).
    NOTA: No se usa en firmware.py, monitoreo se hace en loop principal.
    """
    while True:
        try:
            boton_obj.detectar_presion_sostenida()
            time.sleep(0.1)
        except Exception as e:
            print(f"[MONITOR BOTON ERROR] {e}")
            time.sleep(0.5)

