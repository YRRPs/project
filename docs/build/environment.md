# Build environment

## Host and container

- TrueNAS host: `192.168.4.243`
- Host deployment directory: `/mnt/fast/docker/android`
- Canonical container source: [`Yim-s-Riced-ROM-Project/android_build_server`](https://github.com/Yim-s-Riced-ROM-Project/android_build_server)
- Container SSH profile: `ssh AndroidBuilder`
- Container: `lineageos-builder`
- SSH endpoint: `192.168.4.243:4242`
- Container user: `android` (`UID:GID 950:950`)
- Container base: Debian 13 (`trixie`)

Use `AndroidBuilder` for all source, build, and container-shell work. Access TrueNAS directly only to maintain Docker configuration or lifecycle.

## Persistent paths

| TrueNAS | Container | Purpose |
| --- | --- | --- |
| `/mnt/fast/docker/android/workspace` | `/opt/android` | Source, extracted blobs, and build output |
| `/mnt/fast/docker/android/ccache` | `/ccache` | Compiler cache |
| `/mnt/fast/docker/android/project` | `/opt/yrrp/project` (read-only) | Canonical signing and deployment tooling |
| `/mnt/fast/docker/android/signing` | `/opt/yrrp/signing` | Persistent decrypted signing keys |
| Docker volume `ssh-host-keys` | `/etc/ssh/host-keys` | Stable SSH host identity |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Host Docker control for local OTA deployment |

`CCACHE_MAXSIZE=100G`; compression is disabled.

## Baseline checkout

```text
Manifest: https://github.com/Yim-s-Riced-ROM-Project/android.git
Branch: lineage-23.2
Projects: 1,164
Device: salami
Product: lineage_salami
```

Proprietary source package:

```text
https://mirrorbits.lineageos.org/full/salami/20261002/lineage-23.2-20261002-nightly-salami-signed.zip
SHA-256: e080f63ae23bb866a5234bcc1c79b6ee708ad2a210effa57851b248690784619
```

## First verified build

```text
lineage-23.2-20261003-UNOFFICIAL-salami.zip  2,180,054,279 bytes
boot.img                                      201,326,592 bytes
recovery.img                                  104,857,600 bytes
vendor_boot.img                               201,326,592 bytes
Security patch level: 2026-09-01
```

Artifacts reside under `/opt/android/out/target/product/salami/`.

## Known behavior

- `nsjail` sandboxing is disabled inside current Docker restrictions. Build succeeds without it.
- `e2fsprogs` supplies `debugfs`, required to extract EXT4 `product.img` and `system_ext.img`.
- SSH sessions need `/usr/sbin` and `/sbin` in `PATH`; Dockerfile configures this through `sshd`.
- Runtime package experiments disappear when container is recreated unless added to Dockerfile.
- Source, blobs, output, and ccache survive container recreation through bind mounts.
- Builder Docker socket access equals root authority on TrueNAS host. SSH remains VPN-restricted, key-only, and single-tenant.
- OTA release tooling refuses to replace containers not labeled as YRRP OTA resources.
- OTA serving container joins external `proxy-net`, publishes no host ports, and never receives Docker socket.
