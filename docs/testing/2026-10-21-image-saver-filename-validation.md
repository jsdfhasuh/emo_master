# Image Saver File Name Input Validation

Date: 2026-10-21, Asia/Shanghai.

## Source And Scope

- Starting HEAD: `4550d46e34e508cbb2774f7bcda8863d293541e6`.
- Final focused-test HEAD: `cdd690065bd629d0294151cb0310965aa90b9c71`.
- The intervening commit changes device diagnostics, communication UI, and two
  corresponding TCP tests. Those four files do not overlap this implementation.
- Existing uncommitted video-loader work and other worktree changes are retained.
- Changed implementation: Image Saver operator/manifest and a seven-line addition
  to Runtime project path preparation to retain output/input separation when the
  filename is dynamic. No other operator implementation was changed.
- No commit, push, build, deployment, installed-EXE update, real-device access,
  real-model download, user-video processing, or application restart was done.

## Contract

- Operator `vision.io.image_saver`, version `1.1.0`.
- Existing required `image` input and `result` JSON output retained.
- New optional, non-nullable string input `fileName`.
- Without that input, existing `outputPath` behavior is unchanged.
- With that input, replace the basename only; retain the directory from
  `outputPath`, and inherit its extension if the input has none.
- Reject empty/non-string filenames and paths rather than silently falling back
  to a fixed file. Retain Chinese names and directories.
- Encode with OpenCV and use normal/exclusive file creation according to
  `overwrite`. Exclusive creation prevents a competing writer from being
  overwritten when `overwrite=false`.
- The actual saved path remains in `result.path`. Encoding/write errors have
  `E_OUTPUT_WRITE_FAILED`; existing same-name files have `E_OUTPUT_EXISTS`.
- For a connected `fileName`, include the configured parent directory in the
  existing project input/Runtime output-overlap check. Fixed-file projects retain
  the old single-file check. This does not introduce dynamic global-variable
  file-path binding or a new resource framework.

## Final Focused Checks

**91 passed in 19.97 seconds.** Command selectors:

```text
tests/plugins/test_image_saver_operator.py
tests/core/test_runtime_directory.py
tests/runtime/test_image_saver_filename.py
examples/video_yolo_crop_operators/test_example.py
tests/runtime/test_operator_registration.py
tests/runtime/test_operator_port_specs.py
tests/runtime/test_operator_output_contracts.py
tests/core/plugin/test_builtin_parameter_titles.py
tests/core/plugin/test_registry.py
tests/designer/test_image_saver_form.py
tests/runtime/test_operator_debug_resources.py::testFileWriterConfirmationIsBoundToParamsAndPreparedInputs
```

Run with the existing Python environment:

```powershell
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' -m pytest <selectors-above> -q --tb=short
```

Evidence includes:

- Manifest/class agreement and Runtime-to-Designer catalog transport of an
  optional string input, without making it required in old workflows.
- Original static-path saves and pixel equality, dynamic names, extension
  inheritance/change, Chinese paths, invalid names, duplicate handling, and an
  ordinary concurrent-name collision fixture with no overwrite of the winner.
- Relative project path resolution, draft immutability, and unchanged protection
  of input files, project files and Runtime data for dynamic destinations.
- Three separate native Runtime jobs passing different filename strings through
  workflow inputs, including an extensionless name and a Chinese name.
- Explicit spawned debug-worker execution with prepared filename inputs and the
  existing write confirmation requirement. No automatic preview writes.
- Offscreen Qt save-file picker behavior, unchanged legacy parameters and a
  visible string `fileName` pin in the real scene class.
- Both operator-only video projects compile with Designer pins. The save variant
  uses existing variable-read and pure coordinate-message-format operators to
  make `sourceFrameIndex,targetIndex.png`; no TCP/PLC connection is involved.
- A varying multi-target fixture processes 5 sampled frames, including an empty
  middle frame, into 8 individually named PNGs; every saved crop is pixel-equal
  to the corresponding native crop result.
- Both examples complete native Runtime subprocess jobs with the existing tiny
  real ONNX fixture. The saving example writes 5 separate image files for 5
  sampled frames. The example-only suite contains 12 passing cases.
- Existing cancellation/resource-release, empty-detection and final-frame checks
  continue to pass.

Ruff passed for all changed Python files. Mypy passed for the Image Saver operator
with `--follow-imports=silent`. `git diff --check` passed, with only the existing
repository LF/CRLF conversion warnings.

## Expanded Regression

An expanded run including the complete legacy debug-resource suite produced
**88 passed, 6 failed, 1 warning**. The failures are:

```text
testAllBuiltinsAdmittedButExternalResourcesNotWorkflowEnabled
testPlcWriteRequiresOneShotUnlockAndUsesConfiguredValues
testTcpSendExchangeUsesLoopbackOnly
testTcpReceiveAndAcknowledgeThenReleasePort
testCancelBlockedTcpReceiveNeverClaimsSuccessAndReleasesPort
testCameraLifecycleWithSimulatedSdkInActualWorker
```

They use legacy direct PLC/TCP/camera debug routes which are already rejected by
the managed-device diagnostics boundary. The loopback peer times out after its
client is rejected. This is the same set recorded in
`2026-10-21-video-loader-validation.md`, where an archived pre-video HEAD also
reproduced the six failures. That existing baseline evidence is reused here;
this change did not rerun a separate clean-HEAD archive. No assertions were
weakened, failures hidden, or unrelated device routes repaired.

The 91-test final focused run explicitly lists its scope above and is not an
all-green whole-repository claim.

## Acceptance Boundaries

- Data is synthetic video/images plus the repository's tiny ONNX fixture. Real
  user model detection quality and the Helmet_Crops head/shoulder heuristic are
  not acceptance results of these checks.
- Qt evidence is offscreen software automation, not native desktop manual use.
- No frozen application or installed EXE was rebuilt or verified. Running old
  binaries do not hot-load the new input contract.
- Naming is supplied upstream, not an auto-numbering/rename service. Duplicate
  names still follow `overwrite`; the example deliberately defaults it to false.
