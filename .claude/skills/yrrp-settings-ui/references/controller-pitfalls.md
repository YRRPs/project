# Controller and widget pitfalls

Read this file completely before changing a YRRPs controller, preference widget, observer, or dialog. Paths and line ranges below describe the builder checkout inspected on 2026-10-07.

## Construction and test seams

XML controllers are created reflectively with `Class.getConstructor(Context.class, String.class)`, which finds only a public constructor; missing or inaccessible constructors become `IllegalStateException` (`packages/apps/Settings/src/com/android/settings/core/BasePreferenceController.java:134-151`). Keep that constructor public. Put injectable overloads and small backends at package scope instead of expanding the production API; `YrrpSettingsStore.Backend` and its test constructor are the current pattern (`packages/apps/Settings/src/com/android/settings/yrrp/YrrpSettingsStore.java:59-78`).

Use typed setting APIs in tests. Integer secure settings are written/read with `putInt`/`getInt`, not `getString`; current controller tests demonstrate this at `packages/apps/Settings/tests/robotests/src/com/android/settings/yrrp/YrrpPulseHeightPreferenceControllerTest.java:284-290`.

## Observation and live state

Compose `YrrpSecureSettingObserver`; do not make every controller its own ad-hoc observer. Bind the preference in `displayPreference`, register for `USER_CURRENT` in `onStart`, unregister in `onStop`, and refresh through the controller's `updateState` (`packages/apps/Settings/src/com/android/settings/yrrp/YrrpSecureSettingObserver.java:55-99`). Observe every key that changes the row's value, summary, checked state, or enabled state.

Do not return a one-time disabled availability state for a dependency that can change while the page is open. Return `AVAILABLE`, then set enabled state in `updateState`; the color controller documents and implements this pattern (`packages/apps/Settings/src/com/android/settings/yrrp/YrrpPulseColorPreferenceController.java:88-121`). Radio rows must all observe the shared setting so an external write or another row's write refreshes every checked state (`packages/apps/Settings/src/com/android/settings/yrrp/YrrpScreenOffAnimationPreferenceController.java:71-107`).

Slices are opt-in: `Sliceable.isSliceable()` defaults to false and requires a matching highlight resource when true (`packages/apps/Settings/src/com/android/settings/slices/Sliceable.java:46-62`). Keep YRRPs controls non-sliceable unless the standalone privacy/security review explicitly approves exposure.

## Clicks and dialogs

Dashboard controllers receive the click before the fragment's default preference handling; returning true stops fallback and records the metric (`packages/apps/Settings/src/com/android/settings/dashboard/DashboardFragment.java:252-274`). A custom dialog row must first verify its key, launch exactly once, and return true. AndroidX also refuses to open a second stock preference dialog with the same tag (`prebuilts/sdk/current/androidx/m2repository/androidx/preference/preference/1.3.0-alpha01/preference-1.3.0-alpha01-sources.jar!/androidx/preference/PreferenceFragmentCompat.java:620-670`).

Register fragment result listeners from the host's attach path so recreation receives pending confirmed results. Before showing a dialog, require an attached host, reject saved fragment-manager state, and check the dialog tag; current precedent is `packages/apps/Settings/src/com/android/settings/yrrp/YrrpPulseColorPreferenceController.java:74-85,150-181`. Validate every result field again before writing. Cancel, back, and outside tap must emit no result. Treat multi-key writes as ordered and non-atomic, then re-read persisted state.

Verify the actual layout container before reporting clipping. The YRRPs picker is scrollable from its root (`packages/apps/Settings/res/layout/yrrp_color_picker_dialog.xml:17-117`); test portrait and landscape reachability instead of inferring it from a screenshot.

## Sliders

Configure min, max, step, continuous updates, label formatter, state description, and initial value before binding. SettingsLib applies step size before assigning the stored slider value (`frameworks/base/packages/SettingsLib/SliderPreference/src/com/android/settingslib/widget/SliderPreference.java:329-376`). Material requires every value to equal `valueFrom + n × stepSize` and throws `IllegalStateException` otherwise (`prebuilts/sdk/current/extras/material-design-x/com/google/android/material/material/1.14.0-alpha02/material-1.14.0-alpha02-sources.jar!/com/google/android/material/slider/BaseSlider.java:687-729`).

If stored values may be off-grid, keep the exact normalized value in text/state description but place the thumb on a valid grid point. Do not rewrite storage while reading. During continuous drag, write the selected value and refresh only dependent text; do not call full `updateState` from the slider callback because it rebinds the row. Current precedent and rationale are in `packages/apps/Settings/src/com/android/settings/yrrp/YrrpPulseHeightPreferenceController.java:93-165`.

## Test and evidence language

Run formatting/checkstyle, `m Settings`, and compile-only `m SettingsRoboTests`. While the current AssetManager startup blocker remains, say **Robolectric executed 0 tests** and quote the blocker; compilation is not execution. Use the structural checker for static invariants and device evidence for navigation, search, lifecycle, dialogs, dependent enablement, raw values, and no read-time rewrite.
