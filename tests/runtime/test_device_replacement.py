"""Prepared replacement evidence uses synthetic adapters only."""
from copy import deepcopy
import json
import time
from threading import Event

import pytest

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.models import ProjectDocument
from tests.runtime import test_device_lifecycle as lifecycleTests
from tests.runtime.test_device_lifecycle import capture, control
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_configuration import finish

makeManager = lifecycleTests.makeManager


def changedProject(manager):
    payload = simulatedProject()
    payload["devices"]["definitions"] = {key: value.model_dump(mode="json") for key, value in manager.definitions.items()}
    payload["devices"]["definitions"]["camera"]["connection"]["serial"] = "REPLACED"
    return ProjectDocument.model_validate(payload)


def testOnlyChangedDeviceIsReservedAndOriginalFramesSurvive(makeManager):
    manager = makeManager()
    frame = capture(manager, "other")
    old = manager.drivers["camera"]
    other = manager.drivers["other"]
    manager.revoke("job")
    manager.authorize("job", ["other"])
    prepared = manager.prepareReplacement(changedProject(manager))
    try:
        assert prepared.changed == {"camera"}
        with pytest.raises(DeviceError, match="replacement"):
            manager.authorize("competitor", ["camera"])
        with pytest.raises(DeviceError, match="replacement"):
            manager.beginControl("camera", manager.executors["camera"].generation, "connect")
        capture(manager, "other")
        assert manager.drivers["camera"] is old
        prepared.install()
        assert manager.drivers["camera"] is not old and not manager.drivers["camera"].connected
        assert manager.drivers["other"] is other and other.sequence == 2
        assert frame["frameId"] in manager.frames.frames
    finally:
        prepared.close()
    assert manager.replacement is None


@pytest.mark.parametrize("state", ["authorized", "connected", "active"])
def testReplacementRejectsUnretiredAffectedDevice(makeManager, state):
    manager = makeManager()
    if state != "authorized":
        manager.revoke("job")
    release = Event()
    operation = None
    if state == "connected":
        control(manager, "connect")
    elif state == "active":
        entered = Event()
        def wait(*_):
            entered.set()
            assert release.wait(5)
        operation = manager.executors["camera"].submit("operation", wait)
        assert entered.wait(2)
    try:
        with pytest.raises(DeviceError) as error:
            manager.prepareReplacement(changedProject(manager))
        assert error.value.code == "E_CONFIG_REQUIRES_STOP"
        assert manager.replacement is None
    finally:
        release.set()
        if operation:
            manager.executors["camera"].wait(operation)


def testDiscardClosesCandidateThreadsWithoutReplacingAuthority(makeManager):
    manager = makeManager()
    manager.revoke("job")
    old = manager.drivers["camera"]
    prepared = manager.prepareReplacement(changedProject(manager))
    threads = [executor.thread for executor in prepared.executors.values()]
    prepared.close()
    prepared.close()
    assert not any(thread.is_alive() for thread in threads)
    assert manager.drivers["camera"] is old and manager.replacement is None
    manager.authorize("job", ["camera"])
    capture(manager)


def testPreparedFailurePreservesOldDevicesAndReleasesFence(makeManager, monkeypatch):
    from emo_master.apps.runtime.devices import drivers
    manager = makeManager()
    manager.revoke("job")
    previous = manager.drivers["camera"]
    class Broken(type(previous)):
        def __init__(self, definition):
            raise ValueError("constructor failure")
    monkeypatch.setattr(drivers, "resolveDriver", lambda _entry: Broken)
    with pytest.raises(ValueError, match="constructor failure"):
        manager.prepareReplacement(changedProject(manager))
    assert manager.drivers["camera"] is previous and manager.replacement is None


def testFailedCandidateCleanupRetainsOwnershipUntilActualRelease(makeManager):
    manager = makeManager()
    manager.revoke("job")
    prepared = manager.prepareReplacement(changedProject(manager))
    driver = prepared.drivers["camera"]
    close = driver.close
    def fail(deadline=None):
        raise RuntimeError("injected cleanup failure")
    driver.close = fail
    with pytest.raises(DeviceError, match="cleanup failure"):
        prepared.close()
    assert manager.replacement is prepared and not prepared.closed
    with pytest.raises(DeviceError, match="replacement"):
        manager.authorize("job", ["camera"])
    driver.close = close
    prepared.close()
    assert manager.replacement is None and prepared.closed


@pytest.fixture
def runtime(tmp_path, request):
    path = tmp_path / "project.emoproj"
    payload = simulatedProject()
    if getattr(request, "param", False):
        payload["production"].update(mode="continuous", cycleIntervalMs=10)
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    yield service
    service.close()


def replacementRequest(runtime, requestId="replace"):
    snapshot = runtime.projectConfiguration.snapshot()
    payload = deepcopy(snapshot["payload"])
    payload["devices"]["definitions"]["camera"]["connection"]["serial"] = "REPLACED"
    return dict(requestId=requestId, expectedRuntimeInstanceId=runtime.runtimeInstanceId,
                expectedRevision=snapshot["documentRevision"], localPayload=payload,
                selectedPaths=["/devices/definitions/camera/connection/serial"])


def testValidationDisposesCandidateAndCommitInstallsDisconnectedAdapter(runtime):
    c = runtime.projectConfiguration
    manager = runtime.deviceManager
    old = manager.drivers["camera"]
    request = replacementRequest(runtime)
    c.validate(request)
    assert manager.drivers["camera"] is old and manager.replacement is None
    assert c.snapshot()["documentRevision"] == 1
    c.start(request)
    assert finish(c, "replace")["state"] == "COMMITTED"
    assert manager.drivers["camera"] is not old
    assert manager.drivers["camera"].definition.connection["serial"] == "REPLACED"
    assert not manager.drivers["camera"].connected and manager.replacement is None
    assert c.store.snapshot()["payload"] == runtime.loadedPayload


def testSqliteFailureDiscardsPreparedAdapterAndUnblocksAdmission(runtime):
    c = runtime.projectConfiguration
    manager = runtime.deviceManager
    old = manager.drivers["camera"]
    with c.store.sqlite.connection() as connection:
        connection.execute("CREATE TRIGGER failReplacement BEFORE INSERT ON projectWriteReceipts BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    c.start(replacementRequest(runtime))
    assert finish(c, "replace")["state"] == "FAILED"
    assert manager.drivers["camera"] is old and manager.replacement is None
    assert c.store.snapshot()["documentRevision"] == 1


def testCancelledPreparedReplacementDoesNotCommitOrLeak(runtime):
    c = runtime.projectConfiguration
    prepare = c.prepare
    entered, release = Event(), Event()
    def held(payload, cancelled):
        candidate = prepare(payload, cancelled)
        entered.set()
        assert release.wait(5)
        return candidate
    c.prepare = held
    c.start(replacementRequest(runtime))
    try:
        assert entered.wait(2)
        assert runtime.deviceManager.replacement is not None
        c.cancel("replace")
    finally:
        release.set()
    assert finish(c, "replace")["state"] == "CANCELLED"
    assert runtime.deviceManager.replacement is None
    assert c.store.snapshot()["documentRevision"] == 1


def testAddAndRemoveUnreferencedDevicesWithoutRecreatingOtherAdapter(runtime):
    c = runtime.projectConfiguration
    old = runtime.deviceManager.drivers["camera"]
    request = replacementRequest(runtime, "add")
    payload = deepcopy(c.snapshot()["payload"])
    extra = deepcopy(payload["devices"]["definitions"]["camera"])
    extra["connection"]["serial"] = "EXTRA"
    payload["devices"]["definitions"]["extra"] = extra
    request.update(localPayload=payload, selectedPaths=["/devices/definitions/extra"])
    c.start(request)
    assert finish(c, "add")["state"] == "COMMITTED"
    assert "extra" in runtime.deviceManager.drivers
    assert runtime.deviceManager.drivers["camera"] is old
    request = replacementRequest(runtime, "remove")
    del request["localPayload"]["devices"]["definitions"]["extra"]
    request["selectedPaths"] = ["/devices/definitions/extra"]
    c.start(request)
    assert finish(c, "remove")["state"] == "COMMITTED"
    assert "extra" not in runtime.deviceManager.drivers
    assert runtime.deviceManager.drivers["camera"] is old


@pytest.mark.parametrize("runtime", [True], indirect=True)
def testIndependentContinuousJobSurvivesAdapterAdditionAndReplacement(runtime):
    runtime.productionMode = True
    c = runtime.projectConfiguration
    started = runtime.StartJob(pb.StartJobRequest(project_id=runtime.loadedProjectId, workflow_id="main"), None)
    assert started.ok, started.message
    driver = runtime.deviceManager.drivers["camera"]
    deadline = time.monotonic() + 10
    while driver.sequence < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert driver.sequence >= 2
    request = replacementRequest(runtime, "add-independent")
    request["localPayload"]["devices"]["definitions"]["camera"]["connection"]["serial"] = driver.definition.connection["serial"]
    extra = deepcopy(request["localPayload"]["devices"]["definitions"]["camera"])
    extra["connection"]["serial"] = "EXTRA"
    request["localPayload"]["devices"]["definitions"]["extra"] = extra
    request["selectedPaths"] = ["/devices/definitions/extra"]
    c.start(request)
    assert finish(c, "add-independent")["state"] == "COMMITTED"
    before = driver.sequence
    request = replacementRequest(runtime, "replace-independent")
    request["localPayload"]["devices"]["definitions"]["extra"]["connection"]["serial"] = "EXTRA-NEW"
    request["selectedPaths"] = ["/devices/definitions/extra/connection/serial"]
    c.start(request)
    assert finish(c, "replace-independent")["state"] == "COMMITTED"
    deadline = time.monotonic() + 10
    while driver.sequence <= before + 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert driver.sequence > before + 2
    assert runtime.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), None).status == "RUNNING"
    assert runtime.deviceManager.drivers["camera"] is driver
    request = replacementRequest(runtime, "remove")
    del request["localPayload"]["devices"]["definitions"]["extra"]
    request["selectedPaths"] = ["/devices/definitions/extra"]
    c.start(request)
    assert finish(c, "remove")["state"] == "COMMITTED"
    assert "extra" not in runtime.deviceManager.drivers
    assert runtime.deviceManager.drivers["camera"] is driver
    declaration = runtime.pluginScanResult.deviceTypes[driver.definition.deviceTypeId]
    declaration.nextCyclePaths = []
    request = replacementRequest(runtime, "unsafe-default")
    request["localPayload"]["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    request["selectedPaths"] = ["/devices/definitions/camera/defaults/exposureUs"]
    c.start(request)
    assert finish(c, "unsafe-default")["code"] == "E_CONFIG_REQUIRES_STOP"
