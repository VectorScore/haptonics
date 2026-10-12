"""trial-1: JBL speaker-effect emulation for the 0832 LRA.

Replicates why a speaker "thumps hard" on loud low frequencies and feels
"smooth" on high frequencies / mild volumes, mapped onto an LRA8032 driven
by a DRV2605L on the ESP32-S3-Touch-LCD-1.46.

Speaker physics being emulated
-----------------------------
A cone making sound of a given loudness travels X ~ P / f^2 (constant-SPL
displacement law): bass needs millimetres of swing, treble only micrometres,
and loudness scales displacement linearly. An LRA can only buzz at its
resonance (235 Hz here), so its amplitude knob becomes the "excursion":

    A(t) = shape( loudness_gate * ( (bass/ref)^1.3 + 0.22*(treble/ref)^1.05 ) )

- bass band 25-250 Hz dominates with an accelerating power law  -> HARD
- treble band 1-8 kHz contributes ~1/3 at equal loudness         -> SMOOTH
- loudness gate scales everything: quiet music stays gentle
- asymmetric attack/release mimics the cone + suspension: bass hits fast
  and rings long, treble is tight; a kick-rise punch boosts the slam.

Output: H0..H127 serial lines, same protocol as lra_0832_firmware
(DRV2605L RTP mode, register 0x02 RTP_INPUT, unsigned data format).

Usage
-----
  python3 trial-1.py                      # live system audio -> motor
  python3 trial-1.py --demo               # built-in kick/bass/hat loop
  python3 trial-1.py --demo --dry-run     # no serial, watch the levels
  python3 trial-1.py --port /dev/cu.usbmodem101 --device "BlackHole 2ch"

Dependencies: numpy, scipy, sounddevice, pyserial.
Firmware: flash lra_0832_firmware and press BOOT once to calibrate+enable.
"""

import argparse
import math
import os
import queue
import shutil
import sys
import threading
import time

import numpy as np
import serial
from scipy.signal import butter, sosfilt

# ---------------------------------------------------------------------------
# Tunable speaker-effect parameters
# ---------------------------------------------------------------------------

SAMPLE_RATE = 48_000
BLOCK_SIZE = 256
CHANNELS = 2
SERIAL_BAUD = 115200
KEEPALIVE_SECONDS = 0.25          # firmware disarms after 500 ms of silence
FLOOR_DBFS = -70.0                # raw block RMS below this -> H0 (silence)

# Band split (Hz)
BASS_LOW, BASS_HIGH = 25.0, 250.0
TREBLE_LOW, TREBLE_HIGH = 1_000.0, 8_000.0

# Cone mechanics: attack fast, release long for bass; tight for treble
BASS_ATTACK_S, BASS_RELEASE_S = 0.006, 0.170
TREBLE_ATTACK_S, TREBLE_RELEASE_S = 0.002, 0.045
LOUD_ATTACK_S, LOUD_RELEASE_S = 0.008, 0.180

# Excursion weighting (the 1/f^2 idea, compressed to stay musical).
# Band RMS is normalized against REF_RMS so a loud kick drives near full
# scale. At EQUAL loudness, treble produces only ~1/3 of the bass drive:
# that compressed contrast is the "hard vs smooth" JBL signature.
REF_RMS = 0.20
MIN_EFFECTIVE_REF_RMS = 0.025
DEFAULT_SENSITIVITY = 2.5
BASS_EXP = 1.3
TREBLE_EXP = 1.05
TREBLE_WEIGHT = 0.22

# Perceptual shaping + kick punch
SHAPE_EXP = 0.66
PUNCH_BOOST = 0.35
PUNCH_TAU_S = 0.025
PUNCH_RISE = 1.6                   # bass_env jump ratio that triggers a punch
DEADZONE = 0.025                   # shaped amplitude below this -> H0

# Demo loop timing (seconds within a 1 s cycle)
DEMO_KICK_START, DEMO_KICK_END = 0.00, 0.22
DEMO_BASS_START, DEMO_BASS_END = 0.25, 0.75
DEMO_HAT_START, DEMO_HAT_END = 0.55, 0.65

PRINT_INTERVAL_SECONDS = 0.100
PROOF_PRINT_INTERVAL_SECONDS = 1.0
LIVE_SILENCE_WARNING_SECONDS = 3.0
LIVE_SILENCE_DBFS = -100.0
STARTUP_PROOF_LEVEL = 100
STARTUP_PROOF_ON_SECONDS = 0.12
STARTUP_PROOF_OFF_SECONDS = 0.35
# The firmware performs the physical BOOT check locally.  Keep the old host
# proof available as an opt-in diagnostic, but do not delay live audio by
# default.
DEFAULT_STARTUP_PROOF_SECONDS = 0.0

SERIAL_PORT_ENV = "HAPTIC_MUSIC_SERIAL_PORT"
DEFAULT_PORT = "/dev/cu.usbmodem101"
TTY_CLEAR_LINE = "\r\x1b[2K"


# ---------------------------------------------------------------------------
# Serial helpers (same conventions as the existing bridges)
# ---------------------------------------------------------------------------

def resolve_serial_port():
    override = os.environ.get(SERIAL_PORT_ENV)
    if override:
        return override
    if os.path.exists(DEFAULT_PORT):
        return DEFAULT_PORT
    try:
        from serial.tools import list_ports
        for port in list_ports.comports():
            return port.device
    except Exception:
        pass
    raise SystemExit(
        f"No serial port. Set {SERIAL_PORT_ENV} or pass --port.")


def select_serial_port(explicit_port, dry_run):
    if dry_run:
        return None
    return explicit_port or resolve_serial_port()


def open_serial_no_reset(port):
    esp = serial.Serial()
    esp.port = port
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


def find_input_device(name):
    import sounddevice as sd
    for index, device in enumerate(sd.query_devices()):
        if (name.lower() in device["name"].lower()
                and device["max_input_channels"] >= CHANNELS):
            return index
    raise SystemExit(f"Input device {name!r} not found. Run "
                     "`python3 -m sounddevice` to list devices.")


# ---------------------------------------------------------------------------
# The algorithm
# ---------------------------------------------------------------------------

class OnePole:
    """Asymmetric one-pole envelope follower (attack/release)."""

    def __init__(self, attack_s, release_s):
        self.attack = attack_s
        self.release = release_s
        self.value = 0.0

    def step(self, target, dt):
        tau = self.attack if target > self.value else self.release
        alpha = 1.0 - np.exp(-dt / tau) if tau > 0 else 1.0
        self.value += alpha * (target - self.value)
        return self.value


class SpeakerEffectProcessor:
    """Map an audio block to a speaker-equivalent LRA level (0..127)."""

    def __init__(
            self,
            sample_rate=SAMPLE_RATE,
            sensitivity=DEFAULT_SENSITIVITY,
            floor_dbfs=FLOOR_DBFS):
        if sensitivity <= 0.0:
            raise ValueError("sensitivity must be positive")
        self.sample_rate = sample_rate
        self.sensitivity = sensitivity
        self.floor_dbfs = floor_dbfs
        self.bass_sos = butter(
            4, (BASS_LOW, BASS_HIGH), btype="bandpass",
            fs=sample_rate, output="sos")
        self.treble_sos = butter(
            4, (TREBLE_LOW, TREBLE_HIGH), btype="bandpass",
            fs=sample_rate, output="sos")
        self.bass_zi = None
        self.treble_zi = None
        self.bass_env = OnePole(BASS_ATTACK_S, BASS_RELEASE_S)
        self.treble_env = OnePole(TREBLE_ATTACK_S, TREBLE_RELEASE_S)
        self.loud_env = OnePole(LOUD_ATTACK_S, LOUD_RELEASE_S)
        self.punch = 0.0
        self.prev_bass = 0.0

    @staticmethod
    def _rms(x):
        return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0

    @staticmethod
    def _dbfs(amplitude):
        if amplitude <= 1e-9:
            return -180.0
        return 20.0 * math.log10(amplitude)

    @staticmethod
    def _dbfs_to_amplitude(dbfs):
        return 10.0 ** (dbfs / 20.0)

    @staticmethod
    def _as_channel_matrix(audio_block):
        x = np.asarray(audio_block, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, np.newaxis]
        elif x.ndim != 2:
            x = np.reshape(x, (x.shape[0], -1))
        return x

    def _ensure_filter_state(self, channels):
        expected = (self.bass_sos.shape[0], 2, channels)
        if self.bass_zi is None or self.bass_zi.shape != expected:
            self.bass_zi = np.zeros(expected)
            self.treble_zi = np.zeros(expected)

    def process(self, audio_block, block_time_s):
        """Return (motor_level, debug_dict)."""
        x = self._as_channel_matrix(audio_block)
        if x.size == 0:
            return 0, {}
        self._ensure_filter_state(x.shape[1])

        bass, self.bass_zi = sosfilt(
            self.bass_sos, x, axis=0, zi=self.bass_zi)
        treble, self.treble_zi = sosfilt(
            self.treble_sos, x, axis=0, zi=self.treble_zi)

        bass_rms = self._rms(bass)
        treble_rms = self._rms(treble)
        loud_rms = self._rms(x)

        bass_lin = self.bass_env.step(bass_rms, block_time_s)
        treble_lin = self.treble_env.step(treble_rms, block_time_s)
        loud_lin = self.loud_env.step(loud_rms, block_time_s)
        effective_ref = max(
            MIN_EFFECTIVE_REF_RMS,
            REF_RMS / self.sensitivity,
        )

        # Cone-excursion weighting: bass dominates, treble is a subtle bed.
        # Band RMS is normalized against effective_ref ("full-scale" loudness).
        cone = (bass_lin / effective_ref) ** BASS_EXP
        if treble_lin > 0.0:
            cone += TREBLE_WEIGHT * (treble_lin / effective_ref) ** TREBLE_EXP

        # Loudness gate: quiet music -> gentle, loud -> strong.
        loud_gate = 0.5 + 0.5 * min(1.0, loud_lin / effective_ref)
        amplitude = cone * loud_gate

        # Kick punch: a fast bass rise slams harder, then rings off.
        if bass_lin > self.prev_bass * PUNCH_RISE:
            self.punch = 1.0
        else:
            self.punch *= np.exp(-block_time_s / PUNCH_TAU_S)
        self.prev_bass = bass_lin
        amplitude *= 1.0 + PUNCH_BOOST * self.punch

        # Perceptual shaping + silence floor (raw block RMS) + deadzone.
        if loud_rms < self._dbfs_to_amplitude(self.floor_dbfs):
            amplitude = 0.0
        else:
            amplitude = np.clip(amplitude, 0.0, 1.0) ** SHAPE_EXP
            if amplitude < DEADZONE:
                amplitude = 0.0

        motor_level = int(round(amplitude * 127.0))
        motor_level = max(0, min(127, motor_level))

        debug = {
            "bass_db": self._dbfs(bass_lin),
            "treble_db": self._dbfs(treble_lin),
            "loud_db": self._dbfs(loud_lin),
            "raw_db": self._dbfs(loud_rms),
            "channels": x.shape[1],
            "cone": cone,
            "effective_ref": effective_ref,
            "shaped": float(amplitude),
            "punch": self.punch,
        }
        return motor_level, debug


# ---------------------------------------------------------------------------
# Built-in demo audio: kick / bass note / hi-hat / silence, looped
# ---------------------------------------------------------------------------

class DemoGenerator:
    """Synthesize a 1 s loop that stresses the bass-vs-treble contrast."""

    def __init__(self, sample_rate=SAMPLE_RATE, block_size=BLOCK_SIZE):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.cursor = 0

    def next_block(self):
        n = self.block_size
        t = np.arange(self.cursor, self.cursor + n) / self.sample_rate
        self.cursor += n
        p = t % 1.0  # position inside the 1 s loop

        out = np.zeros(n)

        kick = (p >= DEMO_KICK_START) & (p < DEMO_KICK_END)
        if np.any(kick):
            tk = p[kick] - DEMO_KICK_START
            out[kick] = 0.90 * np.exp(-tk / 0.06) * np.sin(
                2 * np.pi * 55.0 * tk)

        bass = (p >= DEMO_BASS_START) & (p < DEMO_BASS_END)
        if np.any(bass):
            tb = p[bass] - DEMO_BASS_START
            out[bass] = 0.10 * np.exp(-tb / 0.55) * np.sin(
                2 * np.pi * 110.0 * tb)

        hat = (p >= DEMO_HAT_START) & (p < DEMO_HAT_END)
        if np.any(hat):
            th = p[hat] - DEMO_HAT_START
            noise = np.random.uniform(-1.0, 1.0, hat.sum())
            noise = np.diff(noise, prepend=0.0)  # cheap 1-pole high-pass
            out[hat] = 0.12 * np.exp(-th / 0.03) * noise

        return out.astype(np.float32)


# ---------------------------------------------------------------------------
# Status printing
# ---------------------------------------------------------------------------

def excursion_bar(fraction, width=18):
    filled = int(round(np.clip(fraction, 0.0, 1.0) * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def format_status(level, debug, last_command, send_count, columns):
    status = (
        f"TRIAL1 M{level:03d} {excursion_bar(debug.get('shaped', 0.0))} "
        f"in {debug.get('raw_db', 0):+.1f}dB "
        f"bass {debug.get('bass_db', 0):+.1f}dB "
        f"treble {debug.get('treble_db', 0):+.1f}dB "
        f"punch {debug.get('punch', 0):.2f} "
        f"cmd H{last_command:03d} sent {send_count}"
    )
    return status[:max(19, columns - 1)]


def print_status(line, interactive):
    # Newline output stays visible in Terminal, redirected logs, and the IDE.
    # The old carriage-return display could look frozen even while it ran.
    print(line, flush=True)


def print_proof_status(line):
    print(line, flush=True)


def run_loop(processor, demo, dry_run, no_proof, proof_seconds, port, device):
    esp = None
    serial_lock = threading.Lock()
    serial_error = None
    if not dry_run:
        esp = open_serial_no_reset(port)
        time.sleep(2.0)
        esp.reset_input_buffer()
        esp.reset_output_buffer()

    def write_serial(payload, retries=1):
        nonlocal serial_error
        if esp is None:
            return True
        try:
            with serial_lock:
                send_serial_line(esp, payload, retries=retries)
            return True
        except (serial.SerialException, serial.SerialTimeoutException) as exc:
            if serial_error is None:
                serial_error = exc
            return False

    write_serial(b"H0\n")

    interactive = sys.stdout.isatty()
    last_motor_level = None
    last_send = 0.0
    last_print = 0.0
    last_command = 0
    send_count = 0
    stream_started = None
    silence_warned = False

    if esp and not no_proof and proof_seconds > 0.0:
        print(
            "Press BOOT now. Sending H100 proof taps for "
            f"{proof_seconds:.0f}s before audio starts.",
            flush=True,
        )
        deadline = time.monotonic() + max(0.0, proof_seconds)
        next_proof_print = 0.0
        while time.monotonic() < deadline:
            if write_serial(
                    f"H{STARTUP_PROOF_LEVEL}\n".encode("ascii"), retries=1):
                last_command = STARTUP_PROOF_LEVEL
                send_count += 1
            now = time.monotonic()
            if now >= next_proof_print:
                remaining = max(0.0, deadline - now)
                line = (
                    f"BOOT PROOF active: press BOOT now | "
                    f"cmd H{last_command:03d} sent {send_count} | "
                    f"audio status starts in {remaining:04.1f}s"
                )
                print_proof_status(line)
                next_proof_print = now + PROOF_PRINT_INTERVAL_SECONDS
            time.sleep(STARTUP_PROOF_ON_SECONDS)
            if write_serial(b"H0\n", retries=1):
                last_command = 0
                send_count += 1
            now = time.monotonic()
            if now >= next_proof_print:
                remaining = max(0.0, deadline - now)
                line = (
                    f"BOOT PROOF active: press BOOT now | "
                    f"cmd H{last_command:03d} sent {send_count} | "
                    f"audio status starts in {remaining:04.1f}s"
                )
                print_proof_status(line)
                next_proof_print = now + PROOF_PRINT_INTERVAL_SECONDS
            time.sleep(STARTUP_PROOF_OFF_SECONDS)
        print("BOOT proof complete. Starting live audio status now.", flush=True)
    elif esp:
        print(
            "Live audio starts immediately. Press BOOT once for the short "
            "hardware vibration check.",
            flush=True,
        )

    block_time = BLOCK_SIZE / SAMPLE_RATE

    def emit(level):
        nonlocal last_motor_level, last_send, last_command, send_count
        now = time.monotonic()
        if esp is None:
            # Dry-run status should still show the level the motor would get.
            last_command = level
            last_motor_level = level
            return
        if (level != last_motor_level
                or now - last_send >= KEEPALIVE_SECONDS):
            if not write_serial(f"H{level}\n".encode("ascii"), retries=1):
                return
            last_command = level
            send_count += 1
            last_motor_level = level
            last_send = now

    def report(level, debug, audio_status="", processing_ms=0.0,
               block_count=0):
        nonlocal last_print, silence_warned
        now = time.monotonic()
        if stream_started is not None and (
                not demo
                and not silence_warned
                and now - stream_started >= LIVE_SILENCE_WARNING_SECONDS
                and debug.get("raw_db", -180.0) <= LIVE_SILENCE_DBFS):
            print(
                "BlackHole input is silent. Set macOS Sound Output to "
                "Multi-Output Device, play audio, and make sure BlackHole "
                "2ch is checked in Audio MIDI Setup.",
                flush=True,
            )
            silence_warned = True

        if now - last_print < PRINT_INTERVAL_SECONDS:
            return
        columns = shutil.get_terminal_size(fallback=(120, 24)).columns
        line = format_status(level, debug, last_command, send_count, columns)
        line += f" DSP {processing_ms:.2f}ms blocks {block_count}"
        if audio_status:
            line += f" !{audio_status}"
        if serial_error is not None:
            line += f" SERIAL ERROR: {serial_error}"
        print_status(line, interactive)
        last_print = now

    try:
        if demo:
            gen = DemoGenerator()
            print("trial-1 demo loop: kick 55 Hz -> HARD, bass 110 Hz -> "
                  "medium, hi-hat -> SMOOTH, silence -> off.",
                  flush=True)
            stream_started = time.monotonic()
            block_count = 0
            while True:
                started = time.perf_counter()
                level, debug = processor.process(gen.next_block(), block_time)
                block_count += 1
                emit(level)
                report(
                    level,
                    debug,
                    processing_ms=(time.perf_counter() - started) * 1_000.0,
                    block_count=block_count,
                )
                time.sleep(block_time * 0.9)
        else:
            import sounddevice as sd
            device_index = find_input_device(device)
            device_info = sd.query_devices(device_index)
            device_name = device_info.get("name", str(device_index))
            input_channels = device_info.get("max_input_channels", CHANNELS)
            print("trial-1 live mode: system audio -> speaker-effect levels. "
                  "Press BOOT once if the motor has just been reset.",
                  flush=True)
            print(
                f"Listening to input #{device_index}: {device_name} "
                f"({input_channels} channels).",
                flush=True,
            )

            # Audio callbacks only do bounded DSP and publish the newest frame.
            # Serial I/O and terminal printing stay on this ordinary thread.
            frames = queue.Queue(maxsize=2)
            callback_errors = queue.Queue(maxsize=1)
            audio_block_count = 0

            def audio_callback(indata, callback_frames, timing, status):
                nonlocal audio_block_count
                try:
                    started = time.perf_counter()
                    level, debug = processor.process(
                        indata,
                        max(1, callback_frames) / SAMPLE_RATE,
                    )
                    processing_ms = (time.perf_counter() - started) * 1_000.0
                    audio_block_count += 1
                    item = (
                        level,
                        debug,
                        str(status) if status else "",
                        processing_ms,
                        audio_block_count,
                    )
                    try:
                        frames.put_nowait(item)
                    except queue.Full:
                        try:
                            frames.get_nowait()
                        except queue.Empty:
                            pass
                        frames.put_nowait(item)
                except Exception as exc:  # surface callback failures in main
                    try:
                        callback_errors.put_nowait(exc)
                    except queue.Full:
                        pass

            with sd.InputStream(
                device=device_index,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                channels=CHANNELS,
                dtype="float32",
                latency="low",
                callback=audio_callback,
            ):
                stream_started = time.monotonic()
                print(
                    "Audio stream running. Play music now; status lines show "
                    "the input level and exact H command.",
                    flush=True,
                )
                last_frame = (0, {"raw_db": -180.0}, "", 0.0, 0)
                while True:
                    try:
                        last_frame = frames.get(timeout=0.20)
                        # The processor has already seen every block; for the
                        # motor and display, the newest result is sufficient.
                        while True:
                            last_frame = frames.get_nowait()
                    except queue.Empty:
                        pass

                    error = None
                    try:
                        error = callback_errors.get_nowait()
                    except queue.Empty:
                        pass
                    if error is not None:
                        raise RuntimeError(
                            f"audio callback failed: {type(error).__name__}: "
                            f"{error}") from error

                    level, debug, audio_status, processing_ms, block_count = last_frame
                    emit(level)
                    report(
                        level,
                        debug,
                        audio_status=audio_status,
                        processing_ms=processing_ms,
                        block_count=block_count,
                    )
    finally:
        if esp is not None:
            for _ in range(3):
                if not write_serial(b"H0\n", retries=1):
                    break
                time.sleep(0.02)
            esp.close()
        if interactive:
            print()
        print("LRA stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="trial-1: JBL speaker-effect haptic algorithm "
                    "(cone-excursion emulation) for the 0832 LRA.")
    parser.add_argument("--demo", action="store_true",
                        help="use the built-in kick/bass/hat test loop "
                             "instead of system audio")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute levels but do not open the serial port")
    parser.add_argument("--no-startup-proof", action="store_true",
                        help="skip the H100 proof taps before capture")
    parser.add_argument("--startup-proof-seconds", type=float,
                        default=DEFAULT_STARTUP_PROOF_SECONDS)
    parser.add_argument("--sensitivity", type=float,
                        default=DEFAULT_SENSITIVITY,
                        help="audio-to-vibration sensitivity multiplier")
    parser.add_argument("--floor-dbfs", type=float,
                        default=FLOOR_DBFS,
                        help="raw input level treated as silence")
    parser.add_argument("--port", default=None,
                        help="serial port (default: env or /dev/cu.usbmodem101)")
    parser.add_argument("--device", default="BlackHole 2ch",
                        help="audio input device name fragment")
    parser.add_argument("--list-devices", action="store_true",
                        help="print CoreAudio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    port = select_serial_port(args.port, args.dry_run)
    processor = SpeakerEffectProcessor(
        sensitivity=args.sensitivity,
        floor_dbfs=args.floor_dbfs,
    )
    run_loop(processor, args.demo, args.dry_run, args.no_startup_proof,
             args.startup_proof_seconds, port, args.device)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
