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
