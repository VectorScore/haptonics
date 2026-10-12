"""Regression tests for the high-frequency profile of the shared haptic core."""

import unittest

import numpy as np

from erm_bass_rhythm import (
    BASS_MAX_LEVEL,
    BLOCK_SIZE,
    KICK_HIT_SECONDS,
    LRA_HIGH_FREQUENCY_PROFILE,
    SAMPLE_RATE,
    EdmErmProcessor,
)
from lra_texture import (
    DEFAULT_SERIAL_CHANNEL,
    LRA_TEXTURE_CLOSE_DBFS,
    LraTextureGate,
    apply_texture_gate,
    serial_command,
)


def stereo(mono):
    return np.column_stack((mono, mono))


def resonant_bursts(
    frequency_hz,
    hit_times,
    duration=1.4,
    amplitude=0.45,
    attack_seconds=0.010,
    decay_seconds=0.060,
):
    times = np.arange(round(duration * SAMPLE_RATE)) / SAMPLE_RATE
    signal = np.zeros_like(times)
    for hit_time in hit_times:
        active = times >= hit_time
        age = times[active] - hit_time
        envelope = np.minimum(
            1.0, age / attack_seconds
        ) * np.exp(-age / decay_seconds)
        signal[active] += (
            amplitude
            * np.sin(2.0 * np.pi * frequency_hz * age)
            * envelope
        )
    return signal


def run_lra(signal):
    processor = EdmErmProcessor(
        frequency_profile=LRA_HIGH_FREQUENCY_PROFILE
    )
    audio = stereo(signal)
    return [
        processor.process(audio[index:index + BLOCK_SIZE])
        for index in range(0, len(audio) - BLOCK_SIZE + 1, BLOCK_SIZE)
    ]


class LraHighFrequencyTests(unittest.TestCase):
    def test_1030z_defaults_to_dual_driver_lra_protocol(self):
        self.assertEqual(DEFAULT_SERIAL_CHANNEL, "L")
        self.assertEqual(serial_command(DEFAULT_SERIAL_CHANNEL, 100), b"L100\n")

    def test_other_driver_protocol_remains_available(self):
        self.assertEqual(serial_command("H", 33), b"H33\n")

    def test_repeated_high_frequency_bursts_use_full_erm_event_path(self):
        hit_times = (0.25, 0.50, 0.75, 1.00)
        frames = run_lra(resonant_bursts(2_000.0, hit_times))
        detected = [
            frame for frame in frames if frame.detected_event == "kick"
        ]
        self.assertEqual(len(detected), len(hit_times))
        self.assertTrue(
            all(frame.motor_level == 127 for frame in detected)
        )
        phases = [frame.phase for frame in frames]
        self.assertIn("hit", phases)
        self.assertIn("dip", phases)
        self.assertIn("recover", phases)

    def test_low_frequency_burst_is_rejected_by_lra_profile(self):
        frames = run_lra(
            resonant_bursts(
                60.0,
                (0.40,),
                attack_seconds=0.020,
            )
        )
        self.assertEqual(max(frame.motor_level for frame in frames), 0)
        self.assertFalse(
            any(frame.detected_event != "none" for frame in frames)
        )

    def test_sustained_high_tone_does_not_retrigger(self):
        times = np.arange(round(3.0 * SAMPLE_RATE)) / SAMPLE_RATE
        signal = np.zeros_like(times)
        active = times >= 0.40
        age = times[active] - 0.40
        signal[active] = (
            0.18
            * np.sin(2.0 * np.pi * 2_000.0 * age)
            * np.minimum(1.0, age / 0.20)
        )
        frames = run_lra(signal)
        events = [
            frame for frame in frames if frame.detected_event != "none"
        ]
        self.assertLessEqual(len(events), 1)
        self.assertLessEqual(
            max(frame.motor_level for frame in frames[-50:]),
            BASS_MAX_LEVEL,
        )

    def test_high_frequency_hit_duration_matches_erm_timing(self):
        frames = run_lra(resonant_bursts(4_000.0, (0.40,)))
        hit_blocks = sum(
            frame.active_event == "kick" and frame.phase == "hit"
            for frame in frames
        )
        duration = hit_blocks * BLOCK_SIZE / SAMPLE_RATE
        self.assertGreaterEqual(
            duration, KICK_HIT_SECONDS - BLOCK_SIZE / SAMPLE_RATE
        )
        self.assertLessEqual(
            duration, KICK_HIT_SECONDS + BLOCK_SIZE / SAMPLE_RATE
        )

    def test_texture_gate_keeps_silence_at_zero(self):
        processor = EdmErmProcessor(
            frequency_profile=LRA_HIGH_FREQUENCY_PROFILE
        )
        gate = LraTextureGate()
        signal = stereo(np.zeros(round(1.0 * SAMPLE_RATE)))
        frames = []
        for index in range(0, len(signal) - BLOCK_SIZE + 1, BLOCK_SIZE):
            block = signal[index:index + BLOCK_SIZE]
            frame = processor.process(block)
            gate.update(block)
            frames.append(apply_texture_gate(frame, gate))
        self.assertFalse(gate.open)
        self.assertLessEqual(gate.level_dbfs, LRA_TEXTURE_CLOSE_DBFS)
        self.assertEqual(max(frame.motor_level for frame in frames), 0)

    def test_texture_gate_opens_for_real_high_frequency_audio(self):
        processor = EdmErmProcessor(
            frequency_profile=LRA_HIGH_FREQUENCY_PROFILE
        )
        gate = LraTextureGate()
        signal = stereo(resonant_bursts(2_000.0, (0.25,), amplitude=0.8))
        opened = False
        original_levels = []
        frames = []
        for index in range(0, len(signal) - BLOCK_SIZE + 1, BLOCK_SIZE):
            block = signal[index:index + BLOCK_SIZE]
            frame = processor.process(block)
            original_levels.append(frame.motor_level)
            opened = gate.update(block) or opened
            frames.append(apply_texture_gate(frame, gate))
        self.assertTrue(opened)
        self.assertEqual(
            max(frame.motor_level for frame in frames),
            max(original_levels),
        )
        self.assertGreater(max(frame.motor_level for frame in frames), 0)


if __name__ == "__main__":
    unittest.main()
