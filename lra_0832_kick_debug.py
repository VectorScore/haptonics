"""Kick-only system-audio bridge shaped for an 0832 LRA motor.

Audio recognition remains strictly low-frequency: only sharp 20-150 Hz spikes
can create haptics. The faster LRA receives a short full-strength leading edge,
a lighter body, then zero. There is no bass bed, snare/clap detector, or
high-frequency haptic output.
"""

from dataclasses import dataclass
import argparse
import shutil
import sys
import time

import serial
import sounddevice as sd

from kick_only_debug import (
    AUDIO_DEVICE,
    BLOCK_SIZE,
    CHANNELS,
    KEEPALIVE_SECONDS,
    KickOnlyProcessor,
    PORT,
    SAMPLE_RATE,
    SERIAL_BAUD,
    TTY_CLEAR_LINE,
    find_input_device,
)


LRA_PEAK_LEVEL = 127
# The 0832 and its DRV2605L need a longer command than the original 18 ms
# proof-of-concept pulse to remain obvious when worn and mixed with the 1030Z.
# Onset is still immediate; only the hold/body are lengthened.
LRA_PEAK_SECONDS = 0.045
LRA_BODY_LEVEL = 100
LRA_BODY_SECONDS = 0.030
PRINT_INTERVAL_SECONDS = 0.100
STARTUP_PROOF_SECONDS = 30.0
STARTUP_PROOF_LEVEL = 100
STARTUP_PROOF_ON_SECONDS = 0.12
STARTUP_PROOF_OFF_SECONDS = 0.35


def open_serial_no_reset():
    esp = serial.Serial()
    esp.port = PORT
    esp.baudrate = SERIAL_BAUD
    esp.timeout = 0
    esp.write_timeout = 1.0
    esp.dsrdtr = False
    esp.rtscts = False
    esp.dtr = False
    esp.rts = False
    esp.open()
    return esp


def send_serial_line(esp, payload, retries=20, delay=0.1):
    last_error = None
    for _ in range(max(1, retries)):
        try:
            esp.write(payload)
            esp.flush()
            return
        except (serial.SerialTimeoutException, serial.SerialException) as exc:
            last_error = exc
            time.sleep(delay)
    raise last_error


@dataclass(frozen=True)
class LraKickFrame:
    motor_level: int
    detected_kick: bool
    phase: str
    low_level_dbfs: float
    novelty_db: float
    rise_db: float
    low_to_full_db: float
    kick_count: int


class Lra0832KickProcessor:
    """Wrap the isolated kick detector with a fast two-stage LRA pulse."""

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.detector = KickOnlyProcessor(sample_rate=sample_rate)
        self.phase = "off"
        self.phase_remaining = 0.0

    def _advance_phase(self, elapsed):
        self.phase_remaining -= elapsed
        while self.phase != "off" and self.phase_remaining <= 0.0:
            if self.phase == "peak":
                self.phase = "body"
                self.phase_remaining += LRA_BODY_SECONDS
            else:
                self.phase = "off"
                self.phase_remaining = 0.0

    def process(self, audio_block):
        elapsed = len(audio_block) / self.sample_rate
        detected = self.detector.process(audio_block)
        if detected.detected_kick:
            self.phase = "peak"
            self.phase_remaining = LRA_PEAK_SECONDS

        if self.phase == "peak":
            motor_level = LRA_PEAK_LEVEL
        elif self.phase == "body":
            motor_level = LRA_BODY_LEVEL
        else:
            motor_level = 0

        frame = LraKickFrame(
            motor_level=motor_level,
            detected_kick=detected.detected_kick,
            phase=self.phase,
            low_level_dbfs=detected.low_level_dbfs,
            novelty_db=detected.novelty_db,
            rise_db=detected.rise_db,
            low_to_full_db=detected.low_to_full_db,
            kick_count=detected.kick_count,
        )
        self._advance_phase(elapsed)
        return frame


def format_status(frame, processing_ms, last_command, send_count, columns):
    status = (
        f"LRA0832 M{frame.motor_level:03d} {frame.phase.upper()} "
        f"cmd H{last_command:03d} sent {send_count} "
        f"20-150Hz {frame.low_level_dbfs:+.1f}dB "
        f"nov {frame.novelty_db:.1f} rise {frame.rise_db:.1f} "
        f"low/full {frame.low_to_full_db:+.1f} "
        f"hits {frame.kick_count} DSP {processing_ms:.2f}ms"
    )
    return status[:max(19, columns - 1)]


def main():
    parser = argparse.ArgumentParser(
        description="0832 LRA kick-only bridge for BlackHole system audio."
    )
    parser.add_argument(
        "--no-startup-proof",
        action="store_true",
        help="skip the H100 startup tap loop before audio capture",
    )
    parser.add_argument(
        "--startup-proof-seconds",
        type=float,
        default=STARTUP_PROOF_SECONDS,
        help="seconds to send H100 proof taps before audio capture",
    )
    args = parser.parse_args()

    device_index = find_input_device(AUDIO_DEVICE)
    interactive = sys.stdout.isatty()
    processor = Lra0832KickProcessor()
    last_motor_level = None
    last_send = 0.0
    last_print = 0.0
    last_command = 0
    send_count = 0

    with open_serial_no_reset() as esp:
        time.sleep(2.0)
        esp.reset_input_buffer()
        esp.reset_output_buffer()
        send_serial_line(esp, b"H0\n")
        send_count += 1

        if not args.no_startup_proof:
            print(
                "Press BOOT now. Sending H100 proof taps for "
                f"{args.startup_proof_seconds:.0f}s before audio starts.",
                flush=True,
            )
            proof_deadline = time.monotonic() + max(
                0.0,
                args.startup_proof_seconds,
            )
            while time.monotonic() < proof_deadline:
                send_serial_line(
                    esp,
                    f"H{STARTUP_PROOF_LEVEL}\n".encode("ascii"),
                )
                last_command = STARTUP_PROOF_LEVEL
                send_count += 1
                time.sleep(STARTUP_PROOF_ON_SECONDS)
                send_serial_line(esp, b"H0\n")
                last_command = 0
                send_count += 1
                time.sleep(STARTUP_PROOF_OFF_SECONDS)

        def callback(indata, frames, timing, status):
            nonlocal last_motor_level, last_send, last_print
            nonlocal last_command, send_count
            started = time.perf_counter()
            frame = processor.process(indata)
            now = time.monotonic()
            if (
                frame.motor_level != last_motor_level
                or frame.detected_kick
                or now - last_send >= KEEPALIVE_SECONDS
            ):
                try:
                    send_serial_line(esp, f"H{frame.motor_level}\n".encode("ascii"), retries=1)
                except (serial.SerialTimeoutException, serial.SerialException):
                    return
                last_command = frame.motor_level
                send_count += 1
                last_motor_level = frame.motor_level
                last_send = now

            if now - last_print >= PRINT_INTERVAL_SECONDS:
                processing_ms = (time.perf_counter() - started) * 1_000.0
                columns = shutil.get_terminal_size(fallback=(100, 24)).columns
                line = format_status(
                    frame,
                    processing_ms,
                    last_command,
                    send_count,
                    columns,
                )
                if status:
                    line = (line + f" !{status}")[:columns - 1]
                if interactive:
                    print(TTY_CLEAR_LINE + line, end="", flush=True)
                else:
                    print(line, flush=True)
                last_print = now

        print(
            "0832 LRA kick-only mode: 20-150 Hz recognition, "
            "45 ms peak + 30 ms body. No bass bed or snare/high-frequency output."
        )
        print(
            "Use only after flashing lra_0832_firmware. "
            "If it does not vibrate, press BOOT once while this program is running."
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
                    send_serial_line(esp, b"H0\n", retries=1)
                except (serial.SerialException, serial.SerialTimeoutException):
                    break
                time.sleep(0.02)
            if interactive:
                print()
            print("LRA stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
