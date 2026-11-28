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

        window.reloadAction.trigger()
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
    assert "已连接" in custom.headerStatus.text() and custom.headerJob.text() == "已完成"
    assert custom.resumeButton.isHidden() and not custom.identity.isVisibleTo(custom)
    custom.diagnosticToggle.setChecked(True)
    assert custom.identity.isVisibleTo(custom)
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
        item = dialog.deviceItems[named]
        assert "入口相机" in item.text(0) and "模拟相机" in item.text(0)
        assert item.toolTip(0) == named
        assert dialog.deviceItems[unnamed].text(0).splitlines()[0] == unnamed
        dialog.deviceList.setCurrentItem(item)
        QTest.mouseClick(dialog.actions["connect"], Qt.LeftButton)
        waitFor(lambda: bool(calls))
        assert calls[0][0][2] == named and calls[0][0][4] == "connect"
        waitFor(lambda: dialog.pending is None)
        states[named]["name"] = "已重命名的入口相机"
        dialog.nextState = 0
        waitFor(lambda: "已重命名" in dialog.deviceList.currentItem().text(0))
        assert dialog.selectedDeviceId() == named
    finally:
        dialog.dispose()


def testDeviceHealthUsesStateContractAndClearsStaleSelection(qtApp):
    scope = dict(runtimeInstanceId="runtime", projectId="project", projectLoadGeneration=1)
    types = {
        key: SimpleNamespace(displayName=title, managementActions=[], controlCapabilities=controls)
        for key, title, controls in (
            ("camera", "模拟相机", ["connect", "maintenance", "recover"]),
            ("device.tcp.server", "TCP 服务", ["disconnect"]),
        )
    }
    states = {
        key: dict(scope, name=key, deviceTypeId="camera", **values)
        for key, values in (
            ("online", dict(driverState=dict(connectionState="CONNECTED"), lastError="上次连接失败")),
            ("offline", dict(lifecycleConnected=False, availableControls=["connect"])),
            ("maintenance", dict(admissionState="PAUSED", lifecycleConnected=False)),
            ("failed", dict(state="QUARANTINED", lifecycleConnected=True)),
            ("unknown", dict(state="IDLE")),
        )
    }
    states["server"] = dict(scope, name="接收服务", deviceTypeId="device.tcp.server",
        availableControls=["disconnect"], driverState=dict(state="LISTENING",
            channels=[dict(channelId="route", name="测试通道", allowedIps=[], connected=False)]))
    readError = threading.Event()

    def read(project):
        if readError.is_set():
            raise RuntimeError("测试读取失败")
        return "runtime", states

    client = SimpleNamespace(listDeviceTypes=lambda: (types, {}, {}), getDeviceStates=read,
        getDeviceDiagnostics=lambda scope, key, cursor: dict(scope, deviceId=key, cursor=cursor, records=[]),
        close=lambda: None)
    dialog = DeviceManagementDialog(client, lambda: "project")
    dialog.show()
    try:
        waitFor(lambda: len(dialog.states) == 6)
        assert {key: item.text(1) for key, item in dialog.deviceItems.items()} == {
            "online": "已连接", "offline": "未连接", "maintenance": "维护中", "failed": "已隔离",
            "unknown": "未知", "server": "监听中",
        }
        assert dialog.overview.text() == "6 台设备\n正常 2  ·  非运行 1  ·  维护 1  ·  异常 1  ·  未知 1"
        dialog.deviceList.setCurrentItem(dialog.deviceItems["online"])
        assert dialog.deviceError.text() == "上次连接失败"
        assert dialog.rawState.isHidden()
        dialog.rawToggle.click()
        assert dialog.rawState.isReadOnly() and "上次连接失败" in dialog.rawState.toPlainText()
        dialog.deviceList.setCurrentItem(dialog.deviceItems["server"])
        assert dialog.deviceIdField.text() == "server"
        assert "已连接 0 个客户端" in dialog.deviceHealth.text()
        assert dialog.deviceError.isHidden() and dialog.rawState.isHidden()
        assert dialog.channelStatus.item(0, 3).text() == "等待客户端"
        assert dialog.actions["disconnect"].text() == "停止服务"
        assert dialog.actions["disconnect"].isEnabled() and dialog.actions["connect"].isHidden()
        dialog.nextState = 0
        waitFor(dialog.diagnostics.runtimeLogsButton.isEnabled)
        dialog.diagnostics.runtimeLogsButton.click()
        assert "Runtime 本机日志窗口" in dialog.status.text() and dialog.isVisible()

        dialog.deviceList.setCurrentItem(dialog.deviceItems["online"])
        assert dialog.rawToggle.isChecked()
        readError.set()
        dialog.nextState = 0
        waitFor(lambda: "测试读取失败" in dialog.status.text())
        assert dialog.overview.text().endswith("未知 6") and dialog.deviceHealth.text() == "未知"
        assert all(item.text(1) == "未知" for item in dialog.deviceItems.values())
        assert not dialog.rawToggle.isEnabled() and not dialog.actions["connect"].isEnabled()
        readError.clear()
        states["online"]["projectLoadGeneration"] = 2
        states["online"]["name"] = "新发布设备"
        published = states["online"]
        states.clear()
        states["online"] = published
        dialog.nextState = 0
        waitFor(lambda: dialog.deviceItems["online"].text(0).startswith("新发布设备"))
        assert dialog.selectedDeviceId() is None and dialog.selectedDetail.isHidden()
        assert not dialog.diagnostics.runtimeLogsButton.isEnabled()
    finally:
        dialog.dispose()


def testDeviceLogDrillDownPreservesOperatorLogConditions(qtApp, tmp_path):
    key = "camera-full-device-id"
    scope = dict(runtimeInstanceId="runtime", projectId="project", projectLoadGeneration=1)
    states = {key: dict(scope, name="入口相机", deviceTypeId="camera")}
    client = SimpleNamespace(listDeviceTypes=lambda: ({}, {}, {}),
        getDeviceStates=lambda project: ("runtime", states),
        getDeviceDiagnostics=lambda scope, key, cursor: dict(scope, deviceId=key, cursor=cursor, records=[]),
        close=lambda: None)
    window = OperatorWindow(dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    dialog = DeviceManagementDialog(client, lambda: "project", window)
    dialog.show()
    try:
        logView = window.logView
        logView.levelCombo.setCurrentIndex(logView.levelCombo.findData("WARN"))
        logView.keywordEdit.setText("设备")
        waitFor(lambda: key in dialog.deviceItems)
        dialog.deviceList.setCurrentItem(dialog.deviceItems[key])
        dialog.nextState = 0
        waitFor(dialog.diagnostics.runtimeLogsButton.isEnabled)
        dialog.diagnostics.runtimeLogsButton.click()
        assert window.contentStack.currentWidget() is logView and dialog.isHidden()
        assert logView.deviceId == key and logView.deviceLabel.toolTip() == key
        assert "入口相机" in logView.deviceLabel.text()
        assert logView.levelCombo.currentData() == "WARN" and logView.keywordEdit.text() == "设备"
    finally:
        dialog.dispose()
        closeWindow(window)
