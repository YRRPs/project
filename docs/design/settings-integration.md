# Pulse settings integration

## Phase 1: prototype controls

Keep SystemUI prototype independent from settings applications. `PulseSettingsRepository` owns three private, per-user `Settings.Secure` keys:

```text
lineage_pulse_enabled    default 0
lineage_pulse_color      default 0xFFFFFF
lineage_pulse_height_dp  default 48, clamp 8–96
```

Prototype controls:

```bash
adb shell settings put secure lineage_pulse_enabled 1
adb shell settings put secure lineage_pulse_color 16777215
adb shell settings put secure lineage_pulse_height_dp 48
```

Disable and reset optional values:

```bash
adb shell settings put secure lineage_pulse_enabled 0
adb shell settings delete secure lineage_pulse_color
adb shell settings delete secure lineage_pulse_height_dp
```

`PulseSettingsRepository` observes current-user values and emits typed state. Pulse keys remain private implementation details and do not enter public `android.provider.Settings` API.

## Phase 2: durable settings UI

Shipped as a YRRPs page in a forked LineageOS Settings, with `lineage_pulse_alpha` (default 217, clamp 26–255) added. See `yrrp-settings-ui.md` for the final layout, contract and release record. The options below are the original analysis.

Options:

1. Dedicated customization app from `vendor/<rom>`
   - Avoids modifying upstream Settings.
   - Owns future ROM-specific settings.
   - Requires privileged secure-setting access and deliberate navigation entry point.

2. LineageParts fork
   - Uses native Lineage customization location.
   - Adds another upstream fork and merge surface.

3. Main Settings fork
   - Provides deepest platform integration.
   - Creates largest maintenance burden; avoid for first feature.

Recommendation: use dedicated customization app if YRRP adds more ROM features. Use LineageParts only if Pulse remains sole customization and native placement outweighs fork cost.

## Settings behavior

Initial UI controls must match implemented repository state:

- Enable Pulse
- Fixed RGB color
- Overlay height, 8–96dp

Deferred controls require corresponding SystemUI implementation first:

- Solid or fading renderer
- Bar count and rounded bars
- Artwork-derived color
- Attack/release smoothing
- Silence timeout
- Lock-screen visibility
- Ambient visibility, default off
- Optional bass haptics

Settings remain per-user. Invalid values fall back to safe defaults. Disabling feature releases Visualizer immediately.

## Privacy copy

Settings UI should state that Pulse processes local playback spectrum in memory. It stores and transmits no audio. Lock-screen and ambient modes require separate opt-in controls if implemented.
