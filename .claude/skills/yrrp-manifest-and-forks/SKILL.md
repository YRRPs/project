---
name: yrrp-manifest-and-forks
description: Use when changing or publishing the repositories the ROM builds from — "fork this repo", "add a fork to the manifest", "override a Lineage project", "our default.xml", "repo sync fails", "hooks is different", "--force-sync not enabled", "switch to our manifest", "push frameworks/base", "new YRRPs repo", "GHCR image is stale", "merge Dependabot", or "workflow did not publish". Covers local feature branches and PRs, release-manager merge ownership, exact-SHA scoped builder sync, force-sync backup and approval safety, and GHCR revision checks. NOT for feature implementation on the builder, signing/build execution, key restoration, or unscoped checkout cleanup.
---

# YRRP manifest and forks

Announce first: **Using yrrp-manifest-and-forks to publish reviewed source topology safely.**

## Role split

Feature owners fetch the remote's current symbolic default branch, create a fresh clone or worktree beneath `.workdirs/`, implement and test locally, push a feature branch, and open a PR. They never mutate `/opt/android` or call `yrrp-builder-access`.

The `yrrp-release-manager` reviews and merges selected PRs, captures the actual merged default-branch SHA for each repository, and is the only role that publishes integration changes or requests builder synchronization. After selected PR merges and merged project SHA capture, fetch origin and create a clean dedicated release worktree beneath `.workdirs/`, detached at the exact merged project SHA. Run `prepare` and `launch` from that checkout. Retain it through release completion; there is no automatic cleanup. Builder sync is scoped to those exact merged SHAs through `python3 scripts/yrrp-release.py prepare --project-sha ... --repo PATH=SHA`; never sync an inferred branch tip.

## Repository map

| Repository | Builder path | Role |
|---|---|---|
| `YRRPs/project` | `/opt/yrrp/project` read-only | Release scripts, docs, tests |
| `YRRPs/android` | manifest checkout | `repo init` manifest |
| `YRRPs/android_frameworks_base` | `/opt/android/frameworks/base` | Framework and SystemUI changes |
| `YRRPs/android_packages_apps_Settings` | `/opt/android/packages/apps/Settings` | YRRPs Settings UI |
| `YRRPs/android_vendor_extra` | `/opt/android/vendor/extra` | Product properties and additive overrides |
| `YRRPs/android_build_server` | image | Builder container |
| `YRRPs/ota_server` | image | OTA Nginx base |

Forks belong in `YRRPs`, retain upstream history and licensing, and use `lineage-23.2` as the integration branch.

## Add or change a fork

1. Create the correctly named `android_<path>` fork in the organization.
2. Make the manifest change on a local feature branch and open a PR; preserve the original checkout `path` and groups while selecting `remote="yrrp"`.
3. After review, the release manager merges the fork and manifest PRs and captures both actual merged SHAs.
4. Run the fixed prepare command with the project SHA plus each changed path/SHA. Require returned repository and revision-locked manifest evidence before build approval.

## Force-sync safety

A changed project name may produce `hooks is different` and `--force-sync not enabled`. Before any forced sync:

1. Under the shared builder lock, confirm all unique commits are published and create `/opt/android/.backups/<name>-<date>.bundle` with `git bundle create --all`; verify it with `git bundle verify`.
2. Ask through `AskUserQuestion` with exactly **Force-sync only `<path>`** and **Stop**.
3. If approved, run only `repo sync --force-sync <path>` under the shared lock, then report `HEAD`, requested merged SHA, clean status, and backup path.

Never run unscoped `--force-sync`, `repo sync -d`, `git reset --hard`, or `git clean`. The fixed prepare flow remains preferred because it scopes source to reviewed exact SHAs.

## GHCR revision evidence

Pushes to `main` in `android_build_server` and `ota_server` publish `:main` images through pinned workflows. After rapid merges, compare image label `org.opencontainers.image.revision` with the actual merged `origin/main` SHA. If they differ, rerun the workflow for that exact SHA and recheck the label before use. Package visibility remains a user web-UI action.
