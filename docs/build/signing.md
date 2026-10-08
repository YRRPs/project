# Release signing

## Key continuity

The release-key set lives persistently at `/opt/yrrp/signing`; encrypted backup and passphrase are stored separately. Never commit keys, password maps, archives, or decrypted material. Preserve the same keys for the installed-ROM lifetime because replacement keys break OTA continuity.

Restore only through the `yrrp-signing-keys` procedure, verify `MANIFEST.sha256`, require private permissions, and confirm `testkey` symlinks point to `releasekey`.

## Approved release flow

One `yrrp-release-manager` session owns review, merge, exact source, build approval, release proof, and receipt. Feature owners provide local evidence and proof plans but have no builder access.

1. Merge selected PRs and record each actual merged default-branch SHA.
2. Run `python3 scripts/yrrp-release.py prepare --project-sha <sha> --repo <path>=<sha>`.
3. Inspect returned project SHA, repository SHAs, revision-locked manifest, and manifest SHA-256.
4. Ask through `AskUserQuestion` with **Build and release** and **Stop**.
5. Only after **Build and release**, run `python3 scripts/yrrp-release.py launch --approval 'Build and release'` with the same SHA arguments and exact `--manifest-sha256` from prepare.
6. Monitor the detached status/log, verify artifacts and service, run device proof plans serially, and write `.claude/releases/<build-id>.md` through the fixed receipt command. Preserve the input JSON, tested/merged patch-ID evidence, and complete revision-locked manifest XML.

Both prepare and launch use `/home/android/.yrrp-build-launch.lock`. Launch rechecks source and manifest before starting. Never call `sign-lineage-build.sh` directly.

## Pipeline

The signer runs `breakfast salami` and `mka target-files-package otatools`, signs target-files, creates the full OTA, verifies OTA and SystemUI certificates, generates an incremental from a valid live source when available, extracts six matching install images, generates updater metadata and checksums, builds a local latest-only release image, and replaces the OTA container transactionally. A failed rollout restores the previous healthy release and preserves diagnostic artifacts.

Required completion evidence:

- checksum file passes `sha256sum -c`;
- OTA certificate matches `releasekey.x509.pem`;
- SystemUI signer matches `platform`;
- OTA metadata is A/B, `release-keys`, `salami`, `UNOFFICIAL`, and LineageOS 23.2;
- the OTA container is healthy on the intended image;
- public health, metadata, install listing, and byte-range checks pass;
- each proof claim receives `PROVEN`, `FAILED`, or `UNPROVEN`, with observations, limitations, and restoration.

Skip TalkBack and accessibility acceptance for this personal ROM.

## Recovery

An incremental or deployment retry does not require rebuilding. The release manager directly invokes the fixed incremental generator or deployer against existing `/opt/android/out/signed/` artifacts. The invoked script non-blockingly acquires and holds `/home/android/.yrrp-build-launch.lock` for the entire operation and refuses if the lock is busy. Prior read-only inspection may diagnose an active release, but it is not the locking mechanism and never authorizes a retry. Run the script detached with captured exit/output and repeat the affected verification. An OTA-server-only change must not rebuild or re-sign.

## Clean installation

OnePlus 11 requires current stock Android 16 firmware. Flash the six matching signed images from the same target-files output, boot matching recovery, format data, and sideload the signed full OTA. Never mix images from an earlier build. Keep the bootloader unlocked.
