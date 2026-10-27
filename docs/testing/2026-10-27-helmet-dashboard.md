# Helmet Monitoring Page

Date: 2026-10-27. Source base: `331c4d3586cb5cd5141871fbe3da353bca9a56b6`, plus the existing dirty workspace and this scoped change. No other ongoing Designer, Runtime, device, preview, training-model, or startup work was reverted.

## Scope

The external `motorcycle-file-list-riders.emoproj` now has a native QWidget page matching the requested layout: elapsed acquisition time, cumulative rider occurrences, confirmed helmet-wearing percentage, independent live camera preview on the left, and the latest rider crop with verdict, confidence and sample number on the right. The page remains editable in Page Designer. It is not a web dashboard or a static screenshot.

The document remains schema 2.7 with devices schema 1.0. Its original project identity, model paths and parameters, device definition and credentials, runtime capacity, producer loop, Dispatch slot/capacity/skip policy, geometry decisions, production settings, and output paths are preserved. The three save branches now increment job-scoped counters only after the image saver succeeds. A summary subflow executes after the complete per-frame target loop.

Display scopes are deliberately separate:

- `camera`: producer `main -> rtsp_loop -> rtsp_frame`; captures an explicit preview image and acquisition elapsed time.
- `rider`: consumer `rider_consumer -> target_loop -> check -> save_person -> process`; image, accepted label, confidence and sample number belong to the same `process_crop` invocation.
- `statistics`: the consumer's terminal frame result, after all matching people have been saved and counted.

The producer can advance while inference is busy. The right side retains the latest rider result if a later frame has no rider. The left and right images are not claimed to represent the same frame. The frame-level totals can update slightly after the individual rider result.

## Operators

Both new manifests and Python metadata are version 1.0.0 and use the existing registry scan, execution lifecycle and reviewed debug admission path. Neither accesses a device, file or network.

| Operator | Inputs | Outputs | Semantics |
| --- | --- | --- | --- |
| `vision.state.elapsed_time` | Required non-null `after:any` | `seconds:number`, `text:string` | Job/node-local monotonic elapsed time from its first execution, formatted as `HH:MM:SS`. In this project it is refreshed after successful acquisition. It is not application uptime or an independently ticking UI timer. New Job or debug-session reset starts from zero. |
| `vision.math.percentage` | Required `numerator:number`; optional `denominator:number`, default 1 | Nullable `value:number`, `defined:boolean` | Finite nonnegative values only. Computes numerator / denominator * 100. Zero denominator returns null and false, not a fabricated 0%. |

Counts reuse existing global variables, atomic increment, reads and integer addition. There is no second statistics store or background worker.

- Total = with helmet + without helmet + needs review, after successful saves.
- Helmet rate = with helmet / (with helmet + without helmet) * 100.
- Unconfirmed classifications do not enter the rate denominator. With no confirmed classifications the page shows `--`.
- These are per-run detection occurrences, not unique people. No tracking or cross-frame deduplication was added.
- Statistics reset with a new consumer Job. The existing persistent file sample index does not reset.

Explicit preview-only Resize nodes fit the full image to 1280 x 720 and the crop to 480 x 640. They keep normal page-capture lanes bounded. Detection input, classification input and saved original crop pixels are unchanged.

## Rendering

Three local rendering corrections support the actual page:

- Bound strings render as plain text instead of JSON-quoted strings; structured values retain JSON rendering.
- The explicit `sans` appearance option uses Microsoft YaHei UI on Windows instead of an unresolved generic family that fell back to a serif CJK font. The default `system` option remains unchanged.
- A spanning image does not allocate spare grid height to an adjacent scalar footer row. Editor grid behavior is unchanged.

The final page uses a flat 12-column layout, no nested containers, compact top metrics, a two-thirds-width live image, and a one-third-width crop/result section. Native screenshots at both tested scales show the complete page without horizontal or vertical scrolling, clipped labels or overlapping components.

## Verification

Interpreter: `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`.

Private evidence is beside the external project under `validation/helmet-dashboard-20261027/`. The original project and README have fixed backups there. `build_dashboard.py` checks plugin registration, document/binding validation, compilation, save/readback, unchanged protected configuration, and original-file hashes before installation.

Installation completed at the original external path, revision 6, with 13 workflows, one default page and 11 bound components. The installed file and validated candidate both have SHA256 `b2aced8f65749de3927b4f19a4a9c570d83d9dee0cf620b91ea2b1b81d64b343`. The fixed before-page backup has SHA256 `59d50c21cf2996ce633f733d783cbd2725db86d66efd82f6ce7226d5cce04379`. The installer initially hit a Windows default-GBK decode error reading its UTF-8 verification report; explicit UTF-8 decoding corrected the helper before any project write.

- Final native Windows Qt 150%: `verify-20261027-101354-a81cc2`.
- Final native Windows Qt 100%: `verify-20261027-101514-0fe079`.
- Each run opens the actual candidate through `MainWindow.loadProjectSelection`, saves and reopens it, then starts the temporary fixture through the ordinary Designer Run entry. Shared producer/consumer capture is enabled by that entry, not by fabricated display values.
- Native capture alone is replaced by a local-video frame. The actual managed DeviceManager, spawned producer and consumer, original detection ONNX, supplied classification ONNX, page-capture service and shared display session all run.
- Each scale completed 5 admitted items, failed 0, and preserved pixel-exact original saved crops. Queue skip totals were 25 at 150% and 23 at 100%. This is functional evidence, not a throughput benchmark.
- All five crops in this local sample had classification confidence approximately 55.6669%, below the retained 80% threshold. The page correctly displayed needs review, total 5 and an undefined confirmed rate. It did not report a confirmed wearing-helmet decision.
- Separate controlled ONNX cases cover without helmet, with helmet, needs review and recovery on the next invocation. They verify exactly one save/counter increment, the confirmed-only rate, zero denominator, a normal save collision without counter inflation, a no-person frame with unchanged totals, and a fresh Job with zero counters.
- Producer and consumer completed, the dispatch group retired, worker ownership and frame reservations released, and the local decoder was released. Real camera stop/IPC semantics were not changed.
- Focused native regression: **102 passed**, recorded in `focused-native.xml` and `focused-native.log`. This includes real spawned single-node debug execution/reset of both new operators, registration/device registration, and affected renderer/component/loading tests.
- Shared-page regression in a separate native Windows process: **4 passed**, recorded in `dispatch-native.xml` and `dispatch-native.log`. The two non-overlapping successful suites total **106 passed**.
- Ruff passed for the changed source, tests and private generator/verifier. Mypy passed for both new operator implementations. Scoped `git diff --check` passed, apart from a non-failing existing LF/CRLF notice.

Earlier evidence is retained, not relabeled as a pass. The first branch harness used exact float equality for 2/3 * 100 versus 200/3; it was corrected to a 1e-12 tolerance while integer counters and pixel assertions remain exact. An early 150% screenshot revealed unnecessary scrolling; the compact final layout was revalidated with explicit zero-scrollbar assertions.

An expanded mixed offscreen regression did not finish: it reported four setup errors and then a Windows access violation in `run_failure_dialog.showRunFailure`, reached during the normal-run Dispatch test's cleanup. That batch is **not passed**, and no full-suite claim is made. Focused tests were rerun in a separate native Windows process without changing their behavior assertions. The offscreen dialog failure is not claimed fixed by this page work.

## Application Boundary

The source project uses a placeholder RTSP URL until the owner supplies the actual camera connection. This work does not access a real camera/PLC, publish to the production Runtime, restart user processes, change a model or confidence threshold, build/replace an EXE, commit, or release.

Reopen the edited external project instead of saving an older in-memory draft over it. Use Designer and Runtime processes containing the new operators, explicitly write the intended configuration, and start only `main`; the consumer is automatic. An old frozen executable needs a separate rebuild. Page preview and closing the preview remain observation-only actions.
