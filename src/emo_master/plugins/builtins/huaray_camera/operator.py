from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address
import math
from time import monotonic, perf_counter, sleep
from typing import Any, Callable, Mapping, cast

from emo_master.core.contracts.geometry2d import BBox2D, CoordinateSpace2D
from emo_master.core.contracts.operator_logging import getOperatorLogger
from emo_master.plugins.builtins._huaray_imv import (
    CameraSession,
    HuarayCameraError,
    ImvApiProtocol,
    getImvApi,
)
from emo_master.plugins.builtins._image_frame import frameReference


DEFAULTS: dict[str, object] = {
    "selectionMode": "ip",
    "ipAddress": "",
    "cameraKey": "",
    "userId": "",
    "deviceIndex": 0,
    "triggerMode": "hardware",
    "triggerSource": "Line1",
    "triggerActivation": "RisingEdge",
    "captureTimeoutMs": 5000,
    "retryCount": 1,
    "retryDelayMs": 200,
    "outputColor": "bgr",
    "demosaic": "bilinear",
    "exposureMode": "keep",
    "exposureUs": 10000.0,
    "gainMode": "keep",
    "gainRaw": 0.0,
    "frameRateMode": "keep",
    "frameRate": 30.0,
    "roiMode": "keep",
    "width": 1,
    "height": 1,
    "offsetX": 0,
    "offsetY": 0,
    "pixelFormat": "keep",
}

PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "selectionMode": {
            "type": "string",
            "enum": ["ip", "cameraKey", "userId", "index"],
            "default": "ip",
        },
        "ipAddress": {"type": "string", "default": "", "maxLength": 255},
        "cameraKey": {"type": "string", "default": "", "maxLength": 255},
        "userId": {"type": "string", "default": "", "maxLength": 255},
        "deviceIndex": {"type": "integer", "minimum": 0, "maximum": 99, "default": 0},
        "triggerMode": {
            "type": "string",
            "enum": ["hardware", "software", "freeRun"],
            "default": "hardware",
        },
        "triggerSource": {
            "type": "string",
            "enum": ["Line1", "Line2", "Line3", "Line4"],
            "default": "Line1",
        },
        "triggerActivation": {
            "type": "string",
            "enum": ["RisingEdge", "FallingEdge"],
            "default": "RisingEdge",
        },
        "captureTimeoutMs": {
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 5000,
        },
        "retryCount": {"type": "integer", "minimum": 0, "maximum": 3, "default": 1},
        "retryDelayMs": {
            "type": "integer",
            "minimum": 0,
            "maximum": 5000,
            "default": 200,
        },
        "outputColor": {"type": "string", "enum": ["bgr", "gray"], "default": "bgr"},
        "demosaic": {
            "type": "string",
            "enum": ["nearest", "bilinear", "edgeSensing"],
            "default": "bilinear",
        },
        "exposureMode": {"type": "string", "enum": ["keep", "manual"], "default": "keep"},
        "exposureUs": {"type": "number", "exclusiveMinimum": 0, "default": 10000.0},
        "gainMode": {"type": "string", "enum": ["keep", "manual"], "default": "keep"},
        "gainRaw": {"type": "number", "default": 0.0},
        "frameRateMode": {"type": "string", "enum": ["keep", "manual"], "default": "keep"},
        "frameRate": {"type": "number", "exclusiveMinimum": 0, "default": 30.0},
        "roiMode": {"type": "string", "enum": ["keep", "custom", "full"], "default": "keep"},
        "width": {"type": "integer", "minimum": 1, "default": 1},
        "height": {"type": "integer", "minimum": 1, "default": 1},
        "offsetX": {"type": "integer", "minimum": 0, "default": 0},
        "offsetY": {"type": "integer", "minimum": 0, "default": 0},
        "pixelFormat": {
            "type": "string",
            "enum": [
                "keep",
                "Mono8",
                "BayerGR8",
                "BayerRG8",
                "BayerGB8",
                "BayerBG8",
                "RGB8",
                "BGR8",
            ],
            "default": "keep",
        },
    },
    "required": [],
}


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


class HuarayCameraOperator:
    meta = OperatorMeta(
        operatorId="vision.io.huaray_camera",
        displayName="Huaray IMV Camera",
        version="1.1.0",
        inputPorts={},
        outputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.1",
            },
            "blockId": {"type": "integer", "required": True, "nullable": False},
            "deviceTimestamp": {"type": "integer", "required": True, "nullable": False},
            "actualExposureUs": {"type": "number", "required": True, "nullable": False},
        },
        paramSchema=PARAM_SCHEMA,
    )

    def __init__(self, apiFactory: Callable[[], ImvApiProtocol] | None = None) -> None:
        self._apiFactory = apiFactory
        self._session: CameraSession | None = None
        self._fingerprint: tuple[object, ...] | None = None
        self._lifecycleLogger = getOperatorLogger(None)

    def initOperator(self, initContext: dict[str, object]) -> None:
        self._lifecycleLogger = getOperatorLogger(initContext)
        self._lifecycleLogger.info("Huaray camera operator initialized")

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        unknown = sorted(set(params) - set(DEFAULTS))
        if unknown:
            return _paramError(f"unknown camera parameters: {', '.join(unknown)}")

        settings = _settings(params)
        enumFields = {
            "selectionMode": {"ip", "cameraKey", "userId", "index"},
            "triggerMode": {"hardware", "software", "freeRun"},
            "triggerSource": {"Line1", "Line2", "Line3", "Line4"},
            "triggerActivation": {"RisingEdge", "FallingEdge"},
            "outputColor": {"bgr", "gray"},
            "demosaic": {"nearest", "bilinear", "edgeSensing"},
            "exposureMode": {"keep", "manual"},
            "gainMode": {"keep", "manual"},
            "frameRateMode": {"keep", "manual"},
            "roiMode": {"keep", "custom", "full"},
            "pixelFormat": {
                "keep",
                "Mono8",
                "BayerGR8",
                "BayerRG8",
                "BayerGB8",
                "BayerBG8",
                "RGB8",
                "BGR8",
            },
        }
        for name, allowed in enumFields.items():
            if settings[name] not in allowed:
                return _paramError(f"{name} must be one of: {', '.join(sorted(allowed))}")

        for name, minimum, maximum in (
            ("captureTimeoutMs", 1, 60000),
            ("retryCount", 0, 3),
            ("retryDelayMs", 0, 5000),
        ):
            if not _isInteger(settings[name]) or not minimum <= cast(int, settings[name]) <= maximum:
                return _paramError(f"{name} must be an integer in [{minimum}, {maximum}]")

        selectionMode = str(settings["selectionMode"])
        if selectionMode == "ip":
            ipAddress = settings["ipAddress"]
            if not isinstance(ipAddress, str):
                return _paramError("ipAddress must be an IPv4 string")
            try:
                parsedAddress = IPv4Address(ipAddress)
            except ValueError:
                return _paramError("ipAddress must be a valid IPv4 address")
            if str(parsedAddress) != ipAddress:
                return _paramError("ipAddress must use canonical IPv4 notation")
        elif selectionMode in {"cameraKey", "userId"}:
            fieldName = selectionMode
            value = settings[fieldName]
            if not isinstance(value, str) or not value:
                return _paramError(f"{fieldName} must be a non-empty string")
            if len(value.encode("utf-8")) > 255:
                return _paramError(f"{fieldName} must not exceed 255 UTF-8 bytes")
        elif not _isInteger(settings["deviceIndex"]) or not 0 <= cast(int, settings["deviceIndex"]) <= 99:
            return _paramError("deviceIndex must be an integer in [0, 99]")

        if settings["exposureMode"] == "manual":
            if not _isFiniteNumber(settings["exposureUs"]) or float(
                cast(int | float, settings["exposureUs"])
            ) <= 0:
                return _paramError("exposureUs must be a finite number greater than 0")
        if settings["gainMode"] == "manual" and not _isFiniteNumber(settings["gainRaw"]):
            return _paramError("gainRaw must be a finite number")
        if settings["frameRateMode"] == "manual":
            if not _isFiniteNumber(settings["frameRate"]) or float(
                cast(int | float, settings["frameRate"])
            ) <= 0:
                return _paramError("frameRate must be a finite number greater than 0")
        if settings["roiMode"] == "custom":
            for name in ("width", "height"):
                if not _isInteger(settings[name]) or cast(int, settings[name]) <= 0:
                    return _paramError(f"{name} must be a positive integer for custom ROI")
            for name in ("offsetX", "offsetY"):
                if not _isInteger(settings[name]) or cast(int, settings[name]) < 0:
                    return _paramError(f"{name} must be a non-negative integer for custom ROI")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = inputs
        logger = getOperatorLogger(runtimeContext)
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        settings = _settings(params)
        logger.info(
            "starting Huaray camera capture",
            payload={
                "selector": _selectorDescription(settings),
                "triggerMode": settings["triggerMode"],
                "outputColor": settings["outputColor"],
            },
        )
        fingerprint = _sessionFingerprint(settings)
        if self._session is not None and self._fingerprint != fingerprint:
            logger.warning("camera configuration changed; reopening device")
            cleanupError = self._dropSession()
            if cleanupError is not None:
                logger.error(
                    "failed to close camera before reconfiguration",
                    code=cleanupError.code,
                )
                return _errorResult(cleanupError)

        lastError: HuarayCameraError | None = None
        attemptCount = cast(int, settings["retryCount"]) + 1
        for attempt in range(attemptCount):
            try:
                openingSession = self._session is None
                session = self._ensureSession(settings, fingerprint)
                if openingSession:
                    logger.info(
                        "Huaray camera connection opened",
                        payload={"sourceId": session.sourceId},
                    )
                cancellationCheck = runtimeContext.get("raiseIfCancellationRequested")
                captured = session.capture(
                    cast(int, settings["captureTimeoutMs"]),
                    str(settings["outputColor"]),
                    str(settings["demosaic"]),
                    cancellationCheck if callable(cancellationCheck) else None,
                )
                height, width = captured.image.shape[:2]
                space = CoordinateSpace2D(
                    reference=frameReference(runtimeContext, self.meta.operatorId),
                    sourceId=session.sourceId,
                    imageWidth=width,
                    imageHeight=height,
                    transformToSource=(
                        1.0,
                        0.0,
                        0.0,
                        1.0,
                        float(session.offsetX),
                        float(session.offsetY),
                    ),
                )
                frame = BBox2D(0.0, 0.0, float(width), float(height), space)
                elapsedMs = (perf_counter() - startedAt) * 1000.0
                logger.debug(
                    "camera frame captured",
                    payload={
                        "blockId": captured.blockId,
                        "width": width,
                        "height": height,
                        "attempt": attempt + 1,
                    },
                )
                return {
                    "status": "ok",
                    "outputs": {
                        "image": captured.image,
                        "frame": frame.toPayload(),
                        "blockId": captured.blockId,
                        "deviceTimestamp": captured.deviceTimestamp,
                        "actualExposureUs": float(session.actualExposureUs),
                    },
                    "metrics": {
                        "latencyMs": round(elapsedMs, 3),
                        "captureAttempts": attempt + 1,
                    },
                    "diagnostics": {
                        "sourceId": session.sourceId,
                        "selector": _selectorDescription(settings),
                        "sdkVersion": _sdkVersion(session.api),
                    },
                }
            except HuarayCameraError as err:
                if err.code == "E_CANCELLED":
                    raise
                lastError = err
            except Exception as err:
                if getattr(err, "code", None) == "E_CANCELLED":
                    raise
                lastError = HuarayCameraError(
                    "E_CAMERA_IO",
                    f"unexpected camera execution failure: {err}",
                    operation="execute camera operator",
                )

            if lastError.code == "E_CAMERA_TIMEOUT":
                logger.warning(
                    "camera capture timed out",
                    code=lastError.code,
                    payload={"attempt": attempt + 1},
                )
                return _errorResult(lastError)
            if attempt + 1 >= attemptCount:
                logger.error(
                    "camera capture failed",
                    code=lastError.code,
                    payload={"attempts": attempt + 1},
                )
                cleanupError = self._dropSession()
                if cleanupError is not None:
                    lastError.diagnostics["resourceCleanup"] = {
                        "code": cleanupError.code,
                        "message": str(cleanupError),
                        **cleanupError.diagnostics,
                    }
                return _errorResult(lastError)
            logger.warning(
                "camera capture failed; reconnecting before retry",
                code=lastError.code,
                payload={"attempt": attempt + 1, "maximumAttempts": attemptCount},
            )
            cleanupError = self._dropSession()
            if cleanupError is not None:
                lastError.diagnostics["resourceCleanup"] = {
                    "code": cleanupError.code,
                    "message": str(cleanupError),
                    **cleanupError.diagnostics,
                }
                return _errorResult(lastError)
            _cancellableDelay(cast(int, settings["retryDelayMs"]), runtimeContext)

        if lastError is None:  # pragma: no cover - attemptCount is always positive
            lastError = HuarayCameraError("E_CAMERA_IO", "camera capture failed")
        return _errorResult(lastError)

    def _ensureSession(
        self,
        settings: Mapping[str, object],
        fingerprint: tuple[object, ...],
    ) -> CameraSession:
        if self._session is not None:
            return self._session
        factory = self._apiFactory or getImvApi
        api = factory()
        session = CameraSession(api, settings)
        self._session = session
        self._fingerprint = fingerprint
        session.open()
        return session

    def _dropSession(self) -> HuarayCameraError | None:
        session = self._session
        self._session = None
        self._fingerprint = None
        if session is None:
            return None
        return session.close()

    def disposeOperator(self) -> None:
        self._lifecycleLogger.info("disposing Huaray camera operator")
        cleanupError = self._dropSession()
        if cleanupError is not None:
            self._lifecycleLogger.error(
                "Huaray camera cleanup failed",
                code=cleanupError.code,
            )
            raise cleanupError
        self._lifecycleLogger.info("Huaray camera operator disposed")


def _settings(params: Mapping[str, object]) -> dict[str, object]:
    return {**DEFAULTS, **dict(params)}


def _sessionFingerprint(settings: Mapping[str, object]) -> tuple[object, ...]:
    values: list[object] = [
        settings["selectionMode"],
        settings[{"ip": "ipAddress", "cameraKey": "cameraKey", "userId": "userId", "index": "deviceIndex"}[str(settings["selectionMode"])]],
        settings["triggerMode"],
        settings["pixelFormat"],
        settings["exposureMode"],
        settings["gainMode"],
        settings["frameRateMode"],
        settings["roiMode"],
    ]
    if settings["triggerMode"] == "hardware":
        values.extend((settings["triggerSource"], settings["triggerActivation"]))
    if settings["exposureMode"] == "manual":
        values.append(settings["exposureUs"])
    if settings["gainMode"] == "manual":
        values.append(settings["gainRaw"])
    if settings["frameRateMode"] == "manual":
        values.append(settings["frameRate"])
    if settings["roiMode"] == "custom":
        values.extend((settings["width"], settings["height"], settings["offsetX"], settings["offsetY"]))
    return tuple(values)


def _cancellableDelay(delayMs: int, runtimeContext: Mapping[str, object]) -> None:
    if delayMs <= 0:
        return
    check = runtimeContext.get("raiseIfCancellationRequested")
    deadline = monotonic() + delayMs / 1000.0
    while True:
        if callable(check):
            check()
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        sleep(min(0.1, remaining))


def _selectorDescription(settings: Mapping[str, object]) -> str:
    mode = str(settings["selectionMode"])
    fieldName = {"ip": "ipAddress", "cameraKey": "cameraKey", "userId": "userId", "index": "deviceIndex"}[mode]
    return f"{mode}:{settings[fieldName]}"


def _sdkVersion(api: object) -> str:
    version = getattr(api, "version", None)
    if not callable(version):
        return "unknown"
    try:
        return str(version())
    except Exception:
        return "unknown"


def _errorResult(error: HuarayCameraError) -> dict[str, object]:
    return {
        "status": "error",
        "error": {"code": error.code, "message": str(error)},
        "diagnostics": dict(error.diagnostics),
    }


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _isInteger(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _isFiniteNumber(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
