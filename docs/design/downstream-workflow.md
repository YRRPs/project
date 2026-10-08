# Downstream source workflow

## Simple role split

A feature owner handles one feature locally. Reuse one canonical clone per repository—the project root for `project`, or `.workdirs/repos/<repository>` for another repository—and clone only when that cache is missing. Under a short `.workdirs/locks/<repository>.lock`, verify origin and cleanliness, fetch/prune the current symbolic default branch, and add a per-feature worktree at `.workdirs/features/<feature-id>/<repository>`. Never implement in the canonical clone. Branch from the fetched default SHA, implement with TDD, run local checks, review, push, and open a PR. The handoff is `FEATURE_READY` with PR/repository/branch/base/tested-head evidence, acceptance criteria, and a structured proof plan. Feature owners never access the builder.

The `yrrp-release-manager` owns integration and release: after each handoff it asks **Keep release open** or **This is the last feature**; it reviews and merges only selected PRs, captures actual merged default-branch SHAs, prepares exact source under the shared lock, obtains separate **Build and release** approval, launches, verifies, coordinates serial device proof, and writes an immutable receipt.

## Source topology

Initialize from `YRRPs/android` branch `lineage-23.2`. The manifest retains LineageOS as baseline and routes only modified projects to YRRP forks. Use product configuration or overlays before creating a source fork.

Current durable repositories include `project`, `android`, `android_frameworks_base`, `android_packages_apps_Settings`, `android_vendor_extra`, `android_build_server`, and `ota_server`. Feature branches are short-lived; `lineage-23.2` is each source repository's reviewed integration branch.

## Merge and exact builder sync

Never build a feature branch or guessed remote head. After selected PR merges and merged project SHA capture, fetch origin and create a clean dedicated release worktree beneath `.workdirs/`, detached at the exact merged project SHA. Verify its status is empty, then run `prepare` and `launch` from that checkout. Retain it through release completion; there is no automatic cleanup. Remove it only after a later explicit cleanup decision.

From that checkout, run:

```bash
python3 scripts/yrrp-release.py prepare \
  --project-sha <actual-project-sha> \
  --repo frameworks/base=<actual-merged-sha> \
  --repo packages/apps/Settings=<actual-merged-sha>
```

Preparation acquires `/home/android/.yrrp-build-launch.lock`, updates the read-only project bind through a trusted Docker sibling, refuses dirty source, scoped-syncs requested paths, and creates a revision-locked manifest. The returned project SHA, repository SHAs, and manifest SHA-256 are the approval evidence. Launch must reuse those exact values.

## Destructive sync safety

Before a path-scoped `--force-sync`, prove unique work is published, create and verify a Git bundle backup, and ask through `AskUserQuestion` with **Force-sync only `<path>`** and **Stop**. Never run unscoped force sync, `repo sync -d`, `git reset --hard`, or `git clean` on the builder.

## Proof and durable records

Local checks document what they cannot prove. Proof plans name claim, trigger/setup, observable evidence, expected outcome, cheapest proving layer, limitations, post-release device check, and restoration. Device-only boundaries remain unproven until observed on the target device. Skip TalkBack and accessibility acceptance for this personal ROM.

Receipts live at ignored `.claude/releases/<build-id>.md`; they preserve exact SHAs, approval, artifacts, verification, and `PROVEN`/`FAILED`/`UNPROVEN` claim verdicts. `/.claude/build-campaigns/` remains ignored only as a legacy read-only archive; active tooling never reads or writes it.

Durable state is published source, manifests, keys/backups, signed target-files, release metadata, and receipts. Main checkout, `out/`, ccache, and extraction directories are disposable only after required evidence is backed up.
