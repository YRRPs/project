---
name: yrrp-signed-ota-release
description: Use from the release-manager session when merged ROM source should become an installable signed release — "make a signed build", "build and deploy an OTA", "release this", "start an OTA build", "sign the build", "publish to ota.yimura.dev", "update the OTA server", "redeploy the OTA container", "is the OTA live", "salami.json", "gapps.json", "release the gapps build", "which channel", "install images", "clean-flash files", "signing-failed", "incremental-failed", "deployment-failed", or "OTA 404". Covers prepare evidence, exact approval, fixed launch, monitoring, checksums, signatures, container/public/device verification, recovery, and receipts. NOT for feature implementation, unsigned brunch, choosing or merging PRs, general builder maintenance, or restoring signing keys.
---

# YRRP signed OTA release

Announce first: **Using yrrp-signed-ota-release to prepare, approve, launch, and prove one signed channel release.**

Invoke `yrrp-builder-access` for builder boundaries. Read `docs/build/signing.md` and `docs/design/ota-hosting.md`; the scripts are authoritative, so do not improvise their protocol.

## 1. Prepare exact merged source

After selected PR merges and merged project SHA capture, fetch origin and create a clean dedicated release worktree beneath `.workdirs/`, detached at the exact merged project SHA. Verify it is clean, then run `prepare` and `launch` from that checkout. Retain it through release completion; there is no automatic cleanup.

The release manager supplies the actual merged default-branch SHA of this project and each selected Android repository:

```bash
python3 scripts/yrrp-release.py prepare \
  --project-sha <40-hex-project-sha> \
  --repo frameworks/base=<40-hex-merged-sha> \
  --repo packages/apps/Settings=<40-hex-merged-sha>
```

Repeat `--repo PATH=SHA` only for selected repositories. `prepare` acquires the shared lock, validates signing/network/build-idle preconditions, updates the read-only project bind through a trusted Docker sibling, checks the whole checkout is clean, scoped-syncs exact requested SHAs, and returns JSON evidence including project SHA, repository SHAs, validated local-manifest filenames/hashes, revision-locked manifest text, and `manifest_sha256`. It does not start a build.

If signing validation fails, stop and invoke `yrrp-signing-keys`. If any revision or cleanliness evidence differs from the reviewed merge, stop; never substitute a nearby branch head.

## 2. Choose one channel

A release builds, signs, and deploys exactly one channel: `salami/vanilla` or `salami/gapps`. The deploy carries every other live channel over unchanged. The builder runs one release at a time, so release another channel in a separate launch after this one completes.

| | `salami/vanilla` | `salami/gapps` |
|---|---|---|
| Artifact prefix | `lineage-23.2-salami-<build-id>` | `lineage-23.2-salami-gapps-<build-id>` |
| Checksums | `lineage-23.2-salami-<build-id>-SHA256SUMS.txt` | `lineage-23.2-salami-gapps-<build-id>-SHA256SUMS.txt` |
| Full-OTA entry | `/updates/salami.json` | `/updates/salami/gapps.json` |
| Incremental entry | `/updates/salami/<incr>.json` | `/updates/salami/gapps/<incr>.json` |
| Install files | `/install/salami/<build-id>/` | `/install/salami/gapps/<build-id>/` |
| Container label | `io.yrrp.ota.channel.salami.vanilla.build-id` | `io.yrrp.ota.channel.salami.gapps.build-id` |
| Receipt | `.claude/releases/<build-id>.md` | `.claude/releases/gapps-<build-id>.md` |

Derive names instead of typing them: `python3 scripts/ota-channel.py --channel <device>/<type> <field> [values]` prints each one (`checksums`, `ota`, `install-dir`, `updates-full`, `updates-incremental`, `label`, and more; see `--help`).

## 3. Obtain one explicit approval

Present the channel, the exact returned project SHA, every repository path/SHA, validated local-manifest filename/hash pairs, and manifest SHA-256 through `AskUserQuestion` with these options:

- **Build and release** — launch exactly the prepared source.
- **Stop** — make no release mutation.

Do not launch from prose assent or from the earlier intake decision. The build decision occurs after preparation evidence exists.

## 4. Launch the same source

Only after the exact **Build and release** answer, run:

```bash
python3 scripts/yrrp-release.py launch \
  --approval 'Build and release' \
  --project-sha <same-project-sha> \
  --repo frameworks/base=<same-merged-sha> \
  --repo packages/apps/Settings=<same-merged-sha> \
  --manifest-sha256 <exact-prepare-manifest-sha256> \
  --channel <approved-device>/<approved-type>
```

`--channel` is required. Launch reacquires the shared lock, rechecks source and manifest against the approved digest, and starts the fixed signer detached in `screen` with that channel. Its JSON result returns `channel`, `log`, and `source`; confirm `channel` matches the approval. Never invoke `scripts/sign-lineage-build.sh` directly.

## 5. Monitor and verify

Monitor `/home/android/signed-build.status` (it reads `launch-starting-signer:<channel>` while the signer starts), `screen -list`, and the exact `log` path returned by `yrrp-release.py launch` (currently `/opt/android/out/signed/yrrp-ota-build-<timestamp>.log`). Do not guess or substitute a fixed log filename. The log names the channel, `YRRP_BUILD_TYPE`, and the incremental source or its `incremental-skipped:` reason. A full build takes hours; poll sparingly and report exact evidence. The pipeline exports `YRRP_BUILD_TYPE`, builds target-files and otatools, signs target-files and the full OTA, verifies certificates, generates an incremental when the channel's live build has signed target-files, prepares checksums and install images, carries every other live channel over after verifying its build ID and checksums, transactionally replaces the OTA container, and restores the prior healthy container on rollout failure.

Before calling the release successful, capture:

- `sha256sum -c` on the channel's checksum file in `/opt/android/out/signed/`: `lineage-23.2-salami-<build-id>-SHA256SUMS.txt` for vanilla, `lineage-23.2-salami-gapps-<build-id>-SHA256SUMS.txt` for gapps.
- OTA certificate equals `releasekey.x509.pem`; SystemUI signer equals `platform`.
- `docker inspect yrrp-ota-server --format '{{.State.Health.Status}} {{.Image}}'` reports healthy and the intended `yrrp-ota-release:<type>-<build-id>` image.
- `docker inspect yrrp-ota-server --format '{{ index .Config.Labels "io.yrrp.ota.channel.<device>.<type>.build-id" }}'` prints the build ID. Record that value as the receipt's `build_id_label`.
- Public HTTPS for the released channel: `/healthz` is 200; the full-OTA entry (`/updates/salami.json` or `/updates/salami/gapps.json`) names the build; the stale fallback (`/updates/salami/1.json` or `/updates/salami/gapps/1.json`) returns the same full OTA; a known incremental entry (`/updates/salami/<incr>.json` or `/updates/salami/gapps/<incr>.json`) names the incremental when one exists; the install directory (`/install/salami/<build-id>/` or `/install/salami/gapps/<build-id>/`) is 200; and a one-byte range request on each OTA is 206.
- Carried channels: before launch, save the public body of every other live channel's full-OTA and incremental entries. After the deploy, each body must be byte-identical. The deployer already refuses to swap when a carried route changes; the public comparison is the receipt's `routes_unchanged` evidence.
- Device proof claims run serially using their trigger, observable, expected result, limitation, and restoration fields. Skip TalkBack and accessibility acceptance for this personal ROM.

Assign every claim exactly one verdict: `PROVEN`, `FAILED`, or `UNPROVEN`. Local evidence never proves a device-only boundary.

## 6. Recovery without rebuild

For an incremental-generation or deployment-only retry, the release manager directly invokes the fixed incremental generator or deployer with exact existing artifacts under `/opt/android/out/signed/`:

```bash
ssh AndroidBuilder '/opt/yrrp/project/scripts/generate-incremental-ota.sh --channel <device>/<type> --source-build <source-id> --target-build <target-id>'
ssh AndroidBuilder '/opt/yrrp/project/scripts/deploy-ota-release.sh --channel <device>/<type> --ota /opt/android/out/signed/<ota> --target-files /opt/android/out/signed/<target-files> --build-id <build-id> [--incremental /opt/android/out/signed/<incremental>]'
```

Always pass the release's `--channel`. Both scripts default to `salami/vanilla` without it, which would mislabel a gapps release.

The invoked script non-blockingly acquires and holds `/home/android/.yrrp-build-launch.lock` for the entire operation and refuses if the lock is busy. Prior read-only inspection may diagnose an active release, but it is not the locking mechanism and never authorizes a retry. Run the chosen retry detached, preserve its exit code and output, and repeat the affected checksum/container/public checks. Do not rebuild or re-sign for an OTA-server-only change. A redeploy of a release that already has an incremental must pass the same incremental again.

## 7. Immutable receipt

Feed the completed release evidence to the fixed receipt command and require its returned path:

```bash
python3 scripts/yrrp-release.py receipt --input <private-json-file-or-->
```

The receipt is private and no-overwrite at `.claude/releases/<build-id>.md` for vanilla and `.claude/releases/<type>-<build-id>.md` for other types; the JSON input file is preserved for correction and audit. Set `release_identity.channel` to the released channel. A `SUCCESS` receipt with a channel also requires `deployment_public_checks.carried_channels`: one `{"channel", "build_id", "routes_unchanged"}` entry per carried channel, or `[]` when none was live. Every `routes_unchanged` must be `true`. The receipt command checks artifact names and public-check paths against the channel. It records selected PRs, tested and merged patch IDs, actual merged SHAs, validated local-manifest hashes plus the complete revision-locked manifest XML and digest from prepare, approval, build identity, artifacts, checksum/signature/container/public/device evidence, all `PROVEN`/`FAILED`/`UNPROVEN` claims, limitations, restoration, and unresolved gaps. A progress note is not the final result.
