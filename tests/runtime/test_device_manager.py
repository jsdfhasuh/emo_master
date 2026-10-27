from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.frame_store import copyFrame
from emo_master.core.plugin.devices import DeviceType
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_runtime import simulatedProject


@pytest.fixture
def manager():
    root = Path(__file__).resolve().parents[2]
    declaration = DeviceType.model_validate(json.loads((root /
        "src/emo_master/plugins/builtins/simulated_camera/device_types/camera.json").read_text(encoding="utf-8")))
    manager = DeviceManager({declaration.deviceTypeId: declaration})
    manager.load(ProjectDocument.model_validate(simulatedProject()))
    manager.authorize("a", ["camera"])
    manager.authorize("b", ["camera"])
    yield manager
    manager.retire("a")
    manager.retire("b")
    manager.close()


def params(exposure=8000):
    return dict(width=7, height=5, exposureUs=exposure, gain=0)


def testAcquisitionDoesNotWaitForAlgorithmOrConsumerCopy(manager):
    first = manager.execute("a", "camera", "capture", params(), cycleId="cycle-a")
    second = manager.execute("b", "camera", "capture", params(12000), cycleId="cycle-b")
    assert first["frameId"] != second["frameId"]
    assert first["generation"] == second["generation"]
    assert np.all(copyFrame(first)[:, :, 2] == 8)
    assert np.all(copyFrame(second)[:, :, 2] == 12)
    # A's original still exists although the next acquisition already completed.
    assert len(manager.frames.frames) == 2
    assert manager.executors["camera"].snapshot()["state"] == "IDLE"


def testCancellingOneJobDoesNotRetireDeviceUsedBySibling(manager):
    entered = Event()
    driver = manager.drivers["camera"]
    execute = driver.execute
    def recording(*args, **kwargs):
        entered.set()
        return execute(*args, **kwargs)
    driver.execute = recording
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(manager.execute, "a", "camera", "capture", params(1000000), cycleId="a")
        assert entered.wait(2)
        manager.revoke("a")
        with pytest.raises(DeviceError) as error:
            running.result(timeout=2)
        assert error.value.code == "E_DEVICE_CANCELLED"
    image = manager.execute("b", "camera", "capture", params(), cycleId="b")
    assert copyFrame(image).shape == (5, 7, 3)
    assert driver is manager.drivers["camera"]


def testBadParametersAndUnauthorizedJobNeverEnterDriver(manager):
    with pytest.raises(ValueError):
        manager.execute("a", "camera", "capture", params(-1), cycleId="a")
    with pytest.raises(DeviceError) as error:
        manager.execute("unknown", "camera", "capture", params(), cycleId="other")
    assert error.value.code == "E_DEVICE_OWNER"
    assert manager.drivers["camera"].sequence == 0
    assert not manager.executors["camera"].quarantined
    assert not manager.frames.reservations


def testDuplicatePhysicalIdentityCannotCreateSecondExecutor(manager):
    payload = simulatedProject()
    payload["devices"]["definitions"]["alias"] = deepcopy(payload["devices"]["definitions"]["camera"])
    with pytest.raises(DeviceError) as error:
        manager.load(ProjectDocument.model_validate(payload))
    assert error.value.code == "E_DEVICE_DUPLICATE_IDENTITY"
    assert list(manager.executors) == ["camera"]


def testCapacityRejectedBeforeAcquisition(manager):
    manager.frames.maxFrames = 1
    manager.execute("a", "camera", "capture", params(), cycleId="a")
    with pytest.raises(DeviceError) as error:
        manager.execute("b", "camera", "capture", params(), cycleId="b")
    assert error.value.code == "E_FRAME_CAPACITY"
    assert manager.drivers["camera"].sequence == 1
    assert manager.executors["camera"].snapshot()["state"] == "IDLE"


def testReplacementConstructorFailureKeepsExistingAuthority(manager, monkeypatch):
    from emo_master.apps.runtime.devices.simulated import SimulatedCamera
    originalDriver = manager.drivers["camera"]
    originalExecutor = manager.executors["camera"]
    def fail(self, definition):
        raise RuntimeError("injected adapter preparation failure")
    monkeypatch.setattr(SimulatedCamera, "__init__", fail)
    with pytest.raises(RuntimeError, match="preparation failure"):
        manager.load(ProjectDocument.model_validate(simulatedProject()))
    assert manager.drivers["camera"] is originalDriver
    assert manager.executors["camera"] is originalExecutor
    assert not originalExecutor.closed
    result = manager.execute("a", "camera", "capture", params(), cycleId="still-valid")
    assert copyFrame(result).shape == (5, 7, 3)
