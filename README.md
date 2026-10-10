# Yim's Riced ROM Project

YRRP is a personal LineageOS 23.2 derivative for OnePlus 11 (`salami`). This repository holds release tooling, tests, architecture notes, and installation guidance; Android source lives in the `YRRPs` organization forks selected by `YRRPs/android`.

## Workflow

- Feature owners reuse one clean canonical clone per repository under `.workdirs/repos/`, create isolated per-feature worktrees under `.workdirs/features/`, test locally, open PRs, and hand `FEATURE_READY` plus proof plans to `yrrp-release-manager`.
- The release manager reviews and merges selected PRs, records actual merged SHAs, runs `python3 scripts/yrrp-release.py prepare`, presents exact project/repository/manifest evidence, and launches one channel (`salami/vanilla` or `salami/gapps`) only after **Build and release** approval with the returned `--manifest-sha256` and `--channel`.
- The shared builder lock protects exact source sync and the full signed build. Completion requires checksums, signatures, healthy container/public endpoints, serial device proof, and a private immutable receipt: `.claude/releases/<build-id>.md` for vanilla, `.claude/releases/<type>-<build-id>.md` otherwise.
- `.claude/build-campaigns/` is an ignored legacy read-only archive, not active state.

No TalkBack or accessibility acceptance pass is required for this personal ROM.

## Map

- `docs/design/`: source, proof, and OTA architecture
- `docs/build/`: builder and signing operations
- `docs/install/`: clean install and the vanilla and gapps types
- `scripts/`: fixed prepare/launch/receipt and signing/deployment pipeline
- `tests/`: release, signer, deployer, and guidance contracts

Canonical repositories include `android`, `android_frameworks_base`, `android_packages_apps_Settings`, `android_vendor_extra`, `android_build_server`, and `ota_server` under [`YRRPs`](https://github.com/YRRPs).
