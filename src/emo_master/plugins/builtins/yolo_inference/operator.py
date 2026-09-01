from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, ClassVar, Mapping, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
    Detection2D,
    DetectionCollection,
    PayloadValidationError,
)
from emo_master.plugins.builtins._image_frame import frameForInput


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


@dataclass
class _ModelEntry:
    model: object
    inferenceLock: Any


class _BackendUnavailableError(RuntimeError):
    pass


class _ModelLoadError(RuntimeError):
    pass


_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "modelPath": {
            "type": "string",
            "default": "",
            "xWidget": "file",
            "xFileMode": "open",
            "xFilter": "YOLO 模型 (*.pt *.onnx *.engine)",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
        },
        "iou": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.45,
        },
        "imageSize": {"type": "integer", "minimum": 1, "default": 640},
        "maxDetections": {"type": "integer", "minimum": 1, "default": 300},
        "device": {"type": "string", "default": "auto"},
        "classes": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "default": [],
        },
        "agnosticNms": {"type": "boolean", "default": False},
        "drawOverlay": {"type": "boolean", "default": False},
    },
    "required": ["modelPath"],
}


class YoloInferenceOperator:
    meta = OperatorMeta(
        operatorId="vision.inference.yolo",
        displayName="YOLO Inference",
        version="1.1.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "detections": {
                "type": "detectionCollection",
                "required": True,
                "schemaVersion": "1.x",
            },
            "overlay": {"type": "image", "required": False},
            "frame": {
                "type": "bbox2d",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        paramSchema=_PARAM_SCHEMA,
    )

    _modelCache: ClassVar[dict[tuple[str, str], _ModelEntry]] = {}
    _cacheLock: ClassVar[Any] = RLock()

    @classmethod
    def clearCache(cls) -> None:
        with cls._cacheLock:
            cls._modelCache.clear()

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        modelPath = params.get("modelPath", "")
        if not isinstance(modelPath, str) or modelPath.strip() == "":
            return _paramError("modelPath must be a non-empty string")
        for name, default in (("confidence", 0.25), ("iou", 0.45)):
            value = params.get(name, default)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                return _paramError(f"{name} must be a number in [0, 1]")
        for name, default in (("imageSize", 640), ("maxDetections", 300)):
            value = params.get(name, default)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                return _paramError(f"{name} must be an integer >= 1")
        device = params.get("device", "auto")
        if not isinstance(device, str) or device.strip() == "":
            return _paramError("device must be a non-empty string")
        classes = params.get("classes", [])
        if not isinstance(classes, list) or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 0
            for item in classes
        ):
            return _paramError("classes must be an array of non-negative integers")
        for name, default in (("agnosticNms", False), ("drawOverlay", False)):
            if not isinstance(params.get(name, default), bool):
                return _paramError(f"{name} must be boolean")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        startedAt = perf_counter()
        if "image" not in inputs:
            return _error("E_INPUT_MISSING", "input 'image' is required")
        image = inputs["image"]
        if not isinstance(image, np.ndarray):
            return _error("E_INPUT_TYPE", "input 'image' must be numpy.ndarray")
        if image.dtype != np.uint8:
            return _error("E_INPUT_TYPE", "input 'image' must use uint8 pixels")
        if image.size == 0 or image.ndim not in (2, 3):
            return _error("E_INPUT_SHAPE", "image must be non-empty grayscale or BGR")
        if image.ndim == 3 and image.shape[2] != 3:
            return _error("E_INPUT_SHAPE", "BGR image must have exactly 3 channels")
        height, width = image.shape[:2]
        try:
            frame = frameForInput(inputs, width, height)
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frame payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

        modelPath = _resolveModelPath(
            cast(str, params.get("modelPath", "")), runtimeContext
        )
        if not modelPath.exists() or not modelPath.is_file():
            return _error("E_MODEL_NOT_FOUND", f"model file not found: {modelPath}")
        device = cast(str, params.get("device", "auto")).strip()
        try:
            entry = self._modelEntry(modelPath, device)
        except _BackendUnavailableError as err:
            return _error("E_BACKEND_UNAVAILABLE", str(err))
        except _ModelLoadError as err:
            return _error("E_MODEL_LOAD_FAILED", str(err))

        predict = getattr(entry.model, "predict", None)
        if not callable(predict):
            return _error("E_MODEL_LOAD_FAILED", "YOLO model has no callable predict()")
        keywordArguments: dict[str, object] = {
            "source": image,
            "conf": float(cast(float, params.get("confidence", 0.25))),
            "iou": float(cast(float, params.get("iou", 0.45))),
            "imgsz": cast(int, params.get("imageSize", 640)),
            "max_det": cast(int, params.get("maxDetections", 300)),
            "agnostic_nms": cast(bool, params.get("agnosticNms", False)),
            "verbose": False,
        }
        classes = cast(list[int], params.get("classes", []))
        if classes:
            keywordArguments["classes"] = list(classes)
        if device.lower() != "auto":
            keywordArguments["device"] = device
        try:
            with entry.inferenceLock:
                rawResults = predict(**keywordArguments)
        except Exception as err:
            return _error("E_INFERENCE_FAILED", f"YOLO inference failed: {err}")

        coordinateSpace = frame.coordinateSpace
        try:
            detections = _parseResults(
                rawResults,
                entry.model,
                coordinateSpace,
                width,
                height,
            )
        except (TypeError, ValueError) as err:
            return _error("E_RESULT_INVALID", f"invalid YOLO result: {err}")

        outputs: dict[str, object] = {
            "detections": DetectionCollection(
                tuple(detections), coordinateSpace
            ).toPayload(),
            "frame": frame.toPayload(),
        }
        if cast(bool, params.get("drawOverlay", False)):
            outputs["overlay"] = _drawOverlay(image, detections)
        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "detectionCount": len(detections),
            },
            "diagnostics": {
                "text": f"YOLO detected {len(detections)} object(s)",
                "modelPath": str(modelPath),
            },
        }

    @classmethod
    def _modelEntry(cls, modelPath: Path, device: str) -> _ModelEntry:
        cacheKey = (str(modelPath), device.lower())
        with cls._cacheLock:
            cached = cls._modelCache.get(cacheKey)
            if cached is not None:
                return cached
            try:
                model = _createModel(str(modelPath))
            except _BackendUnavailableError:
                raise
            except Exception as err:
                raise _ModelLoadError(
                    f"failed to load YOLO model {modelPath}: {err}"
                ) from err
            entry = _ModelEntry(model=model, inferenceLock=RLock())
            cls._modelCache[cacheKey] = entry
            return entry


def _createModel(modelPath: str) -> object:
    try:
        from ultralytics import YOLO
    except ImportError as err:
        raise _BackendUnavailableError(
            "Ultralytics is not installed; run 'pip install -e .[yolo]' "
            "in the Runtime environment"
        ) from err
    return YOLO(modelPath)


def _resolveModelPath(
    rawPath: str,
    runtimeContext: Mapping[str, object],
) -> Path:
    modelPath = Path(rawPath.strip()).expanduser()
    workspacePath = runtimeContext.get("workspacePath")
    if not modelPath.is_absolute() and isinstance(workspacePath, str) and workspacePath:
        modelPath = Path(workspacePath) / modelPath
    return modelPath.resolve()


def _parseResults(
    rawResults: object,
    model: object,
    coordinateSpace: CoordinateSpace2D,
    width: int,
    height: int,
) -> list[Detection2D]:
    if isinstance(rawResults, (str, bytes, Mapping)):
        raise ValueError("predict() must return a result sequence")
    try:
        results = list(cast(Any, rawResults))
    except TypeError as err:
        raise ValueError("predict() must return a result sequence") from err
    if len(results) != 1:
        raise ValueError("single-image inference must return exactly one result")
    result = results[0]
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    xyxy = _backendArray(getattr(boxes, "xyxy", None), "boxes.xyxy")
    confidence = _backendArray(getattr(boxes, "conf", None), "boxes.conf").reshape(-1)
    classes = _backendArray(getattr(boxes, "cls", None), "boxes.cls").reshape(-1)
    if xyxy.ndim != 2 or xyxy.shape[1] != 4:
        raise ValueError("boxes.xyxy must have shape [N, 4]")
    if xyxy.shape[0] != confidence.size or xyxy.shape[0] != classes.size:
        raise ValueError("boxes arrays must contain the same number of items")
    names = getattr(result, "names", getattr(model, "names", {}))
    detections: list[Detection2D] = []
    for index, rawBox in enumerate(xyxy):
        if not bool(np.all(np.isfinite(rawBox))):
            raise ValueError("boxes.xyxy must contain finite values")
        score = float(confidence[index])
        rawClass = float(classes[index])
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("boxes.conf must be within [0, 1]")
        if not math.isfinite(rawClass) or rawClass < 0 or not rawClass.is_integer():
            raise ValueError("boxes.cls must contain non-negative integers")
        x1, y1, x2, y2 = (float(value) for value in rawBox)
        if x2 < x1 or y2 < y1:
            raise ValueError("boxes.xyxy has reversed coordinates")
        x1 = min(float(width), max(0.0, x1))
        x2 = min(float(width), max(0.0, x2))
        y1 = min(float(height), max(0.0, y1))
        y2 = min(float(height), max(0.0, y2))
        if x2 <= x1 or y2 <= y1:
            continue
        classId = int(rawClass)
        detections.append(
            Detection2D.fromGeometry(
                detectionId=f"det-{index + 1}",
                classId=classId,
                label=_classLabel(names, classId),
                confidence=score,
                geometry=BBox2D(
                    x1,
                    y1,
                    x2 - x1,
                    y2 - y1,
                    coordinateSpace,
                ),
                attributes={"backend": "ultralytics"},
            )
        )
    return detections


def _backendArray(value: object, path: str) -> np.ndarray[Any, Any]:
    if value is None:
        raise ValueError(f"{path} is missing")
    candidate = value
    for methodName in ("detach", "cpu"):
        method = getattr(candidate, methodName, None)
        if callable(method):
            candidate = method()
    numpyMethod = getattr(candidate, "numpy", None)
    if callable(numpyMethod):
        candidate = numpyMethod()
    try:
        return np.asarray(candidate, dtype=np.float64)
    except (TypeError, ValueError) as err:
        raise ValueError(f"{path} cannot be converted to an array") from err


def _classLabel(names: object, classId: int) -> str:
    if isinstance(names, Mapping):
        value = names.get(classId, names.get(str(classId), str(classId)))
        return str(value)
    if isinstance(names, (list, tuple)) and classId < len(names):
        return str(names[classId])
    return str(classId)


def _drawOverlay(
    image: np.ndarray[Any, Any],
    detections: list[Detection2D],
) -> np.ndarray[Any, Any]:
    overlay = (
        image.copy() if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    )
    for detection in detections:
        left = int(round(detection.bbox.x))
        top = int(round(detection.bbox.y))
        right = int(round(detection.bbox.x + detection.bbox.width))
        bottom = int(round(detection.bbox.y + detection.bbox.height))
        cv2.rectangle(overlay, (left, top), (right, bottom), (0, 255, 0), 2)
        caption = f"{detection.label} {detection.confidence:.2f}"
        cv2.putText(
            overlay,
            caption,
            (left, max(0, top - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    return overlay


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
