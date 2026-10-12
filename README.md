# Haptonics

## Website and vibration path

| Role | Files |
| --- | --- |
| Website | `web/musex.html`, `web/shepherd.js`, `web/assets/shepherd.png` |
| Website checks | `web/tests/shepherd.test.cjs` |
| Compatible vibration firmware | `lra_0832_ble_firmware/` |

The website runs in a browser. It uses `navigator.vibrate()` on supported phones. For an external motor, it connects over Web Bluetooth to the Nordic UART Service (`6e400001-b5a3-f393-e0a9-e50e24dcca9e`) and writes `H0`–`H127` intensity commands to its RX characteristic (`6e400002-b5a3-f393-e0a9-e50e24dcca9e`). `lra_0832_ble_firmware/main/main.c` implements that service and drives an 0832 LRA through a DRV2605L. Press BOOT on the device to enable the motor after connecting. The website does not run the Python USB serial bridges.

To run the site locally from the repository root:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/web/musex.html`. Demo visuals work without hardware. Microphone, device audio, and Bluetooth depend on browser and OS support and permissions.

To build the matching ESP32-S3 firmware with ESP-IDF installed:

```sh
idf.py -C lra_0832_ble_firmware build
idf.py -C lra_0832_ble_firmware -p YOUR_SERIAL_PORT flash monitor
```

Confirm the board, motor rating, and DRV2605L wiring before flashing. The firmware source and defaults are in the folder above. See [web/README.md](web/README.md) for app details.

## Older serial experiments

USB serial bridges, other motor firmware, manual motor probes, and Python regression tests are in [archive/serial/](archive/serial/). They are not needed to run the website or its BLE vibration path. The older bridge code is incomplete in this folder: `erm_bass_rhythm.py` and `mac_mic_haptics.py` are absent. See [archive/serial/README.md](archive/serial/README.md) for its historical notes.

Run the website checks with `node web/tests/shepherd.test.cjs`.
