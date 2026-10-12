"""Regression tests for the 0832 LRA kick-only bridge."""

import time
import unittest

import numpy as np

from kick_only_debug import BLOCK_SIZE, SAMPLE_RATE
from lra_0832_kick_debug import (
    LRA_BODY_LEVEL,
    LRA_PEAK_LEVEL,
    Lra0832KickProcessor,
)


def stereo(mono):
    return np.column_stack((mono, mono))


def run_signal(mono):
    signal = stereo(mono)
    remainder = len(signal) % BLOCK_SIZE
    if remainder:
        signal = np.pad(signal, ((0, BLOCK_SIZE - remainder), (0, 0)))
    processor = Lra0832KickProcessor()
    return [
        processor.process(signal[start:start + BLOCK_SIZE])
        for start in range(0, len(signal), BLOCK_SIZE)
    ]


class Lra0832KickTests(unittest.TestCase):
    def test_low_spike_creates_peak_body_off_shape(self):
        signal = np.zeros(round(0.8 * SAMPLE_RATE))
        times = np.arange(round(0.10 * SAMPLE_RATE)) / SAMPLE_RATE
        hit = 0.8 * np.sin(2.0 * np.pi * 75.0 * times)
        hit *= np.exp(-times / 0.025)
        start = round(0.20 * SAMPLE_RATE)
        signal[start:start + len(hit)] = hit
        frames = run_signal(signal)
        levels = [frame.motor_level for frame in frames]
        self.assertIn(LRA_PEAK_LEVEL, levels)
        self.assertIn(LRA_BODY_LEVEL, levels)
        self.assertEqual(frames[-1].motor_level, 0)
        self.assertEqual(frames[-1].kick_count, 1)

    def test_high_frequency_attacks_do_not_drive_lra(self):
        signal = np.zeros(round(1.2 * SAMPLE_RATE))
        for frequency, onset in ((800, 0.25), (2_000, 0.55), (7_000, 0.85)):
            times = np.arange(round(0.08 * SAMPLE_RATE)) / SAMPLE_RATE
            hit = 0.8 * np.sin(2.0 * np.pi * frequency * times)
            hit *= np.exp(-times / 0.020)
            start = round(onset * SAMPLE_RATE)
            signal[start:start + len(hit)] += hit
        frames = run_signal(signal)
        self.assertFalse(any(frame.detected_kick for frame in frames))
        self.assertEqual(max(frame.motor_level for frame in frames), 0)

    def test_sustained_low_tone_has_no_continuous_output(self):
        times = np.arange(round(1.2 * SAMPLE_RATE)) / SAMPLE_RATE
        signal = 0.14 * np.sin(2.0 * np.pi * 60.0 * times)
        frames = run_signal(signal)
        late = [
            frame for index, frame in enumerate(frames)
            if index * BLOCK_SIZE / SAMPLE_RATE >= 0.12
        ]
        self.assertFalse(any(frame.detected_kick for frame in late))
        self.assertTrue(all(frame.motor_level == 0 for frame in late))

    def test_processing_stays_below_one_audio_block_at_p99(self):
        processor = Lra0832KickProcessor()
        rng = np.random.default_rng(832)
        timings = []
        for _ in range(500):
            block = rng.normal(0.0, 0.08, (BLOCK_SIZE, 2))
            started = time.perf_counter()
            processor.process(block)
            timings.append(time.perf_counter() - started)
        self.assertLess(
            np.percentile(timings, 99), BLOCK_SIZE / SAMPLE_RATE
        )


if __name__ == "__main__":
    unittest.main()
