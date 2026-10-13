# Fold animation

Design: `docs/superpowers/specs/2026-10-12-duo-fold-animation-design.md`.
Research: `docs/research/fold-animation.md`.

SystemUI and WM Shell play the iPhone Duo's fold and unfold transition on the OnePlus Open (`xueying`) only. Settings offers **Stock**, **Pixel** and **Duo**. Stock is the default and keeps today's behavior.

## Status

Not yet built, released or device-tested. The host tests (SystemUI, Shell, Settings) were written but never executed: there is no local Android tree. The `android_vendor_extra` Python tests and `scripts/check-yrrp-settings.py` run locally. Every visual claim below is unproven until device acceptance.

## What it looks like

- **Unfold, Duo.** The cover blurs from its right edge as the hinge opens. The blur then continues on a frozen picture of the cover while both panels are lit. The inner screen starts fully blurred and clears from right to left with unfold progress. See Duo open and Duo close. Shell leaves tasks alone, as in Stock.
- **Fold, Duo.** The inner blur grows back from left to right while you fold. Then the cover turns on fully blurred and clears from left to right over `COVER_REVEAL_MS = 350`.
- **Pixel.** The AOSP inner reveal, app inset and Launcher3 icon motion. No cover reveal, see below.
- **Stock.** No fold animation: no progress reaches any consumer.

## Duo open and close

### Cover blur

- The cover listens to the hinge only while the device is folded, the screen is on and the style is Duo. The unfold library does not report the hinge in that state.
- An open arms only after a closed reading. A slow close can report closed at 4-24 degrees, so the end of a close never starts an open.
- When the hinge first reads 1° or more, SystemUI captures the cover and blurs it in from the right edge. The whole-degree reading is the first reading above closed on `xueying`.
- The blurred share of the width is `1 - (1 - p)^2`, with `p = angle / 165°`. The blur strength is `MAX_BLUR_PX * share`, so it fades in with the share up to 220 px. No dim layer is added.
- A soft edge spans `EDGE_WIDTH_FRACTION = 0.6` of the width, in `EDGE_STRIPS = 16` strips. Each strip ramps radius and alpha up to full strength.
- The cover blur has no timeout. It ends when the hinge returns to closed, the inner unfold finishes, the cover display is removed, the device folds, the style changes or the frame fails.

### Both panels lit

- When the device switches to the inner screen, `LogicalDisplayMapper` keeps the cover panel lit on its own logical display. This happens only when `lineage_fold_animation=2`, animator scale is not 0, and the device is interactive.
- SystemUI hands the cover overlay over to `DuoCoverFrame` when that display appears. The frame shows a frozen picture of the cover, captured when the hinge left 0°, and the blur continues on it. Dimming is off for this picture.
- The cover powers off at the fully opened device state (`OPENED`, about 150°), after `TIMEOUT_MS = 1500`, or when the device goes to sleep.
- The mapper marks the cover display in transition, so its panel turns off. It disables the logical display only after the panel reports off.
- If the panel is still on `POWER_OFF_OVERDUE_MS = 1000` after the request, the mapper logs it and keeps the display enabled.
- Folding back ends the session at once, with no power-off.

### Inner screen

- The inner screen turns on fully blurred in its first visible frame. It clears from right to left with unfold progress.
- The blurred share of the width is `1 - (p / 0.95)^2`, so it is clear at 95 % and mostly blurred at 60°. Here `p` is unfold progress.
- The overlay blocks touch while progress is below `TOUCH_UNBLOCK_PROGRESS = 0.8`.

### Close

- While you fold, the inner blur grows back from left to right. The overlay blocks touch for the whole fold.
- The cover then turns on fully blurred and clears from left to right over `COVER_REVEAL_MS = 350`, with one decelerate interpolator.

## Architecture

| Repository | Component | Purpose |
|---|---|---|
| `android_vendor_extra` | RRO `YrrpXueyingUnfold` | Sets `config_unfoldTransitionEnabled`, `config_unfoldTransitionHingeAngle` and `config_fold_lock_behavior` to `true` on `xueying` only (`TARGET_PRODUCT == lineage_xueying`, priority `360`, above the device overlay at `350`). Haptics stay off. |
| | `persist.fold_lock_setting_enabled=true` | Product property for `lineage_xueying` only. Passes the fold-lock flag gate on `eng` and `userdebug` builds, see Fold lock. |
| `android_frameworks_base` SystemUI | `FoldAnimationStyle`, `FoldAnimationStyleRepository` | Reads `lineage_fold_animation` for the current user and normalizes it. |
| | `FoldAnimationStyleSelector` | Latches the style for one transition; every consumer reads the latched style. |
| | `StyleGatedUnfoldTransitionProgressProvider` | Wraps the root progress provider. Stock swallows every event; Pixel and Duo forward. |
| | `UnfoldLightRevealOverlayAnimation`, `FoldLightRevealOverlayAnimation` gates | Outside Pixel they skip the overlay and report ready at once. |
| | `DuoFoldOverlayAnimation` | Duo inner blur and the cover reveal, with its own cover screen-on handshake. Feeds fold, screen and style changes to `DuoCoverOpening`. |
| | `DuoCoverOpening`, `DuoOpenSequence`, `CoverHingeProgress` | The cover side of an open: listens to the hinge, arms on a closed reading, blurs the cover. |
| | `DuoCoverFrame` | The frozen cover picture and its blur layer on the cover's own display. |
| | `DuoBlurCurve` | Blur geometry: shares, front position and soft-edge strips. |
| | Launcher forward gate | Forwards remote progress to Launcher3 only in Pixel. |
| | `YrrpFoldAnimationStartable`, `FoldAnimationRuntimeState`, `YrrpFoldAnimationDumper`, `YrrpFoldAnimationLog` | Sleep and timeout releases, dump state, log buffer. |
| | `DuoFoldLockEnforcer`, `FoldLockSettingsRepository` | Keep **Continue using apps on fold** at Always while Duo is on, see Fold lock. |
| | `YRRP_AOSP_FOLD_REVEAL` (`false`) | Keeps AOSP's `FoldLightRevealOverlayAnimation` off, so Pixel has no cover reveal although the fold-lock gate is open. |
| `android_frameworks_base` system_server | `LogicalDisplayMapper`, `DuoDualPanelSession`, `DuoDualPanelPolicy` | Keep the cover panel lit on its own logical display during a Duo open, then power it off. |
| `android_frameworks_base` WM Shell | `UnfoldTransitionHandler` | In Stock and Duo it does not claim the unfold transition, so Shell leaves tasks alone. |
| `android_packages_apps_Settings` | `YrrpFoldAnimationSettings`, controllers, `YrrpSettingsStore` key | The page and the writes. |
| | `YrrpFoldLockStore`, `FoldLockBehaviorPreferenceController` | Store and restore the fold-lock value around Duo; disable the Display row while Duo is on. |

WM Shell runs in the SystemUI process, so Shell and SystemUI share one selector through Dagger.

### Consumer behavior per style

| Consumer | Stock | Pixel | Duo |
|---|---|---|---|
| Root progress | swallowed | AOSP | AOSP |
| `UnfoldLightRevealOverlayAnimation`, `FoldLightRevealOverlayAnimation` | skipped, ready at once | AOSP | skipped, ready at once |
| `DuoFoldOverlayAnimation` | off | off | runs |
| Shell unfold claim and fullscreen tasks | no claim, no progress | `FullscreenUnfoldTaskAnimator` | no claim, no progress |
| Launcher3 remote progress | none | AOSP | none |
| `DuoCoverOpening`, `DuoCoverFrame` | off | off | runs |
| `LogicalDisplayMapper` dual panel | off | off | runs when gated, see Both panels lit |
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
- Stock and Duo stop Shell from claiming the unfold transition, so `UnfoldTransitionHandler` never holds an unanswered claim for 5 s.
- The latch is also taken at the screen-on handshake, and is released on finish, on sleep, or by the 10 s timeout. A Stock unfold therefore also latches Stock at the handshake.
- The Duo inner overlay has a 5 s safety timeout (`SAFETY_TIMEOUT_MS`) that cancels it if no finish arrives.
- Blur is retried on every progress callback, so `blurAvailable` reflects the latest attempt.
- The cover reveal uses a single decelerate over `COVER_REVEAL_MS`. The blurred share is `1 - fraction` at full `MAX_BLUR_PX`, not the progress-curve mapping.
- `isUnfoldHandled` is set in every style, so a style switch to Pixel cannot leave a stuck black overlay.
- A fold attach starts at the current progress radius (unblurred), so there is no blur flash before the first progress callback.
- `FoldAodAnimationController` skips the fold-to-AOD animation in Stock. Turning the unfold framework on creates it, and it decides from fold state and sleep reason rather than unfold progress, so the progress gate alone did not stop it.
- The Duo overlay's initial blur is set on the relayout transaction that carries the overlay's first frame, so it is never presented unblurred.
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
| `MAX_BLUR_PX` | 220 |
| `BLUR_CLEAR_PROGRESS` | 0.95 |
| `EDGE_WIDTH_FRACTION` | 0.6 |
| `EDGE_STRIPS` | 16 |
| `OPEN_ANGLE_DEGREES` | 165 |
| `TOUCH_UNBLOCK_PROGRESS` | 0.8 |
| `COVER_REVEAL_MS` | 350 |
| `READY_TIMEOUT_MS` | 2000 |
| `SAFETY_TIMEOUT_MS` (Duo inner overlay) | 5000 |
| `LATCH_TIMEOUT_MS` | 10000 |
| `TIMEOUT_MS` (`DuoDualPanelSession`, cover kept lit) | 1500 |
| `POWER_OFF_OVERDUE_MS` (`DuoDualPanelSession`) | 1000 |

## Failure handling

- Animator scale 0: Duo runs no visuals and reports ready at once. system_server does not keep both panels lit.
- Cross-window blur unavailable (Battery Saver, thermal): no blur runs and the dump shows `blurAvailable=false`.
- A failed cover frame ends the cover part and logs `cover frame failed`. A failed cover overlay attach logs `failed stage=attach`.
- An exception while attaching the overlay removes it, reports ready, resets Shell and logs the stage and exception class.
- On finish, sleep or timeout the overlay is removed and Shell surfaces are reset.

## Observability

Dumpable `YrrpFoldAnimation`, one `name=value` per line:

- Inputs: `settingValue`, `selectedStyle`, `latchedStyle`, `configEnabled`, `hingeAngleEnabled`, `animationsEnabled`, `blurAvailable`.
- Device: `hingeSensorPresent`.
- Decision: `eligible`, `blockedBy` (`CONFIG_DISABLED`, `HINGE_ANGLE_DISABLED`, `NO_HINGE_SENSOR`, `STYLE_STOCK`, `ANIMATIONS_DISABLED`, `NONE`).
- Lifecycle: `phase` (`IDLE`, `UNFOLDING`, `FOLDING`, `COVER_REVEAL`, `COVER_OPENING`), `overlayAttached`, `starts`, `completions`, `cancellations`, `lastCancelReason`.
- Duo blur: `dualPanel` (the frozen cover frame is shown on the cover's display), `coverBlurFrontPx` and `innerBlurFrontPx` (the x position of the blur front in pixels, `-1` until first recorded). Both are written at the start and end of a part, never per frame.
- Fold lock: `foldLockValue` (the selected user's value, `null` when unset or on an unsupported device), `foldLockForced` (Duo is on and SystemUI rewrote the value at least once since boot).

LogBuffer `YrrpFoldAnimationLog` (100 entries) records transitions only: style latched or released (forced, with reason), overlay added or removed, Shell reset, cancel reason, failure stage plus exception class, and each fold-lock rewrite (`foldLock forced from=<value> user=<id>` or `foldLock force failed ...`). Nothing per frame, and no package names, task names or UIDs.

SystemUI log lines for the cover:

- `cover hinge first angle=<degrees> state=<state>`: the first reading after listening starts. Arming decides on it.
- `cover hinge unavailable`: the hinge source did not start.
- `cover frame attach display=<id> attempts=<n> ok=<bool>`, then `cover frame display=<id> shown=true`: the frozen picture is attached.
- `cover frame failed display=<id>`: the frame did not attach, and the cover part ends.

system_server logs these lines under the tag `YrrpDualPanel` in `LogicalDisplayMapper`, and `dumpsys display` prints `yrrpDualPanel=<state> display=<id> lastEnd=<reason>`:

- Gate: `YrrpDualPanel: gate duo=<bool> interactive=<bool> started=<bool> session=<state>`
- Start: `YrrpDualPanel: start from=<state> to=<state> display=<id>`
- End reason: `YrrpDualPanel: end reason=<OPENED|TIMEOUT|CANCEL|CLOSED> display=<id>`
- Power-off done: `YrrpDualPanel: power-off done display=<id>`
- Power-off overdue: `YrrpDualPanel: power-off overdue display=<id>`

To read all three sources, run:

```bash
adb -s bc43bee5 shell dumpsys activity service com.android.systemui/.SystemUIService YrrpFoldAnimation YrrpFoldAnimationLog
adb -s bc43bee5 shell dumpsys display | grep yrrpDualPanel
adb -s bc43bee5 logcat -d | grep YrrpDualPanel
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
- **Cover frame gap.** The cover's own display can show a black frame or a mirror for an instant before the frozen picture attaches.
- **Slow open.** The 1500 ms timeout turns the cover off during a slow open, so the cover goes dark before the inner screen finishes clearing.
- Sleep with the Duo overlay attached records two cancellations (overlay `ASLEEP`, selector `SLEEP`); `lastCancelReason` depends on order.
- A handshake latch with no gated start is held 10 s and then counted as a `LATCH_TIMEOUT` cancellation.
- Booting folded triggers a first cover reveal.
- **Hinge pause.** AOSP's `config_unfoldTransitionHalfFoldedTimeout` (1000 ms, unchanged) finishes a transition when the hinge stops for 1 s. A longer pause settles to fully open and unblurred, and closing again then starts a fold. Pixel behaves the same. Raising the timeout would collide with the 5 s Duo safety timeout, so the device check pauses for under 1 s.
- One `SurfaceControl.Transaction` per progress frame, and the Stock provider runs on a background handler; check cost in Perfetto.

## Local tests cannot prove

- That any SystemUI, Shell or Settings code compiles or passes: nothing was built.
- The look of the directional blur and the frozen cover picture, and blur validity on the SurfaceControl view host.
- Frame rate at 120 Hz.
- That Stock matches the previous build, including Launcher3.
- Interrupted transitions (sleep mid-unfold, reversal) leaving no blur, lit cover or blocked touch.
- Hub row hidden on `salami`, and the dump there showing `blockedBy=CONFIG_DISABLED`.
- `cmd overlay list android | grep yrrp.overlay.xueying.unfold` shows `[x]` on xueying.
- `Settings.apk` contains `YrrpFoldAnimationSettings`; the signed `SystemUI.apk` dex contains `DuoFoldOverlayAnimation`, `FoldAnimationStyleSelector`, `StyleGatedUnfoldTransitionProgressProvider` and `DuoCoverFrame`.
