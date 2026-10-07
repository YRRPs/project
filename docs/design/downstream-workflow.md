# Downstream source workflow

## Repository architecture

Use YRRP manifest fork as one-step source entry point:

```bash
repo init \
  -u https://github.com/YRRPs/android.git \
  -b lineage-23.2 \
  --git-lfs
repo sync
```

Manifest keeps LineageOS as baseline and routes only modified projects to YRRP. Device, kernel, and hardware dependencies remain Lineage-owned and roomservice-managed unless YRRP modifies them.

Current repositories:

```text
project/                 # planning, research, release documentation
android/                 # repo-init-capable manifest fork
android_frameworks_base/ # Pulse and future framework changes
android_packages_apps_Settings/ # YRRPs Settings hub, feature pages, search, and secure-setting controllers; builder /opt/android/packages/apps/Settings; branch lineage-23.2
android_build_server/    # reusable build environment
```

Add `android_vendor_<rom>` when product configuration, packages, branding, properties, or overlays require canonical source.

## Change placement

Use highest-level mechanism capable of expressing each change:

1. Product configuration
2. Runtime resource overlay
3. Build-time resource overlay
4. Source-project fork

Pulse requires `frameworks/base` fork because it adds SystemUI behavior.

Patch files remain recovery exports or review artifacts. Project forks are canonical source.

## Branches and remotes

Organization forks use `lineage-23.2` as integration branch so manifest projects inherit one revision.

Repo-managed checkout keeps Lineage remote name from manifest and adds YRRP destination explicitly:

```text
yrrp/lineage-23.2    downstream integration branch
github/lineage-23.2  Lineage upstream branch
```

Feature branches may use descriptive local names such as `pulse-mvp`; publish tested integration state to `yrrp/lineage-23.2` without force-push.

## Upstream updates

1. Run `repo status` and resolve all local work.
2. Push unique downstream commits and confirm recovery.
3. Sync unchanged Lineage projects.
4. Fetch Lineage remote in each fork.
5. Merge or rebase Lineage `lineage-23.2` into reviewed feature branch.
6. Resolve conflicts, run focused tests, and build ROM.
7. Fast-forward YRRP integration branch.
8. Generate locked release manifest with `repo manifest -r`.

Avoid `repo sync -d`, `--force-sync`, `git reset --hard`, and `git clean` until all unique work is pushed and recoverable.

## Durable and disposable state

Durable:

- YRRP manifest and project forks
- Future `vendor/<rom>` source
- Release signing keys and encrypted backups
- Revision-locked release manifests
- Signed target-files and release metadata

Disposable:

- Main source checkout
- `out/`
- ccache
- Generated extraction directories

Do not publish proprietary blobs. Record official source URL and checksum, then regenerate them.
