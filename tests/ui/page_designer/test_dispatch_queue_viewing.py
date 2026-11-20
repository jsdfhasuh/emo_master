"""Original Designer entry with simulated camera, persistent consumer and queues."""
import os
from pathlib import Path

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox, QInputDialog

from examples.dispatch_queue_demo import saveDemo
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from tests.runtime.test_dispatch_queue_integration import gateConsumers
from tests.runtime.runtime_test_utils import jobFailureDetails
from tests.ui.page_designer.test_normal_run_viewing import waitFor, displayedImagesReady


def waitForConsumerImages(preview, runtime, jobId):
    try:
        waitFor(lambda: displayedImagesReady(preview.observer))
    except AssertionError as error:
        observer, session = preview.observer, preview.session
        view = session.readSnapshot()
        state = dict(connection=view.connection, detail=view.detail, job=view.jobId,
            visible=observer.isVisible(), minimized=observer.isMinimized(), detached=observer.detached,
            page=observer.currentPageId, sourceMode=observer.source.currentIndex(),
            sources=sorted(observer.imageSources()), demand=session._wantedImages,
            status=observer.status.text(), errors=list(session.errors)[-4:], records=list(session.records)[-2:],
            scopes={key: dict(key=scope.result.identity.resultKey, status=scope.result.status,
                images=list(scope.images), failures=dict(scope.failures), states=dict(scope.imageStates))
                for key, scope in view.scopes.items()},
            widgets=[dict(id=component.componentId, key=widget.key, null=widget.image.isNull())
                for component, widget in observer.widgets.get(observer.currentPageId, {}).values()
                if component.type == 'image'])
        result = next(iter(view.scopes.values())).result if view.scopes else None
        raise AssertionError(f'{error}; display={state!r}; '
            + jobFailureDetails(runtime, jobId, result=result)) from error


@pytest.mark.parametrize("slots", [("detection",), ("left", "right")])
def testDesignerContinuousConsumerPageAndQueueMonitor(qtApp, tmp_path, monkeypatch, slots):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    messages = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda _parent, title, text, *a, **k: messages.append((title, text)))
    choices = []
    def choose(_parent, _title, _label, labels, *_args):
        choices.append(labels)
        return labels[-1], True
    monkeypatch.setattr(QInputDialog, "getItem", choose)
    first, rest = gateConsumers(monkeypatch)
    path = saveDemo(tmp_path / "project", slots=slots)
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    window = MainWindow(RuntimeClient(runtime))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    dialog = None
    try:
        assert window.loadProjectDirectory(str(path)), messages
        # Provision only this isolated simulated Runtime. Opening a 2.7 draft
        # intentionally does not publish it to Runtime or dismiss that boundary.
        assert window.pageCoordinator._canSync()
        saveProject(path, window.pageCoordinator.session.payload())
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        from emo_master.core.project.configuration import projectConfigurationChanges
        assert not projectConfigurationChanges(runtime.projectConfiguration.current["payload"], window.pageCoordinator.session.payload())
        window.show()
        window.startJob()
        waitFor(lambda: window.currentJobId is not None or window.runtimePanelState.jobStatus == "FAILED")
        producer = window.currentJobId
        assert producer, (window.runtimePanelState.jobMessage, messages)
        group = runtime.deviceManager.dispatchGroups[producer]
        waitFor(lambda: bool(group.waiting))
        dialog = window.openGlobalCountersDialog()
        dialog.tabs.setCurrentIndex(1)
        panel = dialog.queuePanel
        waitFor(lambda: panel._worker is None and len(panel.slots) == len(slots))
        assert panel.groups.currentData() == group.groupId
        assert all(row["queued"] == 1 and row["waitingProducers"] for row in panel.slots)
        panel.details.setChecked(True)
        waitFor(lambda: panel._worker is None and panel.items.rowCount() == 1)
        first.set()
        preview = window.pageCoordinator.preview
        preview.watchCurrent()
        waitFor(lambda: preview.hub is not None or preview.error)
        assert preview.error is None
        waitForConsumerImages(preview, runtime, group.slots[slots[-1]].jobId)
        result = next(iter(preview.observer.displayed.values())).result
        assert result.identity.jobId == group.slots[slots[-1]].jobId and result.status == "COMPLETE"
        assert f"consumer / {slots[-1]}" in preview.observer.banner.text()
        assert "正在等待结果" not in preview.observer.status.text()
        assert window.currentJobId == producer and window.isJobRunning
        if len(slots) > 1:
            assert len(choices[-1]) == len(slots) and all(slot in " ".join(choices[-1]) for slot in slots)
        panel.refresh()
        waitFor(lambda: panel._worker is None and all(row["completedTotal"] == 1 for row in panel.slots))
        artifacts = os.environ.get("EMO_DISPATCH_UI_ARTIFACTS")
        if artifacts and len(slots) == 1:
            root = Path(artifacts)
            root.mkdir(parents=True, exist_ok=True)
            scale = os.environ.get("QT_SCALE_FACTOR", "1")
            dialog.resize(1000, 740)
            qtApp.processEvents()
            assert dialog.grab().save(str(root / f"queue-{scale}.png"))
            assert preview.observer.grab().save(str(root / f"consumer-{scale}.png"))
            panel.slots[0]["workflowName"] = "用于验证窄窗口显示的长名称消费流程与独立槽位"
            panel.slots[0]["lastErrorCode"] = "E_DISPATCH_WAIT_TIMEOUT"
            snapshot = runtime.jobSupervisor.dispatchQueueState(runtime.loadedProjectId, group.groupId, slots[0])
            snapshot["runtimeInstanceId"] = runtime.runtimeInstanceId
            snapshot["selected"]["slots"] = panel.slots
            panel.timer.stop()
            panel.applyState(snapshot)
            dialog.resize(650, 650)
            qtApp.processEvents()
            assert dialog.width() <= 660
            assert dialog.grab().save(str(root / f"queue-narrow-{scale}.png"))
        preview.closeAsync()
        waitFor(lambda: not preview.active())
        dialog.close()
        assert not panel.timer.isActive()
        rest.set()
        waitFor(lambda: all(slot.completedTotal >= 3 for slot in group.slots.values()))
        assert window.isJobRunning  # viewers never acquire execution ownership
        rest.clear()
        first.clear()
        waitFor(lambda: bool(group.waiting))
        window.stopJob()
        waitFor(lambda: not window.isJobRunning and not runtime.deviceManager.dispatchGroups)
        assert not runtime.deviceManager.frames.frames
        assert runtime.jobRepository.get(producer).status == "ABORTED"
        if len(slots) == 1:
            rest.set()
            window.startJob()
            waitFor(lambda: window.currentJobId not in (None, producer) or window.runtimePanelState.jobStatus == "FAILED")
            assert window.currentJobId not in (None, producer), window.runtimePanelState.jobMessage
            newGroup = runtime.deviceManager.dispatchGroups[window.currentJobId]
            preview.watchCurrent()
            waitFor(lambda: preview.hub is not None or preview.error)
            assert preview.error is None
            waitForConsumerImages(preview, runtime, newGroup.slots[slots[0]].jobId)
            assert next(iter(preview.observer.displayed.values())).result.identity.jobId == newGroup.slots[slots[0]].jobId
            rest.clear()
            waitFor(lambda: bool(newGroup.waiting))
            window.stopJob()
            waitFor(lambda: not window.isJobRunning and not runtime.deviceManager.dispatchGroups)
    finally:
        first.set()
        rest.set()
        if window.pageCoordinator.preview.active():
            window.pageCoordinator.preview.closeAsync()
            waitFor(lambda: not window.pageCoordinator.preview.active())
        if dialog:
            dialog.close()
        window.close()
        runtime.close()
