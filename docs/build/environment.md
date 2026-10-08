# Build environment

## Role and trust boundary

Feature owners implement and test only in fresh clones or worktrees under `.workdirs/`, then push a branch, open a PR, and hand `FEATURE_READY` plus a proof plan to `yrrp-release-manager`. They never access the builder. The release manager reviews and merges selected PRs, records the actual merged default-branch SHAs, and is the sole owner of remote source mutation and releases.

The builder is VPN-restricted, key-only, and single-tenant. Its Docker socket grants host-root-equivalent control; that trust is accepted only within this boundary.

## Host and persistent paths

- TrueNAS: `192.168.4.243`; Docker deployment at `/mnt/fast/docker/android`.
- Builder SSH: `ssh AndroidBuilder` (`android@192.168.4.243:4242`).
- Container: `lineageos-builder`, user `android` (`950:950`), Debian 13.

| Host | Container | Purpose |
|---|---|---|
| `/mnt/fast/docker/android/workspace` | `/opt/android` | Source and output |
| `/mnt/fast/docker/android/ccache` | `/ccache` | Compiler cache |
| `/mnt/fast/docker/android/project` | `/opt/yrrp/project` read-only | Release tooling |
| `/mnt/fast/docker/android/signing` | `/opt/yrrp/signing` | Persistent keys |
| Docker socket | `/var/run/docker.sock` | Trusted release deployment and project-bind update |

The fixed release CLI updates the read-only project bind through a short-lived sibling container: it mounts the host project directory read-write only there, verifies cleanliness, fetches the requested project SHA, checks out that exact SHA detached, and exits. The live builder mount remains read-only.

## Exact source and lock

`/home/android/.yrrp-build-launch.lock` covers preparation, source synchronization, launch, and the full signed release. Lock acquisition is non-blocking. Never delete or bypass it.

After selected PR merges and merged project SHA capture, fetch origin and create a clean dedicated release worktree beneath `.workdirs/`, detached at the exact merged project SHA. Verify its status is clean. Run `prepare` and `launch` from that checkout to satisfy the launcher's local clean-HEAD prerequisite. Retain it through release completion; there is no automatic cleanup.

From that release checkout, run:

```bash
python3 scripts/yrrp-release.py prepare \
  --project-sha <actual-merged-project-sha> \
  --repo frameworks/base=<actual-merged-sha>
```

Preparation checks idle state, signing/network prerequisites, checkout cleanliness, exact scoped repository SHAs, and a revision-locked manifest. It returns the project SHA, repository SHAs, manifest, and manifest SHA-256 without building. The release manager presents that evidence for the separate **Build and release** approval, then passes the same SHAs and exact digest to `launch`.

## Detached operation and recreation

The fixed launcher runs the release in `screen` and writes `/home/android/signed-build.status`. Monitor that status plus the exact `log` path returned by `yrrp-release.py launch` (currently `/opt/android/out/signed/yrrp-ota-build-<timestamp>.log`); never guess a fixed log filename. Report the returned path and observed values, and do not infer completion from elapsed time. Source, keys, output, and ccache survive container recreation; container-layer changes do not.

Before recreation, prove the release lock is free and no build screen/process exists. Only the user runs the TrueNAS `docker compose up --force-recreate` command. Afterward verify UID/GID, Docker access, and all persistent mounts. OTA tooling refuses to replace unlabeled containers; the serving container has no host ports, keys, or Docker socket.

## Baseline

- Manifest: `https://github.com/YRRPs/android.git`, branch `lineage-23.2`
- Device/product: OnePlus 11 `salami` / `lineage_salami`
- Output: `/opt/android/out/target/product/salami/`
- `CCACHE_MAXSIZE=100G`; `nsjail` is disabled under current container restrictions.
