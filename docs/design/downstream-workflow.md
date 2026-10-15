# Downstream source workflow

## Simple role split

A feature owner handles one feature locally. Reuse one canonical clone per repository—the project root for `project`, or `.workdirs/repos/<repository>` for another repository—and clone only when that cache is missing. Under a short `.workdirs/locks/<repository>.lock`, verify origin and cleanliness, fetch/prune the current symbolic default branch, and add a per-feature worktree at `.workdirs/features/<feature-id>/<repository>`. Never implement in the canonical clone. Branch from the fetched default SHA, implement with TDD, run local checks, review, push, and open a PR. The handoff is `FEATURE_READY` with PR/repository/branch/base/tested-head evidence, fixed builder target IDs, acceptance criteria, and a structured proof plan. Feature owners never access the builder.

The `yrrp-release-manager` owns integration and release: after each handoff it asks **Keep release open** or **This is the last feature**; it runs PR heads through `--workspace isolated`, reviews and merges only selected PRs, captures actual merged SHAs, repeats required targets through `--workspace release`, prepares exact source with matched check evidence, obtains separate **Build and release** approval, launches, verifies, coordinates serial device proof, and writes an immutable receipt.

## Source topology

Initialize from `YRRPs/android` branch `lineage-23.2`. The manifest retains LineageOS as baseline and routes only modified projects to YRRP forks. Use product configuration or overlays before creating a source fork.

Current durable repositories include `project`, `android`, `android_frameworks_base`, `android_packages_apps_Settings`, `android_vendor_extra`, `android_build_server`, and `ota_server`. Feature branches are short-lived; `lineage-23.2` is each source repository's reviewed integration branch.

## Merge and exact builder sync

Never release a feature branch or guessed remote head. Before merge, list allowlisted IDs with `python3 scripts/yrrp-release.py targets`, run declared targets with `check --workspace isolated`, and monitor with `python3 scripts/yrrp-release.py wait --run-id <run-id>`. This isolated checkout catches failures without becoming release source.

After selected PR merges and merged project SHA capture, fetch origin and create a clean dedicated release worktree beneath `.workdirs/`, detached at the exact merged project SHA. Verify `git status --porcelain` is empty, repeat required targets with `check --workspace release`, and wait for success. Run `prepare` and `launch` from that checkout. Retain it through release completion; there is no automatic cleanup.

From that checkout, bind preparation to the release-workspace result:

```bash
python3 scripts/yrrp-release.py prepare \
  --require-check <release-check-run-id> \
  --require-target <fixed-id> \
  --device <device> \
  --project-sha <actual-project-sha> \
  --repo frameworks/base=<actual-merged-sha> \
  --repo packages/apps/Settings=<actual-merged-sha>
```

Preparation acquires `/home/android/.yrrp-build-launch.lock`, updates the read-only project bind through a trusted Docker sibling, refuses dirty source, scoped-syncs requested paths, and creates a revision-locked manifest. It rejects isolated, failed, stale, mismatched, or incomplete checks. The returned source and source-check evidence are the approval basis. Launch must reuse those exact source values.

## Destructive sync safety

Before a path-scoped `--force-sync`, prove unique work is published, create and verify a Git bundle backup, and ask through `AskUserQuestion` with **Force-sync only `<path>`** and **Stop**. Never run unscoped force sync, `repo sync -d`, `git reset --hard`, or `git clean` on the builder.

## Proof and durable records

Local checks document what they cannot prove. Proof plans name claim, trigger/setup, observable evidence, expected outcome, cheapest proving layer, limitations, post-release device check, and restoration. Device-only boundaries remain unproven until observed on the target device. Skip TalkBack and accessibility acceptance for this personal ROM.

Each release covers one channel. Receipts live at ignored `.claude/releases/<build-id>.md` for `vanilla` and `.claude/releases/<type>-<build-id>.md` for other types; they preserve exact SHAs, `source_checks`, approval, artifacts, verification, and `PROVEN`/`FAILED`/`UNPROVEN` claim verdicts. Every successful deploy also writes an immutable deployment record under signed output.

Rollback requires separate exact `Rollback <channel> to <build-id>` approval. Run `yrrp-release.py rollback` with channel and build ID, add `--receipt-input` only for a historical build lacking its record, then use `yrrp-release.py wait --run-id`. The rollback command accepts no artifact path and never defaults a channel. `/.claude/build-campaigns/` remains ignored only as a legacy read-only archive; active tooling never reads or writes it.

Durable state is published source, manifests, keys/backups, signed target-files, release metadata, and receipts. Main checkout, `out/`, ccache, and extraction directories are disposable only after required evidence is backed up.
