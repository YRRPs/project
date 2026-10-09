# YRRPs Settings UI

The YRRPs page in LineageOS Settings controls Pulse and the screen-off animation. It shipped in OTA `20261007-083337` and passed device acceptance on 2026-10-07.

## Layout

- Settings home page: a **YRRPs** tile in its own group at the top. The group order is `-150` in the standard layout (before Account at `-140`) and `-140` in the expressive layout (before Connectivity at `-130`). The tile highlight key is `yrrp_menu_key`, so System keeps `menu_key_system`.
- YRRPs hub (`YrrpSettings`, root key `yrrp_settings_screen`):
  - **Audio** → **Pulse** (On/Off), opening `YrrpPulseSettings`.
  - **Animations** → **Screen-off animation** (Stock/CRT), opening `YrrpScreenOffAnimationSettings`.
  - Category headers are `searchable="false"`.
- Pulse page (`yrrp_pulse_settings_screen`): intro, **Use Pulse** main switch, **Color mode** category (non-searchable) with **Solid**, **Match theme**, **Rainbow gradient** and **Rainbow cycle** radio rows, **Pulse color** (RGB picker), **Pulse opacity** slider, **Pulse height** slider, privacy footer. Every control below the switch stays visible but is disabled while Pulse is off. **Pulse color** is also disabled outside Solid; opacity applies in every mode.
- Screen-off animation page (`yrrp_screen_off_animation_settings_screen`): **Stock** and **CRT** radio rows.

All three fragments are in `SettingsGateway.ENTRY_FRAGMENTS` and are `@SearchIndexable`. Code lives in `com.android.settings.yrrp`; resources use the `yrrp_` prefix.

## Setting contract

Private per-user `Settings.Secure` keys, shared with SystemUI:

| Key | Default | Read | Write |
|---|---|---|---|
| `lineage_pulse_enabled` | 0 | non-zero is on | 1 or 0 |
| `lineage_pulse_color` | `0xFFFFFF` | low 24 bits | low 24 bits |
| `lineage_pulse_alpha` | 217 (85%) | clamp 26–255 | clamp 26–255 |
| `lineage_pulse_height_dp` | 48 | clamp 8–96 | clamp, then snap to 8 + 4n |
| `lineage_pulse_color_mode` | 0 (Solid) | 1 Match theme, 2 Rainbow gradient, 3 Rainbow cycle; other values Solid | 0-3, other values rejected |
| `lineage_screen_off_animation` | 0 | only 1 is CRT | 0 or 1, other values rejected |

Settings never rewrites a stored value on read. The height slider thumb sits on the nearest 4 dp step, while the row text shows the exact stored value, because the Material slider throws on off-step values. The color row and picker show `#RRGGBB`; confirming the picker writes only the color, then re-reads it. The opacity slider covers 26–255 in steps of 1 and shows a rounded percentage of 255.

Match theme draws white bars when the system dark theme is on and black bars when it is off. SystemUI reads the `UI_MODE_NIGHT` bit of the global configuration, so manual and scheduled dark theme both apply live; app content behind the bars is not sampled. Rainbow gradient gives each bar its own hue and drifts the hues one full turn every 12 s. Rainbow cycle colors every bar with one hue that cycles every 6 s. A later color mode takes value 4 or higher; until then SystemUI and Settings both read such a value as Solid.

The default alpha of 217 matches the fixed alpha SystemUI used before the setting existed. CRT requires Always-on display; see `crt-screen-off-animation.md`.

## Ownership

- `YRRPs/android_packages_apps_Settings` owns YRRPs navigation, pages, controllers,
  search integration, and `YrrpSettingsStore`.
- Runtime feature owners consume private per-user `Settings.Secure` values.
- Settings never restarts SystemUI or drives feature lifecycle directly.

## Navigation model

```text
Settings home
└── YRRPs tile in its own top group
    └── YRRPs hub
        ├── Audio
        │   └── Pulse → Pulse page
        └── Animations
            └── Screen-off animation → radio page
```

Use one category per domain, one row per feature, and one focused feature page.
Add another nesting level only when a category becomes crowded and after user approval.

## Page and controller ownership

- Dashboard fragments declare XML, metrics, search provider, and controllers.
- Controllers own UI state, validation, observation, and writes.
- `YrrpSecureSettingObserver` owns lifecycle-scoped refresh.
- `YrrpSettingsStore` owns private keys, defaults, normalization, and current-user I/O.

## Search architecture

- Every page has a non-empty root key, `@SearchIndexable`, a provider, and a
  `SettingsGateway.ENTRY_FRAGMENTS` entry.
- Hub-row title and target-page title use the same string resource.
- Categories, top intros, and footers are non-searchable.
- Keywords live on hub entries and main controls when synonyms matter.
- Homepage highlight keys are unique within each homepage XML.
- Duplicate hub-row and page-header results are acceptable when both route correctly.

## Extension rules

1. Show current and proposed ASCII hierarchy before restructuring.
2. Add a private per-user secure-setting contract before UI code.
3. Normalize reads without rewriting; constrain writes.
4. Keep Settings and runtime-owner defaults and clamps identical.
5. Add a new key for a new dimension instead of packing bits.
6. Preserve old visual behavior as the default when exposing a fixed value.
7. Add runtime dump state before device acceptance when behavior changes.

## Decision log

- **2026-10-07 — Top-level ownership:** YRRPs uses its own homepage group.
- **2026-10-07 — Hub taxonomy:** feature rows sit under domain categories.
- **2026-10-07 — Feature pages:** each feature gets one page; no third level yet.
- **2026-10-07 — Choice widgets:** finite choices use radio pages, not `ListPreference`.
- **2026-10-07 — Pulse opacity:** separate key, default 217, preserves old appearance.
- **2026-10-07 — Height display:** exact stored text with an on-grid slider thumb.
- **2026-10-08 — Pulse color mode:** inline radio rows on the Pulse page, no third level. Solid stays the default.
- **2026-10-08 — Opacity row:** opacity moved out of the color picker into its own slider, because it applies in every color mode while the RGB color applies only in Solid.
- **2026-10-07 — Accessibility scope:** TalkBack-specific acceptance is excluded by user preference.

## Release

| Item | Value |
|---|---|
| Manifest `YRRPs/android` | `fa59f37f25602c100eab10ec243055b229070467` |
| Settings `YRRPs/android_packages_apps_Settings` | `50a9cea0744b3efd4007a4186dc4992b21f22fdb` |
| frameworks `YRRPs/android_frameworks_base` | `16467d2f51e953bf659d49fbf069161c006586d4` |
| vendor/extra | `1aa802bd66d2bab965cdce2ca66c02c00ca71fef` |
| project | `9f09bd3286e0c7349034dba7fb1b191bc4dfe553` |
| OTA build ID | `20261007-083337` |
| Signed OTA SHA-256 | `9ef0638fd66cc0ff89cee8a67465dcee9092af5c896ae2a03d7208591cb9f0c6` (2180367704 bytes) |
| Signed target-files SHA-256 | `8a204456b84bdd3be7820b8b8e29c0a29b9a1718eb55a735eeef773a0fb369b2` |
| Installed `ro.build.date.utc` | `1791361628` |

Settings.apk and SystemUI.apk are signed with the platform certificate (SHA-256 `A8:7F:67:F2:…:06:8B:03`).

The Settings switch replaced `LineageOS/android_packages_apps_Settings` through the manifest override. The builder checkout was force-synced for `packages/apps/Settings` only, after a bundle backup at `/opt/android/.backups/settings-20261006-140029.bundle`.

## Verification

### Builds

- `m Settings` and `m SettingsRoboTests`: success, 0 `error:` lines, at `50a9cea`.
- `m SystemUI`: success at `16467d2`.
- Full product build: the signed-OTA pipeline (`mka target-files-package otatools`) finished `complete`.

### Tests

- Settings: 135 Robolectric tests in 12 classes under `tests/robotests/src/com/android/settings/yrrp/` compile. **Robolectric executed 0 of them.** Every Robolectric test in this tree fails at startup:

  ```text
  java.lang.IllegalStateException: Failed to create system AssetManager
  Caused by: java.io.IOException: Failed to load asset path /system/framework/org.lineageos.platform-res.apk
  ```

  Lineage's `frameworks/base/core/java/android/content/res/AssetManager.java:295` loads that APK unconditionally. The user chose not to change it.
- The 23 `YrrpSettingsStoreTest` tests also ran under plain JUnit outside Robolectric: `OK (23 tests)`. This is supplemental evidence only.
- `ChooseLockPatternTest` and `ConfirmLockPatternTest` were updated for the pattern-size API so `SettingsRoboTests` compiles.
- SystemUI Pulse tests compile. They do not run: `SystemUiRoboTests` fails on 29 errors in unrelated Lineage test fixtures. `PulseHostStateRepositoryTest.kt` was fixed to use `java.lang.IllegalArgumentException::class`.

### Device

All results below are from builds `20261006-145431`, `20261006-171251`, `20261006-210054` and `20261007-083337`:

- Home page tile, hub, Pulse and screen-off pages render; back navigation returns correctly.
- Search: "YRRPs" opens the hub; "Pulse", "opacity", "Use Pulse", "Pulse color" and "Pulse height" open the Pulse page; "CRT", "Stock" and "Screen-off animation" open the screen-off page. "Audio" and "Animations" no longer list the category headers.
- External `adb shell settings put` refreshes open pages. Raw `7`, `99`, `0`, `10`, `50` and `200` display normalized and stay stored as written.
- Pulse with playback: turning it off releases capture and removes the overlay; changing alpha 141 → 64 → 200 → 255 keeps `captureEpoch` unchanged.
- SystemUI dump `alpha=` follows 26, 128 and 255, and shows 217 when unset.
- CRT: each screen-off uses the current setting; a change during a transition applies to the next one.
- Color picker (before #4, when it also held opacity): Cancel, back and outside tap write nothing; OK writes both values; rotation keeps an unconfirmed color; a double tap opens one picker; landscape scrolls to every slider.
- Height: a fast drag writes only 8 + 4n values, with 0 janky frames.
- A temporary secondary user (user 10) saw only its own values; it was removed afterwards.

### Pending device verification for #4 (Match theme, opacity row)

- Picker OK writes only `lineage_pulse_color`; the swatch and row show `#RRGGBB`.
- Search for "color mode", "Match theme" and "Pulse opacity" opens the Pulse page.
- **Pulse color** is disabled in Match theme and re-enables in Solid; **Pulse opacity** stays enabled in both while Pulse is on.
- `adb shell cmd uimode night yes` and `no` recolor the bars live, and the dump keeps `captureEpoch` unchanged.
- Scheduled dark theme recolors the bars at the transition.

### Pending device verification for #3 (Rainbow modes)

- Search for "rainbow" opens the Pulse page; the **Rainbow gradient** and **Rainbow cycle** rows check and write 2 and 3.
- **Pulse color** is disabled in both Rainbow modes; **Pulse opacity** applies in both.
- Switching between modes while Pulse shows keeps `captureEpoch` unchanged.

Evidence for Tasks 7 and 9 is in `docs/superpowers/evidence/` (not tracked).

## Not covered

- Standard (non-expressive) home page layout: the device uses the expressive layout. The standard XML is covered by structure tests only.
- Accessibility and TalkBack: out of scope by user decision.
- Robolectric execution of the Settings and SystemUI tests (see Tests).
