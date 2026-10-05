# Clean install on OnePlus 11

Quick reference for clean-installing personal signed LineageOS 23.2 build on `salami`.

> **Warning:** Unlocking bootloader and formatting data erase all device data. Back up everything first. Keep bootloader unlocked.

## Files

Use artifacts from same signed build:

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

## 3. Flash matching signed images

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

## 4. Boot recovery

Choose **Recovery** from bootloader menu. Confirm LineageOS recovery appears.

If another recovery appears, stop and reflash matching `recovery.img`.

## 5. Format data

In recovery:

1. Select **Factory Reset**.
2. Select **Format data / factory reset**.
3. Confirm formatting.
4. Return to main menu.

## 6. Sideload signed ROM

In recovery, select **Apply update → Apply from ADB**.

On workstation:

```bash
adb -d sideload lineage-23.2-salami-20261003-121141-signed-ota.zip
```

Matching recovery trusts this personal release key. If ROM signature verification fails, stop; do not bypass it.

`adb sideload` may stop near 47% while recovery reports success. Recovery result is authoritative.

## 7. Optional: install Google Apps

If Google Apps are wanted, install them **now**, before first Android boot. Follow [`gapps.md`](gapps.md).

Do not format data again between ROM and Google Apps.

## 8. First boot

If no add-ons remain, select **Reboot system now**. First boot may take up to 15 minutes.

## Future updates

Future full OTAs signed with same keys can update this installation without wiping. Preserve release keys and signed target-files archives.

## Sources

- [Official LineageOS salami installation guide](https://wiki.lineageos.org/devices/salami/install/)
- [LineageOS signing guide](https://wiki.lineageos.org/signing_builds/)
