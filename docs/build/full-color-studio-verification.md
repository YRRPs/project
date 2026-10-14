# Full color studio verification

## Source under review

| Repository | Base SHA | Tested head SHA |
|---|---|---|
| `YRRPs/android_frameworks_libs_systemui` | `f33cffb935cf4f1a0a27bb3178aa1cf4621efc70` | `5268636f39b0825e4ecc39bdbca445d13a15d7fe` |
| `YRRPs/android_frameworks_base` | `1c65c2289c5cf5a301ef8ec5d7cc144dc82591f9` | `da39df3e818139306c37be3b3959619f45961b97` |
| `YRRPs/android_packages_apps_ThemePicker` | `5aff301410dece4ee3f57107e78b77bd32cf94ed` | `5463838013ed1fefd86ba60a76103679c241b693` |
| `YRRPs/android` | `41b1d367d64a4f14c672e90b4d62c8ac15340837` | `77370ce67690e766caa899c0eb4eb895e51a1644` |
| `YRRPs/project` | `305f282db34b32e01827a1e96b6f59b1d51b3057` | updated by this document commit |

## Local evidence

- `python3 -m unittest ... test_full_theming_manifest.py -v`: 5 tests passed.
- `scripts/check-full-theming-manifest.py ../android/default.xml`: `full theming manifest: PASS`.
- Modified ThemePicker XML files parse through `xml.etree.ElementTree`: pass.
- `git diff --check` on all five repositories: pass.
- Full project suite: 332 tests ran; 17 unrelated release-fixture errors remain. Representative failure: `create_fixture() got an unexpected keyword argument 'build_type'`. Full-suite success is not claimed.
- `atest`: `UNRUN`; no local Android build tree exists, and root had only 39 GiB free.
- `ktfmt`: `UNRUN`; formatter unavailable locally.

Tests were authored before production changes. Native red/green execution remains unproven because `atest` is unavailable.

## Native checks required before merge

```bash
atest monet_tests
atest ThemePickerRoboTests
atest SystemUITests_presubmit
atest SettingsProviderTest
m monet ThemePicker SystemUI SettingsProvider
```

Release-manager proof must also run `m SystemUI-core`, `m SystemUI`, standalone new test classes, full `brunch salami`, and packaged `SystemUI.apk` class inspection.

## Security boundary

SystemUI logs and dumps expose bounded source, schema, validation, generation, apply, and fallback state. They exclude custom theme names, UUIDs, seed colors, wallpaper colors, and raw setting payloads. This follows MASVS 2.1.0 `MASVS-STORAGE-2`, `MASVS-PRIVACY-1`, and `Logging_Cheat_Sheet#data-to-exclude`.

## Device evidence required

1. Capture `ThemeOverlayController` and `ThemeOverlayControllerLog` dumps before changing theme.
2. Create and apply linked four-seed theme. Compare preview against Settings, SystemUI, Launcher, and one ordinary Material app.
3. Change each seed independently and verify intended role family.
4. Enable separate dark seeds and compare both modes with both previews.
5. Apply weak-contrast palette only after explicit **Apply anyway**.
6. Change home and lock wallpapers and verify custom theme persists.
7. Restart SystemUI and reboot. Verify active and saved themes persist.
8. Switch users. Verify saved themes and active selections stay isolated.
9. Reapply wallpaper and preset choices and verify unchanged behavior.
10. Inject malformed and unsupported active payloads. Verify live retention and boot fallback through bounded dump/log state.

TalkBack acceptance is excluded for this personal ROM.
