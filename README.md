# Yim's Riced ROM Project

Central planning and operational documentation for YRRP, a LineageOS 23.2 derivative targeting OnePlus 11 (`salami`). Canonical source, manifest, and infrastructure live in dedicated organization repositories.

## Repository map

- [`project`](https://github.com/YRRPs/project): research, architecture, build/signing helpers, install guides, and release planning
- [`android`](https://github.com/YRRPs/android): canonical `repo init` manifest
- [`android_frameworks_base`](https://github.com/YRRPs/android_frameworks_base): framework changes, including Pulse
- [`android_packages_apps_Settings`](https://github.com/YRRPs/android_packages_apps_Settings): YRRPs Settings hub, feature pages, search, and secure-setting controllers; builder path `/opt/android/packages/apps/Settings`, branch `lineage-23.2`
- [`android_build_server`](https://github.com/YRRPs/android_build_server): reusable Docker-based Android builder
- [`ota_server`](https://github.com/YRRPs/ota_server): hardened latest-only OTA server base

Release signing now prepares and deploys one local latest-only OTA image through trusted builder. Reverse proxy and public HTTPS endpoint remain user-managed.

## Current state

- Baseline: LineageOS `lineage-23.2`
- Device: OnePlus 11 (`salami`)
- Manifest: `YRRPs/android`, branch `lineage-23.2`
- Pulse source: `android_frameworks_base`, branch `lineage-23.2`, head `36269fa52cee`
- Pulse ROM: `lineage-23.2-20261004-UNOFFICIAL-salami.zip`
- Pulse ROM SHA-256: `0f66af474c6ac68ad21372dd473313e9e7b8468973f4a42efd8211e1cf0aeb62`
- Hardware validation: pending physical device

## Layout

- `docs/research/`: upstream and historical research
- `docs/design/`: architecture and implementation records
- `docs/build/`: reproducible environment and signing notes
- `docs/install/`: clean-install and optional GApps procedures
- `scripts/`: signing-key and release-signing helpers
- `patches/`: recovery exports; canonical code remains in project forks

## Working rules

- Keep source changes in explicit project forks, never only in `/opt/android`.
- Put additive product configuration in dedicated `vendor/<rom>` repository when needed.
- Fork only projects requiring source changes.
- Keep proprietary blobs, build output, signing keys, passphrases, and SSH keys outside Git.
- Preserve upstream copyright and license headers.
- Generate revision-locked manifest for every tested release.

See `docs/design/downstream-workflow.md` for source-management workflow.
