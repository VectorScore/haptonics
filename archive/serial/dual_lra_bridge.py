"""Run the 0832 kick motor and 1030Z texture motor from one audio stream.

The two DRV2605L boards share one ESP32 USB serial connection but use separate
commands:

    H0..H127 -> 0832 LRA, low-frequency kick pulse
    L0..L127 -> 1030Z LRA, 800..8000 Hz texture

Only this process may open the serial port.  Do not run lra_texture.py or
lra_0832_kick_debug.py beside it.
"""

import shutil
import sys
import time

import serial
import sounddevice as sd

from erm_bass_rhythm import (
    AUDIO_DEVICE,
    BLOCK_SIZE,
    CHANNELS,
    LRA_HIGH_FREQUENCY_PROFILE,
    PORT,
    SAMPLE_RATE,
    SERIAL_BAUD,
    TTY_CLEAR_LINE,
    EdmErmProcessor,
    find_input_device,
)
from kick_only_debug import KEEPALIVE_SECONDS
from lra_0832_kick_debug import Lra0832KickProcessor
from lra_texture import (
    LraTextureGate,
    apply_texture_gate,
    serial_command,
)


PRINT_INTERVAL_SECONDS = 0.100


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


def command_pair(h_level, l_level):
    """Encode both channels in one serial write."""
    return serial_command("H", h_level) + serial_command("L", l_level)


def format_status(
    kick_frame,
    texture_frame,
    gate,
    h_command,
    l_command,
    send_count,
    processing_ms,
    columns,
    warning="",
):
    warning_text = f" !{warning}" if warning else ""
    line = (
        f"0832 H{h_command:03d} {kick_frame.phase.upper()} "
        f"hits {kick_frame.kick_count} "
        f"| 1030Z L{l_command:03d} "
        f"{'OPEN' if gate.open else 'CLOSED'} "
        f"{gate.level_dbfs:+.0f}dB {texture_frame.phase} "
        f"| sent {send_count} DSP {processing_ms:.2f}ms"
        f"{warning_text}"
    )
    return line[:max(19, int(columns) - 1)]


def main():
    device_index = find_input_device(AUDIO_DEVICE)
    kick_processor = Lra0832KickProcessor()
    texture_processor = EdmErmProcessor(
        frequency_profile=LRA_HIGH_FREQUENCY_PROFILE
    )
    texture_gate = LraTextureGate()
    interactive = sys.stdout.isatty()

    last_h_level = None
    last_l_level = None
    last_h_send = 0.0
    last_l_send = 0.0
    last_print = 0.0
    send_count = 0

    with open_serial_no_reset() as esp:
        time.sleep(2.0)
        esp.reset_input_buffer()
        esp.write(command_pair(0, 0))
        send_count += 2

        print(
            "Dual LRA mode: H=0832 kick (20-150 Hz), "
            "L=1030Z texture (800-8000 Hz). "
            "Press BOOT once after this message, then play music. "
            "Ctrl-C stops both motors.",
            flush=True,
        )

        def callback(indata, frames, timing, status):
            nonlocal last_h_level, last_l_level
            nonlocal last_h_send, last_l_send, last_print, send_count

            started = time.perf_counter()
            kick_frame = kick_processor.process(indata)
            texture_frame = texture_processor.process(indata)
            texture_gate.update(indata)
            texture_frame = apply_texture_gate(
                texture_frame,
                texture_gate,
            )
            now = time.monotonic()

            send_h = (
                kick_frame.motor_level != last_h_level
                or kick_frame.detected_kick
                or now - last_h_send >= KEEPALIVE_SECONDS
            )
            send_l = (
                texture_frame.motor_level != last_l_level
                or texture_frame.detected_event != "none"
                or now - last_l_send >= KEEPALIVE_SECONDS
            )

            payload = b""
            if send_h:
                payload += serial_command("H", kick_frame.motor_level)
                last_h_level = kick_frame.motor_level
                last_h_send = now
                send_count += 1
            if send_l:
                payload += serial_command("L", texture_frame.motor_level)
                last_l_level = texture_frame.motor_level
                last_l_send = now
                send_count += 1
            if payload:
                esp.write(payload)

            if now - last_print >= PRINT_INTERVAL_SECONDS:
                processing_ms = (
                    time.perf_counter() - started
                ) * 1_000.0
                columns = shutil.get_terminal_size(
                    fallback=(110, 24)
                ).columns
                line = format_status(
                    kick_frame,
                    texture_frame,
                    texture_gate,
                    last_h_level or 0,
                    last_l_level or 0,
                    send_count,
                    processing_ms,
                    columns,
                    warning=str(status) if status else "",
                )
                if interactive:
                    print(TTY_CLEAR_LINE + line, end="", flush=True)
                else:
                    print(line, flush=True)
                last_print = now

        try:
            with sd.InputStream(
                device=device_index,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                channels=CHANNELS,
                dtype="float32",
                latency="low",
                callback=callback,
            ):
                while True:
                    time.sleep(1.0)
        finally:
            for _ in range(3):
                try:
                    esp.write(command_pair(0, 0))
                except serial.SerialException:
                    break
                time.sleep(0.02)
            if interactive:
                print()
            print("Both LRA motors stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
