from __future__ import annotations

import math
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import cv2

from emo_master.plugins.builtins._image_frame import defaultFrame
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator, OperatorMeta


class VideoLoaderOperator:
    meta = OperatorMeta(
        operatorId="vision.io.video_loader",
        displayName="\u672c\u5730\u89c6\u9891\u53d6\u5e27",
        version="1.0.0",
        inputPorts={"reset": {"type": "boolean", "required": False, "nullable": False}},
        outputPorts={
            **ImageLoaderOperator.meta.outputPorts,
            "videoPath": {"type": "string", "required": True, "nullable": False},
            "index": {"type": "integer", "required": True, "nullable": False},
            "frameIndex": {"type": "integer", "required": True, "nullable": False},
            "timestampSec": {"type": "number", "required": True, "nullable": False},
            "hasNext": {"type": "boolean", "required": True, "nullable": False},
        },
        paramSchema={
            "type": "object",
            "properties": {
                "videoPath": {
                    "title": "\u89c6\u9891\u6587\u4ef6", "type": "string", "default": "",
                    "xWidget": "file", "xFileMode": "open",
                    "xFilter": "\u89c6\u9891\u6587\u4ef6 (*.mp4 *.avi *.mkv *.mov *.wmv *.m4v *.webm *.mpg *.mpeg);;\u6240\u6709\u6587\u4ef6 (*)",
                },
                "intervalSec": {
                    "title": "\u53d6\u5e27\u95f4\u9694\uff08\u79d2\uff09", "type": "number",
                    "exclusiveMinimum": 0.0, "default": 1.0,
                },
                "colorMode": {
                    "title": "\u989c\u8272\u6a21\u5f0f", "type": "string",
                    "enum": ["color", "grayscale"], "default": "color",
                },
            },
            "required": ["videoPath"],
        },
    )

    def __init__(self) -> None:
        self._capture: Any = None
        self._sequenceKey: tuple | None = None
        self._pending: tuple | None = None
        self._index = 0
        self._frameIndex = -1
        self._timestamp = -1.0
        self._fps = 0.0

    def _closeCapture(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def disposeOperator(self) -> None:
        # Runtime retains lifecycle operators across While body invocations.
        self._closeCapture()
        self._sequenceKey = None
        self._pending = None
        self._index = 0
        self._frameIndex = -1
        self._timestamp = -1.0
        self._fps = 0.0

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        path = params.get("videoPath")
        if not isinstance(path, str) or not path.strip():
            return {"code": "E_PARAM_INVALID", "message": "videoPath must be non-empty string"}
        interval = params.get("intervalSec", 1.0)
        if (not isinstance(interval, (int, float)) or isinstance(interval, bool) or not math.isfinite(interval)
                or interval <= 0):
            return {"code": "E_PARAM_INVALID", "message": "intervalSec must be a finite positive number"}
        if params.get("colorMode", "color") not in ("color", "grayscale"):
            return {"code": "E_PARAM_INVALID", "message": "colorMode must be color or grayscale"}
        return None

    def _readSample(self, target: float, checkCancelled) -> tuple | None:
        while self._capture is not None:
            if callable(checkCancelled):
                checkCancelled()
            if not self._capture.grab():
                self._closeCapture()
                return None
            self._frameIndex += 1
            timestamp = self._capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if not math.isfinite(timestamp) or timestamp < 0 or timestamp <= self._timestamp:
                # Some file backends expose no timestamps; use their declared FPS.
                if not math.isfinite(self._fps) or self._fps <= 0:
                    raise ValueError("video backend provides neither usable timestamps nor FPS")
                timestamp = self._frameIndex / self._fps
            self._timestamp = timestamp
            if timestamp + 1e-9 < target:
                continue
            ok, image = self._capture.retrieve()
            if not ok or image is None:
                raise ValueError(f"failed to decode video frame {self._frameIndex}")
            return image, self._frameIndex, timestamp
        return None

    def executeNode(
        self, inputs: dict[str, object], params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        started = perf_counter()
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        if "reset" in inputs and not isinstance(inputs["reset"], bool):
            return _error("E_INPUT_TYPE", "reset must be a boolean")
        checkCancelled = runtimeContext.get("raiseIfCancellationRequested")
        try:
            if callable(checkCancelled):
                checkCancelled()
            path = Path(str(params["videoPath"]).strip()).expanduser().resolve()
            interval = float(cast(float, params.get("intervalSec", 1.0)))
            colorMode = params.get("colorMode", "color")
            key = (runtimeContext.get("jobId"), str(path), interval, colorMode)
            if inputs.get("reset") is True or key != self._sequenceKey:
                self.disposeOperator()
                if not path.is_file():
                    return _error("E_INPUT_MISSING", f"video file not found: {path}")
                self._capture = cv2.VideoCapture(str(path))
                if not self._capture.isOpened():
                    self.disposeOperator()
                    return _error("E_INPUT_DECODE", f"cannot open video: {path}")
                self._fps = self._capture.get(cv2.CAP_PROP_FPS)
                self._pending = self._readSample(0.0, checkCancelled)
                if self._pending is None:
                    self.disposeOperator()
                    return _error("E_INPUT_DECODE", f"video contains no decodable frames: {path}")
                self._sequenceKey = key
            if self._pending is None:
                return _error("E_INPUT_EXHAUSTED", "all video samples loaded; stop on hasNext=false or reset")
            image, frameIndex, timestamp = self._pending
            # One-sample lookahead lets the final real image carry hasNext=false.
            # Keep the time grid anchored at zero, even between source frames.
            target = (math.floor((timestamp + 1e-9) / interval) + 1) * interval
            pending = self._readSample(target, checkCancelled)
            if callable(checkCancelled):
                checkCancelled()
            if colorMode == "grayscale":
                image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            height, width = image.shape[:2]
            frame = defaultFrame(width, height, sourceId=f"{path}#frame={frameIndex}")
            outputs = {
                "image": image, "frame": frame.toPayload(), "videoPath": str(path),
                "index": self._index, "frameIndex": frameIndex,
                "timestampSec": timestamp, "hasNext": pending is not None,
            }
            self._pending = pending
            self._index += 1
            return {
                "status": "ok", "outputs": outputs,
                "metrics": {"latencyMs": round((perf_counter() - started) * 1000, 3)},
                "diagnostics": {"text": f"Video sample {self._index}: {timestamp:.3f}s (frame {frameIndex})"},
            }
        except (OSError, ValueError, cv2.error) as err:
            self.disposeOperator()
            return _error("E_INPUT_DECODE", f"cannot read video: {err}")
        except BaseException:
            self.disposeOperator()
            raise


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
