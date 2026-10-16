# Yim's Riced ROM Project

YRRP is a personal LineageOS 23.2 derivative for OnePlus 11 (`salami`), with a GApps channel for OnePlus Open (`xueying`). This repository holds release tooling, tests, architecture notes, and installation guidance; Android source lives in the `YRRPs` organization forks selected by `YRRPs/android`.

## Workflow

- Feature owners reuse one clean canonical clone per repository under `.workdirs/repos/`, create isolated per-feature worktrees under `.workdirs/features/`, test locally, open PRs, and hand `FEATURE_READY` plus proof plans to `yrrp-release-manager`.
- The release manager lists fixed builder IDs with `python3 scripts/yrrp-release.py targets`, treats `non_gating_targets` as diagnostics, runs tested PR heads through `check --workspace isolated`, merges reviewed changes, repeats gating targets through `check --workspace release`, and follows each run with `python3 scripts/yrrp-release.py wait --run-id <run-id>`.
- The release manager runs `prepare --require-check <run-id> --require-target <fixed-id>`, presents exact source and check evidence, and launches one channel (`salami/vanilla`, `salami/gapps`, or `xueying/gapps`) only after **Build and release** approval with the returned `--manifest-sha256` and `--channel`.
- The shared builder lock protects exact source sync and the full signed build. Completion requires checksums, signatures, healthy container/public endpoints, serial device proof, and a private immutable receipt: `.claude/releases/<build-id>.md` for vanilla, `.claude/releases/<type>-<build-id>.md` otherwise.
- Every successful deploy writes an immutable deployment record. Rollback uses `yrrp-release.py rollback` with exact `Rollback <channel> to <build-id>` approval; historical builds additionally require `--receipt-input`.
- `.claude/build-campaigns/` is an ignored legacy read-only archive, not active state.

## Features

### Available on all supported devices

- **Pulse visualizer:** Audio spectrum in the navigation area, with color modes and appearance controls.
- **Screen-off effects:** Stock, CRT, Tear, Corrupt, and Signal loss animations with custom-effect speed controls.
- **Matrix boot and shutdown animation:** Matrix renderer with optional full Finish intro and LineageOS fallback.
- **Full theming:** Named four-seed color themes with linked or separate light and dark palettes and rendered previews.
- **Ad blocking:** Hosts-file blocking, quick settings control, scheduled list updates, custom lists, and allowed hosts.
- **Hotspot client management:** Connected-client details on all supported devices; blocking and client-limit controls when hotspot firmware exposes force-disconnect support.

### Foldable-only

- **Fold animations:** Stock, Pixel, and Duo effects. Settings enables them only when unfold configuration and a hinge-angle sensor are present.

### Salami-specific

- **Indoor auto-brightness tuning:** OnePlus 11 brightness curve tuned for indoor use.
- **OnePlus Camera integration:** Stock OPlus Camera framework and application integration.
- **OnePlus Gallery:** Stock Gallery replaces the ROM gallery and supports camera thumbnails, editing, sharing, and Google Lens.

### Build-flavor dependent

- **GApps integration:** Included in `gapps` builds and omitted from `vanilla` builds. OnePlus Open ships only as `gapps`.

No TalkBack or accessibility acceptance pass is required for this personal ROM.

## Map

- `docs/design/`: source, proof, and OTA architecture
- `docs/build/`: builder and signing operations
- `docs/install/`: clean install and the vanilla and gapps types
- `scripts/`: fixed prepare/launch/receipt and signing/deployment pipeline
- `tests/`: release, signer, deployer, and guidance contracts

Canonical repositories include `android`, `android_frameworks_base`, `android_packages_apps_Settings`, `android_vendor_extra`, `android_build_server`, and `ota_server` under [`YRRPs`](https://github.com/YRRPs).
