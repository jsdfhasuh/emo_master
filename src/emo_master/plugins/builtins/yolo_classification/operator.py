from __future__ import annotations

import math
from pathlib import Path
from time import perf_counter

import numpy as np

from emo_master.core.contracts.geometry2d import PayloadValidationError
from emo_master.plugins.builtins._image_frame import frameForInput
from emo_master.plugins.builtins.image_loader.operator import OperatorMeta
from emo_master.plugins.builtins.yolo_inference.onnx_backend import (
    OnnxBackendUnavailableError, OnnxInferenceError, OnnxModelError, OnnxResultError,
)

from .onnx_backend import OnnxClassificationSession


class YoloClassificationOperator:
    meta = OperatorMeta(
        operatorId="vision.inference.yolo_classification",
        displayName="YOLO Classification", version="1.0.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {"type": "bbox2d", "required": False, "nullable": False, "schemaVersion": "1.x"},
        },
        outputPorts={
            "classId": {"type": "integer", "required": True, "nullable": False},
            "label": {"type": "string", "required": True, "nullable": False},
            "confidence": {"type": "number", "required": True, "nullable": False},
            "accepted": {"type": "boolean", "required": True, "nullable": False},
            "acceptedLabel": {"type": "string", "required": True, "nullable": False},
            "result": {"type": "json", "required": True, "nullable": False},
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {"type": "bbox2d", "required": True, "nullable": False, "schemaVersion": "1.x"},
        },
        paramSchema={
            "type": "object", "properties": {
                "modelPath": {
                    "title": "\u5206\u7c7b\u6a21\u578b\u8def\u5f84", "type": "string", "default": "",
                    "xWidget": "file", "xFileMode": "open", "xFilter": "YOLO Classification (*.onnx)",
                },
                "confidence": {
                    "title": "\u5206\u7c7b\u7f6e\u4fe1\u5ea6\u9608\u503c", "type": "number",
                    "minimum": 0, "maximum": 1, "default": 0.8,
                    "description": "\u4f4e\u4e8e\u9608\u503c\u4ecd\u4fdd\u7559 Top-1\uff0caccepted=false\uff0cacceptedLabel \u4e3a\u7a7a\u5b57\u7b26\u4e32\u3002",
                },
                "preprocess": {
                    "title": "\u9884\u5904\u7406\u534f\u8bae", "type": "string",
                    "enum": ["resize_full_frame_v1"], "default": "resize_full_frame_v1",
                    "xOptionLabels": {"resize_full_frame_v1": "\u8bad\u7ec3\u5e73\u53f0\u6574\u56fe\u7f29\u653e"},
                    "description": "PIL \u53cc\u7ebf\u6027\u6574\u56fe\u7f29\u653e\uff0cRGB / 255\u3002\u4e0d\u652f\u6301\u4e2d\u5fc3\u88c1\u526a\u8bad\u7ec3\u7684\u6a21\u578b\u3002",
                },
            }, "required": ["modelPath"], "additionalProperties": False,
        },
    )

    def __init__(self):
        self._session = None
        self._sessionKey = None

    def initOperator(self, initContext):
        self.disposeOperator()

    def disposeOperator(self):
        if self._session is not None:
            self._session.close()
        self._session = None
        self._sessionKey = None

    def validateParams(self, params):
        path = params.get("modelPath")
        if not isinstance(path, str) or not path.strip() or "\x00" in path or Path(path.strip()).suffix.lower() != ".onnx":
            return {"code": "E_PARAM_INVALID", "message": "modelPath must name a classification .onnx file"}
        confidence = params.get("confidence", 0.8)
        if (type(confidence) not in (int, float) or not math.isfinite(confidence)
                or not 0 <= confidence <= 1):
            return {"code": "E_PARAM_INVALID", "message": "confidence must be in [0,1]"}
        if params.get("preprocess", "resize_full_frame_v1") != "resize_full_frame_v1":
            return {"code": "E_PARAM_INVALID", "message": "only resize_full_frame_v1 preprocessing is supported"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        started = perf_counter()
        invalid = self.validateParams(params)
        if invalid:
            return {"status": "error", "error": invalid}
        if "image" not in inputs:
            return _error("E_INPUT_MISSING", "image is required")
        image = inputs["image"]
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
            return _error("E_INPUT_TYPE", "image must be a uint8 numpy.ndarray")
        if image.size == 0 or image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] != 3):
            return _error("E_INPUT_SHAPE", "image must be non-empty GRAY or BGR")
        try:
            frame = frameForInput(inputs, image.shape[1], image.shape[0])
        except (PayloadValidationError, ValueError) as error:
            return _error("E_INPUT_SHAPE", str(error))
        cancelled = runtimeContext.get("raiseIfCancellationRequested")
        if callable(cancelled):
            cancelled()
        path = Path(params["modelPath"].strip()).expanduser()
        if not path.is_absolute() and runtimeContext.get("workspacePath"):
            path = Path(runtimeContext["workspacePath"]) / path
        key = (runtimeContext.get("jobId"), str(path.resolve()))
        if key != self._sessionKey:
            self.disposeOperator()
            if not path.is_file():
                return _error("E_MODEL_NOT_FOUND", f"classification model not found: {path}")
            try:
                self._session = OnnxClassificationSession(path)
            except OnnxBackendUnavailableError as error:
                return _error("E_BACKEND_UNAVAILABLE", str(error))
            except OnnxModelError as error:
                return _error("E_MODEL_LOAD_FAILED", str(error))
            self._sessionKey = key
        if callable(cancelled):
            cancelled()
        try:
            probabilities = self._session.predict(image)
        except OnnxResultError as error:
            return _error("E_RESULT_INVALID", str(error))
        except OnnxInferenceError as error:
            return _error("E_INFERENCE_FAILED", str(error))
        if callable(cancelled):
            cancelled()
        index = int(np.argmax(probabilities))
        score = float(probabilities[index])
        label = self._session.names[index]
        threshold = float(params.get("confidence", 0.8))
        accepted = score >= threshold
        result = {"classId": index, "label": label, "confidence": score,
                  "threshold": threshold, "accepted": accepted,
                  "probabilities": {name: float(value) for name, value in zip(self._session.names, probabilities)}}
        return {
            "status": "ok",
            "outputs": {"classId": index, "label": label, "confidence": score, "accepted": accepted,
                        "acceptedLabel": label if accepted else "", "result": result,
                        "image": image, "frame": frame.toPayload()},
            "metrics": {"latencyMs": round((perf_counter() - started) * 1000, 3)},
            "diagnostics": {"text": f"{label}: {score:.4f}; accepted={accepted}",
                            "backend": "onnxruntime", "provider": "CPUExecutionProvider",
                            "preprocess": "resize_full_frame_v1", "modelPath": str(path),
                            "inputShape": [1, 3, self._session.height, self._session.width]},
        }


def _error(code, message):
    return {"status": "error", "error": {"code": code, "message": message}}
