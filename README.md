# Haptonics

The website is in `web/`. From the repository root, run `python3 -m http.server 8000` and open `http://localhost:8000/web/musex.html`. The page needs `web/shepherd.js` and `web/assets/shepherd.png` alongside it. Browser audio capture and Web Bluetooth require browser support and permission; the demo mode runs without hardware.

Vibration firmware lives in the root ESP-IDF project, `dual_lra_firmware/`, `lra_0832_firmware/`, and `lra_0832_ble_firmware/`. The Python bridge and test sources are at the repository root. Build and flash only the firmware matching your board, driver, and motor, following the instructions below.

This folder does not currently contain `mac_mic_haptics.py` or `erm_bass_rhythm.py`, although the historical instructions below refer to them. Their commands will require those files to be restored.

# ESP32 -> DRV2605L -> vibration motor first test

This project is an ESP-IDF C example. It uses I2C to put a DRV2605L into
real-time playback mode. The motor is off after reset; pressing BOOT starts
continuous vibration.

## Important identification assumption

The supplied ESP32 schematics are for a YD-ESP32 carrier using an
ESP32-WROOM-32-style module. The program uses GPIO21 (SDA) and GPIO22 (SCL),
which are exposed on that carrier. Confirm the labels on your physical board
before soldering.

The DRV2605L board pin labels are assumed to be `VCC`/`VIN`, `GND`, `SDA`,
`SCL`, `TRIG`, `OUT+`, and `OUT-`. Your board does not expose `EN`. Many
breakouts tie EN high on the PCB when they omit the header pin; verify this
by checking the board schematic or a clear photo before assuming it. A pin
labelled `NC` is left unconnected.

## Wiring

With all power disconnected, solder one wire from each connection below:

| ESP32 carrier | DRV2605L breakout | Purpose |
|---|---|---|
| 3V3 | VCC/VDD/VIN | 3.3 V driver supply |
| GND | GND | Common ground |
| GPIO21 | SDA | I2C data |
| GPIO22 | SCL | I2C clock |
| GND | TRIG | Keep the unused trigger input inactive |
| - | motor wire 1 -> OUT+ | Differential motor output |
| - | motor wire 2 -> OUT- | Differential motor output |

Leave the `NC` pin unconnected. Do not jumper `NC` to 3V3 or GND. If the
DRV2605L does not respond at address `0x5A` after wiring, stop and check the
breakout schematic: the unexposed EN pin must be pulled high somewhere on
the breakout for the chip to operate.

The two motor wires are interchangeable for a basic ERM vibration motor. Do
not connect either motor wire to ESP32 GPIO, 3V3, or GND. Do not power the
motor directly from the ESP32; the DRV2605L is the motor driver.

The breakout must have I2C pull-up resistors, normally already fitted. If it
does not, add one 4.7 kOhm resistor from SDA to 3V3 and another 4.7 kOhm
resistor from SCL to 3V3. Never apply 5 V to ESP32 GPIO pins.

## Build and flash

Install ESP-IDF, open a terminal in this directory, then run:

```sh
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/esp/esp-idf-v5.4.2/export.sh"
idf.py set-target esp32
idf.py build
idf.py -p /dev/cu.usbmodem101 flash monitor
```

Replace the serial port if macOS assigns a different name. You can also set
`HAPTIC_MUSIC_SERIAL_PORT` before running the Python bridge.
Press Ctrl-] to leave the monitor. RESET restarts the ESP32 in a latched-off
state: the DRV2605L stays in standby and all serial `H0–H127` commands are
ignored indefinitely. Press BOOT once to enable motor control and run the
one-second confirmation pulse. Do not hold BOOT while pressing RESET, because
GPIO0 is also the ESP32 download-mode strap pin.

## Mac system-audio intensity bridge

The proven baseline bridge `mac_mic_haptics.py` reads system audio from
BlackHole 2ch and sends overall intensity to the ESP32 over USB. Select the
macOS Multi-Output Device, then run:

```sh
python3 mac_mic_haptics.py
```

The displayed dB values are relative digital levels, not calibrated acoustic
SPL measurements. Press Ctrl-C to stop and send `H0` to the motor.

## EDM bass-and-rhythm ERM bridge

The file `erm_bass_rhythm.py` is a causal, low-latency profile for electronic
music. Audio is processed at 48 kHz in 256-sample blocks, so each decision uses
about 5.3 ms of new audio and no future samples. It gives bass a restrained
`0..26` vibration bed and tracks the dominant pitch from 35–180 Hz. If one
tonal pitch remains stable for one second, only the bass bed fades smoothly to
15%. A pitch change beyond 5 Hz or 5%, a confirmed 6 dB loudness change, or
silence starts a new signal and restores it.

Impact recognition analyzes six bands: sub (`25–70 Hz`), kick punch
(`70–180 Hz`), body (`180–800 Hz`), crack (`800–3500 Hz`), air
(`3500–8000 Hz`), and hat/cymbal evidence above `8000 Hz`. A causal 512-sample
Hann FFT measures positive log-spectral flux from the previous and current
blocks. Low envelopes, one-block rise, waveform crest, and broadband flux are
normalized against the previous 1.5 seconds with median/MAD statistics. The
only absolute floor is `-70 dBFS`, used to reject numerical noise.

An additional high-recall path isolates `20–150 Hz` with a steep causal filter.
Every sharp rise that clears the editable `-55 dBFS` level, `5 dB` novelty,
and `2 dB` block-rise thresholds is treated as a kick and overrides the bass
bed at `127/127` for 45 ms. A sample-rate peak follower with a 60 ms release
prevents the individual wave cycles of a steady 20–30 Hz tone from retriggering
the motor. The path rearms causally and has an 80 ms same-path guard, so regular
eighth-note hits remain complete while one kick tail does not create a second
hit. These constants are grouped under `LOW_SPIKE_*` near the top of the file.

This mode intentionally favors low-impact recall: a snare, SFX, bass pluck, or
pad onset that contains a genuine sharp `20–150 Hz` spike will also receive the
strong kick shape. Smooth or already-sustained low tones remain the restrained
bass bed.

Other high-confidence kicks override the bass bed immediately at `127/127` for
45 ms. A less certain low-frequency onset produces an immediate `60/127`
provisional pulse; evidence from that block and the following block is combined
so a bass-masked kick can upgrade to `127` no later than 5.3 ms later.

Click-leading kicks use a second causal path tuned against the first confirmed
kick at about 14.800 seconds in `MOURNFUL CRYING OF A GROWN ADULT.mp3`. A sharp
broadband onset starts at `60` immediately while four low-frequency filters
(`25–55`, `55–85`, `85–120`, and `120–180 Hz`) track the following five
blocks. Punch growth, sub growth, low-to-body dominance, and downward frequency
motion can confirm the kick and upgrade it to `127` within 26.7 ms. If the low
body does not arrive, the event remains the light `60` then `33` impact shape.
Generic broadband impacts use that same light shape. Growling or modulated bass
that persists without a sharp onset remains in the bass bed. The detector
rearms after two quiet-score blocks and uses a 35 ms retrigger guard, allowing
alternating eighth notes through 240 BPM and allowing new hits to interrupt
recovery.

Snares and claps begin with one short `60/127` startup block, then use
`33/127` for the remainder of a 35 ms tap. All hits are followed by a 14 ms
near-silent dip and a 50 ms controlled bass return, preventing layers from
colliding.

Snare and clap recognition is split into four causal analysis bands: body
(`180–800 Hz`), crack (`800–3500 Hz`), clap air (`3500–8000 Hz`), and
hat/cymbal evidence above `8000 Hz`. A snare requires a sudden crack increase
with body support; a clap requires simultaneous crack and air increases.
High-frequency-only hats, sustained vocals and pads, gradual sweeps, and
narrowband melodic plucks remain silent on the ERM. Closely spaced clap bursts
extend one already-started hit instead of delaying the first response. If a
strong kick and clap arrive together, the 45 ms kick shape takes priority.

Keep the macOS output set to the Multi-Output Device, then run:

```sh
python3 erm_bass_rhythm.py
```

The bridge automatically loads `impact_profile.json` from this folder when a
valid version-1 profile exists. Without it, the built-in high-recall EDM profile
is used. To calibrate with one representative MP3, M4A, or WAV file, run:

```sh
python3 erm_bass_rhythm.py --calibrate /path/to/song.mp3
```

The script converts a temporary 48 kHz stereo WAV with macOS `afconvert`, plays
the original through `afplay`, and asks for live keyboard labels: `K` for kick,
`I` for another impact/SFX, `G` for a growl that should stay bass-only, `F` for
a false trigger, and `Q` to finish. Each keypress snaps to the strongest
candidate in the preceding 250 ms to compensate for reaction time. It fits
weights and thresholds with an F2 objective that favors recall, reports a
held-out score, and writes a versioned `impact_profile.json`. Run normal mode
again to use it. Delete or rename that JSON file to return to built-in defaults.

The terminal uses one compact status line that refreshes in place without
scrolling. It reports motor and bass level, pitch/ducking, active event, kick
and generic-impact confidence, low-spike level/novelty/rise, detector state, cumulative
`#kick/snare/impact` counters, and DSP time. Wider terminals also show low
onset/sharpness/broadband scores, click-candidate age and state, punch/sub
growth, low-frequency center/drop, low-to-body dominance, and body/crack/air
levels. The counters persist so a short event cannot disappear between the 10
Hz screen refreshes.
Redirected output uses one normal log line per second.
Drum and phase-transition commands bypass the normal background serial rate
limit. All tuning values for hand/wrist testing are grouped near the top of
the script. Press Ctrl-C to stop safely.

Run the synthetic regression tests with:

```sh
python3 -m unittest -v test_erm_bass_rhythm.py
```

On this Mac the same command also runs an assessment against the supplied
reference MP3. On another machine, set `HAPTIC_REFERENCE_AUDIO` to that file's
path; the reference test is skipped when the file is unavailable. It verifies
the immediate `60` response at 14.800 seconds, a full-strength low-spike upgrade
by 14.827 seconds, and several matching later kicks. Earlier low-band spikes in
the song may now produce strong impacts by design.

If it does not run, disconnect power first and check that the breakout's
motor output is `OUT+`/`OUT-`, that the driver and ESP32 share ground, and
that an I2C scan finds address `0x5A`.

## Kick-only debugging bridge

Use `kick_only_debug.py` when tuning kick recognition without any other haptic
layer:

```sh
python3 kick_only_debug.py
```

This is a separate minimal program. It contains only a steep `20–150 Hz`
filter and causal low-spike detector. Accepted kicks produce a fixed `H127`
for 45 ms; the motor receives `H0` between hits. There is no continuous bass
bed and no snare, clap, SFX, melody, or high-frequency recognition/output.
The single-line display reports low-band level, novelty, block rise,
low-to-full energy ratio, hit count, and DSP time. All debug thresholds are
grouped near the top of the file. Ctrl-C sends `H0` before exit.

Run its isolated tests with:

```sh
python3 -m unittest -v test_kick_only_debug.py
```

## Standalone 0832 LRA kick mode

`lra_0832_kick_debug.py` is a separate version of the latest kick-only ERM
program for a two-wire 0832 LRA. It intentionally uses the same causal
`20–150 Hz` kick detector and ignores bass pads, snares, claps, melody, and
all higher-frequency events. A detected kick produces an immediate
`127/127` leading tap for 18 ms, followed by a lighter `72/127` body for
24 ms, then zero. This shorter two-stage shape takes advantage of the LRA's
faster response and avoids making successive kicks blur together.

The matching ESP32-S3 project is isolated in `lra_0832_firmware/`; it does not
replace the ERM source code. It configures the DRV2605L for closed-loop LRA
real-time playback, runs auto-calibration when BOOT is pressed, and continues
to use the existing `H0` through `H127` serial protocol. RESET always returns
the motor to a latched-off state: commands are ignored indefinitely until
BOOT is deliberately pressed again. Serial loss also stops the motor after
500 ms.

The firmware tries the **235 Hz, 2.0 Vrms, 2.4 V peak** 0832 LRA profile first.
If BOOT auto-calibration fails, it automatically retries the matching 205 Hz
0832 profile before leaving the motor disabled. Before flashing, confirm the
motor's resonance frequency and rated voltage with the seller. Do not use the
default voltage settings for a motor with a different rated voltage.

The motor still connects only to DRV2605L `OUT+` and `OUT-`; either wire
orientation is acceptable. On the ESP32-S3-Touch-LCD-1.46 with this firmware,
connect GPIO11 to SDA and GPIO10 to SCL, 3V3 to VCC, GND to GND, and GND to
unused TRIG. Leave NC unconnected.

Build and flash this separate firmware only after the 0832 LRA is connected:

```sh
source "$HOME/esp/esp-idf/export.sh"
idf.py -C lra_0832_firmware set-target esp32s3
idf.py -C lra_0832_firmware build
idf.py -C lra_0832_firmware -p /dev/cu.usbmodem101 flash monitor
```

After flashing or RESET, press BOOT once. A successful calibration produces
one short confirmation tap and the monitor prints `calibration passed`. If
calibration cannot lock but the BOOT tap works, the firmware accepts audio
commands in uncalibrated RTP mode so the haptic bridge can still drive the
motor.

Leave the macOS Sound Output set to the Multi-Output Device that includes
BlackHole 2ch, exit the serial monitor with Ctrl-], and run the JBL-style
speaker-emulation bridge:

```sh
/opt/anaconda3/bin/python3 trial-1.py --port /dev/cu.usbmodem101 --device "BlackHole 2ch"
```

`trial-1.py` first sends obvious `H100` proof taps for 5 seconds before audio
capture starts. Press BOOT while those proof taps are running. If the live
status line shows `in -180.0dB` or warns that BlackHole is silent, the Mac is
not routing system output into BlackHole yet. To skip this startup proof after
debugging, run:

```sh
/opt/anaconda3/bin/python3 trial-1.py --port /dev/cu.usbmodem101 --device "BlackHole 2ch" --no-startup-proof
```

For stronger or softer speaker-cabinet motion, tune `--sensitivity`; higher
values create more vibration from the same music:

```sh
/opt/anaconda3/bin/python3 trial-1.py --port /dev/cu.usbmodem101 --device "BlackHole 2ch" --sensitivity 3.5
```

For the older kick-only detector, run:

```sh
/opt/anaconda3/bin/python3 lra_0832_kick_debug.py
```

For a hardware-only check, run:

```sh
/opt/anaconda3/bin/python3 lra_0832_direct_test.py
```

This sends repeating `H100` taps with no audio detection. If that does not
vibrate after pressing BOOT while the test is running, debug firmware,
calibration, motor voltage/frequency, or wiring before trying music again.

Ctrl-C sends `H0` before exit. Run the LRA regression tests with:

```sh
/opt/anaconda3/bin/python3 -m unittest -v test_lra_0832_kick_debug.py
```

Do not run an LRA from the ERM firmware profile, and do not reconnect the ERM
while this LRA firmware is active; the DRV2605L drive mode and voltage
configuration are actuator-specific.

## Future second actuator

The second driver is configured for the JYLRA1030Z LRA (1.8 Vrms, 210 Hz,
100 mA max). Because both DRV2605L boards use I2C address `0x5A`, it must use
its own I2C bus: GPIO32 -> SDA and GPIO33 -> SCL. Power and ground are shared.

On boot, the LRA driver tries closed-loop auto-calibration. If the installed
motor does not lock, it falls back to the verified fixed 210 Hz open-loop
drive. The serial protocol is `H0` through `H127` for ERM and `L0` through
`L127` for LRA. Press BOOT once after flashing to initialize both channels and
run the LRA test tap.

First run `python3 lra_direct_test.py`. After pressing BOOT once, it sends a
strong 100 ms LRA tap every 600 ms; if this does not move the motor, diagnose
wiring/calibration before trying audio.

Then run `/opt/anaconda3/bin/python3 lra_texture.py`. It enters music mode
immediately and sends `L0` through `L127` to the 1030Z channel of the current
dual-driver firmware. Press BOOT once after the bridge prints its startup
message. The `H` channel controls the other driver and must not be used for
the 1030Z.

The live status shows the exact command sent, the send counter, and the
high-frequency gate state. The gate forces `H0` (or `L0` when explicitly
selected) when the routed 800–8000 Hz band is below the real-audio floor, so
BlackHole silence or input noise cannot create continuous LRA vibration. When
the gate is open, the bridge passes through the same shifted LRA output that
worked before the silence gate was added. The bridge never pulses DTR because
that resets the ESP32 and re-latches the motor off; BOOT is the intentional
enable control.

This is the complete ERM algorithm—not a separate snare/clap detector—with
only its frequency routing moved upward. Its energy bed and strong-spike path
use 800–8000 Hz; the sub/punch/body/crack/air hierarchy is shifted upward as
well. Adaptive thresholds, causal scoring, tonal ducking, re-trigger guards,
hit/dip/recovery phases, levels and transmission timing stay shared with ERM.
For serial debugging only, run
`/opt/anaconda3/bin/python3 lra_texture.py --startup-proof`.

Run both suites with:

```sh
/opt/anaconda3/bin/python3 -m unittest -v \
    test_erm_bass_rhythm.py test_lra_high_frequency.py
```

## Run the 0832 and 1030Z together

Two Python programs cannot share one USB serial port. Use
`dual_lra_bridge.py`, which reads BlackHole once and sends both command
channels:

- `H0` through `H127`: 0832 LRA, kick-only 20–150 Hz pulse.
- `L0` through `L127`: 1030Z LRA, gated 800–8000 Hz texture.

This requires two DRV2605L boards on separate I2C buses. Connect the 0832
driver to GPIO21 SDA / GPIO22 SCL, and connect the 1030Z driver to GPIO32 SDA /
GPIO33 SCL. Both boards may share 3.3 V and GND.

Build and flash the matching dual-LRA firmware:

```sh
idf.py -C dual_lra_firmware build
idf.py -C dual_lra_firmware -p /dev/cu.usbmodem101 flash
```

Then start the single combined bridge:

```sh
/opt/anaconda3/bin/python3 dual_lra_bridge.py
```

Press BOOT once after the startup message. The 0832 confirmation pulse occurs
first, followed by the shorter 1030Z pulse. RESET stops and latches both motors
off; Ctrl-C sends both `H0` and `L0`.

The combined bridge gives each detected 0832 kick an immediate 45 ms
full-strength peak followed by a 30 ms level-100 body. This longer pulse is
deliberate: the earlier 18 ms + 24 ms shape was visible in terminal output but
too brief to remain clearly tactile beside the 1030Z.
