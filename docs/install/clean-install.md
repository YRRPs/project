# Clean install on OnePlus 11

Quick reference for clean-installing personal signed LineageOS 23.2 build on `salami`.

> **Warning:** Unlocking bootloader and formatting data erase all device data. Back up everything first. Keep bootloader unlocked.

## Files

Use artifacts from same signed build. The examples below show a `vanilla` build. `gapps` file names insert `-gapps` after `salami`, for example `lineage-23.2-salami-gapps-20261003-121141-signed-ota.zip`. See [3. Choose your type](#3-choose-your-type).

```text
lineage-23.2-salami-20261003-121141-signed-ota.zip
install-images-20261003-121141/
├── boot.img
├── dtbo.img
├── init_boot.img
├── vbmeta.img
├── vendor_boot.img
├── recovery.img
└── SHA256SUMS.txt
```

Remote locations:

```text
/opt/android/out/signed/lineage-23.2-salami-20261003-121141-signed-ota.zip
/opt/android/out/signed/install-images-20261003-121141/
```

OTA SHA-256:

```text
0b3b708ea099a26adb710fd595eadd41286dc3e49606f4d01d66a80c6f3565d8
```

Download with:

```bash
scp AndroidBuilder:/opt/android/out/signed/lineage-23.2-salami-20261003-121141-signed-ota.zip .
scp -r AndroidBuilder:/opt/android/out/signed/install-images-20261003-121141 .
```

Verify:

```bash
sha256sum lineage-23.2-salami-20261003-121141-signed-ota.zip
cd install-images-20261003-121141
sha256sum --check SHA256SUMS.txt
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

Each install directory holds the full OTA, the six install images, `SHA256SUMS.txt`, and `release.json`. The device's Updater follows the type you install. To change type later, see [`gapps.md`](gapps.md#switch-types).

Do not sideload a separate GApps package. It does not survive incremental OTAs.

## 4. Flash matching signed images

From `install-images-20261003-121141/`:

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
