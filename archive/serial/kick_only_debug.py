"""Causal 20-150 Hz kick detector used by the 0832 LRA bridge."""

from dataclasses import dataclass
import math
import os
import shutil
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt
from serial.tools import list_ports


SERIAL_PORT_ENV = "HAPTIC_MUSIC_SERIAL_PORT"
DEFAULT_PORT = "/dev/cu.usbmodem101"
AUDIO_DEVICE = "BlackHole 2ch"
SAMPLE_RATE = 48_000
BLOCK_SIZE = 256
CHANNELS = 2
SERIAL_BAUD = 115200
KEEPALIVE_SECONDS = 0.250
TTY_CLEAR_LINE = "\r\033[2K"

KICK_LEVEL = 127
MIN_DBFS = -120.0
LOW_HZ = 20.0
HIGH_HZ = 150.0
MIN_LEVEL_DBFS = -55.0
MIN_NOVELTY_DB = 5.0
MIN_RISE_DB = 2.0
BACKGROUND_SECONDS = 0.180
RETRIGGER_GUARD_SECONDS = 0.080


def resolve_serial_port():
    """Return the most likely ESP32 serial port on this machine."""
    env_port = os.getenv(SERIAL_PORT_ENV, "").strip()
    if env_port:
        return env_port

    available = [port.device for port in list_ports.comports()]
    preferred = []
    for device in available:
        lower = device.lower()
        if "bluetooth" in lower or "debug-console" in lower:
            continue
        if "usbmodem" in lower:
            preferred.append((0, device))
        elif "wchusbserial" in lower or "usbserial" in lower:
            preferred.append((1, device))
        else:
            preferred.append((2, device))

    if preferred:
        preferred.sort(key=lambda item: (item[0], item[1]))
        return preferred[0][1]

    return DEFAULT_PORT


PORT = resolve_serial_port()


def find_input_device(name):
    """Return a stereo input device whose name contains ``name``."""
    import sounddevice as sd

    for index, device in enumerate(sd.query_devices()):
        if name.lower() in device["name"].lower() and device["max_input_channels"] >= CHANNELS:
            return index
    raise RuntimeError(
        f"找不到可录制的 {name!r}。请确认 BlackHole 已安装，并重新打开终端。"
    )


@dataclass(frozen=True)
class KickFrame:
    motor_level: int
    detected_kick: bool
    low_level_dbfs: float
    novelty_db: float
    rise_db: float
    low_to_full_db: float
    kick_count: int


class KickOnlyProcessor:
    """Detect sharp low-band rises without future audio samples."""

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.sos = butter(4, (LOW_HZ, HIGH_HZ), btype="bandpass", fs=sample_rate, output="sos")
        self.zi = np.zeros((len(self.sos), 2, CHANNELS), dtype=np.float64)
        self.background = 0.0
        self.previous_level = 0.0
        self.cooldown = 0.0
        self.rearmed = True
        self.kick_count = 0

    @staticmethod
    def _dbfs(amplitude):
        return MIN_DBFS if amplitude <= 1e-8 else 20.0 * math.log10(amplitude)

    def process(self, audio_block):
        block = np.asarray(audio_block, dtype=np.float64)
        if block.ndim == 1:
            block = np.column_stack((block, block))
        filtered, self.zi = sosfilt(self.sos, block, axis=0, zi=self.zi)
        level = float(np.sqrt(np.mean(filtered * filtered)))
        peak = float(np.max(np.abs(filtered))) if filtered.size else 0.0
        elapsed = len(block) / self.sample_rate
        background_before = max(self.background, 1e-8)
        level_dbfs = self._dbfs(max(level, peak * 0.12))
        background_dbfs = self._dbfs(background_before)
        novelty_db = max(0.0, level_dbfs - background_dbfs)
        previous_dbfs = self._dbfs(max(self.previous_level, 1e-8))
        rise_db = max(0.0, level_dbfs - previous_dbfs)
        low_to_full_db = level_dbfs - self._dbfs(max(float(np.sqrt(np.mean(block * block))), 1e-8))

        self.cooldown = max(0.0, self.cooldown - elapsed)
        quiet = novelty_db < 2.0 and rise_db < 0.5
        if quiet:
            self.rearmed = True
        detected = (
            self.rearmed
            and self.cooldown <= 0.0
            and level_dbfs >= MIN_LEVEL_DBFS
            and novelty_db >= MIN_NOVELTY_DB
            and rise_db >= MIN_RISE_DB
        )
        if detected:
            self.kick_count += 1
            self.rearmed = False
            self.cooldown = RETRIGGER_GUARD_SECONDS

        background_alpha = 1.0 - math.exp(-elapsed / BACKGROUND_SECONDS)
        self.background += background_alpha * (level - self.background)
        self.previous_level = level
        return KickFrame(
            motor_level=KICK_LEVEL if detected else 0,
            detected_kick=detected,
            low_level_dbfs=level_dbfs,
            novelty_db=novelty_db,
            rise_db=rise_db,
            low_to_full_db=low_to_full_db,
            kick_count=self.kick_count,
        )


if __name__ == "__main__":
    print("KickOnlyProcessor ready: 20-150 Hz causal detector")
