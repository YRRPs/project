# Builder check tooling

Date: 2026-10-15

## Goal

Replace ad hoc builder scripts with fixed, typed release-manager commands for source checks, status monitoring, pre-approval evidence, and rollback. Catch compile and test failures before a full signed build without weakening exact-source release controls.

## Decisions

- Extend the existing `scripts/yrrp-release.py` lifecycle instead of adding shell wrappers or a separate CI runner.
- Keep the release manager as the only role that can access or mutate builder state.
- Permit unmerged PR checks only in a separate non-release checkout.
- Require selected merged-source checks to pass before build approval.
- Implement `check`, `wait`, pre-approval gating, `atest` support, and rollback in one change.
- Keep `/opt/android` as the only release workspace.
- Use fixed target identifiers and fixed argv. Never accept arbitrary shell commands.

## Command contract

### List targets

```bash
python3 scripts/yrrp-release.py targets
```

The command returns JSON describing supported devices and fixed target identifiers. Target registry entries contain an executor (`m` or `atest`) and one fixed argument. The public CLI never accepts an executable, script, shell fragment, or additional build argument.

Initial compile targets:

- `m:SystemUI-core`
- `m:SystemUI`
- `m:SettingsProvider`
- `m:ThemePicker`
- `m:monet`
- `m:Settings`
- `m:SettingsRoboTests`

Initial test targets:

- `atest:monet_tests`
- `atest:ThemeOverlayApplierTest`
- `atest:ThemePickerRoboTests`
- `atest:SystemUITests_presubmit`
- `atest:SettingsProviderTest`
- `atest:SystemUiRoboTests`

Supported devices are `salami` and `xueying`.

### Start a check

```bash
python3 scripts/yrrp-release.py check \
  --workspace isolated \
  --project-sha <project-sha> \
  --repo packages/apps/ThemePicker=<pr-head-sha> \
  --device salami \
  --target m:ThemePicker \
  --target atest:ThemePickerRoboTests
```

`--workspace` is required:

- `isolated` runs an unmerged or merged source check in the non-release checkout.
- `release` runs a merged-source check in `/opt/android` and can satisfy the release gate.

The command validates the local project checkout, project SHA, repository paths and SHAs, device, target identifiers, and duplicate targets before contacting the builder. It returns JSON containing generated run ID, workspace, status path, log path, requested source, device, and targets after the detached worker acknowledges launch.

### Wait for a job

```bash
python3 scripts/yrrp-release.py wait --run-id <run-id>
```

The caller supplies only a generated run ID. The remote implementation derives status and log paths beneath fixed roots. It emits each new phase as one JSON line, emits the final result, and exits with the worker result. It includes bounded diagnostic lines on failure.

`wait` supports check and rollback jobs. It performs read-only inspection and does not acquire the release lock.

### Gate preparation

```bash
python3 scripts/yrrp-release.py prepare \
  --project-sha <project-sha> \
  --repo <path>=<merged-sha> \
  --require-check <run-id> \
  --require-target m:ThemePicker \
  --require-target atest:ThemePickerRoboTests
```

Preparation performs its existing exact-source synchronization and validation. It then loads the required check result and rejects it unless all these conditions hold:

- The run used the `release` workspace.
- The run completed successfully.
- Every `--require-target` appears in the check result and completed successfully.
- Every required `atest` target executed tests rather than stopping after compilation or reporting zero tests.
- At least one required target was supplied; an empty check cannot satisfy the gate.
- The project SHA, repository path/SHA map, and revision-locked manifest SHA-256 match the freshly prepared source.
- The check evidence has the expected schema and is stored under the fixed check-results root.

Preparation returns the validated check evidence beside its existing source evidence. The release manager shows both before asking for `Build and release` approval.

### Roll back a deployed channel

```bash
python3 scripts/yrrp-release.py rollback \
  --approval 'Rollback salami/gapps to 20261013-151101' \
  --channel salami/gapps \
  --build-id 20261013-151101
```

The exact approval literal names the channel and build ID. The command accepts no artifact paths. It launches the existing transactional deployer with artifacts resolved from an immutable deployment record.

For a build created before deployment records exist, the release manager may add `--receipt-input <JSON-FILE>`. The local CLI validates the receipt input, extracts only expected artifact basenames and SHA-256 values, and sends a normalized record candidate. The remote worker verifies every artifact beneath `/opt/android/out/signed`, validates the optional incremental metadata, and creates the deployment record with no-overwrite semantics before rollback.

Rollback starts detached, returns a run ID and trusted evidence paths, and completes through `wait`. The deployer still restores the previously healthy container if candidate rollout fails.

## Architecture

### Local CLI

`scripts/yrrp-release.py` owns argument parsing and dispatch. It delegates validation, remote invocation, and result handling to focused modules beneath `scripts/yrrp_release/`.

The public handlers remain short. Target registry, run identifiers, check-result validation, deployment-record validation, and remote script construction live in separate modules when keeping them in `launcher.py` would breach its existing size and responsibility boundaries.

### Generic job protocol

Checks and rollbacks share one job protocol:

- Generated run ID with kind prefix and strict format: `check-<UTC timestamp>-<random hex>` or `rollback-<UTC timestamp>-<random hex>`.
- Kind: `check` or `rollback`, derived from the validated prefix.
- Atomic JSON status file.
- Persistent log file.
- Detached `screen` session.
- Worker acknowledgment before the start command returns.
- Start and completion timestamps in UTC.
- Phase, action, object, result, exit code, and failure reason.
- Final immutable result document.

The worker writes status to a temporary file, sets mode `0600`, and atomically replaces the visible status path. It writes the final result with no-overwrite semantics. The start command rejects a run ID collision instead of reusing evidence.

Check logs and results live under `/opt/android/out/yrrp-checks/<run-id>/`. Rollback logs and results live under `/opt/android/out/yrrp-rollbacks/<run-id>/`. `wait` derives the fixed root from the validated kind prefix; it never accepts a path.

### Shared lock

The worker acquires `/home/android/.yrrp-build-launch.lock` non-blockingly before any source synchronization, compile, test, or deployment mutation. It holds the same descriptor until the operation exits. Checks, preparation, release launch, incremental retries, deployment retries, and rollback remain mutually exclusive.

Read-only `wait` calls do not acquire or bypass the lock.

### Release workspace

A `release` check reuses existing exact-source functions against `/opt/android`:

1. Reject an active release or check.
2. Validate the whole Android checkout is clean.
3. Synchronize only requested repositories to exact SHAs.
4. Capture project SHA, repository SHAs, local-manifest hashes, revision-locked manifest, and manifest digest.
5. Source `build/envsetup.sh` and run `breakfast <device>`.
6. Run the fixed targets.
7. Revalidate source cleanliness and capture final source evidence.

Any source drift or generated source-file change fails the check.

### Isolated workspace

An `isolated` check runs in a disposable sibling container using the trusted builder image. The container mounts:

- `/mnt/fast/docker/android/check-workspace` at `/opt/android` read-write.
- `/mnt/fast/docker/android/ccache` at `/ccache` read-write.
- `/mnt/fast/docker/android/project` at `/opt/yrrp/project` read-only.
- The check result directory at its fixed path read-write.

The check checkout is initialized from `https://github.com/YRRPs/android.git`, branch `lineage-23.2`, when absent. Each run verifies manifest cleanliness and syncs requested repositories. For an unmerged commit, it fetches the exact SHA from that manifest project's existing canonical remote without accepting a caller-supplied URL, verifies the commit object, checks it out detached, and validates source evidence before building. Its source and `out/` never share the release workspace.

The sibling container exits after the check. The persistent isolated checkout and its `out/` remain cached for later checks. A failed or dirty checkout blocks the next check; the tool never silently cleans or resets unexpected state.

### Target execution

The worker sources the Android build environment once and runs `breakfast` once. It combines requested `m` targets into one fixed argv invocation. It runs each `atest` target separately so each suite receives an independent verdict.

Each result records target IDs, exact argv, start/end timestamps, exit code, and bounded summary/error lines. The log retains complete output. The result never stores signing keys, credentials, environment dumps, or unrelated file contents.

An `m` success proves compilation only. An `atest` success requires exit zero and positive executed-test evidence. Exit zero with zero tests, no test execution, or compile-only completion fails the target.

## Pre-approval workflow

1. A feature owner includes required builder target IDs in `FEATURE_READY` proof evidence.
2. The release manager starts an `isolated` check against the tested PR head before merge.
3. The release manager waits for the result and returns failures to the feature owner.
4. After review and merge, the release manager starts the same required targets in the `release` workspace against actual merged SHAs.
5. The release manager waits for success.
6. The release manager runs `prepare --require-check <run-id>` with every declared `--require-target` against the same merged SHAs.
7. Preparation rejects stale or mismatched evidence.
8. The approval prompt shows source digest and target verdicts.
9. Launch remains unchanged and still rechecks approved source before signing.

Target inference from changed paths is out of scope. Explicit target IDs avoid unreliable module inference and make handoff evidence reviewable.

## Deployment records and rollback

Every successful deployment writes an immutable record beneath:

```text
/opt/android/out/signed/deployments/<device>/<type>/<build-id>.json
```

The record contains:

- Schema version.
- Channel and build ID.
- Full OTA basename, size, and SHA-256.
- Signed target-files basename, size, and SHA-256.
- Checksum-manifest basename and SHA-256.
- Optional incremental basename, size, SHA-256, and source incremental.
- Creation timestamp.
- Deployer project SHA when available.

The deployer prepares the record before replacing the OTA container and publishes it only after deployment and route verification succeed. It uses a temporary file, mode `0600`, atomic rename, and no-overwrite semantics. A repeated deployment must match the existing record byte-for-byte.

Rollback validates channel-derived expected names, record schema, file containment beneath signed output, sizes, digests, checksum output, and incremental metadata. It then invokes `deploy-ota-release.sh` using fixed argv and explicit `--channel`.

Rollback success requires healthy container state, expected channel build label, full metadata route, install listing, full OTA range support, optional incremental route/range support, and unchanged carried-channel routes. These checks already exist in the deployment pipeline and remain authoritative.

## Receipt evidence

The receipt schema gains an optional `source_checks` section for compatibility with historical receipt corrections. The release-manager contract requires this section for new releases.

Each source-check entry records:

- Run ID and workspace.
- Device.
- Project SHA, repository SHAs, and manifest SHA-256.
- Requested target IDs.
- Per-target type, exact argv, result, exit code, and executed-test count when applicable.
- Start/end timestamps.
- Log path.

The renderer labels compile-only evidence as compilation. It labels `atest` evidence as test execution only when executed-test count is positive.

## Error handling

- Validation errors return CLI exit code 2 and no remote mutation.
- Busy lock, dirty source, unknown target, source mismatch, missing artifact, checksum mismatch, zero-test `atest`, and stale evidence fail closed.
- Detached launch waits only for worker acknowledgment. Missing acknowledgment, vanished screen, timeout, or malformed JSON returns concise failure evidence and the trusted log path.
- `wait` exits nonzero for a failed worker and preserves the final status/result.
- Rollback failure reports whether the candidate failed and whether the previous container was restored.
- Cleanup never deletes source, logs, final results, signed artifacts, deployment records, or the previously healthy container.

## Security controls

Operator values flow from local CLI arguments into JSON over SSH, then into fixed source selection, target selection, status lookup, or deployment lookup. The implementation separates commands from data with argv arrays and validates every selector against a positive format or fixed registry. It never interpolates operator data into shell text. This follows `OS_Command_Injection_Defense_Cheat_Sheet#defense-option-3-parameterization-in-conjunction-with-input-validation`.

Status and logs record when, where, action, object, result, and reason. They omit credentials and signing material, use restricted permissions, and live on trusted persistent storage. This follows `Logging_Cheat_Sheet#event-attributes` and `Logging_Cheat_Sheet#protection`.

Source and deployment evidence bind builder inputs, parameters, and artifact digests. This follows `Dependency_Graph_SBOM_Cheat_Sheet#bind-and-verify-release-evidence`. Isolated PR checks do not become release provenance; only exact merged-source checks can satisfy the release gate.

## Verification

### Pure and structural tests

- Validate target IDs, devices, run IDs, build IDs, channels, paths, SHAs, and duplicates.
- Prove registry entries generate fixed argv and cannot carry shell metacharacters or extra arguments.
- Exercise job-state transitions and atomic/no-overwrite behavior.
- Exercise source-evidence matching and every stale/mismatch rejection.
- Exercise `atest` parsing for positive, zero-test, compile-only, malformed, and failed outputs.
- Validate deployment and legacy receipt-derived records.
- Validate receipt rendering distinguishes compile and executed-test evidence.

### Integration tests

- Use fake SSH/subprocess runners to verify JSON transport and `shell=False`.
- Execute extracted remote functions against temporary directories and fake `screen`, Docker, repo, build, and deploy commands.
- Verify shared-lock lifetime, detached acknowledgment, status transitions, log/result paths, and failure tails.
- Verify isolated and release workspace commands use distinct mounts and output roots.
- Verify rollback resolves record artifacts and invokes deployer with explicit channel and fixed argv.
- Run existing release launcher, receipt, signer, deployer, source-policy, and agent-contract suites.

### Target-runtime validation

After local tests pass, a release-manager session provisions the isolated workspace and runs:

- One compile target, initially `m:ThemePicker`.
- One test target, initially `atest:ThemePickerRoboTests`.
- `wait` through successful phase transitions.
- One intentional unknown-target rejection without remote mutation.

A real rollback changes an externally visible OTA channel. It requires separate approval naming a real channel and build ID, so implementation completion does not perform one automatically.

## Documentation changes

Update these contracts with fixed commands and evidence rules:

- `README.md`
- `docs/build/environment.md`
- `docs/build/signing.md`
- `docs/design/downstream-workflow.md`
- `.claude/agents/yrrp-feature-owner.md`
- `.claude/agents/yrrp-release-manager.md`
- `.claude/skills/yrrp-builder-access/SKILL.md`
- `.claude/skills/yrrp-signed-ota-release/SKILL.md`
- Relevant agent and source-policy tests

## Out of scope

- Direct builder access for feature owners.
- Arbitrary build commands, module names, test filters, or remote paths.
- Parallel checks or checks concurrent with a release.
- Automatic target inference from changed files.
- Promotion of isolated PR-head evidence into release approval.
- Automatic destructive cleanup of a dirty isolated checkout.
- A real production rollback without separate channel/build approval.
