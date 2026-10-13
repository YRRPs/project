# Fold animation

Design: `docs/superpowers/specs/2026-10-12-duo-fold-animation-design.md`.
Research: `docs/research/fold-animation.md`.

SystemUI and WM Shell play the iPhone Duo's fold and unfold transition on the OnePlus Open (`xueying`) only. Settings offers **Stock**, **Pixel** and **Duo**. Stock is the default and keeps today's behavior.

## Status

Not yet built, released or device-tested. The host tests (SystemUI, Shell, Settings) were written but never executed: there is no local Android tree. The `android_vendor_extra` Python tests and `scripts/check-yrrp-settings.py` run locally. Every visual claim below is unproven until device acceptance.

## What it looks like

- **Unfold, Duo (inner screen, follows the hinge).** Every visible fullscreen task, home included, starts as a centered column `COVER_WIDTH_FRACTION = 1116 / 2268` of the task width (horizontal scale only) and stretches to full width as the hinge opens. A blur of `MAX_BLUR_PX = 120` clears with a decelerate curve by progress `BLUR_CLEAR_PROGRESS = 0.85`. Touch handling matches Pixel: AOSP's `UnfoldLightRevealOverlayAnimation` cancels the current touch when the transition starts, and the overlay window itself never takes touches. Pausing or reversing the hinge holds or reverses both effects. A pause of 1 s or longer settles the transition to fully open, see Known risks.
- **Fold, Duo.** The inner screen blurs as progress falls. There is no stretch. On the cover screen the blur starts at `MAX_BLUR_PX` and clears over `COVER_REVEAL_MS = 350` with one decelerate interpolator after screen-on.
- **Pixel.** The AOSP inner reveal, app inset and Launcher3 icon motion. No cover reveal, see below.
- **Stock.** No fold animation: no progress reaches any consumer.

## Architecture

| Repository | Component | Purpose |
|---|---|---|
| `android_vendor_extra` | RRO `YrrpXueyingUnfold` | Sets `config_unfoldTransitionEnabled`, `config_unfoldTransitionHingeAngle` and `config_fold_lock_behavior` to `true` on `xueying` only (`TARGET_PRODUCT == lineage_xueying`, priority `360`, above the device overlay at `350`). Haptics stay off. |
| | `persist.fold_lock_setting_enabled=true` | Product property for `lineage_xueying` only. Passes the fold-lock flag gate on `eng` and `userdebug` builds, see Fold lock. |
| `android_frameworks_base` SystemUI | `FoldAnimationStyle`, `FoldAnimationStyleRepository` | Reads `lineage_fold_animation` for the current user and normalizes it. |
| | `FoldAnimationStyleSelector` | Latches the style for one transition; every consumer reads the latched style. |
| | `StyleGatedUnfoldTransitionProgressProvider` | Wraps the root progress provider. Stock swallows every event; Pixel and Duo forward. |
| | `UnfoldLightRevealOverlayAnimation`, `FoldLightRevealOverlayAnimation` gates | Outside Pixel they skip the overlay and report ready at once. |
| | `DuoFoldOverlayAnimation` | Duo blur overlay and the cover reveal, with its own cover screen-on handshake. |
| | Launcher forward gate | Forwards remote progress to Launcher3 only in Pixel. |
| | `YrrpFoldAnimationStartable`, `FoldAnimationRuntimeState`, `YrrpFoldAnimationDumper`, `YrrpFoldAnimationLog` | Sleep and timeout releases, dump state, log buffer. |
| | `DuoFoldLockEnforcer`, `FoldLockSettingsRepository` | Keep **Continue using apps on fold** at Always while Duo is on, see Fold lock. |
| | `YRRP_AOSP_FOLD_REVEAL` (`false`) | Keeps AOSP's `FoldLightRevealOverlayAnimation` off, so Pixel has no cover reveal although the fold-lock gate is open. |
| `android_frameworks_base` WM Shell | `DuoUnfoldTaskAnimator`, `DuoUnfoldGeometry` | The Duo stretch, chosen instead of `FullscreenUnfoldTaskAnimator` when the latched style is Duo. |
| `android_packages_apps_Settings` | `YrrpFoldAnimationSettings`, controllers, `YrrpSettingsStore` key | The page and the writes. |
| | `YrrpFoldLockStore`, `FoldLockBehaviorPreferenceController` | Store and restore the fold-lock value around Duo; disable the Display row while Duo is on. |

WM Shell runs in the SystemUI process, so Shell and SystemUI share one selector through Dagger.

### Consumer behavior per style

| Consumer | Stock | Pixel | Duo |
|---|---|---|---|
| Root progress | swallowed | AOSP | AOSP |
| `UnfoldLightRevealOverlayAnimation`, `FoldLightRevealOverlayAnimation` | skipped, ready at once | AOSP | skipped, ready at once |
| `DuoFoldOverlayAnimation` | off | off | runs |
| Shell unfold claim and fullscreen tasks | no claim, no progress | `FullscreenUnfoldTaskAnimator` | `DuoUnfoldTaskAnimator` |
| Launcher3 remote progress | none | AOSP | none (Shell stretches the home task) |
| Wallpaper zoom, keyguard, shade unfold | none | AOSP | AOSP |
| `FoldAodAnimationController` | none | AOSP | AOSP |

### Style latching

- The selector latches the setting at the first of: `onTransitionStarted` from the gated provider, or the unfold screen-turning-on handshake (`latchIfIdle`, called by `UnfoldLightRevealOverlayAnimation` and `DuoFoldOverlayAnimation`). A gated start that finds a handshake latch joins it and does not count a second start. The Duo cover reveal does not latch.
- It holds until `onTransitionFinished`, the device going to sleep (`releaseAll`), or a 10 s safety timeout (`LATCH_TIMEOUT_MS`). Forced releases (sleep, timeout) count as cancellations.
- A setting change during a transition applies to the next one.
- If the repository read fails, the selector latches Stock and logs the stage and exception class.

## Differences from the spec

The code is the source of record. These points differ from the design document:

- `shellAnimatingTasks` is absent from the dump.
- The cover reveal uses Duo's own screen-on handshake with `READY_TIMEOUT_MS = 2000`. Pixel has no cover reveal here: `YRRP_AOSP_FOLD_REVEAL` keeps AOSP's fold reveal off, now that `config_fold_lock_behavior` is `true` on xueying (#65).
- Stock also stops Shell from claiming the unfold transition, so `UnfoldTransitionHandler` never holds an unanswered claim for 5 s.
- Shell resets the home leash only after a Duo stretch, so Pixel and Stock keep today's reset behavior.
- The latch is also taken at the screen-on handshake, and is released on finish, on sleep, or by the 10 s timeout. A Stock unfold therefore also latches Stock at the handshake.
- The Duo inner overlay has a 5 s safety timeout (`SAFETY_TIMEOUT_MS`) that cancels it if no finish arrives.
- Blur is retried on every progress callback, so `blurAvailable` reflects the latest attempt.
- The cover reveal uses a single decelerate over `COVER_REVEAL_MS` (radius `MAX_BLUR_PX * (1 - fraction)`), not the progress-curve mapping.
- `isUnfoldHandled` is set in every style, so a style switch to Pixel cannot leave a stuck black overlay.
- A fold attach starts at the current progress radius (unblurred), so there is no blur flash before the first progress callback.
- `FoldAodAnimationController` skips the fold-to-AOD animation in Stock. Turning the unfold framework on creates it, and it decides from fold state and sleep reason rather than unfold progress, so the progress gate alone did not stop it.
- The Duo overlay's initial blur is set on the relayout transaction that carries the overlay's first frame, so it is never presented unblurred.
- Shell squeezes Duo tasks to progress 0 in the start transaction, so the first unfolded frame is already at the cover width.
- The latch timeout releases only the latch it was armed for (`latchGeneration`), never a later one.
- Sleep clears only the pending cover reveal. The pending unfold stays set while folded, so fold, sleep, unfold, wake still plays the unfold.

## Device scope

LineageOS scopes device features in two layers, and this feature uses both:

1. **Build time.** The device opts in through overlays. `android_vendor_extra` installs `YrrpXueyingUnfold` only for `lineage_xueying`, which turns on `config_unfoldTransitionEnabled` and `config_unfoldTransitionHingeAngle`. No other product gets the overlay. A sensor cannot be checked at build time, because the sensor HAL reports it at run time.
2. **Run time.** The SystemUI and Settings code ships on every device, as all framework code does, and stays idle unless the device is supported. `FoldAnimationSupport` (SystemUI) and `YrrpFoldAnimationAvailability` (Settings) apply the same three conditions:
   - `config_unfoldTransitionEnabled` is `true`.
   - `config_unfoldTransitionHingeAngle` is `true`.
   - `SensorManager.getDefaultSensor(Sensor.TYPE_HINGE_ANGLE)` returns a sensor.

On an unsupported device the Settings row and page are hidden and unsearchable. `YrrpFoldAnimationStartable` registers its dump only, observes neither the setting nor sleep, and the selector stays Stock. The dump names the first failed condition in `blockedBy`. AOSP's unfold chain is not built at all when `config_unfoldTransitionEnabled` is `false`, so on `salami` none of the Shell or overlay code runs.

## Setting

**Settings → YRRPs → Animations → Fold animation**, shown only on a supported foldable, see Device scope. Radio rows **Stock**, **Pixel** and **Duo** under a **Style** category. See `yrrp-settings-ui.md`.

| Key | Scope | Default | Read | Write |
|---|---|---|---|---|
| `lineage_fold_animation` | private per-user `Settings.Secure` | 0 (Stock) | 1 is Pixel, 2 is Duo, anything else is Stock | 0, 1 or 2; other values rejected |

Settings and SystemUI use identical defaults and normalization. Settings never rewrites a stored value on read. No migration: absent means Stock.

```bash
adb shell settings put secure lineage_fold_animation 2     # Duo; skips the fold-lock backup, see Fold lock
adb shell settings delete secure lineage_fold_animation    # restore, then Stock
```

## Fold lock

Folding an unlocked phone used to show the lock screen in every style (#65). With the fold-lock gate closed, `FoldSettingProvider` forces **Swipe up to continue**, and `PowerManagerService` then shows a dismissible keyguard on fold. Two changes open the gate on `xueying`: `config_fold_lock_behavior=true` in `YrrpXueyingUnfold`, and `persist.fold_lock_setting_enabled=true` for `eng` and `userdebug` builds, because the build-time value of the read-only flag `fold_lock_setting_enabled` is unverified. **Settings → Display → Continue using apps on fold** then appears, and Android honors it in every style.

Duo forces **Always**:

| Event | Owner | Effect |
|---|---|---|
| The user selects Duo | Settings | Stores the current `fold_lock_behavior_setting` in `yrrp_fold_lock_before_duo` unless a stored value exists (`selective_stay_awake_key` when unset), writes `stay_awake_on_fold_key`, then `lineage_fold_animation=2`. A failed backup changes nothing. |
| Duo is on | Settings | **Continue using apps on fold** is disabled and reads "Always · Set by Duo fold animation". |
| Another writer changes the value while Duo is on | SystemUI | Rewrites `stay_awake_on_fold_key` for the selected user and logs it. |
| The user leaves Duo | Settings | Writes the new style first, so SystemUI stops forcing, then restores the stored value, or `selective_stay_awake_key` when none exists, and deletes the key. |

Settings restores the fold lock only when leaving Duo. Selecting Stock or Pixel when Duo was not on leaves the fold-lock setting alone, even if a stored value is left over. If the style write fails after entering Duo, Settings restores the value at once. Selecting Duo while Duo is already on changes nothing, so SystemUI's Always is never stored as the user's value.

SystemUI reads the fold-lock value before the style, and re-reads the style just before it writes. Leaving Duo, Settings writes the style first, so SystemUI never forces Always over a value Settings has just restored.

| Key | Scope | Owner | Values |
|---|---|---|---|
| `fold_lock_behavior_setting` | per-user `Settings.System` (AOSP) | Android, Settings; SystemUI while Duo is on | `stay_awake_on_fold_key` (Always), `selective_stay_awake_key` (Swipe up to continue, default), `sleep_on_fold_key` (Never) |
| `yrrp_fold_lock_before_duo` | private per-user `Settings.Secure` | Settings | The value from before Duo; absent while Duo is off |

Setting Duo with `adb` skips Settings, so no value is stored: SystemUI forces Always, and leaving Duo in Settings writes Swipe up to continue.

```bash
adb shell settings get system fold_lock_behavior_setting
adb shell settings get secure yrrp_fold_lock_before_duo
```

## Constants

| Constant | Value |
|---|---|
| `COVER_WIDTH_FRACTION` | `1116f / 2268f` |
| `MAX_BLUR_PX` | 120 |
| `BLUR_CLEAR_PROGRESS` | 0.85 |
| `TOUCH_UNBLOCK_PROGRESS` | 0.8 (relayout hint only; the overlay is never touchable) |
| `COVER_REVEAL_MS` | 350 |
| `READY_TIMEOUT_MS` | 2000 |
| `SAFETY_TIMEOUT_MS` (Duo inner overlay) | 5000 |
| `LATCH_TIMEOUT_MS` | 10000 |

## Failure handling

- Animator scale 0: Duo runs no visuals and reports ready at once.
- Cross-window blur unavailable (Battery Saver, thermal): Duo still stretches and skips the blur; the dump shows `blurAvailable=false`.
- An exception while attaching the overlay removes it, reports ready, resets Shell and logs the stage and exception class.
- On finish, sleep or timeout the overlay is removed and Shell surfaces are reset.

## Observability

Dumpable `YrrpFoldAnimation`, one `name=value` per line:

- Inputs: `settingValue`, `selectedStyle`, `latchedStyle`, `configEnabled`, `hingeAngleEnabled`, `animationsEnabled`, `blurAvailable`.
- Device: `hingeSensorPresent`.
- Decision: `eligible`, `blockedBy` (`CONFIG_DISABLED`, `HINGE_ANGLE_DISABLED`, `NO_HINGE_SENSOR`, `STYLE_STOCK`, `ANIMATIONS_DISABLED`, `NONE`).
- Lifecycle: `phase` (`IDLE`, `UNFOLDING`, `FOLDING`, `COVER_REVEAL`), `overlayAttached`, `starts`, `completions`, `cancellations`, `lastCancelReason`.
- Fold lock: `foldLockValue` (the selected user's value, `null` when unset or on an unsupported device), `foldLockForced` (Duo is on and SystemUI rewrote the value at least once since boot).

LogBuffer `YrrpFoldAnimationLog` (100 entries) records transitions only: style latched or released (forced, with reason), overlay added or removed, Shell reset, cancel reason, failure stage plus exception class, and each fold-lock rewrite (`foldLock forced from=<value> user=<id>` or `foldLock force failed ...`). Nothing per frame, and no package names, task names or UIDs.

```bash
adb shell dumpsys activity service com.android.systemui/.SystemUIService \
  YrrpFoldAnimation YrrpFoldAnimationLog
```

## Status of the change

- Frameworks: `YRRPs/android_frameworks_base` branch `feat/duo-fold-animation`, base `b048326c7224`, head `e73f7f2609fd` (17 commits).
- Settings: `YRRPs/android_packages_apps_Settings` branch `feat/duo-fold-animation`, base `bf9f23729658`, head `24e19c95d813`.
- vendor/extra: `YRRPs/android_vendor_extra` branch `feat/duo-fold-animation`, base `b0e16f041466`, head `fb524c6795e6`.
- Framework patch series: `patches/frameworks-base/duo-fold-animation/` (17 patches), checksums in `SHA256SUMS.txt`.
- Tests, not executed locally: `atest SystemUiRoboTests:com.android.systemui.unfold.yrrp`, `atest SystemUiRoboTests:com.android.systemui.unfold.UnfoldLightRevealOverlayAnimationTest`, `atest SystemUiRoboTests:com.android.systemui.unfold.util.StyleGatedUnfoldTransitionProgressProviderTest`, plus the Shell and Settings test classes named in the PRs. The shared SystemUI Robolectric target may fail on unrelated baseline fixtures.
- Device acceptance: pending the first release with all PRs.

## Known risks and minor findings

- **Launcher3 preemptive progress (Stock).** Launcher3 may animate on a display change before SystemUI's progress arrives. Device claim: Stock looks identical to build `20261011-112111`.
- **Blur cost at 120 Hz.** Compare Pixel and Duo in Perfetto after release; tune `MAX_BLUR_PX` if needed.
- Sleep with the Duo overlay attached records two cancellations (overlay `ASLEEP`, selector `SLEEP`); `lastCancelReason` depends on order.
- A handshake latch with no gated start is held 10 s and then counted as a `LATCH_TIMEOUT` cancellation.
- Booting folded triggers a first cover reveal.
- **Hinge pause.** AOSP's `config_unfoldTransitionHalfFoldedTimeout` (1000 ms, unchanged) finishes a transition when the hinge stops for 1 s. A longer pause settles to fully stretched and unblurred, and closing again then starts a fold. Pixel behaves the same. Raising the timeout would collide with the 5 s Duo safety timeout, so the device check pauses for under 1 s.
- One `SurfaceControl.Transaction` per progress frame, and the Stock provider runs on a background handler; check cost in Perfetto.

## Local tests cannot prove

- That any SystemUI, Shell or Settings code compiles or passes: nothing was built.
- The look of the stretch and blur, and blur validity on the SurfaceControl view host.
- Frame rate at 120 Hz.
- That Stock matches the previous build, including Launcher3.
- Interrupted transitions (sleep mid-unfold, reversal) leaving no blur, squeeze or blocked touch.
- Hub row hidden on `salami`, and the dump there showing `blockedBy=CONFIG_DISABLED`.
- `cmd overlay list android | grep yrrp.overlay.xueying.unfold` shows `[x]` on xueying.
- `Settings.apk` contains `YrrpFoldAnimationSettings`; the signed `SystemUI.apk` dex contains `DuoFoldOverlayAnimation`, `FoldAnimationStyleSelector`, `StyleGatedUnfoldTransitionProgressProvider` and `DuoUnfoldGeometry`.
