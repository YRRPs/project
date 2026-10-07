# OTA hosting design

## Architecture

Use hardened static Nginx container behind user-managed HTTPS reverse proxy. OTA container joins external `proxy-net` as `ota-server:8080`, publishes no host port, and receives no signing keys or Docker socket.

```text
LineageOS Updater
  -> HTTPS reverse proxy
  -> proxy-net / ota-server:8080
  -> latest-only read-only Nginx container
```

## Static layout

Each release image contains exactly one signed build:

```text
/srv/ota/
├── updates/salami.json
├── updates/salami/<source incr>.json
└── install/salami/<build-id>/
    ├── lineage-23.2-salami-<build-id>-signed-ota.zip
    ├── lineage-23.2-salami-<source>-to-<build-id>-signed-incremental-ota.zip
    ├── boot.img
    ├── dtbo.img
    ├── init_boot.img
    ├── vbmeta.img
    ├── vendor_boot.img
    ├── recovery.img
    ├── SHA256SUMS.txt
    └── release.json
```

Each OTA zip exists once. Updater JSON points directly into versioned install directory. Signed target-files remain private for incrementals and recovery. GApps, unsigned builds, previous releases, and signing material never enter image.

## LineageOS 23.2 contract

Updater expects top-level JSON array and consumes `files[0]`. Required release fields are `datetime`, `files`, `type`, and `version`. Required file fields are `filename`, `sha256`, `size`, and direct HTTPS `url`. Optional streaming fields remain included: `os_patch_level`, `os_sdk_level`, and `ota_property_files`.

Metadata endpoint must begin with HTTPS and return HTTP 200 without redirect. Static endpoint is configured later through product property:

```makefile
PRODUCT_SYSTEM_PROPERTIES += \
    lineage.updater.uri=https://ota.example.com/updates/{device}/{incr}.json
```

The Updater replaces `{incr}` with `ro.build.version.incremental` and refuses redirects, so the incremental route falls back internally.

## Nginx policy

- Internal port `8080`
- GET and HEAD only
- `/healthz` returns 200
- `/updates/salami.json` exact JSON, no-cache, no listing
- `/updates/salami/<digits>.json` serves the incremental for that source build, or the full OTA JSON through an internal `try_files` fallback.
- `/install/salami/` scoped autoindex
- Versioned release files immutable with byte-range support
- Every other path returns 404
- No gzip for OTA ZIP
- Non-root process, read-only root filesystem, `/tmp` tmpfs
- All capabilities dropped and no-new-privileges

Nginx autoindex defaults off. YRRP enables it only under `/install/salami/`; root and updater metadata remain hidden.

## Publication flow

1. Generate release-key-signed target-files and full OTA.
2. Verify ZIP integrity, OTA certificate, and SystemUI platform certificate.
3. Generate the signed incremental from the live release's signed target-files in `out/signed/`. If no live release exists, the live container's `io.yrrp.ota.device` label names another device, or the live release's target-files do not exist, publish full-only and log `incremental-skipped: <reason>`. A failed label read, or a source file that exists but is empty or corrupt, is a hard failure and never a skip.
4. Parse signed OTA metadata and target-files build properties.
5. Require A/B, `release-keys`, `salami`, `UNOFFICIAL`, and LineageOS 23.2.
6. Extract exact six `IMAGES/*.img` members.
7. Generate deterministic checksums, release manifest, and one-entry updater JSON.
8. Pull OTA base and pin release build to immutable image digest.
9. Build local `yrrp-ota-release:<build-id>` image; never push it.
10. Replace labeled production container, wait for health, and verify metadata/range serving.
11. Remove previous local release image on success or restore previous healthy container on failure.

Signing reports complete only after deployment becomes healthy. Stale signed pre-Pulse baseline must never deploy.

## Full versus incremental OTA

Each release publishes the full OTA and, when the live release's signed target-files exist in `out/signed/`, one incremental OTA from that live release. `generate-incremental-ota.sh` enables `zucchini` and `lz4diff` and fails if either is disabled. A generation failure stops the release as `incremental-failed:<code>`. The build campaign then reruns generation and deployment through `authorize-recovery`, with no rebuild.

Sources live in `out/`, so `rm -rf out` turns the next release into a full-only release. Retention outside `out/` and its backup are follow-up work.

A device on a build from before the `{incr}` URI can opt in once as root: `adb root` and `adb shell setprop lineage.updater.uri 'https://ota.yimura.dev/updates/{device}/{incr}.json'`. The value lasts until the next reboot.

## Security

- Android OTA signature is final authenticity boundary.
- HTTPS authenticates transport; SHA-256 detects corruption/substitution.
- OTA container has no host ports, signing material, target-files, or Docker socket.
- Builder socket access is explicit host-root trust and accepted only for VPN-restricted single-tenant builder.
- Deployer refuses non-YRRP container/image labels.
- Keep access-log retention minimal because client IP addresses are personal data.

Guidance:

- `Docker_Security_Cheat_Sheet#rule-1---do-not-expose-the-docker-daemon-socket-even-to-the-containers`
- `Docker_Security_Cheat_Sheet#rule-13---enhance-supply-chain-security`
- `GitHub_Actions_Security_Cheat_Sheet#minimize-github_token-permissions`

## Backups

Back up signed OTA, signed target-files, release metadata, revision-locked source manifest, release notes, test results, and encrypted signing keys. Never place private signing keys on OTA server.
