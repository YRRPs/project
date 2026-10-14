# ROM feature inventory design

Date: 2026-10-14
Status: Approved

## Goal

Document every shipped YRRP feature and show installed-build availability in Settings. Keep device, build-flavor, and user restrictions visible instead of hiding unsupported features.

This work also removes obsolete patch bundles from the project repository. Android source changes live and receive review in their target repositories.

## Scope

The inventory covers shipped feature families only:

- Pulse visualizer
- Screen-off effects
- Matrix boot and shutdown animation
- Fold animations
- Ad blocking
- Hotspot client management
- Indoor auto-brightness tuning
- OnePlus Camera integration
- GApps integration and build flavor

The inventory excludes live preference values, planned work, and unmerged branches.

## Settings architecture

Add a read-only page at **Settings → YRRP → Misc → Features** in `android_packages_apps_Settings`.

A Settings-owned registry defines each feature through a stable ID, localized title, localized description, category, and availability resolver. The registry preserves display order. Resolvers return one of two user-facing states:

- **Available:** The installed build includes and supports the feature in the current environment.
- **Unavailable:** The feature exists in the catalog but the current device, build, capability, or user cannot use it.

Every unavailable result includes a localized reason. The page does not expose controls or navigate to feature settings.

The page groups rows under **Customization**, **System services**, and **Device integrations**. Each row shows an icon, title, short description, and availability summary. Unavailable rows remain visible and use disabled styling.

The page and feature-family names participate in Settings search. Search opens the read-only inventory page.

## Availability rules

Resolvers inspect installed-build facts and reuse existing eligibility logic where it exists:

- Pulse visualizer, screen-off effects, and Matrix boot/shutdown animation are available on every supported build.
- Fold animations reuse `YrrpFoldAnimationAvailability`. Availability requires both unfold configuration booleans and a `TYPE_HINGE_ANGLE` sensor. A non-foldable reports that its hardware does not support fold animations.
- Ad blocking requires the primary user. A secondary user reports that only the device owner can use it.
- Hotspot client management requires the platform client-control capability. Missing support reports that hotspot firmware does not expose client controls.
- Indoor auto-brightness tuning detects the Salami product configuration. Other devices report that the tuning targets OnePlus 11.
- OnePlus Camera integration checks the installed integration package. Missing integration reports that the device build does not include it.
- GApps integration reads `ro.yrrp.build.type`. A vanilla build reports that Google apps are not included; a GApps build reports availability.

Resolvers stay independent from Android preference widgets and expose injectable dependencies for tests. A failed property, package, sensor, resource, user, or capability probe returns **Unavailable — status could not be determined** rather than crashing Settings.

## Observability

Visible row status is the primary observation surface.

A stable log tag records each feature ID, availability result, and reason code when the page resolves the registry. Logs exclude package inventories, device identifiers, account data, and other personal data. Probe failures include enough context to identify the failed probe without exposing private values.

## README inventory

Update the project `README.md` with a public feature list organized as:

- Available on all supported devices
- Foldable-only
- Salami-specific
- Build-flavor dependent

Each entry names the user-visible capability and its scope. The README does not include planned or unmerged features. Settings remains authoritative for installed-build availability; the README describes supported release scope.

## Patch cleanup

Delete tracked Android feature patch files and their checksum manifests from the project repository.

Update design documents and the local ROM feature research skill to remove patch-file instructions and references. Preserve useful historical design context, but point implementation history to target-repository commits or pull requests.

Do not add generated patches, source snapshots, or cross-repository payloads to the project repository. Feature owners develop, test, review, and publish changes in each target repository.

## Test strategy

Use strict test-driven development: write a failing test, make the smallest production change, run targeted verification, then refactor.

Settings robotests cover:

- Registry contents, grouping, and stable order
- Universal feature availability
- Salami and non-Salami product behavior
- Xueying fold eligibility and missing hinge-sensor behavior
- GApps and vanilla build types
- Primary and secondary users
- Present and missing OnePlus Camera integration
- Supported and unsupported hotspot client controls
- Probe-failure fallback and reason codes
- Navigation from **YRRP → Misc → Features**
- Search indexing and page opening
- Localized available and unavailable summaries

Project tests cover:

- README feature sections and expected feature families
- Absence of patch files, checksum manifests, and stale patch-workflow references

Run targeted tests during development, then run `m Settings`. Run compile-only `m SettingsRoboTests` if the existing Lineage resource AssetManager blocker still prevents robotest execution. Record the exact blocker rather than claiming test execution.

## Acceptance criteria

1. `README.md` clearly separates universal, foldable-only, Salami-specific, and build-flavor-dependent features.
2. **Settings → YRRP → Misc → Features** opens a searchable, read-only inventory.
3. Every shipped feature family appears exactly once with an availability result.
4. Salami reports fold animations unavailable with a non-foldable reason.
5. Xueying reports fold animations available only when unfold configuration and hinge sensor are present.
6. Ad blocking, hotspot controls, auto-brightness tuning, OnePlus Camera, and GApps report environment-specific reasons.
7. Probe failures never crash Settings and produce a privacy-safe diagnostic log.
8. Settings restart recomputes and restores the same statuses for unchanged environment facts.
9. Project repository contains no tracked feature patch bundles, checksum manifests, or active patch-workflow references.
10. Planned and unmerged features do not appear.

## Verification boundaries

Local tests and Settings compilation prove registry logic, resource wiring, navigation, search metadata, and failure handling. They cannot prove actual target-device properties, sensors, packages, capabilities, or rendered UI.

Post-release device checks cover:

- Salami and Xueying feature lists
- Fold hardware/config eligibility
- OnePlus Camera package detection
- Auto-brightness product scope
- GApps and vanilla build flavor
- Primary and secondary user behavior
- Hotspot client-control capability
- Search result and breadcrumb
- Settings interruption and restart restoration

Skip TalkBack and accessibility acceptance for this personal ROM. Feature-owner work does not access the builder, create product builds, sign artifacts, deploy releases, or alter device state.
