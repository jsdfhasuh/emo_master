# Runtime Review Fixes and Rebuilt Candidate

## Scope and Provenance

On 2026-10-06, repair the three reviewed defects and continue source/frozen
verification. This follow-up does not replace the earlier
[delivery record](runtime-delivery-2026-10-06.md) or its candidate artifacts.

- Application workspace: `C:/Users/jsdfhasuh/my_scripts/emo_master`, the mirror of
  `D:/jsdfhasuh/documents/my_project/emo_master`.
- Application reference HEAD: `9737f04312a2803f824e4bbeab6ea845549edaf7`.
- Packager: `D:/jsdfhasuh/documents/my_project/python_build_script`, reference HEAD
  `fe4921772287cb5d2f5946283f5a4122f5067f89`.
- Both checkouts include uncommitted changes. Preserve the staged ignore/plan files,
  unrelated Designer edits, and previous validation artifacts.
- No commit, push, cloud Action dispatch, public upload, field installation or real
  device operation was performed in this follow-up.

## Repairs

1. In `worker_main.py`, a full bounded diagnostic queue no longer blocks the
   authoritative shared-cell heartbeat. Only optional diagnostic heartbeat packets
   use `put_nowait()` and may be skipped on `queue.Full`. Business events retain
   blocking/backpressure semantics; queue capacity remains 64. Terminal publication
   fences diagnostic heartbeats under the emission lock, then enqueues outside that
   lock. Shared-cell pulses continue while terminal enqueue or feeder flushing waits.
   Legacy callers without a shared cell retain their existing queue behavior.
2. In `operator_runtime/controller.py`, acquire the destination directory owner
   before releasing the previous owner. An occupied destination preserves the old
   project and ownership. A Runtime load exception or negative reply instead clears
   the controller document, releases ownership and disables Start; it must not leave
   a stale document that can be started without its directory lock.
3. In `test_production_boundaries.py`, wait with the existing bounded `waitFor()`
   for the captured result's matching `workflow.completed`. Display results and
   durable events are separate channels, so arrival is not assumed simultaneous.
   Invocation identity and the image/count/NG assertions remain intact.

Add 11 regression cases covering queue saturation, terminal enqueue for completed/
failed/aborted workers, real spawn with persistence paused beyond the heartbeat
deadline, occupied/invalid project switching, load exceptions on switch/reload and
operator Start/ownership consistency. Add heartbeat-liveness coverage to the
Runtime Action's source collection. No new defensive framework was introduced.

## Source Verification

These collections overlap; the counts below must not be added together.

| Check | Result | Evidence or Boundary |
| --- | --- | --- |
| Selected new regressions against the old implementation | 6 failed, 1 passed | Red probe captured in task command history; not a full-suite run |
| Initial focused repaired modules | 39 passed | Heartbeat, worker finalization, production boundaries and operator window |
| Updated Action source collection, executed locally | 105 passed, 73.18 s | [action-regression.xml](runtime-review-fixes-2026-10-06/action-regression.xml) |
| Runtime/core/operator-view/package-entry collection | 1286 passed, 1 skipped, 418.87 s | [source-regression.xml](runtime-review-fixes-2026-10-06/source-regression.xml) |
| Previously flaky result identity test | 10/10 independent subprocess runs passed | `identity-01.xml` through `identity-10.xml` in the evidence directory |
| Ruff | PASS | `src`, `tests` and Runtime scripts |
| Mypy | PASS, 331 source files | Repository configuration; untyped bodies are not all checked |
| Packager Runtime/provenance/workflow/default-build unittest | 27 passed | Local fixture/unit validation, not cloud publication |

The single skip is
`tests.core.plugin.test_icon_resources::testSymlinkCannotEscapeRoot`: symlinks are
unavailable in this environment. It is not counted as a pass. The entire Designer
suite and whole-repository CI were not rerun; historical failures remain recorded in
[the earlier source record](standalone-runtime-2026-10-06.md).

Repeat the broad source collection from the application root with Python 3.10 and
`QT_QPA_PLATFORM=offscreen`:

```powershell
python -m pytest tests/runtime tests/core tests/ui/operator_view `
  tests/test_windows_package_entry.py tests/test_package_bootstrap.py `
  tests/test_operator_runtime_validation.py -q `
  --junitxml=manual_test_workspace/runtime-review-repeat/source-regression.xml
```

The Action's exact narrower collection is in
`../../.github/workflows/runtime-windows.yml`. From the packager root, its focused
check is:

```powershell
python -m unittest tests.test_runtime_target tests.test_master_release_provenance `
  tests.test_workflow_safety tests.test_build_baseline -v
```

## Rebuilt Frozen Candidate

Clean build with the existing isolated build environment and pinned PyInstaller
6.22.2 passed. The central publisher then ran `-BuildOnly -SkipBuild` against that
newly rebuilt directory, passed frozen acceptance and created a new ZIP. SkipBuild
does not imply an old dist was accepted or that publication was requested.

| Artifact | Identity |
| --- | --- |
| EXE | `D:/jsdfhasuh/documents/my_project/python_build_script/dist/EmoMasterRuntime/EmoMasterRuntime.exe` |
| EXE bytes | `5888067` |
| EXE SHA256 | `f71f83e52627f2cd9c77c02921c10cc514f83bc1931c6f8b71bb798f3ab14be1` |
| ZIP | `C:/Users/jsdfhasuh/my_scripts/emo_master/manual_test_workspace/runtime-review-fixes-20261006/final-delivery/emo-master-runtime-windows-v0.6.1.zip` |
| ZIP bytes | `126468381` |
| ZIP SHA256 | `876dc699cc7a7f071ed1b9914b6e615595c116b32968ee5e7550fe7b50b67600` |

The ZIP's embedded EXE matches the rebuilt dist EXE byte-for-byte by SHA256. The
45 recorded application code files and four packager code files remain unchanged
after verification. [candidate.json](runtime-review-fixes-2026-10-06/candidate.json)
records those file hashes, test hashes, reports and acceptance boundaries.

The entire packaged `worker_main` and operator controller modules, including nested
code, constants, imports and arguments, match live source after normalization of
filenames and line metadata. This comparison covers those two repaired production
modules, not all packaged code; see
[packaged-bytecode.json](runtime-review-fixes-2026-10-06/packaged-bytecode.json).

The legacy manifest's `source_commit` is only the reference HEAD. It does not prove
the dirty implementation is in a commit, and its generated download URLs do not
mean any upload or Release exists. Retain the complete onedir directory and
`_internal`; the EXE alone is not a standalone delivery.

## Frozen and Native Verification

Both the [pre-archive gate](runtime-review-fixes-2026-10-06/frozen.json) and
[extracted-ZIP gate](runtime-review-fixes-2026-10-06/archive.json) passed with
`PYTHONPATH`/`PYTHONHOME` cleared and PATH limited to Windows/system directories.
They verify frozen execution, zero imported Designer modules, 50 built-in operators,
four SQL migrations, CPU ONNX, real spawned workers, project export/install, custom
pages and repeated normal stops/restarts using a synthetic two-blob project.

The rebuilt EXE also passed the native Windows Qt measurement under the same
restricted search environment: first session 60 seconds, then 10 restarts, 11
sessions total. The first session observed 103 completions; each later session
observed four. All stopped with `E_CANCELLED` and actual worker retirement. Diagnostic
retention stayed within its configured limit. The inspected screenshot is nonblank,
with the expected image/count and only operator controls, without overlap.

See [native.json](runtime-review-fixes-2026-10-06/native.json),
[measurement.json](runtime-review-fixes-2026-10-06/measurement.json) and
[the native screenshot](runtime-review-fixes-2026-10-06/operator-runtime-native.png).
Raw build/package/process logs and the native workspace remain under
`manual_test_workspace/runtime-review-fixes-20261006/`.

Reproduction, using a new output path each time:

```powershell
python scripts/validate_operator_runtime.py `
  --executable D:\jsdfhasuh\documents\my_project\python_build_script\dist\EmoMasterRuntime\EmoMasterRuntime.exe `
  --output manual_test_workspace/runtime-review-repeat-native `
  --duration 60 --restarts 10 --qt-platform windows
```

## Remaining Acceptance

Cloud Actions, a clean Windows machine without development tooling, actual camera/
PLC/private model behavior, production cycle/output accuracy, workload-specific
resource budgets and long-duration field runs remain NOT_RUN. Disk exhaustion,
process interruption during update and physical power-loss recovery also remain
NOT_RUN. Restricted search paths on this development machine do not replace a clean
machine, and short synthetic functional success does not establish long-term
production readiness or field sign-off.
