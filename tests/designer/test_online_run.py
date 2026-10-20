from copy import deepcopy
import json

import pytest
from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QDialog

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.services.online_run import startOnline
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_configuration import request, finish
from tests.designer.test_device_workspace import waitUntil


@pytest.fixture
def runtime(tmp_path):
    path = tmp_path / "online.emoproj"
    payload = simulatedProject()
    payload["workflows"]["main"]["layout"] = {"nodePositions": {"capture": {"x": 77, "y": 88}}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    try:
        yield service
    finally:
        service.close()


def testRunButtonStartsOnlineSnapshotWithoutChangingDifferentDraft(runtime, ownedDesignerWindow, tmp_path, monkeypatch):
    client = RuntimeClient(runtimeService=runtime)
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    local = deepcopy(runtime.loadedPayload)
    local["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 15000
    local["workflows"]["draft-only"] = deepcopy(local["workflows"]["main"])
    local["workflowOrder"].append("draft-only")
    local["runtime"]["startupWorkflowIds"] = ["draft-only"]
    local["workflows"]["main"]["layout"]["nodePositions"]["capture"] = {"x": 999, "y": 999}
    path = tmp_path / "draft.emoproj"
    saveProject(path, local)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.pageCoordinator.sync()
    session = window.pageCoordinator.session
    before = session.payload()
    with session.transaction():
        session.workflows.devices["definitions"]["camera"]["name"] = "unsaved name"
    draft, disk = session.payload(), path.read_bytes()
    calls = []
    originalStart = client.startJob
    def start(*args, **kwargs):
        calls.append((args, kwargs))
        return originalStart(*args, **kwargs)
    monkeypatch.setattr(client, "startJob", start)
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window, allowRun=True)
    monitor = None
    try:
        waitUntil(lambda: dialog.pending is None)
        assert dialog.runButton.isEnabled()
        dialog.runButton.click()
        monitor = dialog.lastRunDialog
        waitUntil(lambda: monitor.command is None)
        assert monitor.startedJobs, monitor.runStatus.text()
        waitUntil(lambda: monitor.data is not None and monitor.data["snapshot"]["documentRevision"] == 1)
        assert dialog.result() == QDialog.Rejected
        assert len(calls) == 1 and calls[0][0][1] == "main"
        assert calls[0][1]["expectedDocumentRevision"] == 1
        assert calls[0][1]["expectedRuntimeInstanceId"] == runtime.runtimeInstanceId
        assert len(runtime.jobRepository.all()) == 1
        assert monitor.data["snapshot"]["payload"]["workflows"]["main"]["layout"]["nodePositions"]["capture"] == {"x": 77, "y": 88}
        assert session.payload() == draft and path.read_bytes() == disk
        monitor.startOnline()
        assert len(calls) == 1
        assert monitor.scene.items() and not monitor.view.isInteractive()
        assert monitor.grab().save(str(tmp_path / "online-run.png"))
        waitUntil(lambda: monitor.stopAllButton.isEnabled())
        monitor.stopAllButton.click()
        waitUntil(lambda: monitor.command is None)
        window.pageCoordinator.history()
        assert session.payload() == before and path.read_bytes() == disk
    finally:
        if monitor:
            waitUntil(lambda: monitor.command is None)
            monitor.reject()
        dialog.reject()


@pytest.mark.parametrize("changed", ["revision", "instance"])
def testSnapshotFenceRejectsStaleStartBeforeCreatingJob(runtime, changed):
    client = RuntimeClient(runtimeService=runtime)
    snapshot = client.getOnlineProjectSnapshot()
    if changed == "revision":
        runtime.projectConfiguration.start(request(runtime.projectConfiguration))
        assert finish(runtime.projectConfiguration)["state"] == "COMMITTED"
    else:
        snapshot["runtimeInstanceId"] = "previous-runtime"
    results = startOnline(client, snapshot)
    assert len(results) == 1 and not results[0]["reply"].ok
    assert not runtime.jobRepository.all() and not runtime.deviceManager.workers


def testLostStartResponseReconcilesOriginalRequestWithoutRestart(runtime, monkeypatch):
    from emo_master.apps.designer.ui.online_graph import OnlineGraphDialog
    client = RuntimeClient(runtimeService=runtime)
    snapshot = client.getOnlineProjectSnapshot()
    originalStart = client.startJob
    calls = []
    def lost(*args, **kwargs):
        calls.append(kwargs["startRequestId"])
        reply = originalStart(*args, **kwargs)
        assert reply.ok, reply.message
        raise TimeoutError("simulated lost response")
    monkeypatch.setattr(client, "startJob", lost)
    monitor = OnlineGraphDialog(client, runtime.loadedProjectId, launchSnapshot=snapshot)
    monitor.show()
    try:
        monitor.startOnline()
        waitUntil(lambda: monitor.command is None and bool(monitor.uncertainRequests))
        assert list(monitor.uncertainRequests) == calls
        waitUntil(lambda: monitor.reconcileButton.isEnabled())
        monitor.reconcileButton.click()
        waitUntil(lambda: monitor.command is None)
        assert monitor.startedJobs, monitor.runStatus.text()
        assert not monitor.uncertainRequests
        assert len(calls) == len(runtime.jobRepository.all()) == 1
        assert list(monitor.startedJobs) == [runtime.jobRepository.all()[0].jobId]
    finally:
        waitUntil(lambda: monitor.command is None)
        monitor.reject()


def testIndependentOnlineSelectionPrecedesDraftTargetValidation():
    from emo_master.apps.designer.controllers.workflow_runtime_controller import WorkflowRuntimeController
    controller = object.__new__(WorkflowRuntimeController)
    controller._closing = controller._closed = controller._batchStarting = False
    controller.runs = {}
    controller.getRunTargets = lambda: ["deleted-draft-target"]
    calls = []
    controller.syncRuntimeProjectBeforeRun = lambda: calls.append("online-selected") or False
    controller.getWorkflows = lambda: pytest.fail("must not validate draft after independent online selection")
    controller.startJob()
    assert calls == ["online-selected"]
