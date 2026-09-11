# DS18x20 temperature sensor driver for MicroPython.
# Convenience adapter class for reading a single DS18X20 temperature sensor
# 
# BUG FIX from original:
#   - convert_temp() method corrected to not pass extra arguments to writebyte()
#   - Adds convert_and_read() convenience method
#
# Original MIT license; Copyright (c) 2019 Andreas Motl
# Fixed: 2026

from ds18x20 import DS18X20
import time as _time


class DS18X20Single(DS18X20):
    def __init__(self, onewire):
        super().__init__(onewire)
        self.roms = self.scan()
        assert len(self.roms) == 1, "Not (only) one sensor on the bus"

    def convert_temp(self):
        """
        Start temperature conversion for all devices on bus.
        BUG FIX: Original calls writebyte(CMD_CONVERT, self.powerpin) but 
        writebyte() only takes 1 argument. This is now corrected.
        """
        self.ow.reset()
        self.ow.writebyte(self.ow.CMD_SKIPROM)
        self.ow.writebyte(0x44)  # CMD_CONVERT (without extra argument!)

    def read_temp(self):
        """Read temperature from first sensor on bus."""
        try:
            return super().read_temp(self.roms[0])
        except Exception:
            return None

    def convert_and_read(self, wait_ms=800):
        """
        Convert temperature and read result.
        wait_ms: milliseconds to wait for conversion (default 800ms for 12-bit)
        Returns: temperature in Celsius or None if error
        """
        try:
            self.convert_temp()
            _time.sleep_ms(wait_ms)
            return self.read_temp()
        except Exception:
            return None
