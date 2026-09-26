from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import os
from pathlib import Path
import struct
import threading
from time import monotonic
from typing import Any, Callable, Mapping, Protocol, cast

import numpy as np


IMV_OK = 0
IMV_TIMEOUT = -119
IMV_INTERFACE_ALL = 0x00000000

IMV_CREATE_BY_INDEX = 0
IMV_CREATE_BY_CAMERA_KEY = 1
IMV_CREATE_BY_USER_ID = 2
IMV_CREATE_BY_IP_ADDRESS = 3

IMV_GRAB_SEQUENTIAL = 0
IMV_GRAB_LATEST_IMAGE = 1

IMV_PIXEL_MONO8 = 0x01080001
IMV_PIXEL_BAYER_GR8 = 0x01080008
IMV_PIXEL_BAYER_RG8 = 0x01080009
IMV_PIXEL_BAYER_GB8 = 0x0108000A
IMV_PIXEL_BAYER_BG8 = 0x0108000B
IMV_PIXEL_RGB8 = 0x02180014
IMV_PIXEL_BGR8 = 0x02180015

IMV_DEMOSAIC_NEAREST = 0
IMV_DEMOSAIC_BILINEAR = 1
IMV_DEMOSAIC_EDGE_SENSING = 2

_MAX_STRING_LENGTH = 256
_MAX_OUTPUT_BYTES = 0xFFFFFFFF
_apiInstance: ImvApi | None = None
_apiLock = threading.Lock()
_leaseLock = threading.Lock()
_deviceLeases: set[str] = set()


class HuarayCameraError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation: str = "",
        sdkCode: int | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.operation = operation
        self.sdkCode = sdkCode
        self.diagnostics = dict(details or {})
        if operation:
            self.diagnostics.setdefault("operation", operation)
        if sdkCode is not None:
            self.diagnostics.setdefault("sdkCode", sdkCode)
        super().__init__(message)


class ImvApiProtocol(Protocol):
    def version(self) -> str: ...
    def enumerate_devices(self, interfaceType: int = IMV_INTERFACE_ALL) -> tuple[int, int]: ...
    def create_handle(self, mode: int, identifier: str | int) -> tuple[int, Any]: ...
    def destroy_handle(self, handle: object) -> int: ...
    def open(self, handle: object) -> int: ...
    def close(self, handle: object) -> int: ...
    def start_grabbing_ex(self, handle: object, strategy: int) -> int: ...
    def stop_grabbing(self, handle: object) -> int: ...
    def get_frame(self, handle: object, timeoutMs: int) -> tuple[int, Any]: ...
    def release_frame(self, handle: object, frame: Any) -> int: ...
    def set_enum(self, handle: object, name: str, value: str) -> int: ...
    def get_enum(self, handle: object, name: str) -> tuple[int, str]: ...
    def set_double(self, handle: object, name: str, value: float) -> int: ...
    def get_double(self, handle: object, name: str) -> tuple[int, float]: ...
    def get_double_range(self, handle: object, name: str) -> tuple[int, float, float]: ...
    def set_int(self, handle: object, name: str, value: int) -> int: ...
    def get_int(self, handle: object, name: str) -> tuple[int, int]: ...
    def get_int_range(self, handle: object, name: str) -> tuple[int, int, int, int]: ...
    def set_bool(self, handle: object, name: str, value: bool) -> int: ...
    def get_bool(self, handle: object, name: str) -> tuple[int, bool]: ...
    def get_string(self, handle: object, name: str) -> tuple[int, str]: ...
    def execute_command(self, handle: object, name: str) -> int: ...
    def convert_frame(
        self,
        handle: object,
        frame: Any,
        outputColor: str,
        demosaic: str,
    ) -> np.ndarray[Any, Any]: ...


class _IMV_DeviceInfo(ctypes.Structure):
    pass


class IMV_DeviceList(ctypes.Structure):
    _fields_ = [
        ("nDevNum", ctypes.c_uint),
        ("pDevInfo", ctypes.POINTER(_IMV_DeviceInfo)),
    ]


class IMV_String(ctypes.Structure):
    _fields_ = [("str", ctypes.c_char * _MAX_STRING_LENGTH)]


class IMV_FrameInfo(ctypes.Structure):
    _fields_ = [
        ("blockId", ctypes.c_uint64),
        ("status", ctypes.c_uint),
        ("width", ctypes.c_uint),
        ("height", ctypes.c_uint),
        ("size", ctypes.c_uint),
        ("pixelFormat", ctypes.c_int),
        ("timeStamp", ctypes.c_uint64),
        ("chunkCount", ctypes.c_uint),
        ("paddingX", ctypes.c_uint),
        ("paddingY", ctypes.c_uint),
        ("recvFrameTime", ctypes.c_uint),
        ("nReserved", ctypes.c_uint * 19),
    ]


class IMV_Frame(ctypes.Structure):
    _fields_ = [
        ("frameHandle", ctypes.c_void_p),
        ("pData", ctypes.POINTER(ctypes.c_ubyte)),
        ("frameInfo", IMV_FrameInfo),
        ("nReserved", ctypes.c_uint * 10),
    ]


class IMV_PixelConvertParam(ctypes.Structure):
    _fields_ = [
        ("nWidth", ctypes.c_uint),
        ("nHeight", ctypes.c_uint),
        ("ePixelFormat", ctypes.c_int),
        ("pSrcData", ctypes.POINTER(ctypes.c_ubyte)),
        ("nSrcDataLen", ctypes.c_uint),
        ("nPaddingX", ctypes.c_uint),
        ("nPaddingY", ctypes.c_uint),
        ("eBayerDemosaic", ctypes.c_int),
        ("eDstPixelFormat", ctypes.c_int),
        ("pDstBuf", ctypes.POINTER(ctypes.c_ubyte)),
        ("nDstBufSize", ctypes.c_uint),
        ("nDstDataLen", ctypes.c_uint),
        ("nReserved", ctypes.c_uint * 8),
    ]


def candidateMvViewerRoots() -> tuple[Path, ...]:
    roots: list[Path] = []
    configuredRoot = os.environ.get("HUARAY_MV_VIEWER_ROOT")
    if configuredRoot:
        roots.append(Path(configuredRoot))
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        programFiles = os.environ.get(variable)
        if programFiles:
            roots.append(Path(programFiles) / "HuarayTech" / "MV Viewer")
    roots.append(Path(r"C:\Program Files\HuarayTech\MV Viewer"))

    uniqueRoots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        normalized = os.path.normcase(os.path.abspath(str(root)))
        if normalized in seen:
            continue
        seen.add(normalized)
        uniqueRoots.append(Path(normalized))
    return tuple(uniqueRoots)


def resolveImvDllPath() -> Path:
    architecture = "x64" if struct.calcsize("P") * 8 == 64 else "Win32"
    checked: list[str] = []
    for root in candidateMvViewerRoots():
        candidate = root if root.suffix.lower() == ".dll" else root / "Runtime" / architecture / "MVSDKmd.dll"
        checked.append(str(candidate))
        if candidate.is_file():
            return candidate
    raise HuarayCameraError(
        "E_CAMERA_SDK_UNAVAILABLE",
        "Huaray IMV runtime was not found; install MV Viewer or set HUARAY_MV_VIEWER_ROOT",
        operation="locate IMV runtime",
        details={"checkedPaths": checked, "processBits": struct.calcsize("P") * 8},
    )


class ImvApi:
    def __init__(self, dllPath: Path | str | None = None) -> None:
        if os.name != "nt":
            raise HuarayCameraError(
                "E_CAMERA_SDK_UNAVAILABLE",
                "Huaray IMV runtime is only supported on Windows",
                operation="load IMV runtime",
            )
        self.dllPath = Path(dllPath) if dllPath is not None else resolveImvDllPath()
        runtimeDirectory = str(self.dllPath.parent)
        self._dllDirectory: object | None = None
        try:
            if hasattr(os, "add_dll_directory"):
                self._dllDirectory = os.add_dll_directory(runtimeDirectory)
            else:  # pragma: no cover - Python 3.10 Windows has add_dll_directory
                os.environ["PATH"] = runtimeDirectory + os.pathsep + os.environ.get("PATH", "")
            self.dll = ctypes.WinDLL(str(self.dllPath))  # type: ignore[attr-defined]
            self._configureFunctions()
        except HuarayCameraError:
            raise
        except Exception as err:
            raise HuarayCameraError(
                "E_CAMERA_SDK_UNAVAILABLE",
                f"failed to load Huaray IMV runtime: {err}",
                operation="load IMV runtime",
                details={"dllPath": str(self.dllPath), "processBits": struct.calcsize("P") * 8},
            ) from err

    def _configure(self, name: str, argtypes: list[object], restype: object = ctypes.c_int):
        try:
            function = getattr(self.dll, name)
        except AttributeError as err:
            raise HuarayCameraError(
                "E_CAMERA_SDK_UNAVAILABLE",
                f"IMV runtime is missing required export {name}",
                operation="resolve IMV export",
                details={"dllPath": str(self.dllPath), "export": name},
            ) from err
        function.argtypes = argtypes
        function.restype = restype
        return function

    def _configureFunctions(self) -> None:
        self._getVersion = self._configure("IMV_GetVersion", [], ctypes.c_char_p)
        self._enumDevices = self._configure(
            "IMV_EnumDevices", [ctypes.POINTER(IMV_DeviceList), ctypes.c_uint]
        )
        self._createHandle = self._configure(
            "IMV_CreateHandle", [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_void_p]
        )
        self._destroyHandle = self._configure("IMV_DestroyHandle", [ctypes.c_void_p])
        self._open = self._configure("IMV_Open", [ctypes.c_void_p])
        self._isOpen = self._configure("IMV_IsOpen", [ctypes.c_void_p], ctypes.c_bool)
        self._close = self._configure("IMV_Close", [ctypes.c_void_p])
        self._startGrabbingEx = self._configure(
            "IMV_StartGrabbingEx", [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int]
        )
        self._isGrabbing = self._configure("IMV_IsGrabbing", [ctypes.c_void_p], ctypes.c_bool)
        self._stopGrabbing = self._configure("IMV_StopGrabbing", [ctypes.c_void_p])
        self._getFrame = self._configure(
            "IMV_GetFrame", [ctypes.c_void_p, ctypes.POINTER(IMV_Frame), ctypes.c_uint]
        )
        self._releaseFrame = self._configure(
            "IMV_ReleaseFrame", [ctypes.c_void_p, ctypes.POINTER(IMV_Frame)]
        )
        self._setEnum = self._configure(
            "IMV_SetEnumFeatureSymbol", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        )
        self._getEnum = self._configure(
            "IMV_GetEnumFeatureSymbol", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(IMV_String)]
        )
        self._setDouble = self._configure(
            "IMV_SetDoubleFeatureValue", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]
        )
        self._getDouble = self._configure(
            "IMV_GetDoubleFeatureValue", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_double)]
        )
        self._getDoubleMin = self._configure(
            "IMV_GetDoubleFeatureMin", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_double)]
        )
        self._getDoubleMax = self._configure(
            "IMV_GetDoubleFeatureMax", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_double)]
        )
        self._setInt = self._configure(
            "IMV_SetIntFeatureValue", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int64]
        )
        self._getInt = self._configure(
            "IMV_GetIntFeatureValue", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int64)]
        )
        self._getIntMin = self._configure(
            "IMV_GetIntFeatureMin", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int64)]
        )
        self._getIntMax = self._configure(
            "IMV_GetIntFeatureMax", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int64)]
        )
        self._getIntInc = self._configure(
            "IMV_GetIntFeatureInc", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int64)]
        )
        self._setBool = self._configure(
            "IMV_SetBoolFeatureValue", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool]
        )
        self._getBool = self._configure(
            "IMV_GetBoolFeatureValue", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_bool)]
        )
        self._getString = self._configure(
            "IMV_GetStringFeatureValue", [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(IMV_String)]
        )
        self._executeCommand = self._configure(
            "IMV_ExecuteCommandFeature", [ctypes.c_void_p, ctypes.c_char_p]
        )
        self._pixelConvert = self._configure(
            "IMV_PixelConvert", [ctypes.c_void_p, ctypes.POINTER(IMV_PixelConvertParam)]
        )

    def version(self) -> str:
        rawValue = self._getVersion()
        return rawValue.decode("utf-8", errors="replace") if rawValue else "unknown"

    def enumerate_devices(self, interfaceType: int = IMV_INTERFACE_ALL) -> tuple[int, int]:
        devices = IMV_DeviceList()
        result = self._enumDevices(ctypes.byref(devices), ctypes.c_uint(interfaceType))
        return int(result), int(devices.nDevNum)

    def create_handle(self, mode: int, identifier: str | int) -> tuple[int, ctypes.c_void_p]:
        handle = ctypes.c_void_p()
        if mode == IMV_CREATE_BY_INDEX:
            indexValue = ctypes.c_uint(int(identifier))
            pointer = ctypes.cast(ctypes.byref(indexValue), ctypes.c_void_p)
        else:
            encoded = str(identifier).encode("utf-8")
            stringValue = ctypes.create_string_buffer(encoded)
            pointer = ctypes.cast(stringValue, ctypes.c_void_p)
        result = self._createHandle(ctypes.byref(handle), ctypes.c_int(mode), pointer)
        return int(result), handle

    def destroy_handle(self, handle: object) -> int:
        return int(self._destroyHandle(handle))

    def open(self, handle: object) -> int:
        return int(self._open(handle))

    def is_open(self, handle: object) -> bool:
        return bool(self._isOpen(handle))

    def close(self, handle: object) -> int:
        return int(self._close(handle))

    def start_grabbing_ex(self, handle: object, strategy: int) -> int:
        return int(self._startGrabbingEx(handle, ctypes.c_uint64(0), ctypes.c_int(strategy)))

    def is_grabbing(self, handle: object) -> bool:
        return bool(self._isGrabbing(handle))

    def stop_grabbing(self, handle: object) -> int:
        return int(self._stopGrabbing(handle))

    def get_frame(self, handle: object, timeoutMs: int) -> tuple[int, IMV_Frame]:
        frame = IMV_Frame()
        result = self._getFrame(handle, ctypes.byref(frame), ctypes.c_uint(timeoutMs))
        return int(result), frame

    def release_frame(self, handle: object, frame: Any) -> int:
        return int(self._releaseFrame(handle, ctypes.byref(frame)))

    def set_enum(self, handle: object, name: str, value: str) -> int:
        return int(self._setEnum(handle, name.encode("ascii"), value.encode("ascii")))

    def get_enum(self, handle: object, name: str) -> tuple[int, str]:
        value = IMV_String()
        result = int(self._getEnum(handle, name.encode("ascii"), ctypes.byref(value)))
        return result, value.str.decode("utf-8", errors="replace") if result == IMV_OK else ""

    def set_double(self, handle: object, name: str, value: float) -> int:
        return int(self._setDouble(handle, name.encode("ascii"), ctypes.c_double(value)))

    def get_double(self, handle: object, name: str) -> tuple[int, float]:
        value = ctypes.c_double()
        result = int(self._getDouble(handle, name.encode("ascii"), ctypes.byref(value)))
        return result, float(value.value)

    def get_double_range(self, handle: object, name: str) -> tuple[int, float, float]:
        minimum = ctypes.c_double()
        maximum = ctypes.c_double()
        minResult = int(self._getDoubleMin(handle, name.encode("ascii"), ctypes.byref(minimum)))
        if minResult != IMV_OK:
            return minResult, 0.0, 0.0
        maxResult = int(self._getDoubleMax(handle, name.encode("ascii"), ctypes.byref(maximum)))
        return maxResult, float(minimum.value), float(maximum.value)

    def set_int(self, handle: object, name: str, value: int) -> int:
        return int(self._setInt(handle, name.encode("ascii"), ctypes.c_int64(value)))

    def get_int(self, handle: object, name: str) -> tuple[int, int]:
        value = ctypes.c_int64()
        result = int(self._getInt(handle, name.encode("ascii"), ctypes.byref(value)))
        return result, int(value.value)

    def get_int_range(self, handle: object, name: str) -> tuple[int, int, int, int]:
        minimum = ctypes.c_int64()
        maximum = ctypes.c_int64()
        increment = ctypes.c_int64()
        minResult = int(self._getIntMin(handle, name.encode("ascii"), ctypes.byref(minimum)))
        if minResult != IMV_OK:
            return minResult, 0, 0, 0
        maxResult = int(self._getIntMax(handle, name.encode("ascii"), ctypes.byref(maximum)))
        if maxResult != IMV_OK:
            return maxResult, 0, 0, 0
        incResult = int(self._getIntInc(handle, name.encode("ascii"), ctypes.byref(increment)))
        return incResult, int(minimum.value), int(maximum.value), int(increment.value)

    def set_bool(self, handle: object, name: str, value: bool) -> int:
        return int(self._setBool(handle, name.encode("ascii"), ctypes.c_bool(value)))

    def get_bool(self, handle: object, name: str) -> tuple[int, bool]:
        value = ctypes.c_bool()
        result = int(self._getBool(handle, name.encode("ascii"), ctypes.byref(value)))
        return result, bool(value.value)

    def get_string(self, handle: object, name: str) -> tuple[int, str]:
        value = IMV_String()
        result = int(self._getString(handle, name.encode("ascii"), ctypes.byref(value)))
        return result, value.str.decode("utf-8", errors="replace") if result == IMV_OK else ""

    def execute_command(self, handle: object, name: str) -> int:
        return int(self._executeCommand(handle, name.encode("ascii")))

    def convert_frame(
        self,
        handle: object,
        frame: Any,
        outputColor: str,
        demosaic: str,
    ) -> np.ndarray[Any, Any]:
        info = frame.frameInfo
        width = int(info.width)
        height = int(info.height)
        channels = 1 if outputColor == "gray" else 3
        outputSize = width * height * channels
        if outputSize <= 0 or outputSize > _MAX_OUTPUT_BYTES:
            raise HuarayCameraError(
                "E_CAMERA_FRAME_INVALID",
                f"converted frame size is invalid: {outputSize}",
                operation="validate conversion buffer",
            )
        destination = (ctypes.c_ubyte * outputSize)()
        parameters = IMV_PixelConvertParam()
        parameters.nWidth = width
        parameters.nHeight = height
        parameters.ePixelFormat = int(info.pixelFormat)
        parameters.pSrcData = frame.pData
        parameters.nSrcDataLen = int(info.size)
        parameters.nPaddingX = int(info.paddingX)
        parameters.nPaddingY = int(info.paddingY)
        parameters.eBayerDemosaic = {
            "nearest": IMV_DEMOSAIC_NEAREST,
            "bilinear": IMV_DEMOSAIC_BILINEAR,
            "edgeSensing": IMV_DEMOSAIC_EDGE_SENSING,
        }[demosaic]
        parameters.eDstPixelFormat = IMV_PIXEL_MONO8 if outputColor == "gray" else IMV_PIXEL_BGR8
        parameters.pDstBuf = ctypes.cast(destination, ctypes.POINTER(ctypes.c_ubyte))
        parameters.nDstBufSize = outputSize
        result = int(self._pixelConvert(handle, ctypes.byref(parameters)))
        if result != IMV_OK:
            raise HuarayCameraError(
                "E_CAMERA_FRAME_INVALID",
                f"IMV_PixelConvert failed with SDK code {result}",
                operation="IMV_PixelConvert",
                sdkCode=result,
            )
        actualSize = int(parameters.nDstDataLen)
        if actualSize != outputSize:
            raise HuarayCameraError(
                "E_CAMERA_FRAME_INVALID",
                f"converted frame length mismatch: expected {outputSize}, got {actualSize}",
                operation="validate converted frame",
            )
        array = np.ctypeslib.as_array(destination)
        shape = (height, width) if channels == 1 else (height, width, channels)
        return array.reshape(shape).copy()


def getImvApi() -> ImvApi:
    global _apiInstance
    if _apiInstance is None:
        with _apiLock:
            if _apiInstance is None:
                _apiInstance = ImvApi()
    return _apiInstance


@dataclass(frozen=True)
class CapturedFrame:
    image: np.ndarray[Any, Any]
    blockId: int
    deviceTimestamp: int


class CameraSession:
    def __init__(self, api: ImvApiProtocol, settings: Mapping[str, object]) -> None:
        self.api = api
        self.settings = dict(settings)
        self.handle: object | None = None
        self.handleCreated = False
        self.opened = False
        self.grabbing = False
        self.leaseKey = _selectorLeaseKey(self.settings)
        self.leaseAcquired = False
        self.actualExposureUs = 0.0
        self.offsetX = 0
        self.offsetY = 0
        self.sourceId = _fallbackSourceId(self.settings)

    def open(self) -> None:
        if self.opened and self.grabbing:
            return
        _acquireLease(self.leaseKey)
        self.leaseAcquired = True
        try:
            selectionMode = str(self.settings["selectionMode"])
            createMode, identifier = _createIdentifier(self.settings)
            # IMV resolves every selector against its process-local device list.
            enumResult, deviceCount = self.api.enumerate_devices(IMV_INTERFACE_ALL)
            _checkSdkResult(
                enumResult,
                "E_CAMERA_IO",
                "IMV_EnumDevices failed",
                "IMV_EnumDevices",
            )
            if selectionMode == "index":
                if int(identifier) >= deviceCount:
                    raise HuarayCameraError(
                        "E_CAMERA_DEVICE_NOT_FOUND",
                        f"camera index {identifier} is out of range for {deviceCount} enumerated devices",
                        operation="select camera by index",
                        details={"deviceCount": deviceCount, "deviceIndex": int(identifier)},
                    )
            createResult, handle = self.api.create_handle(createMode, identifier)
            if createResult != IMV_OK or handle is None:
                raise HuarayCameraError(
                    "E_CAMERA_DEVICE_NOT_FOUND",
                    f"IMV_CreateHandle could not resolve camera selector ({createResult})",
                    operation="IMV_CreateHandle",
                    sdkCode=int(createResult),
                )
            self.handle = handle
            self.handleCreated = True
            openResult = self.api.open(self.handle)
            _checkSdkResult(
                openResult,
                "E_CAMERA_OPEN_FAILED",
                f"IMV_Open failed with SDK code {openResult}",
                "IMV_Open",
            )
            self.opened = True
            self._configureCamera()
            strategy = (
                IMV_GRAB_LATEST_IMAGE
                if self.settings["triggerMode"] == "freeRun"
                else IMV_GRAB_SEQUENTIAL
            )
            startResult = self.api.start_grabbing_ex(self.handle, strategy)
            _checkSdkResult(
                startResult,
                "E_CAMERA_IO",
                f"IMV_StartGrabbingEx failed with SDK code {startResult}",
                "IMV_StartGrabbingEx",
            )
            self.grabbing = True
        except Exception as err:
            cleanupError = self.close()
            if cleanupError is not None and isinstance(err, HuarayCameraError):
                err.diagnostics["cleanupError"] = str(cleanupError)
            if isinstance(err, HuarayCameraError):
                raise
            raise HuarayCameraError(
                "E_CAMERA_IO",
                f"unexpected IMV initialization failure: {err}",
                operation="initialize camera session",
            ) from err

    def _configureCamera(self) -> None:
        if self.handle is None:
            raise HuarayCameraError("E_CAMERA_OPEN_FAILED", "camera handle is missing")

        pixelFormat = str(self.settings["pixelFormat"])
        if pixelFormat != "keep":
            self._setEnum("PixelFormat", pixelFormat)

        roiMode = str(self.settings["roiMode"])
        if roiMode in {"custom", "full"}:
            self._setInt("OffsetX", 0)
            self._setInt("OffsetY", 0)
            if roiMode == "full":
                _, widthMaximum, _ = self._intRange("Width")
                _, heightMaximum, _ = self._intRange("Height")
                self._setInt("Width", widthMaximum)
                self._setInt("Height", heightMaximum)
            else:
                self._setInt("Width", cast(int, self.settings["width"]))
                self._setInt("Height", cast(int, self.settings["height"]))
                self._setInt("OffsetX", cast(int, self.settings["offsetX"]))
                self._setInt("OffsetY", cast(int, self.settings["offsetY"]))

        if self.settings["exposureMode"] == "manual":
            self._setEnum("ExposureAuto", "Off")
            self._setDouble("ExposureTime", float(cast(int | float, self.settings["exposureUs"])))
        if self.settings["gainMode"] == "manual":
            self._setEnum("GainAuto", "Off")
            self._setDouble("GainRaw", float(cast(int | float, self.settings["gainRaw"])))
        if self.settings["frameRateMode"] == "manual":
            self._setBool("AcquisitionFrameRateEnable", True)
            self._setDouble(
                "AcquisitionFrameRate",
                float(cast(int | float, self.settings["frameRate"])),
            )

        triggerMode = str(self.settings["triggerMode"])
        if triggerMode == "freeRun":
            self._setEnum("TriggerMode", "Off")
        else:
            self._setEnum("TriggerSelector", "FrameStart")
            if triggerMode == "software":
                self._setEnum("TriggerSource", "Software")
            else:
                self._setEnum("TriggerSource", str(self.settings["triggerSource"]))
                self._setEnum("TriggerActivation", str(self.settings["triggerActivation"]))
            self._setEnum("TriggerMode", "On")

        self.actualExposureUs = self._getDouble("ExposureTime")
        self.offsetX = self._getInt("OffsetX")
        self.offsetY = self._getInt("OffsetY")
        try:
            serialResult, serialNumber = self.api.get_string(self.handle, "DeviceSerialNumber")
        except Exception:
            serialResult, serialNumber = -1, ""
        if serialResult == IMV_OK and isinstance(serialNumber, str) and serialNumber.strip():
            self.sourceId = f"huaray-imv:{serialNumber.strip()}"

    def _setEnum(self, name: str, value: str) -> None:
        try:
            result = self.api.set_enum(self.handle, name, value)
            _checkSdkResult(result, "E_CAMERA_CONFIG_FAILED", f"failed to set {name}={value}", f"set {name}")
            readResult, actual = self.api.get_enum(self.handle, name)
            _checkSdkResult(readResult, "E_CAMERA_CONFIG_FAILED", f"failed to read back {name}", f"read {name}")
        except HuarayCameraError:
            raise
        except Exception as err:
            raise _configurationException(name, err) from err
        if actual != value:
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED",
                f"{name} readback mismatch: requested {value}, got {actual}",
                operation=f"verify {name}",
            )

    def _setDouble(self, name: str, value: float) -> None:
        minimum, maximum = self._doubleRange(name)
        if value < minimum or value > maximum:
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED",
                f"{name}={value} is outside device range [{minimum}, {maximum}]",
                operation=f"validate {name}",
            )
        try:
            result = self.api.set_double(self.handle, name, value)
            _checkSdkResult(result, "E_CAMERA_CONFIG_FAILED", f"failed to set {name}={value}", f"set {name}")
            actual = self._getDouble(name)
        except HuarayCameraError:
            raise
        except Exception as err:
            raise _configurationException(name, err) from err
        tolerance = max(1e-9, abs(value) * 1e-6)
        if not math.isclose(actual, value, rel_tol=1e-6, abs_tol=tolerance):
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED",
                f"{name} readback mismatch: requested {value}, got {actual}",
                operation=f"verify {name}",
            )

    def _setInt(self, name: str, value: int) -> None:
        minimum, maximum, increment = self._intRange(name)
        if value < minimum or value > maximum:
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED",
                f"{name}={value} is outside device range [{minimum}, {maximum}]",
                operation=f"validate {name}",
            )
        if increment > 0 and (value - minimum) % increment != 0:
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED",
                f"{name}={value} does not match device increment {increment} from {minimum}",
                operation=f"validate {name}",
            )
        try:
            result = self.api.set_int(self.handle, name, value)
            _checkSdkResult(result, "E_CAMERA_CONFIG_FAILED", f"failed to set {name}={value}", f"set {name}")
            actual = self._getInt(name)
        except HuarayCameraError:
            raise
        except Exception as err:
            raise _configurationException(name, err) from err
        if actual != value:
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED",
                f"{name} readback mismatch: requested {value}, got {actual}",
                operation=f"verify {name}",
            )

    def _setBool(self, name: str, value: bool) -> None:
        try:
            result = self.api.set_bool(self.handle, name, value)
            _checkSdkResult(result, "E_CAMERA_CONFIG_FAILED", f"failed to set {name}={value}", f"set {name}")
            readResult, actual = self.api.get_bool(self.handle, name)
            _checkSdkResult(readResult, "E_CAMERA_CONFIG_FAILED", f"failed to read back {name}", f"read {name}")
        except HuarayCameraError:
            raise
        except Exception as err:
            raise _configurationException(name, err) from err
        if bool(actual) is not value:
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED",
                f"{name} readback mismatch: requested {value}, got {actual}",
                operation=f"verify {name}",
            )

    def _getDouble(self, name: str) -> float:
        try:
            result, value = self.api.get_double(self.handle, name)
        except Exception as err:
            raise _configurationException(name, err) from err
        _checkSdkResult(result, "E_CAMERA_CONFIG_FAILED", f"failed to read {name}", f"read {name}")
        if not math.isfinite(float(value)):
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED", f"{name} readback is not finite", operation=f"read {name}"
            )
        return float(value)

    def _doubleRange(self, name: str) -> tuple[float, float]:
        try:
            result, minimum, maximum = self.api.get_double_range(self.handle, name)
        except Exception as err:
            raise _configurationException(name, err) from err
        _checkSdkResult(result, "E_CAMERA_CONFIG_FAILED", f"failed to query {name} range", f"query {name} range")
        if not (math.isfinite(float(minimum)) and math.isfinite(float(maximum))) or minimum > maximum:
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED", f"device returned invalid {name} range", operation=f"query {name} range"
            )
        return float(minimum), float(maximum)

    def _getInt(self, name: str) -> int:
        try:
            result, value = self.api.get_int(self.handle, name)
        except Exception as err:
            raise _configurationException(name, err) from err
        _checkSdkResult(result, "E_CAMERA_CONFIG_FAILED", f"failed to read {name}", f"read {name}")
        return int(value)

    def _intRange(self, name: str) -> tuple[int, int, int]:
        try:
            result, minimum, maximum, increment = self.api.get_int_range(self.handle, name)
        except Exception as err:
            raise _configurationException(name, err) from err
        _checkSdkResult(result, "E_CAMERA_CONFIG_FAILED", f"failed to query {name} range", f"query {name} range")
        if minimum > maximum or increment < 0:
            raise HuarayCameraError(
                "E_CAMERA_CONFIG_FAILED", f"device returned invalid {name} range", operation=f"query {name} range"
            )
        return int(minimum), int(maximum), int(increment)

    def capture(
        self,
        timeoutMs: int,
        outputColor: str,
        demosaic: str,
        raiseIfCancelled: Callable[[], object] | None = None,
    ) -> CapturedFrame:
        if not self.opened or not self.grabbing or self.handle is None:
            raise HuarayCameraError(
                "E_CAMERA_IO", "camera session is not grabbing", operation="capture frame"
            )
        checkCancellation = raiseIfCancelled if callable(raiseIfCancelled) else lambda: None
        checkCancellation()
        if self.settings["triggerMode"] == "software":
            try:
                triggerResult = self.api.execute_command(self.handle, "TriggerSoftware")
            except Exception as err:
                raise HuarayCameraError(
                    "E_CAMERA_IO", f"software trigger failed: {err}", operation="TriggerSoftware"
                ) from err
            _checkSdkResult(
                triggerResult,
                "E_CAMERA_IO",
                f"software trigger failed with SDK code {triggerResult}",
                "TriggerSoftware",
            )

        deadline = monotonic() + timeoutMs / 1000.0
        frame: object | None = None
        while frame is None:
            checkCancellation()
            remainingMs = math.ceil((deadline - monotonic()) * 1000.0)
            if remainingMs <= 0:
                raise HuarayCameraError(
                    "E_CAMERA_TIMEOUT",
                    f"camera frame timed out after {timeoutMs} ms",
                    operation="IMV_GetFrame",
                )
            chunkMs = max(1, min(100, remainingMs))
            try:
                result, candidate = self.api.get_frame(self.handle, chunkMs)
            except Exception as err:
                raise HuarayCameraError(
                    "E_CAMERA_IO", f"IMV_GetFrame raised an exception: {err}", operation="IMV_GetFrame"
                ) from err
            if result == IMV_TIMEOUT:
                continue
            _checkSdkResult(
                result,
                "E_CAMERA_IO",
                f"IMV_GetFrame failed with SDK code {result}",
                "IMV_GetFrame",
            )
            frame = candidate

        primaryError: Exception | None = None
        captured: CapturedFrame | None = None
        try:
            info = getattr(frame, "frameInfo", None)
            if info is None:
                raise HuarayCameraError(
                    "E_CAMERA_FRAME_INVALID", "frameInfo is missing", operation="validate frame"
                )
            status = int(getattr(info, "status", -1))
            width = int(getattr(info, "width", 0))
            height = int(getattr(info, "height", 0))
            size = int(getattr(info, "size", 0))
            data = getattr(frame, "pData", None)
            if status != 0 or width <= 0 or height <= 0 or size <= 0 or not data:
                raise HuarayCameraError(
                    "E_CAMERA_FRAME_INVALID",
                    f"invalid frame: status={status}, width={width}, height={height}, size={size}",
                    operation="validate frame",
                )
            try:
                image = self.api.convert_frame(self.handle, frame, outputColor, demosaic)
            except HuarayCameraError:
                raise
            except Exception as err:
                raise HuarayCameraError(
                    "E_CAMERA_FRAME_INVALID", f"frame conversion failed: {err}", operation="convert frame"
                ) from err
            expectedShape = (height, width) if outputColor == "gray" else (height, width, 3)
            if (
                not isinstance(image, np.ndarray)
                or image.dtype != np.uint8
                or image.shape != expectedShape
            ):
                raise HuarayCameraError(
                    "E_CAMERA_FRAME_INVALID",
                    f"converted frame must be uint8 with shape {expectedShape}",
                    operation="validate converted frame",
                )
            captured = CapturedFrame(
                image=image.copy(),
                blockId=int(getattr(info, "blockId", 0)),
                deviceTimestamp=int(getattr(info, "timeStamp", 0)),
            )
        except Exception as err:
            primaryError = err

        releaseError: HuarayCameraError | None = None
        try:
            releaseResult = self.api.release_frame(self.handle, frame)
            if releaseResult != IMV_OK:
                releaseError = HuarayCameraError(
                    "E_CAMERA_IO",
                    f"IMV_ReleaseFrame failed with SDK code {releaseResult}",
                    operation="IMV_ReleaseFrame",
                    sdkCode=int(releaseResult),
                )
        except Exception as err:
            releaseError = HuarayCameraError(
                "E_CAMERA_IO", f"IMV_ReleaseFrame raised an exception: {err}", operation="IMV_ReleaseFrame"
            )
        if primaryError is not None:
            if releaseError is not None and isinstance(primaryError, HuarayCameraError):
                primaryError.diagnostics["releaseError"] = str(releaseError)
            raise primaryError
        if releaseError is not None:
            raise releaseError
        if captured is None:  # pragma: no cover - guarded by primaryError
            raise HuarayCameraError("E_CAMERA_FRAME_INVALID", "frame conversion produced no output")
        return captured

    def close(self) -> HuarayCameraError | None:
        errors: list[str] = []
        if self.handle is not None and self.grabbing:
            try:
                result = self.api.stop_grabbing(self.handle)
                if result != IMV_OK:
                    errors.append(f"IMV_StopGrabbing={result}")
                else:
                    self.grabbing = False
            except Exception as err:
                errors.append(f"IMV_StopGrabbing exception: {err}")
        if self.handle is not None and self.opened:
            try:
                result = self.api.close(self.handle)
                if result != IMV_OK:
                    errors.append(f"IMV_Close={result}")
                else:
                    self.opened = False
            except Exception as err:
                errors.append(f"IMV_Close exception: {err}")
        if self.handle is not None and self.handleCreated:
            try:
                result = self.api.destroy_handle(self.handle)
                if result != IMV_OK:
                    errors.append(f"IMV_DestroyHandle={result}")
                else:
                    self.handleCreated = False
                    self.handle = None
            except Exception as err:
                errors.append(f"IMV_DestroyHandle exception: {err}")
        if self.handle is None:
            self.grabbing = False
            self.opened = False
            if self.leaseAcquired:
                _releaseLease(self.leaseKey)
                self.leaseAcquired = False
        if not errors:
            return None
        return HuarayCameraError(
            "E_RESOURCE_CLEANUP_FAILED",
            "camera resource cleanup failed: " + "; ".join(errors),
            operation="close camera session",
            details={"cleanupErrors": errors},
        )


def _checkSdkResult(result: int, code: str, message: str, operation: str) -> None:
    if result != IMV_OK:
        raise HuarayCameraError(code, message, operation=operation, sdkCode=result)


def _configurationException(name: str, error: Exception) -> HuarayCameraError:
    return HuarayCameraError(
        "E_CAMERA_CONFIG_FAILED",
        f"camera feature {name} is unavailable or invalid: {error}",
        operation=f"configure {name}",
    )


def _selectorLeaseKey(settings: Mapping[str, object]) -> str:
    selectionMode = str(settings["selectionMode"])
    identifierName = {
        "ip": "ipAddress",
        "cameraKey": "cameraKey",
        "userId": "userId",
        "index": "deviceIndex",
    }[selectionMode]
    return f"{selectionMode}:{settings[identifierName]}"


def _acquireLease(leaseKey: str) -> None:
    with _leaseLock:
        if leaseKey in _deviceLeases:
            raise HuarayCameraError(
                "E_CAMERA_OPEN_FAILED",
                f"camera selector is already opened by another node: {leaseKey}",
                operation="acquire camera lease",
            )
        _deviceLeases.add(leaseKey)


def _releaseLease(leaseKey: str) -> None:
    with _leaseLock:
        _deviceLeases.discard(leaseKey)


def _createIdentifier(settings: Mapping[str, object]) -> tuple[int, str | int]:
    mode = str(settings["selectionMode"])
    if mode == "ip":
        return IMV_CREATE_BY_IP_ADDRESS, str(settings["ipAddress"])
    if mode == "cameraKey":
        return IMV_CREATE_BY_CAMERA_KEY, str(settings["cameraKey"])
    if mode == "userId":
        return IMV_CREATE_BY_USER_ID, str(settings["userId"])
    return IMV_CREATE_BY_INDEX, cast(int, settings["deviceIndex"])


def _fallbackSourceId(settings: Mapping[str, object]) -> str:
    mode = str(settings["selectionMode"])
    identifierName = {
        "ip": "ipAddress",
        "cameraKey": "cameraKey",
        "userId": "userId",
        "index": "deviceIndex",
    }[mode]
    return f"huaray-imv:{mode}:{settings[identifierName]}"


def resetImvApiForTests() -> None:
    global _apiInstance
    with _apiLock:
        _apiInstance = None
    with _leaseLock:
        _deviceLeases.clear()
