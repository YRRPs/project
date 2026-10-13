# Hotspot client management

The Wi-Fi hotspot page lists connected devices, blocks and unblocks a device by MAC address, and shows or sets the client limit with its source. Issue [#74](https://github.com/YRRPs/project/issues/74). Settings fork only; no framework, Wi-Fi module, or device-tree change.

## Page

**Network & internet → Hotspot & tethering → Wi-Fi hotspot** (`WifiTetherSettings`, `wifi_tether_settings.xml`). The stock rows are unchanged. The YRRPs rows follow them:

```text
Wi-Fi hotspot
├ …stock rows
├ [Connected devices]            yrrp_hotspot_clients; non-searchable; hidden while the hotspot is off
│ ├ <hostname | Unknown device>  "<IPv4> · <MAC>" or "<MAC>"; tap → details, Block
│ └ No devices connected
└ [Client controls]              yrrp_hotspot_controls; non-searchable; hidden while neither row shows
  ├ Maximum connected devices    yrrp_hotspot_max_clients; slider 1…cap, top = No limit
  │                              "Up to N devices · Set by you | Set by your carrier | Device maximum"
  └ Blocked devices              yrrp_hotspot_blocked_entry → YrrpHotspotBlockedSettings
      ├ intro
      └ [MAC addresses]          one row per blocked MAC; tap → Unblock
```

**Blocked devices** sits at the same depth as the stock **Security** and **Speed** sub-pages. The hierarchy was approved on 2026-10-13. It applies the hotspot page's restrictions: admin users only, `DISALLOW_CONFIG_TETHERING`, and the tethering and Wi-Fi tethering policies. Search indexes it only where the hotspot page is usable.

Row keys on both pages are indexes such as `yrrp_hotspot_client_0`, never MACs. `DashboardFragment` logs the key of every clicked preference to the events buffer and statsd.

`YrrpHotspotRowKeys` owns those keys. A tap resolves the device by key alone, and the rows are rebuilt only when their content changes. A rebuild with unchanged content strands the visible rows: `PreferenceGroup` reuses a removed row's id by key, and `DashboardFragment`'s `SimplePreferenceComparisonCallback` then treats the new row as unchanged and keeps the view bound to the old, detached `Preference`. Settings#14 compared the tapped object with the one in the group, so every tap failed on salami.

## State

The Wi-Fi module owns every value. Settings stores nothing in `Settings.Secure`.

| Value | Source | Settings reads | Settings writes |
|---|---|---|---|
| Blocked list | `SoftApConfiguration.getBlockedClientList()` | as stored, MACs lowercased for display | adds or removes one MAC |
| User limit | `SoftApConfiguration.getMaxNumberOfClients()` | 0 is no limit; a value at or above the cap shows as No limit | slider position 1…cap−1 stores that value; the top position stores 0 |
| Cap | `SoftApCapability.getMaxSupportedClients()` | slider maximum | never |
| Carrier cap | `CarrierConfigManager.Wifi.KEY_HOTSPOT_MAX_CLIENT_COUNT` for the default data SIM | source label only | never |

On salami the cap is 32, from `config_wifiHardwareSoftapMaxClientCount` in `android_hardware_oplus`. The module lowers it to the carrier value when the carrier sets one.

Limit source:

- **Set by you:** the user value is 1…cap−1.
- **Set by your carrier:** otherwise, if the carrier value is above 0 and equals the cap.
- **Device maximum:** otherwise.

Settings never rewrites a stored value on read. A stored value at or above the cap stays stored until you move the slider.

## Writes

Each write reads the current configuration, changes one field, and calls `WifiManager.setSoftApConfiguration`, as the stock auto-off switch does. It never calls `WifiHotspotRepository.setSoftApConfiguration`, because that restarts tethering and drops every client.

The module applies both fields to a running hotspot without a restart. `SoftApManager` handles `CMD_UPDATE_CONFIG` with `updateClientConnection()`, which deauthenticates blocked clients with `SAP_CLIENT_BLOCK_REASON_CODE_BLOCKED_BY_USER` and surplus clients with `SAP_CLIENT_BLOCK_REASON_CODE_NO_MORE_STAS`. That covers Wi-Fi module `8e08d5bf50`, `SoftApManager.java` lines 1708-1760 and 2219-2240. The slider writes on release only, because each write can disconnect clients.

The module rejects a configuration with a blocked list or a non-zero limit when the capability lacks `SOFTAP_FEATURE_CLIENT_FORCE_DISCONNECT` (`ApConfigUtil.checkSupportAllConfiguration`). Without that feature, Settings hides **Block** and the slider, and shows **Blocked devices** only while the list is non-empty, so a restored list can still be cleared.

A blocked device that changes its MAC address, for example with per-network MAC randomization reset, can connect again. Block is not a one-shot kick: the device stays out until you unblock it.

## Connected devices

The Wi-Fi module's `SoftApCallback.onConnectedClientsChanged` decides who is connected. Tethering's `TetheringEventCallback.onClientsChanged` adds the DHCP hostname and the first IPv4 address of each Wi-Fi `TetheredClient`, joined by MAC. A detail for a MAC that the module does not list is ignored. Named devices sort first, then by MAC.

Known gaps:

- The module re-sends the client list only when the count changes. If one device leaves and another joins in the same update, the list keeps the old MAC until the next count change.
- Search indexes **Maximum connected devices** and **Blocked devices** whenever the hotspot is usable, even on a device without force-disconnect, where the rows are hidden.

## Settings units

All in `com.android.settings.yrrp`:

- Pure, plain-JUnit tested: `YrrpHotspotMac`, `YrrpHotspotBlockList`, `YrrpHotspotLimit`, `YrrpHotspotClients`, `YrrpHotspotDump`, `YrrpHotspotEventLog`, `YrrpHotspotRowKeys`.
- Android-bound: `YrrpHotspotMonitor` (callbacks between `onStart` and `onStop`), `YrrpHotspotConfigWriter`, `YrrpHotspotControls` (the one entry point `WifiTetherSettings` calls), three controllers on the hotspot page, and `YrrpHotspotBlockedSettings` with its list controller.

## Observability

With the hotspot page open, run:

```bash
adb shell dumpsys activity top | grep -A 60 'YrrpHotspot:'
```

The block prints:

- `apEnabled`, `capabilityKnown`, `forceDisconnectSupported`
- `connected`, `withDetails`, `blocked`
- `maxSupported`, `userMax`, `carrierMax`, `effectiveMax`, `limitSource`
- `blockAllowed`, `blockBlockedBy`, `limitAllowed`, `limitBlockedBy`
- `events:`, the last 50 transitions in this Settings process: monitor start and stop, AP state, capability, client count, and each write with its result or exception class

It holds counts, booleans, and enum names only. It never prints a MAC address, hostname, or IP address. `adb shell dumpsys wifi` shows the module's own view, including `MaxClient` and `BlockedClientList`.

## Local tests cannot prove

- That the salami driver honours `forceClientDisconnect`. The capability flag is a config value, not a probe of the hardware.
- That the driver accepts 32 clients.
- That hostapd and DHCP report a hostname for every client.
- That the page renders the YRRPs rows when the `catalyst_tether_settings` flag is on. The screen declares an incomplete hierarchy, so the XML still applies.
- Compilation of the Android-bound classes. They were type-checked against hand-written stubs only; `m Settings` proves them.
- Robolectric executed 0 tests in this tree.

## Decision log

- **2026-10-13 — Placement:** inline **Connected devices** category, limit slider row, and a **Blocked devices** sub-page on the stock hotspot page.
- **2026-10-13 — Limit widget:** a slider from 1 to the cap whose top position is **No limit** and stores 0, so the device maximum follows the capability.
- **2026-10-13 — Block flow:** tapping a device opens a details dialog with **Block**; no one-tap block.
- **2026-10-13 — Client controls category:** **Maximum connected devices** and **Blocked devices** move into a titled, non-searchable **Client controls** category. Without a header they read as entries in the **Connected devices** list. Keys, search entries, and the sub-page depth are unchanged.
- **2026-10-13 — Cause of the 2-client limit on salami:** the stored user value `MaxClient = 2`, restored from a backup; `MaximumSupportedClientNumber=32` rules out the carrier path. The slider's **No limit** position clears it.
