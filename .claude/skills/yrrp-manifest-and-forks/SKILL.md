---
name: yrrp-manifest-and-forks
description: Use when changing which repositories the ROM builds from or publishing YRRP repos — "fork <repo>", "add a fork to the manifest", "override a Lineage project", "our manifest", "default.xml", "repo sync fails", "hooks is different", "--force-sync not enabled", "switch the checkout to our manifest", "push to frameworks_base", "YRRPs", "new repo in the org", "GHCR image is stale", "Dependabot PRs", "merge dependabot", "builder image main tag", "workflow didn't publish". Covers the org's repository map, adding a forked project, the force-sync trap, and GHCR publishing races. NOT for building or signing (yrrp-signed-ota-release).
---

# YRRP manifest and forks

Announce first: **Using yrrp-manifest-and-forks to change the source topology.**

## Repository map

GitHub org `YRRPs`, all public, Apache-2.0 for original work:

| Repo | Local path | Role |
|---|---|---|
| `project` | `~/Documents/Projects/lineageos-salami-custom` | docs, scripts, tests; cloned read-only into the builder |
| `android` | — | repo manifest; `default.xml` overrides Lineage projects |
| `android_frameworks_base` | builder `/opt/android/frameworks/base` | Pulse fork, branch `lineage-23.2` |
| `android_vendor_extra` | `~/Documents/Projects/android_vendor_extra`, builder `vendor/extra` | product overrides inherited first by `vendor/lineage/config/common.mk` (Updater URL); put new properties here instead of forking `vendor/lineage` |
| `android_build_server` | `~/Documents/Projects/android_build_server` | builder image → `ghcr.io/yrrps/android-build-server:main` |
| `ota_server` | `~/Documents/Projects/ota_server` | Nginx OTA base → `ghcr.io/yrrps/ota-server:main` |

Forks go in the org, never the user's personal profile.

## Adding a forked project

1. Fork the Lineage repo into the org, keeping the `android_<path>` name.
2. In `android/default.xml`, replace the project's entry with one using `remote="yrrp"` (the remote fetches `https://github.com/YRRPs`). Keep the original `path` and `groups`.
3. Push the manifest, then on the builder run `repo sync <path>` for that path only.

## The force-sync trap

Changing a project's `name` makes `repo sync` fail with:

```text
hooks is different ...
--force-sync not enabled
```

`--force-sync` rewrites that project's Git metadata and worktree. Before it:

1. Confirm all unique work in that project is pushed, and create a backup: `git -C /opt/android/<path> bundle create /opt/android/.backups/<name>-$(date +%Y%m%d).bundle --all`, then `git bundle verify` it.
2. Ask through `AskUserQuestion` (options: force-sync only `<path>` / stop). Never run an unscoped `--force-sync`, `repo sync -d`, `git reset --hard`, or `git clean` on the checkout.
3. Run `repo sync --force-sync <path>` and report the new `HEAD` against the fork's branch.

## GHCR publishing

Pushes to `main` in `android_build_server` and `ota_server` publish `:main` images through pinned-SHA workflows. Merging several Dependabot PRs in quick succession can leave `:main` built from an intermediate commit. After a batch of merges, compare the image's `org.opencontainers.image.revision` label with `git rev-parse origin/main`, and rerun the workflow on `main` if they differ.

Package visibility cannot be changed through the GitHub API; the user changes it in the web UI.
