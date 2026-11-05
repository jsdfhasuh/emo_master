"""Real simulated-camera cancellation receipts survive graceful Stop and restart."""
import json
from threading import Event

import pytest

from examples.dispatch_queue_demo import demoProject
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.core.project.models import ProjectDocument
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_dispatch_queue_integration import runtime as runtime


def loadStopProject(service, client, tmp_path, dispatch):
    payload = demoProject(tmp_path).model_dump(mode="json")
    payload["devices"]["definitions"]["camera"]["defaults"].update(
        width=64, height=48, exposureUs=200000)
    if not dispatch:
        payload["workflows"].pop("consumer")
        payload["workflowOrder"] = ["main", "capture"]
        workflow = payload["workflows"]["capture"]
        workflow["nodes"] = [node for node in workflow["nodes"] if node["nodeId"] != "dispatch"]
        workflow["edges"] = [edge for edge in workflow["edges"] if edge["toNode"] != "dispatch"]
        workflow["layout"]["nodePositions"].pop("dispatch")
        payload["presentation"] = WorkflowStore().toPayload()["presentation"]
    document = ProjectDocument.model_validate(payload)
    assert document.schemaVersion == "2.9" and document.presentation.schemaVersion == "1.2"
    path = tmp_path / "stop-receipts.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    loaded = client.loadProject(str(path))
    assert loaded.ok, loaded.message
    validated = client.validateProject(service.loadedProjectId)
    assert validated.ok, validated.errors


@pytest.mark.parametrize("dispatch", [False, True], ids=["ordinary", "dispatch"])
def testGracefulStopDuringSimulatedCapturePreservesReceiptAndRestarts(runtime, tmp_path, monkeypatch, dispatch):
    service, client, _display = runtime
    loadStopProject(service, client, tmp_path, dispatch)
    driver = service.deviceManager.drivers["camera"]
    execute = driver.execute
    captureEntered, releaseCapture = Event(), Event()
    calls, completed, cancelled = [], [], []

    def observedExecute(action, params, cancellation, deadline, **kwargs):
        call = len(calls) + 1
        calls.append(call)
        paused = False

        def observedCancellation():
            nonlocal paused
            if call % 3 == 0 and not paused:
                paused = True
                # Synchronize at the real driver's own cancellation probe, not
                # an assumed exposure delay. Its implementation decides the outcome.
                captureEntered.set()
                assert releaseCapture.wait(10), "Stop did not release the capture probe"
            return cancellation()

        try:
            image = execute(action, params, observedCancellation, deadline, **kwargs)
        except DeviceError as error:
            cancelled.append((call, error.code, error.outcome))
            raise
        completed.append(call)
        return image

    monkeypatch.setattr(driver, "execute", observedExecute)
    jobs = []
    for attempt in range(2):
        captureEntered.clear()
        releaseCapture.clear()
        started = client.startJob(service.loadedProjectId, "main", capturePresentation=True)
        assert started.ok, started.message
        jobs.append(started.job_id)
        try:
            assert captureEntered.wait(20), "third simulated capture did not enter"
            assert driver.sequence == 2 * (attempt + 1)
            assert completed == [call for call in range(1, 3 * (attempt + 1)) if call % 3]
            assert (started.job_id in service.deviceManager.dispatchGroups) == dispatch
            stopped = client.stopJob(started.job_id, mode="graceful")
            assert stopped.ok, stopped.message
        finally:
            releaseCapture.set()

        terminal = waitForTerminal(service, started.job_id, timeoutSeconds=20)
        waitFor(lambda: not service.jobSupervisor._ownedJobs()
                and not service.deviceManager.workers
                and not service.deviceManager.dispatchGroups, timeout=15)
        assert cancelled == [(3 * (index + 1), "E_DEVICE_CANCELLED", "CANCELLED")
                             for index in range(attempt + 1)]
        assert (terminal.status, terminal.error_code) == ("ABORTED", "E_CANCELLED")
        aborted, = [event for event in service._eventsAfter(started.job_id, 0)
                    if event.eventType == "job.aborted"]
        assert json.loads(aborted.payloadJson)["outcome"] == "CANCELLED"
        assert len(calls) == 3 * (attempt + 1)  # cancellation never replays the acquisition
        assert driver.sequence == 2 * (attempt + 1)
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.reservations
        assert not service.projectConfiguration.cycles and not service.projectConfiguration.childCycles
        executor = service.deviceManager.executors["camera"]
        assert executor.active is None and not executor.queue and not executor.quarantined

    assert jobs[0] != jobs[1]
    assert service.deviceManager.drivers["camera"] is driver
