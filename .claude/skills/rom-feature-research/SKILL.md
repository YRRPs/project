---
name: rom-feature-research
description: Use when investigating or porting a feature from an older custom ROM onto LineageOS 23.2 — "port X from Resurrection Remix", "RR had this", "Dirty Unicorns feature", "DU Pulse", "CRT screen-off animation", "how did <ROM> implement", "bring back <feature>", "rice the ROM", "custom SystemUI feature", "research a mod", "is this still possible on Android 16", "find the original commits". Covers where the reference sources are, how to map deprecated APIs onto current Lineage architecture, where research and design notes go, and how to verify SystemUI changes when shared tests are broken. NOT for building, signing, or deploying (yrrp-signed-ota-release), NOT for on-device debugging.
---

# ROM feature research

Announce first: **Using rom-feature-research to research and port a ROM feature.**

## Reference sources

| Source | Notes |
|---|---|
| `ResurrectionRemix/android_frameworks_base` | e.g. CRT screen-off commit `3c802fa49778` |
| `ResurrectionRemix/Resurrection_packages_apps_Settings` | settings halves of RR features |
| `ResurrectionRemix/external_pulse` (branch `Q`) | RR Pulse, adds lock-screen and ambient hosts |
| `DirtyUnicorns/android_packages_apps_DUI`, `android_external_DUI`, `android_external_pulse`, `android_frameworks_base`, `android_packages_apps_DU-Tweaks` | original Pulse |
| `crdroidandroid/android_frameworks_base`, `RisingOS-Revived/android_frameworks_base` | modern ports; check for current-API precedent first |
| `LineageOS/android_frameworks_base` (`lineage-23.2`) | the target |

Older features nearly always depend on APIs or hooks that no longer exist. Find the modern-ROM port before designing from the historical one.

## Procedure

1. Read the existing notes first: `docs/research/` and `docs/design/`. Pulse and CRT are already researched; extend those files instead of starting over.
2. Find the original commits, and cite each with a commit link.
3. List every platform API or hook the original used. Mark each one as current, deprecated, or removed in Android 16 / SDK 36.
4. Map each one onto the current Lineage architecture, checked against the live source in `/opt/android` on the builder, not from memory. Examples: SystemUI Dagger scopes, `NavigationBarControllerImpl`, `LightRevealEffect`, `ScreenOffAnimationController`.
5. Write `docs/research/<feature>.md` (history, compatibility, recommended reuse, performance, privacy, sources). Then write `docs/design/<feature>.md`.
6. Before you implement, run superpowers brainstorming and writing-plans. Put the spec and plan in `docs/superpowers/specs/` and `docs/superpowers/plans/`. That folder is git-ignored on purpose, so do not commit it.

## Lessons already paid for

- **Navigation host.** The OnePlus 11 (`salami`) runs Launcher3 `Taskbar` as its navigation host, in both gesture and three-button mode. A feature hooked only into SystemUI `NavigationBar` never attaches on this device. The Pulse MVP hit exactly this. Any bottom-of-screen feature must support both `NavigationBar` and `TaskbarDelegate`.
- **Settings.** Settings keys use the per-user secure prefix `lineage_<feature>_*`, and the feature is off by default. Until a settings UI exists, the only way to toggle a feature is `adb shell settings put secure …`.

## Implementation and verification

- Before the first device build, add a Dumpable and a LogBuffer for the feature's gates and lifecycle. Use rom-feature-observability for this.

- Commit in the org fork of the touched repo, with one commit per logical step. Export the series to `patches/<repo>/<feature>/` with `SHA256SUMS.txt`. See yrrp-manifest-and-forks.
- The shared Ravenwood and Robolectric SystemUI test targets fail on unrelated existing fixtures. Do not report that as your breakage. Use these as the authoritative checks:
  1. `m SystemUI-core`
  2. `m SystemUI`
  3. Standalone runs of the new test classes
  4. A full `brunch salami`
  5. `classes.dex` inspection of the packaged `SystemUI.apk`, confirming the new classes are present
- Run long builds through yrrp-builder-access.
- Format Kotlin with `/opt/android/external/ktfmt/ktfmt.sh`. It needs JDK 21 on `PATH`.
- A build that passes on the builder does not prove the feature works on the device. Say which behaviors are still unverified on hardware.
