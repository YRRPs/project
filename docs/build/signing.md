# Release signing

## Generated key set

Generated 2026-10-03 for subject:

```text
/C=BE/ST=Vlaams Brabant/L=Leuven/O=Yim's Riced ROM Project/OU=Me, myself and AI/CN=Yim's Ricing Development Team
```

Inventory:

- 11 standard APK/OTA key pairs
- 75 RSA-4096 APEX certificate and payload-key sets
- 86 encrypted PKCS#8 private keys
- 86 X.509 certificates
- 75 APEX payload PEM files
- `ANDROID_PW_FILE`-compatible password mapping

Encrypted export:

```text
/opt/android/.signing-export/android-signing-keys-20261003.tar.gz.gpg
SHA-256: 50130731446d31a6165e74c1065fa318e9f41dad21dc72048b4956d60ef116ce
Size: 541,542 bytes
```

Base64 export also exists, but binary encrypted archive is preferred for file attachments.

## Storage

- Store encrypted `.gpg` archive as Bitwarden attachment.
- Store passphrase in separate Bitwarden item.
- Keep second offline encrypted backup.
- Verify downloaded attachment against recorded SHA-256.
- Never commit archive, base64 export, password file, or unpacked key directory.
- Preserve same keys for lifetime of installed ROM update chain.

## Restore

Decrypt into AndroidBuilder home:

```bash
mkdir -p /home/android
gpg --decrypt android-signing-keys-20261003.tar.gz.gpg \
  | tar -xzf - -C /home/android
chmod 0700 /home/android/.android-certs
chmod 0600 /home/android/.android-certs/passwords
```

Verify restored files:

```bash
cd /home/android/.android-certs
sha256sum --check MANIFEST.sha256
```

Configure signing:

```bash
export ANDROID_PW_FILE=/home/android/.android-certs/passwords
```

## Signed build and deployment flow

Run canonical mounted signing entry point:

```bash
/opt/yrrp/project/scripts/sign-lineage-build.sh
```

Script initializes build environment, runs `breakfast salami`, and executes `mka target-files-package otatools` before selecting target-files. This forces current source through build graph before signing and prevents a lone stale intermediate from being deployed.

Before signing starts, script requires:

- `OTA_PUBLIC_BASE_URL` with public HTTPS origin
- `OTA_BASE_IMAGE_REF` for public YRRP OTA base
- mounted Docker socket and working Docker/Buildx/Compose clients
- existing external `proxy-net`
- release signing keys under `/home/android/.android-certs`

Script signs target-files, creates full OTA, verifies OTA and SystemUI certificates, extracts six matching install images, generates updater metadata/checksums, builds local latest-only release image, and replaces OTA container transactionally. Failed rollout restores previous healthy release and preserves newly signed artifacts for diagnosis.

## First verified signed build

Generated 2026-10-03:

```text
lineage-23.2-salami-20261003-121141-signed-ota.zip
Size: 2,180,277,103 bytes
SHA-256: 0b3b708ea099a26adb710fd595eadd41286dc3e49606f4d01d66a80c6f3565d8

lineage-23.2-salami-20261003-121141-signed-target_files.zip
Size: 4,963,451,573 bytes
SHA-256: e473b572040955acbd9f675df99c8d8a873d8fc9557ec1148e17f3bbc36c5031
```

Verification:

- Both ZIP archives pass full integrity checks.
- OTA `META-INF/com/android/otacert` matches generated `releasekey` certificate.
- Signed SystemUI APK matches generated `platform` certificate.
- OTA metadata reports A/B update, SDK 36, `release-keys`, and security patch `2026-09-01`.
- Matching signed installation images were extracted to `out/signed/install-images-20261003-121141/`.
- Recovery OTA trust certificate matches generated `releasekey`.

`zucchini` and `lz4diff` were unavailable for this OTA generation. These are payload delta/compression optimizations, not ROM compilation or runtime features. Their absence can increase OTA size or generation/application time, especially for future incrementals, without changing installed partition contents.

## Clean installation

OnePlus 11 requires latest stock Android 16 firmware before installation. Flash matching signed `boot.img`, `dtbo.img`, `init_boot.img`, `vbmeta.img`, `vendor_boot.img`, and `recovery.img` from signed target-files output. Boot matching recovery, format data, then sideload signed full OTA.

Do not use auxiliary images from earlier test-key build. Keep bootloader unlocked.

## Continuity warning

Installing test-key build before release-key build requires clean installation or controlled key migration. LineageOS 23.2 migration patch is `repopick -f 471209`; migration builds are intentionally insecure and must remain installed briefly.

## Sources

- [LineageOS signing builds](https://wiki.lineageos.org/signing_builds/)
- [AOSP release signing](https://source.android.com/docs/core/ota/sign_builds)
- [AOSP OTA tools](https://source.android.com/docs/core/ota/tools)
