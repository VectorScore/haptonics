"""Directly prove that the second-actuator LRA firmware can vibrate."""
import time

import serial

from kick_only_debug import resolve_serial_port

PORT = resolve_serial_port()
BAUD = 115200


def open_serial_no_reset():
    esp = serial.Serial()
    esp.port = PORT
    esp.baudrate = BAUD
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
    print("Sending L100 LRA taps. If nothing happens, press BOOT once now.")
    try:
        while True:
            esp.write(b"L100\n")
            time.sleep(0.10)
            esp.write(b"L0\n")
            time.sleep(0.50)
    finally:
        esp.write(b"L0\n")
