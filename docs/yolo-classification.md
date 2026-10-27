# YOLO Classification

`vision.inference.yolo_classification @ 1.0.0` performs image-level classification
using ONNX Runtime CPU. It is separate from `vision.inference.yolo`, which remains
a detection operator. Registration, construction and parameter validation do not
load a model or access any camera.

## Model Contract

- One float32 NCHW input with three channels and fixed height/width. Batch is one
  or the supported dynamic batch marker.
- One float32 output `[1, classCount]` containing finite probabilities in `[0,1]`
  whose sum is one. Exported YOLO probabilities are not passed through softmax again.
- ONNX metadata must declare `task=classify` and a continuous, zero-based `names`
  dictionary with unique non-empty class labels. Detection exports are rejected.
- Only `resize_full_frame_v1` is supported: BGR/gray to RGB, PIL bilinear resize of
  the complete image to the model shape, float32 `/255`, NCHW. This matches the
  training platform's full-frame PIL/torchvision evaluation transform. It does
  not support models trained with the default Ultralytics center-crop transform.
- `Pillow>=10,<13` is a runtime dependency. No torch/torchvision dependency is added.

The caller chooses the preprocessing contract explicitly in the node. For a
training-platform export, check the model's `.classification.json` hash, names,
input size and preprocessing at configuration time. That sidecar is not required
at execution time; resource transfer only needs the ONNX file and node parameters.

## Parameters And Outputs

`modelPath` is an input-file parameter; normal project resource resolution applies.
`confidence` is the minimum Top-1 probability, inclusive, default `0.8`.
`preprocess` is `resize_full_frame_v1`.

Outputs are `classId`, `label`, `confidence`, `accepted`, `acceptedLabel`, `result`,
`image` and `frame`. `label` always preserves Top-1, including below threshold.
`acceptedLabel` is the label when accepted and the empty string otherwise. A
Switch can match known labels and route its default branch to manual review.
`result` contains Top-1, threshold, acceptance and probabilities for all classes.
`image` and `frame` pass through the original pixels and source coordinates,
not the resized model tensor.

The session belongs to the Runtime node/Job lifecycle. It is reused across
consumer invocations and released by `disposeOperator`; changing model path or
Job identity creates a new session. Replacing model bytes at the same path is not
hot reload: end the Job or reset the debug session before using replacement bytes.
Cancellation is checked before loading, before inference and after inference;
no result is manufactured on cancellation or model errors.

Explicit single-node debugging is admitted with the same local Runtime path
preflight as YOLO detection. This is not automatic device access or publication.

## Verification

- `tests/plugins/test_yolo_classification_operator.py`: registration/meta parity,
  real tiny ONNX inference, thresholds, preprocessing pixels, malformed results,
  model errors, lifecycle reuse and cancellation.
- `tests/runtime/test_operator_debug_resources.py`: classification path preflight
  and explicit debug execution in a spawned worker.

The rider consumer project was also checked separately through actual Designer
load/save/reopen, branch-exclusive image saves and a spawned producer/consumer
Runtime using local-video capture substitution and the user's real ONNX models.
These checks do not constitute physical RTSP, frozen-package or field-accuracy
acceptance. Source changes do not update an already running Runtime or EXE.
