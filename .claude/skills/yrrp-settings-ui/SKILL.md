---
name: yrrp-settings-ui
description: Use when adding, moving, renaming, removing, exposing or searching any YRRPs Settings control or page — including “add a setting/toggle/slider/color”, “make this feature configurable”, “new YRRPs page”, “nest under Audio/Animations”, “change a Settings.Secure key/default/range”, “search cannot find this setting”, “wrong breadcrumb or highlighted tile”, and changes spanning Settings and SystemUI. Covers navigation decisions, setting contracts, search/indexing, controller patterns, structural checks, and device validation. NOT for feature runtime logic alone, manifests/forks, builder access, signing, or OTA publication.
---

# YRRPs Settings UI

Announce first: **Using yrrp-settings-ui to change YRRPs Settings without breaking navigation, search, or runtime contracts.**

## Required sources

1. Read `docs/design/yrrp-settings-ui.md` for current hierarchy and contracts.
2. Before navigation/search work, read `references/search-indexing.md` completely.
3. Before controller/widget work, read `references/controller-pitfalls.md` completely.

Do not copy the current layout or key table into this skill. The design document is canonical.

## Scope boundaries

- Fork or manifest work: invoke `yrrp-manifest-and-forks`.
- Any remote command belongs to `yrrp-release-manager`. Feature owners never invoke `yrrp-builder-access`.
- Runtime gates, logs, or dumps: invoke `rom-feature-observability`.
- Build signing or OTA work: invoke `yrrp-signed-ota-release`.
- Runtime implementation without Settings changes belongs to the feature owner, not this skill.

## Workflow

1. Read the canonical design and relevant feature design. Record the current contract before proposing edits.
2. For any navigation addition, move, rename, removal, or restructuring, show current and proposed ASCII hierarchies. Ask through `AskUserQuestion` before editing, with one **Hierarchy** entry and options **Use proposed hierarchy** / **Keep current hierarchy** / **Revise proposal**. A third level requires explicit approval.
3. Write a contract diff for every setting: key, namespace, user scope, owner, consumer, default, read normalization, allowed writes, failure behavior, observability, and migration. Use private per-user `Settings.Secure` keys through `YrrpSettingsStore`; normalize reads without rewriting and constrain typed writes. Keep runtime-owner defaults and clamps identical. Use a new key for a new dimension.
4. Write an impact matrix covering Settings, runtime owner, search, tests, observability, and device acceptance. If runtime behavior changes, invoke `rom-feature-observability` before implementation.
5. Read `references/search-indexing.md` completely. Search the live current source for precedent; do not infer APIs or signatures. Apply every navigation and indexing requirement from the reference.
6. Read `references/controller-pitfalls.md` completely. Select widgets: main switch for page ownership, switch for a boolean row, `SliderPreference` for a numeric range, the YRRPs color picker dialog for an RGB color, and `SelectorWithWidgetPreference` radio rows (inline under a category, or on a focused radio page) for finite choices. Never add a YRRPs `ListPreference`. Lay the page out like **System > Gestures > Navigation mode**:
   - A setting that applies to only one choice sits behind that choice row's gear (`setExtraWidgetOnClickListener`), never in a separate row that is greyed out for the other choices.
   - Settings that apply to every choice sit below the choices under a titled, non-searchable `PreferenceCategory`.
   - Never collapse a group (`initialExpandedChildrenCount`); the structural checker rejects it.
   - When a row's title does not say what it changes on screen, put a short explanation in its summary, and label slider ends with `setTextStart`/`setTextEnd`.
   - Keep controls that depend on the main switch visible, and update their enabled state live.
7. Implement a device-first vertical slice. Settings owns UI, validation, observation, and writes; it never restarts SystemUI or drives runtime lifecycle.
8. Run the structural checker and quote its complete output in review evidence:

   ```bash
   python3 scripts/check-yrrp-settings.py .workdirs/<feature-id>/android_packages_apps_Settings
   ```

   This checks structure only; it does not replace compilation, search routing, runtime, or device evidence.
9. Keep feature-owner checks local. Put formatting/checkstyle, `m Settings`, and compile-only `m SettingsRoboTests` in the `FEATURE_READY` proof plan for `yrrp-release-manager` after merge. If the AssetManager blocker remains, record exactly: **Robolectric executed 0 tests.** Do not present compilation as test execution.
10. Review the contract diff, impact matrix, checker output, build output, and documentation changes. Use `yrrp-manifest-and-forks` only if repository publication is requested and `yrrp-signed-ota-release` only if signed build/OTA work is requested.
11. On device, verify both navigation directions, search terms and breadcrumbs, external setting changes, valid writes, invalid-value display, no read-time rewrite, process lifecycle, and feature dumpsys. Before reporting a runtime defect, read the feature dump and report `blockedBy`. Restore all user values changed during acceptance and report the restored values.
12. Update `docs/design/yrrp-settings-ui.md` for current hierarchy/contracts and the feature document for runtime semantics. Report what is proven by checker, compilation, test execution, and hardware separately.

## Stop conditions

Stop and ask rather than guessing when the hierarchy is not approved, Settings and runtime contracts disagree, current source differs from the references, or device evidence contradicts dumpsys. Do not weaken a checker invariant merely to make a checkout pass.
