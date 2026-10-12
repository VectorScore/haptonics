"""Synthetic regression tests for the EDM ERM haptic processor."""

import unittest
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

from erm_bass_rhythm import (
    BLOCK_SIZE,
    BASS_MAX_LEVEL,
    EdmErmProcessor,
    DEFAULT_IMPACT_PROFILE,
    KICK_LEVEL,
    KICK_HIT_SECONDS,
    PITCH_DUCK_FRACTION,
    RECOVERY_SECONDS,
    SAMPLE_RATE,
    SNARE_CLAP_LEVEL,
    SNARE_CLAP_START_LEVEL,
    SNARE_CLAP_START_SECONDS,
    SNARE_HIT_SECONDS,
    TTY_CLEAR_LINE,
    fit_impact_profile,
    format_status_line,
    load_impact_profile,
    snap_calibration_labels,
    should_send_frame,
)


REFERENCE_AUDIO = Path(os.environ.get(
    "HAPTIC_REFERENCE_AUDIO",
    "/Users/vectorscoresmacbook/Documents/Music Projects/Personal Portfolio/"
    "MOURNFUL CRYING OF A GROWN ADULT.mp3",
))


def stereo(mono):
    return np.column_stack((mono, mono))


def run_signal(signal):
    processor = EdmErmProcessor()
    remainder = len(signal) % BLOCK_SIZE
    if remainder:
        signal = np.pad(signal, ((0, BLOCK_SIZE - remainder), (0, 0)))
    return [
        processor.process(signal[start:start + BLOCK_SIZE])
        for start in range(0, len(signal), BLOCK_SIZE)
    ]


def make_pad(duration=2.0, amplitude=0.14, fade_seconds=0.35):
    count = round(duration * SAMPLE_RATE)
    times = np.arange(count) / SAMPLE_RATE
    envelope = np.ones(count)
    fade_count = round(fade_seconds * SAMPLE_RATE)
    envelope[:fade_count] = np.linspace(0.0, 1.0, fade_count)
    envelope[-fade_count:] = np.linspace(1.0, 0.0, fade_count)
    return amplitude * envelope * np.sin(2.0 * np.pi * 60.0 * times)


def make_vibrato_bass(duration=3.0, center_hz=60.0, deviation_hz=2.0):
    count = round(duration * SAMPLE_RATE)
    times = np.arange(count) / SAMPLE_RATE
    frequency = center_hz + deviation_hz * np.sin(2.0 * np.pi * 0.7 * times)
    phase = 2.0 * np.pi * np.cumsum(frequency) / SAMPLE_RATE
    return 0.14 * np.sin(phase)


def make_changing_bass(note_seconds=0.40, repeats=3):
    frequencies = (48.0, 67.0, 92.0, 57.0) * repeats
    segments = []
    count = round(note_seconds * SAMPLE_RATE)
    times = np.arange(count) / SAMPLE_RATE
    fade = np.minimum(
        np.minimum(times / 0.020, (note_seconds - times) / 0.020), 1.0
    )
    fade = np.clip(fade, 0.0, 1.0)
    for frequency in frequencies:
        segments.append(
            0.14 * fade * np.sin(2.0 * np.pi * frequency * times)
        )
    return np.concatenate(segments)


def add_kicks(signal, hit_times, amplitude=0.65, seed=101):
    rng = np.random.default_rng(seed)
    result = signal.copy()
    duration = 0.120
    count = round(duration * SAMPLE_RATE)
    times = np.arange(count) / SAMPLE_RATE
    phase = 2.0 * np.pi * (115.0 * times - 280.0 * times * times)
    body = amplitude * np.sin(phase) * np.exp(-times / 0.040)
    click = 0.10 * rng.standard_normal(count) * np.exp(-times / 0.006)
    hit = body + click
    for hit_time in hit_times:
        start = round(hit_time * SAMPLE_RATE)
        end = min(len(result), start + count)
        result[start:end] += hit[:end - start]
    return result


def add_snares(signal, hit_times, amplitude=0.40, seed=202):
    rng = np.random.default_rng(seed)
    result = signal.copy()
    duration = 0.100
    count = round(duration * SAMPLE_RATE)
    times = np.arange(count) / SAMPLE_RATE
    noise = rng.standard_normal(count)
    sos = butter(3, (300.0, 2_700.0), btype="bandpass",
                 fs=SAMPLE_RATE, output="sos")
    hit = amplitude * sosfilt(sos, noise) * np.exp(-times / 0.035)
    for hit_time in hit_times:
        start = round(hit_time * SAMPLE_RATE)
        end = min(len(result), start + count)
        result[start:end] += hit[:end - start]
    return result


def add_hats(signal, hit_times, amplitude=0.35, seed=303):
    rng = np.random.default_rng(seed)
    result = signal.copy()
    duration = 0.045
    count = round(duration * SAMPLE_RATE)
    times = np.arange(count) / SAMPLE_RATE
    noise = rng.standard_normal(count)
    sos = butter(5, 6_000.0, btype="highpass", fs=SAMPLE_RATE, output="sos")
    hit = amplitude * sosfilt(sos, noise) * np.exp(-times / 0.012)
    for hit_time in hit_times:
        start = round(hit_time * SAMPLE_RATE)
        end = min(len(result), start + count)
        result[start:end] += hit[:end - start]
    return result


def add_claps(signal, hit_times, amplitude=0.35, bursts=(0.0,), seed=404):
    """Broadband EDM clap; optional offsets create a multi-burst handclap."""
    rng = np.random.default_rng(seed)
    result = signal.copy()
    duration = 0.065
    count = round(duration * SAMPLE_RATE)
    times = np.arange(count) / SAMPLE_RATE
    noise = rng.standard_normal(count)
    sos = butter(
        3, (450.0, 7_500.0), btype="bandpass",
        fs=SAMPLE_RATE, output="sos"
    )
    burst = amplitude * sosfilt(sos, noise) * np.exp(-times / 0.020)
    for hit_time in hit_times:
        for offset in bursts:
            start = round((hit_time + offset) * SAMPLE_RATE)
            end = min(len(result), start + count)
            result[start:end] += burst[:end - start]
    return result


def add_open_hats(signal, hit_times, amplitude=0.35, seed=505):
    rng = np.random.default_rng(seed)
    result = signal.copy()
    duration = 0.180
    count = round(duration * SAMPLE_RATE)
    times = np.arange(count) / SAMPLE_RATE
    sos = butter(5, 7_000.0, btype="highpass", fs=SAMPLE_RATE, output="sos")
    hit = amplitude * sosfilt(sos, rng.standard_normal(count))
    hit *= np.exp(-times / 0.075)
    for hit_time in hit_times:
        start = round(hit_time * SAMPLE_RATE)
        end = min(len(result), start + count)
        result[start:end] += hit[:end - start]
    return result


def make_rhythmic_bass(duration=2.2, note_interval=0.25):
    count = round(duration * SAMPLE_RATE)
    result = np.zeros(count)
    note_count = round(0.145 * SAMPLE_RATE)
    note_times = np.arange(note_count) / SAMPLE_RATE
    # Harmonics make this closer to an electronic bass synth than a pure sine.
    note = (
        np.sin(2.0 * np.pi * 60.0 * note_times)
        + 0.35 * np.sin(2.0 * np.pi * 180.0 * note_times)
        + 0.20 * np.sin(2.0 * np.pi * 300.0 * note_times)
    )
    attack = np.minimum(1.0, note_times / 0.004)
    release = np.exp(-note_times / 0.085)
    note *= 0.18 * attack * release
    for note_time in np.arange(0.25, duration - 0.15, note_interval):
        start = round(note_time * SAMPLE_RATE)
        end = min(count, start + note_count)
        result[start:end] += note[:end - start]
    return result


class EdmErmProcessorTests(unittest.TestCase):
    @unittest.skipUnless(
        REFERENCE_AUDIO.is_file() and Path("/usr/bin/afconvert").is_file(),
        "reference EDM assessment sample is not available",
    )
    def test_reference_song_low_spike_upgrades_first_kick(self):
        with tempfile.TemporaryDirectory() as directory:
            converted = Path(directory) / "reference.wav"
            subprocess.run(
                [
                    "/usr/bin/afconvert", str(REFERENCE_AUDIO), str(converted),
                    "-f", "WAVE", "-d", f"LEF32@{SAMPLE_RATE}", "-c", "2",
                ],
                check=True,
            )
            rate, audio = wavfile.read(converted)
            self.assertEqual(rate, SAMPLE_RATE)

            processor = EdmErmProcessor()
            rows = []
            stop = round(34.10 * rate)
            for start in range(0, stop, BLOCK_SIZE):
                frame = processor.process(audio[start:start + BLOCK_SIZE])
                rows.append((start / rate, frame))

        provisional = next(
            frame for timestamp, frame in rows
            if 14.800 <= timestamp <= 14.805
            and frame.detected_event == "provisional"
        )
        self.assertEqual(provisional.motor_level, SNARE_CLAP_START_LEVEL)

        kick_time, kick = next(
            (timestamp, frame) for timestamp, frame in rows
            if 14.800 <= timestamp <= 14.827
            and frame.detected_event == "kick"
        )
        self.assertLessEqual(kick_time, 14.827)
        self.assertEqual(kick.motor_level, KICK_LEVEL)
        self.assertEqual(kick.click_kick_status, "low-spike")
        self.assertGreaterEqual(kick.low_spike_novelty_db, 5.0)
        self.assertGreaterEqual(kick.low_spike_rise_db, 2.0)

        def nearest(target):
            return min(rows, key=lambda item: abs(item[0] - target))[1]

        early_center = nearest(14.811).low_center_hz
        later_center = nearest(14.837).low_center_hz
        self.assertIsNotNone(early_center)
        self.assertIsNotNone(later_center)
        self.assertGreater(early_center - later_center, 12.0)

        for onset in (17.996, 24.396, 27.595, 33.995):
            self.assertTrue(any(
                onset <= timestamp <= onset + 0.032
                and frame.detected_event == "kick"
                for timestamp, frame in rows
            ), f"reference kick near {onset:.3f}s was missed")

    def test_compressed_click_heavy_and_sub_heavy_kicks_are_detected(self):
        compressed = add_kicks(
            np.zeros(round(1.1 * SAMPLE_RATE)), (0.45,), amplitude=0.20
        )
        compressed = np.tanh(5.0 * compressed) / np.tanh(5.0)

        count = round(0.150 * SAMPLE_RATE)
        times = np.arange(count) / SAMPLE_RATE
        sub_hit = 0.60 * np.sin(2.0 * np.pi * 52.0 * times)
        sub_hit *= np.exp(-times / 0.050)
        sub_heavy = np.zeros(round(1.1 * SAMPLE_RATE))
        start = round(0.45 * SAMPLE_RATE)
        sub_heavy[start:start + count] += sub_hit

        rng = np.random.default_rng(808)
        click_hit = 0.12 * np.sin(2.0 * np.pi * 95.0 * times)
        click_hit *= np.exp(-times / 0.040)
        click_hit += 0.45 * rng.standard_normal(count) * np.exp(-times / 0.003)
        click_heavy = np.zeros(round(1.1 * SAMPLE_RATE))
        click_heavy[start:start + count] += click_hit

        for signal in (compressed, sub_heavy, click_heavy):
            frames = run_signal(stereo(signal))
            self.assertTrue(any(
                frame.detected_event == "kick" for frame in frames
            ))
            self.assertEqual(max(frame.motor_level for frame in frames), 127)

    def test_every_sharp_20_to_150_hz_spike_becomes_a_strong_kick(self):
        duration = 1.5
        signal = np.zeros(round(duration * SAMPLE_RATE))
        onsets = (0.20, 0.45, 0.70, 0.95, 1.20)
        for frequency, onset in zip((20, 40, 75, 110, 145), onsets):
            times = np.arange(round(0.12 * SAMPLE_RATE)) / SAMPLE_RATE
            hit = 0.80 * np.sin(2.0 * np.pi * frequency * times)
            hit *= np.exp(-times / 0.025)
            start = round(onset * SAMPLE_RATE)
            signal[start:start + len(hit)] += hit

        frames = run_signal(stereo(signal))
        for onset in onsets:
            self.assertTrue(any(
                onset - BLOCK_SIZE / SAMPLE_RATE
                <= index * BLOCK_SIZE / SAMPLE_RATE
                <= onset + 2 * BLOCK_SIZE / SAMPLE_RATE
                and frame.detected_event == "kick"
                and frame.motor_level == KICK_LEVEL
                for index, frame in enumerate(frames)
            ), f"low spike at {onset:.2f}s was not a strong kick")

    def test_low_spike_path_does_not_retrigger_a_steady_wave_cycle(self):
        for frequency in (20.0, 30.0, 60.0, 100.0, 145.0):
            times = np.arange(round(1.5 * SAMPLE_RATE)) / SAMPLE_RATE
            signal = 0.14 * np.sin(2.0 * np.pi * frequency * times)
            frames = run_signal(stereo(signal))
            late_kicks = [
                frame for index, frame in enumerate(frames)
                if index * BLOCK_SIZE / SAMPLE_RATE >= 0.12
                and frame.detected_event == "kick"
            ]
            self.assertEqual(late_kicks, [], frequency)

    def test_alternating_eighth_notes_remain_complete_at_240_bpm(self):
        duration = 2.0
        kicks = np.arange(0.25, 1.80, 0.25)
        snares = np.arange(0.375, 1.80, 0.25)
        signal = np.zeros(round(duration * SAMPLE_RATE))
        signal = add_kicks(signal, kicks, amplitude=0.65)
        signal = add_snares(signal, snares, amplitude=0.40)
        frames = run_signal(stereo(signal))
        self.assertEqual(frames[-1].kick_count, len(kicks))
        # A click-leading kick can report a temporary clap label before its
        # next-block upgrade; cumulative counters correct that causal label.
        self.assertEqual(frames[-1].snare_count, len(snares))

    def test_causal_dsp_stays_within_one_audio_block_at_p99(self):
        processor = EdmErmProcessor()
        rng = np.random.default_rng(909)
        processing_times = []
        for _ in range(600):
            block = rng.normal(0.0, 0.08, (BLOCK_SIZE, 2))
            started = time.perf_counter()
            processor.process(block)
            processing_times.append(time.perf_counter() - started)
        self.assertLess(
            np.percentile(processing_times, 99), BLOCK_SIZE / SAMPLE_RATE
        )

    def test_profile_loading_falls_back_and_calibration_helpers_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text('{"version": 999}', encoding="utf-8")
            profile, loaded = load_impact_profile(invalid)
            self.assertIsNone(loaded)
            self.assertEqual(profile["version"], DEFAULT_IMPACT_PROFILE["version"])

            rows = []
            for index, kind in enumerate(("K", "I", "G", "F")):
                if kind == "K":
                    normalized = dict(sub=.9, punch=.9, body=.8, crack=.3,
                                      air=.2, hat=.1, rise=.9, crest=.8)
                elif kind == "I":
                    normalized = dict(sub=.2, punch=.3, body=.8, crack=.9,
                                      air=.8, hat=.2, rise=.8, crest=.7)
                else:
                    normalized = dict(sub=.3, punch=.2, body=.2, crack=.2,
                                      air=.1, hat=.1, rise=.1, crest=.1)
                rows.append({
                    "time": index * 0.5,
                    "normalized": normalized,
                    "low_onset": 0.9 if kind == "K" else 0.2,
                    "sharpness": 0.9 if kind in ("K", "I") else 0.1,
                    "broadband": 0.9 if kind == "I" else 0.2,
                    "kick": 0.9 if kind == "K" else 0.2,
                    "impact": 0.9 if kind in ("K", "I") else 0.1,
                })
            presses = [("K", 0.10), ("I", 0.60), ("G", 1.10), ("F", 1.60)]
            markers = snap_calibration_labels(presses, rows)
            fitted, report = fit_impact_profile(rows, markers, random_trials=8)
            self.assertEqual(len(markers), 4)
            self.assertEqual(fitted["version"], DEFAULT_IMPACT_PROFILE["version"])
            self.assertGreaterEqual(report["train_f2"], 0.5)
            destination = Path(directory) / "impact_profile.json"
            destination.write_text(json.dumps(fitted), encoding="utf-8")
            loaded_profile, loaded_path = load_impact_profile(destination)
            self.assertEqual(loaded_path, destination)
            self.assertEqual(loaded_profile["version"], fitted["version"])

    def test_sustained_pad_creates_bass_bed_without_hits(self):
        frames = run_signal(stereo(make_pad()))
        events = [
            frame.detected_event for index, frame in enumerate(frames)
            if index * BLOCK_SIZE / SAMPLE_RATE >= 0.10
            and frame.detected_event != "none"
        ]
        self.assertEqual(events, [])
        self.assertGreater(max(frame.bass_level for frame in frames), 20)
        self.assertLessEqual(max(frame.bass_level for frame in frames), BASS_MAX_LEVEL)

    def test_long_sustained_bass_fades_to_a_weaker_bed(self):
        frames = run_signal(stereo(make_pad(duration=4.0, fade_seconds=0.20)))
        early = [
            frame.bass_level for index, frame in enumerate(frames)
            if 0.45 <= index * BLOCK_SIZE / SAMPLE_RATE <= 0.65
        ]
        late = [
            frame.bass_level for index, frame in enumerate(frames)
            if 2.70 <= index * BLOCK_SIZE / SAMPLE_RATE <= 3.20
        ]
        self.assertGreater(np.median(early), np.median(late) * 4.0)
        self.assertLessEqual(frames[round(3.0 * SAMPLE_RATE / BLOCK_SIZE)].bass_attenuation, 0.20)

    def test_small_pitch_fluctuations_still_duck(self):
        frames = run_signal(stereo(make_vibrato_bass()))
        self.assertLessEqual(frames[-20].bass_attenuation, 0.22)
        estimated = [
            frame.bass_pitch_hz for frame in frames[-100:]
            if frame.bass_pitch_hz is not None
        ]
        self.assertTrue(estimated)
        self.assertLess(abs(np.median(estimated) - 60.0), 4.0)

    def test_pitch_change_resets_ducking_and_changing_line_stays_present(self):
        first = 0.14 * np.sin(
            2.0 * np.pi * 60.0 * np.arange(round(2.0 * SAMPLE_RATE)) / SAMPLE_RATE
        )
        # A 10 Hz jump must be a distinct signal throughout the bass range.
        second = 0.14 * np.sin(
            2.0 * np.pi * 70.0 * np.arange(round(0.8 * SAMPLE_RATE)) / SAMPLE_RATE
        )
        frames = run_signal(stereo(np.concatenate((first, second))))
        before_change = frames[round(1.90 * SAMPLE_RATE / BLOCK_SIZE)]
        after_change = frames[round(2.35 * SAMPLE_RATE / BLOCK_SIZE)]
        self.assertLess(before_change.bass_attenuation, 0.30)
        self.assertGreater(after_change.bass_attenuation, 0.75)

        changing_frames = run_signal(stereo(make_changing_bass()))
        self.assertGreater(min(
            frame.bass_attenuation for frame in changing_frames
        ), 0.70)

    def test_same_pitch_loudness_change_resets_after_confirmation(self):
        duration = 3.0
        count = round(duration * SAMPLE_RATE)
        times = np.arange(count) / SAMPLE_RATE
        amplitude = np.full(count, 0.07)
        amplitude[round(2.0 * SAMPLE_RATE):] = 0.15
        signal = amplitude * np.sin(2.0 * np.pi * 60.0 * times)
        frames = run_signal(stereo(signal))

        before_change = frames[round(1.90 * SAMPLE_RATE / BLOCK_SIZE)]
        after_change = frames[round(2.40 * SAMPLE_RATE / BLOCK_SIZE)]
        self.assertLess(before_change.bass_attenuation, 0.30)
        self.assertGreater(after_change.bass_attenuation, 0.75)
        self.assertLess(after_change.pitch_stable_seconds, 0.60)

    def test_small_same_pitch_loudness_change_remains_one_signal(self):
        duration = 3.0
        count = round(duration * SAMPLE_RATE)
        times = np.arange(count) / SAMPLE_RATE
        amplitude = np.full(count, 0.10)
        # Three dB is below the editable six dB signal boundary.
        amplitude[round(2.0 * SAMPLE_RATE):] *= 10.0 ** (3.0 / 20.0)
        signal = amplitude * np.sin(2.0 * np.pi * 60.0 * times)
        frames = run_signal(stereo(signal))
        self.assertLess(frames[round(2.50 * SAMPLE_RATE / BLOCK_SIZE)].bass_attenuation, 0.30)

    def test_four_on_the_floor_kicks_create_hit_and_dip(self):
        hit_times = (0.50, 1.00, 1.50, 2.00)
        signal = make_pad(duration=2.6)
        frames = run_signal(stereo(add_kicks(signal, hit_times)))
        kick_count = sum(frame.detected_event == "kick" for frame in frames)
        self.assertGreaterEqual(kick_count, len(hit_times) - 1)
        self.assertEqual(max(frame.motor_level for frame in frames), KICK_LEVEL)
        self.assertTrue(any(frame.phase == "dip" for frame in frames))
        self.assertTrue(any(frame.phase == "recover" for frame in frames))

    def test_detected_kicks_have_fixed_strength_at_different_audio_levels(self):
        peaks = []
        for amplitude in (0.25, 0.50, 0.80):
            signal = add_kicks(
                np.zeros(round(1.1 * SAMPLE_RATE)),
                (0.45,),
                amplitude=amplitude,
            )
            frames = run_signal(stereo(signal))
            self.assertTrue(any(
                frame.detected_event == "kick" for frame in frames
            ))
            peaks.append(max(frame.motor_level for frame in frames))
        self.assertEqual(peaks, [KICK_LEVEL, KICK_LEVEL, KICK_LEVEL])

    def test_snares_create_light_short_accents(self):
        hit_times = (0.45, 0.95, 1.45, 1.95)
        signal = np.zeros(round(2.4 * SAMPLE_RATE))
        frames = run_signal(stereo(add_snares(signal, hit_times)))
        snare_count = sum(
            frame.detected_event in ("snare", "clap") for frame in frames
        )
        self.assertGreaterEqual(snare_count, len(hit_times) - 1)
        self.assertEqual(
            max(frame.motor_level for frame in frames), SNARE_CLAP_START_LEVEL
        )
        boost_frames = [
            frame for frame in frames
            if frame.active_event in ("snare", "clap")
            and frame.phase == "boost"
        ]
        self.assertTrue(boost_frames)
        self.assertTrue(all(
            frame.motor_level == SNARE_CLAP_START_LEVEL
            for frame in boost_frames
        ))
        self.assertTrue(any(
            frame.active_event in ("snare", "clap")
            and frame.phase == "hit"
            and frame.motor_level == SNARE_CLAP_LEVEL
            for frame in frames
        ))
        self.assertEqual(len(boost_frames), snare_count)

    def test_broadband_clap_triggers_from_first_burst(self):
        clap_time = 0.45
        signal = add_claps(
            np.zeros(round(1.2 * SAMPLE_RATE)), (clap_time,)
        )
        frames = run_signal(stereo(signal))
        first = next(
            index for index, frame in enumerate(frames)
            if frame.detected_event == "clap"
        )
        self.assertLessEqual(
            first * BLOCK_SIZE / SAMPLE_RATE,
            clap_time + BLOCK_SIZE / SAMPLE_RATE,
        )
        self.assertEqual(frames[first].motor_level, SNARE_CLAP_START_LEVEL)

    def test_multi_burst_clap_is_one_immediate_extended_event(self):
        clap_time = 0.45
        signal = add_claps(
            np.zeros(round(1.3 * SAMPLE_RATE)),
            (clap_time,),
            bursts=(0.0, 0.016, 0.032),
        )
        frames = run_signal(stereo(signal))
        event_frames = [
            (index, frame) for index, frame in enumerate(frames)
            if frame.detected_event == "clap"
        ]
        self.assertEqual(len(event_frames), 1)
        self.assertLessEqual(
            event_frames[0][0] * BLOCK_SIZE / SAMPLE_RATE,
            clap_time + BLOCK_SIZE / SAMPLE_RATE,
        )
        tactile_frames = [
            frame for frame in frames
            if frame.active_event == "clap"
            and frame.phase in ("boost", "hit")
        ]
        self.assertGreater(
            len(tactile_frames) * BLOCK_SIZE / SAMPLE_RATE,
            SNARE_HIT_SECONDS,
        )

    def test_quiet_and_loud_claps_are_adaptively_recognized(self):
        for amplitude in (0.08, 0.65):
            signal = add_claps(
                np.zeros(round(1.2 * SAMPLE_RATE)),
                (0.45,),
                amplitude=amplitude,
            )
            frames = run_signal(stereo(signal))
            self.assertTrue(any(
                frame.detected_event == "clap" for frame in frames
            ))
            self.assertEqual(
                max(frame.motor_level for frame in frames),
                SNARE_CLAP_START_LEVEL,
            )

    def test_body_heavy_snare_with_a_low_spike_uses_strong_path(self):
        duration = 1.2
        hit_time = 0.45
        signal = add_snares(
            np.zeros(round(duration * SAMPLE_RATE)), (hit_time,), amplitude=0.45
        )
        count = round(0.10 * SAMPLE_RATE)
        times = np.arange(count) / SAMPLE_RATE
        body = 0.35 * np.sin(2.0 * np.pi * 240.0 * times)
        body *= np.exp(-times / 0.035)
        start = round(hit_time * SAMPLE_RATE)
        signal[start:start + count] += body
        frames = run_signal(stereo(signal))
        self.assertTrue(any(
            frame.detected_event == "kick" for frame in frames
        ))
        self.assertEqual(
            max(frame.motor_level for frame in frames), KICK_LEVEL
        )

    def test_drum_hits_are_immediate_short_overrides(self):
        signal = make_pad(duration=1.4)
        frames = run_signal(stereo(add_kicks(signal, (0.50,))))
        hit_indices = [
            index for index, frame in enumerate(frames)
            if index * BLOCK_SIZE / SAMPLE_RATE >= 0.30
            and frame.active_event == "kick" and frame.phase == "hit"
        ]
        self.assertTrue(hit_indices)
        first_hit = frames[hit_indices[0]]
        self.assertEqual(first_hit.motor_level, KICK_LEVEL)
        self.assertGreater(first_hit.motor_level, first_hit.bass_level * 3)
        hit_duration = len(hit_indices) * BLOCK_SIZE / SAMPLE_RATE
        self.assertLessEqual(hit_duration, KICK_HIT_SECONDS + BLOCK_SIZE / SAMPLE_RATE)

        snare_signal = np.zeros(round(1.2 * SAMPLE_RATE))
        snare_frames = run_signal(stereo(add_snares(snare_signal, (0.45,))))
        snare_hit_count = sum(
            frame.active_event == "snare" and frame.phase == "hit"
            for frame in snare_frames
        )
        snare_duration = snare_hit_count * BLOCK_SIZE / SAMPLE_RATE
        self.assertLessEqual(
            snare_duration, SNARE_HIT_SECONDS + BLOCK_SIZE / SAMPLE_RATE
        )

    def test_kick_is_strong_snare_is_light_and_immediate(self):
        kick_frames = run_signal(stereo(add_kicks(
            np.zeros(round(1.2 * SAMPLE_RATE)), (0.50,)
        )))
        snare_time = 0.45
        snare_frames = run_signal(stereo(add_snares(
            np.zeros(round(1.2 * SAMPLE_RATE)), (snare_time,)
        )))
        kick_peak = max(frame.motor_level for frame in kick_frames)
        snare_peak = max(frame.motor_level for frame in snare_frames)
        self.assertEqual(kick_peak, KICK_LEVEL)
        self.assertEqual(snare_peak, SNARE_CLAP_START_LEVEL)

        first_snare = next(
            index for index, frame in enumerate(snare_frames)
            if frame.detected_event == "snare"
        )
        first_snare_time = first_snare * BLOCK_SIZE / SAMPLE_RATE
        self.assertLessEqual(first_snare_time, snare_time + BLOCK_SIZE / SAMPLE_RATE)

    def test_high_frequency_hats_do_not_drive_erm(self):
        hit_times = np.arange(0.25, 1.80, 0.125)
        signal = np.zeros(round(2.0 * SAMPLE_RATE))
        frames = run_signal(stereo(add_hats(signal, hit_times)))
        events = [frame.detected_event for frame in frames if frame.detected_event != "none"]
        self.assertEqual(events, [])
        self.assertLessEqual(max(frame.motor_level for frame in frames), 3)

    def test_open_hats_and_cymbal_tails_do_not_drive_erm(self):
        signal = add_open_hats(
            np.zeros(round(2.0 * SAMPLE_RATE)), (0.35, 1.05)
        )
        frames = run_signal(stereo(signal))
        self.assertFalse(any(
            frame.detected_event != "none" for frame in frames
        ))

    def test_sustained_mid_high_material_does_not_trigger(self):
        duration = 2.0
        count = round(duration * SAMPLE_RATE)
        times = np.arange(count) / SAMPLE_RATE
        fade = np.minimum(np.clip(times / 0.30, 0.0, 1.0),
                          np.clip((duration - times) / 0.30, 0.0, 1.0))
        vocal_like = fade * (
            0.08 * np.sin(2.0 * np.pi * 440.0 * times)
            + 0.04 * np.sin(2.0 * np.pi * 1_320.0 * times)
            + 0.02 * np.sin(2.0 * np.pi * 3_960.0 * times)
        )
        frames = run_signal(stereo(vocal_like))
        self.assertFalse(any(
            frame.detected_event in ("snare", "clap") for frame in frames
        ))

    def test_gradual_filter_sweep_does_not_trigger(self):
        duration = 2.2
        count = round(duration * SAMPLE_RATE)
        times = np.arange(count) / SAMPLE_RATE
        frequency = 300.0 + (5_500.0 - 300.0) * times / duration
        phase = 2.0 * np.pi * np.cumsum(frequency) / SAMPLE_RATE
        fade = np.minimum(np.clip(times / 0.35, 0.0, 1.0),
                          np.clip((duration - times) / 0.35, 0.0, 1.0))
        frames = run_signal(stereo(0.10 * fade * np.sin(phase)))
        self.assertFalse(any(
            frame.detected_event in ("snare", "clap") for frame in frames
        ))

    def test_narrowband_percussive_pluck_is_not_a_snare(self):
        duration = 1.2
        count = round(duration * SAMPLE_RATE)
        signal = np.zeros(count)
        pluck_count = round(0.20 * SAMPLE_RATE)
        times = np.arange(pluck_count) / SAMPLE_RATE
        pluck = 0.30 * np.sin(2.0 * np.pi * 1_200.0 * times)
        pluck *= np.exp(-times / 0.045)
        start = round(0.45 * SAMPLE_RATE)
        signal[start:start + pluck_count] += pluck
        frames = run_signal(stereo(signal))
        self.assertFalse(any(
            frame.detected_event in ("snare", "clap") for frame in frames
        ))

    def test_dense_mix_stays_bounded_and_preserves_events(self):
        duration = 3.0
        signal = make_pad(duration=duration, amplitude=0.18)
        signal = add_kicks(signal, np.arange(0.40, 2.80, 0.25), amplitude=0.70)
        signal = add_snares(signal, np.arange(0.525, 2.80, 0.50), amplitude=0.35)
        signal = add_hats(signal, np.arange(0.275, 2.80, 0.125), amplitude=0.25)
        frames = run_signal(stereo(signal))
        levels = np.array([frame.motor_level for frame in frames])
        kick_count = sum(frame.detected_event == "kick" for frame in frames)
        snare_count = sum(
            frame.detected_event in ("snare", "clap") for frame in frames
        )
        self.assertGreaterEqual(kick_count, 8)
        self.assertGreaterEqual(snare_count, 4)
        self.assertGreaterEqual(levels.min(), 0)
        self.assertLessEqual(levels.max(), 127)
        self.assertLess(np.mean(levels >= 90), 0.55)

    def test_simultaneous_kick_and_clap_uses_kick_shape(self):
        duration = 1.2
        hit_time = 0.50
        signal = np.zeros(round(duration * SAMPLE_RATE))
        signal = add_kicks(signal, (hit_time,), amplitude=0.65)
        signal = add_claps(signal, (hit_time,), amplitude=0.40)
        frames = run_signal(stereo(signal))
        # A clap-like click may occupy the first causal block. The event must
        # upgrade to a kick as soon as its low body satisfies the strict gate.
        kick_frame = next(
            frame for frame in frames if frame.detected_event == "kick"
        )
        self.assertEqual(kick_frame.motor_level, KICK_LEVEL)
        kick_hit_count = sum(
            frame.active_event == "kick" and frame.phase == "hit"
            for frame in frames
        )
        duration_seconds = kick_hit_count * BLOCK_SIZE / SAMPLE_RATE
        self.assertGreaterEqual(
            duration_seconds, KICK_HIT_SECONDS - BLOCK_SIZE / SAMPLE_RATE
        )

    def test_low_spike_bypasses_tonal_guard_for_weak_and_strong_hits(self):
        weak = add_kicks(
            make_pad(duration=2.2), (1.50,), amplitude=0.25
        )
        weak_frames = run_signal(stereo(weak))
        self.assertTrue(any(
            frame.detected_event == "kick"
            for index, frame in enumerate(weak_frames)
            if index * BLOCK_SIZE / SAMPLE_RATE >= 0.50
        ))
        self.assertEqual(
            max(
                frame.motor_level
                for index, frame in enumerate(weak_frames)
                if index * BLOCK_SIZE / SAMPLE_RATE >= 0.50
            ),
            KICK_LEVEL,
        )
        self.assertTrue(any(frame.tonal_guard_active for frame in weak_frames))

        definite = add_kicks(
            make_pad(duration=2.2), (1.50,), amplitude=0.50
        )
        definite_frames = run_signal(stereo(definite))
        self.assertTrue(any(
            frame.detected_event == "kick" for frame in definite_frames
        ))
        self.assertEqual(
            max(frame.motor_level for frame in definite_frames), KICK_LEVEL
        )

    def test_smooth_harmonic_pad_does_not_retrigger_after_startup(self):
        duration = 2.5
        count = round(duration * SAMPLE_RATE)
        times = np.arange(count) / SAMPLE_RATE
        modulation = 1.0 + 0.15 * np.sin(2.0 * np.pi * 1.5 * times)
        signal = modulation * (
            0.11 * np.sin(2.0 * np.pi * 60.0 * times)
            + 0.025 * np.sin(2.0 * np.pi * 120.0 * times)
            + 0.012 * np.sin(2.0 * np.pi * 240.0 * times)
        )
        frames = run_signal(stereo(signal))
        self.assertFalse(any(
            frame.detected_event == "kick"
            for index, frame in enumerate(frames)
            if index * BLOCK_SIZE / SAMPLE_RATE >= 0.12
        ))

    def test_drums_bypass_pitch_ducking_and_transitions_send_immediately(self):
        signal = make_pad(duration=3.2, fade_seconds=0.20)
        signal = add_kicks(signal, (2.45,), amplitude=0.65)
        signal = add_snares(signal, (2.75,), amplitude=0.40)
        frames = run_signal(stereo(signal))
        pre_hit = frames[round(2.35 * SAMPLE_RATE / BLOCK_SIZE)]
        self.assertLess(pre_hit.bass_attenuation, 0.25)

        kick_frame = next(
            frame for frame in frames if frame.detected_event == "kick"
        )
        snare_frame = next(
            frame for frame in frames if frame.detected_event == "snare"
        )
        self.assertEqual(kick_frame.motor_level, KICK_LEVEL)
        self.assertEqual(snare_frame.motor_level, SNARE_CLAP_START_LEVEL)
        self.assertTrue(should_send_frame(
            kick_frame, "bass", now=1.001, last_send=1.000
        ))

        steady_frame = next(
            frame for frame in frames if frame.phase == "bass"
        )
        self.assertFalse(should_send_frame(
            steady_frame, "bass", now=1.001, last_send=1.000
        ))

    def test_rhythmic_bass_attacks_can_become_rhythm(self):
        frames = run_signal(stereo(make_rhythmic_bass()))
        event_count = sum(frame.detected_event == "kick" for frame in frames)
        self.assertGreaterEqual(event_count, 4)
        self.assertGreater(max(frame.bass_level for frame in frames), 10)
        self.assertLessEqual(max(frame.bass_level for frame in frames), BASS_MAX_LEVEL)

    def test_silence_releases_to_zero(self):
        active = add_kicks(make_pad(duration=1.0), (0.50,))
        silence = np.zeros(round(1.0 * SAMPLE_RATE))
        frames = run_signal(stereo(np.concatenate((active, silence))))
        self.assertEqual(frames[-1].motor_level, 0)
        self.assertEqual(frames[-1].phase, "bass")

    def test_terminal_status_is_one_physical_line_at_common_widths(self):
        frame = run_signal(stereo(np.zeros(BLOCK_SIZE)))[0]
        for columns in (60, 80, 120):
            line = format_status_line(
                frame, processing_ms=0.75, columns=columns
            )
            self.assertNotIn("\n", line)
            self.assertNotIn("\r", line)
            self.assertLessEqual(len(line), columns - 1)
        self.assertIn("#0/0", format_status_line(
            frame, processing_ms=0.75, columns=120
        ))
        self.assertEqual(TTY_CLEAR_LINE, "\r\x1b[2K")

    def test_event_counters_persist_between_short_hits(self):
        signal = np.zeros(round(1.8 * SAMPLE_RATE))
        signal = add_kicks(signal, (0.45, 1.05), amplitude=0.65)
        signal = add_snares(signal, (0.75, 1.35), amplitude=0.40)
        frames = run_signal(stereo(signal))
        self.assertEqual(frames[-1].kick_count, 2)
        self.assertEqual(frames[-1].snare_count, 2)


if __name__ == "__main__":
    unittest.main()
