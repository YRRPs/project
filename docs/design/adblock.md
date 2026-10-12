# Ad blocking

Issue: YRRPs/project#60.
Design: `docs/superpowers/specs/2026-10-12-adblock-design.md`.
Plan: `docs/superpowers/plans/2026-10-12-adblock-hosts.md`.
Research: `docs/research/adblock.md`.

YRRP blocks ad and tracker servers for every app through `/system/etc/hosts`. It needs no root,
uses no VPN slot, and leaves `/system` read-only, so verified boot and OTAs stay intact. A
change applies to the next DNS lookup, without a reboot.

## Setting

**Settings → YRRPs → Network → Ad blocking**, plus an **Ad blocking** Quick Settings tile. See
`yrrp-settings-ui.md` for the page tree.

| Row | What it does |
|---|---|
| **Use ad blocking** | Main switch. The tile writes the same property through the same code path. |
| **Update lists now** | Downloads every enabled list and rebuilds the blocklist. Shows the last update time and blocked-host count, or why it failed. |
| **Update automatically** | Weekly job on an unmetered network while the device is idle. Default on. |
| **Lists** | One row per hosts list with a switch. Tap a row to remove it. **Add list** accepts only `https://` addresses. |
| **Allowed hosts** | Names the blocklist never contains. Tap a row to remove it. |

Defaults: the three AdAway lists, all enabled. Rows below the main switch stay enabled while
blocking is off, so you can prepare lists first. Turning blocking on with no blocklist yet
starts a download.

Ad blocking is device-wide. Only the primary user sees the page enabled; the tile shows
unavailable for other users.

## How it works

```text
init, post-fs-data (every boot):
  mkdir /data/misc/yrrp 0771 system system
  copy /system/etc/hosts -> /data/misc/yrrp/hosts.stock and /data/misc/yrrp/hosts
  mount /data/misc/yrrp/hosts on /system/etc/hosts (bind)

init, property triggers:
  persist.yrrp.adblock.enabled=1            copy hosts.blocked -> hosts
  persist.yrrp.adblock.enabled=0            copy hosts.stock   -> hosts
  sys.yrrp.adblock.apply=* while enabled    copy hosts.blocked -> hosts
  boot while enabled                        copy hosts.blocked -> hosts

Settings (system uid):
  update: fetch lists -> per-list cache -> strict parse -> merge - allowlist
          -> hosts.blocked (temp file, fsync, rename) -> bump sys.yrrp.adblock.apply
  switch and tile: set persist.yrrp.adblock.enabled
```

`init` owns the live file. `copy` truncates and rewrites it in place, so the bind mount keeps the
same inode; a rename would leave the mount on the old file. Settings never writes the live file.
Every boot reseeds it from stock, so a bad file cannot survive a reboot.

Persist properties can load before the `post-fs-data` seed and be overwritten by it. The
`on boot` action copies the blocklist again before `netd` starts.

| Property | Type | Writer | Meaning |
|---|---|---|---|
| `persist.yrrp.adblock.enabled` | bool | Settings, shell | `1` or `true` blocks; anything else is off |
| `sys.yrrp.adblock.apply` | int | Settings, shell | A new value re-copies `hosts.blocked` while enabled |

| File | Label | Writer |
|---|---|---|
| `/data/misc/yrrp/hosts` | `yrrp_hosts_file` | `init` only |
| `/data/misc/yrrp/hosts.stock` | `yrrp_hosts_file` | `init` only |
| `/data/misc/yrrp/hosts.blocked` | `yrrp_adblock_data_file` | Settings |

## Repositories

| Repository | Files |
|---|---|
| `android_vendor_extra` | `init/yrrp_adblock.rc` (installed to `/product/etc/init`), `sepolicy/private/{file.te,file_contexts,init.te,netd.te,system_app.te,shell.te,property.te,property_contexts}` |
| `android_packages_apps_Settings` | `src/com/android/settings/yrrp/YrrpAdBlock*.java`, `res/xml/yrrp_ad_block_settings.xml`, the Network category in `res/xml/yrrp_settings.xml`, strings, tile icon, manifest services |

SELinux: `init` gets `mounton` on `system_file:file` and writes both `yrrp_*` types. `netd`
reads `yrrp_hosts_file`. `system_app` sets the property, writes `yrrp_adblock_data_file`, and
only reads `yrrp_hosts_file`. `shell` may set the property for adb acceptance.

## Settings units

| Unit | Responsibility |
|---|---|
| `YrrpAdBlockHostnames` | Name rules: Guava `InternetDomainName` syntax (as Private DNS uses), ASCII only, dotted, no trailing dot, no reserved local names, no addresses |
| `YrrpAdBlockHostsParser` | Keeps only `0.0.0.0`, `127.0.0.1`, and `::` entries; counts and drops redirect entries, invalid names, and lines over 1024 characters |
| `YrrpAdBlockHostsBuilder` | Stock localhost lines, then sorted unique `0.0.0.0` lines minus the allowlist; refuses more than 2,000,000 entries before writing |
| `YrrpAdBlockStore` | Lists, allowlist, and status in `state.tsv` under device-encrypted Settings storage; per-list cache files; https-only lists, at most 20 lists and 1000 allowed hosts |
| `YrrpAdBlockFetcher` | Conditional GET (`If-None-Match`, `If-Modified-Since`); https on the first URL and every redirect hop, followed by hand; 10 MiB cap; 15 s connect and 30 s read timeouts; platform TLS trust |
| `YrrpAdBlockUpdater` | One lock for updates and page edits; keeps the last good `hosts.blocked` on failure |
| `YrrpAdBlockState` | The two properties |
| `YrrpAdBlockEventLog` | Last 50 transitions in a file, so they survive process death |
| `YrrpAdBlockDump`, `YrrpAdBlockStatusService` | The dump below |
| `YrrpAdBlockTileService`, `YrrpAdBlockUpdateJobService` | Tile and weekly job |

## Failure rules

- A list that fails to download keeps its previous cache, and the build still uses it.
- If no enabled list has a cache, or the build fails, `hosts.blocked` keeps its last good
  content and `init` is not asked to copy.
- `hosts.blocked` and `state.tsv` change only by temp file, fsync, and rename.
- Redirect entries are never written; the per-list `droppedRedirects` count shows them.
- Settings allows cleartext traffic, so the fetcher enforces https itself.

## Observability

Run these first in every device session:

```bash
adb shell am start-service -n com.android.settings/.yrrp.YrrpAdBlockStatusService
adb shell dumpsys activity service com.android.settings/.yrrp.YrrpAdBlockStatusService
adb shell getprop | grep yrrp.adblock
adb shell cat /proc/$(adb shell pidof netd)/mountinfo | grep hosts
adb shell ls -lZ /data/misc/yrrp
```

The dump prints:

```text
enabled= autoUpdate= isPrimaryUser= jobScheduled=
hostsBlockedBytes= blockedEntries= sourceEntries= liveHostsBytes= bindMountPresent=
lastUpdate= lastResult= allowlistSize=
source[i] host= enabled= status= entries= droppedRedirects= etagPresent= lastSuccess=
events:
```

`bindMountPresent` reads Settings' own `/proc/self/mountinfo`; app processes inherit `init`'s
mount because zygote starts after `post-fs-data`. The status service stops itself five minutes
after it starts. The dump and event log record list hosts only: never paths, queries, allowlist
entries, or looked-up names.

## Local tests cannot prove

- That `init` runs the fragment, that the `copy` keeps the inode, and that `netd` sees the mount.
- That `m selinux_policy` accepts the rules, including `init` `mounton` on `system_file:file`.
- That `m Settings` compiles the Android-bound classes. They were compiled locally only against
  hand-written API stubs.
- Robolectric tests (`YrrpAdBlockSettingsTest`, `YrrpSettingsTest`): `SettingsRoboTests` runs
  no test in this tree today.
- Real TLS, real servers, the lookup cost of the full lists, and Private DNS behavior.

The release handoff carries the device proof plan.
