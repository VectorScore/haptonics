"""Directly prove that the standalone 0832 LRA firmware can vibrate."""
import time
import sys
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kick_only_debug import PORT, SERIAL_BAUD


def open_serial_no_reset():
    esp = serial.Serial()
    esp.port = PORT
    esp.baudrate = SERIAL_BAUD
    esp.timeout = 0
    esp.write_timeout = 0.1
    esp.dsrdtr = False
    esp.rtscts = False
    esp.dtr = False
    esp.rts = False
    esp.open()
    return esp


with open_serial_no_reset() as esp:
    time.sleep(2.0)
    print("Sending H100 0832 LRA taps. If nothing happens, press BOOT once now.")
    try:
        while True:
            esp.write(b"H100\n")
            time.sleep(0.10)
            esp.write(b"H0\n")
            time.sleep(0.50)
    finally:
        esp.write(b"H0\n")
