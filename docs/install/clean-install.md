# Clean install

Quick reference for clean-installing a personal signed LineageOS 23.2 build on a supported device.

> **Warning:** Unlocking bootloader and formatting data erase all device data. Back up everything first. Keep bootloader unlocked.

## Devices

| Device | Codename | Types | Stock firmware the build was made from |
|---|---|---|---|
| OnePlus 11 | `salami` | `vanilla`, `gapps` | `CPH2449_16.0.5.1301(EX01)` |
| OnePlus Open | `xueying` | `gapps` only | `CPH2551_16.0.5.1301(EX01)` |

The steps are the same on both devices. The OnePlus Open steps follow the OnePlus 11 steps and use the same six images. Nobody has run them on a OnePlus Open yet, so stop and report anything that differs. The examples below use `salami`. For `xueying`, replace `salami` with `xueying` in every URL and file name.

## Files

Every file comes from one HTTPS install directory on `ota.yimura.dev`. You choose the device and type, and download the files, in [3. Choose your type](#3-choose-your-type). The examples use `salami` `vanilla` build `20261003-121141`. Every type except `vanilla` adds `-<type>` after the codename in file names, for example `lineage-23.2-salami-gapps-20261003-121141-signed-ota.zip` or `lineage-23.2-xueying-gapps-20261011-112111-signed-ota.zip`.

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

The device must run the latest stock **Android 16** OxygenOS firmware before installation. If you are not sure, return to the latest Android 16 stock OS first. The signed OTA also carries the firmware images listed in the device's LineageOS `proprietary-firmware.txt`, taken from the stock version in [Devices](#devices).

## 2. Unlock bootloader

Enable OEM unlocking and USB debugging, then:

```bash
adb -d reboot bootloader
fastboot devices
fastboot flashing unlock
```

Confirm unlock on device. Device erases all data. Re-enable USB debugging afterward if needed.

## 3. Choose your type

Pick one build type before you flash. Every file in the following steps must come from the same device, type, and build.

| Device | Type | Contents | Full OTA entry | Install directory |
|---|---|---|---|---|
| `salami` | `vanilla` | No Google apps | `https://ota.yimura.dev/updates/salami.json` | `https://ota.yimura.dev/install/salami/<build_id>/` |
| `salami` | `gapps` | MindTheGapps built in | `https://ota.yimura.dev/updates/salami/gapps.json` | `https://ota.yimura.dev/install/salami/gapps/<build_id>/` |
| `xueying` | `gapps` | MindTheGapps built in | `https://ota.yimura.dev/updates/xueying/gapps.json` | `https://ota.yimura.dev/install/xueying/gapps/<build_id>/` |

Each install directory holds the full OTA, the six install images, `SHA256SUMS.txt`, and `release.json`. When the build has an incremental OTA, the directory holds it too; a clean install does not use it. The device's Updater follows the type you install. To change type later on `salami`, see [`gapps.md`](gapps.md#switch-types). `xueying` has no other type to switch to.

Do not sideload a separate GApps package. It does not survive incremental OTAs.

1. Find the current build ID for your device and type. Open its full OTA entry from the table. The `url` field names the install directory, for example `https://ota.yimura.dev/install/salami/20261003-121141/lineage-23.2-salami-20261003-121141-signed-ota.zip`.
2. Download every file that `SHA256SUMS.txt` lists into an empty directory:

   ```bash
   base=https://ota.yimura.dev/install/salami/20261003-121141
   curl -fLO "${base}/SHA256SUMS.txt"
   awk '{print $2}' SHA256SUMS.txt | while read -r name; do curl -fLO "${base}/${name}"; done
   ```

   Set `base` to your install directory. For example, use `base=https://ota.yimura.dev/install/salami/gapps/<build_id>` for `salami` `gapps`, or `base=https://ota.yimura.dev/install/xueying/gapps/<build_id>` for `xueying`.
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

On workstation, sideload the full OTA from your install directory:

```bash
adb -d sideload lineage-23.2-salami-20261003-121141-signed-ota.zip
```

Matching recovery trusts this personal release key. If ROM signature verification fails, stop; do not bypass it.

`adb sideload` may stop near 47% while recovery reports success. Recovery result is authoritative.

## 8. First boot

Select **Reboot system now**. First boot may take up to 15 minutes.

## Future updates

Future full and incremental OTAs for the same device and type, signed with the same keys, update this installation without wiping. Preserve release keys and signed target-files archives.

## Sources

- [Official LineageOS salami installation guide](https://wiki.lineageos.org/devices/salami/install/)
- [LineageOS xueying device tree](https://github.com/LineageOS/android_device_oneplus_xueying) (`xueying` has no official LineageOS wiki page)
- [LineageOS signing guide](https://wiki.lineageos.org/signing_builds/)
