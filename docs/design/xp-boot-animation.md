# Windows XP boot animation

Visual reference: private artifact "XP Boot Prototype",
https://claude.ai/artifact/PU4WEDGNYZRWHWNqvFa7tp (approved look: Professional, phone layout,
nearest scaling).

`bootanimation` can draw the Windows XP Professional boot screen with its moving progress bar on
boot, and an XP-style "Windows is shutting down..." screen on shutdown and reboot. It is a third
boot animation style beside Matrix and LineageOS, and reuses the Matrix hook, properties, status and
fallback rules in `matrix-boot-animation.md`.

## What it looks like

- **Boot:** a black panel with the XP logo and the empty bar frame centred at 47 % of the height,
  "Copyright © 1985-2001 Microsoft Corporation" at the bottom-left and the Microsoft wordmark at the
  bottom-right. Every source pixel is one of the 16 XP palette colours, scaled nearest-neighbour by
  `panelWidth / 640` (2.25× on salami, 1.6875× on xueying).
- **Bar:** three blue 6 px cells move through the frame in 18 steps of 80 ms (1.44 s per sweep):

  | Step | Bar |
  |---|---|
  | 0–1 | Enters from the left, clipped (one cell, then two). |
  | 2–14 | All three cells, 8 frame px further each step. |
  | 15–16 | Leaves to the right, clipped. |
  | 17 | Empty. |

  The step follows monotonic wall time. After a CPU stall the bar jumps to the current step.
- **Boot completion:** the panel cuts to black at once. No outro, no minimum show time, so home
  appears no later than with stock.
- **Shutdown and reboot:** one static frame until power-off: a `#5A7EDC` body, `#00309C` bands at
  the top (10 %) and bottom (12 %) with light and orange gradient lines, the XP logo, and white
  "Windows is shutting down...". The look is approximate; no verified reference exists. The frame
  is drawn once, and again only if a display's surface is recreated (a resize). An exit
  request during shutdown stops the renderer with `EXIT_PENDING`, as Matrix does.

## Setting

**Settings → YRRPs → Animations → Boot animation → Windows XP** writes
`persist.yrrp.bootanim.style=xp`. The row has no gear; **Finish intro** applies to Matrix only.

```bash
adb shell setprop persist.yrrp.bootanim.style xp       # Windows XP
adb shell setprop persist.yrrp.bootanim.style matrix   # restore
```

Exact `xp` reads as XP in both `YrrpMatrixConfig` and `YrrpBootAnimationStore`. A build without this
feature reads `xp` as Matrix. No new property or SELinux rule exists.

## Routes

| Style | Boot | Shutdown or reboot |
|---|---|---|
| XP, init OK | XP boot screen | XP shutdown screen |
| XP, init failed | Stock zip path | Exit at once |

## How it works

- `playYrrpBootAnimation()` dispatches the `Xp` route to `playYrrpXp()` in `BootAnimationXp.cpp`.
- On boot it loads `yrrp-xp-boot.png` and `yrrp-xp-bar.png`; on shutdown only
  `yrrp-xp-shutdown.png`. Boot textures keep `GL_NEAREST`; the shutdown texture uses `GL_LINEAR`.
  `GL_DITHER` is off on boot and on for shutdown.
- `YrrpXpShader.h` draws one textured quad per blit. The stock image shader cannot sample a
  sub-rect of a texture, which the frame regions and the clipped bar need.
- `YrrpGlProgram` compiles and links both the Matrix and XP programs and frees everything on
  failure.
- Pure C++17 units, host-tested:
  - `YrrpXpLayout`: `xpBarSpan(step)`, `xpLayout(W, H)`, `xpBarBlit`, `xpQuad`.
  - `YrrpXpTimeline`: step from elapsed nanoseconds, one draw per 80 ms tick (once on shutdown),
    wake time, exit outcome.
- The renderer draws a frame (one swap per display) only when the step changes, about 12.5 times a
  second, or when a display's surface was recreated. It polls for exit every 16.67 ms.

## Assets

`cmds/bootanimation/tools/gen_yrrp_xp_assets.py` renders three plain 8-bit RGB PNGs into
`core/res/assets/images/` from the Wikimedia Commons capture
[Windows_XP_Professional_boot.png](https://commons.wikimedia.org/wiki/File:Windows_XP_Professional_boot.png)
and Noto Sans CJK JP Medium. Neither input is committed; the PNGs are the source of record.

| Asset | Size | Content |
|---|---|---|
| `yrrp-xp-boot.png` | 640×480 | The capture with the bar interior (x 257–377, y 353–363) black. |
| `yrrp-xp-bar.png` | 22×9 | The bar sprite cut from the capture at x 291, y 354. |
| `yrrp-xp-shutdown.png` | 720×1608 | The approximate shutdown screen. |

The boot and bar images are Microsoft artwork. The user chose on 2026-10-21 to ship them in this
personal ROM and accepted the copyright and trademark risk.

The bar timing comes from ReactOS `bootanim.c` (`RB_SQUARE_CELLS`, 80 ms, 18 steps), not from a
measurement on XP. Both Wikimedia captures sit exactly on that step model: Home Edition at step 1,
Professional at step 6.

## Observability

The shared `sys.yrrp.bootanim.*` properties carry the XP decision:

| Property | XP values |
|---|---|
| `sys.yrrp.bootanim.config` | `style=xp/prop fullIntro=… shutdown=<0|1>` |
| `sys.yrrp.bootanim.route` | `XP`, or `STOCK` / `EXIT` after an init failure |
| `sys.yrrp.bootanim.blocked_by` | `none`, `ASSET_XP_BOOT`, `ASSET_XP_BAR`, `ASSET_XP_SHUTDOWN`, `COMPILE`, `LINK`, `GL` |
| `sys.yrrp.bootanim.result` | `outcome=<COMPLETED|EXIT_PENDING|SWAP_FAILED> frames=<frames drawn> avgMs=<gap> maxMs=<gap>` |

On boot, `avgMs` near 80 means the bar kept its cadence; a large `maxMs` marks a stall.

Logcat:

- `YrrpMatrix`: `config style=xp/prop …` (logged before dispatch).
- `YrrpXp`: `init ok panel=<w>x<h> displays=<n> scale=<s>`, `layout centre=… copy=… ms=…`,
  `init failed reason=<…> fallback=<STOCK|EXIT>`, `exit observed t=<s> step=<n>`,
  `end outcome=<…> frames=<n> avgSwapGapMs=<x> maxSwapGapMs=<x> durationS=<s>`.
- `YrrpGl`: `shader compile failed …` or `program link failed …`, for XP and Matrix.

```bash
adb shell getprop | grep yrrp.bootanim
adb logcat -b all -d | grep -E 'YrrpXp|YrrpGl|YrrpMatrix'
```

These lines carry no user data.

## Status

- Issue: YRRPs/project#112.
- Framework: `YRRPs/android_frameworks_base` branch `feat/xp-boot-animation`, base `656622dc1e1c`.
  PR: https://github.com/YRRPs/android_frameworks_base/pull/21 (draft).
- Settings: `YRRPs/android_packages_apps_Settings` branch `feat/xp-boot-animation`, base
  `fc421630d6`. PR: https://github.com/YRRPs/android_packages_apps_Settings/pull/18 (draft).
- Docs: https://github.com/YRRPs/project/pull/113 (draft).
- Device acceptance: pending the first release with both PRs.

## Differences from the spec

- `xpBarSpan` lives in `YrrpXpLayout`, not a separate `YrrpXpBar` unit.
- Shader compile and link moved into the shared `YrrpGlProgram`, so the Matrix compile and link
  failure lines now use tag `YrrpGl`.

## Local tests cannot prove

- The look on the panel, including nearest-neighbour scaling at 2.25× and RGB565 rounding.
- Texture orientation (top row at the top) and sub-rect sampling on the Adreno driver.
- Shader compilation on device, and that `BootAnimationXp.cpp` builds in the tree: locally it is
  only syntax-checked against a stub of `BootAnimation.h`.
- The 80 ms cadence under boot load.
- The shutdown screen from the power menu until power-off.
- The Settings rows on device; the Robolectric tests are compiled, not run, by
  `m:SettingsRoboTests`.
- No `avc: denied` lines for `bootanim`, `system_app` or `shell`.
