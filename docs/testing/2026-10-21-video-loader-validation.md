# Local Video Loader Validation

Date: 2026-10-21 (Asia/Shanghai).

## Source and scope

- Starting HEAD: `31b5de4a604e4facdf636ad39795da0869d908d4`.
- Final observed HEAD: `325f60e4fe7b9210eaf50abddc13d0b2086e8bcb`.
- The intervening commit contains independent TCP/device fixes. Its ten-file
  diff does not overlap the video implementation or modified debug adapters.
- Video changes remain uncommitted. No push, build, deployment, live project
  change, real-device connection or application restart was performed.
- Python 3.10.21, PySide2 5.15.2.1, OpenCV 4.10.0, existing `emo_master` Conda
  environment. Tests use temporary generated videos and isolated Runtime data.

## Implemented

- Manifest-discovered `vision.io.video_loader`, file picker, positive fractional
  interval, color/grayscale, standard image/coordinate outputs.
- Sequential timestamp sampling, FPS fallback for backends without usable
  timestamps, one-sample lookahead, final image with `hasNext=false`.
- Node/Job-local cursor, reset, parameter-change restart, EOF and cancellation
  cleanup through the existing Runtime lifecycle.
- Explicit single-node debug including spawned-worker image output. No new
  automatic resource-execution permission in workflow debug.
- A boolean-While example with persisted Designer pins and video parameter
  schema, plus `docs/video-loader.md`.

## Final focused checks

**104 passed in 11.60 seconds.** Files/selectors:

```text
tests/plugins/test_video_loader_operator.py
tests/runtime/test_video_sample_while.py
tests/runtime/test_operator_registration.py
tests/core/plugin/test_builtin_parameter_titles.py
tests/designer/test_video_loader_form.py
tests/plugins/test_image_batch_loader_operator.py
tests/plugins/test_image_loader_operator.py
tests/runtime/test_image_batch_while.py
tests/runtime/test_image_batch_boolean_while.py
tests/runtime/test_operator_lifecycle.py
tests/runtime/test_operator_port_specs.py
tests/runtime/test_operator_output_contracts.py
tests/designer/test_image_batch_loader_form.py
tests/runtime/test_operator_debug_resources.py::testVideoLoaderSequenceExhaustionResetAndParameterChange
tests/runtime/test_operator_debug_resources.py::testPathsMustExistOnRuntime[vision.io.video_loader-videoPath]
```

Run these with `python -m pytest -q` in the declared environment.

Coverage includes actual generated MP4/AVI decoding and frame contents,
fractional intervals, shorter-than-frame intervals, a one-frame video, Chinese
paths, grayscale, invalid input, per-instance independence, reset/new Job/
parameter changes, cancellation during scanning, real compiled While execution
into Blur (including the final frame), lifecycle cleanup, relative project
paths, manifest agreement, Qt file-picker behavior, and spawned debug workers.
Variable timestamp and missing timestamp behavior also have small decoder
fixtures; these are not real VFR container acceptance.

Ruff passed on all changed Python files. Mypy passed for the new operator
with `--follow-imports=silent`. Scoped `git diff --check` passed (only repository
line-ending conversion warnings).

## Expanded regression and baseline comparison

An earlier expanded run of `tests/plugins`, `tests/core/plugin`, the affected
Runtime suites and Qt forms produced **847 passed, 13 failed, 2 skipped**.
The two skipped tests are not counted as passes. The failures were twelve TCP
client/receive-once tests in `test_communication_operators.py` and
`testSplitLfAckAndUncertainTimeoutNeverResends` in `test_gateway_coordinates.py`.
They expect direct socket ownership but encounter the existing managed-device
runtime requirement, resulting in `E_DEVICE_RUNTIME_UNAVAILABLE` or peer timeout.

The full `test_operator_debug_resources.py` run produced **15 passed, 6 failed**,
plus a loopback peer-thread timeout warning. The six failures concern the old
all-builtins admission expectation, direct PLC write, TCP send/receive/cancel,
and simulated camera debug. Existing managed-device diagnostics reject those
legacy debug routes. Both new video cases passed.

To distinguish pre-existing failures from this change, the starting SHA's
`src`, `tests`, and `pyproject.toml` were exported with `git archive` into a new
temporary directory, without modifying the working tree:

- The two communication test files reproduced the **same 13 failures**,
  with **43 passed**.
- Baseline debug resources plus the numeric debug RPC capability-count test
  produced **7 failed, 13 passed**: the same six resource failures and a
  pre-existing `62 != 67` capability-count assertion.

These failures were not repaired, skipped, or weakened for video delivery.
No claim of an all-green repository suite is made. The expanded runs preceded
the final two timestamp fixture cases and example-pin assertion; the final
104-test run above includes them.

## Acceptance boundaries

- Qt checks use the repository's offscreen QApplication fixture, not manual
  desktop interaction.
- No frozen EXE or installed package was built or tested. Running applications
  must reload the updated source, and packaged applications need a rebuild.
- Actual codec compatibility beyond the generated MP4/AVI fixtures, real VFR
  container handling, and very large user videos were not manually verified.
- OpenCV cannot distinguish every midstream decode failure from EOF.
- A video must be accessible on the Runtime computer; selecting a local file
  does not implement automatic upload to a remote Runtime.
