from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address
import math
from time import monotonic, perf_counter, sleep
from typing import Any, Callable, Mapping, cast

from emo_master.core.contracts.geometry2d import BBox2D, CoordinateSpace2D
from emo_master.core.contracts.operator_logging import getOperatorLogger
from emo_master.plugins.builtins._device_settings import deviceSettings
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
    "waitMode": "bounded",
    "sequencePolicy": "off",
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
    "xDeviceActionFields": [],
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "selectionMode": {
            "description": "直接IMV路径按IP/唯一key/用户名称/索引选择设备；托管路径由绑定类型的connection确定。",
            "title": "连接方式",
            "type": "string",
            "enum": ["ip", "cameraKey", "userId", "index"],
            "default": "ip",
        },
        "ipAddress": {
            "description": "直接路径selectionMode=ip时的规范IPv4。",
            "title": "相机 IP 地址", "type": "string", "default": "", "maxLength": 255
        },
        "cameraKey": {
            "description": "直接路径cameraKey选择；托管IMV固定取device.connection.cameraKey。",
            "title": "设备唯一标识", "type": "string", "default": "", "maxLength": 255
        },
        "userId": {
            "description": "直接路径userId选择时的设备用户标识。",
            "title": "用户自定义名称", "type": "string", "default": "", "maxLength": 255
        },
        "deviceIndex": {
            "description": "直接路径index选择时的零基枚举索引。",
            "title": "设备索引", "type": "integer", "minimum": 0, "maximum": 99, "default": 0
        },
        "triggerMode": {
            "description": "hardware外部触发、software单次软触发、freeRun连续采集；决定取帧选择策略。",
            "title": "触发模式",
            "type": "string",
            "enum": ["hardware", "software", "freeRun"],
            "default": "hardware",
        },
        "triggerSource": {
            "description": "hardware有效的触发输入线；IMV Line1..4，托管MVS为Line0..3。",
            "title": "触发输入线",
            "type": "string",
            "enum": ["Line1", "Line2", "Line3", "Line4"],
            "default": "Line1",
        },
        "triggerActivation": {
            "description": "hardware有效触发边沿RisingEdge/FallingEdge。",
            "title": "触发边沿",
            "type": "string",
            "enum": ["RisingEdge", "FallingEdge"],
            "default": "RisingEdge",
        },
        "captureTimeoutMs": {
            "description": "直接路径有界取图预算；托管设备允许0表示可取消硬件等待，实际还受动作deadline限制。",
            "title": "取图超时（毫秒）",
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 5000,
        },
        "waitMode": {
            "title": "取图等待方式", "type": "string", "enum": ["bounded", "hardware"],
            "default": "bounded", "xGlobalVariableBindingDisabled": True,
            "xOptionLabels": {"bounded": "有界超时（兼容默认）", "hardware": "持续等待硬件触发"},
            "description": "直接路径hardware为持续等待，要求硬件触发且retryCount=0；托管由captureTimeoutMs=0表达。",
        },
        "sequencePolicy": {
            "description": "IMV可要求blockId严格连续；托管MVS声明未提供此配置。",
            "title": "设备帧号检查", "type": "string", "enum": ["off", "contiguous"],
            "default": "off", "xGlobalVariableBindingDisabled": True,
            "xOptionLabels": {"off": "不检查（兼容默认）", "contiguous": "严格连续，重复或缺帧停止"},
        },
        "retryCount": {
            "description": "直接IMV路径失败重试数；超时直接返回；托管IMV强制0、不自动重放触发。",
            "title": "异常重试次数", "type": "integer", "minimum": 0, "maximum": 3, "default": 1
        },
        "retryDelayMs": {
            "description": "直接重试前的可取消等待毫秒；托管采集不使用此节点字段。",
            "title": "重试间隔（毫秒）",
            "type": "integer",
            "minimum": 0,
            "maximum": 5000,
            "default": 200,
        },
        "outputColor": {
            "description": "输出uint8 BGR三通道或gray单通道。",
            "title": "输出颜色", "type": "string", "enum": ["bgr", "gray"], "default": "bgr"
        },
        "demosaic": {
            "description": "IMV像素转换去马赛克算法；MVS当前设备schema无此项。",
            "title": "去马赛克算法",
            "type": "string",
            "enum": ["nearest", "bilinear", "edgeSensing"],
            "default": "bilinear",
        },
        "exposureMode": {
            "description": "keep不写曝光配置；manual关闭自动曝光并设置ExposureTime。",
            "title": "曝光设置", "type": "string", "enum": ["keep", "manual"], "default": "keep"
        },
        "exposureUs": {
            "description": "manual请求的曝光微秒；不是当然等于本帧真实曝光。",
            "title": "曝光时间（微秒）", "type": "number", "exclusiveMinimum": 0, "default": 10000.0
        },
        "gainMode": {
            "description": "keep保留设备当前增益；manual关闭自动并设置增益。",
            "title": "增益设置", "type": "string", "enum": ["keep", "manual"], "default": "keep"
        },
        "gainRaw": {"description": "IMV手动原始增益值；MVS使用设备gainDb，不可混同。", "title": "增益值", "type": "number", "default": 0.0},
        "frameRateMode": {
            "description": "IMV keep保留或manual使能并设置采集帧率。",
            "title": "帧率设置", "type": "string", "enum": ["keep", "manual"], "default": "keep"
        },
        "frameRate": {"description": "IMV manual帧率值fps。", "title": "采集帧率（帧/秒）", "type": "number", "exclusiveMinimum": 0, "default": 30.0},
        "roiMode": {
            "description": "IMV keep保留ROI，full取SDK最大宽高并清零偏移，custom使用指定宽高偏移。",
            "title": "采集区域", "type": "string", "enum": ["keep", "custom", "full"], "default": "keep"
        },
        "width": {"description": "IMV custom ROI宽度像素。", "title": "图像宽度（像素）", "type": "integer", "minimum": 1, "default": 1},
        "height": {"description": "IMV custom ROI高度像素。", "title": "图像高度（像素）", "type": "integer", "minimum": 1, "default": 1},
        "offsetX": {
            "description": "IMV custom ROI水平偏移像素；输出frame变换采用实际SDK读回偏移。",
            "title": "水平偏移（像素）", "type": "integer", "minimum": 0, "default": 0
        },
        "offsetY": {
            "description": "IMV custom ROI垂直偏移像素；输出frame变换采用实际SDK读回偏移。",
            "title": "垂直偏移（像素）", "type": "integer", "minimum": 0, "default": 0
        },
        "pixelFormat": {
            "description": "IMV keep保留，其他值写SDK PixelFormat；不是输出numpy颜色模式。",
            "title": "相机像素格式",
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
        displayName="工业相机采集（大华华睿 IMV / 海康 MVS）",
        version="1.2.0",
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
        self._lastBlockId: int | None = None
        self._lifecycleLogger = getOperatorLogger(None)

    def initOperator(self, initContext: dict[str, object]) -> None:
        self._lifecycleLogger = getOperatorLogger(initContext)
        self._lifecycleLogger.info("Huaray camera operator initialized")

    def createConfiguration(self, params: dict[str, object], persist: Callable):
        from emo_master.plugins.builtins.huaray_camera.configuration import CameraConfiguration
        return CameraConfiguration((self._apiFactory or getImvApi)(), params, persist)

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        unknown = sorted(set(params) - set(DEFAULTS))
        if unknown:
            return _paramError(f"unknown camera parameters: {', '.join(unknown)}")

        settings = _settings(params)
        enumFields = {
            "waitMode": {"bounded", "hardware"},
            "sequencePolicy": {"off", "contiguous"},
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
            if not isinstance(settings[name], str) or settings[name] not in allowed:
                return _paramError(f"{name} must be one of: {', '.join(sorted(allowed))}")
        if settings["waitMode"] == "hardware" and (settings["triggerMode"] != "hardware" or settings["retryCount"] != 0):
            return _paramError("waitMode=hardware requires hardware trigger and retryCount=0")
        if settings["sequencePolicy"] == "contiguous" and settings["retryCount"] != 0:
            return _paramError("contiguous frame sequence requires retryCount=0; reconnect cannot recover cycle phase")

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

    @staticmethod
    def validateDeviceParams(params):
        return _paramError("managed camera parameters belong to the device") if params else None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        captureDevice = runtimeContext.get("captureDevice")
        if callable(captureDevice):
            image, descriptor = captureDevice("camera")
            acquisition = descriptor["acquisition"]
            height, width = image.shape[:2]
            space = CoordinateSpace2D(reference=frameReference(runtimeContext, self.meta.operatorId),
                sourceId=acquisition["sourceId"], imageWidth=width, imageHeight=height,
                transformToSource=(1.0, 0.0, 0.0, 1.0, float(acquisition["offsetX"]), float(acquisition["offsetY"])))
            work = cast(dict[str, dict[str, object]], runtimeContext.get("deviceParameters", {})).get("camera", {})
            fields = ["triggerMode", "captureTimeoutMs", "outputColor", "exposureMode"]
            if work.get("triggerMode") == "hardware":
                fields.extend(("triggerSource", "triggerActivation"))
            if work.get("exposureMode") == "manual":
                fields.append("exposureUs")
            return dict(status="ok", outputs=dict(image=image,
                frame=BBox2D(0.0, 0.0, float(width), float(height), space).toPayload(),
                blockId=acquisition["blockId"], deviceTimestamp=acquisition["deviceTimestamp"],
                actualExposureUs=float(acquisition["actualExposureUs"])),
                metrics=dict(captureAttempts=1),
                diagnostics=dict(deviceFrame=descriptor, **deviceSettings(runtimeContext, "camera", fields)))
        _ = inputs
        if runtimeContext.get("isPreview"):
            params = dict(params, waitMode="bounded", sequencePolicy="off",
                          captureTimeoutMs=min(cast(int, params.get("captureTimeoutMs", 1000)), 1000))
        logger = getOperatorLogger(runtimeContext)
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        settings = _settings(params)
        if settings["waitMode"] == "hardware" and not callable(runtimeContext.get("raiseIfCancellationRequested")):
            return {"status": "error", "error": _paramError("hardware wait requires a cancellable runtime context")}
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
            if (settings["waitMode"] == "hardware" or self._session.settings["waitMode"] == "hardware" or
                    settings["sequencePolicy"] == "contiguous" or self._session.settings["sequencePolicy"] == "contiguous"):
                cleanupError = self._dropSession()
                error = HuarayCameraError("E_CAMERA_SESSION_CONFIG_CHANGED",
                    "production capture configuration changed; stop and synchronize before new admission")
                if cleanupError is not None:
                    error.diagnostics["resourceCleanup"] = str(cleanupError)
                return _errorResult(error)
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
                    None if settings["waitMode"] == "hardware" else cast(int, settings["captureTimeoutMs"]),
                    str(settings["outputColor"]),
                    str(settings["demosaic"]),
                    cancellationCheck if callable(cancellationCheck) else None,
                )
                if settings["sequencePolicy"] == "contiguous":
                    if captured.blockId <= 0 or (self._lastBlockId is not None and captured.blockId != self._lastBlockId + 1):
                        raise HuarayCameraError("E_CAMERA_SEQUENCE_UNCERTAIN",
                            f"device frame sequence uncertain: previous={self._lastBlockId}, current={captured.blockId}; synchronize before restarting")
                    self._lastBlockId = captured.blockId
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
        self._lastBlockId = None
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
        settings["waitMode"],
        settings["sequencePolicy"],
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
