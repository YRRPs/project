# OTA hosting design

## Architecture

A hardened static Nginx container joins external `proxy-net` as `ota-server:8080`. It publishes no host port and receives no keys or Docker socket. The user-managed HTTPS reverse proxy is the public edge.

The server holds one live build per channel. A channel is `<device>/<type>`, currently `salami/vanilla` or `salami/gapps`. For each channel, the release image contains updater JSON, a full OTA, at most one incremental from that channel's previous live build, six install images, checksums, and `release.json`. Signed target-files remain private for future incrementals and recovery.

| Channel | Full-OTA entry | Incremental entry | Install files |
|---|---|---|---|
| `salami/vanilla` | `/updates/salami.json` | `/updates/salami/<incr>.json` | `/install/salami/<build-id>/` |
| `salami/gapps` | `/updates/salami/gapps.json` | `/updates/salami/gapps/<incr>.json` | `/install/salami/gapps/<build-id>/` |

`vanilla` keeps the routes devices already use. `scripts/yrrp_ota/channel.py` owns every channel-derived name, route, and label.

## Release pipeline

1. `yrrp-release-manager` merges selected PRs and records actual merged default-branch SHAs.
2. `python3 scripts/yrrp-release.py prepare --project-sha ... --repo PATH=SHA` acquires the shared lock, scoped-syncs exact source, and returns project/repository/manifest evidence plus manifest SHA-256.
3. The manager presents that exact evidence through `AskUserQuestion`; only **Build and release** permits launch.
4. `python3 scripts/yrrp-release.py launch --approval 'Build and release' --channel <device>/<type> ... --manifest-sha256 <exact>` rechecks and starts the detached signed pipeline for that one channel.
5. The signer exports `YRRP_BUILD_TYPE`, builds target-files, signs and verifies full OTA artifacts, generates a signed incremental when a valid live source exists, extracts install images, and creates deterministic checksums and metadata.
6. The deployer pins the public base by immutable image digest and copies every other live channel out of the running container. It verifies each carried channel's `release.json` build ID against its label and every file against its `SHA256SUMS.txt`, and records the SHA-256 of each carried updater route. It then builds local `yrrp-ota-release:<type>-<build-id>` from the base image, never from the live image, with one label per channel. It swaps the production container, waits for health, verifies the released channel's routes, requires byte-identical bodies on every carried route, and removes the old image only after success. Any failed check stops the deploy before the swap or rolls back to the previous container.
7. Proof plans execute serially; the immutable ignored receipt records `PROVEN`, `FAILED`, or `UNPROVEN` for every claim.

## Serving contract

Updater JSON is a top-level array whose first file contains `filename`, `sha256`, `size`, and direct HTTPS `url`; release fields include `datetime`, `files`, `type`, and `version`. The endpoint begins with HTTPS and returns 200 without redirect.

Nginx permits GET and HEAD only, serves `/healthz`, exact no-cache updater metadata for every channel, scoped `/install/salami/` autoindex, immutable versioned files, and byte ranges. Everything else is 404. The container is non-root, read-only, capability-free, and uses a `/tmp` tmpfs.

## Incremental and recovery behavior

When the channel's live build has valid private signed target-files, generate one incremental with `zucchini` and `lz4diff`. The signer reads the channel's live build from the container label `io.yrrp.ota.channel.<device>.<type>.build-id`. A container that still carries only the legacy `io.yrrp.ota.device=salami` and `io.yrrp.ota.build-id=<id>` labels counts as `salami/vanilla`; the next deploy writes channel labels only. No live container, no live build for the channel, or genuinely absent source produces an explicit full-only skip; label-read failure or corrupt source is fatal. A release never deletes another channel's signed target-files.

For a generator or deployer failure, the release manager directly invokes the fixed corresponding script with the release's `--channel` against exact existing signed artifacts. The invoked script non-blockingly acquires and holds `/home/android/.yrrp-build-launch.lock` for the entire operation and refuses if the lock is busy. Prior read-only inspection may diagnose an active release, but it is not the locking mechanism and never authorizes a retry. Run it detached with captured output, then repeat checksum, container, and public checks. A redeploy must include the existing incremental or it would remove that route.

## Verification and retention

Require checksum, OTA/SystemUI signature, container health/image, channel label, public health, updater build ID, install listing, HTTP 206 range evidence for the released channel, and unchanged routes for every carried channel. Back up signed OTA, signed target-files, release metadata, revision-locked manifest, proof results, receipt, and encrypted keys. Wiping `out/` removes incremental source history.
