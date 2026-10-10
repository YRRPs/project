# Signing profiler design

## Goal

Show where the 848 s `signing-target-files` phase of a signed release goes, so the speed-up that follows targets the real cost. The profiler answers three questions for one release:

1. Does the time go to pure-Python code, to C code called from Python (`zlib`, `zipfile`, `hashlib`), or to child tools (`mkfs.erofs`, `fec`, `avbtool`, `java`)?
2. Is the work memory-heavy? It records peak RSS, swap, and page faults.
3. Would a RAM-backed temp directory help? It records bytes read and written, and how much the temp directory holds.

This feature measures only. It changes no signing output.

## Evidence so far

Issue #25 measured release `20261009-092107` on the 56-core builder. Signing took 848 s at 0.85 busy cores:

| Process | CPU-s |
|---|---|
| `sign_target_files_apks` (Python) | 436 |
| `mkfs.erofs` | 138 |
| `fec` | 44 |
| `java` | 26 |
| `lz4` | 22 |
| `avbtool` | 21 |

About 128 s used little CPU, and the cause is unmeasured. `iowait` stayed under 0.06 cores.

Upstream source (`LineageOS/android_build`, `lineage-23.2`, `e5aaa62`) suggests two causes:

- `ProcessTargetFiles` (`tools/releasetools/sign_target_files_apks.py:743`) reads every target-files entry fully into memory and recompresses it with `ZIP_DEFLATED`, one entry at a time.
- `add_img_to_target_files.py:1060` builds partition images in a thread pool only when no output zip is open. During signing one is always open, so the images build one after another.

## Scope

In scope:

- A new fork, `YRRPs/android_build`, of `LineageOS/android_build` on `lineage-23.2`.
- A new module, `tools/releasetools/yrrp_signing_profile.py`, plus step hooks in `sign_target_files_apks.py`.
- A manifest change in `YRRPs/android` that points `build/make` at the fork.
- A change to `scripts/sign-lineage-build.sh` in `YRRPs/project` that turns the profiler on for every release.
- A section in `docs/build/signing.md` on reading the output.

Out of scope:

- Any speed-up of signing. A separate PR follows once the profile picks the fix.
- The target-files build phase (#26) and the post-signing checks (#27).
- `ota_from_target_files`, which already runs in parallel.

## Components

### `yrrp_signing_profile.py` (fork)

When the environment has no `YRRP_SIGNING_PROFILE_DIR` set, `start()` returns a no-op profile. Signing then behaves exactly as upstream.

When the variable is set, `start()` creates the directory, enables `cProfile`, and starts a sampler thread. It returns a profile object with two methods:

- `mark(name)`: ends the running step as `ok` and starts the step called `name`. Each ended step writes one JSON line to `timeline.jsonl`.
- `finish(ok)`: ends the running step with `ok`, stops the sampler, disables `cProfile`, and writes the dump and summaries. Later calls do nothing.

`start()` also remembers the profile it returns. `finish_active()` finishes that profile, and marks the last step not `ok` while an exception is unwinding.

The hooks use `mark()` rather than a context manager on purpose. A context manager would re-indent about 40 upstream lines in `main()`, and every Lineage rebase would then conflict. With `mark()`, the patch only adds lines.

Each `timeline.jsonl` line holds:

- `step`, `ok`, `wall_s`
- `cpu_self_s`: user plus system CPU of the signing process
- `cpu_children_s`: user plus system CPU of child processes reaped during the step
- `maxrss_self_kb`, `maxrss_children_kb`
- `minflt`, `majflt`: page faults during the step
- `read_bytes`, `write_bytes`: from `/proc/self/io` during the step
- `nvcsw`, `nivcsw`: voluntary and involuntary context switches during the step

The sampler writes one line to `samples.jsonl` every 1 s. Each line holds:

- `t_s`
- `rss_kb`, `swap_kb`: from `/proc/self/status`
- `tmp_used_bytes`: used bytes on the filesystem that holds `tempfile.gettempdir()`, from `os.statvfs`. Other writers on that filesystem add noise.
- `minflt`, `majflt`: running totals

`finish()` writes three more files:

- `signing.prof`: the raw `cProfile` dump, readable with `python3 -m pstats`.
- `signing-top.txt`: the top 40 functions by self time, then the top 40 by cumulative time.
- `summary.json`: `nproc`, Python version, total wall time, the step list, peak RSS, peak swap, peak `tmp_used_bytes`, and the temp directory path.

### Hooks in `sign_target_files_apks.py` (fork)

`main()` starts the profile after argument parsing and marks four steps. The `__main__` block calls `finish_active()` in its existing `finally` block:

| Step | Covers |
|---|---|
| `load-keys` | `LoadInfoDict` through `GetCodenameToApiLevelMap` |
| `process-target-files` | `ProcessTargetFiles` |
| `zip-close` | both `common.ZipClose` calls |
| `add-img-to-target-files` | `BuildVendorPartitions` and `add_img_to_target_files.main` |

`cProfile` breaks each step down by function. That includes `AddSystem`, `AddVendor`, and the other per-partition calls.

`Android.bp` adds the module to the `sign_target_files_apks` binary sources and to the releasetools test sources.

### `sign-lineage-build.sh` (project)

Before signing, the script exports `YRRP_SIGNING_PROFILE_DIR=${output_dir}/profile/${build_type}-${build_date}`, so builds of different channels never share a directory. After signing, it prints that path. The deploy script receives explicit file arguments, so the profile directory never reaches the OTA server.

## Error handling

Profiling must never fail a release.

- If the profiler cannot create its directory or write a file, it prints one warning to stderr and turns itself into a no-op. Signing continues.
- If `/proc/self/io` or `/proc/self/status` is unreadable, those fields are `null`.
- A signing error still propagates unchanged. `finish()` runs in `finally`, so the profile covers failed runs too.

## Privacy

The output holds function names, file paths of releasetools source, step names, and resource counters. It holds no arguments, key material, or passwords. `cProfile` records no argument values.

## Overhead

`cProfile` slows Python-heavy code. The `cpu_self_s` and wall time of the profiled release, compared with the 848 s baseline from #25, measure that overhead. The follow-up speed-up PR turns `cProfile` off. The step timeline and the sampler can stay, because they cost under 1 s of CPU per release.

## Testing

Local, test-driven:

- Unit tests for `yrrp_signing_profile.py` in the fork, runnable with `python3 -m unittest` from `tools/releasetools`:
  - an unset variable gives a no-op profile that writes nothing
  - each mark writes one timeline line with every field, and a failed finish marks the last step not `ok`
  - child CPU shows up in `cpu_children_s` after a `subprocess.run` of a busy child
  - the sampler writes samples and stops on `finish()`
  - `finish()` writes `signing.prof`, `signing-top.txt`, and `summary.json`, and a second call does nothing
  - an unwritable directory gives one warning and a no-op profile
- A hook test in the fork, `test_yrrp_signing_hooks.py`, that runs `sign_target_files_apks.main` against an empty zip with the heavy calls patched. Outside a full tree it stubs only the modules that fail to import: `avbtool`, `google.protobuf`, `ota_metadata_pb2`, `apex_manifest`, and `update_payload`. It checks the four steps in order, and that a failure inside `ProcessTargetFiles` marks that step not `ok`.
- A case in `tests/test_sign_lineage_build.py` asserting that the stub `sign_target_files_apks` sees `YRRP_SIGNING_PROFILE_DIR` set to the expected path.

Local tests cannot prove that the hooks run inside the built `sign_target_files_apks` binary, or the overhead on real target files. The release proof covers both.

## Release proof

After the next signed release, `out/signed/profile/<type>-<build_date>/` on the builder holds `timeline.jsonl`, `samples.jsonl`, `signing.prof`, `signing-top.txt`, and `summary.json`. The four steps add up to within 5 s of the `signing-target-files` phase duration, and the release still produces an OTA signed by the release key.
