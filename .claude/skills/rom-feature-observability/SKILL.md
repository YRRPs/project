---
name: rom-feature-observability
description: Use when implementing, planning, reviewing, or debugging any ROM feature that decides at runtime whether to act — SystemUI features, overlays, controllers, listeners, eligibility gates, state machines, capture/teardown lifecycles, Pulse, CRT animation, anything in frameworks/base we add. Triggers on "add logging", "trace logs", "why isn't it showing", "feature does nothing on device", "can't tell which gate is false", "add a dump", "dumpsys for our feature", "LogBuffer", "Dumpable", "observe branch decisions", "debug on device", "it works in tests but not on the phone", and on writing a plan or spec for a new feature (observability is a required plan task). Covers the SystemUI Dumpable + LogBuffer pattern, what to record, privacy limits, and the adb commands to read it. NOT for build/sign/deploy (yrrp-signed-ota-release), NOT for research of old ROM features (rom-feature-research).
---

# ROM feature observability

Announce first: **Using rom-feature-observability to make the feature's runtime decisions visible.**

## Why this exists

Pulse shipped with 98 green unit tests and did nothing on the phone. The controller gated on `DeviceEntryInteractor.isDeviceEntered`, which only emits with `SceneContainerFlag` enabled; on salami it is disabled, so the gate stayed `false`. Finding it took a long session because:

- the controller exposed no state, so no command could say which gate was false;
- release-signed builds have `ro.debuggable=0`, so `am dumpheap` and JDWP attach to SystemUI are refused;
- boot-time logs had rotated out of logcat by the time anyone looked;
- unit tests faked the interactor, so they could not catch a platform flow that never emits.

A single `dumpsys` line listing each gate would have shown `deviceEntered=false` immediately.

## Required for every feature that gates or owns a lifecycle

Add both, in the same change as the feature (or as its own commit before the first device build). Do not defer to "after it works".

### 1. A Dumpable with the current decision

Inject `DumpManager` and register in the component's start/init:

```kotlin
dumpManager.registerNormalDumpable(TAG, this)   // class implements com.android.systemui.Dumpable
```

Unregister in stop/destroy if the owner can be torn down (per-display components can). `dump(pw, args)` prints, one per line:

- every eligibility input as `name=value` (booleans, enums, small ints);
- the computed result (`eligible=`) and, if false, the first false gate (`blockedBy=`);
- lifecycle state: started, latch/failure flags, epoch counters, resource ownership (`captureActive=`, `windowAttached=`).

Read it with:

```bash
adb shell dumpsys activity service com.android.systemui/.SystemUIService <TAG>
```

### 2. A LogBuffer for transitions

Add a qualifier annotation next to the existing ones in `packages/SystemUI/src/com/android/systemui/log/dagger/` and a provider following the existing pattern in `LogModule`:

```java
@Provides @SysUISingleton @PulseLog
public static LogBuffer providePulseLogBuffer(LogBufferFactory factory) {
    return factory.create("PulseLog", 100);
}
```

Log state **transitions** only: an input changed, eligibility flipped, resource acquired or released, readiness reached, timeout or failure (with stage name). The buffer is in memory, survives logcat rotation, and prints in the same `dumpsys` output, so boot-time decisions are still readable an hour later.

## Rules

- **Never per-frame.** No log or buffer entry in audio/FFT/draw callbacks; per-frame paths must stay allocation-free.
- **Privacy.** Record booleans, enums, stage names, and exception class names only. Never package names, UIDs, player IDs, session IDs, media metadata, audio or spectrum values.
- **No raw `Log.d` trace lines** as the primary mechanism: they rotate away and need `setprop log.tag.<TAG> DEBUG` per tag. Keep `Log.w` for genuine failures.
- **Platform-flow check.** For each injected platform flow the feature depends on, read its source and confirm it emits in this build's configuration (check `SceneContainerFlag` and other feature flags in the SystemUI dump). Note the result in the plan.
- **Test it.** One unit test asserts the dump contains each gate name and `blockedBy=` for a known-false input, so the dump cannot silently drift from the predicate.

## Plan and review checklist

- [ ] Plan has an explicit observability task before the first device build.
- [ ] Dumpable registered and unregistered with the component lifecycle.
- [ ] Dump lists every gate, `eligible=`, `blockedBy=`, and owned resources.
- [ ] LogBuffer records transitions only, no per-frame entries.
- [ ] No identifying or audio data in dump or buffer.
- [ ] Every platform flow the gate uses is confirmed to emit in this build's flag configuration.
- [ ] Hardware checklist starts with "run the dumpsys command and record the output".
