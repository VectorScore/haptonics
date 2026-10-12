"""Regression tests for the isolated 20-150 Hz kick debug bridge."""

import time
import unittest

import numpy as np

from kick_only_debug import (
    BLOCK_SIZE,
    KICK_LEVEL,
    KickOnlyProcessor,
    SAMPLE_RATE,
)


def stereo(mono):
    return np.column_stack((mono, mono))


def run_signal(mono):
    signal = stereo(mono)
    remainder = len(signal) % BLOCK_SIZE
    if remainder:
        signal = np.pad(signal, ((0, BLOCK_SIZE - remainder), (0, 0)))
    processor = KickOnlyProcessor()
    return [
        processor.process(signal[start:start + BLOCK_SIZE])
        for start in range(0, len(signal), BLOCK_SIZE)
    ]


class KickOnlyDebugTests(unittest.TestCase):
    def test_20_to_150_hz_spikes_are_full_strength(self):
        signal = np.zeros(round(1.5 * SAMPLE_RATE))
        onsets = (0.20, 0.45, 0.70, 0.95, 1.20)
        for frequency, onset in zip((20, 40, 75, 110, 145), onsets):
            times = np.arange(round(0.12 * SAMPLE_RATE)) / SAMPLE_RATE
            hit = 0.80 * np.sin(2.0 * np.pi * frequency * times)
            hit *= np.exp(-times / 0.025)
            start = round(onset * SAMPLE_RATE)
            signal[start:start + len(hit)] += hit

        frames = run_signal(signal)
        for onset in onsets:
            self.assertTrue(any(
                onset - BLOCK_SIZE / SAMPLE_RATE
                <= index * BLOCK_SIZE / SAMPLE_RATE
                <= onset + 2 * BLOCK_SIZE / SAMPLE_RATE
                and frame.detected_kick
                and frame.motor_level == KICK_LEVEL
                for index, frame in enumerate(frames)
            ), frequency)

    def test_high_frequency_attacks_never_vibrate(self):
        duration = 1.4
        signal = np.zeros(round(duration * SAMPLE_RATE))
        for frequency, onset in ((800, 0.25), (2_000, 0.60), (7_000, 0.95)):
            times = np.arange(round(0.10 * SAMPLE_RATE)) / SAMPLE_RATE
            hit = 0.80 * np.sin(2.0 * np.pi * frequency * times)
            hit *= np.exp(-times / 0.020)
            start = round(onset * SAMPLE_RATE)
            signal[start:start + len(hit)] += hit
        frames = run_signal(signal)
        self.assertFalse(any(frame.detected_kick for frame in frames))
        self.assertEqual(max(frame.motor_level for frame in frames), 0)

    def test_no_bass_bed_and_no_wave_cycle_retrigger(self):
        times = np.arange(round(1.5 * SAMPLE_RATE)) / SAMPLE_RATE
        signal = 0.14 * np.sin(2.0 * np.pi * 30.0 * times)
        frames = run_signal(signal)
        late = [
            frame for index, frame in enumerate(frames)
            if index * BLOCK_SIZE / SAMPLE_RATE >= 0.12
        ]
        self.assertFalse(any(frame.detected_kick for frame in late))
        self.assertTrue(all(frame.motor_level == 0 for frame in late))

    def test_motor_returns_to_zero_after_one_short_hit(self):
        signal = np.zeros(round(0.8 * SAMPLE_RATE))
        times = np.arange(round(0.10 * SAMPLE_RATE)) / SAMPLE_RATE
        hit = 0.8 * np.sin(2.0 * np.pi * 70.0 * times)
        hit *= np.exp(-times / 0.025)
        start = round(0.20 * SAMPLE_RATE)
        signal[start:start + len(hit)] = hit
        frames = run_signal(signal)
        self.assertIn(KICK_LEVEL, [frame.motor_level for frame in frames])
        self.assertEqual(frames[-1].motor_level, 0)
        self.assertEqual(frames[-1].kick_count, 1)

    def test_processing_stays_below_one_audio_block_at_p99(self):
        processor = KickOnlyProcessor()
        rng = np.random.default_rng(2605)
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
