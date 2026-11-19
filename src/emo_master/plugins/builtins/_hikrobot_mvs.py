"""Hikrobot GigE / USB3 cameras through the Python bindings shipped with MVS."""

from __future__ import annotations

from contextlib import ExitStack
import ctypes
from importlib import import_module
from ipaddress import IPv4Address
import math
import os
from pathlib import Path
import sys
from threading import Lock
from time import monotonic
from typing import Any, Callable, Mapping

import numpy as np


class MvsError(RuntimeError):
    softwareTriggerIssued = False
    frameReceived = False

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require(result: int, operation: str, code: str = "E_CAMERA_IO") -> None:
    if result:
        raise MvsError(code, f"{operation} failed (MVS 0x{result & 0xFFFFFFFF:08X})")


def _importWindowsBindings() -> Any:
    try:
        return import_module("MvCameraControl_class")
    except OSError as err:
        # The vendor loads a bare DLL name. Frozen ctypes can exclude PATH
        # from that lookup, and portable launches may start with a clean PATH.
        lastError = err

    if sys.platform != "win32":
        raise lastError

    is64Bit = sys.maxsize > 2**32
    platformDir = "Win64_x64" if is64Bit else "Win32_i86"
    incompatibleDir = "win32_i86" if is64Bit else "win64_x64"
    common = Path(os.environ.get("CommonProgramFiles(x86)", r"C:\Program Files (x86)\Common Files"))
    paths = [Path(entry.strip('"')) for entry in os.get_exec_path() if entry]
    paths.append(common / "MVS" / "Runtime" / platformDir)
    for directory in dict.fromkeys(paths):
        dll = directory / "MvCameraControl.dll"
        if not directory.is_absolute() or directory.name.casefold() == incompatibleDir or not dll.is_file():
            continue
        try:
            with ExitStack() as search:
                search.enter_context(os.add_dll_directory(str(directory)))
                ctypes.WinDLL(str(dll))
                sdk: Any = import_module("MvCameraControl_class")
                # Keep the directory available to the SDK's delay-loaded DLLs.
                sdk._emoRuntimeDllSearch = search.pop_all()
                return sdk
        except OSError as err:
            lastError = err
    raise lastError


def _loadBindings() -> Any:
    if os.name != "nt":
        raise MvsError("E_CAMERA_SDK_UNAVAILABLE", "Hikrobot MVS requires Windows")
    development = os.environ.get("MVCAM_COMMON_RUNENV")
    if not development:
        development = str(
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "MVS" / "Development"
        )
    directory = Path(development) / "Samples" / "Python" / "MvImport"
    if not (directory / "MvCameraControl_class.py").is_file():
        raise MvsError(
            "E_CAMERA_SDK_UNAVAILABLE", f"Install the MVS Python development samples: {directory}"
        )
    # The vendor module imports its sibling files by absolute (top-level) name.
    sys.path.insert(0, str(directory))
    try:
        return _importWindowsBindings()
    except (ImportError, OSError) as err:
        raise MvsError(
            "E_CAMERA_SDK_UNAVAILABLE", f"Could not load the MVS Python bindings or runtime: {err}"
        ) from err
    finally:
        sys.path.remove(str(directory))


_sdkLock = Lock()
_sdkUsers = 0


def _acquireSdk() -> Any:
    global _sdkUsers
    with _sdkLock:
        sdk = _loadBindings()
        if _sdkUsers == 0:
            _require(sdk.MvCamera.MV_CC_Initialize(), "MV_CC_Initialize", "E_CAMERA_SDK_UNAVAILABLE")
        _sdkUsers += 1
    return sdk


def _releaseSdk(sdk: Any) -> None:
    global _sdkUsers
    with _sdkLock:
        _sdkUsers -= 1
        if _sdkUsers == 0:
            _require(sdk.MvCamera.MV_CC_Finalize(), "MV_CC_Finalize")


def _deviceText(value: Any) -> str:
    raw = bytes(value).split(b"\0", 1)[0]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


# Match the reported manufacturer, not a model prefix or user-editable device ID.
_HIK_MANUFACTURERS = {
    "hikrobot",
    "hikvision",
    "hangzhouhikrobotcoltd",
    "hangzhouhikrobottechnologycoltd",
    "hangzhouhikvisiondigitaltechnologycoltd",
    "海康机器人",
    "海康威视",
    "杭州海康机器人股份有限公司",
    "杭州海康机器人技术有限公司",
    "杭州海康威视数字技术股份有限公司",
}


def _enumerateDevices(sdk: Any, serialNumber: str | None = None) -> dict[str, tuple[dict[str, str], Any]]:
    devices = sdk.MV_CC_DEVICE_INFO_LIST()
    transport = sdk.MV_GIGE_DEVICE | sdk.MV_USB_DEVICE
    matches = {}
    # MVS invalidates the previous list on enumeration. Copy each match before
    # releasing the lock so another camera can be opened concurrently.
    with _sdkLock:
        _require(
            sdk.MvCamera.MV_CC_EnumDevices(transport, devices), "MV_CC_EnumDevices", "E_CAMERA_OPEN_FAILED"
        )
        for i in range(devices.nDeviceNum):
            device = devices.pDeviceInfo[i].contents
            # MV_GIGE_DEVICE also enumerates physical cameras behind GenTL NICs.
            if device.nTLayerType in (sdk.MV_GIGE_DEVICE, getattr(sdk, "MV_GENTL_GIGE_DEVICE", 0x40)):
                details = device.SpecialInfo.stGigEInfo
                transportName = "GigE"
                ip = str(IPv4Address(int(details.nCurrentIp)))
            elif device.nTLayerType == sdk.MV_USB_DEVICE:
                details = device.SpecialInfo.stUsb3VInfo
                transportName = "USB3"
                ip = ""
            else:
                continue
            manufacturer = _deviceText(details.chManufacturerName).strip()
            if not manufacturer and transportName == "USB3":
                manufacturer = _deviceText(details.chVendorName).strip()
            normalized = "".join(ch for ch in manufacturer.casefold() if ch.isalnum())
            if normalized not in _HIK_MANUFACTURERS:
                continue
            serial = _deviceText(details.chSerialNumber)
            if not serial or (serialNumber is not None and serial != serialNumber):
                continue
            if serial in matches:
                raise MvsError(
                    "E_CAMERA_OPEN_FAILED", f"Multiple MVS devices have serial number {serial!r}"
                )
            summary = dict(
                serialNumber=serial,
                modelName=_deviceText(details.chModelName),
                manufacturerName=manufacturer,
                transport=transportName,
                ipAddress=ip,
            )
            matches[serial] = summary, sdk.MV_CC_DEVICE_INFO.from_buffer_copy(device)
    return matches


def discoverMvsDevices() -> list[dict[str, str]]:
    sdk = _acquireSdk()
    try:
        return [summary for summary, _ in _enumerateDevices(sdk).values()]
    finally:
        _releaseSdk(sdk)


def _findDevice(sdk: Any, serialNumber: str) -> Any:
    matches = _enumerateDevices(sdk, serialNumber)
    if serialNumber in matches:
        return matches[serialNumber][1]
    raise MvsError(
        "E_CAMERA_OPEN_FAILED", f"No Hikrobot GigE or USB3 camera with serial number {serialNumber!r}"
    )


def _outputShape(width: int, height: int, color: str, capacity: int | None) -> tuple[int, ...]:
    shape = (height, width) if color == "gray" else (height, width, 3)
    size = math.prod(shape)
    if min(width, height) <= 0 or size > 0xFFFFFFFF:
        raise MvsError("E_CAMERA_FRAME_INVALID", f"Invalid MVS frame dimensions {width} x {height}")
    if capacity is not None and size > capacity:
        raise MvsError("E_FRAME_CAPACITY", f"Image requires {size} bytes, reserved capacity is {capacity}")
    return shape


class MvsSession:
    def __init__(self, serialNumber: str):
        self.serialNumber = serialNumber
        self._sdk: Any = None
        self._camera: Any = None
        self._opened = False
        self._grabbing = False
        self._previousStream = False
        self._width = self._height = 0
        self._offsetX = self._offsetY = 0

    def connect(self) -> None:
        if self._opened:
            return
        self._sdk = _acquireSdk()
        try:
            device = _findDevice(self._sdk, self.serialNumber)
            camera = self._sdk.MvCamera()
            _require(camera.MV_CC_CreateHandle(device), "MV_CC_CreateHandle", "E_CAMERA_OPEN_FAILED")
            self._camera = camera
            _require(
                camera.MV_CC_OpenDevice(self._sdk.MV_ACCESS_Exclusive, 0),
                "MV_CC_OpenDevice",
                "E_CAMERA_OPEN_FAILED",
            )
            self._opened = True
        except Exception:
            self.close()
            raise

    def _intValue(self, name: str) -> int:
        value = self._sdk.MVCC_INTVALUE_EX()
        _require(self._camera.MV_CC_GetIntValueEx(name, value), name, "E_CAMERA_CONFIG_FAILED")
        return int(value.nCurValue)

    def _setEnum(self, name: str, value: str) -> None:
        _require(
            self._camera.MV_CC_SetEnumValueByString(name, value),
            f"{name}={value}",
            "E_CAMERA_CONFIG_FAILED",
        )

    def _setOptionalEnum(self, name: str, value: str, *, requireAvailable: bool = False) -> None:
        # A fixed capability (e.g. manual exposure only) has no writable selector.
        access = self._sdk.MV_XML_AccessMode()
        result = self._camera.MV_XML_GetNodeAccessMode(name, access)
        if result == self._sdk.MV_E_GC_NODE_NOT_FOUND:
            return
        _require(result, f"check access to {name}", "E_CAMERA_CONFIG_FAILED")
        if access.value in (self._sdk.AM_RW, self._sdk.AM_WO):
            self._setEnum(name, value)
        elif access.value == self._sdk.AM_RO:
            current = self._sdk.MVCC_ENUMVALUE()
            _require(self._camera.MV_CC_GetEnumValue(name, current), name, "E_CAMERA_CONFIG_FAILED")
            entry = self._sdk.MVCC_ENUMENTRY()
            entry.nValue = current.nCurValue
            _require(self._camera.MV_CC_GetEnumEntrySymbolic(name, entry), name, "E_CAMERA_CONFIG_FAILED")
            actual = _deviceText(entry.chSymbolic)
            if actual != value:
                raise MvsError("E_CAMERA_CONFIG_FAILED", f"Read-only {name} is {actual}, requires {value}")
        elif requireAvailable and access.value != self._sdk.AM_NI:
            raise MvsError("E_CAMERA_CONFIG_FAILED", f"Cannot verify {name}={value} (node unavailable)")

    def _exposureValue(self) -> float:
        value = self._sdk.MVCC_FLOATVALUE()
        _require(self._camera.MV_CC_GetFloatValue("ExposureTime", value), "ExposureTime")
        return float(value.fCurValue)

    def start(self, params: Mapping[str, Any]) -> None:
        if not self._opened or self._grabbing:
            raise MvsError(
                "E_CAMERA_IO", "MVS camera must be open and stopped before configuring acquisition"
            )
        camera = self._camera
        mode = params["triggerMode"]
        # TriggerMode selects how each frame starts. AcquisitionMode controls how
        # many frames the camera can deliver after a single StartGrabbing.
        self._setOptionalEnum("AcquisitionMode", "Continuous", requireAvailable=True)
        if params.get("exposureMode", "keep") == "manual":
            self._setOptionalEnum("ExposureAuto", "Off")
            _require(
                camera.MV_CC_SetFloatValue("ExposureTime", params["exposureUs"]),
                "ExposureTime",
                "E_CAMERA_CONFIG_FAILED",
            )
        if params.get("gainMode", "keep") == "manual":
            self._setOptionalEnum("GainAuto", "Off")
            _require(camera.MV_CC_SetFloatValue("Gain", params["gainDb"]), "Gain", "E_CAMERA_CONFIG_FAILED")
        # Some cameras have only one frame trigger. Models with a writable
        # selector may still have an acquisition/burst selector from MVS.
        self._setOptionalEnum("TriggerSelector", "FrameStart")
        self._setEnum("TriggerMode", "Off" if mode == "freeRun" else "On")
        if mode != "freeRun":
            self._setEnum("TriggerSource", "Software" if mode == "software" else params["triggerSource"])
            if mode == "hardware":
                self._setEnum("TriggerActivation", params["triggerActivation"])
        self._width, self._height = self._intValue("Width"), self._intValue("Height")
        self._offsetX, self._offsetY = self._intValue("OffsetX"), self._intValue("OffsetY")
        strategy = (
            self._sdk.MV_GrabStrategy_LatestImagesOnly
            if mode == "freeRun"
            else self._sdk.MV_GrabStrategy_OneByOne
        )
        _require(camera.MV_CC_SetGrabStrategy(strategy), "MV_CC_SetGrabStrategy", "E_CAMERA_CONFIG_FAILED")
        _require(camera.MV_CC_StartGrabbing(), "MV_CC_StartGrabbing")
        self._grabbing = True
        if self._previousStream:
            _require(camera.MV_CC_ClearImageBuffer(), "MV_CC_ClearImageBuffer")
        self._previousStream = True

    def stop(self) -> None:
        if self._grabbing:
            _require(self._camera.MV_CC_StopGrabbing(), "MV_CC_StopGrabbing")
            self._grabbing = False

    def capture(
        self,
        params: Mapping[str, Any],
        check: Callable[[], object],
        capacity: int | None,
        timeoutMs: int | None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if not self._grabbing:
            raise MvsError("E_CAMERA_IO", "MVS camera is not grabbing")
        check()
        color = params["outputColor"]
        _outputShape(self._width, self._height, color, capacity)
        deadline = None if timeoutMs is None else monotonic() + timeoutMs / 1000
        frame = self._sdk.MV_FRAME_OUT()
        software = params["triggerMode"] == "software"
        received = False
        try:
            if software:
                _require(self._camera.MV_CC_SetCommandValue("TriggerSoftware"), "TriggerSoftware")
            while True:
                check()
                remainingMs = 100 if deadline is None else math.ceil((deadline - monotonic()) * 1000)
                if remainingMs <= 0:
                    raise MvsError("E_CAMERA_TIMEOUT", f"MVS frame timed out after {timeoutMs} ms")
                result = self._camera.MV_CC_GetImageBuffer(frame, min(100, remainingMs))
                if result != self._sdk.MV_E_NODATA:
                    _require(result, "MV_CC_GetImageBuffer")
                    break
            received = True
            try:
                check()
                return self._convert(frame, color, capacity)
            finally:
                _require(self._camera.MV_CC_FreeImageBuffer(frame), "MV_CC_FreeImageBuffer")
        except Exception as err:
            if software:
                setattr(err, "softwareTriggerIssued", True)
            if received:
                setattr(err, "frameReceived", True)
            raise

    def _convert(self, frame: Any, color: str, capacity: int | None) -> tuple[np.ndarray, dict[str, Any]]:
        info = frame.stFrameInfo
        width = int(info.nExtendWidth or info.nWidth)
        height = int(info.nExtendHeight or info.nHeight)
        size = int(info.nFrameLenEx or info.nFrameLen)
        if not frame.pBufAddr or not 0 < size <= 0xFFFFFFFF or info.nLostPacket:
            raise MvsError("E_CAMERA_FRAME_INVALID", "MVS returned an incomplete image")
        shape = _outputShape(width, height, color, capacity)
        image = np.empty(shape, dtype=np.uint8)
        conversion = self._sdk.MV_CC_PIXEL_CONVERT_PARAM_EX()
        conversion.nWidth, conversion.nHeight = width, height
        conversion.pSrcData, conversion.nSrcDataLen = frame.pBufAddr, size
        conversion.enSrcPixelType = info.enPixelType
        conversion.enDstPixelType = (
            self._sdk.PixelType_Gvsp_Mono8 if color == "gray" else self._sdk.PixelType_Gvsp_BGR8_Packed
        )
        conversion.pDstBuffer = image.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte))
        conversion.nDstBufferSize = image.nbytes
        _require(
            self._camera.MV_CC_ConvertPixelTypeEx(conversion),
            "MV_CC_ConvertPixelTypeEx",
            "E_CAMERA_FRAME_INVALID",
        )
        if conversion.nDstLen != image.nbytes:
            raise MvsError(
                "E_CAMERA_FRAME_INVALID",
                f"MVS conversion produced {conversion.nDstLen} of {image.nbytes} bytes",
            )
        exposure = float(info.fExposureTime)
        hasFrameExposure = math.isfinite(exposure) and exposure > 0
        return image, {
            "blockId": int(info.nFrameNum),
            "deviceTimestamp": (int(info.nDevTimeStampHigh) << 32) | int(info.nDevTimeStampLow),
            "actualExposureUs": exposure if hasFrameExposure else self._exposureValue(),
            "frameExposureUs": exposure if hasFrameExposure else None,
            "exposureEvidence": "frame_metadata"
            if hasFrameExposure
            else "configuration_readback_not_frame_metadata",
            "offsetX": int(info.nOffsetX) or self._offsetX,
            "offsetY": int(info.nOffsetY) or self._offsetY,
        }

    def close(self) -> None:
        if self._sdk is None:
            return
        try:
            with ExitStack() as cleanup:
                cleanup.callback(_releaseSdk, self._sdk)
                if self._camera is not None:
                    cleanup.callback(
                        lambda: _require(self._camera.MV_CC_DestroyHandle(), "MV_CC_DestroyHandle")
                    )
                if self._opened:
                    cleanup.callback(
                        lambda: _require(self._camera.MV_CC_CloseDevice(), "MV_CC_CloseDevice")
                    )
                self.stop()
        finally:
            self._camera = self._sdk = None
            self._opened = self._grabbing = self._previousStream = False
