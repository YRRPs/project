# Optional Google Apps installation

Install Google Apps only if wanted. This step runs after ROM sideload and before first Android boot.

## Download

For LineageOS 23.2, use **MindTheGapps Android 16 ARM64**:

- [LineageOS Google Apps compatibility page](https://wiki.lineageos.org/gapps/)
- [MindTheGapps Android 16 ARM64 releases](https://github.com/MindTheGapps/16.0.0-arm64/releases/latest)

Expected filename resembles:

```text
MindTheGapps-16.0.0-arm64-*.zip
```

Download only from linked project release page and verify any published checksum.

## Install

After signed ROM finishes sideloading:

1. Do **not** boot Android.
2. If recovery asks to reboot to recovery for add-ons, select **Yes**.
3. Select **Apply update → Apply from ADB** again.
4. Run:

```bash
adb -d sideload MindTheGapps-16.0.0-arm64-*.zip
```

5. Recovery will report signature verification failure because GApps is not signed with personal ROM key. Continue only for verified package from trusted link above.
6. After installation succeeds, select **Reboot system now**.

## Rules

- Use ARM64 package for Android 16.
- Install before first Android boot.
- Do not format data between ROM and GApps.
- Do not install package built for another Android version.
- Reinstalling or changing GApps package after first boot may require clean installation.

## Source

- [Official LineageOS salami installation guide](https://wiki.lineageos.org/devices/salami/install/)
