# Matrix boot animation

Design: `docs/superpowers/specs/2026-10-11-matrix-boot-animation-design.md`.
Plan: `docs/superpowers/plans/2026-10-11-matrix-boot-animation.md`.
Visual reference: private artifact "Matrix Boot Animation" (approved look).

`bootanimation` renders a Matrix rain of mirrored half-width katakana live on the GPU. The rain
decodes a centred YRRP mark. It plays on boot, shutdown and reboot. LineageOS's stock zip
animation stays available as a style and as the automatic fallback.

## What it looks like

Times are seconds since the Matrix renderer starts.

| Phase | When | What |
|---|---|---|
| Intro | 0–3.0 | Each column starts at its own time between 0 and 1.4 s. Mark cells decode one by one between 1.4 s and 3.0 s, each with a white flash. |
| Loop | 3.0 until `exitT` | Endless rain, two streams per column, per-column periods of 1.3–2.8 s. Each stream cycle re-rolls its trail length. The mark glyphs re-roll slowly and pulse. |
| Outro | `exitT` to `exitT` + 1.2 | No new drops spawn. Mark cells blink out within 0.6 s. A drain line sweeps top to bottom over 1.0 s and blacks out everything above it. From `exitT` + 1.2, every pixel is exact black and the renderer returns. |

- `exitT` is the time at which `checkExit()` first sees `service.bootanim.exit` non-zero.
- **Finish intro** (default on): an exit before 3.0 s is deferred to 3.0 s, so the mark always
  finishes decoding. With it off, the outro starts at the observed exit, even mid-intro.
- **Shutdown and reboot:** the intro, then the loop, with no outro. Power-off ends it. If
  `service.bootanim.exit` turns non-zero during shutdown, the renderer stops at once with
  outcome `EXIT_PENDING`.
- Colours: green trail `(0.06, 1.0, 0.38)`, near-white head cell, pale green mark that flashes
  white on reveal, black background.

## Setting

**Settings → YRRPs → Animations → Boot animation.** The page has two radio rows, **Matrix**
and **LineageOS**. **Finish intro** sits behind the Matrix row's gear. See
`yrrp-settings-ui.md` for the page tree.

| Property | Values | Default | Read as |
|---|---|---|---|
| `persist.yrrp.bootanim.style` | `matrix`, `lineage`, `xp` | `matrix` | Exact `lineage` is LineageOS; exact `xp` is Windows XP (`xp-boot-animation.md`); anything else, unset or empty included, is Matrix. |
| `persist.yrrp.bootanim.full_intro` | `true`, `false` | `true` | `false` or `0` is off; `true` or `1` is on; anything else is on. |

`YrrpMatrixConfig` in `bootanimation` and `YrrpBootAnimationStore` in Settings parse both values
with the same rules. Settings writes only `matrix`, `lineage`, `xp`, `true` and `false`, and never
rewrites a stored value on read. A newer style value on an older build reads as Matrix.

The values are device-wide system properties, not per-user `Settings.Secure` keys: no settings
provider runs while `bootanimation` starts. The user approved this exception to the YRRPs
per-user Secure-key rule on 2026-10-11.

Properties have no change observer. The rows refresh on page resume and after their own
writes, so an `adb shell setprop` while the page is open shows only after you leave and
reopen the page.

```bash
adb shell setprop persist.yrrp.bootanim.style lineage     # LineageOS
adb shell setprop persist.yrrp.bootanim.full_intro false  # Finish intro off
adb shell setprop persist.yrrp.bootanim.style matrix      # restore
adb shell setprop persist.yrrp.bootanim.full_intro true   # restore
```

A change applies on the next boot or shutdown.

## Routes

| Style | Boot | Shutdown or reboot |
|---|---|---|
| Matrix, init OK | Matrix | Matrix (intro and loop) |
| Matrix, init failed | Stock zip path | Exit at once; the stock shutdown dialog stays |
| LineageOS | Stock zip path | Exit at once; the stock shutdown dialog stays |

No `shutdownanimation.zip` exists, so the stock shutdown path would show the AOSP "android"
shimmer. The YRRP hook ends the run instead, which keeps the pre-feature shutdown look.

## How it works

- `BootAnimation::threadLoop()` calls `playYrrpBootAnimation()` after `initShaders()`. It
  returns true when the run is handled (Matrix played, or the route is Exit); false continues
  with the existing `movie()` or `android()` path.
- `BootAnimationYrrp.cpp` holds the GL side: it loads two textures through
  `BootAnimation::initTexture`, compiles and links the shader, draws one full-screen quad per
  display per frame, and frees everything afterwards.
- Pure C++17 units with no Android includes:
  - `YrrpMatrixConfig` parses both properties and chooses the route.
  - `YrrpMatrixTimeline` maps elapsed time to shader time, exit time and phase, applies the
    Finish intro deferral, and ignores exit while shutting down.
  - `YrrpMatrixStatus` formats the `sys.yrrp.bootanim.*` values.
- `YrrpMatrixShader.h` holds the GLSL ES 1.00 vertex and fragment shaders, ported from the
  approved prototype. Each frame is a pure function of `u_time` and `u_exit`.
- Assets live in `frameworks/base/core/res/assets/images/`, beside `android-logo-mask.png`, so
  they ship in `framework-res.apk`:
  - `yrrp-matrix-atlas.png`: 512×512, 8×8 cells of 64 px, white mirrored glyphs on black:
    U+FF66–U+FF9D (56 kana) and `01234578`.
  - `yrrp-matrix-logo.png`: 1024×300, "YRRP" in white on black.
  - `cmds/bootanimation/tools/gen_yrrp_matrix_assets.py` (Pillow) renders both from Noto Sans CJK
    JP Regular and IBM Plex Mono Bold, both under the SIL Open Font License 1.1. The committed
    PNGs are the source of record.
- Geometry follows the panel's short side: a cell is `24 px × shortSide / 720` wide and 1.25×
  as tall, and the mark is `0.86 × shortSide` wide. A taller or wider panel gets more cells,
  never stretched glyphs, so any aspect ratio works.
- Frames are capped at 60 fps: the loop sleeps until 16.67 ms after the frame started. Shader
  time is wall-clock monotonic time, not a frame count, so when the CPU stalls during boot the
  rain freezes and then jumps forward. It never speeds up to catch up.
- The glyph tick wraps at 2048, so the fp32 hash inputs stay small on long boots. Below tick
  2048 the output matches the prototype.
- `GL_DITHER` is on for the Matrix pass to limit banding on the RGB565 surface.

## SELinux and init

`android_vendor_extra` adds `sepolicy/private` to `PRODUCT_PRIVATE_SEPOLICY_DIRS`. Both types
are `product_internal_prop`, so only core domains may use them.

| Domain | `yrrp_bootanim_prop` (`persist.yrrp.bootanim.style`, `.full_intro`) | `yrrp_bootanim_status_prop` (`sys.yrrp.bootanim.*`) |
|---|---|---|
| `system_app` (Settings) | set | none |
| `bootanim` | read | set |
| `shell` | set (acceptance, restoration) | read |

`product.mk` sets `ro.init.shutdown_animation=true`. `ShutdownThread` never starts
`bootanimation`; init restarts the `animation` class during shutdown only when that property is
true, and then also skips `TurnOffBacklight()`.

`bootanimation` treats a run as shutdown when `sys.shutdown.requested` or `sys.powerctl` is
non-empty. Lineage 23.2 init never stores `sys.powerctl` (`init/property_service.cpp`
`PropertySet`), so it always reads empty; `ShutdownThread` sets `sys.shutdown.requested` before
power-off. Until YRRPs/project#114 was fixed, every shutdown run took the boot route. A
shutdown that bypasses `ShutdownThread`, such as `adb reboot`, still reads as a boot.

```bash
adb shell getprop ro.init.shutdown_animation   # must print true
```

`bootanim` cannot read `ro.persistent_properties.ready` under stock policy, so the renderer does
not read it. Whether persistent properties load before `bootanimation` reads the style is a
device check.

## Observability

`bootanimation` exits after boot and has no dump. It publishes its decision as non-persistent
status properties, which the next boot clears.

| Property | Example |
|---|---|
| `sys.yrrp.bootanim.config` | `style=matrix/default fullIntro=1/default shutdown=0` |
| `sys.yrrp.bootanim.route` | `MATRIX`, `STOCK` or `EXIT` |
| `sys.yrrp.bootanim.blocked_by` | `none`, `STYLE_LINEAGE`, `ASSET_ATLAS`, `ASSET_LOGO`, `COMPILE`, `LINK` or `GL` |
| `sys.yrrp.bootanim.result` | `outcome=COMPLETED frames=312 avgMs=16.9 maxMs=211.4` (Matrix route only) |

`/prop` means the value came from the property, `/default` that it was unset or unknown.

Logcat tag `YrrpMatrix` carries the same decision:

- `config style=<matrix|lineage|xp>/<prop|default> fullIntro=<0|1>/<prop|default> shutdown=<0|1>`
- `route=<STOCK|EXIT> blockedBy=STYLE_LINEAGE`, or `init ok panel=<w>x<h> displays=<n>`, or
  `init failed reason=<ASSET_ATLAS|ASSET_LOGO|COMPILE|LINK|GL> fallback=<STOCK|EXIT>`
- `shader compile failed …` or `program link failed …` with the driver's info log, under tag
  `YrrpGl` since the Windows XP style added the shared `YrrpGlProgram`
- `exit observed t=<s> exitT=<s> deferred=<0|1>` (boot only)
- `end outcome=<COMPLETED|EXIT_PENDING|SWAP_FAILED> frames=<n> avgFrameMs=<x> maxFrameMs=<x> durationS=<s>`

```bash
adb shell getprop | grep yrrp.bootanim
adb logcat -b all -d | grep YrrpMatrix
adb logcat -b all -d | grep 'avc: denied' | grep -E 'bootanim|yrrp_bootanim'
```

These lines carry no user data.

## Home-screen delay

`WindowManagerService` waits for `bootanim` to stop before it enables the screen. The outro
holds `bootanimation` open for 1.2 s after the exit request, so home appears up to 1.2 s later
than with stock. On a very fast boot with Finish intro on, the deferral adds up to 3.0 s more:
up to 4.2 s in total.

## Status

- Issue: YRRPs/project#56.
- Framework: `YRRPs/android_frameworks_base` branch `feat/matrix-boot-animation`, base
  `806ce24c8c72`, head `e912908e1780`. PR: https://github.com/YRRPs/android_frameworks_base/pull/12 (draft).
- vendor/extra: `YRRPs/android_vendor_extra` branch `feat/matrix-boot-animation`, base
  `43d73d9`, head `d68baf1`. PR: https://github.com/YRRPs/android_vendor_extra/pull/4 (draft).
- Settings: `YRRPs/android_packages_apps_Settings` branch `feat/matrix-boot-animation`, base
  `6d4230cbb1`, head `b18ac07a08`. PR: https://github.com/YRRPs/android_packages_apps_Settings/pull/8 (draft).
- Framework implementation merged through https://github.com/YRRPs/android_frameworks_base/pull/12 and is recorded on `lineage-23.2` at `b048326c7224`.
- Local checks: `cmds/bootanimation/tests/yrrp/run_host_tests.sh` builds and runs the pure-unit
  gtests with g++ and checks both shaders with `glslangValidator`;
  `test_gen_yrrp_matrix_assets.py` checks the assets. The release manager runs
  `bootanimation_yrrp_matrix_tests`, `m bootanimation framework-res selinux_policy`, the
  Settings tests and the full build.
- Device acceptance: pending the first release with all three PRs.

## Differences from the spec

The code is the source of record. It differs from the spec in these points:

- During shutdown, an exit request ends the renderer at once with `EXIT_PENDING`, instead of
  being ignored until power-off.
- The glyph tick wraps at 2048 (not in the prototype shader).
- The kana atlas uses Noto Sans CJK JP Regular; the spec names only IBM Plex Mono Bold for the
  mark.
- A logo texture with zero height fails init as `ASSET_LOGO`. GL errors left by earlier work are
  cleared before init, so `GL` reports only Matrix errors.
- The Settings section of the spec names the page **YRRPs → Boot animation**; the approved
  hierarchy, and the code, place it under **YRRPs → Animations**.

## Local tests cannot prove

- The look on the panel, and that the outro ends on exact black.
- Shader compilation on the Adreno driver.
- The frame rate under boot load, and how often stalls freeze the rain.
- RGB565 banding with `GL_DITHER`.
- The fallback route on device; local tests cover the init-failure branch only.
- The setting round-trip: **Style → LineageOS** plays the Lineage zip with
  `style=lineage/prop`; **Finish intro** off lets the outro start before 3 s on a fast boot.
- The props race: with Style set to LineageOS, `sys.yrrp.bootanim.config` across 3 boots must
  show `style=lineage/prop` each time. `style=matrix/default` means persistent properties were
  not loaded in time.
- No `avc: denied` lines for `bootanim`, `system_app` or `shell`.
- The shutdown path from the power menu: the Matrix intro and loop play. The panel state at
  power-off with LineageOS style is unproven, because init skips `TurnOffBacklight()` when
  `ro.init.shutdown_animation` is true.
- Search freshness: "Boot animation", "Matrix" and "startup" open the page after the OTA.
- The home-screen delay on a real boot.
