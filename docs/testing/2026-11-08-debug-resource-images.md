# Debug Resource Paths and Image Results

Date: 2026-11-08 (Asia/Shanghai)

## Source State

- HEAD: `1aa3023d13703dddcf021f9734466cb78f524613`.
- Tests ran with uncommitted Designer and Runtime changes, including the preceding
  JPEG/alpha-PNG import fix. These are not results for the clean HEAD.
- Existing untracked `test/` was left unchanged. No commit or push was performed.
- The user's saved project, model, image, and running Designer were not modified
  or restarted. Temporary test projects and databases were used.

## Behavior

- Single-operator debugging captures a resource directory separately from its
  disposable worker workspace. Embedded Designer sessions pass the current
  project directory; gRPC clients do not send Designer-local directories.
- Without an explicit root, Runtime can use its loaded project directory only
  when its project ID matches the debug draft. The root stays fixed through reset.
- Image, batch, file-list, coordinate, video, YOLO detection/classification,
  PatchCore, image-save, and SQLite paths resolve relative to that directory.
  Absolute paths still work. Missing-root and missing-file errors explain the
  original path, base directory, and resolution outcome. No cwd guessing occurs.
- Submitted parameters remain unchanged. Resolved resource paths are recorded
  in execution diagnostics. No new Runtime loading or automatic upload occurs.
- Result writing and SQLite retain disposable output isolation. Image saving
  remains a confirmed real write, including when its output path is relative.
- Single-operator results default to an available image, preferring overlay,
  crop, then image. Common image labels are localized; tooltips retain port IDs.
- Missing conditional outputs remain selectable with their recorded reason,
  cannot be exported, and do not display stale images. Historical records use
  their own output assets and diagnostics, not current editor parameters.
- Workflow snapshots prefer output images when available, with input images as
  a fallback. Existing workflow operator admission restrictions are unchanged.
- No YOLO overlay/crop options were enabled implicitly. The optional frame input
  remains coordinate metadata, not a second image.

## Verification

Environment: repository Python 3.10 / PySide2 environment. The final combined
suite and final follow-up used `QT_QPA_PLATFORM=windows`.

- Initial path/isolation tests: 26 passed.
- Initial affected regression suite: 73 passed.
- Initial native image-result tests: 5 passed, including 620/1000-pixel windows.
- Combined targeted regression: 155 passed in 282.04 seconds. Covered resource
  paths, resource adapters, PatchCore, debug RPC/connection ownership, image input,
  image results, operator/workflow debug windows, and main-window boundaries.
- Final follow-up after shortening image labels and updating the image-write
  warning: 10 passed in 47.21 seconds. Covered image results/history, image loading,
  PatchCore image display, and relative-path write isolation/confirmation.
- Real ONNX fixture inference passed for YOLO detection and classification with
  both absolute and relative model paths. This is software-fixture validation.
- Native screenshots were inspected; result labels fit the narrow window and
  rendered image dimensions/pixels were asserted. File dialogs are mocked in tests.
- Read-only resolution of the user's `models/2026_10_19_ln_1_0.75.onnx` succeeded
  against the actual `two_station_screw_vision` project directory without changing
  the original relative parameter. The user's model was not used for inference.
- Ruff `F` checks on touched debug modules/new tests and `git diff --check` passed.

Not performed: full repository suite, packaged-product validation, manual
acceptance in the user's running Designer, physical camera/PLC tests, or an
independent remote deployment. Remote-directory selection was tested using local
fixtures; this is not remote-site acceptance.

Normal Designer shutdown/relaunch is needed to load this source change. The
project's relative model path does not need to be rewritten as an absolute path.

## Subsequent Delivery Verification

The later combined delivery run passed all 261 tests, including resource paths,
image import/results, and collapsible parameters. See `2026-11-08-debug-delivery.md`
for the complete invocation scope. The earlier results above remain historical evidence.
