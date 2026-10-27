# Runtime Node Inspection: Background Adoption

Date: 2026-10-26. Started at source base `633de0e197f5553168a7786440199f68f3410bed`, plus the existing dirty workspace and this uncommitted change. During validation, unrelated release work advanced HEAD to `da072110c227ab8043c7bf8955101baba246af56`. Its diff was checked: it changes version/release metadata and associated tests, not the Runtime modules changed here. Unrelated Designer, Operator Runtime, versioning, and packaging work was preserved.

## Observed Bottleneck

The preceding live sampling run showed that ordinary inspection PNG encoding had moved to the preview writer, but `EventBridge -> RunInspectionStore.publish -> _copy` still performed image adoption and repeated quota-directory walks. Ordinary worker event publication could consequently wait behind image handling. The earlier sampling file is `build/runtime-live-check-20261026/post-async-inspection-1719.json`; its stack percentages are wall-stack samples, not CPU percentages.

This change addresses that receiver-side path. It does not constitute a new live FPS/CPU measurement.

## Changes

- Each inspection-enabled Job owns one `inspection-receiver-<job>` thread. The existing ordered worker event channel carries file descriptors and completion messages; the event bridge only updates preparing metadata and submits to the receiver, without copying, deleting, or scanning files.
- Eight process-shared credits bound inspection messages across active adoption, receiver queue, and transport. The preview publisher reserves a credit before sending; ordinary node events never reserve one. Receiver completion returns the credit, including adoption failures. Messages include both image descriptors and completion notices; this is not a claim of an eight-image or eight-byte buffer.
- Ordinary output-preview backpressure stays on the existing encoder thread, whose raw queue continues to coalesce optional previews. Cancellation interrupts credit waits, releases the active capture, and does not report an ordinary stop as an encoding failure.
- Final Job handling drains the receiver before removing its watch or retiring the workspace. Abandoned starts and Runtime shutdown close the receiver without joining under locks needed by the adopter. Closing an inspection session does not stop a live Job or strand its preview publisher; descriptors continue to be consumed and staged files released until Job terminal.
- Queued images, including saved-artifact inspection images, remain `PREPARING` until adoption settles. Exact Job/revision/workflow/node invocation identities are retained. An image from a different invocation is never substituted.
- Inspection byte ownership is recorded on the existing execution objects. Retired executions with readers and failed deletions remain charged until actual cleanup. Public asset usage is cached and invalidated by owned promotion, upload, removal, and cleanup operations, rather than rescanning every inspection image. First use after a public mutation still scans public storage.
- Cleanup failure is based on attempted deletion results, not the presence of a newly retired execution that arrived concurrently. After cleanup, adoption rechecks that the target execution is still retained, preventing orphan files outside byte accounting.

No project schema, public RPC/protobuf, event ordering, Dispatch admission, inference model, camera, or PLC configuration was changed. No extra image process or generic resource framework was introduced.

## Verification

- Initial receiver tests could not collect before the new receiver existed; the focused quota test reproduced the old per-image `Path.rglob` call before the size-accounting change.
- A real spawn + loopback gRPC test holds receiver adoption while all **20 of 20** ordinary image-node completions and their count values reach the Runtime event store. After release, the latest image has the exact final invocation identity and expected pixels; adoption runs on the receiver thread, and graceful stop retires it. This is a device-free schema-2.7 fixture, not a camera FPS benchmark.
- Focused tests cover bounded credits, continued ordinary publication while saturated, cancellation, failed transport/adoption, final drain for completion/failure/abort, session close during a live Job, abandoned start, original saved-image readiness, reader-owned bytes, file/inline Job/session/project/global budgets, public-storage cache invalidation, and a steady inspection path that forbids directory walks.
- The first broad run reported **192 passed, 3 failed**. It exposed an obsolete immediate-ready assertion and the real newly-retired-file/failed-cleanup race. The test helper now waits only for bounded `PREPARING`, preserving readiness, identity, pixel, and saved-original assertions. The race was corrected and given a direct regression test. An intermediate rerun passed **199 tests**.
- A further deterministic regression reproduced a file being copied after its execution had been retired during cleanup. The retained-execution recheck fixes it; the test asserts both zero accounted bytes and no remaining image files.
- Final focused backend/transport/live-result run: **205 passed in 90.77 s** on the completed source and regression tests.
- Native Windows Qt, 100% (`QT_QPA_PLATFORM=windows`, `QT_SCALE_FACTOR=1`): **39 passed in 69.34 s**, covering node-result panels/viewers and page observation entrypoints.
- Native Windows Qt, 150% (`QT_QPA_PLATFORM=windows`, `QT_SCALE_FACTOR=1.5`): **15 passed in 30.46 s**, covering node-result panels/viewers, real simulated runs, and small/4K image identity.
- Ruff passed on all eight affected source files and four affected test files. Mypy passed on the eight source files with the repository's normal untyped-function checking limits (`--follow-imports=silent`).
- Scoped `git diff --check` passed; only the repository's Windows line-ending notices were emitted.
- The earlier combined offscreen Windows access violation at `run_failure_dialog.py:131` remains unroot-caused; this change does not resolve or erase it. Native targeted runs passed, but the entire repository and that earlier combined invocation are not reported green.

## Remaining Boundaries

- Encoding, file writes/copies, and rendering still have real cost. Ordinary event persistence/transport can still apply its own backpressure. These changes do not prove compute saturation or guarantee 25-FPS processing.
- Public storage is assumed to be managed through existing store APIs. External manual tampering is not continuously watched; no background filesystem monitor was added.
- Business image saving and explicit saved-artifact inspection (`captureArtifact`) are still synchronous on their producer path. Saved-artifact inspection now uses the background receiver and its preparing state, but its producer may wait for an inspection credit. This is not a claim that every preview/debug path is fully asynchronous.
- Page exporter processes and interactive debug/step execution were not redesigned.
- No running user Designer/Runtime was stopped or restarted, no field project/device was accessed, and no package was rebuilt, committed, or published. Tests used temporary projects and simulated/local operators.
- Existing processes do not hot-reload this sender/receiver change. Finish the current work, then reopen the source Designer and its Runtime together for manual testing. A new child Job alone with the old parent receiver is not a complete update. Packaged EXEs require a separate rebuild.
