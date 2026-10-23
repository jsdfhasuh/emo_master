from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4

import numpy as np

from emo_master.core.contracts.geometry2d import DetectionCollection, PayloadValidationError
from emo_master.plugins.builtins._image_frame import coordinateSpacesEquivalent, frameForInput
from emo_master.plugins.builtins.crop.operator import CropOperator
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict
    outputPorts: dict
    paramSchema: dict


class DetectionArchiveOperator:
    meta = OperatorMeta(
        operatorId="vision.io.detection_archive", displayName="Detection Archive", version="1.0.0",
        inputPorts={"image": "image", "frame": "bbox2d", "detections": "detectionCollection", "overlay": "image"},
        outputPorts={"result": "json", "saved": "boolean", "cropCount": "integer"},
        paramSchema={
            "type": "object", "properties": {
                "outputPath": {
                    "title": "\u8f93\u51fa\u8def\u5f84\u6a21\u677f",
                    "description": "\u4f7f\u7528\u6b64\u8def\u5f84\u7684\u6240\u5728\u76ee\u5f55\u548c\u6587\u4ef6\u540d\u524d\u7f00\uff1b\u81ea\u52a8\u6dfb\u52a0 UTC \u65f6\u95f4\u548c\u5e8f\u53f7\uff0c\u4e0d\u8986\u76d6\u5386\u53f2\u56fe\u7247\u3002\u88c1\u526a\u56fe PNG\uff0c\u6807\u6ce8\u56fe JPG\u3002",
                    "type": "string", "default": "outputs/captures/capture.jpg",
                    "xWidget": "file", "xFileMode": "save", "xFilter": "JPEG (*.jpg)",
                },
                "minIntervalMs": {"title": "\u6700\u5c0f\u4fdd\u5b58\u95f4\u9694\uff08\u6beb\u79d2\uff09",
                                  "type": "integer", "minimum": 0, "default": 2000},
            }, "required": ["outputPath"],
        },
    )

    def __init__(self):
        self._session = uuid4().hex[:12]
        self._sequence = 0
        self._lastSaved = None

    def initOperator(self, runtimeContext):
        # Retain the cooldown across While iterations, but never across jobs.
        self.__init__()

    def validateParams(self, params):
        path = params.get("outputPath")
        interval = params.get("minIntervalMs", 2000)
        if not isinstance(path, str) or not path.strip() or not Path(path).stem:
            return {"code": "E_PARAM_INVALID", "message": "outputPath must be a non-empty filename template"}
        if not isinstance(interval, int) or isinstance(interval, bool) or interval < 0:
            return {"code": "E_PARAM_INVALID", "message": "minIntervalMs must be an integer >= 0"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        error = self.validateParams(params)
        if error:
            return {"status": "error", "error": error}
        for port in self.meta.inputPorts:
            if port not in inputs:
                return _error("E_INPUT_MISSING", f"input '{port}' is required")
        for port in ("image", "overlay"):
            value = inputs[port]
            if not isinstance(value, np.ndarray) or value.dtype != np.uint8:
                return _error("E_INPUT_TYPE", f"{port} must be a uint8 image")
            if not value.size or value.ndim not in (2, 3) or (value.ndim == 3 and value.shape[2] != 3):
                return _error("E_INPUT_SHAPE", f"{port} must be a non-empty grayscale or BGR image")
        image = inputs["image"]
        if inputs["overlay"].shape[:2] != image.shape[:2]:
            return _error("E_INPUT_SHAPE", "overlay dimensions must match image")
        try:
            frame = frameForInput(inputs, image.shape[1], image.shape[0])
            detections = DetectionCollection.fromPayload(inputs["detections"])
            if not coordinateSpacesEquivalent(frame.coordinateSpace, detections.coordinateSpace):
                return _error("E_INPUT_SHAPE", "detections and image frame coordinate spaces must match")
        except (PayloadValidationError, ValueError) as error:
            return _error("E_INPUT_SHAPE", str(error))

        _checkCancellation(runtimeContext)
        now = monotonic()
        if not detections.items:
            return _result(False, "no_detections", [], None)
        if self._lastSaved is not None and (now - self._lastSaved) * 1000 < params.get("minIntervalMs", 2000):
            return _result(False, "cooldown", [], None)

        self._sequence += 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        prefix = f"{Path(params['outputPath']).stem}-{stamp}-{self._session}-{self._sequence:06d}"
        saver = ImageSaverOperator()
        cropper = CropOperator()
        saveParams = {"outputPath": params["outputPath"], "overwrite": False}
        paths = []
        for index, detection in enumerate(detections.items, 1):
            _checkCancellation(runtimeContext)
            cropped = cropper.executeNode({"image": image, "frame": frame.toPayload(),
                                           "roi": detection.bbox.toPayload()}, {"clip": True}, runtimeContext)
            if cropped["status"] != "ok":
                return cropped
            _checkCancellation(runtimeContext)
            saved = saver.executeNode({"image": cropped["outputs"]["image"],
                                       "fileName": f"{prefix}-crop-{index:03d}.png"}, saveParams, runtimeContext)
            if saved["status"] != "ok":
                return saved
            paths.append(saved["outputs"]["result"]["path"])
        _checkCancellation(runtimeContext)
        saved = saver.executeNode({"image": inputs["overlay"], "fileName": f"{prefix}-overlay.jpg"},
                                  saveParams, runtimeContext)
        if saved["status"] != "ok":
            return saved
        self._lastSaved = monotonic()
        return _result(True, "saved", paths, saved["outputs"]["result"]["path"])


def _checkCancellation(context):
    check = context.get("raiseIfCancellationRequested")
    if callable(check):
        check()


def _result(saved, reason, crops, overlay):
    return {"status": "ok", "outputs": {"saved": saved, "cropCount": len(crops),
        "result": {"saved": saved, "reason": reason, "cropPaths": crops, "overlayPath": overlay}}}


def _error(code, message):
    return {"status": "error", "error": {"code": code, "message": message}}
