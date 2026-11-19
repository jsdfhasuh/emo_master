"""Opt-in frozen camera Host check with synthetic pixels and a version-only MVS SDK call."""
from pathlib import Path
import os
import sys
from time import monotonic

import numpy as np

import emo_master
from emo_master.apps.runtime.devices.drivers import validateDriver
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.devices import DeviceDefinition


class _SyntheticMvsSession:
    def __init__(self, serialNumber):
        if serialNumber != "SELFTEST-MVS":
            raise ValueError("unexpected synthetic camera identity")
        self.sequence = 0

    def connect(self):
        from emo_master.plugins.builtins._hikrobot_mvs import _loadBindings
        sdk = _loadBindings()
        version = sdk.MvCamera.MV_CC_GetSDKVersion()
        if not version:
            raise RuntimeError("MVS SDK did not report its version")
        self.sdkVersion = ".".join(str((version >> shift) & 255) for shift in (24, 16, 8, 0))

    def start(self, params):
        if params["triggerMode"] != "software" or params["outputColor"] != "bgr":
            raise ValueError("unexpected MVS default acquisition settings")

    def capture(self, params, check, capacity, timeoutMs):
        check()
        self.sequence += 1
        image = np.full((3, 5, 3), (self.sequence, 72, 154), dtype=np.uint8)
        return image, dict(blockId=self.sequence, deviceTimestamp=200 + self.sequence,
                           actualExposureUs=6500.0, offsetX=4, offsetY=8, sdkVersion=self.sdkVersion,
                           hostPid=os.getpid(), hostFrozen=bool(getattr(sys, "frozen", False)))

    def close(self):
        pass


def checkCameraDrivers() -> dict[str, object]:
    root = Path(emo_master.__file__).resolve().parent / "plugins" / "builtins"
    scan = PluginRegistry(emo_master.__version__).scan(root)
    camera = scan.activeOperators["vision.io.huaray_camera"]
    requirement = camera.manifest.deviceRequirements["camera"]
    typeIds = ("device.camera.huaray_imv", "device.camera.hikrobot_mvs")
    for typeId in typeIds:
        if not requirement.acceptsDeclaration(scan.deviceTypes[typeId]):
            raise RuntimeError(f"registered camera operator cannot bind {typeId}")
    mvs = scan.deviceTypes["device.camera.hikrobot_mvs"]
    definition = mvs.validateDefinition(DeviceDefinition(name="Synthetic MVS", deviceTypeId=mvs.deviceTypeId,
        driverVersion=mvs.version, connection={"serialNumber": "SELFTEST-MVS", "maxFrameBytes": 1024}))
    driver = validateDriver(mvs)(definition, apiFactory=_SyntheticMvsSession)

    def capture(slot):
        if slot != "camera":
            raise ValueError(f"unexpected camera slot {slot}")
        image, acquisition = driver.execute("capture", definition.defaults, lambda: False, monotonic() + 30)
        return image, dict(acquisition=acquisition)

    operator = camera.operatorClass()
    try:
        for index in (1, 2):
            result = operator.executeNode({}, {}, dict(captureDevice=capture))
            outputs = result["outputs"]
            if set(outputs) != {"image", "frame", "blockId", "deviceTimestamp", "actualExposureUs"}:
                raise RuntimeError("camera operator output ports changed")
            expected = np.full((3, 5, 3), (index, 72, 154), dtype=np.uint8)
            if outputs["image"].dtype != expected.dtype or not np.array_equal(outputs["image"], expected):
                raise RuntimeError("camera operator returned incorrect synthetic pixels")
            if (outputs["blockId"], outputs["deviceTimestamp"], outputs["actualExposureUs"]) != (index, 200 + index, 6500.0):
                raise RuntimeError("camera operator returned incorrect synthetic frame metadata")
            acquisition = result["diagnostics"]["deviceFrame"]["acquisition"]
            if acquisition["hostPid"] == os.getpid() or acquisition["hostPid"] != driver.process.pid:
                raise RuntimeError("synthetic acquisition did not run in the registered MVS Host")
            if acquisition["hostFrozen"] != bool(getattr(sys, "frozen", False)):
                raise RuntimeError("MVS Host did not use the parent process's frozen application mode")
        return dict(operatorId=camera.manifest.operatorId, operatorVersion=camera.manifest.version,
                    deviceTypes=list(typeIds), capturedDeviceType=mvs.deviceTypeId, syntheticFrames=2,
                    sdkVersion=acquisition["sdkVersion"], hostPid=acquisition["hostPid"],
                    hostFrozen=acquisition["hostFrozen"], hardwareAccessed=False)
    finally:
        driver.close()
