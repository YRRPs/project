---
name: yrrp-signed-ota-release
description: Use when a new ROM build should become an installable release — "make a signed build", "build and deploy an OTA", "push a new OTA", "release this", "start an OTA build", "sign the build", "publish to ota.yimura.dev", "update the OTA server", "redeploy the OTA container", "is the OTA live", "salami.json", "the install images", "clean-flash files", "signing-failed", "deployment-failed", "OTA 404". Covers preflight, launching sign-lineage-build.sh in screen, reading its status, verifying signatures, and checking the public endpoints; also redeploying an existing signed release after an ota_server change. NOT for restoring missing keys (yrrp-signing-keys), NOT for general builder access (yrrp-builder-access), NOT for an unsigned test-key brunch.
---

# YRRP signed OTA release

Announce first: **Using yrrp-signed-ota-release to build, sign, and deploy.**

The pipeline lives in this repo: `scripts/sign-lineage-build.sh` → `scripts/prepare-ota-release.py` → `scripts/deploy-ota-release.sh`. Read `docs/build/signing.md` and `docs/design/ota-hosting.md` for the contract; do not restate the scripts from memory.

## 1. Preflight

All checks must pass before launch. Run them in one SSH call:

```bash
ssh AndroidBuilder 'bash -s' <<'EOF'
set -euo pipefail
sudo install -d -m 0777 -o root -g utmp /run/screen
git -C /opt/yrrp/project pull --ff-only
cd /opt/yrrp/signing
sha256sum --check --quiet MANIFEST.sha256
test "$(readlink testkey.pk8)" = releasekey.pk8
test "$(readlink testkey.x509.pem)" = releasekey.x509.pem
test -z "$(ps -eo cmd | grep -E '[s]oong_ui|[n]inja|[o]ta_from_target_files|[s]ign_target_files_apks' || true)"
! screen -list | grep -q '[.]yrrp-ota-build'
docker network inspect proxy-net >/dev/null
EOF
```

If `MANIFEST.sha256` is missing or fails, stop and use yrrp-signing-keys. If `/opt/yrrp/project` lags local `main`, push first: the builder runs the scripts from that read-only clone, and its TrueNAS source must be pulled with `ssh truenas_admin@192.168.4.243 'git -C /mnt/fast/docker/android/project pull --ff-only'`.

## 2. Launch

```bash
ssh AndroidBuilder 'bash -s' <<'EOF'
set -euo pipefail
mkdir -p /opt/android/out/signed
stamp=$(date +%Y%m%d-%H%M%S)
log=/opt/android/out/signed/yrrp-ota-build-${stamp}.log
screen -L -Logfile "$log" -dmS yrrp-ota-build bash -lc "exec /opt/yrrp/project/scripts/sign-lineage-build.sh"
screen -list | grep '[.]yrrp-ota-build'
printf 'log=%s\nproject=%s\n' "$log" "$(git -C /opt/yrrp/project rev-parse HEAD)"
EOF
```

The script defaults `YRRP_CERT_DIR`, `OTA_PUBLIC_BASE_URL`, `OTA_BASE_IMAGE_REF`, and `OTA_NETWORK`; SSH sessions do not inherit Compose environment, so do not rely on it. It always runs `breakfast salami` and `mka target-files-package otatools` first, so a stale target-files ZIP is never signed.

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
