# Ad blocking research

## Feature

System-wide ad blocking through the hosts file, without root, without a VPN slot, and with a
read-only `/system`. Tracked in YRRPs/project#60.

## Existing approaches

| Approach | Cost | Verdict |
|---|---|---|
| AdAway VPN mode | Holds the one VPN slot Android allows; conflicts with any real VPN. | Rejected |
| AdAway root mode | Remounts `/system` read-write and rewrites `/system/etc/hosts`. A YRRP build has a read-only, verified `/system`, so it needs Magisk or KernelSU. | Rejected |
| Magisk Systemless hosts | Bind-mounts `/data/adb/modules/hosts/system/etc/hosts` over `/system/etc/hosts`; AdAway then writes that file in place. | The model for YRRP, minus root |
| Private DNS with a blocking provider | No code, but depends on an outside service and gives no local list control. | Not chosen |
| Patch DnsResolver to read an indexed blocklist | Faster lookups, but forks the `com.android.resolv` mainline module. | Follow-up if the measured lookup cost is too high |
| Separate ad-block app | Isolates downloading and parsing from the `system` user, but costs an app, a SELinux domain, and a navigation entry. | Not chosen; Settings already has `INTERNET` |

## AdAway (reference only)

Read at `AdAway/AdAway` `fa16432c3b8f`. AdAway is GPL-3.0 and the Settings fork is Apache-2.0,
so YRRP reimplements the behavior and copies no code. The default source URLs are data.

- `RootModel.java` (`copyHostsFile`) builds the file in app storage, remounts the partition
  read-write, writes it with `dd`, then `chown` and `chmod 644`, and remounts read-only.
  `revertHostFile` writes only the two `localhost` lines.
- `SourceModel.java` checks `ETag` and `Last-Modified` before it downloads.
- `AppDatabase.java:100-110` holds the default sources:

  ```text
  https://adaway.org/hosts.txt
  https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
  https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext
  ```

## Platform behavior

Read from AOSP `main`; the `lineage-23.2` tree is confirmed on the device, not here.

- `bionic/libc/include/netdb.h:71` defines `_PATH_HOSTS` as `/system/etc/hosts`.
- `packages/modules/DnsResolver/getaddrinfo.cpp` `explore_fqdn` calls `files_getaddrinfo`
  before `dns_getaddrinfo`. The hosts file therefore wins over DNS, over the resolver cache, and
  over Private DNS. `files_getaddrinfo` and `_gethtent` open the file and read it with `fgets`
  on every call, so a change applies to the next lookup.
- App lookups go through `netd`'s DNS proxy, so only `netd`'s mount namespace must see the bind
  mount. `netd` is `class main` and starts after `post-fs-data`.
- `system/sepolicy/private/init.te` grants `init` `mounton` on `system_file` directories but
  not files. The `mounton` neverallow on `system_file_type` in `private/domain.te` exempts
  `init`, so a file rule is allowed.
- `system/core/init/builtins.cpp:468` supports the `bind` mount flag. `init`'s `copy` opens the
  destination with `O_TRUNC` and writes in place, so a bind mount over the destination keeps
  pointing at the same inode.

## Limits

- **Lookup cost.** DnsResolver scans the whole file on every lookup that the hosts file does not
  answer. The three default lists merge to roughly 100,000 or more lines. Measure on salami.
- **Bypass.** Browsers with their own DNS-over-HTTPS (Chrome Secure DNS, Firefox), apps with
  hard-coded addresses, and apps with a built-in resolver skip the hosts file. AdAway root mode
  has the same limit.
- **App caches.** Apps and browsers that cache answers can keep a stale answer briefly after a
  change.
- **Security.** Settings runs as `system`, and lists come from the internet. The parser must keep
  only block entries: a redirect entry in a poisoned list could send a bank's name to an
  attacker's address.
