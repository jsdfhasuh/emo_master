"""Exercise the public MVS adapter using the vendor API shape, without devices."""

from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from emo_master.plugins.builtins import _hikrobot_mvs as mvs


class Identity(ctypes.Structure):
    _fields_ = [
        ("chSerialNumber", ctypes.c_char * 16),
        ("chModelName", ctypes.c_char * 16),
        ("nCurrentIp", ctypes.c_uint32),
        ("chManufacturerName", ctypes.c_char * 64),
        ("chVendorName", ctypes.c_char * 64),
    ]


class Device(ctypes.Structure):
    class Info(ctypes.Union):
        _fields_ = [("stGigEInfo", Identity), ("stUsb3VInfo", Identity)]

    _fields_ = [("nTLayerType", ctypes.c_uint), ("SpecialInfo", Info)]


@pytest.fixture
def sdk(monkeypatch):
    calls = []
    devices = [
        Device(1, Device.Info(Identity(b"GIGE", b"MV-G", 0xC0A8050A, b"HIKROBOT"))),
        Device(4, Device.Info(Identity(b"USB", b"MV-U", 0, b"Hikvision"))),
    ]
    frameInfo = NS(
        nWidth=2,
        nHeight=2,
        nExtendWidth=0,
        nExtendHeight=0,
        nFrameLen=4,
        nFrameLenEx=0,
        nLostPacket=0,
        enPixelType=1,
        nFrameNum=27,
        nDevTimeStampHigh=1,
        nDevTimeStampLow=9,
        nOffsetX=0,
        nOffsetY=0,
        fExposureTime=0.0,
    )
    pixels = (ctypes.c_ubyte * 4)(1, 2, 3, 4)
    outcomes = []
    failures = {}
    accessModes = {}
    enumSymbols = {"AcquisitionMode": b"SingleFrame", "TriggerSelector": b"FrameBurstStart"}
    triggerSelectors = {3: b"FrameStart", 6: b"FrameBurstStart"}
    intValues = {"Width": 2, "Height": 2, "OffsetX": 6, "OffsetY": 8, "AcquisitionBurstFrameCount": 1}

    def call(name, *args):
        calls.append((name, *args))
        return failures.get(name, 0)

    class Camera:
        MV_CC_Initialize = staticmethod(lambda: call("initialize"))
        MV_CC_Finalize = staticmethod(lambda: call("finalize"))

        @staticmethod
        def MV_CC_EnumDevices(mask, out):
            assert mask == 5
            out.nDeviceNum = len(devices)
            out.pDeviceInfo = [ctypes.pointer(device) for device in devices]
            return call("enumerate")

        def MV_CC_CreateHandle(self, device):
            return call("create", device.nTLayerType)

        def MV_CC_OpenDevice(self, access, key):
            return call("open", access, key)

        def MV_CC_SetEnumValueByString(self, name, value):
            if name == "TriggerSelector" and value.encode("ascii") not in triggerSelectors.values():
                return 0x80000004
            enumSymbols[name] = value.encode("ascii")
            return call(name, value)

        def MV_XML_GetNodeAccessMode(self, name, out):
            out.value = accessModes.get(name, 4)
            return failures.get(f"{name} access", 0)

        def MV_CC_GetEnumValue(self, name, out):
            out.nCurValue = 0
            if name == "TriggerSelector":
                out.nSupportValue = list(triggerSelectors)
                out.nSupportedNum = len(triggerSelectors)
                out.nCurValue = next(key for key, symbol in triggerSelectors.items() if symbol == enumSymbols[name])
            return failures.get(name + " read", 0)

        def MV_CC_GetEnumEntrySymbolic(self, name, out):
            out.chSymbolic = triggerSelectors[out.nValue] if name == "TriggerSelector" else enumSymbols.get(name, b"Off")
            return 0

        def MV_CC_GetIntValueEx(self, name, output):
            output.nCurValue = intValues[name]
            return failures.get(name + " read", 0)

        def MV_CC_SetIntValueEx(self, name, value):
            status = call(name, value)
            if not status:
                intValues[name] = value
            return status

        def MV_CC_GetFloatValue(self, name, output):
            output.fCurValue = 1001.5
            return call("read", name)

        def MV_CC_SetFloatValue(self, name, value):
            return call(name, value)

        def MV_CC_SetGrabStrategy(self, strategy):
            return call("strategy", strategy)

        def MV_CC_StartGrabbing(self):
            self.remaining = 1 if enumSymbols["AcquisitionMode"] == b"SingleFrame" else None
            return call("start")

        def MV_CC_ClearImageBuffer(self):
            return call("clear")

        def MV_CC_SetCommandValue(self, name):
            return call(name)

        def MV_CC_GetImageBuffer(self, frame, wait):
            call("get", wait)
            if self.remaining == 0:
                return 7
            frame.stFrameInfo = frameInfo
            frame.pBufAddr = ctypes.cast(pixels, ctypes.POINTER(ctypes.c_ubyte))
            status = outcomes.pop(0) if outcomes else 0
            if self.remaining is not None and status == 0:
                self.remaining -= 1
            return status

        def MV_CC_ConvertPixelTypeEx(self, output):
            call("convert", output.enDstPixelType)
            for i in range(output.nDstBufferSize):
                output.pDstBuffer[i] = output.pSrcData[i % 4]
            output.nDstLen = output.nDstBufferSize
            return failures.get("convert", 0)

        def MV_CC_FreeImageBuffer(self, frame):
            frame.pBufAddr[0] = 255
            return call("free")

        def MV_CC_StopGrabbing(self):
            return call("stop")

        def MV_CC_CloseDevice(self):
            return call("close")

        def MV_CC_DestroyHandle(self):
            return call("destroy")

    fake = NS(
        MvCamera=Camera,
        MV_CC_DEVICE_INFO_LIST=NS,
        MV_CC_DEVICE_INFO=Device,
        MV_GIGE_DEVICE=1,
        MV_GENTL_GIGE_DEVICE=0x40,
        MV_USB_DEVICE=4,
        MV_ACCESS_Exclusive=1,
        MVCC_INTVALUE_EX=NS,
        MVCC_FLOATVALUE=NS,
        MVCC_ENUMVALUE=NS,
        MVCC_ENUMENTRY=NS,
        MV_GrabStrategy_LatestImagesOnly=1,
        MV_GrabStrategy_OneByOne=0,
        MV_FRAME_OUT=NS,
        MV_E_NODATA=7,
        MV_CC_PIXEL_CONVERT_PARAM_EX=NS,
        PixelType_Gvsp_BGR8_Packed=3,
        PixelType_Gvsp_Mono8=1,
        MV_XML_AccessMode=ctypes.c_int,
        AM_RW=4,
        AM_WO=2,
        AM_RO=3,
        AM_NI=0,
        MV_E_GC_NODE_NOT_FOUND=265,
        calls=calls,
        failures=failures,
        outcomes=outcomes,
        frameInfo=frameInfo,
        devices=devices,
        accessModes=accessModes,
        enumSymbols=enumSymbols,
        triggerSelectors=triggerSelectors,
        intValues=intValues,
    )
    monkeypatch.setattr(mvs, "_loadBindings", lambda: fake)
    assert mvs._sdkUsers == 0
    yield fake
    assert mvs._sdkUsers == 0


@pytest.fixture
def session(sdk):
    camera = mvs.MvsSession("USB")
    camera.connect()
    try:
        yield camera
    finally:
        camera.close()


@pytest.mark.parametrize(("mode", "color"), [("software", "gray"), ("hardware", "bgr"), ("freeRun", "bgr")])
def test_configure_capture_and_own_pixels(sdk, session, mode, color):
    params = dict(
        triggerMode=mode,
        triggerSource="Line2",
        triggerActivation="FallingEdge",
        outputColor=color,
        exposureMode="manual",
        exposureUs=1001.5,
        gainMode="manual",
        gainDb=3.5,
    )
    session.start(params)
    sdk.outcomes.extend([7, 0])
    image, meta = session.capture(params, lambda: None, 16, 300)
    assert image.flags.owndata and image.dtype == np.uint8 and image.flat[0] == 1
    assert image.shape == ((2, 2) if color == "gray" else (2, 2, 3))
    assert meta == dict(
        blockId=27,
        deviceTimestamp=(1 << 32) + 9,
        actualExposureUs=1001.5,
        frameExposureUs=None,
        exposureEvidence="configuration_readback_not_frame_metadata",
        offsetX=6,
        offsetY=8,
    )
    assert sdk.calls.count(("TriggerSoftware",)) == int(mode == "software")
    assert ("create", 4) in sdk.calls and ("strategy", int(mode == "freeRun")) in sdk.calls
    assert ("TriggerMode", "Off" if mode == "freeRun" else "On") in sdk.calls
    assert ("TriggerSelector", "FrameStart") in sdk.calls
    if mode == "hardware":
        assert ("TriggerSource", "Line2") in sdk.calls and ("TriggerActivation", "FallingEdge") in sdk.calls
    elif mode == "software":
        assert ("TriggerSource", "Software") in sdk.calls
    assert ("ExposureAuto", "Off") in sdk.calls and ("ExposureTime", 1001.5) in sdk.calls
    assert ("GainAuto", "Off") in sdk.calls and ("Gain", 3.5) in sdk.calls
    assert sdk.calls.count(("free",)) == 1 and ("clear",) not in sdk.calls
    session.stop()
    session.start(params)
    assert sdk.calls[-2:] == [("start",), ("clear",)]


def test_lifecycle_multiple_devices_and_open_failure(sdk, session):
    other = mvs.MvsSession("GIGE")
    other.connect()
    assert ("create", 1) in sdk.calls
    session.close()
    assert ("finalize",) not in sdk.calls
    sdk.failures["open"] = 123
    with pytest.raises(mvs.MvsError, match="0x0000007B"):
        mvs.MvsSession("USB").connect()
    assert sdk.calls[-1] == ("destroy",)
    with pytest.raises(mvs.MvsError, match="missing"):
        mvs.MvsSession("missing").connect()
    other.close()
    assert sdk.calls.count(("initialize",)) == sdk.calls.count(("finalize",)) == 1


def test_discovery_registers_gige_and_usb_without_opening(sdk):
    assert mvs.discoverMvsDevices() == [
        dict(
            serialNumber="GIGE",
            modelName="MV-G",
            manufacturerName="HIKROBOT",
            transport="GigE",
            ipAddress="192.168.5.10",
        ),
        dict(
            serialNumber="USB",
            modelName="MV-U",
            manufacturerName="Hikvision",
            transport="USB3",
            ipAddress="",
        ),
    ]
    assert sdk.calls == [("initialize",), ("enumerate",), ("finalize",)]
    sdk.devices.clear()
    assert mvs.discoverMvsDevices() == []


@pytest.mark.parametrize("compatibilityConstants", [False, True])
def test_gentl_gige_open_and_discovery_exclude_other_manufacturers(sdk, compatibilityConstants):
    if compatibilityConstants:
        del sdk.MV_GENTL_GIGE_DEVICE
    sdk.devices[0].nTLayerType = 0x40
    sdk.devices[0].SpecialInfo.stGigEInfo.chManufacturerName = b"Hangzhou Hikrobot Co., Ltd."
    # A different manufacturer can legitimately reuse the same serial number.
    sdk.devices.append(Device(1, Device.Info(Identity(b"GIGE", b"MV-G", 0, b"Dahua"))))
    usb = sdk.devices[1].SpecialInfo.stUsb3VInfo
    usb.chManufacturerName = b"Huaray"
    usb.chVendorName = b"Hikrobot"
    rows = mvs.discoverMvsDevices()
    assert rows == [
        dict(
            serialNumber="GIGE",
            modelName="MV-G",
            manufacturerName="Hangzhou Hikrobot Co., Ltd.",
            transport="GigE",
            ipAddress="192.168.5.10",
        )
    ]
    with pytest.raises(mvs.MvsError, match="No Hikrobot.*USB"):
        mvs.MvsSession("USB").connect()
    other = mvs.MvsSession("GIGE")
    try:
        other.connect()
        assert ("create", 0x40) in sdk.calls
    finally:
        other.close()
    usb.chManufacturerName = b""
    assert mvs.discoverMvsDevices()[1]["manufacturerName"] == "Hikrobot"


def test_duplicate_serial_rejected_without_opening(sdk):
    sdk.devices[1].SpecialInfo.stUsb3VInfo.chSerialNumber = b"GIGE"
    with pytest.raises(mvs.MvsError, match="Multiple MVS devices.*GIGE"):
        mvs.discoverMvsDevices()
    with pytest.raises(mvs.MvsError, match="Multiple MVS devices.*GIGE"):
        mvs.MvsSession("GIGE").connect()
    assert not any(row[0] == "create" for row in sdk.calls)


def test_connection_only_opens_and_closes(sdk, session):
    session.close()
    assert sdk.calls == [
        ("initialize",),
        ("enumerate",),
        ("create", 4),
        ("open", 1, 0),
        ("close",),
        ("destroy",),
        ("finalize",),
    ]


def test_only_device_use_requires_sdk(monkeypatch, tmp_path):
    monkeypatch.setenv("MVCAM_COMMON_RUNENV", str(tmp_path))
    session = mvs.MvsSession("SN")
    with pytest.raises(mvs.MvsError) as caught:
        session.connect()
    assert caught.value.code == "E_CAMERA_SDK_UNAVAILABLE"
    assert mvs._sdkUsers == 0
    session.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows vendor DLL lookup")
@pytest.mark.parametrize("layout", ["working_vendor_import", "custom_path", "common_runtime"])
def test_bindings_load_in_frozen_search_environment(monkeypatch, tmp_path, layout):
    samples = tmp_path / "Samples" / "Python" / "MvImport"
    samples.mkdir(parents=True)
    (samples / "MvCameraControl_class.py").touch()
    common = tmp_path / "common"
    arch = "Win64_x64" if sys.maxsize > 2**32 else "Win32_i86"
    otherArch = "Win32_i86" if sys.maxsize > 2**32 else "Win64_x64"
    wrongDirectory = tmp_path / otherArch
    wrongDirectory.mkdir()
    (wrongDirectory / "MvCameraControl.dll").touch()
    directory = tmp_path / "custom-mvs" if layout == "custom_path" else common / "MVS" / "Runtime" / arch
    directory.mkdir(parents=True)
    dll = directory / "MvCameraControl.dll"
    dll.touch()
    path = os.pathsep.join(map(str, (wrongDirectory, directory))) if layout == "custom_path" else ""
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("CommonProgramFiles(x86)", str(common))
    monkeypatch.setenv("MVCAM_COMMON_RUNENV", str(tmp_path))
    sdk = NS()
    loaded, directories = [], []

    @contextmanager
    def addDirectory(value):
        directories.append(Path(value))
        try:
            yield
        finally:
            directories.remove(Path(value))

    def vendorImport(name):
        assert name == "MvCameraControl_class" and sys.path[0] == str(samples)
        if layout != "working_vendor_import" and not loaded:
            raise OSError("bare MvCameraControl.dll unavailable")
        return sdk

    monkeypatch.setattr(os, "add_dll_directory", addDirectory)
    monkeypatch.setattr(ctypes, "WinDLL", lambda value: loaded.append(Path(value)))
    monkeypatch.setattr(mvs, "import_module", vendorImport)
    assert mvs._loadBindings() is sdk
    assert str(samples) not in sys.path and os.environ["PATH"] == path
    if layout == "working_vendor_import":
        assert not loaded and not directories
    else:
        assert loaded == [dll] and directories == [directory]
        sdk._emoRuntimeDllSearch.close()
        assert not directories


def test_fixed_manual_auto_features_and_chunk_exposure(sdk, session):
    sdk.accessModes["GainAuto"] = sdk.AM_RO
    sdk.failures["ExposureAuto access"] = sdk.MV_E_GC_NODE_NOT_FOUND
    params = dict(
        triggerMode="freeRun",
        outputColor="gray",
        exposureMode="manual",
        gainMode="manual",
        exposureUs=55.0,
        gainDb=2.0,
    )
    session.start(params)
    assert ("GainAuto", "Off") not in sdk.calls and ("ExposureAuto", "Off") not in sdk.calls
    assert ("Gain", 2.0) in sdk.calls and ("ExposureTime", 55.0) in sdk.calls
    sdk.frameInfo.fExposureTime = 54.75
    _, meta = session.capture(params, lambda: None, None, 100)
    assert meta["actualExposureUs"] == 54.75 and ("read", "ExposureTime") not in sdk.calls
    assert meta["exposureEvidence"] == "frame_metadata" and meta["frameExposureUs"] == 54.75
    session.stop()
    sdk.enumSymbols["GainAuto"] = b"Continuous"
    with pytest.raises(mvs.MvsError, match="Read-only GainAuto"):
        session.start(params)


def test_single_frame_configuration_is_changed_before_repeated_triggers(sdk, session):
    assert sdk.enumSymbols["AcquisitionMode"] == b"SingleFrame"
    params = {"triggerMode": "software", "outputColor": "gray"}
    session.start(params)
    for _ in range(2):
        image, _meta = session.capture(params, lambda: None, 4, 100)
        assert image.shape == (2, 2)
    assert sdk.calls.index(("AcquisitionMode", "Continuous")) < sdk.calls.index(("start",))
    assert sdk.calls.count(("TriggerSoftware",)) == 2


@pytest.mark.parametrize("frameCount", [1, 4])
def test_burst_only_camera_uses_one_frame_per_software_trigger(sdk, session, frameCount):
    del sdk.triggerSelectors[3]
    count = "AcquisitionBurstFrameCount"
    sdk.intValues[count] = frameCount
    params = {"triggerMode": "software", "outputColor": "gray"}
    session.start(params)
    assert ("TriggerSelector", "FrameBurstStart") in sdk.calls
    assert sdk.intValues[count] == 1
    if frameCount == 1:
        assert (count, 1) not in sdk.calls
    else:
        assert sdk.calls.index((count, 1)) < sdk.calls.index(("start",))
    for _ in range(2):
        session.capture(params, lambda: None, 4, 100)
    assert sdk.calls.count(("TriggerSoftware",)) == 2


@pytest.mark.parametrize("failure", ["unreadable", "unwritable", "readback_mismatch"])
def test_burst_mode_fails_before_grabbing_when_one_frame_cannot_be_verified(sdk, session, monkeypatch, failure):
    del sdk.triggerSelectors[3]
    count = "AcquisitionBurstFrameCount"
    sdk.intValues[count] = 3
    if failure == "readback_mismatch":
        monkeypatch.setattr(sdk.MvCamera, "MV_CC_SetIntValueEx", lambda self, name, value: 0)
    else:
        sdk.failures[count + " read" if failure == "unreadable" else count] = 42
    with pytest.raises(mvs.MvsError, match=count) as caught:
        session.start({"triggerMode": "software"})
    assert caught.value.code == "E_CAMERA_CONFIG_FAILED"
    assert ("start",) not in sdk.calls and ("TriggerSoftware",) not in sdk.calls


@pytest.mark.parametrize(
    ("access", "result", "symbol", "canStart"),
    [
        (3, 0, b"Continuous", True),
        (3, 0, b"MultiFrame", False),
        (0, 0, b"Continuous", True),  # Not implemented: fixed acquisition hardware.
        (0, 265, b"Continuous", True),  # Older node map with no selectable mode.
        (1, 0, b"SingleFrame", False),  # Temporarily unavailable; current mode cannot be checked.
    ],
)
def test_fixed_acquisition_mode_is_verified_when_readable(sdk, session, access, result, symbol, canStart):
    sdk.accessModes["AcquisitionMode"] = access
    sdk.failures["AcquisitionMode access"] = result
    sdk.enumSymbols["AcquisitionMode"] = symbol
    if canStart:
        session.start({"triggerMode": "freeRun"})
        assert ("start",) in sdk.calls
    else:
        with pytest.raises(mvs.MvsError, match="AcquisitionMode.*Continuous") as caught:
            session.start({"triggerMode": "freeRun"})
        assert caught.value.code == "E_CAMERA_CONFIG_FAILED" and ("start",) not in sdk.calls
    assert ("AcquisitionMode", "Continuous") not in sdk.calls


def test_capacity_checked_before_command_and_after_receipt(sdk, session):
    params = {"triggerMode": "software", "outputColor": "bgr"}
    session.start(params)
    with pytest.raises(mvs.MvsError) as caught:
        session.capture(params, lambda: None, 11, 100)
    assert caught.value.code == "E_FRAME_CAPACITY" and not caught.value.softwareTriggerIssued
    assert ("TriggerSoftware",) not in sdk.calls
    sdk.frameInfo.nWidth = 3
    with pytest.raises(mvs.MvsError) as caught:
        session.capture(params, lambda: None, 12, 100)
    assert caught.value.code == "E_FRAME_CAPACITY" and caught.value.softwareTriggerIssued
    assert caught.value.frameReceived and sdk.calls[-1] == ("free",)
    assert not any(call[0] == "convert" for call in sdk.calls)


def test_timeout_uses_slices_and_single_command(sdk, session, monkeypatch):
    params = {"triggerMode": "software", "outputColor": "gray"}
    session.start(params)
    sdk.outcomes.extend([7, 7])
    clock = iter([0.0, 0.0, 0.1, 0.16])
    monkeypatch.setattr(mvs, "monotonic", lambda: next(clock))
    with pytest.raises(mvs.MvsError) as caught:
        session.capture(params, lambda: None, 4, 150)
    assert caught.value.code == "E_CAMERA_TIMEOUT" and caught.value.softwareTriggerIssued
    assert not caught.value.frameReceived
    assert [call for call in sdk.calls if call[0] == "get"] == [("get", 100), ("get", 50)]
    assert sdk.calls.count(("TriggerSoftware",)) == 1 and ("free",) not in sdk.calls


def test_unbounded_hardware_cancellation_releases_received_frame(sdk, session):
    params = dict(
        triggerMode="hardware", triggerSource="Line0", triggerActivation="RisingEdge", outputColor="gray"
    )
    session.start(params)
    sdk.outcomes.extend([7, 0])

    def check():
        if not sdk.outcomes:
            raise InterruptedError()

    with pytest.raises(InterruptedError) as caught:
        session.capture(params, check, 4, None)
    assert caught.value.frameReceived and not getattr(caught.value, "softwareTriggerIssued", False)
    assert sdk.calls[-1] == ("free",) and ("TriggerSoftware",) not in sdk.calls


def test_cleanup_after_sdk_failure(sdk, session):
    session.start({"triggerMode": "freeRun"})
    sdk.failures["stop"] = 42
    with pytest.raises(mvs.MvsError, match="StopGrabbing"):
        session.close()
    assert sdk.calls[-4:] == [("stop",), ("close",), ("destroy",), ("finalize",)]
