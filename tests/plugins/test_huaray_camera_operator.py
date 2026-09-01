from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from emo_master.plugins.builtins._huaray_imv import (
    HuarayCameraError,
    IMV_CREATE_BY_CAMERA_KEY,
    IMV_CREATE_BY_INDEX,
    IMV_CREATE_BY_IP_ADDRESS,
    IMV_CREATE_BY_USER_ID,
    IMV_GRAB_LATEST_IMAGE,
    IMV_GRAB_SEQUENTIAL,
    IMV_OK,
    IMV_TIMEOUT,
    resetImvApiForTests,
)
from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator


class _FakeImvApi:
    def __init__(self) -> None:
        self.enumCount = 1
        self.enumerateCalls: list[int] = []
        self.createCalls: list[tuple[int, str | int]] = []
        self.openCount = 0
        self.startStrategies: list[int] = []
        self.stopCount = 0
        self.closeCount = 0
        self.destroyCount = 0
        self.releaseCount = 0
        self.commands: list[str] = []
        self.convertCalls: list[tuple[str, str]] = []
        self.getFrameResults: list[int] = []
        self.alwaysTimeout = False
        self.releaseResult = IMV_OK
        self.createResult = IMV_OK
        self.openResult = IMV_OK
        self.startResult = IMV_OK
        self.stopResult = IMV_OK
        self.closeResult = IMV_OK
        self.destroyResult = IMV_OK
        self.invalidFrame = False
        self.failFeature: str | None = None
        self.serialNumber = "SN-FAKE-001"
        self.enums = {
            "PixelFormat": "Mono8",
            "ExposureAuto": "Continuous",
            "GainAuto": "Continuous",
            "TriggerMode": "Off",
            "TriggerSelector": "FrameStart",
            "TriggerSource": "Line1",
            "TriggerActivation": "RisingEdge",
        }
        self.doubles = {
            "ExposureTime": 8000.0,
            "GainRaw": 0.0,
            "AcquisitionFrameRate": 30.0,
        }
        self.doubleRanges = {
            "ExposureTime": (10.0, 1_000_000.0),
            "GainRaw": (0.0, 24.0),
            "AcquisitionFrameRate": (0.1, 120.0),
        }
        self.ints = {"Width": 4, "Height": 3, "OffsetX": 2, "OffsetY": 1}
        self.intRanges = {
            "Width": (2, 8, 2),
            "Height": (2, 6, 1),
            "OffsetX": (0, 100, 1),
            "OffsetY": (0, 100, 1),
        }
        self.bools = {"AcquisitionFrameRateEnable": False}
        self.blockId = 40

    def version(self) -> str:
        return "2.5.1.fake"

    def enumerate_devices(self, interfaceType: int = 0) -> tuple[int, int]:
        self.enumerateCalls.append(interfaceType)
        return IMV_OK, self.enumCount

    def create_handle(self, mode: int, identifier: str | int) -> tuple[int, object]:
        self.createCalls.append((mode, identifier))
        return self.createResult, object()

    def destroy_handle(self, handle: object) -> int:
        _ = handle
        self.destroyCount += 1
        return self.destroyResult

    def open(self, handle: object) -> int:
        _ = handle
        self.openCount += 1
        return self.openResult

    def close(self, handle: object) -> int:
        _ = handle
        self.closeCount += 1
        return self.closeResult

    def start_grabbing_ex(self, handle: object, strategy: int) -> int:
        _ = handle
        self.startStrategies.append(strategy)
        return self.startResult

    def stop_grabbing(self, handle: object) -> int:
        _ = handle
        self.stopCount += 1
        return self.stopResult

    def get_frame(self, handle: object, timeoutMs: int) -> tuple[int, Any]:
        _ = handle
        _ = timeoutMs
        if self.alwaysTimeout:
            return IMV_TIMEOUT, None
        result = self.getFrameResults.pop(0) if self.getFrameResults else IMV_OK
        if result != IMV_OK:
            return result, None
        self.blockId += 1
        width = self.ints["Width"]
        height = self.ints["Height"]
        info = SimpleNamespace(
            status=7 if self.invalidFrame else 0,
            width=width,
            height=height,
            size=width * height,
            blockId=self.blockId,
            timeStamp=123456789 + self.blockId,
        )
        return IMV_OK, SimpleNamespace(frameInfo=info, pData=object())

    def release_frame(self, handle: object, frame: object) -> int:
        _ = handle
        _ = frame
        self.releaseCount += 1
        return self.releaseResult

    def set_enum(self, handle: object, name: str, value: str) -> int:
        _ = handle
        if self.failFeature == name:
            return -9
        self.enums[name] = value
        return IMV_OK

    def get_enum(self, handle: object, name: str) -> tuple[int, str]:
        _ = handle
        return IMV_OK, self.enums[name]

    def set_double(self, handle: object, name: str, value: float) -> int:
        _ = handle
        if self.failFeature == name:
            return -9
        self.doubles[name] = value
        return IMV_OK

    def get_double(self, handle: object, name: str) -> tuple[int, float]:
        _ = handle
        return IMV_OK, self.doubles[name]

    def get_double_range(self, handle: object, name: str) -> tuple[int, float, float]:
        _ = handle
        minimum, maximum = self.doubleRanges[name]
        return IMV_OK, minimum, maximum

    def set_int(self, handle: object, name: str, value: int) -> int:
        _ = handle
        if self.failFeature == name:
            return -9
        self.ints[name] = value
        return IMV_OK

    def get_int(self, handle: object, name: str) -> tuple[int, int]:
        _ = handle
        return IMV_OK, self.ints[name]

    def get_int_range(self, handle: object, name: str) -> tuple[int, int, int, int]:
        _ = handle
        minimum, maximum, increment = self.intRanges[name]
        return IMV_OK, minimum, maximum, increment

    def set_bool(self, handle: object, name: str, value: bool) -> int:
        _ = handle
        if self.failFeature == name:
            return -9
        self.bools[name] = value
        return IMV_OK

    def get_bool(self, handle: object, name: str) -> tuple[int, bool]:
        _ = handle
        return IMV_OK, self.bools[name]

    def get_string(self, handle: object, name: str) -> tuple[int, str]:
        _ = handle
        _ = name
        return IMV_OK, self.serialNumber

    def execute_command(self, handle: object, name: str) -> int:
        _ = handle
        self.commands.append(name)
        return IMV_OK

    def convert_frame(
        self,
        handle: object,
        frame: object,
        outputColor: str,
        demosaic: str,
    ) -> np.ndarray[Any, Any]:
        _ = handle
        self.convertCalls.append((outputColor, demosaic))
        info = frame.frameInfo
        shape = (info.height, info.width) if outputColor == "gray" else (info.height, info.width, 3)
        return np.full(shape, 17, dtype=np.uint8)


@pytest.fixture(autouse=True)
def _resetGlobalImvState():
    resetImvApiForTests()
    yield
    resetImvApiForTests()


def _params(**overrides: object) -> dict[str, object]:
    return {
        "selectionMode": "ip",
        "ipAddress": "192.168.1.10",
        "retryCount": 0,
        **overrides,
    }


def testCameraReusesSessionAndOutputsTypedFrame() -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)

    first = operator.executeNode({}, _params(), {"nodeId": "camera"})
    second = operator.executeNode({}, _params(), {"nodeId": "camera"})

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert api.openCount == 1
    assert api.startStrategies == [IMV_GRAB_SEQUENTIAL]
    outputs = first["outputs"]
    assert outputs["image"].dtype == np.uint8
    assert outputs["image"].shape == (3, 4, 3)
    assert isinstance(outputs["blockId"], int)
    assert isinstance(outputs["deviceTimestamp"], int)
    assert outputs["actualExposureUs"] == 8000.0
    assert outputs["frame"]["schemaVersion"] == "1.1"
    assert outputs["frame"]["coordinateSpace"]["sourceId"] == "huaray-imv:SN-FAKE-001"
    assert outputs["frame"]["coordinateSpace"]["transformToSource"] == [1.0, 0.0, 0.0, 1.0, 2.0, 1.0]

    operator.disposeOperator()
    assert (api.stopCount, api.closeCount, api.destroyCount) == (1, 1, 1)


@pytest.mark.parametrize(
    ("params", "expectedMode", "expectedIdentifier"),
    [
        (_params(), IMV_CREATE_BY_IP_ADDRESS, "192.168.1.10"),
        (_params(selectionMode="cameraKey", cameraKey="vendor:serial"), IMV_CREATE_BY_CAMERA_KEY, "vendor:serial"),
        (_params(selectionMode="userId", userId="inspection-cam"), IMV_CREATE_BY_USER_ID, "inspection-cam"),
        (_params(selectionMode="index", deviceIndex=0), IMV_CREATE_BY_INDEX, 0),
    ],
)
def testCameraSupportsAllSelectionModes(
    params: dict[str, object], expectedMode: int, expectedIdentifier: str | int
) -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode({}, params, {})

    assert result["status"] == "ok"
    assert api.createCalls == [(expectedMode, expectedIdentifier)]
    assert bool(api.enumerateCalls) is (expectedMode == IMV_CREATE_BY_INDEX)
    operator.disposeOperator()


@pytest.mark.parametrize(
    ("triggerMode", "expectedStrategy"),
    [
        ("hardware", IMV_GRAB_SEQUENTIAL),
        ("software", IMV_GRAB_SEQUENTIAL),
        ("freeRun", IMV_GRAB_LATEST_IMAGE),
    ],
)
def testCameraConfiguresTriggerAndGrabStrategy(triggerMode: str, expectedStrategy: int) -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode(
        {},
        _params(
            triggerMode=triggerMode,
            triggerSource="Line3",
            triggerActivation="FallingEdge",
        ),
        {},
    )

    assert result["status"] == "ok"
    assert api.startStrategies == [expectedStrategy]
    if triggerMode == "hardware":
        assert api.enums["TriggerSource"] == "Line3"
        assert api.enums["TriggerActivation"] == "FallingEdge"
        assert api.enums["TriggerMode"] == "On"
    elif triggerMode == "software":
        assert api.enums["TriggerSource"] == "Software"
        assert api.commands == ["TriggerSoftware"]
    else:
        assert api.enums["TriggerMode"] == "Off"
        assert api.commands == []
    operator.disposeOperator()


@pytest.mark.parametrize("demosaic", ["nearest", "bilinear", "edgeSensing"])
def testCameraSupportsGrayAndEveryBayerAlgorithm(demosaic: str) -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode({}, _params(outputColor="gray", demosaic=demosaic), {})

    assert result["status"] == "ok"
    assert result["outputs"]["image"].shape == (3, 4)
    assert api.convertCalls == [("gray", demosaic)]
    operator.disposeOperator()


def testCameraAppliesExplicitAdvancedConfigurationAndRoi() -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)
    params = _params(
        exposureMode="manual",
        exposureUs=12000.0,
        gainMode="manual",
        gainRaw=1.5,
        frameRateMode="manual",
        frameRate=25.0,
        roiMode="custom",
        width=6,
        height=4,
        offsetX=4,
        offsetY=3,
        pixelFormat="BayerRG8",
    )

    result = operator.executeNode({}, params, {})

    assert result["status"] == "ok"
    assert api.enums["PixelFormat"] == "BayerRG8"
    assert api.enums["ExposureAuto"] == "Off"
    assert api.enums["GainAuto"] == "Off"
    assert api.doubles["ExposureTime"] == 12000.0
    assert api.doubles["GainRaw"] == 1.5
    assert api.doubles["AcquisitionFrameRate"] == 25.0
    assert api.bools["AcquisitionFrameRateEnable"] is True
    assert api.ints == {"Width": 6, "Height": 4, "OffsetX": 4, "OffsetY": 3}
    assert result["outputs"]["image"].shape == (4, 6, 3)
    assert result["outputs"]["frame"]["coordinateSpace"]["transformToSource"][-2:] == [4.0, 3.0]
    operator.disposeOperator()


def testCameraFullRoiUsesDeviceMaximumAndZeroOffset() -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode({}, _params(roiMode="full"), {})

    assert result["status"] == "ok"
    assert api.ints == {"Width": 8, "Height": 6, "OffsetX": 0, "OffsetY": 0}
    assert result["outputs"]["image"].shape == (6, 8, 3)
    operator.disposeOperator()


def testCameraRejectsUnsupportedExplicitFeature() -> None:
    api = _FakeImvApi()
    api.failFeature = "GainRaw"
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode(
        {}, _params(gainMode="manual", gainRaw=2.0), {}
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_CAMERA_CONFIG_FAILED"


def testCameraIndexOutOfRangeIsDeviceNotFound() -> None:
    api = _FakeImvApi()
    api.enumCount = 0
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode(
        {}, _params(selectionMode="index", deviceIndex=0), {}
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_CAMERA_DEVICE_NOT_FOUND"
    assert api.createCalls == []


def testCameraOpenFailureDestroysCreatedHandle() -> None:
    api = _FakeImvApi()
    api.openResult = -44
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode({}, _params(), {})

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_CAMERA_OPEN_FAILED"
    assert api.destroyCount == 1


def testCameraRejectsDeviceRangeAndIncrementMismatch() -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode(
        {}, _params(roiMode="custom", width=5, height=3, offsetX=0, offsetY=0), {}
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_CAMERA_CONFIG_FAILED"
    assert "increment" in result["error"]["message"]


def testCameraTimeoutDoesNotReconnect() -> None:
    api = _FakeImvApi()
    api.alwaysTimeout = True
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode(
        {}, _params(captureTimeoutMs=1, retryCount=3, retryDelayMs=0), {}
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_CAMERA_TIMEOUT"
    assert api.openCount == 1
    operator.disposeOperator()


def testCameraReconnectsAfterTransportError() -> None:
    api = _FakeImvApi()
    api.getFrameResults = [-77, IMV_OK]
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode(
        {}, _params(retryCount=1, retryDelayMs=0), {}
    )

    assert result["status"] == "ok"
    assert result["metrics"]["captureAttempts"] == 2
    assert api.openCount == 2
    assert api.destroyCount == 1
    operator.disposeOperator()


def testInvalidFrameIsReleasedBeforeError() -> None:
    api = _FakeImvApi()
    api.invalidFrame = True
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode({}, _params(), {})

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_CAMERA_FRAME_INVALID"
    assert api.releaseCount == 1
    operator.disposeOperator()


def testReleaseFailureBecomesCameraIoError() -> None:
    api = _FakeImvApi()
    api.releaseResult = -88
    operator = HuarayCameraOperator(lambda: api)

    result = operator.executeNode({}, _params(), {})

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_CAMERA_IO"
    operator.disposeOperator()


def testDisposeReportsCleanupFailure() -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)
    assert operator.executeNode({}, _params(), {})["status"] == "ok"
    api.destroyResult = -99

    with pytest.raises(Exception) as error:
        operator.disposeOperator()

    assert getattr(error.value, "code", "") == "E_RESOURCE_CLEANUP_FAILED"


def testSameSelectorCannotBeOpenedByTwoLiveNodes() -> None:
    api = _FakeImvApi()
    first = HuarayCameraOperator(lambda: api)
    second = HuarayCameraOperator(lambda: api)
    assert first.executeNode({}, _params(), {})["status"] == "ok"

    blocked = second.executeNode({}, _params(), {})

    assert blocked["status"] == "error"
    assert blocked["error"]["code"] == "E_CAMERA_OPEN_FAILED"
    first.disposeOperator()
    assert second.executeNode({}, _params(), {})["status"] == "ok"
    second.disposeOperator()


def testChangingConversionOnlyDoesNotReconnectButConfigurationDoes() -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)
    assert operator.executeNode({}, _params(outputColor="bgr"), {})["status"] == "ok"
    assert operator.executeNode({}, _params(outputColor="gray"), {})["status"] == "ok"
    assert api.openCount == 1

    changed = operator.executeNode(
        {}, _params(outputColor="gray", triggerMode="freeRun"), {}
    )

    assert changed["status"] == "ok"
    assert api.openCount == 2
    operator.disposeOperator()


def testSegmentedWaitObservesCancellation() -> None:
    api = _FakeImvApi()
    api.alwaysTimeout = True
    operator = HuarayCameraOperator(lambda: api)
    checks = 0

    class _Cancelled(RuntimeError):
        code = "E_CANCELLED"

    def cancelAfterFirstChunk() -> None:
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise _Cancelled("cancelled")

    with pytest.raises(_Cancelled):
        operator.executeNode(
            {},
            _params(captureTimeoutMs=1000),
            {"raiseIfCancellationRequested": cancelAfterFirstChunk},
        )
    operator.disposeOperator()


def testCameraCancellationErrorDoesNotEnterRetryFlow() -> None:
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)

    def cancelWithCameraError() -> None:
        raise HuarayCameraError("E_CANCELLED", "cancelled")

    with pytest.raises(HuarayCameraError) as error:
        operator.executeNode(
            {},
            _params(captureTimeoutMs=1000, retryCount=3, retryDelayMs=0),
            {"raiseIfCancellationRequested": cancelWithCameraError},
        )

    assert error.value.code == "E_CANCELLED"
    assert api.openCount == 1
    assert api.destroyCount == 0
    operator.disposeOperator()
    assert api.destroyCount == 1


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({}, "ipAddress"),
        (_params(ipAddress="999.1.1.1"), "ipAddress"),
        (_params(selectionMode="index", deviceIndex=True), "deviceIndex"),
        (_params(captureTimeoutMs=0), "captureTimeoutMs"),
        (_params(exposureMode="manual", exposureUs=float("inf")), "exposureUs"),
        (_params(roiMode="custom", width=0), "width"),
        (_params(unknown=True), "unknown camera parameters"),
    ],
)
def testCameraStrictParameterValidation(params: dict[str, object], message: str) -> None:
    error = HuarayCameraOperator().validateParams(params)

    assert error is not None
    assert error["code"] == "E_PARAM_INVALID"
    assert message in error["message"]
