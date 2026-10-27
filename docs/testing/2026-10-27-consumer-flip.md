# Consumer-Entry Flip and Valid-Mask Bounds

Date: 2026-10-27. Source base: `331c4d3586cb5cd5141871fbe3da353bca9a56b6`, plus the existing dirty workspace and this uncommitted change. Existing Designer, Runtime, preview, log, and YOLO edits were preserved.

## Scope

The requested changes are limited to moving the live-camera 180-degree correction to the existing Dispatch consumer entrance and reducing the flip operator's valid-mask bounding-box overhead. No queue implementation, preview protocol, new process, device configuration, inference settings, or offline sample workflow was changed.

The editable source was identified from the online Runtime's `sourcePath` using read-only SQLite access:

`manual_test_workspace/rtsp-live-face-pages-20261025/engineering/RTSP Live Face Closeup.emoproj`

Before migration, its differences from the active Job snapshot were resolved resource/output paths and populated operator metadata. The editable source, not the materialized Job snapshot, was migrated.

## Project Changes

- `camera_acquire` now contains only its input boundary, RTSP capture, and output boundary. `source.image` and `source.frame` feed the output directly.
- `camera_once` now starts with `input.image/frame -> orient.image/frame`. The existing flip node, `mode=both`, was moved intact from acquisition.
- Detection, archive input image/frame, corrected preview image/frame, and the consumer's image output all receive the corrected image and matching coordinate frame. The detector's frame still supplies the consumer output and overlay preview.
- The unused acquisition `live_preview` resize node and its edges were removed. The main page's `main_image` binding now targets `camera_once.preview_image`; the existing `main_live` result scope now has entry/scope workflow `camera_once` and an empty call path.
- Main corrected-image refresh now follows consumer completion, not independent acquisition completion. The cropped-face/count/presence scope remains unchanged. No claim of atomic grouping across the two existing page scopes is made.
- Shared `detect`, `sample`, `empty`, and the producer loop remain unchanged. Dispatch still has one consumer slot, capacity 1, and `queueFullPolicy=skip`, which skips new admission when full rather than replacing a queued frame.
- Project identity, resources and their hashes, device definitions/bindings, production settings, global variables, runtime settings, and dependencies were preserved. Source project revision advanced from 3 to 4; schema remains 2.7 with devices schema 1.0.

The private one-off helper is `manual_test_workspace/flip_consumer_entry_20261027.py`. It validates bindings, resources, and compilation before applying; preserves an exact backup; saves through `saveProject`; then verifies readback and compilation again. It has no online Runtime write or hardware access path.

| Artifact | SHA256 |
| --- | --- |
| Original / `manual_test_workspace/flip-consumer-entry-20261027/before.emoproj` | `2ec4cf92e595c1c1299dcd745b97ae13fe29eb3e0c4115a185ebf620c29d9587` |
| Migrated editable source | `61560f32d5091bab8d2ce3e753bbd144fdffdec8112326e7224c1992f2bcf4a0` |

`saveProject` also retains the adjacent `.emoproj.bak`. The dedicated backup above is the fixed original evidence, independent of subsequent saves. Source/resources and the private migration/test artifacts stay in the existing ignored workspace rather than being committed with credentials or models.

## Operator Change

`_transform_operators._frameForWarp` now calls `cv2.boundingRect(mask)` instead of creating `ys, xs = np.nonzero(mask)` and reducing both arrays. The helper already receives a single-channel uint8 valid mask. It preserves the exact nonzero content rectangle, float frame coordinates, empty-result error, and output-to-source transform composition.

For a fully valid 2560 x 1440 image, the old helper allocated two int64 coordinate arrays totaling 58,982,400 bytes (56.25 MiB), in addition to the image and masks. Those arrays are no longer needed. This shared helper also benefits rotate, affine, and perspective operations; their behavior was included in focused regression tests. No manifest or port contract changed.

## Verification

Interpreter: `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`.

- Before the implementation edit, the new no-coordinate-enumeration regression failed at the old `np.nonzero` call; the other 25 transform tests passed. After the edit all 26 passed.
- Tests cover all three flip modes, partial/fractional frame bounds intersected with nonrectangular masks, holes/sparse/edge/single-pixel masks, strided masks, affine and homography source transforms, source image/mask/frame immutability, and the empty transformed-mask error. Existing rotate/affine/perspective and downstream geometry tests remain intact.
- Seven private project tests exercise the actual migrated document with an in-memory camera/Dispatch proxy and fake YOLO results. They verify raw admission without a producer flip, exactly one consumer flip, matching preview pixels/source coordinates, exact archive PNG crop pixels for 0/1/2 faces, a full queue skipping without any flip, and unchanged offline image orientation.
- The initial private harness tried to compare image arrays taken from serialized ordinary events. These events contain string image summaries, so three assertions failed. The harness now observes the real resize return value before serialization, without replacing resize computation or weakening pixel assertions. All seven passed afterward.
- Native Windows Qt 100%: the actual edited file was opened through `MainWindow.loadProjectDirectory`, its graph inspected, saved to a temporary resource-complete copy, and reopened. Temporary Runtime paths and QSettings were used; no Job was started.
- Final combined source/project/native-100% run: **50 passed in 11.22 s**. Native-150% repeat: **1 passed, 7 intentionally deselected in 3.71 s**. The latter selection runs only the native opening test, not an additional full workflow suite.
- Screenshots were inspected at both scales after waiting for the existing asynchronous fit-to-content operation. The consumer entrance and downstream nodes/ports are visible without node overlap.
- Ruff passed on the affected implementation, tracked test, private migration, test, and benchmark files. Mypy passed on the changed source file. Scoped `git diff --check` passed.

Private evidence directory: `manual_test_workspace/flip-consumer-entry-20261027/`, containing `migration.json`, `tests-100.xml`, `tests-150.xml`, `designer-consumer-1.png`, `designer-consumer-1.5.png`, and `benchmark.json`.

Reproduction (PowerShell, repository root):

```powershell
$env:PYTHONPATH = 'src;.'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'windows'
$env:QT_SCALE_FACTOR = '1'
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -m pytest tests/plugins/test_classic_transform_operators.py tests/plugins/test_geometry_bridges.py tests/runtime/test_rtsp_face_project.py manual_test_workspace/test_flip_consumer_entry_20261027.py -q
$env:QT_SCALE_FACTOR = '1.5'
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -m pytest manual_test_workspace/test_flip_consumer_entry_20261027.py -k Native -q
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe manual_test_workspace/benchmark_flip_20261027.py
```

## Paired Microbenchmark

Local synthetic 2560 x 1440 uint8 BGR image, full valid mask, OpenCV 4.10.0 / NumPy 1.26.4. Both variants use the same operator code except that the old variant temporarily restores the exact old frame-bounds helper. Three warmups per variant precede 32 timed samples each, alternating old/new ordering. Image, mask, and complete frame payload equality are checked. Allocation peaks are measured separately with `tracemalloc`, not process RSS or system memory.

| Measurement | Old | New |
| --- | ---: | ---: |
| Bounds + frame helper median | 45.947 ms | 0.069 ms |
| Complete flip median | 62.977 ms | 8.695 ms |
| Complete flip p95 | 69.219 ms | 11.484 ms |
| Complete flip measured peak allocated bytes | 77,418,636 | 18,437,112 |

The complete-operator median is about 7.2 times faster (about 86% less time), and the measured allocation peak drops by about 56.25 MiB. Host contention and allocator/cache behavior affect these local timings. This is not measured camera FPS, end-to-end throughput, or a claim of 25-FPS processing.

## Application Boundary

The final read-only check still found online `documentRevision=1`, `executionRevision=1`, and the flip in `camera_acquire`. The edited source has the flip only in `camera_once`. This confirms the source and currently published configuration remain distinct.

No running user process was stopped or restarted, no online configuration/Job snapshot was overwritten, no camera or PLC was operated, and no package was built, committed, or released. The full repository suite and new live CPU/FPS measurements were not run.

For manual testing, reopen the modified source file rather than saving an old in-memory Designer draft over it; explicitly write the project to Runtime through the normal workflow and start a fresh acquisition/consumer run. Existing worker processes do not hot-reload the helper or graph. A packaged executable would need its own rebuild; this work verified the source checkout.
