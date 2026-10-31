"""Native Qt regressions for the no-page Operator shell, not desktop evidence."""
import threading
from types import SimpleNamespace

import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.core.presentation.models import Presentation
from emo_master.ui.device_management import DeviceManagementDialog
from emo_master.ui.presentation.renderer import RuntimePages
from tests.runtime.production_fixture import productionProject, saveDocument
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def noPageProject(root, **kwargs):
    document = productionProject(root, **kwargs)
    document.presentation = Presentation(schemaVersion=document.presentation.schemaVersion)
    saveDocument(root, document)
    return document


@pytest.mark.parametrize("failed", [False, True])
def testNoPageUsesOwnedJobAndShowsActualTerminalWithoutSelectingHistory(qtApp, tmp_path, failed):
    root = tmp_path / "project"
    noPageProject(root, mode="single")
    if failed:
        (root / "outputs/result.png").mkdir(parents=True)
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        assert not window.error
        assert "Runtime：已连接" in window.pages.status.text()
        assert "尚未开始" in window.pages.jobStatus.text()
        assert "尚未配置运行页面" in window.pages.stack.currentWidget().text()
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: bool(window.state["jobId"]) and window.state["canLoad"] and not window.busy or window.error)
        assert not window.error
        job = window.controller.sessions["main"].jobId
        expected = "FAILED" if failed else "COMPLETED"
        assert window.controller.runtime.jobRepository.get(job).status == expected
        assert window.state["jobStatus"] == expected and window.state["resourcesReleased"]
        assert "最近任务：" in window.pages.jobStatus.text()
        assert expected in window.pages.jobStatus.text() and job in window.pages.jobStatus.text()
        assert "任务资源已释放" in window.pages.jobStatus.text()
        assert "Runtime：已连接" in window.pages.status.text()  # A failed Job is not a lost connection.
        if failed:
            assert window.state["message"] and window.state["message"] in window.pages.jobStatus.text()
        assert window.session is None and not window.controller.presentation.jobs
        assert window.startButton.isEnabled()

        QTest.mouseClick(window.reloadButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and not window.controller.jobId)
        assert window.controller.runtime.jobRepository.get(job).status == expected
        assert "尚未开始" in window.pages.jobStatus.text()
        assert job not in window.pages.jobStatus.text()  # No arbitrary historical Job is adopted.
    finally:
        closeWindow(window)


def testNoPageRunningStopProgressAndRealRestart(qtApp, tmp_path, monkeypatch):
    root = tmp_path / "project"
    noPageProject(root)
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    entered, release = threading.Event(), threading.Event()
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and window.state.get("jobStatus") == "RUNNING" or window.error)
        assert not window.error
        first = window.controller.jobId
        assert first in window.pages.jobStatus.text() and "运行中" in window.pages.jobStatus.text()
        original = window.controller._waitRetired

        def heldRetirement(session, deadline):
            entered.set()
            assert release.wait(10)
            original(session, deadline)

        monkeypatch.setattr(window.controller, "_waitRetired", heldRetirement)
        QTest.mouseClick(window.stopButton, Qt.LeftButton)
        waitFor(entered.is_set)
        assert "正在请求停止" in window.pages.jobStatus.text()
        assert first in window.pages.jobStatus.text() and not window.startButton.isEnabled()
        release.set()
        waitFor(lambda: not window.busy and window.state["canStart"])
        assert window.state["jobStatus"] == "ABORTED"
        assert "已中止" in window.pages.jobStatus.text() and "任务资源已释放" in window.pages.jobStatus.text()
        assert not window.controller.runtime.jobSupervisor.ownsJobResources(first)
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and window.controller.jobId != first and window.state.get("jobStatus") == "RUNNING")
        second = window.controller.jobId
        waitFor(lambda: any(event.eventType == "workflow.completed" for event in window.controller.runtime.eventStore.read(second)))
        assert second in window.pages.jobStatus.text() and first not in window.pages.jobStatus.text()
    finally:
        release.set()
        closeWindow(window)


def testBasicFeedbackDoesNotReplaceCustomPageStatusAndMarksStaleConnection(qtApp, tmp_path):
    document = productionProject(tmp_path / "project")
    custom = RuntimePages(document.presentation)
    before = (custom.status.text(), custom.jobStatus.text(), custom.identity.text())
    custom.setBasicStatus(dict(state="READY", jobId="other", jobStatus="COMPLETED"), connected=True)
    assert (custom.status.text(), custom.jobStatus.text(), custom.identity.text()) == before
    assert not custom.resumeButton.isHidden() and not custom.identity.isHidden()
    basic = RuntimePages(None)
    basic.setBasicStatus(dict(state="RUNNING", jobId="owned", jobStatus="STOPPING"),
                         connected=False, connectionDetail="status read unavailable")
    assert "连接状态不可用" in basic.status.text()
    assert "停止中" in basic.jobStatus.text() and "最近一次已确认状态" in basic.jobStatus.text()
    assert "status read unavailable" in basic.jobStatus.text()


def testNoPageParallelWorkflowsKeepTheirOwnJobFeedback(qtApp, tmp_path):
    from tests.runtime.two_station_fixture import twoStationProject
    root = tmp_path / "project"
    document = twoStationProject(root)
    document.presentation = Presentation(schemaVersion=document.presentation.schemaVersion)
    document.runtime.startupWorkflowIds = ["main", "second"]
    saveDocument(root, document)
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: len(window.workflowPages) == 2 and not window.busy or window.error)
        assert not window.error
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and all(row.get("jobStatus") == "RUNNING"
            for row in window.state["workflows"].values()) or window.error)
        assert not window.error
        jobs = {key: row["jobId"] for key, row in window.state["workflows"].items()}
        assert len(set(jobs.values())) == 2
        for key, pages in window.workflowPages.items():
            assert jobs[key] in pages.jobStatus.text()
            assert jobs["second" if key == "main" else "main"] not in pages.jobStatus.text()
        QTest.mouseClick(window.workflowControls["main"]["stop"], Qt.LeftButton)
        waitFor(lambda: not window.busy and window.state["workflows"]["main"]["canStart"])
        assert "已中止" in window.workflowPages["main"].jobStatus.text()
        assert "运行中" in window.workflowPages["second"].jobStatus.text()
        assert not window.workflowSessions and not window.controller.presentation.jobs
    finally:
        closeWindow(window)


def testDeviceSelectorNamesPreserveSelectionAndControlIds(qtApp):
    named, unnamed = "camera-123456789", "camera-no-name"
    states = {key: dict(name=name, deviceTypeId="camera", availableControls=["connect"],
                       runtimeInstanceId="runtime", projectId="project", projectLoadGeneration=1)
              for key, name in [(named, "入口相机"), (unnamed, "")]}
    calls = []
    declaration = SimpleNamespace(displayName="模拟相机", managementActions=[], controlCapabilities=["connect"])
    client = SimpleNamespace(
        listDeviceTypes=lambda: ({"camera": declaration}, {}, {}),
        getDeviceStates=lambda project: ("runtime", states),
        getDeviceDiagnostics=lambda scope, key, cursor: dict(projectLoadGeneration=1,
            deviceId=key, cursor=cursor, records=[]),
        controlDevice=lambda *args, **kwargs: calls.append((args, kwargs)),
        close=lambda: None)
    dialog = DeviceManagementDialog(client, lambda: "project")
    dialog.show()
    try:
        waitFor(lambda: len(dialog.states) == 2 and dialog.pending is None)
        index = dialog.selector.findData(named)
        assert "入口相机" in dialog.selector.itemText(index) and "模拟相机" in dialog.selector.itemText(index)
        assert dialog.selector.itemData(index, Qt.ToolTipRole) == named
        assert dialog.selector.itemText(dialog.selector.findData(unnamed)) == unnamed
        dialog.selector.setCurrentIndex(index)
        QTest.mouseClick(dialog.actions["connect"], Qt.LeftButton)
        waitFor(lambda: bool(calls))
        assert calls[0][0][2] == named and calls[0][0][4] == "connect"
        waitFor(lambda: dialog.pending is None)
        states[named]["name"] = "已重命名的入口相机"
        dialog.nextState = 0
        waitFor(lambda: "已重命名" in dialog.selector.currentText())
        assert dialog.selector.currentData() == named
    finally:
        dialog.dispose()
