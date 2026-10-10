# Release signing

## Key continuity

The release-key set lives persistently at `/opt/yrrp/signing`; encrypted backup and passphrase are stored separately. Never commit keys, password maps, archives, or decrypted material. Preserve the same keys for the installed-ROM lifetime because replacement keys break OTA continuity.

Restore only through the `yrrp-signing-keys` procedure, verify `MANIFEST.sha256`, require private permissions, and confirm `testkey` symlinks point to `releasekey`.

## Approved release flow

One `yrrp-release-manager` session owns review, merge, exact source, build approval, release proof, and receipt. Feature owners provide local evidence and proof plans but have no builder access.

1. Merge selected PRs and record each actual merged default-branch SHA.
2. Run `python3 scripts/yrrp-release.py prepare --project-sha <sha> --repo <path>=<sha>`.
3. Inspect returned project SHA, repository SHAs, validated local-manifest filename/hash pairs, revision-locked manifest, and manifest SHA-256.
4. Ask through `AskUserQuestion` with **Build and release** and **Stop**. Name the channel to release in the question.
5. Only after **Build and release**, run `python3 scripts/yrrp-release.py launch --approval 'Build and release' --channel <device>/<type>` with the same SHA arguments and exact `--manifest-sha256` from prepare.
6. Monitor the detached status/log, verify artifacts and service, run device proof plans serially, and write the receipt through the fixed receipt command. Preserve the input JSON, tested/merged patch-ID evidence, and complete revision-locked manifest XML.

Both prepare and launch use `/home/android/.yrrp-build-launch.lock`. Launch rechecks source and manifest before starting. Never call `sign-lineage-build.sh` directly.

## Channels

A release builds, signs, and deploys exactly one channel. A channel is `<device>/<type>`. Launch requires `--channel` and accepts only `salami/vanilla` and `salami/gapps`. The builder runs one release at a time, so release another channel in a separate launch.

The launcher passes the channel to `sign-lineage-build.sh --channel`. The signer exports `YRRP_BUILD_TYPE=<type>` before `breakfast`, and `android_vendor_extra` selects the type from it. When `YRRP_BUILD_TYPE` is unset, the build is `vanilla`. The status file reads `launch-starting-signer:<channel>` while the signer starts, and the log names the channel and `YRRP_BUILD_TYPE`.

`vanilla` keeps the original artifact names. Other types insert `-<type>` after the device:

| Artifact | `salami/vanilla` | `salami/gapps` |
|---|---|---|
| Signed target-files | `lineage-23.2-salami-<id>-signed-target_files.zip` | `lineage-23.2-salami-gapps-<id>-signed-target_files.zip` |
| Full OTA | `lineage-23.2-salami-<id>-signed-ota.zip` | `lineage-23.2-salami-gapps-<id>-signed-ota.zip` |
| Incremental OTA | `lineage-23.2-salami-<src>-to-<id>-signed-incremental-ota.zip` | `lineage-23.2-salami-gapps-<src>-to-<id>-signed-incremental-ota.zip` |
| Checksums | `lineage-23.2-salami-<id>-SHA256SUMS.txt` | `lineage-23.2-salami-gapps-<id>-SHA256SUMS.txt` |
| Receipt | `.claude/releases/<id>.md` | `.claude/releases/gapps-<id>.md` |

`scripts/yrrp_ota/channel.py` owns every channel-derived name, route, and label. Shell scripts read them through `scripts/ota-channel.py`, for example `python3 scripts/ota-channel.py --channel salami/gapps ota <build-id>`.

Each channel's incremental source is that channel's live build, read from the `io.yrrp.ota.channel.<device>.<type>.build-id` label on the live OTA container. A release never deletes another channel's signed target-files.

## Pipeline

The signer exports `YRRP_BUILD_TYPE`, runs `breakfast salami` and `mka target-files-package otatools`, signs target-files, creates the full OTA, verifies OTA and SystemUI certificates, generates an incremental from the channel's live source when available, extracts six matching install images, generates updater metadata and checksums, builds a release image that holds the new channel plus every other live channel carried over unchanged, and replaces the OTA container transactionally. A failed rollout restores the previous healthy release and preserves diagnostic artifacts.

Required completion evidence:

- checksum file passes `sha256sum -c`;
- OTA certificate matches `releasekey.x509.pem`;
- SystemUI signer matches `platform`;
- OTA metadata is A/B, `release-keys`, `salami`, `UNOFFICIAL`, and LineageOS 23.2, and target-files `ro.yrrp.build.type` equals the channel's type;
- the OTA container is healthy on the intended image;
- public health, metadata, install listing, and byte-range checks pass for the released channel;
- every carried channel serves byte-identical updater routes before and after the deploy;
- each proof claim receives `PROVEN`, `FAILED`, or `UNPROVEN`, with observations, limitations, and restoration.

Skip TalkBack and accessibility acceptance for this personal ROM.

## Signing profile

Every release signs through the `YRRPs/android_build` fork. That fork records where `sign_target_files_apks` spends its time, and writes the results to `/opt/android/out/signed/profile/<type>-<build-id>/`, for example `profile/gapps-20261010-120000/`:

| File | Contents |
|---|---|
| `timeline.jsonl` | One line per step: `load-keys`, `process-target-files`, `zip-close`, `add-img-to-target-files`. Each line gives wall time, own CPU, child-process CPU, peak RSS, page faults, bytes read and written, and context switches. |
| `samples.jsonl` | One line per second: RSS, swap, used bytes on the temp filesystem, and page-fault totals. |
| `signing.prof` | The raw `cProfile` dump. |
| `signing-top.txt` | The top 40 functions by self time, then the top 40 by cumulative time. |
| `summary.json` | Core count, Python version, total wall time, all steps, and peak RSS, swap, and temp usage. |

To read the results:

- Compare `wall_s`, `cpu_self_s`, and `cpu_children_s` in each step. When wall time sits well above CPU time, the step waits on disk or something else.
- Read `signing-top.txt` first. For a deeper look, run `python3 -m pstats signing.prof`, then `sort tottime` and `stats 40`.
- Check `samples.jsonl` for swap above zero, or for RSS close to the container's memory limit.

Limits:

- `read_bytes` and `write_bytes` cover only the signing process. Child tools such as `mkfs.erofs` are not included.
- `tmp_used_bytes` counts every writer on the temp filesystem, not only signing.
- `cProfile` slows Python code. Compare against the unprofiled 848 s signing phase of release `20261009-092107` (issue #25) to see the overhead.
- Profiling never fails a release. If the profiler cannot write, it prints `YRRP signing profile disabled:` to stderr and signing continues.

## Recovery

An incremental or deployment retry does not require rebuilding. The release manager directly invokes the fixed incremental generator or deployer against existing `/opt/android/out/signed/` artifacts, with the release's `--channel`. The invoked script non-blockingly acquires and holds `/home/android/.yrrp-build-launch.lock` for the entire operation and refuses if the lock is busy. Prior read-only inspection may diagnose an active release, but it is not the locking mechanism and never authorizes a retry. Run the script detached with captured exit/output and repeat the affected verification. An OTA-server-only change must not rebuild or re-sign.

## Clean installation

OnePlus 11 requires current stock Android 16 firmware. Flash the six matching signed images from the same target-files output, boot matching recovery, format data, and sideload the signed full OTA. All files must come from one type; see `docs/install/clean-install.md`. Never mix images from an earlier build. Keep the bootloader unlocked.
