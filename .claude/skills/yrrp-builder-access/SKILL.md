---
name: yrrp-builder-access
description: Use whenever release-manager work touches the remote LineageOS builder — "ssh AndroidBuilder", "the builder", "the build server", "/opt/android", "sync merged SHAs", "run breakfast or mka", "start a build in screen", "check build status", "tail the build log", "recreate the builder", "restart the container", "Docker on TrueNAS", "192.168.4.243", or "lineageos-builder". Covers exact merged-source synchronization, the shared release lock, detached jobs, status evidence, the read-only project bind, and safe container recreation. NOT for feature implementation, feature-owner sessions, signing-key restoration, manifest design, or deciding which PRs to merge.
---

# YRRP builder access

Announce first: **Using yrrp-builder-access to operate the remote builder from the release-manager session.**

Only the `yrrp-release-manager` invokes this skill. Feature owners work locally in `.workdirs/` and never call it. The release manager owns every remote mutation and supplies actual merged default-branch SHAs; do not infer revisions or sync unselected projects.

## Hosts and paths

| Host | Access | Purpose |
|---|---|---|
| `lineageos-builder` | `ssh AndroidBuilder` (`android@192.168.4.243:4242`) | Source sync, build, sign, deploy, status |
| TrueNAS | `ssh truenas_admin@192.168.4.243` | Docker lifecycle only in `/mnt/fast/docker/android/builder` |

| Container path | Host source | Contract |
|---|---|---|
| `/opt/android` | `/mnt/fast/docker/android/workspace` | Lineage checkout and output |
| `/ccache` | `/mnt/fast/docker/android/ccache` | Compiler cache |
| `/opt/yrrp/project` | `/mnt/fast/docker/android/project` | Project checkout, mounted read-only |
| `/opt/yrrp/signing` | `/mnt/fast/docker/android/signing` | Persistent release keys |
| `/var/run/docker.sock` | host socket | Trusted host Docker control |

The builder's Docker socket is host-root-equivalent. It is accepted only for this VPN-restricted, key-only, single-tenant builder. The fixed release CLI updates the read-only `/opt/yrrp/project` bind safely by starting a short-lived sibling container through that trusted socket, mounting `/mnt/fast/docker/android/project` read-write only in the sibling, checking cleanliness, fetching the requested project SHA, and detaching exactly at it. This update and all source sync happen while the same shared lock is held. Never remount the live builder path read-write or edit it in place.

## Shared release lock

`/home/android/.yrrp-build-launch.lock` serializes preparation, source mutation, launch, and the full signed release. The fixed `python3 scripts/yrrp-release.py prepare ...` and `launch ...` commands acquire it non-blockingly and return evidence. For exceptional manual source maintenance, use:

```bash
ssh AndroidBuilder "flock -n /home/android/.yrrp-build-launch.lock bash -lc '<one scoped mutation>'"
```

Stop if busy. Never remove, replace, or bypass the lock. Read-only inspection is lock-free. Direct `repo sync`, checkout, reset, pull, cherry-pick, patching, and scripted edits are forbidden outside release-manager-controlled lock ownership.

## Build, screen, and evidence

The fixed launcher creates the detached `yrrp-ota-build` screen and keeps the lock descriptor in the signing process. Do not start the signer directly. Monitor `/home/android/signed-build.status`, `screen -list`, and the exact `log` path returned by `yrrp-release.py launch` (currently `/opt/android/out/signed/yrrp-ota-build-<timestamp>.log`). Do not guess or substitute a fixed log filename. Use the returned path for the tail command:

```bash
ssh AndroidBuilder "cat /home/android/signed-build.status; screen -list; tail -n 40 '<launch-log>'"
```

Report the exact status, screen listing, returned log path, and relevant log tail. Expected phases include build, signing, artifact verification, incremental generation, release preparation, deployment, and `complete`; failures carry a phase and exit code.

For any other long maintenance job, use detached `screen`, a dedicated log, a status file, and an `ERR` trap. Never keep `brunch`, `repo sync`, extraction, signing, or deployment attached to an SSH session.

Non-interactive shells need `bash -lc`, `/opt/android`, `source build/envsetup.sh`, and `breakfast salami`. Do not combine `set -u` with `envsetup.sh`. Use `/opt/android/external/ktfmt/ktfmt.sh` with JDK 21 when formatting Kotlin.

## Container recreation safety

Before recreation, require lock-free state plus no build screen or build/sign process. Give the user this explicit local command; do not run it as the agent:

```bash
! ssh truenas_admin@192.168.4.243 'set -eu; cd /mnt/fast/docker/android/builder; git pull --ff-only; sudo docker compose pull; sudo docker compose up -d --no-build --force-recreate --wait --wait-timeout 180'
```

Afterward verify `ssh AndroidBuilder 'id; docker version; ls -ld /opt/android /opt/yrrp/project /opt/yrrp/signing'`. Persistent binds survive recreation; container-layer experiments do not. The entrypoint exits 67 if signing ownership does not match UID/GID 950.
