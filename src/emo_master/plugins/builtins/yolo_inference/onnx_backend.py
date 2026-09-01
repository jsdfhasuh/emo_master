from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np


class OnnxBackendUnavailableError(RuntimeError):
    pass


class OnnxModelError(RuntimeError):
    pass


class OnnxInferenceError(RuntimeError):
    pass


class OnnxResultError(RuntimeError):
    pass


@dataclass(frozen=True)
class OnnxInferenceResult:
    boxesXyxy: np.ndarray[Any, Any]
    scores: np.ndarray[Any, Any]
    classIds: np.ndarray[Any, Any]
    names: Mapping[int, str]
    provider: str
    inputShape: tuple[int, int, int, int]


@dataclass(frozen=True)
class _LetterboxTransform:
    scale: float
    left: int
    top: int
    originalWidth: int
    originalHeight: int


class OnnxYoloSession:
    def __init__(self, modelPath: Path | str) -> None:
        self.modelPath = Path(modelPath)
        try:
            import onnxruntime as ort
        except ImportError as err:
            raise OnnxBackendUnavailableError(
                "ONNX Runtime is unavailable; install or repair onnxruntime==1.23.2"
            ) from err

        try:
            self._session = ort.InferenceSession(
                str(self.modelPath),
                providers=["CPUExecutionProvider"],
            )
        except Exception as err:
            raise OnnxModelError(f"failed to load ONNX model: {err}") from err

        providers = tuple(str(value) for value in self._session.get_providers())
        if "CPUExecutionProvider" not in providers:
            raise OnnxModelError("CPUExecutionProvider is unavailable")
        self.provider = "CPUExecutionProvider"

        inputs = list(self._session.get_inputs())
        outputs = list(self._session.get_outputs())
        if len(inputs) != 1:
            raise OnnxModelError("YOLO ONNX model must expose exactly one input")
        if len(outputs) != 1:
            raise OnnxModelError("YOLO ONNX model must expose exactly one output")

        modelInput = inputs[0]
        if str(modelInput.type) != "tensor(float)":
            raise OnnxModelError(
                "YOLO ONNX input must be float32 tensor; quantized and float16 models are unsupported"
            )
        self._inputName = str(modelInput.name)
        self._outputName = str(outputs[0].name)
        self._inputHeight, self._inputWidth = _validateInputShape(modelInput.shape)

        metadata = self._session.get_modelmeta().custom_metadata_map or {}
        self.names = _parseNames(metadata.get("names", ""))
        _validateMetadata(metadata)

    def predict(
        self,
        image: np.ndarray[Any, Any],
        *,
        confidence: float,
        iou: float,
        imageSize: int,
        maxDetections: int,
        classes: Sequence[int],
        agnosticNms: bool,
    ) -> OnnxInferenceResult:
        inputHeight = self._inputHeight or imageSize
        inputWidth = self._inputWidth or imageSize
        tensor, transform = _prepareInput(image, inputWidth, inputHeight)
        try:
            outputs = self._session.run(
                [self._outputName],
                {self._inputName: tensor},
            )
        except Exception as err:
            raise OnnxInferenceError(f"ONNX Runtime inference failed: {err}") from err

        if len(outputs) != 1:
            raise OnnxResultError("YOLO ONNX inference must return exactly one output")
        boxes, scores, classIds = _decodeOutput(
            outputs[0],
            confidence=confidence,
            iou=iou,
            maxDetections=maxDetections,
            classes=classes,
            agnosticNms=agnosticNms,
            expectedClassCount=(max(self.names) + 1 if self.names else None),
        )
        boxes = _mapBoxesToOriginal(boxes, transform)
        return OnnxInferenceResult(
            boxesXyxy=boxes,
            scores=scores,
            classIds=classIds,
            names=self.names,
            provider=self.provider,
            inputShape=(1, 3, inputHeight, inputWidth),
        )


def _validateInputShape(rawShape: object) -> tuple[int | None, int | None]:
    if not isinstance(rawShape, (list, tuple)) or len(rawShape) != 4:
        raise OnnxModelError("YOLO ONNX input must use NCHW rank-4 layout")
    batch, channels, height, width = rawShape
    if not _isDynamic(batch) and batch != 1:
        raise OnnxModelError("YOLO ONNX input batch size must be 1 or dynamic")
    if channels != 3:
        raise OnnxModelError("YOLO ONNX input must have exactly 3 channels")
    return _fixedDimension(height, "height"), _fixedDimension(width, "width")


def _fixedDimension(value: object, name: str) -> int | None:
    if _isDynamic(value):
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OnnxModelError(f"YOLO ONNX input {name} must be positive or dynamic")
    return value


def _isDynamic(value: object) -> bool:
    return value is None or isinstance(value, str)


def _validateMetadata(metadata: Mapping[str, str]) -> None:
    task = str(metadata.get("task", "")).strip().lower()
    if task not in {"", "detect"}:
        raise OnnxModelError(f"unsupported YOLO task: {task}")
    endToEnd = str(metadata.get("end2end", "")).strip().lower()
    if endToEnd in {"1", "true", "yes"}:
        raise OnnxModelError("end-to-end NMS ONNX exports are unsupported")
    rawArgs = str(metadata.get("args", "")).strip()
    if rawArgs:
        try:
            args = ast.literal_eval(rawArgs)
        except (SyntaxError, ValueError):
            args = {}
        if isinstance(args, Mapping) and args.get("nms") is True:
            raise OnnxModelError("ONNX exports with built-in NMS are unsupported")


def _parseNames(rawNames: object) -> dict[int, str]:
    text = str(rawNames).strip()
    if not text:
        return {}
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return {}
    names: dict[int, str] = {}
    if isinstance(parsed, Mapping):
        for rawKey, value in parsed.items():
            try:
                key = int(rawKey)
            except (TypeError, ValueError):
                continue
            if key >= 0:
                names[key] = str(value)
    elif isinstance(parsed, (list, tuple)):
        names = {index: str(value) for index, value in enumerate(parsed)}
    return names


def _prepareInput(
    image: np.ndarray[Any, Any],
    inputWidth: int,
    inputHeight: int,
) -> tuple[np.ndarray[Any, Any], _LetterboxTransform]:
    originalHeight, originalWidth = image.shape[:2]
    scale = min(inputWidth / originalWidth, inputHeight / originalHeight)
    resizedWidth = max(1, int(round(originalWidth * scale)))
    resizedHeight = max(1, int(round(originalHeight * scale)))
    resized = cv2.resize(
        image,
        (resizedWidth, resizedHeight),
        interpolation=cv2.INTER_LINEAR,
    )
    if resized.ndim == 2:
        resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)

    horizontalPadding = inputWidth - resizedWidth
    verticalPadding = inputHeight - resizedHeight
    left = int(round(horizontalPadding / 2 - 0.1))
    right = horizontalPadding - left
    top = int(round(verticalPadding / 2 - 0.1))
    bottom = verticalPadding - top
    padded = cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(114, 114, 114),
    )
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
    tensor = np.ascontiguousarray(
        rgb.transpose(2, 0, 1)[None],
        dtype=np.float32,
    )
    tensor /= 255.0
    return tensor, _LetterboxTransform(
        scale=scale,
        left=left,
        top=top,
        originalWidth=originalWidth,
        originalHeight=originalHeight,
    )


def _decodeOutput(
    rawOutput: object,
    *,
    confidence: float,
    iou: float,
    maxDetections: int,
    classes: Sequence[int],
    agnosticNms: bool,
    expectedClassCount: int | None = None,
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    try:
        output = np.asarray(rawOutput, dtype=np.float32)
    except (TypeError, ValueError) as err:
        raise OnnxResultError("YOLO ONNX output cannot be converted to float32") from err
    if output.ndim != 3 or output.shape[0] != 1:
        raise OnnxResultError("YOLO ONNX output must have shape [1, 4+C, N] or [1, N, 4+C]")

    matrix = output[0]
    if matrix.shape[0] < 5 and matrix.shape[1] < 5:
        raise OnnxResultError("YOLO ONNX output must contain boxes and class scores")
    expectedFeatures = 4 + expectedClassCount if expectedClassCount else None
    if expectedFeatures is not None and matrix.shape[0] == expectedFeatures:
        predictions = matrix.T
    elif expectedFeatures is not None and matrix.shape[1] == expectedFeatures:
        predictions = matrix
    elif matrix.shape[0] < 5 <= matrix.shape[1]:
        predictions = matrix
    elif matrix.shape[1] < 5 <= matrix.shape[0]:
        predictions = matrix.T
    elif matrix.shape[0] <= matrix.shape[1]:
        predictions = matrix.T
    else:
        predictions = matrix
    if predictions.ndim != 2 or predictions.shape[1] < 5:
        raise OnnxResultError("YOLO ONNX output feature dimension must be at least 5")
    if not bool(np.all(np.isfinite(predictions))):
        raise OnnxResultError("YOLO ONNX output must contain only finite values")

    classScores = predictions[:, 4:]
    classIds = np.argmax(classScores, axis=1).astype(np.int64)
    scores = np.max(classScores, axis=1)
    keep = scores >= confidence
    if classes:
        keep &= np.isin(classIds, np.asarray(tuple(classes), dtype=np.int64))
    predictions = predictions[keep]
    scores = scores[keep]
    classIds = classIds[keep]
    if predictions.shape[0] == 0:
        return _emptyDetections()

    xywh = predictions[:, :4]
    boxes = np.empty_like(xywh, dtype=np.float32)
    boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
    boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
    boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
    boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    boxes = boxes[valid]
    scores = scores[valid]
    classIds = classIds[valid]
    if boxes.shape[0] == 0:
        return _emptyDetections()

    selected = _nonMaximumSuppression(
        boxes,
        scores,
        classIds,
        iou,
        agnosticNms,
    )[:maxDetections]
    return boxes[selected], scores[selected], classIds[selected]


def _nonMaximumSuppression(
    boxes: np.ndarray[Any, Any],
    scores: np.ndarray[Any, Any],
    classIds: np.ndarray[Any, Any],
    iouThreshold: float,
    agnostic: bool,
) -> np.ndarray[Any, Any]:
    remaining = np.argsort(-scores, kind="stable")
    selected: list[int] = []
    while remaining.size:
        current = int(remaining[0])
        selected.append(current)
        if remaining.size == 1:
            break
        candidates = remaining[1:]
        ious = _boxIou(boxes[current], boxes[candidates])
        sameClass = classIds[candidates] == classIds[current]
        suppressed = ious > iouThreshold
        if not agnostic:
            suppressed &= sameClass
        remaining = candidates[~suppressed]
    return np.asarray(selected, dtype=np.int64)


def _boxIou(
    box: np.ndarray[Any, Any],
    others: np.ndarray[Any, Any],
) -> np.ndarray[Any, Any]:
    left = np.maximum(box[0], others[:, 0])
    top = np.maximum(box[1], others[:, 1])
    right = np.minimum(box[2], others[:, 2])
    bottom = np.minimum(box[3], others[:, 3])
    intersection = np.maximum(0.0, right - left) * np.maximum(0.0, bottom - top)
    boxArea = max(0.0, float(box[2] - box[0])) * max(0.0, float(box[3] - box[1]))
    otherAreas = np.maximum(0.0, others[:, 2] - others[:, 0]) * np.maximum(
        0.0, others[:, 3] - others[:, 1]
    )
    union = boxArea + otherAreas - intersection
    return intersection / np.maximum(union, np.finfo(np.float32).eps)


def _mapBoxesToOriginal(
    boxes: np.ndarray[Any, Any],
    transform: _LetterboxTransform,
) -> np.ndarray[Any, Any]:
    if boxes.shape[0] == 0:
        return boxes
    mapped = boxes.astype(np.float64, copy=True)
    mapped[:, [0, 2]] = (mapped[:, [0, 2]] - transform.left) / transform.scale
    mapped[:, [1, 3]] = (mapped[:, [1, 3]] - transform.top) / transform.scale
    mapped[:, [0, 2]] = np.clip(
        mapped[:, [0, 2]], 0.0, float(transform.originalWidth)
    )
    mapped[:, [1, 3]] = np.clip(
        mapped[:, [1, 3]], 0.0, float(transform.originalHeight)
    )
    return mapped


def _emptyDetections(
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    return (
        np.empty((0, 4), dtype=np.float32),
        np.empty((0,), dtype=np.float32),
        np.empty((0,), dtype=np.int64),
    )
