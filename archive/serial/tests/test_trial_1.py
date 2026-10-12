"""Regression tests for the JBL speaker-effect bridge."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

import numpy as np


TRIAL_PATH = Path(__file__).resolve().parents[1] / "trial-1.py"
SPEC = importlib.util.spec_from_file_location("trial_1_module", TRIAL_PATH)
trial_1 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(trial_1)


class Trial1Tests(unittest.TestCase):
    def run_processor(self, audio):
        processor = trial_1.SpeakerEffectProcessor()
        block_time = trial_1.BLOCK_SIZE / trial_1.SAMPLE_RATE
        levels = []
        for start in range(0, len(audio), trial_1.BLOCK_SIZE):
            block = audio[start:start + trial_1.BLOCK_SIZE]
            if len(block) < trial_1.BLOCK_SIZE:
                break
            level, _ = processor.process(block, block_time)
            levels.append(level)
        return levels

    def test_dry_run_skips_serial_port_lookup(self):
        with mock.patch.object(trial_1, "resolve_serial_port") as resolver:
            self.assertIsNone(trial_1.select_serial_port(None, True))
            resolver.assert_not_called()

    def test_explicit_port_is_preserved(self):
        self.assertEqual(
            trial_1.select_serial_port("/dev/cu.test", False),
            "/dev/cu.test",
        )

    def test_port_is_resolved_when_not_dry_run(self):
        with mock.patch.object(
            trial_1, "resolve_serial_port", return_value="/dev/cu.mock"
        ) as resolver:
            self.assertEqual(
                trial_1.select_serial_port(None, False),
                "/dev/cu.mock",
            )
            resolver.assert_called_once()

    def test_antiphase_stereo_bass_still_drives_motor(self):
        duration = 0.5
        samples = round(duration * trial_1.SAMPLE_RATE)
        times = np.arange(samples) / trial_1.SAMPLE_RATE
        left = 0.10 * np.sin(2.0 * np.pi * 55.0 * times)
        right = -left
        audio = np.column_stack((left, right)).astype(np.float32)

        levels = self.run_processor(audio)

        self.assertGreater(max(levels), 30)

    def test_quieter_system_bass_still_maps_to_vibration(self):
        duration = 0.5
        samples = round(duration * trial_1.SAMPLE_RATE)
        times = np.arange(samples) / trial_1.SAMPLE_RATE
        mono = 0.035 * np.sin(2.0 * np.pi * 70.0 * times)
        audio = np.column_stack((mono, mono)).astype(np.float32)

        levels = self.run_processor(audio)

        self.assertGreater(max(levels), 10)


if __name__ == "__main__":
    unittest.main()
