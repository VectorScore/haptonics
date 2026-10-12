# Haptonics prototype

Open `musex.html` in a compatible desktop browser. Chrome is recommended for audio input and Bluetooth. This is a local prototype; purchases, community posting, and physical watch visual streaming are not implemented. Home statistics use local daily records: completed breathing sessions and actual active music analysis time. Existing installs start these counters at zero; no historical activity is invented.

## Current interface

- Home: round watch-framed preview, today’s mood shortcuts, device connection below the preview, and separate expandable audio and effect/pet controls
- Modes: music visuals, breathing, metronome, relaxation, and mood diary
- Store: side-by-side effect/pet previews (three each) and full catalogs, including two custom effect presets and six pet choices
- Profile settings: English / 简体中文, dark/light/system appearance, custom theme hue, text size, haptics, reduced motion, 30/60/120 fps limits, and confirmed local-data erasure
- Mood diary: native color emojis, custom emoji palette/text input, local calendar entries, and the most frequent mood of the last seven days (most recent breaks ties)

Native emoji artwork comes from the operating system; Apple Color Emoji is preferred on Apple devices. Control + Command + Space opens the macOS system emoji picker while typing. Erasure removes only the six legacy `musex-*` storage keys, leaving unrelated browser data intact and preserving existing users' local data across the brand change.

## Audio access

Sources: demo, uploaded music, microphone, device audio. Selecting microphone enumerates available devices without starting capture. Start Analysis requests one recording stream, then enumerates named inputs while the stream is live. If the browser exposes no device list, starting with the default input still attempts capture. Stop/cancel releases all tracks. Permission persistence belongs to the browser and OS; screen sharing can require a new selection each time.

Microphone sensitivity is adjustable from 1× to 12× (default 3×) on the custom-analysis branch; original raVe receives the unchanged source. Live input is not monitored to speakers, avoiding feedback. File playback uses the selected output where AudioContext.setSinkId is supported; otherwise use system sound settings. Shared device audio uses getDisplayMedia and rejects shares that contain no audio track. Available tab/system audio depends on browser and OS support.

There is no dedicated BlackHole source tab. An installed BlackHole or aggregate input can still be selected from the microphone input list. To capture system audio through BlackHole, set macOS output to a Multi-Output Device containing speakers/headphones plus BlackHole, and select BlackHole as the app input. A Multi-Output Device itself is output-only. This page does not embed or install audio drivers.

## Rendering

Custom effects: lines, particles and spikes, plus gentle-line and vivid-particle catalog presets. Color, presets and detailed controls are shared by phone/watch previews. Pets are centered, outline contours with cat whiskers and a small bear nose; audio detail displacement is bounded to preserve their shape. The former dog option now uses a wolf silhouette; its stored ID is preserved for compatibility. Custom response uses a fast 12 ms attack and preset-dependent release, a recent-loudness reference, a noise gate, and low-band onset accents. The spectrum renders at the selected frame limit, independently of the 12.5 Hz text update cadence. Its logarithmic frequency axis spans 20 Hz–20 kHz; level spans −12 to +12 dB relative to the recent average spectrum power (0 dB reference), not dBFS. Demo data is explicitly synthetic.

120 fps is a ceiling, not a guarantee; display refresh rate, browser scheduling and hardware determine achieved performance. Original raVe retains its original 60 Hz feedback cadence.

## raVe isolation

[ajm13/raVe](https://github.com/ajm13/raVe) upstream drawing routines and analyzer defaults are preserved: 600 px canvas, radii 30/120, gain/bass 1, smoothing .2, feedback .95, original hue cycle. No custom pets, transient glow, color override, or microphone boost affects this branch. Controls are locked. Future unspecified visual changes apply to custom effects only; see `AGENTS.md`.

## Breathing and haptics

Durations: 30 seconds, 1 minute, 3 minutes, custom 10–3600 seconds. A session ends after a complete breathing cycle (30 seconds rounds to 38 seconds for 4-7-8, 32 seconds for box breathing). Switching pattern during a session requires confirmation. Inhalation sends a low continuous amplitude with a 100 ms accent each second; stopping/hidden-page transitions send zero. Browser phone vibration is best-effort and cannot reproduce amplitude or carrier frequency.

Compatible BLE Nordic UART firmware receives H0–H127 amplitude commands. The repository firmware (`lra_0832_ble_firmware/main/main.c`) uses a 235 Hz motor carrier with a 205 Hz fallback, both above 100 Hz. The user’s ≥100 Hz requirement applies only to the built-in metronome motor carrier. Incoming audio and audio-driven effects have no 100 Hz minimum or cutoff. This carrier is independent of musical BPM, BLE update rate and visual fps. The browser cannot set a motor carrier through navigator.vibrate. BOOT enable is required on compatible hardware. Physical haptics have not been tested in this session.

Metronome: numerator 1–12, denominator 2/4/8/16, 30–240 quarter-note BPM, 0–100 strength, accented downbeats. Late animation frames skip old beats rather than replaying them in bursts. Timed modes pause/stop when hidden.

## Verification and backups

Syntax and mocked DOM/Canvas/Web Audio regressions pass for source switching, one capture request, microphone gain isolation, stream cleanup, default input, missing shared audio, bilingual round-trip, native/custom mood persistence, catalog navigation, preset locks, 120 Hz frame scheduling, breathing cycle completion/switch confirmation and scoped erasure. raVe drawing methods still exactly match the retrieved upstream routines.

Actual browser appearance, OS permission prompts, audio capture/output and physical hardware remain unverified: the browser preview tool previously rejected local file URLs. The tests use mocked drawing and audio APIs and do not establish visual quality or physical timing.

Historical HTML and Shepherd snapshots were removed after confirming that they were not loaded by the app. The current runtime files are `musex.html`, `shepherd.js`, and `assets/shepherd.png`.

## Player and phone dialogs

Selecting Upload music only changes source mode. Choose music file opens the picker; loading a file does not autoplay. The compact player provides play/pause, a seek slider and elapsed/total mm:ss. Pausing preserves the position.

Parameter help and emoji selection use overlays contained inside the phone screen, ordered title, scrollable content, close. They support Escape, focus return and keyboard focus containment. Explanation paragraphs retain punctuation in both languages; captions omit final punctuation. Unavailable raVe customization controls are hidden.

Daily usage (`musex-usage`) records completed breathing sessions and audio-analysis milliseconds, split at local midnight and saved on stop, page exit and every five seconds while rendering. Microphone sensitivity uses `musex-mic-sensitivity`. Both keys are included in confirmed local-data erasure.

Latest simulated checks cover dialog containment/order/punctuation, source selection without opening the file chooser, explicit upload, seek timing, custom microphone sensitivity isolated from raVe, hidden controls, daily time accumulation and completed-only breathing counts. Actual browser appearance and hardware remain unverified.

## Adaptive visuals and direction

Custom effects now expand uniformly with bass energy and rotate bounded fine detail, avoiding fixed frequency-to-angle lobes. Outward/ring is the default; inward starts close to the watch edge and contracts. Strong-signal overlays default to None, with optional dots or expanding/contracting fading rings. Onsets use low-band rise relative to a running reference and a 140 ms refractory interval, with bounded event history. Quiet input is gated; sustained audio remains below fresh accents. Presets use substantially different gains, bass response, smoothing and trails.

The custom audio context requests interactive latency, custom analyzer smoothing is capped at .25, and geometry smoothing is reduced. FFT size remains 2048 to retain bass resolution; raVe’s separate analyzer and original drawing routines remain unchanged. Browser capture and hardware latency remain outside the page’s control and have not been measured.

Dropdowns open consistent large option sheets inside the phone, including keyboard entry and focus containment. Appearance options list Light before Dark while retaining Dark as the default. Standard text is larger; the large-text setting scales core controls further.

Mocked regressions pass for adaptive onset response, silence decay, preset separation, onset cooldown, bounded radial geometry, inward drawing, raVe isolation and new controls/pet details. Real audio, measured latency and rendered visual quality remain unverified.


## 德牧 theme demo

Shop → Theme bundles → 德牧 → Try demo selects the supplied transparent sunglasses sprite and opens the home preview. An active audio source is preserved; otherwise the synthetic demo starts. The selected pet is remembered. This is a local catalog demo, with no payment or physical-watch image transfer. Keep `shepherd.js` and `assets/shepherd.png` beside `musex.html` when serving the app.

The shared custom-analysis bands and both inside/outside-phone legends are LOW [20, 250) Hz, MID [250, 1000) Hz and HIGH [1000, Nyquist) Hz. Bins belong to one band only. HIGH is not truncated at 10 kHz. raVe retains its isolated original DSP and rendering.

LOW RMS band level drives linear scale up to +18%. Each fresh rise immediately replaces the previous envelope, including a weaker new pulse arriving during release; release is 65 ms with no cooldown or queued pulses. The shepherd uses unsmoothed analyzer snapshots. MID uses 7× sensitivity above a small gate, 6 ms attack / 28 ms release, and a bounded 12 source-pixel displacement of the gold-white alpha contour (roughly 1.5 CSS px on the watch). A soft gold-white glow separates the original sprite from the deep blue night sky. HIGH energy and positive energy flux drive meteor density; its power-weighted spectral centroid controls travel speed. The same normalized meteor field fills the sky and clips to the glasses lens. A meteor train is brightest near the head, dims toward its end, and persists up to 650 ms after its emission before fading completely. The shared state advances once per analysis frame and is rendered without mutation on both canvases. Reduced-motion mode lowers scale, outline motion, particle rate and speed. Stop clears every envelope and particle.

Verification: `node web/tests/shepherd.test.cjs` covers boundaries, silence, band isolation, MID release, meteor speed, reduced motion and decay/reset. Playwright browser checks verified catalog activation, sprite loading, native aspect ratio, lens clipping, shared rendering and stop/reset. Screenshots are in `output/playwright/`. Physical watch output and live microphone timing have not been tested.

## Reference silhouettes and doubled radial spectrum

Wolf now follows the supplied right-facing profile with upright ears, projecting muzzle and neck fur; rabbit follows the long splayed ears and rounded cheeks; bear has compact ears and a broader furred jaw. Existing cat details, bear nose, and the separately added Shepherd module are preserved.

Audio source opens through Choose into a dedicated phone screen with Back returning to Home or Rhythm without interrupting capture/playback. Effects stay in their own panel. Diffusion effect replaces the former Beat bursts label. Ring rotation appears above Pet and defaults clockwise, with counterclockwise and stationary choices.

Each semicircle maps the same 64 logarithmically spaced frequencies from 20 Hz to 20 kHz. Opposite samples are identical; interpolation, local smoothing and a three-sample seam blend keep the closed line continuous. A single shared rotation angle drives phone/watch contours and spikes; changing direction preserves the current angle. raVe remains unchanged.

Syntax and mocked regressions passed for opposite-point identity, all rotation modes, both audio-page return paths, uninterrupted playback state and upstream raVe geometry equality. No actual browser or live-audio visual validation was performed.

The follow-up browser check verifies visible MID contour changes at watch resolution and the night-sky/lens presentation. Regression cases additionally cover interruption of a stronger LOW release by a weaker new signal, trail brightness along its length, post-flight persistence and cleanup. Audio-device latency remains browser/hardware dependent.

## Shepherd low–mid pulse update

LOW and MID now drive uniform whole-sprite scaling. The 20 Hz–1 kHz power-weighted centroid controls the expansion ceiling: lower frequencies give larger pulses, tapering to approximately 5% at the top of MID. Signal strength modulates the extra expansion, with a 25% maximum; active signals above the analysis noise gate retain a 5% minimum. Silence releases back to original size. Reduced motion retains the minimum and reduces expansion above it. New rising pulses replace unfinished releases immediately.

MID scanline/edge deformation and its alpha-boundary extraction are removed. HIGH meteor creation, speed, trails and lens reflections are unchanged. Band analysis adds lowMidHz/lowMidLevel without changing existing band values. Node tests cover monotonic frequency response, expansion bounds, weaker retriggers, silence, HIGH-only isolation and a single intact sprite draw. Hardware and browser visuals were not measured for this change.

## Current Shepherd LOW/HIGH response (supersedes low–mid pulse)

MID animation is temporarily disabled. LOW alone drives a 10–50% whole-sprite pulse, with immediate response to a rising input and a 60 ms release. The resting sprite is 60% of watch diameter so the stronger expansion has room. Reduced-motion settings attenuate it.

LOW/HIGH peak measurements supplement band averages so a narrow spectral signal is not lost among empty bins. Every detected HIGH rise emits a meteor immediately, without waiting for accumulated credit; continuing HIGH activity also emits a rain. New meteors begin visibly inside the watch sky. The 96-item bound replaces the oldest item rather than discarding the newest event. Silence does not emit meteors, and existing tails finish fading. Signals absent from browser audio samples cannot be recovered by the visualizer.

Rendering order is sky, selected custom effect, then Shepherd and meteors. Selecting Shepherd no longer bypasses the selected effect or forces the startup effect to Lines. raVe retains its existing original-only rendering behavior. Tests cover MID isolation, strengthened LOW, weak/repeated/one-frame HIGH signals, spectral peaks, visible launches, bounded event retention, meteor behavior and foreground transparency. Browser appearance and capture timing were not measured.

## Watch-design navigation

Audio source and Watch design are side-by-side entry cards. Watch design opens a separate screen, sharing the live canvas with Home; Back returns to its originating Home/Rhythm screen. Direction, diffusion and rotation live in one initially collapsed Motion settings group. Pet has a bold heading. The existing visual preset selector is relabeled Vibration intensity; its preset behavior remains unchanged.

Verified in the in-app browser over localhost: design navigation, shared preview, custom-effect selection, initially collapsed settings, expansion showing all three controls, bold pet heading, intensity label, and return to Home with side-by-side entries. Shepherd unit tests pass. Backup: musex.before-watch-design.html.

## Current preview and signal rules

The shared preview switches between watch face and live spectrum. Pet selection has its own card, remains enabled with raVe, and is composited above the unchanged raVe renderer. This explicitly supersedes the earlier no-pet raVe restriction.

Shepherd pulse/meteor triggers now have a 100 ms cooldown per detected dominant FFT frequency, separately for LOW and HIGH. A new frequency can trigger while another cools down; analysis and the spectrum remain continuous. Custom bass-triggered diffusion also uses a 100 ms per-peak-frequency cooldown. MID stays inactive for Shepherd. The sprite resting scale is reduced from .60 to .46 of watch diameter. Meteor launch points fill a disk rather than a horizontal top strip, including the lower-right quadrant.

Ring vertices and spike angles remain fixed. Rotation shifts the interpolated frequency samples along those fixed positions instead of rotating the geometry or pet.

Tests pass for cooldown boundary timing, independent frequencies, MID isolation, all four meteor quadrants and transparent sprite overlay. Browser verification confirmed the spectrum toggle, separate pet card and simultaneous raVe/pet display. Live capture timing was not measured.

## Shepherd LOW pulse (current)

Wind deformation is disabled. LOW (20–250 Hz) above the normalized .22 threshold triggers centered sprite scaling of 10–50%, with a smooth 500 ms release after the latest accepted trigger, using the peak as well as band average so isolated bass bins remain visible. MID is inactive; HIGH retains meteors. Triggers use a 100 ms cooldown per band and dominant frequency. Explicit silent RMS suppresses stale FFT data. The .46 resting sprite size and independent raVe/pet layers remain unchanged.

Default custom effect: spikes, inward direction, particle diffusion. Rotation shifts frequency samples at .65 rad/s. Beat color mode changes on strong bass attacks with a 100 ms cooldown, choosing a different palette entry (red/orange initially) or a different RGB value. The palette supports adding/removing colors with at least two distinct entries. raVe retains its own color renderer.

Validation: `node web/tests/shepherd.test.cjs` checks actual LOW draw scaling, MID isolation, HIGH emission, cooldown timing, silence/stop, nonrepeating palette/RGB transitions, raVe isolation, and inline script syntax. Browser checks use simulated audio; physical watch and live capture were not tested.


## October 1 interaction updates

Mood selection toggles off when repeated. The usage card is on the Modes screen; new rhythm time accrues only with playing audio, enabled/synced haptic feedback, RMS > .002 and preview envelope > .05. This measures active haptic signal time, not hardware-confirmed motor runtime. Historical values are retained. Suspended frame gaps are capped at 100 ms.

Breathing now includes per-second accents in inhale/hold/exhale/rest; inhale and exhale have different continuous amplitude envelopes. Firmware still determines physical motor carrier frequency. Browser vibration approximates these phases with pulse patterns.

Custom backgrounds support fixed/RGB/uploaded local images, brightness, and per-ring paired backgrounds in bass color mode (black/white by default). Uploaded images stay local for this session. raVe is unchanged. Guest mode and login/registration forms are present; account submit is deliberately unavailable until an authentication backend is connected. No password is stored or transmitted.


## Shared bass trigger and theme components (latest)

Device audio is selected on load; capture still requires Start and browser consent. `BassTrigger` reads only the 20–100 Hz FFT bins (`bass100`, `bass100Peak`), detects rising energy with the former color-mode onset formula, and enforces one global 150 ms cooldown. The one accepted event drives Shepherd scaling and bass-color changes. Pet triggering stays enabled for RGB/fixed colors; its release remains exactly 500 ms. Ordinary LOW/MID/HIGH analysis, custom diffusion and motor carrier are unchanged. FFT bin resolution still limits separation of tones near 100 Hz.

Meteor drawing is an independent background component behind custom effects/pets. It uses black with white meteors by default, switches to black meteors on light paired backgrounds, and works without the Shepherd pet. The bundled background is in My backgrounds; system backgrounds retain solid/RGB/image options. The Shepherd theme resets builtin visual defaults, spikes/inward/particles/clockwise, then selects Shepherd plus meteors. Store items open a local animated simulated preview; only Use applies them. Current bundled items are free; no payment backend or purchase transaction is simulated. raVe preview uses a separate renderer and restores shared sample arrays.

Tests cover FFT band boundaries, 150 ms timing, shared events in all color modes, 500 ms release, high-band meteor emission and background contrast. Browser QA covers default source, preview/use flow and theme application. Live-capture spectral leakage and hardware output are not measured.


## raVe-style custom bass detection (current)

Custom motion, diffusion bursts, bass colors and Shepherd scaling now share raVe's detector: read the existing isolated 100 Hz low-pass analyzer into a separate 2,048-sample float buffer, take its absolute peak, and compare against a zero-initialized 15-frame rolling mean at raVe's 60 Hz cadence. A spike requires peak > 0.3 and peak > 1.3 × mean (including the current frame). These are raVe's original checks; the former custom LOW level/rise gates, adaptive floor, RMS veto and 100/150 ms bass cooldowns are removed. Consecutive qualifying analysis frames can each trigger; this is raVe's spike flag, not instrument classification or a once-per-note detector.

Continuous bass motion also uses the filtered waveform peak. Custom gain/bass controls scale the visual response, not detection; microphone sensitivity still applies to the separate full-band spectrum. LOW/MID/HIGH spectrum displays retain their existing frequency bands. HIGH meteor detection, full-band silence handling, Shepherd's 500 ms release, and the original raVe renderer remain unchanged. The store pet preview uses an independent instance of the same detector.

Validation: `node web/tests/shepherd.test.cjs` passes reference-equation parity, sustained bass, negative waveform peaks, close attacks, reset, 120 Hz sampling cadence, shared consumer wiring, HIGH emission, release, backgrounds and inline script syntax checks. Original raVe audio/render/update code was compared unchanged. Live audio and physical hardware have not been verified.
