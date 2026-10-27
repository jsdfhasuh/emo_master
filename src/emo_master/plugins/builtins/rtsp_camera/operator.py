from __future__ import annotations

import hashlib
from time import perf_counter, time
from urllib.parse import quote, urlunsplit

import cv2

from emo_master.plugins.builtins._image_frame import defaultFrame
from emo_master.plugins.builtins._network_camera import (
    CREDENTIAL_PROPERTIES, cameraUrl, credentials, errorResult, integerParam, validateCredentials,
)
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator, OperatorMeta


class RtspCameraOperator:
    meta = OperatorMeta(
        operatorId="vision.io.rtsp_camera",
        displayName="RTSP\u6444\u50cf\u5934\u8bfb\u53d6",
        version="1.0.0",
        inputPorts={},
        outputPorts={
            **ImageLoaderOperator.meta.outputPorts,
            "capturedAt": {"type": "number", "required": True, "nullable": False},
        },
        paramSchema={
            "type": "object",
            "properties": {
                "rtspUrl": {"title": "RTSP\u5730\u5740", "type": "string", "default": ""},
                **CREDENTIAL_PROPERTIES,
                "openTimeoutMs": {"title": "\u8fde\u63a5\u8d85\u65f6\uff08\u6beb\u79d2\uff09", "type": "integer",
                                  "minimum": 100, "maximum": 60000, "default": 5000},
                "readTimeoutMs": {"title": "\u53d6\u5e27\u8d85\u65f6\uff08\u6beb\u79d2\uff09", "type": "integer",
                                  "minimum": 100, "maximum": 60000, "default": 5000},
                "colorMode": {"title": "\u989c\u8272\u6a21\u5f0f", "type": "string", "default": "color",
                              "enum": ["color", "grayscale"],
                              "xOptionLabels": {"color": "\u5f69\u8272", "grayscale": "\u7070\u5ea6"}},
            },
            "required": ["rtspUrl"],
        },
    )

    def validateParams(self, params):
        try:
            cameraUrl(params.get("rtspUrl"), rtsp=True)
            validateCredentials(params)
            integerParam(params, "openTimeoutMs", 5000, 100, 60000)
            integerParam(params, "readTimeoutMs", 5000, 100, 60000)
            if params.get("colorMode", "color") not in ("color", "grayscale"):
                raise ValueError("colorMode must be color or grayscale")
        except ValueError as error:
            return {"code": "E_PARAM_INVALID", "message": str(error)}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        started = perf_counter()
        invalid = self.validateParams(params)
        if invalid:
            return {"status": "error", "error": invalid}
        check = runtimeContext.get("raiseIfCancellationRequested", lambda: None)
        check()
        try:
            user, password = credentials(params)
        except ValueError as error:
            return errorResult("E_PARAM_INVALID", str(error))
        parsed = cameraUrl(params["rtspUrl"], rtsp=True)
        authority = parsed.netloc
        if user or password:
            authority = f"{quote(user, safe='')}:{quote(password, safe='')}@{authority}"
        url = urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, ""))
        capture = cv2.VideoCapture()
        try:
            # A fresh bounded connection per invocation avoids returning a backlog
            # after a slow inference node. No reader thread survives the call.
            opened = capture.open(url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, params.get("openTimeoutMs", 5000),
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, params.get("readTimeoutMs", 5000),
            ])
            check()
            if not opened:
                return errorResult("E_CAMERA_CONNECT", "Cannot open RTSP stream; check address, credentials and codec")
            ok, image = capture.read()
            capturedAt = time()
            check()
            if not ok or image is None or image.size == 0:
                return errorResult("E_CAMERA_READ", "No RTSP frame received before disconnect or read timeout")
            if params.get("colorMode", "color") == "grayscale":
                image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            height, width = image.shape[:2]
            # The frame identity is useful downstream but never carries a password or query token.
            source = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            if parsed.query:
                source += "#stream=" + hashlib.sha256(parsed.query.encode()).hexdigest()[:16]
            frame = defaultFrame(width, height, sourceId=source)
            return {
                "status": "ok",
                "outputs": {"image": image, "frame": frame.toPayload(), "capturedAt": capturedAt},
                "metrics": {"latencyMs": round((perf_counter() - started) * 1000, 3)},
                "diagnostics": {"text": "RTSP frame captured"},
            }
        except (cv2.error, OSError):
            check()
            return errorResult("E_CAMERA_READ", "RTSP capture failed; check connectivity and decoder support")
        finally:
            capture.release()
