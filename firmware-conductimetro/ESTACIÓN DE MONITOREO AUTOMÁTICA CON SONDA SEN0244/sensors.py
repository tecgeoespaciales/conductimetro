import json
import time
from machine import Pin, I2C

try:
    import ads1x15
except Exception:
    ads1x15 = None

try:
    from onewire import OneWire
except Exception:
    OneWire = None

try:
    from ds18x20_single import DS18X20Single
except Exception:
    DS18X20Single = None

try:
    import ds1307
except Exception:
    ds1307 = None


class SensorSystem:
    def __init__(self, config):
        self.config = config
        self.i2c = None
        self.rtc = None
        self.adc = None
        self.temp_sensor = None
        self.last_temp = float(config.TEMP_REF)
        self.last_temp_request = time.ticks_ms()
        self._temp_reinit_last_try = time.ticks_ms()
        self._temp_reinit_interval_ms = 5000

    def _try_init_temp_sensor(self, emit_status=False):
        """Intenta inicializar/reinicializar el sensor de temperatura DS18B20.

        Returns:
            bool: True si el sensor quedó operativo, False en caso contrario.
        """
        cfg = self.config
        if OneWire is None or DS18X20Single is None:
            self.temp_sensor = None
            return False

        try:
            ow = OneWire(Pin(cfg.TEMP_SENSOR_PIN))
            sensor = DS18X20Single(ow)
            self.temp_sensor = sensor

            temp = sensor.convert_and_read(wait_ms=1200)
            if temp is not None and temp != 85.0:
                self.last_temp = float(temp) + cfg.TEMP_OFFSET
                self.last_temp_request = time.ticks_ms()

            if emit_status:
                payload = {"type": "temp", "status": "ok"}
                if temp is not None and temp != 85.0:
                    payload["value"] = round(self.last_temp, 2)
                else:
                    payload["msg"] = "sensor_init_sin_muestra_valida"
                print(json.dumps(payload))
            return True
        except Exception as error:
            self.temp_sensor = None
            if emit_status:
                print(json.dumps({"type": "temp", "status": "error", "msg": str(error)[:40]}))
            return False

    def init_hw(self):
        cfg = self.config

        self.i2c = I2C(cfg.I2C_ID, scl=Pin(cfg.I2C_SCL), sda=Pin(cfg.I2C_SDA), freq=100_000)

        if ds1307 is not None:
            try:
                self.rtc = ds1307.DS1307(self.i2c)
                print(json.dumps({"type": "rtc", "status": "ok"}))
            except Exception as error:
                self.rtc = None
                print(json.dumps({"type": "rtc", "status": "error", "msg": str(error)[:40]}))

        if ads1x15 is not None:
            try:
                self.adc = ads1x15.ADS1115(self.i2c, address=cfg.ADS_ADDRESS, gain=cfg.ADS_GAIN)
                print(json.dumps({"type": "adc", "status": "ok"}))
            except Exception as error:
                self.adc = None
                print(json.dumps({"type": "adc", "status": "error", "msg": str(error)[:40]}))

        self._try_init_temp_sensor(emit_status=True)

    def adc_read_voltage(self):
        if self.adc is None:
            return 0.0
        try:
            raw = self.adc.read(self.config.ADS_RATE, self.config.ADS_CHANNEL)
            if raw is None:
                return 0.0
            voltage = self.adc.raw_to_v(raw)
            if voltage is None:
                return 0.0
            value = float(voltage)
            if value < 0.0 or value > 4.5:
                return 0.0
            threshold = float(getattr(self.config, "NOISE_THRESHOLD", 0.0) or 0.0)
            if threshold > 0.0 and abs(value) < threshold:
                return 0.0
            return value
        except Exception:
            return 0.0

    def read_temp(self):
        cfg = self.config
        if self.temp_sensor is None:
            now = time.ticks_ms()
            if time.ticks_diff(now, self._temp_reinit_last_try) >= self._temp_reinit_interval_ms:
                self._temp_reinit_last_try = now
                self._try_init_temp_sensor(emit_status=False)

            # Si aún no hay sensor disponible, conservar última temperatura válida
            # en lugar de fijar siempre TEMP_REF.
            return float(self.last_temp)

        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self.last_temp_request)
        if elapsed < int(cfg.TEMP_CONV_TIME * 1000):
            return self.last_temp

        try:
            raw = self.temp_sensor.convert_and_read(wait_ms=900)
            if raw is not None and raw != 85.0:
                temp_new = float(raw) + cfg.TEMP_OFFSET
                if -40.0 <= temp_new <= 125.0:
                    self.last_temp = temp_new
        except Exception:
            pass

        self.last_temp_request = now
        return self.last_temp

    def voltage_to_ec(self, voltage):
        cfg = self.config
        v = float(voltage)
        ec = cfg.POLY_A * v * v * v - cfg.POLY_B * v * v + cfg.POLY_C * v
        if ec < 0:
            return 0.0
        return ec

    def read_measurement(self):
        voltage = self.adc_read_voltage()
        temp = self.read_temp()
        ec_poly = self.voltage_to_ec(voltage)
        return voltage, temp, ec_poly
