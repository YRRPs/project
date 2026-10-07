---
name: yrrp-signed-ota-release
description: Use when a frozen ROM build campaign should become an installable release — "make a signed build", "build and deploy an OTA", "push a new OTA", "release this", "start an OTA build", "sign the build", "publish to ota.yimura.dev", "update the OTA server", "redeploy the OTA container", "is the OTA live", "salami.json", "the install images", "clean-flash files", "signing-failed", "deployment-failed", "OTA 404". Covers signing preflight, fixed campaign launcher, status, signature verification, and public endpoints; also redeploys an existing signed release after an ota_server change. NOT for restoring keys (yrrp-signing-keys), general builder access (yrrp-builder-access), feature batching (yrrp-build-campaign), or unsigned brunch.
---

# YRRP signed OTA release

Announce first: **Using yrrp-signed-ota-release to build, sign, and deploy.**

The pipeline lives in this repo: `scripts/sign-lineage-build.sh` → `scripts/prepare-ota-release.py` → `scripts/deploy-ota-release.sh`. Read `docs/build/signing.md` and `docs/design/ota-hosting.md` for the contract; do not restate the scripts from memory.

## 1. Preflight

All checks must pass before freeze. From a campaign in `READY_TO_FREEZE`, run the fixed capture command:

```bash
python3 scripts/yrrp-launch-campaign-build.py --campaign-id <campaign-id> --capture-snapshot
```

Capture validates signing manifest checksums, release-key symlinks, idle build/sign processes, absence of an existing build screen, Docker network availability, revision-locked manifest, modified repository revisions/branches/clean states, and orchestration-project SHA. It stores the manifest as private campaign evidence and prints the canonical snapshot for freeze.

If signing material fails validation, stop and use `yrrp-signing-keys`. If `/opt/yrrp/project` lags the required revision, update it before capture; never pull after source approval.

## 2. Launch

Do not launch `sign-lineage-build.sh` directly. It rejects invocations without the private, short-lived campaign claim created by the fixed launcher.

After explicit freeze approval and canonical snapshot capture, launch from the `yrrp-build-campaign` main session only:

```bash
python3 scripts/yrrp-launch-campaign-build.py --campaign-id <campaign-id>
```

The project `PreToolUse` hook verifies the agent type and creates one one-time authorization. The launcher consumes it, rechecks source on `AndroidBuilder` under a remote lock, creates the builder claim, and starts `sign-lineage-build.sh` in `screen`. `claude --bare`, disabled project hooks, direct SSH builds, and direct signing-script calls are unsupported.

Threat boundary: the VPN-only `android` builder account is trusted. These controls prevent accidental and agent-driven bypass; they do not defend against a human intentionally forging files or commands as that same account. The user accepted this boundary on 2026-10-07. A hostile-account boundary would require a signed claim or privileged launch service.

The signing script defaults `YRRP_CERT_DIR`, `OTA_PUBLIC_BASE_URL`, `OTA_BASE_IMAGE_REF`, and `OTA_NETWORK`. It always runs `breakfast salami` and `mka target-files-package otatools` first, so a stale target-files ZIP is never signed.

## 3. Watch

`/home/android/signed-build.status` moves through `building-target-files`, `signing-target-files`, `generating-signed-ota`, `verifying-signed-artifacts`, `preparing-ota-release`, deployer phases, then `complete`. Failure is `signing-failed:<exit>` or `deployment-failed:<exit>`; read the log tail before diagnosing. A full build takes hours; poll sparingly.

## 4. Verify

Report each of these with its output:

- `sha256sum -c` on `/opt/android/out/signed/lineage-23.2-salami-<id>-SHA256SUMS.txt`.
- `docker inspect yrrp-ota-server --format '{{.State.Health.Status}} {{.Image}}'` → `healthy`.
- Public: `/healthz` 200, `/updates/salami.json` lists the new build ID, `/install/salami/<id>/` 200 with a listing, a `Range: bytes=0-0` request on the OTA ZIP returns 206 — all on `https://ota.yimura.dev`.

Signature checks the script already performs, for manual diagnosis: OTA cert in `META-INF/com/android/otacert` must match `releasekey.x509.pem`; SystemUI APK `apksigner verify --print-certs` must match `platform`. `keytool -printcert -jarfile` reports modern OTAs as unsigned; that is not a failure.

## Redeploy without rebuilding

If only `ota_server` changed (Nginx config, base image), do not rebuild or re-sign. Wait for the `ota_server` GitHub workflow to publish `ghcr.io/yrrps/ota-server:main`, then run `deploy-ota-release.sh` against the existing signed OTA and target-files in `/opt/android/out/signed/`. It builds a local `yrrp-ota-release:<id>` image (never pushed), swaps `yrrp-ota-server` on `proxy-net`, and rolls back on any failure or signal.

## Known gaps

- Builds before manifest `0c819fd` lack `lineage.updater.uri`; on those, set it per boot as root (`adb root; adb shell setprop lineage.updater.uri https://ota.yimura.dev/updates/salami.json`). Later builds get it from `vendor/extra/product.mk`.
- Only full OTAs exist; incremental OTAs are not built.
- The release image build prints `InvalidDefaultArgInFrom`; harmless, the digest is passed explicitly.
