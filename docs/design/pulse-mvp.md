# Pulse MVP design

## Goal

Render audio spectrum at physical screen bottom on LineageOS 23.2 without destabilizing SystemUI, intercepting input, or changing audio routing.

## Implemented scope

- Unlocked and entered primary display only
- Physical display bottom in portrait and landscape
- Separate trusted, non-touchable SystemUI window
- Hides with navigation during immersive mode
- Hides during screen pinning and Battery Saver
- Output-mix `Visualizer(0)` FFT capture
- 32 solid bars
- Configurable fixed RGB color
- White default at 85% opacity
- Configurable 8–96dp height; 48dp default
- Per-user private secure settings
- Default off

Deferred:

- Fading blocks
- Lock screen and AOD
- Artwork-derived color
- Haptics
- User-facing settings UI
- Taskbar and large screens
- Secondary displays
- Per-player Visualizer sessions
- Hardware-only acceptance

## Source branch

```text
Repository: frameworks/base
Branch: pulse-mvp
Base: LineageOS lineage-23.2 @ 1c45e31a86be
Head: 36269fa52cee
```

Commits:

```text
72ffff77eaab  SystemUI: add Pulse state inputs and spectrum model
998ae4f20d69  SystemUI: add Pulse FFT capture lifecycle
71e6323b75ee  SystemUI: add Pulse bottom overlay
e5f466525b4d  SystemUI: coordinate Pulse runtime state
36269fa52cee  SystemUI: integrate Pulse with navigation lifecycle
```

Patch backup: `patches/frameworks-base/pulse-mvp/`.

## Components

### PulseSettingsRepository

Current-user configuration:

```text
lineage_pulse_enabled    default 0
lineage_pulse_color      default 0xFFFFFF
lineage_pulse_height_dp  default 48, clamp 8–96
```

### PulsePlaybackRepository

Uses `AudioManager.AudioPlaybackCallback`. Activates for active media, game, or unknown usage. Excludes remote-submix-only playback and non-media usages. Stores no media identity.

### VisualizerPulseAudioCapture

Sole native Visualizer owner:

- Session `0`
- Capture size 512
- FFT only
- 25Hz capped to platform maximum
- Configure disabled, enable last
- Deterministic disable/release on stop or error

### PulseSpectrumProcessor

Interprets platform FFT bytes into 32 logarithmic bars. Uses noise floor, attack `0.55`, decay `0.20`, clamping, and retained output buffer.

### PulseView

Draws solid bottom-up bars. Keeps Paint, data, and geometry arrays. Copies incoming levels and allocates nothing during `onDraw`.

### PulseWindowController

Owns `TYPE_NAVIGATION_BAR_PANEL` overlay at physical bottom. Window is trusted, non-focusable, non-touchable, cutout-aware, and exists only while Pulse runs.

### PulseController

Owns one eligibility predicate:

```text
attached
&& primary display
&& enabled
&& navigation window visible
&& aggregate visibility
&& device entered
&& awake
&& eligible playback
&& !Battery Saver
&& !screen pinning
```

Every false transition releases capture and removes window. Activation errors latch until eligibility resets, preventing retry loops.

## Color modes (issue #4)

`lineage_pulse_color_mode` selects how `PulseController` colors the bars. `0` or any unknown value is Solid: the stored RGB. `1` is Match theme: white with the system dark theme on, black with it off. `lineage_pulse_alpha` applies in both modes.

`PulseThemeRepository` maps the `UI_MODE_NIGHT` bit from the `@Main` `ConfigurationInteractor.configurationValues` flow, which emits the current configuration on collection and then every change. Night mode is appearance only: it is not an eligibility input, so a theme change calls `setColor` on the shown overlay and leaves `captureEpoch` unchanged. The dump adds `colorMode=`, `nightMode=` and `effectiveColor=#AARRGGBB`; `PulseLog` records `colorMode=` and `nightMode=` transitions.

```bash
adb shell settings put secure lineage_pulse_color_mode 1   # Match theme
adb shell cmd uimode night yes                             # bars turn white
adb shell cmd uimode night no                              # bars turn black
adb shell settings delete secure lineage_pulse_color_mode  # back to Solid
```

## Controls

```bash
adb shell settings put secure lineage_pulse_enabled 1
adb shell settings put secure lineage_pulse_color 16777215
adb shell settings put secure lineage_pulse_height_dp 48
```

Disable/reset:

```bash
adb shell settings put secure lineage_pulse_enabled 0
adb shell settings delete secure lineage_pulse_color
adb shell settings delete secure lineage_pulse_height_dp
```

## Verification completed

- `SystemUI-core` builds.
- Full `SystemUI` APK builds with Dagger integration.
- Pulse classes confirmed inside packaged `SystemUI.apk`.
- Full `brunch salami` completed successfully.
- ROM artifact:

```text
lineage-23.2-20261004-UNOFFICIAL-salami.zip
Size: 2,180,180,787 bytes
SHA-256: 0f66af474c6ac68ad21372dd473313e9e7b8468973f4a42efd8211e1cf0aeb62
```

- `frameworks/base` working tree is clean and five commits ahead.
- Shared Ravenwood/Robolectric test targets have unrelated baseline fixture compilation failures. Pulse test sources compile without Pulse-specific errors.
- `PulseSpectrumProcessorTest` passes standalone.

## Hardware verification pending

- OnePlus audio stack support for `Visualizer(0)`
- Speaker, Bluetooth, USB, cast, offload, and protected playback
- Overlay z-order against gesture handle/buttons/IME/cutouts
- Physical-bottom placement across rotations
- Real frame pacing, allocations, jank, CPU, thermal, and battery cost
- SystemUI/audioserver restart behavior

Do not start fading renderer until capture, lifecycle, overlay, and solid rendering pass salami hardware validation.

## Hardware-ready core follow-up (2026-10-06)

The MVP's NavigationBar-owned lifecycle and output-mix-only capture are superseded. On salami, Launcher3 Taskbar hosts navigation in gesture and three-button modes, so `NavigationBar` never attached the MVP controller and Pulse never ran on device.

Design: `docs/superpowers/specs/2026-10-05-pulse-hardware-ready-core-design.md` (local, ignored).

Branch `pulse-hardware-ready-core` on the builder (`/opt/android/frameworks/base`), based on `36269fa52cee`, not pushed:

```text
94a8a02e8388  SystemUI: add Pulse host state repository
1d9ab3f1b9df  SystemUI: select Pulse playback sessions
e1efd21a88a2  SystemUI: add Pulse capture session fallback
c59f1a3b4fe7  SystemUI: harden Pulse playback target privacy
7a7487e178c4  SystemUI: harden Pulse capture fallback failures
a0f077a3b6ad  SystemUI: gate Pulse on valid FFT frames
0fa97d4cb742  SystemUI: move Pulse runtime to display lifecycle
819d3909071a  SystemUI: bind Pulse in display component
7023e21a0b25  SystemUI: publish Pulse navigation host state
ba7922a6a20f  SystemUI: implement Pulse host repository in display fixtures
c5de4d6b90d6  SystemUI: harden Pulse runtime teardown
50caaafe4118  SystemUI: harden Pulse host activation
```

Changes:

- `PulseController` is a default-display `SystemUIDisplaySubcomponent` lifecycle listener.
- NavigationBar and Taskbar publish source-tagged host state into a per-display repository; `NavigationBarControllerImpl` keeps the host `NONE` during replacement init, then activates and republishes.
- Playback selection keeps a stable player and prefers a positive session; capture tries that session, then falls back once to session 0.
- The overlay appears only after three valid FFT frames; 1 s startup and 2 s silence timeouts fail closed and latch until an input changes.

### Verified (automated and build)

- Plain-JVM Pulse suites ran green (controller, host repository, store, playback, capture, frame gate, spectrum): 98 tests.
- Android-runner tests (`PulseWindowControllerTest`, `NavigationBarTest`, `TaskbarDelegateTest`, `NavigationBarControllerImplTest`) compile but did not run: `SystemUI-tests` fails on unrelated pre-existing fixtures (screen capture/record, status bar events, battery, Wi-Fi, UDFPS, shade header).
- `m SystemUI` and `brunch salami` pass. Packaged `SystemUI.apk` contains `PulseController`, `PulseFrameGate`, `PulseHostStateRepositoryStore`, and `PulsePlaybackTarget`.

### Signed release `20261006-015450`

```text
OTA:          lineage-23.2-salami-20261006-015450-signed-ota.zip
Size:         2,180,308,324 bytes
SHA-256:      7fb1b607128a968b7a4d554d51b0f0d10e38a3813d6e3245c37acf10be0725c9
Target-files: lineage-23.2-salami-20261006-015450-signed-target_files.zip
SHA-256:      4e489341a57931b263c836aa49d32b5988278462514aafa15280f55b23b7dab5
Framework:    50caaafe41186f89d0a6e57d04d623383b770f09
URL:          https://ota.yimura.dev/install/salami/20261006-015450/
```

OTA certificate matches `releasekey`; SystemUI signer matches `platform`.

### Hardware acceptance pending

Not yet observed on device: speaker and Bluetooth playback, gesture/three-button transitions, every teardown gate, offloaded/protected playback, SystemUI and audioserver restart recovery, frame rate, and allocations. Watch items:

- Visualizer setup runs on the SystemUI main thread; check for jank or ANR during audioserver restart.
- A session-specific capture that delivers only silent frames latches Pulse off after the 1 s startup timeout (no session-0 retry). Look for `Pulse stopped: startup timeout` while audio plays.

### Device-entry fix and observability (release `20261006-075443`)

Release `20261006-015450` installed but Pulse never ran. The controller gated on `DeviceEntryInteractor.isDeviceEntered`, which only emits with `SceneContainerFlag` enabled; on salami it is disabled, so the gate stayed `false`.

```text
29fa0b70d414  SystemUI: gate Pulse on keyguard-gone without scene container
bb07b63193ca  SystemUI: make Pulse decisions observable
```

- The gate now uses `KeyguardTransitionInteractor.isFinishedIn(Scenes.Gone, KeyguardState.GONE)`.
- `PulseController` is a Dumpable and logs transitions to the `PulseLog` buffer. Read both with:

```bash
adb shell dumpsys activity service com.android.systemui/.SystemUIService PulseController PulseLog
```

- `vendor/extra` (`Yim-s-Riced-ROM-Project/android_vendor_extra`) sets `lineage.updater.uri=https://ota.yimura.dev/updates/{device}.json`.

```text
OTA:          lineage-23.2-salami-20261006-075443-signed-ota.zip
Size:         2,180,311,543 bytes
SHA-256:      de6ad8082a99ac8dcfb969fe6c8a966ead5b95bcaab96ba95e43d68f9afd4fd6
Target-files: 12baaad9253bb9067ed791ec609f3809e626ef8ef5288b679e96480c3f647fa0
Framework:    bb07b63193ca
```

Observed on salami (2026-10-06): Pulse renders during Spotify speaker playback with gesture navigation; Taskbar host; `lineage.updater.uri` present after the update.

```text
activeHost=TASKBAR  navigationVisible=true  keyguardGone=true  awake=true
playbackActive=true eligible=true blockedBy=none captureActive=true
frameGateReady=true overlayShown=true windowAttached=true
Window{… Pulse0}
```

Still pending: Bluetooth, three-button navigation, every teardown gate, offloaded/protected playback, SystemUI and audioserver restart recovery, frame rate, allocations.
