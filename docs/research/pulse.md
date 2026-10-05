# Pulse research

## Feature

Pulse is an audio visualizer drawn at screen bottom. Original implementations offered solid lines, fading blocks, color animation, and media-artwork colors.

## Source lineage

### Dirty Unicorns

Pulse evolved through:

- `DirtyUnicorns/android_packages_apps_DUI`
- `DirtyUnicorns/android_external_DUI`
- `DirtyUnicorns/android_external_pulse`
- Framework glue in `DirtyUnicorns/android_frameworks_base`

Core package: `com.android.systemui.navigation.pulse`

Core classes:

- `PulseController` / later `PulseControllerImpl`
- `VisualizerStreamHandler`
- `Renderer`
- `FadingBlockRenderer`
- `SolidLineRenderer`
- `ColorController`
- `FFTAverage`
- `PulseView`

Original pipeline attached `android.media.audiofx.Visualizer` to session `0`, captured FFT data, and drew into navigation-bar canvas. It required several valid nonzero frames before showing content and limited redraws to roughly 40 FPS.

Original DU implementation was navigation-bar-only. It stopped while keyguard, screen-off, power saver, mute, or screen pinning made visualization inappropriate.

### Resurrection Remix

Resurrection Remix preserved DU history and added:

- Navigation-bar, lock-screen, and ambient hosts
- `VisualizerView`
- One `PulseView` dynamically moved between hosts
- Settings for navbar, lock-screen, ambient, style, color, and always-on behavior

Its Android 10 source remains useful for behavior and historical integration, but its host APIs do not exist unchanged in LineageOS 23.2.

### Modern Android 16 references

RisingOS carries close descendant of legacy Java architecture. It still depends on custom `IAudioService.setVisualizerLocked()` framework changes and old lifecycle patterns.

crDroid Android 16 contains modern Kotlin rewrite:

- `PulseViewController`
- `PulseAudioDataProcessor`
- `PulseEngine`
- `PulseSettingsRepository`
- Multiple renderer implementations
- `AudioPlaybackCallback`
- Active player-session selection with session `0` fallback

crDroid is strongest architectural reference, but its custom SystemUI hosts and settings components still require adaptation to LineageOS.

## LineageOS 23.2 compatibility

Still available:

- `android.media.audiofx.Visualizer`
- `Visualizer(sessionId)`
- FFT and waveform capture callbacks
- `AudioManager.AudioPlaybackCallback`
- Active playback configurations and session IDs for platform code
- SystemUI privileged audio permissions

Absent or obsolete:

- DU `IAudioService.setVisualizerLocked()` and companion constructor checks
- Old Pulse-specific `CommandQueue` callbacks
- `NavigationBarView.getNavbarFrame()` Pulse host
- `CentralSurfacesImpl.getLsVisualizer()`
- Old power-saver “changing” broadcast API
- Pulse-specific settings constants and resources

`Visualizer` itself is not deprecated. Old custom integration around it is obsolete.

## Recommended reuse

- Reuse DU/RR rendering mathematics and visual behavior with license headers preserved.
- Reuse crDroid concepts for playback tracking, session selection, FFT processing, and settings repository.
- Write current Lineage-specific SystemUI lifecycle and host integration.
- Do not port DU audio-service locking.
- Do not dynamically reparent one view between unrelated SystemUI windows.

## Performance constraints

Initial targets:

```text
FFT size: 256 or 512
Capture rate: 20–30 Hz
Draw rate: 20–30 Hz
Memory allocation during frames: zero
AOD animation: disabled
Battery saver: disabled
```

Compressed DSP-offloaded or protected playback may provide no visualizer data. Hide Pulse after silence timeout. Never disable audio offload for cosmetic visualization.

## Privacy and security

- Process spectrum data only in memory.
- Store and transmit no audio.
- Capture only while feature is enabled, playback is local, and a Pulse surface is visible.
- Release Visualizer immediately when disabled or idle.
- Keep lock-screen and ambient modes separate and default-off.
- Treat every audio failure as feature-unavailable, never SystemUI-fatal.

Applicable controls: MASVS 2.1.0 `MASVS-PRIVACY-1`, `MASVS-PRIVACY-3`, and `MASVS-PRIVACY-4`.

## Sources

- [Dirty Unicorns Pulse](https://github.com/DirtyUnicorns/android_external_pulse/tree/r11x/src/com/android/systemui/navigation/pulse)
- [Resurrection Remix Pulse](https://github.com/ResurrectionRemix/external_pulse/tree/Q/src/com/android/systemui/navigation/pulse)
- [RisingOS Android 16 Pulse](https://github.com/RisingOS-Revived/android_frameworks_base/tree/sixteen/packages/SystemUI/src/com/android/systemui/pulse)
- [crDroid Android 16 Pulse](https://github.com/crdroidandroid/android_frameworks_base/tree/16.0/packages/SystemUI/src/com/android/systemui/pulse)
- [LineageOS 23.2 Visualizer](https://raw.githubusercontent.com/LineageOS/android_frameworks_base/lineage-23.2/media/java/android/media/audiofx/Visualizer.java)
- [AOSP runtime resource overlays](https://source.android.com/docs/core/runtime/rros)
- [Repo manifest format](https://gerrit.googlesource.com/git-repo/+/HEAD/docs/manifest-format.md)
