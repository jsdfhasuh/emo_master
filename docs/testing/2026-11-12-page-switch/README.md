# 2026-11-12 Page Designer Switch Freeze

## Scope And Source

- Executor: Codex. Local source repair only; no commit, push, package or restart of the user's Designer.
- HEAD: `51bff936ee2f2a0e4c3712441f8e622e33312c3c`.
- This is a dirty working-tree verification, not verification of the clean commit. Existing ROI, image-editor, collection-filter, plugin-registry and workflow-navigation changes were preserved. Unrelated navigation work continued during this task and is not claimed as verified here.
- The reproduction opened the user's project through `MainWindow.loadProjectDirectory(..., confirmed=True)` and triggered the real Page Designer action in an isolated Designer with a stub Runtime and temporary settings/data directories. No hardware operators were executed.
- Project identity for the final comparisons: SHA256 `81b3265e1d582f3d2275fcf8f23f73961d7db099a18c9c385102fafb5ea5225b`; 46 workflows, 370 nodes, 2 pages, 81 available operator manifests and 2,118 output choices. The field project was only read, and its hash was checked again after each run.

## Cause And Repair

`PresentationStore.snapshot()` previously called `ProjectEditSession.document()`, which serialized and normalized every workflow. Page initialization repeatedly requested these snapshots, built the output catalog twice and rebuilt the initial renderer. `cProfile` with the GUI-thread CPU clock substantially amplified this Python call volume.

The repair:

1. Read and validate only the presentation section for page snapshots, returning a detached copy without a persistent cache. Full project validation remains at load, transaction/checkpoint and save boundaries.
2. Share one validated document within a synchronous page initialization/refresh, build the catalog once, and update workspace chrome once after switching.
3. Construct the first renderer in editor mode, preserving editor grids, binding hints and local preview behavior without an immediate rebuild. Keep the existing `reload(presentation)` hook compatible with grouping/alignment callers.
4. Skip observer document construction when no observer exists.
5. Normalize boundary nodes without recopying all ordinary node parameter schemas. Capture incoming canvas nodes with a deep copy, and keep exported payloads detached.
6. Default the performance-probe UI to lightweight heartbeat/stack sampling. Explicit detailed function profiling remains available and tested; it still has measurable overhead.

## Native Comparison

All listed runs used the Windows Qt backend, application styling and a hidden test window, not the user's live window. These are single-run observations on this machine, not latency guarantees. `open` measures the synchronous toolbar action; `ready` includes draining queued Qt events. Repeat timings are three further flow/page switches with detailed profiling stopped.

| Run | Scale | Detailed Profile | Open | Ready | Repeat Range | Whole-Project Snapshots |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline | 100% | No | 6.74 s | 7.32 s | 1.41-1.92 s | 37 |
| Fixed | 100% | No | 2.78 s | 3.24 s | 0.34-0.56 s | 3 |
| Baseline | 100% | Yes | 47.61 s | 50.52 s | 1.29-1.63 s | 37 |
| Fixed | 100% | Yes | 5.28 s | 5.71 s | 0.27-0.39 s | 3 |
| Fixed | 150% | No | 4.57 s | 4.85 s | 0.72-1.37 s | 3 |

The baseline comparison loads the seven affected implementation modules from the fixed HEAD SHA **in the measurement process only**. Other working-tree changes and current manifests remain the same. It does not check out or overwrite files, and is not a claim about a clean historical build. `result.json` identifies these module overrides and the hashes of the on-disk sources.

Authoritative evidence:

- [Baseline, lightweight](baseline-native/result.json) and [detailed](baseline-native-detailed/result.json).
- [Fixed, lightweight](final-native/result.json) and [detailed](final-native-detailed/result.json).
- [Fixed, 150%](final-native-150/result.json).
- [100% screenshot](final-native/page.png) and [150% screenshot](final-native-150/page.png), visually inspected for page content, property controls and binding hints.
- Each run directory also contains `profile.json`. Earlier `fixed-native*` directories are exploratory, partial-catalog runs and are not the final comparison above.

All final runs asserted unchanged in-memory project payload and undo depth, a clean project-edit state, successful page activation and nonempty output choices.

## Regression Record

- Initial collection was blocked by missing `jsonschema`; the environment also lacked `pytest-timeout`. Dependencies were installed only in ignored `build/page-switch-test-deps`, without changing the running application's Conda environment.
- The first broad run stopped at 4 failures and 730 passes. Three failures exposed the new keyword argument breaking existing renderer-reload instrumentation; the old hook was restored, without relaxing its once-only assertion. The new initialization-count test was corrected to measure the synchronous action before unrelated queued chrome timers. See [initial report](regression-first-failed.xml).
- The focused rerun passed 61 tests, including the failed arrangement cases, initialization, workflow-store ownership and lightweight/detailed probe behavior. See [focused report](regression-focused.xml).
- A subsequent combined run passed the normal core/page-designer portion and continued into shared-renderer tests, then the existing 1,000-navigation/30-floating-window lifecycle test exceeded the imposed 60-second per-test limit. The Windows timeout terminated the process before a final JUnit report could be written; this combined run is **not** recorded as an all-pass run. The captured main-thread stack was in `test_retained_acceptance_lifecycle.py:344 -> drainDeletes():32 -> QApplication.processEvents()`.
- That unchanged lifecycle test passed independently in 50.12 seconds with a 300-second harness limit. All navigation, pixels, owner retirement, file/thread release and native-object assertions remained enabled. See [lifecycle report](regression-lifecycle.xml).
- Core presentation, shared renderer and workflow-state regressions then passed **573 tests**, with **1 deselected**, in 188.61 seconds using a 300-second limit. Only the already independently executed lifecycle test was deselected in this group; it was not silently skipped or counted twice. See [renderer/state report](regression-renderer-state.xml).
- The three successful JUnit reports contain **622 distinct passing test cases**, after deduplicating `classname + name`; none has failures, errors or skipped cases. The interrupted combined run and initial failing run are excluded from this count.
- Targeted Ruff `F` checks and the changed-file `git diff --check` passed. The HEAD remained `51bff936ee2f2a0e4c3712441f8e622e33312c3c` at completion.

Reproduction commands, from the repository root with the project's Python 3.10 interpreter:

```powershell
$python = 'C:\Users\dd109\.conda\envs\emo_master\python.exe'
$project = 'C:\Users\dd109\my_work\emo-master-project\two_station_screw_vision\two_station_screw_vision.emoproj'
& $python -B docs/testing/2026-11-12-page-switch/measure_page_switch.py --project $project --output build/page-switch-check
& $python -B docs/testing/2026-11-12-page-switch/measure_page_switch.py --project $project --output build/page-switch-check-detailed --detailed

$env:PYTHONPATH = "$PWD\build\page-switch-test-deps;$PWD\src"
& $python -m pytest tests/core/presentation tests/ui/page_designer tests/ui/presentation tests/designer/test_workflow_store.py tests/designer/test_workflow_boundary_nodes.py tests/designer/test_performance_probe.py -q --disable-warnings --maxfail=3 --timeout=300
```

## Limits And Activation

- No full-repository suite, packaged/frozen binary test, manual UI acceptance, camera/PLC access or production execution was performed.
- Detailed profiling still adds overhead; lightweight default capture is the representative interaction check. No background worker, global lock, resource watcher or disabled legal workflow was introduced.
- The user's existing process was neither closed nor restarted. Save any pending edits and reopen Designer normally to load the updated source; no live hot patch was attempted.
