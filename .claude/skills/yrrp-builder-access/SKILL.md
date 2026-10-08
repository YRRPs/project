---
name: yrrp-builder-access
description: Use whenever work touches the remote LineageOS build machine — "ssh AndroidBuilder", "the builder", "the build server", "/opt/android", "run breakfast/brunch/mka", "start a build in screen", "check the build", "is the build done", "tail the build log", "recreate the builder", "restart the container", "docker compose on TrueNAS", "TrueNAS", "192.168.4.243", "lineageos-builder", "screen session", "envsetup.sh fails over ssh". Covers which host does what, where paths live, how long jobs run detached in screen with status files, and how the builder container is recreated. NOT for the signing/OTA pipeline itself (yrrp-signed-ota-release), NOT for restoring keys (yrrp-signing-keys), NOT for manifest or fork changes (yrrp-manifest-and-forks).
---

# YRRP builder access

Announce first: **Using yrrp-builder-access to work on the remote builder.**

## Two hosts, two jobs

| Host | Reach it with | Use it for |
|---|---|---|
| Builder container `lineageos-builder` | `ssh AndroidBuilder` (`android@192.168.4.243:4242`) | All source, build, sign, and deploy work |
| TrueNAS host | `ssh truenas_admin@192.168.4.243` | Only `sudo docker …` in `/mnt/fast/docker/android/builder` |

Do not build, sync, or edit source on the TrueNAS host. Only `sudo docker` is passwordless there; every other `sudo` needs a password you do not have. TrueNAS `/home` is `noexec`, which is why VS Code Server never runs there.

## Paths

| Container path | TrueNAS bind source | Contents |
|---|---|---|
| `/opt/android` | `/mnt/fast/docker/android/workspace` | ~270 GB Lineage checkout, UID 950 |
| `/ccache` | `/mnt/fast/docker/android/ccache` | ccache |
| `/opt/yrrp/project` (read-only) | `/mnt/fast/docker/android/project` | clone of `YRRPs/project` |
| `/opt/yrrp/signing` | `/mnt/fast/docker/android/signing` | release keys (see yrrp-signing-keys) |
| `/var/run/docker.sock` | host socket | lets the builder deploy the OTA container |

Anything outside these mounts lives in the container layer and is lost on recreate. Signing keys were lost exactly that way once.

## Checkout mutation lock

The campaign build holds `/home/android/.yrrp-build-launch.lock` for the full signed build. Every command that can mutate `/opt/android` or `/opt/yrrp/project` must use the same lock, including `repo sync`, `git checkout`, `git switch`, `git reset`, `git pull`, cherry-pick, patch application, and scripted source edits.

Use this shape and stop if lock is busy:

```bash
ssh AndroidBuilder "flock -n /home/android/.yrrp-build-launch.lock bash -lc '<mutation command>'"
```

Never bypass, remove, or replace the lock file. Read-only inspection does not need the lock. Feature implementation must finish mutations before campaign freeze; no source mutation is allowed while a build screen exists.

## Long jobs run in screen with a status file

Never run `brunch`, `repo sync`, extraction, or signing in the foreground of an SSH call. Use this shape:

```bash
ssh AndroidBuilder 'bash -s' <<'EOF'
set -euo pipefail
sudo install -d -m 0777 -o root -g utmp /run/screen   # /run is tmpfs; screen needs this
if screen -list | grep -q '[.]<job>'; then echo '<job> screen already exists' >&2; exit 1; fi
screen -L -Logfile /home/android/<job>.log -dmS <job> /home/android/<job>.sh
screen -list | grep '[.]<job>'
EOF
```

The job script writes its phase to `/home/android/<job>.status` and traps `ERR` to write `failed:<exit>`. Poll the status file and log tail; report the status string and log tail as evidence, not "looks done".

## Shell traps on the builder

- Non-interactive SSH has no Lineage environment. Wrap: `bash -lc "cd /opt/android && source build/envsetup.sh && breakfast salami && …"`.
- `set -u` breaks `envsetup.sh`. Use `set -Eeo pipefail` in build scripts.
- Do not name a zsh variable `status` in local monitor loops; it is read-only.
- `ktfmt` is not on `PATH`: use `/opt/android/external/ktfmt/ktfmt.sh` with JDK 21 on `PATH`.

## Recreating the builder container

A local safety hook blocks the agent from running `docker compose up` on the remote. Hand the user this command to run with the `!` prefix:

```bash
! ssh truenas_admin@192.168.4.243 'set -eu; cd /mnt/fast/docker/android/builder; git pull --ff-only; sudo docker compose pull; sudo docker compose up -d --no-build --force-recreate --wait --wait-timeout 180'
```

Before asking, confirm no build is running (`screen -list`, `pgrep -af 'soong_ui|ninja'`). After it, verify: `ssh AndroidBuilder 'id; docker version; ls -ld /opt/yrrp/signing'`. The entrypoint remaps the `android` user to `ANDROID_UID`/`ANDROID_GID` (950) and exits 67 if `/opt/yrrp/signing` is not owned by that UID/GID.

The builder holds the host Docker socket, which is host-root-equivalent. The user accepted that because builder SSH is VPN-only. Do not widen builder exposure without raising this.
