# Runtime Node Inspection: Background Encoding

Date: 2026-10-26. Source base: `633de0e197f5553168a7786440199f68f3410bed`, with existing uncommitted workspace changes preserved.

## Scope

- Move ordinary node-output inspection resize/PNG encoding/publication onto the existing per-Job snapshot thread. No extra process, global thread pool, workflow migration, or device access.
- Keep the necessary owned copy of operator outputs on submission. Images and companion ports retain one capture identity and the submission timestamp.
- Bound the raw snapshot buffer with the existing 64-entry / 64-MiB defaults, including the active capture. The existing single-oversized-capture exception remains when otherwise empty; this is not a limit on total process memory or encoder scratch space.
- Coalesce whole pending invocations of the same workflow/node/output-port set. Under pressure retire old pending previews; if the active owner still prevents admission, skip the new preview instead of waiting. Only optional snapshots are affected, not business outputs or Dispatch admission policy.
- Publish only immutable staged-file descriptors and small completion metadata over the existing Job event channel. PNG bytes no longer compete with ordinary node events in the production worker channel. This does not claim that ordinary event transport itself can never apply backpressure.
- Keep page-presentation exporters, business image saving, explicit saved-artifact inspection (`captureArtifact`), and breakpoint/step semantics unchanged. Saved-artifact inspection is still synchronous; this change targets ordinary operator image outputs responsible for the measured acquisition bottleneck.

## Asynchronous State Contract

- `node.completed.payload.previewCapture` declares an optional ordinary image capture (`captureId`, `state`) and bounded retirement notices for previously coalesced/skipped captures.
- Private `inspection.snapshot` completion metadata closes that exact capture as ready or failed. Existing identity checks and per-node last-two-execution retention remain in force; skipped or expired executions cannot borrow another invocation's image.
- A completed node whose image is still pending is returned as `PREPARING`, not falsely reported as having no image. An early encoder completion is not reverted by a later `node.completed` declaration.
- Designer polls the exact execution at 100-ms intervals using its existing cancellable image worker. Skipped/failed/expired results are terminal for that read; no unbounded retries were added.
- Successful Job completion drains admitted captures before terminal publication. Cancel/failure discards pending captures, joins the active owner, and closes any remaining pending state. New captures still honor cancellation.
- Failed inspection encoding produces the existing `preview.snapshot.failed` warning and an `inspectionFailed` count without suppressing business results or legacy persistence.
- No public RPC/protobuf or project-schema changes. Existing synchronous writers remain supported without the optional completion metadata.

## Verification

- Before implementation: all 5 initial asynchronous-delivery regression cases failed. They reproduced producer blocking and the lack of a post-completion preparing state.
- Updated older tests intentionally asserting synchronous every-invocation inspection/backpressure to the approved best-effort preview contract. Replacement assertions cover exact frozen pixels, capture identities, explicit retirement, bounded buffers, unchanged business invocation count, and latest admitted persistence.
- Intermediate focused run: 67 passed (snapshot writer, inspection store/delivery, terminal retirement, Designer image worker).
- Intermediate Runtime/transport/regression run: 112 passed (spawn/gRPC, PNG budgets, origin checks, snapshot policy, event barriers, heartbeat finalization, Dispatch integration, YOLO outputs, node inspection).
- Intermediate live-result and Qt regression run: 73 passed (running Jobs, last-two execution inspection, original saved images, result panels/history, and page observation). Qt tests used offscreen; this is not manual desktop acceptance.
- Final backend/transport/live-result regression on the completed source: **185 passed in 91.11 s**. This includes the added active-buffer skip and cross-node retirement cases.
- Native Windows Qt at 100% (`QT_QPA_PLATFORM=windows`, `QT_SCALE_FACTOR=1`): **39 passed in 79.16 s**, covering node result panels/viewers and page observation entrypoints.
- Native Windows Qt at 150% (`QT_QPA_PLATFORM=windows`, `QT_SCALE_FACTOR=1.5`): **15 passed in 36.32 s**, covering node result panels, the real-run image viewer, and live small/4K image/result identity checks.
- The larger combined offscreen invocation **did not complete**. Windows reported an access violation in `run_failure_dialog.py:131` (`showRunFailure`), reached from the Qt status callback during `test_node_image_viewer.py::testTwoRealRunsFollowSelectionWithoutExtraReadsAndViewerCloseKeepsRuntime`. The direct cause of that combined-run failure has not been established; the native rerun does not erase it. No assertion was removed and no unrelated dialog fix was folded into this patch. Do not report the combined run or the full repository as green.
- Ruff: passed on the six changed source files and four changed/new test files.
- Mypy: no issues in six source files with the repository's normal untyped-function checking limits (`--follow-imports=silent`).
- Scoped `git diff --check`: passed (only the repository's Windows line-ending notices).

## Controlled Submission Comparison

Input: a deterministic noisy 1920x1080 uint8 BGR array, 16 consecutive submissions per round, two alternating rounds per mode. Temporary directories only; no camera, inference model, live project, or current Job was used.

The synchronous comparison reconstructs the old arrangement: asynchronous legacy persistence plus an inline call to the existing inspection encoder. The asynchronous case uses the changed writer. Both benchmark callbacks discard inline encoded results, so this measures producer-side copying/encoding admission, not real IPC, disk adoption, or end-to-end detection FPS.

| Mode | Median submit, round 1 | Median submit, round 2 | Written snapshots / round | Coalesced / round |
| --- | ---: | ---: | ---: | ---: |
| Synchronous inspection | 78.633 ms | 106.754 ms | 16 | 0 |
| Background inspection | 1.315 ms | 1.571 ms | 2 | 14 |

Asynchronous final draining took 293 / 391 ms, and the peak accounted raw buffer was 11.865 MiB. Encoding work was moved and redundant previews were coalesced; it did not disappear. These measurements are not a claim of 25-FPS live throughput or reduced total system CPU.

## Delivery Boundary

No running Designer/Runtime was stopped or restarted; no field project, camera/PLC configuration, package, or repository commit was changed by this work. All source edits are local and uncommitted.

Existing processes do not hot-reload these changes. For consistent sender/receiver behavior, finish the current work and reopen the source Designer and its Runtime together before testing. Starting only a new child Job while keeping an old Runtime receiver is not a complete upgrade. Frozen EXEs need a separate rebuild, which was not performed.
