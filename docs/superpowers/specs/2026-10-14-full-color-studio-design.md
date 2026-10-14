# Full color studio design

**Date:** 2026-10-14  
**Status:** Approved design

## Summary

YRRPs adds a full color studio to **Wallpaper & style**. Users can build named local themes from primary, secondary, tertiary, and neutral seed colors. One theme generates linked light and dark palettes by default; advanced users can unlock separate dark-mode seeds.

The studio renders representative Material components before applying a theme. It warns about weak contrast but lets the user continue after explicit confirmation. Existing wallpaper-derived choices and shipped presets remain available.

## Context

Current LineageOS ThemePicker offers wallpaper-derived colors and fixed preset bundles. It already writes the per-user `theme_customization_overlay_packages` secure JSON setting. SystemUI observes that setting, generates fabricated overlays, and applies them without restarting either process.

Current UI does not let users enter arbitrary multi-seed global palettes or keep named custom themes. This feature extends the existing pipeline instead of adding a separate YRRPs Settings page.

## Goals

- Let users edit primary, secondary, tertiary, and neutral seed families.
- Generate coherent Material color roles from those seeds.
- Generate linked light and dark palettes by default.
- Let advanced users configure separate dark-mode seeds.
- Preview representative Material components before system-wide apply.
- Warn about weak contrast without blocking deliberate choices.
- Save, rename, duplicate, delete, and apply named local themes.
- Keep existing wallpaper colors and presets unchanged.
- Apply a custom theme without restarting ThemePicker or SystemUI.
- Explain runtime decisions through bounded logs and a SystemUI dump.

## Non-goals

- Importing or exporting theme files.
- Cloud sync or cross-device backup.
- Editing every Material color role separately.
- Changing fonts, icon shapes, status bar icon styles, or themed icons.
- Replacing wallpaper-derived colors or shipped presets.
- Adding a duplicate page under YRRPs Settings.
- TalkBack-specific acceptance testing.

## User experience

### Color page

The ThemePicker color page keeps three peer sections:

```text
Wallpaper colors
Presets
Custom
├── saved custom theme cards
└── Create theme
```

A saved-theme card previews its four seed families and exposes edit, duplicate, rename, delete, and apply actions. A new installation shows an empty Custom section with a short explanation and **Create theme**.

### Progressive studio

The editor shows:

1. Theme name.
2. Primary, secondary, tertiary, and neutral seed controls.
3. Palette style.
4. **Separate dark palette**, off by default.
5. Side-by-side light and dark component previews.
6. **Save** and **Apply** actions.

Custom themes support Tonal Spot, Vibrant, Expressive, Spritz, Rainbow, and Fruit Salad. Monochromatic remains available for existing wallpaper and preset choices but is excluded here because it deliberately removes seed hue and chroma.

Turning on **Separate dark palette** reveals four dark-mode seed controls. Turning it off restores generated dark colors from the light inputs after confirmation when separate values would be discarded.

Each seed control supports a visual picker, hue/saturation/value sliders, and exact `#RRGGBB` input. Invalid text stays in the field with an error and never updates the preview.

**Save** validates and stores the editor state without changing the active system theme. **Apply** validates, saves the current editor state, then writes the complete active theme contract. Back navigation with unsaved changes asks whether to discard them.

### Rendered preview

The preview updates locally after every valid edit. It never changes system overlays before **Apply**.

Light and dark panes render representative components and role relationships:

- top app bar;
- surface card and body text;
- filled and outlined buttons;
- secondary chip;
- tertiary switch;
- text field and outline;
- floating action button;
- selected and unselected states;
- fixed Material error roles; and
- a preview-only amber warning treatment.

Material exposes system error roles but no standard warning role consumed by ordinary apps. The amber warning treatment explains contrast problems inside the studio and carries no runtime-parity claim.

The preview uses the same palette-generation library, seeds, style, mode, and current system contrast level as SystemUI. It does not approximate applied colors with hand-authored swatches.

Contrast checks inspect rendered foreground/background role pairs rather than raw seed pairs. A warning identifies each affected preview component. **Apply** opens an **Apply anyway** confirmation when warnings remain. The confirmation does not weaken validation of malformed values.

### Named local themes

Each theme has a stable UUID and a user-visible name. Names trim surrounding whitespace, contain 1–64 characters, and are unique within the current Android user. Duplicate creates a new UUID and a unique `Copy` name.

Deleting the active theme removes its saved definition but does not change currently applied overlays. The confirmation states that colors remain active until another theme is selected. Active payloads are self-contained, so SystemUI never depends on a saved-theme lookup.

Themes are per-user and survive reboot, ThemePicker app-data clearing, package updates, and OTA updates because SettingsProvider owns them. Backup explicitly denies this key. Factory reset removes themes, and version 1 does not export, import, cloud-sync, or transfer them.

## Architecture

### Repository ownership

- `YRRPs/android_packages_apps_ThemePicker` owns editor UI, rendered preview, contrast warnings, saved-theme management, and active contract writes.
- `YRRPs/android_frameworks_libs_systemui` owns shared multi-seed palette generation and active-contract parsing used by ThemePicker and SystemUI.
- `YRRPs/android_frameworks_base` owns secure-setting constants, runtime validation, fabricated-overlay generation, application, logs, and dump state.
- `YRRPs/android` selects new ThemePicker and color-library forks through narrow manifest overrides.
- `YRRPs/project` owns this design, workflow checks, and release proof boundaries.

The manifest must continue using upstream Lineage projects for unrelated repositories.

### Component boundaries

`CustomThemeDefinition` models stored editor state. It validates names, schema versions, styles, linkage, and complete RGB seed sets.

`CustomThemePaletteGenerator` accepts validated seeds, linkage mode, and style. It returns immutable light and dark Material role maps. Both ThemePicker preview and SystemUI overlays consume this API.

`CustomThemeRepository` performs current-user reads and atomic writes for named themes. ThemePicker remains its only writer.

`CustomThemeContract` serializes and parses active JSON fields. ThemePicker and SystemUI share field names, schema rules, and normalization tests.

`ThemeOverlayController` remains runtime orchestrator. It decides whether active source is wallpaper, preset, or custom, then delegates custom generation to `CustomThemePaletteGenerator`.

No ThemePicker component restarts SystemUI or drives overlay lifecycle directly.

## Persistence contracts

### Saved themes

A new private per-user `Settings.Secure` key, `theme_customization_saved_themes`, stores one versioned JSON document:

```json
{
  "version": 1,
  "themes": [
    {
      "id": "stable-uuid",
      "name": "Aurora",
      "style": "VIBRANT",
      "separate_dark": false,
      "light": {
        "primary": "B69CFF",
        "secondary": "75D5C7",
        "tertiary": "FF91B8",
        "neutral": "8C8791"
      },
      "dark": null
    }
  ]
}
```

Stored colors use six uppercase hexadecimal RGB digits without alpha. Writers replace the complete document atomically. Readers never rewrite data while normalizing it.

Unknown top-level or theme fields are ignored for forward compatibility. Missing required fields, duplicate UUIDs, duplicate normalized names, unknown versions, invalid styles, and malformed colors reject the complete write. An unknown document version leaves existing themes untouched and shows a non-destructive error.

### Active theme

ThemePicker continues writing `theme_customization_overlay_packages`. Wallpaper and preset payloads retain existing fields and behavior. A custom payload adds a namespaced, versioned custom object and sets source to `custom_theme`:

```json
{
  "android.theme.customization.color_source": "custom_theme",
  "android.theme.customization.theme_style": "VIBRANT",
  "android.theme.customization.custom_theme": {
    "version": 1,
    "id": "stable-uuid",
    "separate_dark": false,
    "light": {
      "primary": "B69CFF",
      "secondary": "75D5C7",
      "tertiary": "FF91B8",
      "neutral": "8C8791"
    },
    "dark": null
  }
}
```

The active payload embeds every runtime input. It excludes user-visible theme name. SystemUI does not read `theme_customization_saved_themes`.

## Runtime data flow

1. User edits valid values.
2. ThemePicker calls shared generator and updates local preview.
3. ThemePicker evaluates rendered role-pair contrast.
4. User saves, or confirms **Apply anyway** when warnings exist.
5. ThemePicker saves current definition.
6. ThemePicker atomically replaces active customization JSON.
7. `ThemeOverlayController` observes the current user's setting.
8. Controller parses and validates complete custom payload.
9. Shared generator returns light and dark role maps.
10. SystemUI creates and applies fabricated overlays through existing `ThemeOverlayApplier` flow.
11. Controller records decision and result in log and dump state.

Wallpaper events continue updating cached wallpaper colors. They do not replace an active custom or preset source. Switching back to wallpaper mode uses latest cached wallpaper colors.

## Failure handling

- If an editor field is invalid, ThemePicker shows field error and disables Save and Apply.
- If contrast is weak, ThemePicker warns but permits **Apply anyway**.
- If saved-theme JSON is invalid, ThemePicker preserves stored data, shows an error, and offers no destructive repair.
- If active custom JSON update is invalid while SystemUI runs, SystemUI rejects it and keeps currently applied overlays.
- If SystemUI starts with invalid active custom JSON, it records rejection and falls back to existing wallpaper behavior.
- If palette generation fails, SystemUI keeps current overlays when available; during boot it falls back to wallpaper behavior.
- SystemUI commits each overlay change in one transaction and receives one completion result. If application fails, existing overlays remain active, retry state remains set, and dump state identifies attempted inputs.
- If selected source is wallpaper or preset, new custom parsing and generation paths do not run.

## Observability

`ThemeOverlayController` dump output adds:

- current color source;
- custom contract schema version;
- custom theme UUID;
- linked or separate dark mode;
- normalized light and dark seeds;
- palette style;
- validation result and rejection reason;
- generation result;
- overlay apply result; and
- last successful custom apply timestamp.

A bounded SystemUI log records source selection, validation, generation, apply, and fallback decisions. Logs exclude theme names and never emit complete saved-theme documents.

Device proof uses existing SystemUI dump commands plus filtered log output. Observability must distinguish malformed payload, unsupported version, generation failure, overlay failure, and successful application.

## Testing strategy

Implementation follows strict test-driven development: add one failing test, make minimal production change, run focused verification, then refactor.

### Shared color library

Tests prove:

- deterministic role maps for four seed families;
- linked dark generation;
- separate dark generation;
- each seed family changes intended role families;
- supported styles produce expected role differences;
- Monochromatic is rejected for custom themes;
- fixed Material error roles remain unchanged across seed mutations;
- input normalization and rejection; and
- preview and runtime entry points return identical maps for the same contrast level.

### ThemePicker

Tests prove:

- valid and invalid RGB entry behavior;
- preview updates only from valid inputs;
- light and dark preview binding;
- contrast warnings target rendered component pairs;
- warned themes require explicit confirmation;
- Save does not apply;
- Apply saves and writes one complete active payload;
- cancel, back, and outside dismissal write nothing;
- save, rename, duplicate, delete, and active-theme deletion behavior;
- unique normalized names and stable UUIDs;
- current-user isolation;
- wallpaper and preset sections remain unchanged; and
- unknown saved schema stays non-destructive.

### SystemUI

Tests extend `ThemeOverlayControllerTest` to prove:

- valid custom payload selection;
- complete four-seed light and dark generation;
- linked and separate dark behavior;
- fabricated overlay resources match generated roles;
- wallpaper changes do not replace active custom themes;
- switching back to wallpaper uses latest cached colors;
- invalid live update keeps current overlays;
- invalid boot payload falls back to wallpaper;
- unknown schema version rejects cleanly;
- source-specific paths remain isolated; and
- dump state and logs explain each terminal decision.

Existing wallpaper and preset tests remain regression gates.

### Manifest and project checks

Tests prove manifest overrides select only intended YRRPs forks and preserve correct revision branches. Project documentation records exact tested SHAs and local limitations before handoff.

### Compile checks

Run project-native compile targets for ThemePicker, shared SystemUI color library, and SystemUI. Public repositories do not gain local CI emulation.

## Acceptance criteria

- Custom section coexists with wallpaper colors and presets.
- User can create, save, rename, duplicate, delete, and apply a named theme.
- User can enter exact primary, secondary, tertiary, and neutral seed colors.
- User can keep generated linked light/dark palettes or configure separate dark seeds.
- Preview renders representative components in light and dark modes before Apply.
- Changing each seed visibly affects its intended preview roles.
- Weak contrast identifies affected components and allows explicit override.
- Applied system colors match preview across Settings, SystemUI, Launcher, and ordinary Material apps.
- Applying requires no process restart.
- Applied custom theme survives SystemUI restart and device reboot.
- Wallpaper changes do not overwrite active custom theme.
- Named themes remain isolated per Android user and survive reboot and OTA update.
- Existing wallpaper-derived choices and presets still apply correctly.
- Invalid or unsupported active payload never replaces working overlays.
- SystemUI dump identifies source, inputs, validation, generation, application, and fallback result.

## Verification boundaries

Local unit and integration tests can prove parsing, validation, deterministic generation, preview/runtime parity, repository behavior, controller decisions, and fabricated resource maps. Compile checks can prove source compatibility.

Local checks cannot prove final rendering across installed apps, process restart behavior, device reboot persistence, current-user switching, or actual overlay replacement. Release proof must exercise those boundaries on target device after merged-source build and installation.

Required post-release device checks:

1. Create and apply a four-seed linked theme; compare preview with Settings, SystemUI, Launcher, and one ordinary Material app.
2. Change each seed separately; confirm intended component families change while unrelated families stay stable.
3. Apply separate dark seeds; toggle light and dark modes and compare both previews.
4. Apply weak-contrast theme through confirmation; confirm chosen colors render and warning path leaves dump evidence.
5. Change home and lock wallpapers; confirm active custom theme stays applied.
6. Restart SystemUI and reboot device; confirm active theme and saved themes remain.
7. Switch users; confirm saved themes and active selections stay isolated.
8. Apply wallpaper and preset choices; confirm original behavior returns.
9. Inject malformed and unsupported active payloads; confirm live rejection keeps overlays and boot rejection falls back safely.
10. Capture SystemUI dump and filtered logs for accepted, rejected, applied, and fallback paths.

TalkBack-specific acceptance remains excluded for this personal ROM.

## Decisions

- **2026-10-14 — Scope:** first release is a full color studio, not a single custom accent picker.
- **2026-10-14 — Location:** studio extends **Wallpaper & style**, not YRRPs Settings.
- **2026-10-14 — Inputs:** users edit primary, secondary, tertiary, and neutral seed families.
- **2026-10-14 — Guardrails:** studio warns about contrast but permits explicit override.
- **2026-10-14 — Modes:** light and dark palettes are linked by default; advanced users can separate them.
- **2026-10-14 — Reuse:** first release supports named local themes without import or export.
- **2026-10-14 — Compatibility:** Custom becomes third section beside wallpaper colors and presets.
- **2026-10-14 — Preview:** editor renders representative light and dark components before Apply.
- **2026-10-14 — Runtime:** ThemePicker writes self-contained active inputs; SystemUI generates and applies overlays.
