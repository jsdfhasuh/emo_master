"""Windows native QTest walkthrough using one temporary software Runtime."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch

import emo_master  # noqa: F401 - DLL bootstrap
import cv2
import grpc
import numpy as np
import PySide2
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtGui import QDesktopServices
from PySide2.QtWidgets import (
    QApplication, QBoxLayout, QDialog, QFileDialog, QInputDialog, QPushButton, QScrollArea, QTableWidget, QTreeWidgetItemIterator,
    QWidget,
)

from emo_master import __version__
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.operator_runtime.settings import DEFAULT_ROOT_KEY
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.current_production_fixture import currentProductionProject
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_run_view_selection import remoteStart
from tests.ui.operator_view.test_runtime_log_view import waitMs
from tests.ui.operator_view.test_view import waitFor


def softwareProject(root, *, pages=True, autoStart=False):
    path, document = currentProductionProject(root, mode="continuous", autoStart=autoStart, secondPage=pages)
    payload = document.model_dump(mode="json")
    payload["production"]["cycleIntervalMs"] = 300
    presentation = payload["presentation"]
    if pages:
        presentation["pages"]["main"]["name"] = "采集结果"
        presentation["pages"]["review"]["name"] = "复核结果"
        review = presentation["pages"]["review"]
        review["components"].insert(0, dict(componentId="freeze", type="navigation_button",
            props=dict(text="冻结当前结果"), actions=dict(clicked=dict(type="freeze", resultScopeId="root")),
            responsive=dict(width="content", minWidth=120, preferredWidth=120, minHeight=32)))
        review["components"][1]["layout"]["row"] = 1
        presentation["shell"] = dict(title="软件检测 · 双页走查",
            headerSurface=dict(kind="card", background="#f8f5ff", borderColor="#8b5cf6"),
            startControl=dict(label="开始检测", appearance=dict(customStyle=dict(qtQss=dict(
                enabled=True, code='QPushButton[emoPart="action"] { border-color: #8b5cf6; }')))))
        payload["devices"]["definitions"]["software-camera"] = dict(name="验收模拟相机",
            deviceTypeId="device.simulated.camera", driverVersion="1.0.0", connection={"serial": "R3-SOFTWARE"},
            defaults=dict(width=32, height=24, exposureUs=8000, gain=0), policies=dict(connect="manual"))
        # Give the software-only image a recognizable target in the native screenshots.
        pixels = np.full((240, 320, 3), (230, 223, 217), dtype=np.uint8)
        cv2.rectangle(pixels, (20, 20), (300, 220), (166, 124, 77), 3)
        for center in ((92, 88), (228, 88), (92, 152), (228, 152)):
            cv2.circle(pixels, center, 15, (119, 92, 68), -1, cv2.LINE_AA)
        cv2.putText(pixels, "R3", (138, 132), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (86, 57, 32), 2, cv2.LINE_AA)
        ok, encoded = cv2.imencode(".png", pixels)
        assert ok
        path.with_name("input.png").write_bytes(encoded.tobytes())
    else:
        presentation.update(defaultPageId=None, pages={}, pageOrder=[], resultScopes={}, dataSources={})
    result = ProjectDocument.model_validate(payload)
    assert (result.schemaVersion, result.presentation.schemaVersion, result.devices.schemaVersion) == ("2.10", "1.3", "1.0")
    saveProject(path, result.model_dump(mode="json"))
    assert loadProjectDocument(path) == result
    scan = PluginRegistry(__version__).scan(Path(emo_master.__file__).parent / "plugins" / "builtins")
    prepared = prepareRuntimeProject(result, path.parent, scan.activeOperators, deviceTypes=scan.deviceTypes)
    WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes).compile(prepared)
    return path, result


def click(button):
    assert button.isVisible() and button.isEnabled(), button.text()
    QTest.mouseClick(button, Qt.LeftButton)


def button(widget, title):
    return next(item for item in widget.findChildren(QPushButton) if item.text() == title and item.isVisible())


def treeItem(tree, matches):
    iterator = QTreeWidgetItemIterator(tree)
    while iterator.value() is not None:
        item = iterator.value()
        if matches(item.data(0, Qt.UserRole)):
            return item
        iterator += 1
    return None


def chooseTreeItem(tree, item):
    tree.scrollToItem(item)
    point = tree.visualItemRect(item).center()
    assert tree.viewport().rect().contains(point)
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=point, delay=0)
    assert tree.currentItem() is item


def tab(bar, index):
    QTest.mouseClick(bar, Qt.LeftButton, pos=bar.tabRect(index).center())
    assert bar.currentIndex() == index


def expose(widget):
    widget.show()
    widget.raise_()
    widget.activateWindow()
    assert QTest.qWaitForWindowExposed(widget)


def chooseProject(window, path):
    window.startupPage.ensureWidgetVisible(window.startupPage.chooseButton)
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")) as chooser:
        click(window.startupPage.chooseButton)
        if not chooser.called:
            waitFor(lambda: chooser.called)
    assert window.busy and window.operationKind == "loaded" and not window.startButton.isEnabled()


def showControls(window):
    assert window.topBar.isVisible() and window.projectButton.isVisible()


def clickProjectAction(window, action):
    menu = window.projectMenu
    assert window.projectButton.menu() is menu and action in menu.actions() and action.isEnabled()
    menu.popup(window.projectButton.mapToGlobal(window.projectButton.rect().bottomLeft()))
    QApplication.processEvents()
    assert menu.isVisible()
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())


def selectRun(window, job):
    showControls(window)
    def select(_parent, _title, _label, options, _index, _editable):
        return next(text for text in options if job in text), True
    with patch.object(QInputDialog, "getItem", side_effect=select) as chooser:
        clickProjectAction(window, window.viewRunsAction)
        if not chooser.called:
            waitFor(lambda: chooser.called)
    waitFor(lambda: window.viewSession is not None and window.viewSession.jobId == job and not window.busy)
    assert not window.error


def readPage(window, page, job):
    assert window.pages.currentPageId == page
    image = window.pages.widgets[page]["review-image" if page == "review" else "image"][1]
    waitFor(lambda: bool(window.pages.displayed) and not image.image.isNull())
    assert {record.result.identity.jobId for record in window.pages.displayed.values()} == {job}
    coverage = window.pages.captureCoverage
    assert coverage.jobId == job and coverage.enabled and not coverage.sourceProblem(window.pages.config, "image")
    return dict(pageId=page, jobId=job, imageSize=[image.image.width(), image.image.height()],
        capturePlanRevision=coverage.capturePlanRevision)


def choosePage(window, page):
    tabs = window.pages.pageNavigation.tabs
    index = next(i for i in range(tabs.count()) if tabs.tabData(i) == page)
    tab(tabs, index)


def startup(window, root, evidence, shot):
    waitFor(lambda: window.controller is not None and not window.busy)
    assert window.contentStack.currentWidget() is window.startupPage
    assert window.state["state"] == "EMPTY" and not window.startButton.isEnabled()
    assert window.topBar.parentWidget() is window and window.projectButton.isVisible()
    assert not window.controller.runtime.jobRepository.all()
    panel = window.startupPage.storage
    assert window.resolution.source == "saved" and panel.currentPath.text() == str(window.resolution.path)
    assert window.startupPage.network.currentLabel.text()
    shot(window, "01-startup-settings")
    window.resize(900, 700)
    waitMs(200)
    assert window.startupPage.columns.direction() == QBoxLayout.TopToBottom
    assert window.startupPage.horizontalScrollBar().maximum() == 0
    shot(window, "02-startup-900x700")
    click(panel.detailsButton)
    waitMs(100)
    assert str(window.resolution.path / "runtime.sqlite3") in panel.details.toPlainText()
    assert panel.currentPath.toolTip() == str(window.resolution.path)
    nextRoot = root / "下次启动 数据目录"
    window.startupPage.ensureWidgetVisible(panel.nextEdit)
    waitMs(100)
    assert window.startupPage.viewport().rect().contains(
        panel.nextEdit.mapTo(window.startupPage.viewport(), panel.nextEdit.rect().center()))
    QTest.mouseClick(panel.nextEdit, Qt.LeftButton)
    panel.nextEdit.setText(str(nextRoot))
    assert panel.nextEdit.hasFocus() and "未保存" in panel.feedback.text()
    window.startupPage.ensureWidgetVisible(panel.saveButton)
    click(panel.saveButton)
    assert "下次启动生效" in panel.feedback.text() and not nextRoot.exists()
    evidence["startup"] = dict(source=window.resolution.source, current=window.resolution.path.name,
        next=nextRoot.name, saveFeedback=panel.feedback.text(), narrowWidth=window.width())
    window.resize(1060, 800)
    window.startupPage.ensureWidgetVisible(window.startupPage.chooseButton)
    return nextRoot


def loadAndReload(window, root, path, document, evidence):
    invalid = root / "损坏工程.emoproj"
    invalid.write_text("{ invalid json", encoding="utf-8")
    chooseProject(window, invalid)
    waitFor(lambda: bool(window.error) and not window.busy)
    assert not window.startButton.isEnabled() and window.controller.document is None
    failure = window.error
    chooseProject(window, path)
    waitFor(lambda: window.pages is not None and not window.busy)
    assert not window.error and window.state["state"] == "READY" and window.startButton.isEnabled()
    assert window.contentStack.currentWidget() is window.businessHost
    assert window.topBar.parentWidget() is window and window._controlDock is window.pages.hostControls
    actualPath = Path(window.controller.runtime.loadedProjectFile).resolve()
    assert actualPath == path.resolve(), (actualPath, path)
    assert window.projectLabel.text() == document.project.name
    showControls(window)
    assert "#8b5cf6" in window.startButton.styleSheet() and window.startButton.text() == "开始检测"
    header = window.pages.findChild(QWidget, "pageShellHeader")
    assert header is not None and "#f8f5ff" in header.styleSheet()
    prior = window.pages
    clickProjectAction(window, window.reloadAction)
    waitFor(lambda: window.pages is not None and window.pages is not prior and not window.busy)
    assert not window.error and not window.controller.runtime.jobRepository.all()
    with grpc.insecure_channel(window.controller.address) as channel:
        info = rpc.RuntimeServiceStub(channel).GetRuntimeInfo(pb.RuntimeInfoRequest(), timeout=5)
    assert info.runtime_instance_id == window.controller.runtime.runtimeInstanceId
    assert window.controller.document.project.projectId == document.project.projectId
    expectedRevision = window.controller.runtime.projectConfiguration.current["documentRevision"]
    assert info.project_id == document.project.projectId
    assert info.document_revision == window.state["documentRevision"] == expectedRevision
    evidence["load"] = dict(invalidReason=failure, retrievedProjectId=document.project.projectId,
        documentRevision=expectedRevision, runtimeInstanceId=info.runtime_instance_id,
        persistentTopBarAndBusinessControlDock=True, customQss=True)


def openCompactDeviceDialog(window):
    showControls(window)
    click(window.communicationButton)
    dialog = window.communicationDialog
    available = dialog.screen().availableGeometry()
    compactSize = min(920, available.width() - 32), min(620, available.height() - 48)
    dialog.resize(*compactSize)
    expose(dialog)
    assert dialog.size().toTuple() == compactSize
    waitFor(lambda: "software-camera" in dialog.deviceItems)
    chooseTreeItem(dialog.deviceList, dialog.deviceItems["software-camera"])
    return dialog, dialog.findChild(QScrollArea)


def verifyDevice(window, evidence, shot):
    dialog, details = openCompactDeviceDialog(window)
    connect = dialog.actions["connect"]
    waitFor(connect.isEnabled)
    details.ensureWidgetVisible(connect)
    assert details.viewport().rect().contains(connect.mapTo(details.viewport(), connect.rect().center()))
    click(connect)
    waitFor(lambda: "控制完成" in dialog.status.text() and dialog.actions["disconnect"].isEnabled())
    shown = dialog.states["software-camera"]
    scope = dict(window.controller.runtime.deviceManager.scope)
    assert {key: shown[key] for key in scope} == scope and shown["deviceTypeId"] == "device.simulated.camera"
    assert shown["lifecycleConnected"] is True and dialog.deviceHealth.text().startswith("已连接")
    shot(dialog, "05-device-controls")
    evidence["device"] = dict(scope, deviceId="software-camera", state=shown["state"], connected=True,
        feedback=dialog.status.text())
    dialog.close()
    expose(window)


def runPair(window, root, document, evidence, shot):
    showControls(window)
    click(window.startButton)
    waitFor(lambda: window.state.get("jobStatus") == "RUNNING" and not window.busy)
    first = window.controller.jobId
    menu = window.pages.pageNavigation.tabs
    assert menu.count() == 2
    choosePage(window, "review")
    firstPage = readPage(window, "review", first)
    click(window.pages.widgets["review"]["freeze"][1])
    waitFor(lambda: window.pages.frozen is not None and window.pages.resumeButton.isVisible())
    assert readPage(window, "review", first) == firstPage
    click(window.pages.resumeButton)
    waitFor(lambda: window.pages.frozen is None and bool(window.pages.displayed))
    assert "实时" in window.pages.modeLabel.text()
    waitFor(lambda: window.pages.headerJob.text() == "运行中")
    shot(window, "03-production-review")
    with grpc.insecure_channel(window.controller.address) as observer:
        server = rpc.RuntimeServiceStub(observer)
        second = remoteStart(window, observer)
        waitFor(lambda: len(window.state.get("activeJobIds", ())) == 2 and not window.busy)
        selectRun(window, second)
        choosePage(window, "main")
        observed = readPage(window, "main", second)
        showControls(window)
        click(window.returnRunButton)
        waitFor(lambda: window.viewSession is None and not window.busy)
        returned = readPage(window, "review", first)
        evidence["pages"] = dict(first=firstPage, observed=observed, returned=returned, frozenAndResumed=True)
        showControls(window)
        click(window.logsButton)
        assert window.contentStack.currentWidget() is window.logView
        tab(window.logView.sources, 0)
        openLogJob(window.logView.jobLogs, second)
        assert window.viewSession is None and window.session.jobId == first and window.state["jobId"] == first
        click(window.backButton)
        assert window.contentStack.currentWidget() is window.businessHost and window.viewSession is None
        afterLog = readPage(window, "review", first)
        assert afterLog == returned
        evidence["logSelectionPreservesProduction"] = dict(logJobId=second, production=afterLog,
            productionControlJobId=window.state["jobId"])
        selectRun(window, second)
        choosePage(window, "main")
        inspectLogsAndReturn(window, root, first, second, evidence, shot)
        assert window.viewSession.jobId == second and window.state["jobId"] == first
        assert window.contentStack.currentWidget() is window.businessHost
        assert window.pages.currentPageId == "main"
        expectedDefault = []
        def confirm(_parent, title, _label, items, index, _editable):
            assert title == "停止运行"
            expectedDefault.append(items[index])
            assert first in expectedDefault[-1]
            return items[index], True
        showControls(window)
        with patch.object(QInputDialog, "getItem", side_effect=confirm):
            click(window.stopButton)
            if not expectedDefault:
                waitFor(lambda: bool(expectedDefault))
        assert window.operationKind == "stopped"
        runtime = window.controller.runtime
        waitFor(lambda: runtime.jobRepository.get(first).isTerminal and not runtime.jobSupervisor.ownsJobResources(first))
        # Query through a distinct client after the stop and after native Designer closes.
        records = server.ListRuns(pb.ListRunsRequest(project_id=document.project.projectId, include_terminal=True,
            runtime_instance_id=runtime.runtimeInstanceId), timeout=5)
        assert records.ok
        states = {entry.job_id: entry.status for entry in records.runs}
        assert {entry.workflow_id for entry in records.runs if entry.job_id in (first, second)} == {"main"}
        assert states[first] == "ABORTED" and states[second] == "RUNNING"
        evidence["stop"] = dict(workflowId="main", target=first, observed=second, after=states, firstRetired=True)
    return second


def openLogJob(jobs, job, *, canStop=False):
    if jobs.pages.currentWidget() is not jobs.catalog:
        click(button(jobs.jobPage, "返回运行任务"))
    def find():
        return treeItem(jobs.directory, lambda value: getattr(value, "job_id", "") == job)
    waitFor(find)
    chooseTreeItem(jobs.directory, find())
    if canStop:
        assert jobs.stopButton is not None and jobs.stopButton.isVisible() and jobs.stopButton.isEnabled()
    click(jobs.openButton)
    waitFor(lambda: jobs.job is not None and jobs.job.job_id == job and not jobs._opening and jobs.window is not None)
    assert jobs.pages.currentWidget() is jobs.jobPage


def currentEvent(jobs, message):
    jobs.search.setText(message)
    def event():
        return next((entry for entry in jobs.window.entries if entry.message == message), None)
    waitFor(lambda: event() is not None)
    entry = event()
    def find():
        return treeItem(jobs.events, lambda role: role == ("event", entry.eventId))
    waitFor(find)
    chooseTreeItem(jobs.events, find())
    assert jobs._detailEntry.eventId == entry.eventId
    return entry


def diagnosticRoundTrip(jobs, window, scope, job, evidence, shot):
    def drill(message):
        entry = currentEvent(jobs, message)
        anchor = jobs.events.verticalScrollBar().value()
        click(jobs.deviceButton)
        assert jobs.pages.currentWidget() is jobs.devicePage
        return entry, anchor

    right, before = drill("R3 设备执行摘要")
    waitFor(lambda: bool(jobs.devices.page.get("records")))
    assert {key: jobs.devices.page[key] for key in scope} == scope
    assert jobs.devices.page["deviceId"] == "software-camera"
    shot(window, "07-job-device-diagnostics")
    click(button(jobs.devicePage, "返回事件"))
    assert jobs.pages.currentWidget() is jobs.jobPage and jobs.job.job_id == job
    assert jobs._detailEntry.eventId == right.eventId and jobs.search.text() == right.message
    assert jobs.events.verticalScrollBar().value() == before

    wrong, before = drill("R3 历史加载代次")
    waitFor(lambda: "缓存不可用于" in jobs.deviceInfo.text())
    reason = jobs.deviceInfo.text()
    assert not jobs.devices.page.get("records")
    click(button(jobs.devicePage, "返回事件"))
    assert jobs.job.job_id == job and jobs._detailEntry.eventId == wrong.eventId
    assert jobs.search.text() == wrong.message and jobs.events.verticalScrollBar().value() == before
    evidence["diagnostics"] = dict(scope, deviceId="software-camera", selectedEvent=right.eventId,
        rejectedEvent=wrong.eventId, rejectedReason=reason, restored=True)


def verifyArrayDetail(jobs, message, expectedRows, evidence):
    entry = currentEvent(jobs, message)
    detail = jobs.detail
    tab(detail.tabs.tabBar(), detail.tabs.indexOf(detail.readable))
    data = next(detail.readable.topLevelItem(i) for i in range(detail.readable.topLevelItemCount())
                if detail.readable.topLevelItem(i).text(0) == "日志数据")
    chooseTreeItem(detail.readable, data)
    click(detail.arrayTableButton)
    dialog = detail.findChild(QDialog)
    assert dialog is not None and dialog.isVisible()
    try:
        table = dialog.findChild(QTableWidget)
        assert [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())] == ["name", "value", "ok"]
        assert [[table.item(row, column).text() for column in range(table.columnCount())]
                for row in range(table.rowCount())] == [["first", "37", "true"], ["second", "0", "false"]]
    finally:
        dialog.close()
    tab(detail.tabs.tabBar(), detail.tabs.indexOf(detail.rawTree))
    click(detail.copyRecordButton)
    raw = json.loads(QApplication.clipboard().text())
    assert raw["eventId"] == entry.eventId and raw["payload"]["data"] == expectedRows
    evidence["nativeDetail"] = dict(eventId=entry.eventId, jobId=entry.effectiveJobId, copied=True, table=expectedRows)
    return entry


def designerObservation(window, root, first, second, evidence, shot):
    source = window.controller
    operatorJobs = window.logView.jobLogs
    click(button(operatorJobs.jobPage, "返回最新"))
    waitFor(lambda: not operatorJobs._opening and operatorJobs.window and not operatorJobs.window.paused)
    channel = grpc.insecure_channel(source.address)
    client = RuntimeClient(rpc.RuntimeServiceStub(channel), ownedChannel=channel, runtimeTarget=source.address)
    designer = MainWindow(client, settingsStore=preferences(root / "designer"), localLogDirectory=root / "designer-logs")
    expose(designer)
    try:
        designer.openLogDialog()
        logDialog = designer.logDialog
        expose(logDialog)
        jobs = logDialog.jobLogs
        waitFor(lambda: jobs.instance == source.runtime.runtimeInstanceId and second in jobs.runs)
        row = jobs.runs[second]
        assert row.status == "RUNNING" and row.project_id == source.document.project.projectId
        assert row.runtime_instance_id == source.runtime.runtimeInstanceId
        tab(logDialog.sources, 0)
        tab(logDialog.runtimeNavigation, logDialog.runtimePages.indexOf(jobs))
        openLogJob(jobs, second, canStop=True)
        jobs.mode.setCurrentIndex(1)
        message = "R3 软件采样成功"
        rows = [dict(name="first", value=37, ok=True), dict(name="second", value=0, ok=False)]
        source.runtime.eventStore.append(second, "node.log", message,
            projectId=row.project_id, workflowId=row.workflow_id, payload={"data": rows})
        entry = currentEvent(jobs, message)
        shot(logDialog, "08-designer-same-job")
    finally:
        designer.close()
        waitFor(lambda: not designer.isVisible() and client._closed)
        window.raise_()
        window.activateWindow()
        QApplication.processEvents()
    assert verifyArrayDetail(operatorJobs, message, rows, evidence).eventId == entry.eventId
    runtime = source.runtime
    assert not runtime._closed and runtime.jobRepository.get(first).status == "RUNNING"
    assert runtime.jobRepository.get(second).status == "RUNNING" and not window.viewSession.stop.is_set()
    evidence["designer"] = dict(runtimeInstanceId=row.runtime_instance_id, projectId=row.project_id,
        jobId=row.job_id, status=row.status, eventId=entry.eventId, stopAvailable=True, remoteClosed=True,
        ownerRetained=True)


def nativeLocalLogs(window, root, evidence):
    logs = window.logView
    tab(logs.sources, 2)
    waitFor(lambda: window.logFeed.worker is None and logs.model.rowCount() > 0)
    feed = window.logFeed
    assert any(path.name.startswith("runtime-") for path in feed.files)
    assert any(path.name.startswith("runtime_app-") for path in feed.files)
    assert feed.activePath.name.startswith("runtime-")
    index = logs.fileCombo.findData(str(feed.activePath))
    assert index > 0
    logs.fileCombo.setCurrentIndex(index)
    waitFor(lambda: feed.worker is None and logs.model.rowCount() > 0)
    assert logs.statusLabel.text().startswith("本次活动文件（手动固定，不跟随轮转）")
    logs.levelCombo.setCurrentIndex(logs.levelCombo.findData("WARN"))
    click(logs.refreshButton)
    waitFor(lambda: feed.worker is None and logs.model.rowCount() > 0)
    current = root / "native-local-filter.jsonl"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(current), "JSONL (*.jsonl)")):
        logs.exportCurrentAction.trigger()
    rows = [json.loads(row) for row in current.read_text(encoding="utf-8").splitlines()]
    matching = [row for row in rows if row.get("level") == "WARN"]
    assert matching and len(matching) == logs.model.rowCount()
    with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")) as chooser:
        logs.exportFileAction.trigger()
        assert chooser.called and feed.exportWorker is None
    with patch.object(QDesktopServices, "openUrl", return_value=True) as folder:
        logs.scrollArea.ensureWidgetVisible(logs.folderButton)
        click(logs.folderButton)
        assert Path(folder.call_args.args[0].toLocalFile()) == feed.directory
    evidence["nativeJsonl"] = dict(fileWriters=[path.name for path in feed.files],
        fixedSelection=True, exportedWarnings=len(matching), saveDialogCancelled=True, folderRequest=True)


def inspectLogsAndReturn(window, root, first, second, evidence, shot):
    runtime = window.controller.runtime
    scope = dict(runtime.deviceManager.scope)
    runtime.eventStore.append("__runtime__", "runtime.acceptance", "R3 无任务系统记录",
        projectId=scope["projectId"], level="WARN")
    generation = scope["projectLoadGeneration"]
    for message, eventGeneration in (("R3 设备执行摘要", generation), ("R3 历史加载代次", f"past-{generation}")):
        runtime.eventStore.append("__runtime__", "device.action.failed", message, level="WARN",
            projectId=scope["projectId"], workflowId="main", payload=dict(scope,
            projectLoadGeneration=eventGeneration, jobId=second, deviceId="software-camera"))
    showControls(window)
    click(window.settingsButton)
    assert window.contentStack.currentWidget() is window.startupPage
    click(window.backButton)
    assert window.viewSession.jobId == second and window.pages.currentPageId == "main"
    showControls(window)
    window.logsButton.setFocus(Qt.OtherFocusReason)
    click(window.logsButton)
    logs = window.logView
    assert window.contentStack.currentWidget() is logs and logs.sources.count() == 3
    tab(logs.sources, 1)
    system = logs.systemLogs
    def systemVisible():
        return [[system.table.item(row, column).text() for column in range(system.table.columnCount())
                 if system.table.item(row, column)] for row in range(system.table.rowCount())]
    waitFor(lambda: any("R3 无任务系统记录" in cell for row in systemVisible() for cell in row))
    assert all("R3 设备执行摘要" not in cell for row in systemVisible() for cell in row)
    nativeLocalLogs(window, root, evidence)
    tab(logs.sources, 0)
    jobs = logs.jobLogs
    waitFor(lambda: first in jobs.runs and second in jobs.runs)
    assert all(jobs.runs[job].project_id == scope["projectId"] and jobs.runs[job].status == "RUNNING"
               for job in (first, second))
    assert not any("停止" in item.text() and item.isVisible() for item in jobs.catalog.findChildren(QPushButton))
    openLogJob(jobs, second)
    jobs.mode.setCurrentIndex(1)
    related = currentEvent(jobs, "R3 设备执行摘要")
    assert related.effectiveJobId == second and related.jobId == "__runtime__"
    assert not any(entry.message == "R3 无任务系统记录" for entry in jobs.window.entries)
    click(jobs.detail.copyRecordButton)
    copied = json.loads(QApplication.clipboard().text())
    assert copied["eventId"] == related.eventId and copied["payload"]["jobId"] == second
    shot(window, "04-job-log-workbench")
    window.resize(900, 700)
    waitMs(150)
    # Settle native height-for-width layouts after changing width before
    # requesting the final height, as two independent desktop edge resizes.
    window.resize(900, 700)
    QApplication.processEvents()
    assert window.size().toTuple() == (900, 700), (window.size().toTuple(), window.minimumSizeHint().toTuple(),
        window.minimumSize().toTuple(), window.contentStack.minimumSizeHint().toTuple(),
        logs.minimumSizeHint().toTuple(), jobs.minimumSizeHint().toTuple(), jobs.jobPage.minimumSizeHint().toTuple())
    assert jobs.search.isVisible() and jobs.events.isVisible() and jobs.detail.isVisible()
    assert jobs.detail.tabs.isVisible() and jobs.detail.tabs.count() >= 2
    tab(jobs.detail.tabs.tabBar(), jobs.detail.tabs.indexOf(jobs.detail.rawTree))
    assert jobs.detail.rawTree.topLevelItemCount() > 0 and window.backButton.isVisible()
    shot(window, "06-job-log-900x700")
    window.resize(1060, 800)
    waitMs(150)
    window.resize(1060, 800)
    diagnosticRoundTrip(jobs, window, scope, second, evidence, shot)
    designerObservation(window, root, first, second, evidence, shot)
    click(window.backButton)
    assert window.logsButton.hasFocus()
    evidence["logs"] = dict(jobId=second, associatedEvent=related.eventId, systemOnly=True,
        noJobStopAction=True, productionFocusRestored=True, copiedEventId=copied["eventId"])


def restart(root, nextRoot, path, evidence):
    prefs = preferences(root)
    window = OperatorWindow(preferences=prefs)
    expose(window)
    try:
        waitFor(lambda: window.pages is not None and not window.busy)
        assert not window.error
        assert Path(window.controller.runtime.loadedProjectFile).resolve() == path.resolve()
        assert Path(window.controller.dataRoot).resolve() == nextRoot.resolve() and window.resolution.source == "saved", (
            window.controller.dataRoot, nextRoot, window.resolution.source)
        assert window.explicitProject is None and not window.controller.runtime.jobRepository.all()
        evidence["restartFalse"] = dict(projectId=window.controller.document.project.projectId, jobs=0, remembered=True)
    finally:
        closeWindow(window)
    assert window.controller.closed and window.controller.runtime._closed
    enabled, document = softwareProject(root / "auto-start", pages=False, autoStart=True)
    autoPrefs = preferences(root / "auto-start")
    autoPrefs.setValue(DEFAULT_ROOT_KEY, str(root / "自动启动 Runtime 数据"))
    autoPrefs.sync()
    seed = OperatorWindow(enabled, preferences=autoPrefs)
    expose(seed)
    try:
        waitFor(lambda: (seed.state.get("jobStatus") == "RUNNING" and not seed.busy) or seed.error)
        assert not seed.error and len(seed.controller.runtime.jobRepository.all()) == 1
        seedJob = seed.controller.jobId
    finally:
        closeWindow(seed)
    auto = OperatorWindow(preferences=autoPrefs)
    expose(auto)
    try:
        waitFor(lambda: auto.state.get("jobStatus") == "RUNNING" and not auto.busy)
        jobs = auto.controller.runtime.jobRepository.all()
        assert len(jobs) == 1 and auto.topBar.parentWidget() is auto
        assert auto._controlDock is auto.hostControls
        assert auto.controller.jobId != seedJob and auto.explicitProject is None
        assert Path(auto.controller.runtime.loadedProjectFile).resolve() == enabled.resolve()
        assert auto.controller.document.project.projectId == document.project.projectId and auto.stopButton.isEnabled()
        click(auto.logsButton)
        assert not auto.startButton.isEnabled()
        assert auto.stopButton.isVisible() and auto.stopButton.isEnabled()
        click(auto.backButton)
        assert auto.stopButton.isVisible() and auto.stopButton.isEnabled()
        evidence["restartTrue"] = dict(projectId=document.project.projectId, jobId=auto.controller.jobId, jobs=1,
            seedJobId=seedJob, remembered=True, rootControls=True, rootToolStopAvailable=True)
    finally:
        closeWindow(auto)
    assert auto.controller.closed and auto.controller.runtime._closed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true", help="Validate startup, load, device and restart only.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    assert os.environ.get("QT_QPA_PLATFORM") == "windows"
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    screen = app.primaryScreen()
    evidence = dict(head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        mode="preflight" if args.preflight else "main chain", platform=platform.platform(),
        qpa=app.platformName(), pyside=PySide2.__version__, python=platform.python_version(), grpc=grpc.__version__,
        screen=[screen.size().width(), screen.size().height()], screenshots={})

    def shot(widget, name):
        app.processEvents()
        expected = {"01-startup-settings": (1060, 800), "02-startup-900x700": (900, 700),
            "03-production-review": (1060, 800), "04-job-log-workbench": (1060, 800),
            "06-job-log-900x700": (900, 700)}.get(name)
        assert expected is None or widget.size().toTuple() == expected, (name, widget.size().toTuple())
        pixmap = widget.grab()
        assert pixmap.save(str(args.output / (name + ".png")))
        evidence["screenshots"][name] = dict(logicalSize=[widget.width(), widget.height()],
            pixelSize=[pixmap.width(), pixmap.height()], dpr=pixmap.devicePixelRatio())

    try:
        with TemporaryDirectory(prefix="emo-r3-native-") as directory:
            root = Path(directory)
            path, document = softwareProject(root / "软件检测工程")
            prefs = preferences(root)
            prefs.setValue(DEFAULT_ROOT_KEY, str(root / ("本机运行数据 " + "长目录" * 17)))
            window = OperatorWindow(preferences=prefs)
            expose(window)
            second = None
            try:
                nextRoot = startup(window, root, evidence, shot)
                loadAndReload(window, root, path, document, evidence)
                verifyDevice(window, evidence, shot)
                if not args.preflight:
                    second = runPair(window, root, document, evidence, shot)
            finally:
                closeWindow(window)
            assert window.controller.closed and window.controller.runtime._closed and window.logFeed.idle
            if second:
                assert window.controller.runtime.jobRepository.get(second).isTerminal
            evidence["owningRuntimeClosed"] = True
            restart(root, nextRoot, path, evidence)
        evidence["result"] = "PASS"
    finally:
        (args.output / "native-runtime-r3.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
