"""Full ERM haptic algorithm with only its frequency routing moved upward.

The causal detector, adaptive thresholds, event state machine, hit/dip/recover
shape, timing, level limits and serial rate limiting all live in
erm_bass_rhythm.py and are shared unchanged.  This entry point selects the
800–8000 Hz LRA profile and sends the resulting level to the selected serial
channel.  The current dual-driver firmware routes L to the 1030Z LRA.
"""

import argparse
from dataclasses import replace
import math
import shutil
import sys
import time

import numpy as np
import serial
import sounddevice as sd
from scipy.signal import butter, sosfilt

from erm_bass_rhythm import (
    AUDIO_DEVICE,
    BLOCK_SIZE,
    CHANNELS,
    LRA_HIGH_FREQUENCY_PROFILE,
    NON_TTY_PRINT_INTERVAL_SECONDS,
    PORT,
    PRINT_INTERVAL_SECONDS,
    SAMPLE_RATE,
    SERIAL_BAUD,
    TTY_CLEAR_LINE,
    EdmErmProcessor,
    find_input_device,
    format_status_line,
    should_send_frame,
)

LRA_TEXTURE_GATE_LOW_HZ = 800.0
LRA_TEXTURE_GATE_HIGH_HZ = 8_000.0
LRA_TEXTURE_GATE_ORDER = 4
LRA_TEXTURE_OPEN_DBFS = -58.0
LRA_TEXTURE_CLOSE_DBFS = -66.0
LRA_TEXTURE_REOPEN_RISE_DB = 8.0
LRA_TEXTURE_BACKGROUND_SECONDS = 0.750
LRA_STARTUP_PROOF_SECONDS = 30.0
LRA_STARTUP_PROOF_LEVEL = 100
LRA_STARTUP_PROOF_ON_SECONDS = 0.12
LRA_STARTUP_PROOF_OFF_SECONDS = 0.35
DEFAULT_SERIAL_CHANNEL = "L"
MIN_DBFS = -120.0


def dbfs(amplitude):
    if amplitude <= 1e-8:
        return MIN_DBFS
    return 20.0 * math.log10(amplitude)


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


def serial_command(channel, level):
    """Encode one bounded haptic command for either ESP32 firmware."""
    channel = channel.upper()
    if channel not in ("H", "L"):
        raise ValueError("serial channel must be H or L")
    level = max(0, min(127, int(level)))
    return f"{channel}{level}\n".encode("ascii")


class LraTextureGate:
    """Suppress 1030Z output when the routed high-frequency band is silence."""

    def __init__(self, sample_rate=SAMPLE_RATE, channels=CHANNELS):
        self.sos = butter(
            LRA_TEXTURE_GATE_ORDER,
            (LRA_TEXTURE_GATE_LOW_HZ, LRA_TEXTURE_GATE_HIGH_HZ),
            btype="bandpass",
            fs=sample_rate,
            output="sos",
        )
        self.zi = np.zeros((len(self.sos), 2, channels))
        self.background = 0.0
        self.background_retained = math.exp(
            -BLOCK_SIZE / (sample_rate * LRA_TEXTURE_BACKGROUND_SECONDS)
        )
        self.open = False
        self.level_dbfs = MIN_DBFS
        self.novelty_db = 0.0

    def update(self, audio_block):
        block = np.asarray(audio_block, dtype=np.float64)
        high_audio, self.zi = sosfilt(self.sos, block, axis=0, zi=self.zi)
        level = float(np.sqrt(np.mean(high_audio * high_audio)))
        self.background = (
            self.background_retained * self.background
            + (1.0 - self.background_retained) * level
        )
        self.level_dbfs = dbfs(level)
        self.novelty_db = self.level_dbfs - dbfs(self.background)

        should_open = (
            self.level_dbfs >= LRA_TEXTURE_OPEN_DBFS
            or (
                self.level_dbfs >= LRA_TEXTURE_CLOSE_DBFS
                and self.novelty_db >= LRA_TEXTURE_REOPEN_RISE_DB
            )
        )
        if should_open:
            self.open = True
        elif self.level_dbfs < LRA_TEXTURE_CLOSE_DBFS:
            self.open = False
        return self.open


def mute_frame(frame):
    return replace(
        frame,
        motor_level=0,
        bass_level=0,
        detected_event="none",
        active_event="none",
        phase="bass",
    )


def apply_texture_gate(frame, gate):
    if not gate.open:
        return mute_frame(frame)
    return frame


def format_lra_status(
    frame,
    processing_ms,
    gate,
    serial_channel,
    last_command,
    send_count,
    columns,
    warning="",
):
    line = format_status_line(
        frame,
        processing_ms,
        columns,
        warning=warning,
    )
    line = "L" + line[1:] if line.startswith("M") else line
    prefix = (
        f"cmd {serial_channel}{last_command:03d} sent {send_count} "
        f"gate {'OPEN' if gate.open else 'CLOSED'} "
        f"{gate.level_dbfs:+.0f}dB nov {gate.novelty_db:+.0f} "
    )
    return (prefix + line)[:max(19, columns - 1)]


def main():
    parser = argparse.ArgumentParser(
        description="1030Z LRA high-frequency bridge for BlackHole audio."
    )
    parser.add_argument(
        "--no-startup-proof",
        action="store_true",
        help="skip the L100 startup tap loop before audio capture",
    )
    parser.add_argument(
        "--startup-proof",
        action="store_true",
        help="send L100 proof taps before audio capture",
    )
    parser.add_argument(
        "--startup-proof-seconds",
        type=float,
        default=LRA_STARTUP_PROOF_SECONDS,
        help="seconds to send L100 proof taps before audio capture",
    )
    parser.add_argument(
        "--serial-channel",
        choices=("H", "L", "h", "l"),
        default=DEFAULT_SERIAL_CHANNEL,
        help=(
            "ESP32 command prefix: L for the 1030Z LRA on the current "
            "dual-driver firmware (default); H selects the other driver"
        ),
    )
    args = parser.parse_args()
    serial_channel = args.serial_channel.upper()

    device_index = find_input_device(AUDIO_DEVICE)
    processor = EdmErmProcessor(
        frequency_profile=LRA_HIGH_FREQUENCY_PROFILE
    )
    gate = LraTextureGate()
    interactive_output = sys.stdout.isatty()
    print_interval = (
        PRINT_INTERVAL_SECONDS
        if interactive_output
        else NON_TTY_PRINT_INTERVAL_SECONDS
    )
    last_send = 0.0
    last_print = 0.0
    last_phase = None
    last_command = 0
    send_count = 0

    with open_serial_no_reset() as esp:
        time.sleep(2.0)
        esp.reset_input_buffer()
        # Never pulse DTR here: on ESP32 boards it controls reset/EN, not BOOT.
        # Resetting at this point would latch the firmware off immediately
        # before music commands begin.
        esp.write(serial_command(serial_channel, 0))
        send_count += 1

        if args.startup_proof and not args.no_startup_proof:
            print(
                f"Press BOOT now. Sending {serial_channel}100 proof taps for "
                f"{args.startup_proof_seconds:.0f}s before audio starts.",
                flush=True,
            )
            proof_deadline = time.monotonic() + max(
                0.0,
                args.startup_proof_seconds,
            )
            while time.monotonic() < proof_deadline:
                esp.write(
                    serial_command(
                        serial_channel,
                        LRA_STARTUP_PROOF_LEVEL,
                    )
                )
                last_command = LRA_STARTUP_PROOF_LEVEL
                send_count += 1
                time.sleep(LRA_STARTUP_PROOF_ON_SECONDS)
                esp.write(serial_command(serial_channel, 0))
                last_command = 0
                send_count += 1
                time.sleep(LRA_STARTUP_PROOF_OFF_SECONDS)

        def callback(indata, frames, timing, status):
            nonlocal last_send, last_print, last_phase
            nonlocal last_command, send_count
            callback_started = time.perf_counter()
            frame = processor.process(indata)
            gate.update(indata)
            frame = apply_texture_gate(frame, gate)
            now = time.monotonic()

            # Identical transition-aware rate limiting to the ERM bridge.
            if (
                frame.motor_level != last_command
                or should_send_frame(frame, last_phase, now, last_send)
            ):
                esp.write(
                    serial_command(
                        serial_channel,
                        frame.motor_level,
                    )
                )
                last_command = frame.motor_level
                send_count += 1
                last_send = now
            last_phase = frame.phase

            if now - last_print >= print_interval:
                processing_ms = (
                    time.perf_counter() - callback_started
                ) * 1_000.0
                columns = shutil.get_terminal_size(
                    fallback=(100, 24)
                ).columns
                line = format_lra_status(
                    frame,
                    processing_ms,
                    gate,
                    serial_channel,
                    last_command,
                    send_count,
                    columns,
                    warning=str(status) if status else "",
                )
                if interactive_output:
                    print(TTY_CLEAR_LINE + line, end="", flush=True)
                else:
                    print(line, flush=True)
                last_print = now

        print(
            "EDM LRA high-frequency mode: the full ERM algorithm moved to "
            f"800–8000 Hz. Sending {serial_channel}0–{serial_channel}127. "
            "Press BOOT once after this message, then play music; "
            "Ctrl-C stops the motor."
        )
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
                    esp.write(serial_command(serial_channel, 0))
                except serial.SerialException:
                    break
                time.sleep(0.02)
            if interactive_output:
                print()
            print("LRA stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
