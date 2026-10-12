# Fold animation research

## Feature

The iPhone Duo (iPhone Fold) animates the fold and unfold. When you open the phone, the content on the inner screen starts squeezed to the cover screen's width and blurred. It stretches to full width and sharpens as the hinge opens. On closing, the inner screen blurs and the cover screen clears from a blur. The effect follows the hinge live.

Sources:

- 9to5Google, "iPhone Duo unfold animation recreated for Galaxy Z Fold 8": https://9to5google.com/2026/09/10/iphone-duo-unfold-animation-recreated-for-galaxy-z-fold-8/
- PhoneArena, "Apple iPhone Duo home screen animation copied for Galaxy Z Fold 8": https://www.phonearena.com/news/apple-iphone-duo-home-screen-animation-copied-for-galaxy-z-fold-8_id183282
- Gulf News, "iPhone Duo's folding animation is a feature no other foldable phone has": https://gulfnews.com/technology/iphone-duos-folding-animation-is-a-feature-no-other-foldable-phone-have-1.500669297

## Device facts: OnePlus Open (`xueying`)

Observed 2026-10-12 on build `20261011-112111`.

| Fact | Value | Source |
|---|---|---|
| Hinge sensor | `hinge_detect google hinge angle Wakeup`, `android.sensor.hinge_angle(36)` | `dumpsys sensorservice` |
| Device states | 0 `CLOSED`, 1 `TENT`, 2 half-folded, 3 `OPENED` | `device_state_configuration.xml`, `dumpsys device_state` |
| Inner panel | `Built-in Screen`, 2268 x 2440, 120 Hz | `dumpsys display` |
| Cover panel | `Outer Display`, 1116 x 2484, 120 Hz | `dumpsys display` |
| Cross-window blur | `ro.surface_flinger.supports_background_blur=1`, `mBlurEnabled=true` | `getprop`, `dumpsys window` |
| Unfold framework | off: `config_unfoldTransitionEnabled` and `config_unfoldTransitionHingeAngle` unset, so default `false` | `LineageOS/android_device_oneplus_xueying` overlays |

## AOSP unfold architecture

The pieces below already exist in AOSP. The feature gates or replaces them; it does not add a second progress source.

| Piece | Role |
|---|---|
| `PhysicsBasedUnfoldTransitionProgressProvider` | Turns device-state and hinge-angle events into a transition progress from 0 to 1 and the start, progress and finish callbacks. |
| `UnfoldLightRevealOverlayAnimation` | Pixel inner-screen reveal. A black overlay attaches at the unfold screen-turning-on handshake, and a light reveal opens it from the hinge. |
| `FoldLightRevealOverlayAnimation` | Pixel cover-screen reveal after a fold, about 200 ms, with a 2000 ms ready timeout. |
| `FullscreenUnfoldTaskAnimator` | WM Shell. Moves and scales every visible fullscreen task with the unfold progress. |
| `UnfoldTransitionHandler` | WM Shell. Claims the display-change transition so the task animator can run. |
| Launcher3 remote progress | SystemUI forwards unfold progress to Launcher3 over a remote progress provider; Launcher3 animates icons with it. |

WM Shell runs in the SystemUI process, so Shell and SystemUI can share one Dagger object.

## Findings

- `config_fold_lock_behavior` is `false` on xueying. That disables `FoldLightRevealOverlayAnimation`, so Pixel has no cover-screen reveal on xueying. A Duo cover reveal needs its own screen-on handshake through `ScreenOnCoordinator`.
- `UnfoldTransitionHandler` holds a claimed transition until it is answered or `FINISH_ANIMATION_TIMEOUT_MILLIS = 5_000` elapses. If SystemUI swallows the progress events (the Stock style), a claim would sit unanswered for 5 s. Stock therefore stops Shell from claiming the transition at all.
- Only SystemUI's `ResourceUnfoldTransitionConfig` reads `config_unfoldTransitionEnabled`. Framework, Shell and Launcher3 do not, so one RRO that sets it to `true` turns the whole path on, and SystemUI can then gate every consumer.
- The black overlays attach at screen-on independently of progress, and are removed only on `onTransitionFinished`. A gate that swallows progress must also skip the overlays.

## Not decided here

- The cover-screen screenshot dissolve (a later, separate feature) and hinge-tuned audio.
- Blur cost at 120 Hz on the 2268 x 2440 panel. `MAX_BLUR_PX` is a constant; a Perfetto comparison of Pixel and Duo is a post-release check.
