# CRT screen-off animation research

## Feature

Resurrection Remix exposed selectable screen-off animations: fade, color fade, CRT, and scale. CRT collapsed current screen into bright horizontal beam before black.

## Historical implementation

Framework commit:

- [`3c802fa49778` — Screen off animations [1/2]](https://github.com/ResurrectionRemix/android_frameworks_base/commit/3c802fa49778)

Settings commit:

- [`a7544c732d1c` — Screen off Animations [2/2]](https://github.com/ResurrectionRemix/Resurrection_packages_apps_Settings/commit/a7544c732d1c)

RR credited Dirty Unicorns and earlier `ElectronBeam` history. Original concept came from AOSP's Android 4-era display-power animation.

### Framework changes

RR modified `frameworks/base`:

```text
core/java/android/provider/Settings.java
services/core/java/com/android/server/display/ColorFade.java
services/core/java/com/android/server/display/DisplayPowerController.java
services/core/java/com/android/server/display/DisplayPowerState.java
services/core/java/com/android/server/display/ElectronBeam.java
services/core/java/com/android/server/display/ScreenStateAnimator.java
```

`ScreenStateAnimator` abstracted:

```text
MODE_WARM_UP
MODE_COOL_DOWN
MODE_FADE
MODE_SCALE_DOWN
```

`DisplayPowerController` observed `screen_off_animation`, switched animator implementation, then drove normal color-fade level from `1f` to `0f` over stock 400 ms screen-off duration.

### ElectronBeam rendering

`ElectronBeam`:

1. Captured current display into `SurfaceTexture` backed by external OES texture.
2. Created opaque surface above display content.
3. Rendered captured image with OpenGL ES 1 fixed-function pipeline.
4. Pre-rendered three frames to avoid first-frame EGL jank.
5. Kept natural-display geometry aligned across rotation.
6. Destroyed screenshot texture and surface after transition while retaining EGL context for reuse.

CRT cool-down used two normalized phases:

- Vertical stretch/collapse: image compressed toward center horizontal line.
- Horizontal stretch/collapse: bright line narrowed and faded into black.

`HSTRETCH_DURATION = 0.5f`; remaining 0.5 controlled vertical phase. RGB channels used slightly different sigmoid strengths, producing chromatic split. White highlight emphasized final beam.

Scale mode drew captured image progressively smaller around display center with black overlay.

### Settings UI

RR added global animation page with values:

```text
0 Fade
1 Color fade
2 CRT
3 Scale
```

The final RR Q snapshots appear internally inconsistent:

- Framework constants are `0 Fade`, `1 CRT`, `2 Scale`.
- Framework reads `Settings.System.SCREEN_OFF_ANIMATION`.
- Settings UI writes `Settings.Global.SCREEN_OFF_ANIMATION`.

Therefore historical patch set is reference material, not clean cherry-pick candidate.

## Why historical approach is risky now

Legacy `ElectronBeam` sits in system-server display path and captures full display content. It must handle:

- Secure/protected layers
- Rotation and cutouts
- Wide color and HDR
- Multi-display state
- EGL/SurfaceControl lifecycle
- Wake cancellation
- AOD/doze handoff
- Display-driver failures

Old implementation predates modern secure capture flags and does not explicitly propagate secure/protected content attributes. Copying it creates privacy and stability risk.

Applicable security control: MASVS 2.1.0 `MASVS-PLATFORM-3`.

## Current LineageOS 23.2 architecture

Relevant classes:

```text
packages/SystemUI/src/com/android/systemui/statusbar/LightRevealScrim.kt
packages/SystemUI/src/com/android/systemui/statusbar/phone/ScreenOffAnimationController.kt
packages/SystemUI/src/com/android/systemui/statusbar/phone/UnlockedScreenOffAnimationController.kt
packages/SystemUI/src/com/android/systemui/keyguard/data/repository/LightRevealScrimRepository.kt
packages/SystemUI/src/com/android/systemui/keyguard/domain/interactor/LightRevealScrimInteractor.kt
packages/SystemUI/src/com/android/systemui/unfold/FoldAodAnimationController.kt
services/core/java/com/android/server/display/ColorFade.java
```

`ScreenOffAnimationController` coordinates fold-to-AOD and unlocked screen-off animation. `UnlockedScreenOffAnimationController` owns keyguard delay, AOD timing, wake cancellation, jank monitoring, and final lock completion.

Current screen-off reveal runs 500 ms. AOD begins after 600 ms scaled by animator-duration setting. Changing this choreography risks delayed lock, stuck scrim, biometric regressions, or extra full-power display time.

## Recommended MVP

Implement CRT as new SystemUI `LightRevealEffect`, not screenshot-based display-server animation.

Proposed shape:

```text
CrtCollapseReveal
  → LightRevealScrim
  → existing UnlockedScreenOffAnimationController
```

Behavior:

- Shrink visible mask vertically toward screen center.
- Horizontally overscan bounds so corners never remain visible.
- Finish at exact opaque black.
- Preserve existing 500/600 ms timing.
- Allocate nothing per frame.
- Snapshot selected effect when transition starts.
- Restore prior effect on wake/cancellation.
- Never replace biometric or screen-on reveal effects.
- Leave fold-to-AOD and min-mode behavior stock.

This masks live screen without copying app pixels, preserving secure-content boundaries.

### Limitation

SystemUI unlocked screen-off reveal runs only where SystemUI can retain display control, generally AOD-enabled devices without mandatory display blanking.

If CRT must work universally with AOD disabled, implementation must move into system-server `ColorFade` path. That is later, higher-risk phase.

### Visual fidelity

Scrim MVP produces convincing horizontal closure but does not physically squash captured image pixels. Authentic RGB-distorted compressed screenshot requires display-server capture path.

Recommended progression:

1. Mask-only CRT reveal.
2. Tune aperture, glow, and timing on OnePlus 11.
3. Decide whether visual difference justifies screenshot/compositor complexity.

## Settings plan

MVP setting can be private secure key:

```text
screen_off_animation
```

Suggested values:

```text
0 Stock
1 CRT
```

Read through injected settings abstraction. Sample once at transition start. Do not change effect mid-animation.

Honor global animator-duration scale and accessibility animation disablement.

## Fork impact

Required:

- `android_frameworks_base`

Optional later:

- Custom settings package or LineageParts fork
- Product overlay for default enablement

Not required for MVP:

- `frameworks/native`
- SurfaceFlinger
- WindowManagerService
- PowerManagerService
- Display HAL

## Validation priorities

- Power button and timeout sleep
- AOD on/off
- Battery saver
- Portrait and landscape
- Wake interruption throughout 500 ms transition
- Face, fingerprint, and tap-to-wake
- Shade expanded and keyguard occluded
- `FLAG_SECURE` and protected video content
- 60/90/120 Hz
- Repeated sleep/wake loops
- Exact opaque-black final frame
- No delayed or skipped keyguard lock
- No increased display-on residency

Collect existing `CUJ_SCREEN_OFF` and `CUJ_SCREEN_OFF_SHOW_AOD` metrics plus Perfetto display/power/SystemUI traces.

## Sources

- [RR framework screen-off animations commit](https://github.com/ResurrectionRemix/android_frameworks_base/commit/3c802fa49778)
- [RR settings screen-off animations commit](https://github.com/ResurrectionRemix/Resurrection_packages_apps_Settings/commit/a7544c732d1c)
- [AOSP ElectronBeam Android 4.2.2](https://android.googlesource.com/platform/frameworks/base/+/android-4.2.2_r1/services/java/com/android/server/power/ElectronBeam.java)
- [Current AOSP ColorFade](https://android.googlesource.com/platform/frameworks/base/+/master/services/core/java/com/android/server/display/ColorFade.java)
- [LineageOS 23.2 frameworks/base](https://github.com/LineageOS/android_frameworks_base/tree/lineage-23.2)
- [MASVS-PLATFORM-3](https://mas.owasp.org/MASVS/controls/MASVS-PLATFORM-3/)
