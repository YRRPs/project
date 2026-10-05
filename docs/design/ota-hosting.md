# OTA hosting design

## Recommendation

Use static Nginx container behind existing HTTPS reverse proxy. No application server or database is required.

LineageOS 23.2 Updater fetches one HTTPS JSON endpoint, downloads referenced OTA ZIP, checks exact size and SHA-256, then Android verifies signed OTA payload.

Current Updater client refuses non-HTTPS metadata URLs and does not follow redirects. Metadata endpoint must return HTTP `200` directly over trusted HTTPS.

## Architecture

```text
LineageOS Updater
        |
        | HTTPS
        v
Existing reverse proxy / TLS
        |
        | private Docker network, HTTP
        v
Static Nginx container
        |
        v
read-only release directory
├── updates/salami.json
└── files/salami/
    ├── lineage-23.2-salami-...-signed-ota.zip
    └── SHA256SUMS.txt
```

Prefer shared external Docker network between reverse proxy and static server. Publish no host port when proxy can reach container by service name. If host port is required, bind only loopback or LAN address.

## Current LineageOS 23.2 JSON contract

Current client expects top-level JSON array. Older community examples using `{ "response": [...] }` are incompatible.

Example for first signed build:

```json
[
  {
    "datetime": 1791028977,
    "files": [
      {
        "filename": "lineage-23.2-salami-20261003-121141-signed-ota.zip",
        "os_patch_level": "2026-09-01",
        "os_sdk_level": 36,
        "ota_property_files": "payload_metadata.bin:5124:188489,payload.bin:5124:2180268445,payload_properties.txt:2180273627:156,apex_info.pb:2430:1279,care_map.pb:3756:1321,metadata:69:711,metadata.pb:848:1534",
        "sha256": "0b3b708ea099a26adb710fd595eadd41286dc3e49606f4d01d66a80c6f3565d8",
        "size": 2180277103,
        "url": "https://ota.example.com/files/salami/lineage-23.2-salami-20261003-121141-signed-ota.zip"
      }
    ],
    "type": "UNOFFICIAL",
    "version": "23.2"
  }
]
```

Field requirements:

- `datetime`: OTA `post-timestamp`; must increase for each update.
- `filename`: exact hosted filename.
- `sha256`: final hosted ZIP digest.
- `size`: exact byte length.
- `url`: direct HTTPS file URL without redirect.
- `type`: must match `ro.lineage.releasetype`, case-insensitively.
- `version`: must match `ro.lineage.build.version`.
- `ota_property_files`: optional but recommended for A/B streaming metadata.
- `os_patch_level` and `os_sdk_level`: optional but recommended.

Current build values:

```text
ro.lineage.device=salami
ro.lineage.releasetype=UNOFFICIAL
ro.lineage.build.version=23.2
ro.build.version.incremental=1791028977
```

## ROM endpoint configuration

Set build property from future `vendor/<rom>` product configuration:

```makefile
PRODUCT_SYSTEM_PROPERTIES += \
    lineage.updater.uri=https://ota.example.com/updates/salami.json
```

Property takes precedence over default `updater_server_url` resource. Static URL needs no `{device}`, `{type}`, or `{incr}` placeholders.

## Nginx behavior

Recommended server behavior:

- `sendfile on`
- Range requests enabled; Nginx static serving handles them automatically
- `GET` and `HEAD` only
- `autoindex off`
- No compression for OTA ZIP
- `application/json` for metadata
- `application/zip` or `application/octet-stream` for OTA
- JSON cache lifetime short or `no-cache`
- Versioned OTA files immutable with long cache lifetime
- Read-only release-directory mount
- No redirects for JSON or ZIP URLs
- No authentication; current Updater sends no custom credentials

Example shape:

```nginx
server {
    listen 80;
    server_name _;
    root /srv/ota;

    autoindex off;
    sendfile on;
    tcp_nopush on;

    location = /updates/salami.json {
        limit_except GET HEAD { deny all; }
        default_type application/json;
        add_header Cache-Control "no-cache" always;
        try_files $uri =404;
    }

    location /files/salami/ {
        limit_except GET HEAD { deny all; }
        default_type application/zip;
        add_header Cache-Control "public, max-age=31536000, immutable" always;
        try_files $uri =404;
    }

    location / {
        return 404;
    }
}
```

Do not enable gzip for ZIP files. Avoid proxy buffering or caching multi-gigabyte OTA files at TLS proxy unless intentionally provisioned.

## Publication workflow

Publish atomically:

1. Build and sign OTA.
2. Verify ZIP, OTA certificate, SHA-256, size, and metadata.
3. Copy OTA using temporary filename.
4. Verify copied file on server.
5. Rename OTA atomically to final immutable filename.
6. Generate JSON from final file and OTA metadata.
7. Validate JSON locally.
8. Replace `salami.json` atomically last.
9. Test `HEAD`, full `GET`, Range `GET`, JSON schema, and SHA-256 through public HTTPS endpoint.

Never publish metadata before OTA file is complete.

## Full versus incremental OTA

Start with full OTAs only. They are simpler and safe for small personal deployment.

Retain every signed target-files archive. Incremental OTA later requires previous and current target-files:

```bash
ota_from_target_files \
  -i previous-signed-target_files.zip \
  current-signed-target_files.zip \
  incremental-ota.zip
```

Incremental packages require stricter source-version matching and more testing. Full OTAs remain fallback.

## Security properties

- Trusted HTTPS authenticates metadata endpoint (`MASVS-NETWORK`).
- SHA-256 detects corrupted or substituted downloads.
- Android OTA signature is final authenticity boundary.
- Same release keys preserve update continuity.
- Updater rejects builds not newer than installed timestamp unless downgrades are enabled.
- Nginx container receives read-only release mount and no signing keys.
- Publisher and signing environment remain separate from public server.
- Minimize access-log retention because logs contain client IP addresses.

## Backups

Back up:

- Signed OTA ZIP
- Signed target-files ZIP
- JSON metadata
- SHA-256 file
- Revision-locked source manifest
- Release notes/test results

Never place private signing keys on OTA server.

## Sources

- [LineageOS 23.2 Updater README](https://github.com/LineageOS/android_packages_apps_Updater/blob/lineage-23.2/README.md)
- [LineageOS 23.2 Updater source](https://github.com/LineageOS/android_packages_apps_Updater/tree/lineage-23.2)
- [AOSP OTA tools](https://source.android.com/docs/core/ota/tools)
- [NGINX static content documentation](https://nginx.org/en/docs/http/ngx_http_core_module.html)
- MASVS 2.1.0 `MASVS-NETWORK`
- `CI_CD_Security_Cheat_Sheet#dependency-management`
