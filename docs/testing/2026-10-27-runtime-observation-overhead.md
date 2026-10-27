# Runtime observation overhead: 2026-10-27

Base HEAD: `331c4d3586cb5cd5141871fbe3da353bca9a56b6`, plus the existing dirty workspace and this uncommitted change. Existing asynchronous inspection, receiver, page preview, flip and startup work is preserved. No project/configuration write, deployment, real-device command, or restart of the user's live Designer/Runtime was performed.

## Evidence and scope

The preceding live sample found worker threads waiting for capacity in the Runtime event queue, Designer main-thread work in log filters and repeated history JSON sizing, and two preview threads spending CPU in PNG encoding. The most recent measured pre-change processing rate was 5.13 FPS while the camera decoded 24.91 FPS. These are pre-change live measurements, not acceptance numbers for this patch.

The raw prior profile remains at `build/runtime-live-check-20261027/consumer-flip-live-1004.json`.

## Changes

- Log choices and error totals now use incremental counts. Ordinary batches no longer scan the retained log buffer. Hidden windows retain bounded records without an automatic table refresh; reopening reconciles the table and preserves a retained selection/scroll anchor. Explicit filter changes still rebuild their matching view.
- Node inspection sizes are computed when a record changes. Frozen definitions and each node's two retained executions have cached sizes. Replacement, pruning, eviction, cancellation and context reset update those sizes. Existing byte/count budgets and execution identities are unchanged.
- Inspection and legacy snapshots share one invocation-local PNG when it represents the full original image and fits inspection limits. Downscaled display bytes never replace original-resolution legacy pixels. There is no cross-invocation image cache.
- Continuous or persistent-consumer Jobs start optional snapshot writes at most once per 50 ms per worker. The bounded existing queue coalesces whole invocations while the encoder waits. This is a total snapshot cadence, not 20 FPS per node and not a business-frame rate limit. Normal completion drains the final pending snapshots without waiting for the cadence timer; cancellation discards pending optional work.
- A committed ordinary event cohort extends/trims the in-memory history once and wakes waiters once. Every event still reaches sinks in sequence. SQLite FULL commits, the maximum cohort size, lifecycle/inspection/display barriers, terminal ordering and cancellation semantics remain unchanged. The bounded event channel can still exert backpressure; this patch does not claim a completely nonblocking event path.

## Focused performance probe

Interpreter: `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe` with `PYTHONPATH=src;.`. Probe: `build/runtime-live-check-20261027/refresh-cost-probe.py`.

The history baseline loads the two state modules from the fixed HEAD above; both files were clean before this turn. The test feeds the same 600 synthetic started/completed events, using three repeats. The camera workflow remained running elsewhere, so these are local cost comparisons, not an isolated hardware benchmark.

| Synthetic history | Before, median ms/event | After, median ms/event |
| --- | ---: | ---: |
| 8 nodes | 0.7134 | 0.2056 |
| 64 nodes | 4.1296 | 0.2119 |

Current log cost for a batch of 20 appended records, including offscreen Qt event processing/paint:

| Retained rows | Visible, median ms/batch | Hidden, median ms/batch |
| --- | ---: | ---: |
| 1,000 | 5.4531 | 0.2872 |
| 10,000 | 7.3640 | 0.6365 |

The log probe is current-only; it is not an identical-fixture before/after benchmark. PNG regression tests count one encode instead of two for same-resolution inspection/legacy output, including 1920x1080, and separately verify original pixels when inspection is downscaled. No new live FPS/CPU claim is made before restarting onto the new source.

## Verification

All test Runtime instances use temporary roots or fakes. Suite counts below overlap and must not be added into a claimed unique total.

- Focused regression: 210 passed in the final rerun. Covers history, node summaries, log controls, asynchronous previews/inspection delivery, event commit barriers/persistence and worker finalization. Output: `build/runtime-live-check-20261027/focused-final.txt`.
- Separate Runtime extension: 184 passed. Includes preview stores/origins, inspection RPC/receiver, live node results, Supervisor, terminal assets/retirement, persistence and event bus. Output: `build/runtime-live-check-20261027/extended-runtime.txt`.
- Designer/offscreen extension: 76 passed after fixing the test setup race below. Output: `build/runtime-live-check-20261027/extended-designer-offscreen-final.txt`.
- Native Windows extension: 99 passed, including normal Run and shared-page result observation. Output: `build/runtime-live-check-20261027/extended-native-first.txt`.
- Native Windows log tests: final reruns passed 89 at 100% and 89 at 150%, including the hidden-timer guard. Outputs: `build/runtime-live-check-20261027/log-native-100-final.txt` and `log-native-150-final.txt`.
- Scoped Ruff, mypy and `git diff --check` passed; mypy covers the seven changed production modules with the repository configuration and `--follow-imports=silent`. Git only reported the existing Windows line-ending conversion warnings.

### Retained first-red evidence

The first mixed offscreen extension reported a test failure and then exited with a Windows access violation at `run_failure_dialog.py:131` (`QMessageBox.open`), during `testNormalRunEnablesSharedPageCapture` cleanup. This mixed run is not counted as passing and no popup fix is claimed. The separate native Windows extension passed all 99 cases.

The earlier assertion failure reproduced separately: `testRuntimeLogDialogReusesWindowWithoutResizingCanvasOrLosingLogs` received the real asynchronous catalog-loaded log after its log reset, in addition to the three expected test messages. The test now waits for catalog state `ready` before clearing logs. Its exact original message-list, selection, filtering and geometry assertions remain intact. Product logging was not suppressed. First-red output: `build/runtime-live-check-20261027/extended-designer-offscreen.txt`.

Full-repository, frozen/package, real-camera performance and human interaction acceptance are not claimed. The already running user processes retain their loaded source. A normal stop and Designer restart is needed to load these code changes; no new project publication is required by this patch.
