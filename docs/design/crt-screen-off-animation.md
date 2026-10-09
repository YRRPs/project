# CRT screen-off animation

> **Current architecture.** Since framework PRs #5 (`bcdaaafc`) and #6 (`df2a6179`), `DisplayPowerController` plays CRT as a `ColorFade` in `MODE_CRT`, and SystemUI runs no reveal for it. The SystemUI sections below describe the first version. For speed and timing, see `screen-off-animation-speed.md`. The glitch effects (Tear, Corrupt, Signal loss) share these paths; see `glitch-screen-off.md`.

Design: `docs/superpowers/specs/2026-10-06-crt-screen-off-animation-design.md`.
Plan: `docs/superpowers/plans/2026-10-06-crt-screen-off-animation.md`.

## Source of record

- `YRRPs/android_frameworks_base` branch `lineage-23.2`, commit `224468bd4cd3d437b5376b7cece3506bc5d96038` ("SystemUI: add CRT screen-off animation"), fast-forwarded from `bb07b63193ca6b435c52ceebecfbf970c4ee8fe4` after device acceptance. `git ls-remote` confirms the remote SHA.
- The builder has no GitHub push credentials. The commit travelled as a thin `git bundle` into a shallow local clone, which pushed it.
- Patch backup: `patches/frameworks-base/crt-screen-off-animation/0001-SystemUI-add-CRT-screen-off-animation.patch`, SHA-256 `18f24f3f923ffe3464d25e2264926ba0c6854025811e0b25a1698241dda5fac6`. Its `git patch-id --stable` matches the commit (`fdb6eb8249d74bb8b8064dbd175aa1bceac49d52`).

## Deviations from the plan

- All test classes live in `multivalentTests/src`. `SystemUiRoboTests` does not compile `tests/src`, and `LightRevealScrimTest` is excluded from host runs as a Kotlin-`internal` user. The four planned ownership tests sit in `CrtCollapseRevealTest`; `tests/src/.../LightRevealScrimTest.kt` is unchanged and runs only on device (`SystemUITests`).
- The CRT scrim state, `screenOffRevealEffectOverride` and `activeRevealEffect` are public with private setters, because multivalent tests compile as a separate Kotlin module.
- CRT masks use `revealGradientEndColor`, so the last CRT frame matches the `revealAmount == 0` frame.
- Clearing the override restores `interpolatedRevealAmount`; `LiftReveal` never resets it.
- The settings repository retries a failed read three times at 1 s intervals, emitting Stock meanwhile, and logs the exception class only.
- The separate `brunch salami` was skipped. The signed release script builds target-files from the same checkout, so it served as the full-build gate.

## Verification evidence (2026-10-06)

- Host tests: written, **not executed**. The combined `atest` failed compiling `SystemUiRavenTests` on unrelated Lineage baseline fixtures (`FakeWifiRepository`, `FakeScreenRecordingService`, `BatteryInteractorKosmos`, `BatteryRepositoryTest`, `SystemEventCoordinatorTest`, `KeyguardStatusBarViewControllerTest`, `FakeHomeStatusBarViewModel`, `ScreenCaptureComponentInteractorTest`, `ScreenRecordingServiceInteractorTest`). No error referenced a CRT file. The same blocker is recorded for Pulse.
- Reviews: spec compliance and three code-quality rounds closed with no open issues.
- `m SystemUI-core SystemUI`: `#### build completed successfully (11:21 (mm:ss)) ####`.
- Packaged classes: `CrtCollapseReveal`, `CrtScreenOffAnimationCoordinator` and `ScreenOffAnimationSettingsRepository` appear in the dex of both the build-tree and the signed `SystemUI.apk`.
- Signed release `20261006-114614` (project `7c8214c7`, framework `224468bd`):

```text
lineage-23.2-salami-20261006-114614-signed-ota.zip
Size: 2,180,328,893 bytes
SHA-256: 8742289d0b432ecf6e7c1e1ecbfab87302bc2ea50caf65b7514517ffd5ad9779

lineage-23.2-salami-20261006-114614-signed-target_files.zip
Size: 4,963,625,358 bytes
SHA-256: c1525045593f9b2912e1d67fd6977a293a9bdc7ae783a088e633d8cc97a7c066
```

- `sha256sum -c` reports `OK` for both files. `yrrp-ota-server` is `healthy` on image `yrrp-ota-release:20261006-114614`.
- Public: `/healthz` 200; `/updates/salami.json` 200 with one release naming the OTA above; `/install/salami/20261006-114614/` 200 with the OTA and recovery images; `Range: bytes=0-0` on the OTA returns 206.

## Device acceptance (2026-10-06)

On OTA `20261006-114614`, with the setting unset, the dump showed `settingValue=0` and `selectedEffect=STOCK`. After setting it to `1`, the user judged the animation good. The dump then recorded `starts=9`, `completions=6` and `cancellations=3` (`transition ended=WAKE`), ending `overrideActive=false` and `animationState=IDLE`, with no `failed stage` entries. Each completed CRT transition logged about 530 ms from start to end.

The dumpable is registered as `CrtScreenOffAnimation`, not `CrtScreenOffAnimationCoordinator` as the plan says.

CRT requires Always-on display because it overrides Android's unlocked screen-off path. `DozeParameters.canControlUnlockedScreenOff()` requires `getAlwaysOn()` and no display blanking. With `doze_always_on=0`, the dump reports `blockedBy=CANNOT_CONTROL_UNLOCKED_SCREEN_OFF` and Stock behavior remains. On 2026-10-07, temporarily setting `doze_always_on=1` produced `started`, `override installed`, and `COMPLETED`; restoring `0` restored the expected block.

## Untested on device

These Task 9 items have no recorded result yet: timeout sleep, Battery Saver, landscape, biometric and tap wake, keyguard occlusion, `FLAG_SECURE`, protected video, 60/90/120 Hz, and the Stock-versus-CRT Perfetto comparison. Inspect state with:

```bash
adb shell dumpsys activity service com.android.systemui/.SystemUIService \
  CrtScreenOffAnimation CrtScreenOffAnimationLog
```
