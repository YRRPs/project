# Clean install on OnePlus 11

Quick reference for clean-installing personal signed LineageOS 23.2 build on `salami`.

> **Warning:** Unlocking bootloader and formatting data erase all device data. Back up everything first. Keep bootloader unlocked.

## Files

Every file comes from one HTTPS install directory on `ota.yimura.dev`. You choose the type and download the files in [3. Choose your type](#3-choose-your-type). The examples use `vanilla` build `20261003-121141`. `gapps` file names insert `-gapps` after `salami`, for example `lineage-23.2-salami-gapps-20261003-121141-signed-ota.zip`.

An install directory holds:

```text
lineage-23.2-salami-20261003-121141-signed-ota.zip
boot.img
dtbo.img
init_boot.img
vbmeta.img
vendor_boot.img
recovery.img
SHA256SUMS.txt
release.json
```

## 1. Confirm firmware

Device must run latest stock **Android 16** firmware before installation. If uncertain, return to latest Android 16 stock OS first.

## 2. Unlock bootloader

Enable OEM unlocking and USB debugging, then:

```bash
adb -d reboot bootloader
fastboot devices
fastboot flashing unlock
```

Confirm unlock on device. Device erases all data. Re-enable USB debugging afterward if needed.

## 3. Choose your type

Pick one build type before you flash. Every file in the following steps must come from the same type and build.

| Type | Contents | Install directory |
|---|---|---|
| `vanilla` | No Google apps | `https://ota.yimura.dev/install/salami/<build_id>/` |
| `gapps` | MindTheGapps built in | `https://ota.yimura.dev/install/salami/gapps/<build_id>/` |

Each install directory holds the full OTA, the six install images, `SHA256SUMS.txt`, and `release.json`. When the build has an incremental OTA, the directory holds it too; a clean install does not use it. The device's Updater follows the type you install. To change type later, see [`gapps.md`](gapps.md#switch-types).

Do not sideload a separate GApps package. It does not survive incremental OTAs.

1. Find the current build ID of your type. Open `https://ota.yimura.dev/updates/salami.json` for `vanilla` or `https://ota.yimura.dev/updates/salami/gapps.json` for `gapps`. The `url` field names the install directory, for example `https://ota.yimura.dev/install/salami/20261003-121141/lineage-23.2-salami-20261003-121141-signed-ota.zip`.
2. Download every file that `SHA256SUMS.txt` lists into an empty directory:

   ```bash
   base=https://ota.yimura.dev/install/salami/20261003-121141
   curl -fLO "${base}/SHA256SUMS.txt"
   awk '{print $2}' SHA256SUMS.txt | while read -r name; do curl -fLO "${base}/${name}"; done
   ```

   For `gapps`, use `base=https://ota.yimura.dev/install/salami/gapps/<build_id>`.
3. Verify the files:

   ```bash
   sha256sum --check SHA256SUMS.txt
   ```

   Every line must report `OK`. If any line fails, stop and download again.

## 4. Flash matching signed images

From the directory that holds the verified files:

```bash
fastboot flash boot boot.img
fastboot flash dtbo dtbo.img
fastboot flash init_boot init_boot.img
fastboot flash vbmeta vbmeta.img
fastboot flash vendor_boot vendor_boot.img
fastboot reboot bootloader
fastboot flash recovery recovery.img
```

Use these signed images, not images from earlier test-key build.

## 5. Boot recovery

Choose **Recovery** from bootloader menu. Confirm LineageOS recovery appears.

If another recovery appears, stop and reflash matching `recovery.img`.

## 6. Format data

In recovery:

1. Select **Factory Reset**.
2. Select **Format data / factory reset**.
3. Confirm formatting.
4. Return to main menu.

## 7. Sideload signed ROM

In recovery, select **Apply update → Apply from ADB**.

On workstation:

```bash
adb -d sideload lineage-23.2-salami-20261003-121141-signed-ota.zip
```

Matching recovery trusts this personal release key. If ROM signature verification fails, stop; do not bypass it.

`adb sideload` may stop near 47% while recovery reports success. Recovery result is authoritative.

## 8. First boot

Select **Reboot system now**. First boot may take up to 15 minutes.

## Future updates

Future full and incremental OTAs of the same type, signed with same keys, update this installation without wiping. Preserve release keys and signed target-files archives.

## Sources

- [Official LineageOS salami installation guide](https://wiki.lineageos.org/devices/salami/install/)
- [LineageOS signing guide](https://wiki.lineageos.org/signing_builds/)
