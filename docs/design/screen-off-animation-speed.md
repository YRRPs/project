# Screen-off animation speed

You can play custom screen-off effects (CRT today) at 0.5×, 0.75×, 1×, 1.5×, or 2× speed from **Settings → YRRPs → Screen-off animation → Speed**. Stock ignores the speed and keeps its own timing. This answers YRRPs/project#7.

## Source of record

- `YRRPs/android_frameworks_base` branch `feat/screen-off-animation-speed`, base `5b8babad443e42708d777a07e778e27279296cb5`.
- `YRRPs/android_packages_apps_Settings` branch `feat/screen-off-animation-speed`, base `0f2bc7ac8c9eca4b2f4eed18cc1de18f3db37d73`.

## Where CRT runs now

Issue #7 was written against the first CRT, which ran inside SystemUI's unlocked screen-off reveal (500 ms, 100 ms in min mode, AOD 600 ms later). Framework PRs #5 (`bcdaaafc`) and #6 (`df2a6179`) moved CRT into `DisplayPowerController`. SystemUI now reports `CRT_OWNED_BY_DISPLAY` and runs no reveal, so the reveal and AOD scheduling no longer time CRT.

`DisplayPowerController` plays CRT as a `ColorFade` in `MODE_CRT` (CRT colour-fade mode), driven by `mColorFadeOffAnimator` with a linear interpolator. Each frame depends only on the fade level, so scaling the animator duration scales the whole effect evenly.

## The four questions

### 1. Can CRT run longer or shorter without breaking the choreography?

Yes, from 250 ms to 1000 ms. The coupling under `DisplayPowerController`:

- **Screen off (OFF path):** the display stays `STATE_ON` at awake brightness until the animator ends, then turns `STATE_OFF`. A slower CRT keeps the panel at full power longer, up to 500 ms more at 0.5×.
- **Doze entry (DOZE path):** CRT plays inside SystemUI's `STATE_ON` hold under `POLICY_DOZE`, which lasts `ENTER_DOZE_DELAY` (4000 ms). Doze updates during the hold return early, and only an awake policy cancels CRT. When CRT ends, the next pass reveals the AOD that SystemUI drew and jumps straight to doze brightness. A 1000 ms CRT ends well inside the hold.
- **Wake:** `cancelCrtForWake` cancels the animator at any progress, whatever its duration.
- **Animator duration scale:** still multiplies the system-server animator on top of this speed. At scale 0 the animator ends at once.
- **Must not move:** the Stock fade (400 ms), the doze hold, and the gate order in `CrtScreenOffPolicy`.
- **Not settled locally:** whether keyguard lock timing waits for `STATE_OFF`. The device check at 0.5× settles it.

### 2. Where does the speed apply?

To the animator duration of the whole effect. CRT's progress mapping inside the window is unchanged.

### 3. Per-feature or shared?

One speed for every custom screen-off effect. Stock ignores it. A future effect declares its own 1× base duration and calls `ScreenOffAnimationSpeed.scaledDurationMillis`. Pulse Rainbow (#3) keeps its own 6 s cycle. A YRRP-wide animation speed is out of scope.

### 4. Range

50–200 percent of normal speed: 1000 ms to 250 ms for CRT. The slowest value stays below a quarter of the doze hold.

## Setting

Private per-user secure key `lineage_screen_off_animation_speed`, a speed percent: 200 plays twice as fast. Unset reads as 100. The display service clamps any stored value into 50–200. Settings writes only the listed values.

| Label | Stored | CRT duration |
|---|---|---|
| 0.5× | 50 | 1000 ms |
| 0.75× | 75 | 666 ms |
| 1× | 100 | 500 ms |
| 1.5× | 150 | 333 ms |
| 2× | 200 | 250 ms |

The Speed rows stay visible but disabled while Stock is selected.

## Observability

Each CRT start log line carries the effective speed and duration, and `dumpsys display` reports the last ones:

```bash
adb logcat -s CrtScreenOffAnimation
adb shell dumpsys display | grep -A18 'CrtScreenOffAnimation:'
```

Expect, for example, `start path=OFF setting=1 speed=50 durationMs=1000`, and `lastSpeedPercent=50` and `lastDurationMs=1000` in the dump.

To restore the default:

```bash
adb shell settings delete secure lineage_screen_off_animation_speed
```

## Verification

- Local JVM: `ScreenOffAnimationSpeedTest` and `CrtScreenOffRecorderTest` pass with the other pure display units.
- Written, not run locally: the `DisplayPowerControllerTest` speed cases and the Settings Robolectric tests. They need an AOSP test build.
- Device checks after release: unset key gives 500 ms; at 50 and 200, lock timing, AOD reveal, and wake cancellation behave as at 1×; Stock stays at 400 ms.
