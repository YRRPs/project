# OTA hosting design

## Architecture

A hardened static Nginx container joins external `proxy-net` as `ota-server:8080`. It publishes no host port and receives no keys or Docker socket. The user-managed HTTPS reverse proxy is the public edge.

Each local release image contains one signed build: updater JSON, a full OTA, at most one incremental from the live release, six install images, checksums, and `release.json`. Signed target-files remain private for future incrementals and recovery.

## Release pipeline

1. `yrrp-release-manager` merges selected PRs and records actual merged default-branch SHAs.
2. `python3 scripts/yrrp-release.py prepare --project-sha ... --repo PATH=SHA` acquires the shared lock, scoped-syncs exact source, and returns project/repository/manifest evidence plus manifest SHA-256.
3. The manager presents that exact evidence through `AskUserQuestion`; only **Build and release** permits launch.
4. `python3 scripts/yrrp-release.py launch --approval 'Build and release' ... --manifest-sha256 <exact>` rechecks and starts the detached signed pipeline.
5. The signer builds target-files, signs and verifies full OTA artifacts, generates a signed incremental when a valid live source exists, extracts install images, and creates deterministic checksums and metadata.
6. The deployer pins the public base by immutable image digest, builds local `yrrp-ota-release:<build-id>`, swaps the labeled production container, waits for health, verifies serving, and removes the old image only after success.
7. Proof plans execute serially; the immutable ignored receipt records `PROVEN`, `FAILED`, or `UNPROVEN` for every claim.

## Serving contract

Updater JSON is a top-level array whose first file contains `filename`, `sha256`, `size`, and direct HTTPS `url`; release fields include `datetime`, `files`, `type`, and `version`. The endpoint begins with HTTPS and returns 200 without redirect.

Nginx permits GET and HEAD only, serves `/healthz`, exact no-cache updater metadata, scoped `/install/salami/` autoindex, immutable versioned files, and byte ranges. Everything else is 404. The container is non-root, read-only, capability-free, and uses a `/tmp` tmpfs.

## Incremental and recovery behavior

When the live release has valid private signed target-files for the same device, generate one incremental with `zucchini` and `lz4diff`. No live release or genuinely absent source produces an explicit full-only skip; label-read failure or corrupt source is fatal.

For a generator or deployer failure, the release manager directly invokes the fixed corresponding script against exact existing signed artifacts. The invoked script non-blockingly acquires and holds `/home/android/.yrrp-build-launch.lock` for the entire operation and refuses if the lock is busy. Prior read-only inspection may diagnose an active release, but it is not the locking mechanism and never authorizes a retry. Run it detached with captured output, then repeat checksum, container, and public checks. A redeploy must include the existing incremental or it would remove that route.

## Verification and retention

Require checksum, OTA/SystemUI signature, container health/image, public health, updater build ID, install listing, and HTTP 206 range evidence. Back up signed OTA, signed target-files, release metadata, revision-locked manifest, proof results, receipt, and encrypted keys. Wiping `out/` removes incremental source history.
