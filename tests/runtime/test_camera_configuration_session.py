import json
import threading
import time

import pytest

from emo_master.apps.runtime.preview.live import LivePreviewManager
from emo_master.plugins.builtins._huaray_imv import IMV_TIMEOUT, resetImvApiForTests
from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator
from tests.plugins.test_camera_configuration import ConfigurationApi
from tests.plugins.test_huaray_camera_operator import _params
from tests.runtime.test_live_operator_preview import _descriptor


def waitUntil(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "camera state did not converge"
        time.sleep(0.01)


class TriggerApi(ConfigurationApi):
    def __init__(self):
        super().__init__()
        self.images = 0
        self.dropSoftware = False
        self.ownerThreads = set()

    def open(self, handle):
        self.ownerThreads.add(threading.get_ident())
        return super().open(handle)

    def set_enum(self, handle, name, value):
        self.ownerThreads.add(threading.get_ident())
        return super().set_enum(handle, name, value)

    def get_frame(self, handle, timeoutMs):
        self.ownerThreads.add(threading.get_ident())
        if self.getFrameResults:
            return super().get_frame(handle, timeoutMs)
        if self.enums["TriggerMode"] == "On":
            if not self.images:
                time.sleep(timeoutMs / 1000)
                return IMV_TIMEOUT, None
            self.images -= 1
        return super().get_frame(handle, timeoutMs)

    def execute_command(self, handle, name):
        self.ownerThreads.add(threading.get_ident())
        if not self.dropSoftware:
            self.images += 1
        return super().execute_command(handle, name)


@pytest.fixture
def session(tmp_path):
    resetImvApiForTests()
    api = TriggerApi()

    class Camera(HuarayCameraOperator):
        def __init__(self):
            super().__init__(lambda: api)

    registry = {"vision.io.fake_camera": _descriptor(Camera)}
    manager = LivePreviewManager(registry, journalRoot=tmp_path / "adjustments")
    sessionId, error = manager.open("vision.io.fake_camera", "p", "main", "camera", _params(), configurationOnly=True)
    assert error is None
    worker = manager._configurationSessions[sessionId]
    yield manager, worker, api
    manager.closeAll(terminate=True)
    resetImvApiForTests()


def request(worker, action, payload=None, requestId=None):
    from uuid import uuid4
    return worker.request(action, payload or {}, worker.config.identity, requestId or str(uuid4()))


def testConfigurationOpensReadOnlyAndSharesOneThread(session):
    manager, worker, api = session
    assert api.writes == []
    assert api.startStrategies == []
    assert request(worker, "apply", {"exposureMode": "manual", "exposureUs": 15000.0})["ok"]
    assert request(worker, "start")["ok"]
    waitUntil(lambda: bool(worker.latest.jpeg))
    assert request(worker, "stop")["ok"]
    assert api.openCount == 1
    assert len(api.ownerThreads) == 1 and threading.get_ident() not in api.ownerThreads
    assert "confirmation" in manager.close(worker.sessionId)
    assert request(worker, "close", {"decision": "restore"})["ok"]
    waitUntil(lambda: worker.closed)
    assert api.enums["ExposureAuto"] == "Continuous"


def testSoftwarePreviewNeverTriggersAutomaticallyAndDeduplicates(session):
    _, worker, api = session
    assert request(worker, "start", {"triggerMode": "software"})["ok"]
    time.sleep(0.2)
    assert api.commands == []
    payload = request(worker, "trigger", requestId="one-trigger")
    assert payload["ok"]
    waitUntil(lambda: worker.state == "softwareReady")
    assert api.commands == ["TriggerSoftware"]
    assert request(worker, "trigger", requestId="one-trigger")["ok"]
    time.sleep(0.15)
    assert api.commands == ["TriggerSoftware"]
    assert request(worker, "trigger", requestId="second-trigger")["ok"]
    waitUntil(lambda: len(api.commands) == 2 and worker.state == "softwareReady")


def testHardwareWaitExternalFramesAndCancellation(session):
    _, worker, api = session
    assert request(worker, "start", {"triggerMode": "hardware"})["ok"]
    time.sleep(0.35)
    assert worker.state == "waiting"
    assert worker.thread.is_alive() and not worker.config.failed
    assert api.commands == [] and api.openCount == 1
    api.images = 1
    waitUntil(lambda: worker.latest.state == "waitingNext" and bool(worker.latest.jpeg))
    firstFrame = worker.latest
    time.sleep(0.2)
    assert worker.latest.jpeg == firstFrame.jpeg
    api.images = 1
    waitUntil(lambda: worker.latest.blockId > firstFrame.blockId)
    start = time.monotonic()
    assert request(worker, "stop")["ok"]
    assert time.monotonic() - start < 0.5
    assert worker.state == "connected" and not worker.closed
    assert api.commands == []


def testSoftwareTimeoutIsFaultNotHardwareWaitAndDoesNotRetry(session):
    _, worker, api = session
    assert request(worker, "apply", {"captureTimeoutMs": 150})["ok"]
    assert request(worker, "start", {"triggerMode": "software"})["ok"]
    api.dropSoftware = True
    assert request(worker, "trigger")["ok"]
    waitUntil(lambda: worker.state == "fault")
    assert worker.errorCode == "E_CAMERA_TIMEOUT"
    assert api.commands == ["TriggerSoftware"]
    assert not worker.config.camera.grabbing
    assert not request(worker, "trigger")["ok"]
    assert api.commands == ["TriggerSoftware"]


def testHardwareDisconnectIsNotWaiting(session):
    _, worker, api = session
    assert request(worker, "start", {"triggerMode": "hardware"})["ok"]
    api.online = False
    waitUntil(lambda: worker.state == "fault")
    assert worker.errorCode == "E_CAMERA_DISCONNECTED"
    assert api.openCount == 1


def testSwitchModesStopsClearsRestartsAndKeepsOriginal(session):
    _, worker, api = session
    assert request(worker, "start", {"triggerMode": "hardware"})["ok"]
    original = dict(worker.config.original)
    generation = worker.generation
    assert request(worker, "apply", {"triggerMode": "software"})["ok"]
    assert worker.generation > generation
    assert worker.state == "softwareReady"
    assert api.bufferClears >= 1 and api.stopCount >= 1
    assert worker.config.original == original
    assert api.commands == []
    assert request(worker, "close", {"decision": "restore"})["ok"]
    assert api.enums["TriggerMode"] == "Off" and api.enums["TriggerSource"] == "Line1"


def testFaultedRestoreIsReportedAndJournalSurvivesShutdown(session):
    manager, worker, api = session
    assert request(worker, "apply", {"exposureMode": "manual", "exposureUs": 10000.0})["ok"]
    api.denied.add("ExposureAuto")
    result = request(worker, "close", {"decision": "restore"})
    assert not result["ok"] and not worker.closed
    assert result["snapshot"]["pending"]
    manager.closeAll()
    stored = json.loads(worker.journalPath.read_text(encoding="utf-8"))
    assert stored["pending"] and stored["records"]
    count = len(api.writes)
    nextManager = LivePreviewManager({}, journalRoot=worker.journalPath.parent)
    assert nextManager.recoveryWarnings
    assert len(api.writes) == count


def testRetainAndDuplicateCloseNeverWriteAgain(session):
    _, worker, api = session
    assert request(worker, "apply", {"gainMode": "manual", "gainRaw": 4.0})["ok"]
    count = len(api.writes)
    assert request(worker, "close", {"decision": "retain"}, "close-1")["ok"]
    waitUntil(lambda: worker.closed)
    assert request(worker, "close", {"decision": "retain"}, "close-1")["ok"]
    assert len(api.writes) == count
    assert api.doubles["GainRaw"] == 4.0


def testIdentityAndRequestIdConflictRejectBeforeWrites(session):
    _, worker, api = session
    result = worker.request("apply", {"gainMode": "manual"}, "wrong-camera", "bad-identity")
    assert not result["ok"] and result["code"] == "E_CAMERA_IDENTITY"
    assert api.writes == []
    assert request(worker, "recover")["ok"]
    assert request(worker, "apply", {"gainMode": "manual", "gainRaw": 1.0}, "write-1")["ok"]
    result = request(worker, "apply", {"gainRaw": 2.0}, "write-1")
    assert result["code"] == "E_REQUEST_CONFLICT"
    assert api.doubles["GainRaw"] == 1.0


def testRequestLimitNeverBlocksStopRecoveryOrRestore(session):
    _, worker, api = session
    assert request(worker, "start", {"triggerMode": "software"})["ok"]
    worker.results.update({f"old-{index}": ("", {}) for index in range(4096)})
    assert request(worker, "trigger")["code"] == "E_CAMERA_SESSION_LIMIT"
    assert request(worker, "stop")["ok"]
    assert request(worker, "recover")["ok"]
    assert request(worker, "close", {"decision": "restore"})["ok"]
    assert api.enums["TriggerMode"] == "Off"


def testExplicitAbandonCanReleaseDisconnectedCamera(session, monkeypatch):
    _, worker, api = session
    assert request(worker, "start", {"triggerMode": "hardware"})["ok"]
    api.online = False
    monkeypatch.setattr(api, "stop_grabbing", lambda handle: -1)
    result = request(worker, "close", {"decision": "abandon"})
    assert result["ok"] and result["snapshot"]["state"] == "abandoned"
    waitUntil(lambda: worker.closed)
    assert worker.config.camera.handle is None
    journal = json.loads(worker.journalPath.read_text(encoding="utf-8"))
    assert journal["pending"] and journal["state"] == "abandoned"


def testPreviewMakesSingleFrameDeviceContinuousAndRestoresIt(session):
    _, worker, api = session
    api.enums["AcquisitionMode"] = "SingleFrame"
    assert request(worker, "start")["ok"]
    assert api.enums["AcquisitionMode"] == "Continuous"
    assert request(worker, "close", {"decision": "restore"})["ok"]
    assert api.enums["AcquisitionMode"] == "SingleFrame"


def testFailedShutdownKeepsResourceOwnership(session, monkeypatch):
    manager, worker, _ = session
    originalClose = worker.config.close

    def failedClose():
        raise RuntimeError("native release failed")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(worker.config, "close", failedClose)
            assert manager.closeAll(terminate=True) == ["native release failed"]
        assert worker.closed and not worker.thread.is_alive()
        assert worker.ownsResources() and manager.ownsResources()
        assert manager.hasConfigurationSession("p")
        assert manager.close(worker.sessionId) == "native release failed"
    finally:
        originalClose()
        worker.releaseError = None


def testFreshStreamNeedsNoBufferClearButRestartsMustClear(session, monkeypatch):
    _, worker, api = session
    clears = []

    def clear(handle):
        clears.append(handle)
        return 0 if api.startStrategies else -101

    monkeypatch.setattr(api, "clear_buffer", clear)
    assert request(worker, "start", {"triggerMode": "software"})["ok"]
    assert clears == []
    assert request(worker, "stop")["ok"]
    assert request(worker, "start", {"triggerMode": "software"})["ok"]
    assert len(clears) == 1
    monkeypatch.setattr(api, "clear_buffer", lambda handle: -101)
    assert request(worker, "stop")["ok"]
    result = request(worker, "start", {"triggerMode": "software"})
    assert not result["ok"] and "-101" in result["message"]
    assert not worker.config.camera.grabbing


@pytest.mark.parametrize("mode", ["freeRun", "software"])
def testPreviewStartsWhenSoftwareSourceHidesTriggerActivation(session, monkeypatch, mode):
    from tests.plugins.test_camera_configuration import hideInactiveTriggerEdge
    _, worker, api = session
    api.enums["TriggerSource"] = "Software"
    hideInactiveTriggerEdge(api, monkeypatch)
    assert request(worker, "start", {"triggerMode": mode})["ok"]
    if mode == "software":
        assert api.commands == []
        assert request(worker, "trigger")["ok"]
    waitUntil(lambda: bool(worker.latest.jpeg))
    assert request(worker, "close", {"decision": "restore"})["ok"]
