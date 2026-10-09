# Glitch screen-off effects

Design: `docs/superpowers/specs/2026-10-09-glitch-screen-off-design.md`.
Plan: `docs/superpowers/plans/2026-10-09-glitch-screen-off.md`.
Visual reference: private artifact "Glitch Screen-Off Directions", directions A, B, and C.

## Effects

Each effect distorts a screenshot of the default display and ends on exact black.

- **Tear:** horizontal slices jump sideways with a red/blue split, some frames drop to 12 %
  brightness, then rows go dark band by band.
- **Corrupt:** macroblocks copy from the wrong place, lose colour channels, or smear, then die
  to black one by one.
- **Signal loss:** horizontal sync wobbles, the picture rolls vertically, static creeps in, and
  the picture fades out under the noise.

## Setting

Per-user secure key `lineage_screen_off_animation`, chosen on **Settings → YRRPs → Screen-off
animation**.

| Value | Effect | Duration at 1× |
|---|---|---|
| 0 | Stock | stock 400 ms fade |
| 1 | CRT | 500 ms |
| 2 | Tear | 600 ms |
| 3 | Corrupt | 600 ms |
| 4 | Signal loss | 600 ms |

Any other value means Stock. `lineage_screen_off_animation_speed` scales every non-Stock effect:
the glitch effects run 1200 ms at 0.5× and 300 ms at 2×.

```bash
adb shell settings put secure lineage_screen_off_animation 2   # Tear
adb shell settings put secure lineage_screen_off_animation 0   # back to Stock
```

## How it works

- `DisplayPowerController` plays every custom effect on the same paths as CRT: screen-off to
  `OFF`, and the lockscreen doze entry. `ScreenOffEffect` maps the setting; `ColorFade.modeFor`
  picks `MODE_CRT` or one of `MODE_GLITCH_TEAR`, `MODE_GLITCH_CORRUPT`,
  `MODE_GLITCH_SIGNAL_LOSS`. Wake cancels any effect at once.
- A glitch mode runs the stock cool-down capture path: `systemScreenshot()`, the screenshot as an
  external texture, and the stock vertex shader. Only the fragment shader differs:
  `core/res/res/raw/color_fade_glitch_{tear,corrupt,signal}.frag`.
- `GlitchSchedule` turns `(effect, level)` into a `GlitchFrame` of uniforms. Each frame depends
  only on the level, so a level always draws the same frame. Pixel sizes are authored for a
  720 px wide screen and scaled to the panel in the shader.
- SystemUI reports `CRT_OWNED_BY_DISPLAY` for every non-Stock value and runs no reveal of its own.

## Security

- The screenshot exists only as the `ColorFade` GPU texture. Nothing writes it to disk, logs
  it, or reads it back to the CPU, and `dismiss()` frees it.
- With `FLAG_SECURE` content on screen, the `ColorFade` layers are marked secure, so screenshots
  and screen recordings cannot capture the glitch frames.
- With protected (DRM) content on screen, a protected EGL context renders it, as in stock.

## Observability

```bash
adb logcat -s CrtScreenOffAnimation
adb shell dumpsys display | grep -A12 'CrtScreenOffAnimation:'
adb shell dumpsys activity service com.android.systemui | grep -A12 CrtScreenOffAnimation
```

- Logcat prints `start path=<OFF|DOZE> effect=<CRT|TEAR|CORRUPT|SIGNAL_LOSS> setting=<n>
  speed=<percent> durationMs=<ms>` and an `end … outcome=` line per transition.
- `dumpsys display` shows `lastEffect=`, `lastDurationMs=`, the outcome counters, and history.
  A shader that fails to compile or link on the device shows as `FALLBACK:PREPARE_FAILED`.
- The `Color Fade State:` dump shows `mMode=` and `mGlitchEffect=`.

## Local tests cannot prove

The look on the panel, shader compilation on the Adreno driver, frame timing at 120 Hz,
`FLAG_SECURE` and DRM behaviour, the AOD handoff, and wake timing. Those are device checks.
