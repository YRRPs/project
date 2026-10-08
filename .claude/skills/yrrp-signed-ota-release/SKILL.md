---
name: yrrp-signed-ota-release
description: Use from the release-manager session when merged ROM source should become an installable signed release — "make a signed build", "build and deploy an OTA", "release this", "start an OTA build", "sign the build", "publish to ota.yimura.dev", "update the OTA server", "redeploy the OTA container", "is the OTA live", "salami.json", "install images", "clean-flash files", "signing-failed", "incremental-failed", "deployment-failed", or "OTA 404". Covers prepare evidence, exact approval, fixed launch, monitoring, checksums, signatures, container/public/device verification, recovery, and receipts. NOT for feature implementation, unsigned brunch, choosing or merging PRs, general builder maintenance, or restoring signing keys.
---

# YRRP signed OTA release

Announce first: **Using yrrp-signed-ota-release to prepare, approve, launch, and prove one signed release.**

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

## 2. Obtain one explicit approval

Present the exact returned project SHA, every repository path/SHA, validated local-manifest filename/hash pairs, and manifest SHA-256 through `AskUserQuestion` with these options:

- **Build and release** — launch exactly the prepared source.
- **Stop** — make no release mutation.

Do not launch from prose assent or from the earlier intake decision. The build decision occurs after preparation evidence exists.

## 3. Launch the same source

Only after the exact **Build and release** answer, run:

```bash
python3 scripts/yrrp-release.py launch \
  --approval 'Build and release' \
  --project-sha <same-project-sha> \
  --repo frameworks/base=<same-merged-sha> \
  --repo packages/apps/Settings=<same-merged-sha> \
  --manifest-sha256 <exact-prepare-manifest-sha256>
```

Launch reacquires the shared lock, rechecks source and manifest against the approved digest, and starts the fixed signer detached in `screen`. Never invoke `scripts/sign-lineage-build.sh` directly.

## 4. Monitor and verify

Monitor `/home/android/signed-build.status`, `screen -list`, and the exact `log` path returned by `yrrp-release.py launch` (currently `/opt/android/out/signed/yrrp-ota-build-<timestamp>.log`). Do not guess or substitute a fixed log filename. A full build takes hours; poll sparingly and report exact evidence. The pipeline builds target-files and otatools, signs target-files and the full OTA, verifies certificates, generates an incremental when a valid live source exists, prepares checksums and install images, transactionally replaces the OTA container, and restores the prior healthy container on rollout failure.

Before calling the release successful, capture:

- `sha256sum -c /opt/android/out/signed/lineage-23.2-salami-<build-id>-SHA256SUMS.txt`.
- OTA certificate equals `releasekey.x509.pem`; SystemUI signer equals `platform`.
- `docker inspect yrrp-ota-server --format '{{.State.Health.Status}} {{.Image}}'` reports healthy and the intended image.
- Public HTTPS: `/healthz` is 200, `/updates/salami.json` names the build, `/install/salami/<build-id>/` is 200, and a one-byte OTA range request is 206.
- Device proof claims run serially using their trigger, observable, expected result, limitation, and restoration fields. Skip TalkBack and accessibility acceptance for this personal ROM.

Assign every claim exactly one verdict: `PROVEN`, `FAILED`, or `UNPROVEN`. Local evidence never proves a device-only boundary.

## 5. Recovery without rebuild

For an incremental-generation or deployment-only retry, the release manager directly invokes the fixed incremental generator or deployer with exact existing artifacts under `/opt/android/out/signed/`:

```bash
ssh AndroidBuilder '/opt/yrrp/project/scripts/generate-incremental-ota.sh --source-build <source-id> --target-build <target-id>'
ssh AndroidBuilder '/opt/yrrp/project/scripts/deploy-ota-release.sh --ota /opt/android/out/signed/<ota> --target-files /opt/android/out/signed/<target-files> --build-id <build-id> [--incremental /opt/android/out/signed/<incremental>]'
```

The invoked script non-blockingly acquires and holds `/home/android/.yrrp-build-launch.lock` for the entire operation and refuses if the lock is busy. Prior read-only inspection may diagnose an active release, but it is not the locking mechanism and never authorizes a retry. Run the chosen retry detached, preserve its exit code and output, and repeat the affected checksum/container/public checks. Do not rebuild or re-sign for an OTA-server-only change. A redeploy of a release that already has an incremental must pass the same incremental again.

## 6. Immutable receipt

Feed the completed release evidence to the fixed receipt command and require its returned path:

```bash
python3 scripts/yrrp-release.py receipt --input <private-json-file-or-->
```

The receipt is private and no-overwrite at `.claude/releases/<build-id>.md`; the JSON input file is preserved for correction and audit. It records selected PRs, tested and merged patch IDs, actual merged SHAs, validated local-manifest hashes plus the complete revision-locked manifest XML and digest from prepare, approval, build identity, artifacts, checksum/signature/container/public/device evidence, all `PROVEN`/`FAILED`/`UNPROVEN` claims, limitations, restoration, and unresolved gaps. A progress note is not the final result.
