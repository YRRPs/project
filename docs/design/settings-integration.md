# Pulse settings integration

## Phase 1: prototype controls

Keep first SystemUI experiment independent from any settings application. Use private secure-setting strings owned by Pulse code:

```text
pulse_enabled
pulse_style
pulse_color
pulse_bar_count
pulse_rounded_bars
```

Prototype control:

```bash
adb shell settings put secure pulse_enabled 1
adb shell settings put secure pulse_style solid
```

`PulseSettingsRepository` observes values for current user and emits typed state. Do not add Pulse constants to public `android.provider.Settings` API.

## Phase 2: durable settings UI

Options:

1. Dedicated customization app from `vendor/<rom>`
   - Avoids modifying upstream Settings.
   - Owns all future ROM-specific settings.
   - Requires privileged secure-setting access and deliberate navigation entry point.

2. LineageParts fork
   - Native Lineage customization location.
   - Adds another upstream fork and merge surface.

3. Main Settings fork
   - Deepest platform integration.
   - Largest maintenance burden; avoid for first feature.

Recommendation: start with dedicated customization app if more ROM features are expected. Use LineageParts only if Pulse remains sole customization and native placement matters more than fork count.

## Settings behavior

Minimum controls:

- Enable Pulse
- Renderer: solid or fading
- Fixed color
- Bar count
- Rounded bars

Later controls:

- Artwork-derived color
- Attack/release smoothing
- Silence timeout
- Lock-screen visibility
- Ambient visibility, default off
- Optional bass haptics

Settings must be per-user. Invalid values fall back to safe defaults. Feature disabled state must release Visualizer immediately.

## Privacy copy

Settings UI should state that Pulse processes local playback spectrum in memory. It stores and transmits no audio. Lock-screen and ambient display modes need separate opt-in controls.
