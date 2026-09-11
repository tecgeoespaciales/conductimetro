"""
Módulo de extracción de datos desde tarjeta SD en ESP32.
Maneja descarga segura de archivos de lecturas registradas.
MicroPython compatible.
"""

import os
import time
import network
import socket
import json

try:
    import gc
except Exception:
    gc = None

ENABLE_OLED_EXTRACCION = False
if ENABLE_OLED_EXTRACCION:
    try:
        from oled_display import oled
    except Exception:
        oled = None
else:
    oled = None


AP_SSID = "ESP32-EXTRACCION"
AP_PASSWORD = "12345678"

_ULTIMO_INTENTO_SD_MS = 0
_WIFI_FATAL = False
_AP_INSTANCIA = None


def _existe_ruta(ruta):
    try:
        os.stat(ruta)
        return True
    except Exception:
        return False


def _archivo_memoria_para_descarga():
    candidatos = ["lectura.csv", "lecturas_local.txt", "log.txt"]
    for nombre in candidatos:
        try:
            if nombre in os.listdir('.'):
                return nombre
        except Exception:
            pass
    return None


def _archivo_sd_para_descarga():
    candidatos = ["lectura.csv", "lecturas.csv", "log.txt", "lecturas_local.txt"]
    try:
        archivos_sd = os.listdir('/sd')
        for nombre in candidatos:
            if nombre in archivos_sd:
                return '/sd/' + nombre
        for nombre in archivos_sd:
            if nombre.endswith('.csv'):
                return '/sd/' + nombre
    except Exception:
        pass
    return None


def _montar_sd_para_extraccion():
    try:
        from machine import SDCard  # type: ignore
    except Exception:
        return False

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
                os.mount(sd, '/sd')
            except Exception:
                pass

            os.listdir('/sd')
            return True
        except Exception:
            continue

    return False


def _asegurar_sd_montada(periodo_reintento_ms=3000):
    global _ULTIMO_INTENTO_SD_MS

    try:
        os.listdir('/sd')
        return True
    except Exception:
        pass

    ahora = time.ticks_ms()
    if _ULTIMO_INTENTO_SD_MS and time.ticks_diff(ahora, _ULTIMO_INTENTO_SD_MS) < periodo_reintento_ms:
        return False

    _ULTIMO_INTENTO_SD_MS = ahora
    return _montar_sd_para_extraccion()


def _estado_sd():
    ruta_sd = _archivo_sd_para_descarga()
    if not ruta_sd:
        return {
            "sd_ok": False,
            "archivo": None,
            "size": 0
        }

    size = 0
    try:
        size = os.stat(ruta_sd)[6]
    except Exception:
        size = 0

    return {
        "sd_ok": True,
        "archivo": ruta_sd.split('/')[-1],
        "size": size
    }


def _leer_archivo_binario(ruta):
    try:
        with open(ruta, 'rb') as f:
            return f.read()
    except Exception:
        return None


def _pagina_html_extraccion(estado):
        archivo = estado.get("archivo") or "No detectado"
        size = int(estado.get("size") or 0)
        sd_ok = bool(estado.get("sd_ok"))

        estado_chip = '<span class="chip bad" id="sd-chip">SD no disponible</span>'
        if sd_ok:
                estado_chip = '<span class="chip ok" id="sd-chip">SD detectada</span>'

        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>ESP32 | Centro de Descarga SD</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            min-height: 100vh;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
            color: #e2e8f0;
            background: radial-gradient(circle at 15% 20%, #1d4ed8 0%, rgba(29, 78, 216, 0) 40%),
                        radial-gradient(circle at 85% 15%, #0ea5e9 0%, rgba(14, 165, 233, 0) 35%),
                        #0b1220;
            padding: 24px;
        }}
        .wrap {{ max-width: 780px; margin: 0 auto; }}
        .header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
        }}
        .brand {{ font-size: 13px; color: #93c5fd; letter-spacing: 0.08em; text-transform: uppercase; }}
        .card {{
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 18px;
            padding: 20px;
            box-shadow: 0 18px 40px rgba(2, 6, 23, 0.45);
            backdrop-filter: blur(4px);
        }}
        h1 {{ margin: 0 0 6px; font-size: 24px; }}
        .subtitle {{ margin: 0 0 18px; color: #94a3b8; font-size: 14px; }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px;
            margin: 12px 0 18px;
        }}
        .tile {{
            background: rgba(30, 41, 59, 0.55);
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 12px;
            padding: 12px;
        }}
        .k {{ color: #94a3b8; font-size: 12px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.06em; }}
        .v {{ font-size: 16px; font-weight: 600; word-break: break-all; }}
        .chip {{ display: inline-flex; padding: 5px 11px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
        .ok {{ background: #064e3b; color: #6ee7b7; border: 1px solid #10b981; }}
        .bad {{ background: #7f1d1d; color: #fecaca; border: 1px solid #ef4444; }}
        .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }}
        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            text-decoration: none;
            padding: 10px 14px;
            border-radius: 10px;
            border: 1px solid transparent;
            color: #fff;
            font-weight: 600;
            font-size: 14px;
            transition: transform .15s ease, opacity .15s ease;
        }}
        .btn:active {{ transform: translateY(1px); }}
        .btn-primary {{ background: linear-gradient(135deg, #2563eb, #0ea5e9); }}
        .btn-secondary {{ background: #1f2937; border-color: #334155; color: #cbd5e1; }}
        .btn.disabled {{ pointer-events: none; opacity: 0.45; }}
        .foot {{ margin-top: 14px; color: #94a3b8; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="header">
            <div class="brand">ESP32 · Data Extraction</div>
            <div>{estado_chip}</div>
        </div>

        <div class="card">
            <h1>Centro de descarga SD</h1>
            <p class="subtitle">Descarga segura de datos directamente desde la tarjeta SD.</p>

            <div class="grid">
                <div class="tile">
                    <div class="k">Archivo disponible</div>
                    <div class="v" id="archivo">{archivo}</div>
                </div>
                <div class="tile">
                    <div class="k">Tamaño</div>
                    <div class="v" id="tam">{size} bytes</div>
                </div>
                <div class="tile">
                    <div class="k">Red Wi-Fi</div>
                    <div class="v">{ssid}</div>
                </div>
            </div>

            <div class="actions">
                <a id="btn-download" class="btn btn-primary" href="/descargar">⬇ Descargar SD</a>
                <a class="btn btn-secondary" href="/">↻ Recargar</a>
            </div>

            <p class="foot">Esta vista se actualiza automáticamente cada 2 segundos.</p>
        </div>
    </div>

    <script>
        (function () {{
            var chip = document.getElementById('sd-chip');
            var archivoEl = document.getElementById('archivo');
            var tamEl = document.getElementById('tam');
            var btn = document.getElementById('btn-download');

            function render(data) {{
                var ok = !!data.sd_ok;
                archivoEl.textContent = data.archivo || 'No detectado';
                tamEl.textContent = String(data.size || 0) + ' bytes';

                if (ok) {{
                    chip.textContent = 'SD detectada';
                    chip.className = 'chip ok';
                    btn.className = 'btn btn-primary';
                    btn.href = '/descargar';
                }} else {{
                    chip.textContent = 'SD no disponible';
                    chip.className = 'chip bad';
                    btn.className = 'btn btn-primary disabled';
                    btn.removeAttribute('href');
                }}
            }}

            function refresh() {{
                fetch('/estado', {{ cache: 'no-store' }})
                    .then(function (r) {{ return r.json(); }})
                    .then(render)
                    .catch(function () {{}});
            }}

            refresh();
            setInterval(refresh, 2000);
        }})();
    </script>
</body>
</html>""".format(
                estado_chip=estado_chip,
                archivo=archivo,
                size=size,
                ssid=AP_SSID,
        )


def _iniciar_ap_extraccion():
    global _AP_INSTANCIA

    if _AP_INSTANCIA is None:
        _AP_INSTANCIA = network.WLAN(network.AP_IF)
    ap = _AP_INSTANCIA

    try:
        if ap.active():
            try:
                ip = ap.ifconfig()[0]
                if ip and ip != "0.0.0.0":
                    return ap
            except Exception:
                pass
    except Exception:
        pass

    try:
        if not ap.active():
            ap.active(True)
            time.sleep_ms(300)
    except Exception as e:
        raise Exception("No se pudo activar AP: {}".format(e))

    ultimo_error = None
    auth_open = getattr(network, "AUTH_OPEN", 0)
    auth_wpa2 = getattr(network, "AUTH_WPA2_PSK", 3)
    auth_mixed = getattr(network, "AUTH_WPA_WPA2_PSK", 4)
    intentos = [
        {"essid": AP_SSID, "password": AP_PASSWORD, "authmode": auth_mixed},
        {"essid": AP_SSID, "password": AP_PASSWORD, "authmode": auth_wpa2},
        {"essid": AP_SSID, "password": AP_PASSWORD},
        {"essid": AP_SSID, "authmode": auth_open},
        {"essid": AP_SSID},
    ]

    for cfg in intentos:
        try:
            ap.config(**cfg)
            time.sleep_ms(200)
            if not ap.active():
                ap.active(True)
                time.sleep_ms(150)

            try:
                ip = ap.ifconfig()[0]
                if ip and ip != "0.0.0.0":
                    return ap
            except Exception:
                pass

            return ap
        except Exception as e:
            ultimo_error = e
            try:
                print("[EXTRACCION] AP config falló: {}".format(e))
            except Exception:
                pass
            continue

    try:
        if ap.active():
            return ap
    except Exception:
        pass

    if ultimo_error:
        raise ultimo_error
    return ap


def _iniciar_web_extraccion():
    if gc is not None:
        try:
            gc.collect()
        except Exception:
            pass

    ap = _iniciar_ap_extraccion()
    ip_info = ("0.0.0.0", "", "", "")
    try:
        ip_info = ap.ifconfig() if ap else ip_info
    except Exception:
        pass

    if ip_info[0] == "0.0.0.0":
        try:
            ap.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "0.0.0.0"))
            time.sleep_ms(100)
            ip_info = ap.ifconfig()
        except Exception:
            pass

    servidor = None
    try:
        addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
        servidor = socket.socket()
        servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        servidor.bind(addr)
        servidor.listen(2)
        servidor.settimeout(0.2)
    except Exception as e:
        try:
            if servidor:
                servidor.close()
        except Exception:
            pass
        raise Exception("No se pudo iniciar servidor HTTP: {}".format(e))

    return ap, servidor, ip_info[0]


def _detener_ap_extraccion(ap):
    try:
        # Evitar deinit explícito para no disparar errores 0x3001 en firmwares sensibles.
        # El AP se cerrará naturalmente al reset de salida de extracción.
        _ = ap
    except Exception:
        pass


def _enviar_archivo_http(cliente, contenido, nombre_descarga):
    try:
        header = (
            'HTTP/1.1 200 OK\r\n'
            'Content-Type: text/csv\r\n'
            'Content-Disposition: attachment; filename="{}"\r\n'
            'Connection: close\r\n\r\n'
        ).format(nombre_descarga)
        cliente.send(header)
        cliente.sendall(contenido)
        return True
    except Exception:
        return False


def _enviar_html_http(cliente, html, status='200 OK'):
    try:
        header = (
            'HTTP/1.1 {}\r\n'
            'Content-Type: text/html; charset=utf-8\r\n'
            'Connection: close\r\n\r\n'
        ).format(status)
        cliente.send(header)
        cliente.sendall(html)
    except Exception:
        pass


def _enviar_json_http(cliente, data, status='200 OK'):
    try:
        header = (
            'HTTP/1.1 {}\r\n'
            'Content-Type: application/json; charset=utf-8\r\n'
            'Connection: close\r\n\r\n'
        ).format(status)
        cliente.send(header)
        cliente.sendall(data)
    except Exception:
        pass


def _atender_cliente_http(cliente, wdt=None):
    _asegurar_sd_montada()
    ruta_sd = _archivo_sd_para_descarga()
    estado = _estado_sd()

    try:
        try:
            cliente.settimeout(1.0)
        except Exception:
            pass

        raw = cliente.recv(1024)
        if not raw:
            return

        if wdt:
            try:
                wdt.feed()
            except Exception:
                pass

        if isinstance(raw, bytes):
            req = raw.decode('utf-8', 'ignore')
        else:
            req = str(raw)

        if 'GET /estado' in req:
            _enviar_json_http(cliente, json.dumps(estado), status='200 OK')
            return

        if 'GET /descargar' in req:
            if ruta_sd:
                contenido = _leer_archivo_binario(ruta_sd)
                if contenido is not None:
                    if wdt:
                        try:
                            wdt.feed()
                        except Exception:
                            pass
                    _enviar_archivo_http(cliente, contenido, ruta_sd.split('/')[-1])
                    return
            _enviar_html_http(cliente, '<h3>No se encontró archivo en microSD</h3>', status='404 Not Found')
            return

        html = _pagina_html_extraccion(estado)
        if wdt:
            try:
                wdt.feed()
            except Exception:
                pass
        _enviar_html_http(cliente, html)
    except Exception:
        pass

def listar_archivos():
    """Lista todos los archivos disponibles para extracción."""
    print("\n" + "="*60)
    print("ARCHIVOS DISPONIBLES PARA EXTRACCION")
    print("="*60 + "\n")
    
    archivos = []
    
    # Archivos en SD (obligatorio)
    _montar_sd_para_extraccion()
    try:
        sd_files = os.listdir('/sd')
        if sd_files:
            print("\n  Archivos en tarjeta SD:")
            for archivo in sd_files:
                try:
                    ruta_sd = f'/sd/{archivo}'
                    size = os.stat(ruta_sd)[6]
                    archivos.append((ruta_sd, size, 'SD'))
                    print(f"  ✓ {archivo} ({size} bytes) - SD")
                except Exception as e:
                    print(f"  ✗ Error: {e}")
    except Exception as e:
        print(f"  ✗ Tarjeta SD no disponible: {e}")
    
    print("\n" + "="*60 + "\n")
    return archivos


def mostrar_contenido_archivo(ruta):
    """Muestra el contenido de un archivo en pantalla."""
    try:
        print(f"\n{'='*60}")
        print(f"CONTENIDO: {ruta}")
        print('='*60 + "\n")
        
        with open(ruta, 'r') as f:
            lineas = f.readlines()
            total = len(lineas)
            
            # Mostrar primeras 20 líneas y las últimas 10
            print(f"Mostrando {min(20, total)} de {total} lineas:\n")
            
            for i, linea in enumerate(lineas[:20]):
                print(linea.rstrip())
            
            if total > 30:
                print(f"\n... ({total - 30} lineas omitidas) ...\n")
                for linea in lineas[-10:]:
                    print(linea.rstrip())
            
            print(f"\n{'='*60}")
            print(f"Total de lineas: {total}")
            print('='*60 + "\n")
        
        return True
    except Exception as e:
        print(f"✗ Error leyendo archivo: {e}\n")
        return False


def limpiar_archivos_locales():
    """Limpia archivos locales después de extracción."""
    print("\n" + "="*60)
    print("LIMPIEZA DE ARCHIVOS LOCALES")
    print("="*60 + "\n")
    
    print("Eliminando archivos locales...")
    print("  Los datos en SD se conservaran.\n")
    
    eliminados = 0
    
    try:
        if 'lecturas_local.txt' in os.listdir('.'):
            os.remove('lecturas_local.txt')
            print("✓ lecturas_local.txt eliminado")
            eliminados += 1
    except Exception as e:
        print(f"✗ Error eliminando lecturas_local.txt: {e}")
    
    try:
        if 'log.txt' in os.listdir('.'):
            os.remove('log.txt')
            print("✓ log.txt eliminado")
            eliminados += 1
    except Exception as e:
        print(f"✗ Error eliminando log.txt: {e}")
    
    print(f"\n✓ Limpieza completada: {eliminados} archivos eliminados\n")
    return True


def modo_extraccion():
    """
    Modo de extracción de datos con switch de dos estados.
    Estado 1: Visualizar archivos disponibles
    Estado 2: Salir y reiniciar sistema
    
    Presionar botón 3 segundos durante extracción para volver al sistema normal.
    """
    # Mostrar en OLED que se activó modo extracción
    if oled:
        oled.mostrar_modo_extraccion_activado()
        time.sleep(2)
    
    print("\n" + "╔" + "="*58 + "╗")
    print("║" + " "*58 + "║")
    print("║" + "  MODO EXTRACCION DE DATOS".center(58) + "║")
    print("║" + "  Sistema en pausa - Lectura de sensores pausada".center(58) + "║")
    print("║" + " "*58 + "║")
    print("╚" + "="*58 + "╝\n")
    
    # Estado 1: Listar y mostrar archivos disponibles en SD
    print("═ ESTADO 1: VISUALIZANDO ARCHIVOS ═\n")
    listar_archivos()

    sd_ready = _asegurar_sd_montada(periodo_reintento_ms=0)
    if not sd_ready:
        print("✗ SD requerida: inserta la tarjeta SD para habilitar descarga.")
    
    # ═ Esperar a que se presione botón 3 segundos para salir ═
    print("\n" + "="*60)
    print("PRESIONA EL BOTON 3 SEGUNDOS PARA VOLVER AL SISTEMA")
    print("="*60 + "\n")
    
    ap = None
    servidor = None
    wdt = None
    wifi_reintento_habilitado = True

    try:
        from boton_extraccion import BotonExtraccion
        try:
            from machine import WDT  # type: ignore
            wdt = WDT(timeout=20000)
            print("[EXTRACCION] Watchdog activo (20s)")
        except Exception:
            wdt = None

        boton_salida = BotonExtraccion(
            pin_numero=34,
            tiempo_debounce_ms=50,
            tiempo_presion_minimo_ms=3000,
            tipo_boton="NA"
        )

        try:
            ap, servidor, ip = _iniciar_web_extraccion()

            print("\n[EXTRACCION] AP iniciado: {}".format(AP_SSID))
            print("[EXTRACCION] Clave: {}".format(AP_PASSWORD))
            print("[EXTRACCION] IP: {}".format(ip))
            print("[EXTRACCION] Abre en navegador: http://{}".format(ip))
        except Exception as e:
            ap = None
            servidor = None
            print("\n[EXTRACCION] WiFi no disponible: {}".format(e))
            print("[EXTRACCION] Continuando en modo extracción sin web (salida por botón activa).")
            wifi_reintento_habilitado = False
        
        print("Aguardando presión de botón para salir...")
        print("Primero suelta el botón para armar salida.")
        
        salir = False
        salida_armada = False
        aviso_armado_mostrado = False
        last_wifi_retry = time.ticks_ms()
        while not salir:
            now = time.ticks_ms()

            if wifi_reintento_habilitado and (not servidor) and time.ticks_diff(now, last_wifi_retry) >= 7000:
                last_wifi_retry = now
                try:
                    if wdt:
                        try:
                            wdt.feed()
                        except Exception:
                            pass
                    ap, servidor, ip = _iniciar_web_extraccion()
                    print("[EXTRACCION] WiFi recuperado. Abre en navegador: http://{}".format(ip))
                except Exception as e:
                    msg = str(e)
                    if ("0x0101" in msg) or ("0x3001" in msg) or ("duplicate key" in msg) or ("rx buffer" in msg):
                        wifi_reintento_habilitado = False
                        print("[EXTRACCION] Reintentos WiFi deshabilitados por fallo fatal del driver.")

            # Atender cliente HTTP sin bloquear salida por botón
            if servidor:
                try:
                    cliente, direccion = servidor.accept()
                    print("[EXTRACCION] Cliente conectado desde {}".format(direccion))
                    try:
                        cliente.settimeout(1.0)
                    except Exception:
                        pass
                    try:
                        if wdt:
                            try:
                                wdt.feed()
                            except Exception:
                                pass
                        _atender_cliente_http(cliente, wdt=wdt)
                    finally:
                        try:
                            cliente.close()
                        except Exception:
                            pass
                except Exception:
                    pass

            # Watchdog: alimentar justo después del if de servidor
            if wdt:
                try:
                    wdt.feed()
                except Exception:
                    pass

            if not salida_armada:
                # Evita re-disparo inmediato por el mismo botón que activó extracción.
                if not boton_salida.esta_presionado():
                    salida_armada = True
                    print("Salida armada. Mantén botón 3s para reiniciar.")
                else:
                    if oled:
                        oled.mostrar_esperando_salir()
                    time.sleep(0.1)
                    continue
            
            if boton_salida.detectar_presion_sostenida():
                print("\n✓ Presión detectada - Limpiando y volviendo al sistema...\n")
                # Mostrar en OLED que se está saliendo
                if oled:
                    for i in range(3, 0, -1):
                        oled.mostrar_esperando_salir(i)
                        time.sleep(1)
                salir = True
            elif boton_salida.tiempo_presion_inicio is not None:
                # Mostrar progreso mientras se presiona
                tiempo_transcurrido_ms = time.ticks_diff(time.ticks_ms(), boton_salida.tiempo_presion_inicio)
                segundos_restantes = max(0, int(3 - (tiempo_transcurrido_ms / 1000.0)))
                
                if oled and segundos_restantes > 0:
                    oled.mostrar_boton_presionado(segundos_restantes)
            else:
                # Mostrar instrucción en OLED
                if oled:
                    oled.mostrar_esperando_salir()

                if not aviso_armado_mostrado:
                    print("Aguardando nueva presión sostenida (3s) para salir...")
                    aviso_armado_mostrado = True
            
            time.sleep(0.1)
    
    except Exception as e:
        print(f"Error esperando botón: {e}")
        print("Continuando sin detección de botón...")
        if oled:
            time.sleep(3)
    finally:
        try:
            if servidor:
                servidor.close()
        except Exception:
            pass
        _detener_ap_extraccion(ap)
    
    # Estado 2: Salir
    print("═ ESTADO 2: SALIENDO ═\n")
    print("Cerrando modo extracción (datos locales no utilizados).")
    time.sleep(1)
    
    print("\nReiniciando sistema en 5 segundos...\n")
    time.sleep(5)
    
    try:
        from machine import reset  # type: ignore
        reset()
    except Exception as e:
        print(f"✗ Error en reinicio: {e}")

