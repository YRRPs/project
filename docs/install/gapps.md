# Install the gapps type

YRRP ships two build types for `salami`. Each type is a separate update channel with its own builds and incremental OTAs.

| Type | Channel | Contents |
|---|---|---|
| `vanilla` | `salami/vanilla` | LineageOS with YRRP features, no Google apps. |
| `gapps` | `salami/gapps` | The same ROM with MindTheGapps built into the signed image. |

Both types are signed with the same release key. You can move between them without wiping `/data`.

## Why GApps are built in

Do not sideload MindTheGapps on top of a `vanilla` build. Sideloaded GApps do not survive incremental OTAs.

A sideload changes ext4 metadata in `system`. During an incremental download, update_engine checks every source block it reads. The changed blocks fail that check, so update_engine rebuilds the factory blocks with FEC and writes them back over the running slot. GApps are gone after the next reboot. See [issue #28](https://github.com/YRRPs/project/issues/28) for the evidence.

The `gapps` type never modifies `system` after install, so its incremental OTAs stay valid.

## Update channels

Each build has its channel's Updater URL built in. The Updater requests the incremental route with the installed build's `ro.build.version.incremental`. If no incremental exists for that number, the server returns the channel's full OTA entry.

| Type | Updater requests | Full OTA entry | Install files |
|---|---|---|---|
| `vanilla` | `https://ota.yimura.dev/updates/salami/{incr}.json` | `https://ota.yimura.dev/updates/salami.json` | `https://ota.yimura.dev/install/salami/<build_id>/` |
| `gapps` | `https://ota.yimura.dev/updates/salami/gapps/{incr}.json` | `https://ota.yimura.dev/updates/salami/gapps.json` | `https://ota.yimura.dev/install/salami/gapps/<build_id>/` |

`<build_id>` has the form `YYYYMMDD-HHMMSS`. To see which type a device runs:

```bash
adb shell getprop ro.yrrp.build.type
adb shell getprop lineage.updater.uri
```

## Install on a new device

Follow [`clean-install.md`](clean-install.md) and pick the `gapps` type in the "Choose your type" step.

## Switch types

You switch types by sideloading the other type's full OTA in recovery. The full OTA replaces `system`, and the new build's Updater URL moves the device to the other channel.

1. Make sure no update is pending. Enable **Developer options → Rooted debugging**, then run:

   ```bash
   adb root
   adb shell snapshotctl dump
   ```

   Continue only when the output shows `Update state: none`.
   - If it shows `merging`, wait and run the command again.
   - If it shows `unverified`, an installed update is waiting for a reboot. Reboot the phone first, then run the command again until it shows `none`. Waiting without a reboot never reaches `none`.
2. Find the target type's current build. Open its full OTA entry:
   - For `gapps`: `https://ota.yimura.dev/updates/salami/gapps.json`
   - For `vanilla`: `https://ota.yimura.dev/updates/salami.json`

   The `url` field gives the full OTA's download URL, which contains the build ID. The `datetime` field gives the build time in seconds since 1970.
3. Check that the target build is newer than the installed build:

   ```bash
   adb shell getprop ro.build.date.utc
   ```

   The entry's `datetime` must be greater than this value. If it is not, update_engine refuses the sideload as a downgrade. Nothing is installed and no data is lost. Release a new build of the target channel first, then start again. This is standard update_engine behavior; it has not been tested on salami.
4. Download the full OTA from the `url` field, and `SHA256SUMS.txt` from the same directory:
   - For `gapps`: `https://ota.yimura.dev/install/salami/gapps/<build_id>/lineage-23.2-salami-gapps-<build_id>-signed-ota.zip`
   - For `vanilla`: `https://ota.yimura.dev/install/salami/<build_id>/lineage-23.2-salami-<build_id>-signed-ota.zip`
5. Check the download:

   ```bash
   grep signed-ota.zip SHA256SUMS.txt | sha256sum --check
   ```

6. Reboot to recovery:

   ```bash
   adb -d reboot recovery
   ```

7. Select **Apply update → Apply from ADB**, then run:

   ```bash
   adb -d sideload lineage-23.2-salami-gapps-<build_id>-signed-ota.zip
   ```

   Use the `vanilla` file name when you switch to `vanilla`. Recovery accepts both types because they share the release key. If signature verification fails, stop.
8. Select **Reboot system now**. Do not format data.
9. Check that the device now reports the target type with `adb shell getprop ro.yrrp.build.type`.

`/data` is not formatted in either direction. What survives depends on the direction.

### From vanilla with sideloaded GApps

If you sideloaded MindTheGapps on a `vanilla` build, switch to `gapps` with the steps above. The `gapps` type ships the same MindTheGapps packages, so Google accounts, Google app data, and third-party apps are expected to survive. This is expected, not verified on a device.

After the first boot, check that `/system/addon.d/30-gapps.sh` is gone:

```bash
adb shell ls /system/addon.d
```

The full OTA rewrites `system`, so the file normally does not survive it. Do not run `adb remount` and do not edit `/system` to remove it. Changing `system` reproduces [#28](https://github.com/YRRPs/project/issues/28). If the file is still there, sideload the `gapps` full OTA again.

### From gapps to vanilla

Switching to `vanilla` removes Google Play services, the Play Store, and the other bundled Google apps from the system image. Expect these losses:

- Google accounts are removed, because the Google account authenticator is part of Google Play services.
- The bundled Google apps disappear, and their data is usually wiped.
- Apps that depend on Google Play services stop working or lose features such as push notifications and Google sign-in.

Only third-party apps and their data stay. Switching back to `gapps` does not restore Google accounts or the data of the bundled Google apps. You sign in again and start those apps fresh.

## Rules

- Install updates through the Updater. It stays on your type's channel.
- Never sideload MindTheGapps or another GApps package on any YRRP build.
- Switch types only with a full OTA, never with an incremental.
- Switch types only when `snapshotctl dump` shows `Update state: none`.

## Source

- [Issue #28: OTA postinstall still drops GApps](https://github.com/YRRPs/project/issues/28)
- [MindTheGapps](https://github.com/MindTheGapps)
- [Official LineageOS salami installation guide](https://wiki.lineageos.org/devices/salami/install/)
