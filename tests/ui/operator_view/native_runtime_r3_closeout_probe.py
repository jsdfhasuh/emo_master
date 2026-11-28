"""Windows native QTest closeout walkthrough using the existing R3 software project."""

import argparse
from ctypes import windll
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch

import emo_master  # noqa: F401 - DLL bootstrap
import grpc
from PIL import Image
import PySide2
from PySide2.QtCore import Qt
from PySide2.QtGui import QFont, QFontDatabase, QFontInfo
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QAbstractSlider, QApplication, QInputDialog, QWidget
import win32gui
import win32ui

from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.operator_runtime.settings import DEFAULT_ROOT_KEY
from emo_master.clients.runtime.global_logs import RuntimeGlobalLogs
from tests.ui.operator_view.native_runtime_r3_probe import (
    button, choosePage, chooseProject, chooseTreeItem, click, currentEvent, expose,
    openCompactDeviceDialog, openLogJob, readPage, selectRun, showControls, softwareProject, tab,
)
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_run_view_selection import remoteStart
from tests.ui.operator_view.test_runtime_log_view import waitMs
from tests.ui.operator_view.test_view import waitFor


def capture(widget, output):
    QApplication.processEvents()
    hwnd = int(widget.winId())
    assert win32gui.IsWindowVisible(hwnd) and widget.windowHandle().isExposed()
    screen = widget.windowHandle().screen()
    frame = widget.frameGeometry()
    if win32gui.GetForegroundWindow() == hwnd and screen.geometry().contains(frame):
        pixmap = screen.grabWindow(0, *frame.getRect())
        if not pixmap.isNull():
            assert pixmap.save(str(output))
            with Image.open(output) as image:
                if image.crop((12, 50, image.width - 12, image.height - 12)).getcolors(4) is None:
                    return "screen", image.size

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    windowDC = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(windowDC)
    destination = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    destination.SelectObject(bitmap)
    try:
        assert windll.user32.PrintWindow(hwnd, destination.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT
        image = Image.frombuffer("RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        assert image.crop((12, 50, width - 12, height - 12)).getcolors(4) is None
        image.save(output)
        return "PrintWindow", image.size
    finally:
        destination.DeleteDC()
        win32gui.DeleteObject(bitmap.GetHandle())
        win32gui.ReleaseDC(hwnd, windowDC)


def startup(window, evidence, shot):
    waitFor(lambda: window.controller is not None and not window.busy, seconds=60)
    page = window.startupPage
    assert window.contentStack.currentWidget() is page and not window.startButton.isEnabled()
    families = ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC")
    installed = QFontDatabase().families()
    expected = next((family for family in families if family in installed), QApplication.font().family())
    font = page.allLogsButton.font()
    family = QFontInfo(font).family()
    fontMatches = font.family() == expected and family == QFontInfo(QFont(expected)).family()
    assert page.storage.currentPath.text() == str(window.resolution.path)
    assert page.horizontalScrollBar().maximum() == 0
    listenSave = page.network.saveButton
    assert page.viewport().rect().contains(listenSave.mapTo(page.viewport(), listenSave.rect().center()))
    shot(window, "01-startup")
    window.resize(900, 700)
    QApplication.processEvents()
    window.resize(900, 700)
    assert window.size().toTuple() == (900, 700)
    page.ensureWidgetVisible(page.storage.saveButton)
    save = page.storage.saveButton
    assert page.viewport().rect().contains(save.mapTo(page.viewport(), save.rect().center()))
    assert page.horizontalScrollBar().maximum() == 0
    window.resize(1060, 800)
    page.ensureWidgetVisible(page.allLogsButton)
    click(page.allLogsButton)
    logs = window.logView
    assert window.contentStack.currentWidget() is logs and logs.sources.currentIndex() == 0
    waitFor(lambda: logs.runtimeLogs._delivery is not None)
    click(window.backButton)
    evidence["startupEntry"] = dict(source=logs.sources.tabText(0), preferredFont=expected, resolvedFont=family,
                                    fontMatches=fontMatches, compactSettingsReachable=True)


def startJobs(window, projectPath):
    chooseProject(window, projectPath)
    waitFor(lambda: window.pages is not None and not window.busy, seconds=60)
    assert not window.error and window.contentStack.currentWidget() is window.businessHost
    showControls(window)
    click(window.startButton)
    waitFor(lambda: window.state.get("jobStatus") == "RUNNING" and not window.busy, seconds=60)
    first = window.controller.jobId
    with grpc.insecure_channel(window.controller.address) as observer:
        second = remoteStart(window, observer)
    waitFor(lambda: len(window.state.get("activeJobIds", ())) == 2 and not window.busy, seconds=60)
    selectRun(window, second)
    choosePage(window, "main")
    return first, second, readPage(window, "main", second)


def checkRunningCatalog(workbench, jobIds):
    assert workbench.pages.currentWidget() is workbench.catalog and workbench.directory.isVisible()
    directory = workbench.directory
    assert [directory.topLevelItem(i).text(0) for i in range(directory.topLevelItemCount())] == [
        "运行中", "最近已结束",
    ]

    def runningJobs():
        running = directory.topLevelItem(0)
        return {running.child(i).toolTip(0) for i in range(running.childCount())}

    waitFor(lambda: set(jobIds) <= runningJobs())


def settingsEntry(window, jobs, evidence, shot):
    showControls(window)
    click(window.settingsButton)
    settings = window.startupPage
    assert window.contentStack.currentWidget() is settings
    network = settings.network
    settings.ensureWidgetVisible(network.allowLan)
    assert not network.host.isEnabled() and not network.port.isEnabled()
    network.allowLan.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()
    QTest.keyClick(network.allowLan, Qt.Key_Space)
    checkbox = network.allowLan.isChecked(), network.allowLan.hasFocus(), network.host.isEnabled()
    assert all(checkbox), checkbox
    shot(window, "02-runtime-settings")
    QTest.keyClick(network.allowLan, Qt.Key_Space)
    assert not network.host.isEnabled() and not network.port.isEnabled()
    settings.ensureWidgetVisible(settings.allLogsButton)
    click(settings.allLogsButton)
    logs = window.logView
    assert window.contentStack.currentWidget() is logs and logs.sources.currentIndex() == 0
    workbench = logs.jobLogs
    checkRunningCatalog(workbench, jobs)
    click(workbench.catalogHistory)
    project = "closeout-history-filter"
    QTest.keyClicks(workbench.catalogProject, project)
    workbench.catalogStatus.setCurrentIndex(workbench.catalogStatus.findData("FAILED"))
    click(workbench.catalogDateRange.startControl)
    click(button(workbench.catalog, "刷新"))
    waitFor(lambda: workbench.historyScope.get("projectId") == project)
    assert workbench.historyScope["statuses"] == ("FAILED",) and "startEndedAtMs" in workbench.historyScope
    assert workbench.directory.topLevelItem(0).text(0) == "终态任务"
    click(window.backButton)
    assert window.contentStack.currentWidget() is settings
    settings.ensureWidgetVisible(settings.allLogsButton)
    click(settings.allLogsButton)
    assert window.contentStack.currentWidget() is logs and logs.sources.currentIndex() == 0
    checkRunningCatalog(workbench, jobs)
    assert not workbench.catalogProject.text() and not workbench.catalogStatus.currentData()
    assert workbench.catalogDateRange.bounds() == (None, None)
    sources = logs.sources
    assert tuple(sources.tabText(i) for i in range(sources.count())) == ("运行任务", "Runtime 系统事件", "本机 JSONL")
    right = sources.tabRect(sources.count() - 1).right()
    assert not sources.expanding() and right < sources.width() * .6
    assert sources.tabRect(1).width() > sources.tabRect(0).width()
    evidence["settingsEntry"] = dict(checkedAndFocused=True, disabledFields=True, source=sources.tabText(0),
                                     historyFiltersCleared=True, runningJobsVisible=list(jobs),
                                     sourceTabWidth=right + 1, sourceBarWidth=sources.width())
    shot(window, "03-log-default")


def readerPaused(source, reader, queries):
    waitFor(lambda: not reader.thread.is_alive() and not source.timer.isActive())
    count = len(queries)
    position = source._delivery
    assert position is not None and reader.stop.is_set()
    waitMs(650)  # A normal poll interval is 500 ms.
    assert len(queries) == count and source._delivery == position
    return position, count


def readerResumed(source, oldReader, position, queries):
    waitFor(lambda: source.reader is not None and source.reader is not oldReader
            and any(reader is source.reader for reader, _, _ in queries))
    first = next((instance, cursor) for reader, instance, cursor in queries if reader is source.reader)
    assert first == position, (first, position)
    return first


def checkLogViews(window, first, second, queries, evidence, shot):
    runtime = window.controller.runtime
    scope = dict(runtime.deviceManager.scope)
    logs = window.logView
    jobs, source = logs.jobLogs, logs.runtimeLogs
    message = "模拟相机曝光参数校验告警 · 8000 µs"
    runtime.eventStore.append(first, "node.log", "第一项软件任务读回成功",
        projectId=scope["projectId"], workflowId="main")
    runtime.eventStore.append("__runtime__", "device.action.failed", message, level="WARN",
        projectId=scope["projectId"], workflowId="main", payload=dict(scope, jobId=second, deviceId="software-camera"))
    runtime.eventStore.append("__runtime__", "runtime.acceptance", "软件 Runtime 系统事件",
        projectId=scope["projectId"], level="WARN")
    for job, expected in ((first, "第一项软件任务读回成功"), (second, message)):
        openLogJob(jobs, job)
        jobs.mode.setCurrentIndex(1)
        entry = currentEvent(jobs, expected)
        assert entry.effectiveJobId == job and jobs.detail.isVisible()
    assert entry.projectId == scope["projectId"]
    waitFor(lambda: jobs.events.topLevelItemCount() == 1 and jobs.events.currentItem() is not None)
    selected = jobs.events.visualItemRect(jobs.events.currentItem())
    assert jobs.events.viewport().rect().contains(selected.topLeft())
    assert jobs.events.viewport().rect().contains(selected.bottomLeft())
    shot(window, "04-log-detail")
    window.resize(900, 700)
    QApplication.processEvents()
    window.resize(900, 700)
    assert window.size().toTuple() == (900, 700)
    assert logs.sources.tabRect(2).right() < logs.sources.width() and window.backButton.isVisible()
    assert jobs.search.isVisible() and jobs.events.isVisible() and jobs.detail.isVisible()
    window.resize(1060, 800)
    QApplication.processEvents()
    click(jobs.filterToggle)
    assert jobs.filters.isVisible() and jobs.events.isVisible() and jobs.detail.isVisible()
    # Expanding the panel resizes the native window. Let Windows paint the new
    # backing store before asking for its pixels instead of capturing the old frame.
    waitMs(200)
    shot(window, "05-log-filters")
    click(jobs.clearFilters)
    assert logs.sources.currentIndex() == 0 and jobs._filterValues() == {} and not jobs.search.text()
    click(jobs.filterToggle)
    window.resize(1060, 800)
    currentEvent(jobs, message)

    reader = source.reader
    tab(logs.sources, 1)
    position, queryCount = readerPaused(source, reader, queries)
    system = logs.systemLogs.table
    waitFor(lambda: bool(system.findItems("软件 Runtime 系统事件", Qt.MatchExactly)))
    tab(logs.sources, 2)
    waitFor(lambda: logs.model.rowCount() > 0)
    waitMs(650)
    assert len(queries) == queryCount
    tab(logs.sources, 0)
    switched = readerResumed(source, reader, position, queries)
    assert jobs._detailEntry.eventId == entry.eventId and jobs.search.text() == message
    anchor = jobs.events.verticalScrollBar().value()
    reader = source.reader
    click(window.backButton)
    assert window.contentStack.currentWidget() is window.startupPage
    click(window.backButton)
    assert window.contentStack.currentWidget() is window.businessHost
    position, queryCount = readerPaused(source, reader, queries)
    marker = "返回生产页期间写入的唯一事件"
    received = []
    appendEntry = jobs.appendEntry

    def observeEntry(event):
        if event.message == marker:
            received.append(event.eventId)
        appendEntry(event)

    runtime.eventStore.append(second, "node.log", marker, projectId=scope["projectId"], workflowId="main")
    waitMs(650)
    assert len(queries) == queryCount
    showControls(window)
    with patch.object(jobs, "appendEntry", observeEntry):
        click(window.logsButton)
        assert logs.sources.currentIndex() == 0 and jobs.job.job_id == second
        reopened = readerResumed(source, reader, position, queries)
        waitFor(lambda: received and jobs.window.newCount > 0 and source._delivery[1] >= received[0])
        assert jobs._detailEntry.eventId == entry.eventId and jobs.search.text() == message
        assert jobs.events.verticalScrollBar().value() == anchor
        click(button(jobs.jobPage, "返回最新"))
        waitFor(lambda: not jobs._opening and not jobs.window.paused)
        recovered = currentEvent(jobs, marker)
        assert received == [recovered.eventId] and recovered.eventId > position[1]
        assert sum(row.eventId == recovered.eventId for row in jobs.window.entries) == 1
    evidence["logs"] = dict(jobIds=[first, second], sources=3, clearedFilters=True, retainedReadingPosition=True,
        compactLogActionsReachable=True,
        switchedCursor=switched, reopenedCursor=reopened, selectedEventId=entry.eventId,
        resumedEventId=recovered.eventId, readerDeliveriesForEvent=len(received), hiddenQueries=queryCount)
    click(window.backButton)
    return entry


def verifyDevice(window, jobEvent, evidence, shot):
    dialog, details = openCompactDeviceDialog(window)
    connect = dialog.actions["connect"]
    waitFor(connect.isEnabled)
    details.ensureWidgetVisible(connect)
    assert details.viewport().rect().contains(connect.mapTo(details.viewport(), connect.rect().center()))
    click(connect)
    waitFor(lambda: dialog.deviceHealth.text().startswith("已连接") and dialog.actions["disconnect"].isEnabled())
    diagnostics = dialog.diagnostics
    waitFor(lambda: diagnostics.records.topLevelItemCount() > 0 and diagnostics.runtimeLogsButton.isEnabled())
    details.ensureWidgetVisible(diagnostics.records)
    first = diagnostics.records.topLevelItem(0)
    chooseTreeItem(diagnostics.records, first)
    point = diagnostics.records.visualItemRect(first).center()
    assert details.viewport().rect().contains(diagnostics.records.viewport().mapTo(details.viewport(), point))
    assert diagnostics.details.toPlainText()
    shot(dialog, "06-device-management")
    details.ensureWidgetVisible(diagnostics.runtimeLogsButton)
    assert details.viewport().rect().contains(diagnostics.runtimeLogsButton.mapTo(
        details.viewport(), diagnostics.runtimeLogsButton.rect().center()))
    click(diagnostics.runtimeLogsButton)
    logs = window.logView
    assert not dialog.isVisible() and window.contentStack.currentWidget() is logs
    assert logs.sources.currentIndex() == 2 and logs.deviceId == "software-camera"
    expose(window)
    logs.levelCombo.setCurrentIndex(logs.levelCombo.findData("WARN"))
    click(window.backButton)
    showControls(window)
    click(window.settingsButton)
    window.startupPage.ensureWidgetVisible(window.startupPage.allLogsButton)
    click(window.startupPage.allLogsButton)
    assert logs.sources.currentIndex() == 0 and logs.deviceId == "" and logs.levelCombo.currentIndex() == 0
    jobs = logs.jobLogs
    assert not jobs.search.text()
    checkRunningCatalog(jobs, [jobEvent.effectiveJobId])
    openLogJob(jobs, jobEvent.effectiveJobId)
    jobs.mode.setCurrentIndex(1)
    while jobs.window.firstEventId > jobEvent.eventId:
        previous = jobs.window.firstEventId
        jobs.events.verticalScrollBar().triggerAction(QAbstractSlider.SliderToMinimum)
        waitFor(lambda: jobs.window.firstEventId < previous)
    assert currentEvent(jobs, jobEvent.message).eventId == jobEvent.eventId
    click(jobs.deviceButton)
    assert jobs.pages.currentWidget() is jobs.devicePage
    waitFor(lambda: bool(jobs.devices.page.get("records")))
    assert jobs.devices.page["deviceId"] == "software-camera"
    click(button(jobs.devicePage, "返回事件"))
    assert jobs._detailEntry.eventId == jobEvent.eventId
    click(window.backButton)
    click(window.backButton)
    evidence["device"] = dict(connected=True, diagnosticRecords=diagnostics.records.topLevelItemCount(),
                              explicitLocalSource=True, startupClearsPriorFilters=True,
                              jobReopenedFromCatalog=jobEvent.effectiveJobId,
                              logDiagnosticDevice=jobs.devices.page["deviceId"])


def production(window, first, second, previousPage, evidence, shot):
    assert window.contentStack.currentWidget() is window.businessHost
    assert readPage(window, "main", second) == previousPage
    assert window.viewSession.jobId == second and window.state["jobId"] == first
    header = window.pages.findChild(QWidget, "pageShellHeader")
    assert header is not None and "#f8f5ff" in header.styleSheet()
    assert "#8b5cf6" in window.startButton.styleSheet() and window.startButton.text() == "开始检测"
    shot(window, "07-production")
    selected = []

    def confirm(_parent, title, _label, items, index, _editable):
        assert title == "停止运行" and first in items[index]
        selected.append(items[index])
        return items[index], True

    showControls(window)
    with patch.object(QInputDialog, "getItem", side_effect=confirm):
        click(window.stopButton)
        waitFor(lambda: bool(selected))
    runtime = window.controller.runtime
    waitFor(lambda: runtime.jobRepository.get(first).isTerminal)
    assert runtime.jobRepository.get(first).status == "ABORTED" and runtime.jobRepository.get(second).status == "RUNNING"
    evidence["production"] = dict(customQtStyle=True, viewed=second, stopped=first, otherJobStillRunning=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    assert os.environ.get("QT_QPA_PLATFORM") == "windows"
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    assert app.platformName() == "windows"
    evidence = dict(head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        modified=subprocess.check_output(["git", "status", "--short"], text=True).splitlines(),
        automation="Windows native Qt via QTest", humanDesktopAcceptance=False, platform=platform.platform(),
        qpa=app.platformName(), python=platform.python_version(), pyside=PySide2.__version__, grpc=grpc.__version__,
        pywin32=version("pywin32"), pillow=version("Pillow"), screen=app.primaryScreen().size().toTuple(),
        screenshots={}, result="FAIL")

    def shot(widget, name):
        mode, pixels = capture(widget, args.output / (name + ".png"))
        evidence["screenshots"][name] = dict(method=mode, pixels=pixels, logicalClientSize=widget.size().toTuple())
        print(f"Captured {name} through {mode}", flush=True)

    queries = []
    query = RuntimeGlobalLogs._query

    def observe(reader, request):
        queries.append((reader, request.runtime_instance_id, request.after_event_id))
        return query(reader, request)

    try:
        with TemporaryDirectory(prefix="software-runtime-", dir=args.output.parent) as directory:
            root = Path(directory)
            path, _ = softwareProject(root / "软件检测工程")
            prefs = preferences(root)
            prefs.setValue(DEFAULT_ROOT_KEY, str(root / ("本机运行数据 " + "长目录" * 17)))
            with patch.object(RuntimeGlobalLogs, "_query", observe):
                window = OperatorWindow(preferences=prefs)
                window.resize(1060, 800)
                expose(window)
                try:
                    startup(window, evidence, shot)
                    first, second, page = startJobs(window, path)
                    settingsEntry(window, (first, second), evidence, shot)
                    event = checkLogViews(window, first, second, queries, evidence, shot)
                    verifyDevice(window, event, evidence, shot)
                    production(window, first, second, page, evidence, shot)
                finally:
                    closeWindow(window)
            assert window.controller.closed and window.controller.runtime._closed and window.logView.idle
        assert evidence["startupEntry"]["fontMatches"], evidence["startupEntry"]
        evidence["result"] = "PASS"
    finally:
        (args.output / "native-runtime-r3-closeout.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
