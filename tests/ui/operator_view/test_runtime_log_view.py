import threading
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QEventLoop, QTimer, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QAbstractItemView

from emo_master.apps.operator_runtime.log_reader import LogRecord, MAX_RECORDS, ReadCursor, ReadResult
from emo_master.apps.operator_runtime.log_view import LogFeed, RuntimeLogView
from emo_master.apps.operator_runtime.main import OperatorWindow
from tests.runtime.current_production_fixture import currentProductionProject
from tests.runtime.test_operator_runtime_logs import line
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def fixtureFeed(tmp_path):
    path = tmp_path / "runtime-synthetic.jsonl"
    feed = LogFeed(tmp_path)
    feed.writer = SimpleNamespace(directory=tmp_path, activePath=path, enabled=True, failureMessage="")
    return feed, path


def closeFeed(feed):
    feed.beginClose()
    waitFor(lambda: feed.worker is None)


def waitMs(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec_()


def testRealLogLevelsDetailsPlainTextEmptyFilterAndSelectionPreservation(qtApp, tmp_path):
    feed, path = fixtureFeed(tmp_path)
    path.write_bytes(b"".join(line("<b>真实消息</b>", level, projectId="record-project", workflowId="historic-flow",
        nodeId="historic-node", jobId="record-job", payload={"workflowName": "记录中的流程", "password": "do-not-display"})
        for level in ("ERROR", "WARN", "INFO", "DEBUG")))
    view = RuntimeLogView(feed)
    view.show()
    feed.setVisible(True)
    try:
        waitFor(lambda: view.table.rowCount() == 4)
        assert path.name in view.statusLabel.text()
        for index in range(1, 5):
            view.levelCombo.setCurrentIndex(index)
            assert view.table.rowCount() == 1
        view.table.selectRow(0)
        assert "E_SYNTHETIC" in view.detail.toPlainText()
        assert "historic-node" in view.detail.toPlainText() and "record-job" in view.detail.toPlainText()
        assert "<b>真实消息</b>" in view.detail.toPlainText()
        assert "do-not-display" not in view.raw.toPlainText()
        view.followLatest.setChecked(False)
        selected = feed.records[-1].identity
        with path.open("ab") as handle:
            handle.write(line("new debug", "DEBUG"))
        feed.refresh()
        waitFor(lambda: view.table.rowCount() == 2)
        assert view._shownRecords[view.table.currentRow()].identity == selected
        view.levelCombo.setCurrentIndex(1)
        feed.records = [row for row in feed.records if row.data["level"] != "ERROR"]
        feed.updated.emit()
        assert not view.table.rowCount() and "无匹配" in view.emptyLabel.text()
        QTest.mouseClick(view.resetButton, Qt.LeftButton)
        assert view.levelCombo.currentIndex() == 0 and view.table.rowCount() == 4
    finally:
        closeFeed(feed)


def testRotationFollowsActiveButHistoryStaysSelectedAndReadFailureCanRefresh(qtApp, tmp_path):
    feed, first = fixtureFeed(tmp_path)
    first.write_bytes(line("first"))
    view = RuntimeLogView(feed)
    view.show()
    feed.setVisible(True)
    try:
        waitFor(lambda: len(feed.records) == 1)
        second = tmp_path / "runtime-second.jsonl"
        second.write_bytes(line("second"))
        feed.writer.activePath = second
        feed.refresh()
        waitFor(lambda: feed.records and feed.records[0].data["message"] == "second")
        feed.selectFile(first)
        waitFor(lambda: feed.records and feed.records[0].data["message"] == "first")
        third = tmp_path / "runtime-third.jsonl"
        third.write_bytes(line("third"))
        feed.writer.activePath = third
        feed.refresh()
        waitFor(lambda: feed.worker is None)
        assert feed.historyPath == first and feed.records[0].data["message"] == "first"
        assert "历史文件（不是本次故障）" in view.statusLabel.text()
        missing = tmp_path / "runtime-missing.jsonl"
        feed.selectFile(missing)
        waitFor(lambda: bool(feed.error))
        assert "读取失败" in view.statusLabel.text()
        missing.write_bytes(line("retry success"))
        feed.refresh()
        waitFor(lambda: feed.records and feed.records[0].data["message"] == "retry success")
        assert not feed.error
    finally:
        closeFeed(feed)


def testSingleFlightDiscardsOldSelectionAndHiddenFeedStopsNewWork(qtApp, tmp_path, monkeypatch):
    feed, first = fixtureFeed(tmp_path)
    first.write_bytes(line("old"))
    second = tmp_path / "runtime-second.jsonl"
    second.write_bytes(line("selected"))
    entered, release = threading.Event(), threading.Event()
    realRead = feed.reader.read
    calls, workerThreads = [], []

    def delayed(path, cursor):
        calls.append(path)
        workerThreads.append(threading.get_ident())
        if path == first:
            entered.set()
            assert release.wait(5)
        return realRead(path, cursor)

    monkeypatch.setattr(feed.reader, "read", delayed)
    feed.setVisible(True)
    try:
        waitFor(entered.is_set)
        for _ in range(5):
            feed.refresh()
        assert calls == [first]
        feed.selectFile(second)
        release.set()
        waitFor(lambda: feed.records and feed.records[0].data["message"] == "selected")
        assert all(identity != threading.get_ident() for identity in workerThreads)
        feed.setVisible(False)
        waitFor(lambda: feed.worker is None)
        count = len(calls)
        feed.refresh()
        waitMs(1100)
        assert len(calls) == count and not feed.timer.isActive()
    finally:
        release.set()
        closeFeed(feed)


def testWriterBecomingReadyPreservesHistoryChoiceAndComboMatchesFeed(qtApp, tmp_path):
    history = tmp_path / "runtime-history.jsonl"
    history.write_bytes(line("historical record"))
    feed = LogFeed(tmp_path)
    view = RuntimeLogView(feed)
    feed.setVisible(True)
    try:
        feed.selectFile(history)
        waitFor(lambda: bool(feed.records) and feed.worker is None)
        cursor, record = feed.cursor, feed.records[0]
        writer = SimpleNamespace(directory=tmp_path, activePath=None, enabled=True, failureMessage="")
        feed.setSource(SimpleNamespace(runtime=SimpleNamespace(operationalLogWriter=writer)))
        assert feed.historyPath == history and feed.cursor == cursor and feed.records[0] is record
        assert view.fileCombo.currentData() == str(history)
        feed.selectFile(None)
        assert view.fileCombo.currentData() is None
    finally:
        closeFeed(feed)


def testViewCapAndFileOffsetKeepRepeatedTrueErrors(qtApp, tmp_path):
    feed, path = fixtureFeed(tmp_path)
    rows = tuple(LogRecord(path, index, {"message": "repeated error", "level": "ERROR"}) for index in range(MAX_RECORDS + 5))
    feed._accept((feed.generation, path, (path,), ReadResult(ReadCursor(path, 1), rows, True), ""))
    assert len(feed.records) == MAX_RECORDS and feed.records[0].offset == 5 and feed.recentOnly
    feed.beginClose()


@pytest.mark.parametrize("scrollMode", [QAbstractItemView.ScrollPerItem, QAbstractItemView.ScrollPerPixel],
                         ids=["item-scroll", "pixel-scroll"])
def testViewCapPreservesReadingAnchorAndSelectionWhileAppending(qtApp, tmp_path, scrollMode):
    feed, path = fixtureFeed(tmp_path)
    view = RuntimeLogView(feed)
    view.table.setVerticalScrollMode(scrollMode)
    view.resize(1060, 800)
    view.show()
    view.followLatest.setChecked(False)

    def append(start, count, *, reset=False):
        rows = tuple(LogRecord(path, index, {"message": f"record-{index}", "level": "INFO"})
                     for index in range(start, start + count))
        feed._accept((feed.generation, path, (path,),
                      ReadResult(ReadCursor(path, start + count), rows, reset), ""))
        qtApp.processEvents()

    def topRecord():
        row = view.table.rowAt(0)
        assert row >= 0
        return view._shownRecords[row].identity, view.table.rowViewportPosition(row)

    try:
        append(0, MAX_RECORDS, reset=True)
        view.table.selectRow(3505)
        view.table.scrollToItem(view.table.item(3500, 0), QAbstractItemView.PositionAtTop)
        if scrollMode == QAbstractItemView.ScrollPerPixel:
            view.table.verticalScrollBar().setValue(view.table.verticalScrollBar().value() + 11)
        qtApp.processEvents()
        anchor = topRecord()
        selected = view._shownRecords[view.table.currentRow()].identity
        detail = view.detail.toPlainText()
        assert anchor[0] == (str(path), 3500)
        assert view.table.visualItemRect(view.table.item(3505, 0)).intersects(view.table.viewport().rect())

        append(MAX_RECORDS, 200)
        assert len(feed.records) == MAX_RECORDS and feed.records[0].offset == 200
        assert topRecord() == anchor
        assert view._shownRecords[view.table.currentRow()].identity == selected
        assert view.table.visualItemRect(view.table.item(view.table.currentRow(), 0)).intersects(view.table.viewport().rect())
        assert view.detail.toPlainText() == detail

        # Once the anchor itself expires, show the earliest retained record.
        view.table.selectRow(10)
        view.table.scrollToItem(view.table.item(5, 0), QAbstractItemView.PositionAtTop)
        append(MAX_RECORDS + 200, 200)
        assert topRecord() == ((str(path), 400), 0)
        assert view.table.currentRow() == -1
        assert not view.raw.toPlainText()

        view.followLatest.setChecked(True)
        append(MAX_RECORDS + 400, 200)
        assert view.table.verticalScrollBar().value() == view.table.verticalScrollBar().maximum()
        assert len(feed.records) == MAX_RECORDS
    finally:
        closeFeed(feed)
        view.close()


def testOpenFolderUsesActualCurrentDirectoryAndReportsMissingOrSystemFailure(qtApp, tmp_path, monkeypatch):
    from emo_master.apps.operator_runtime import log_view
    feed = LogFeed(tmp_path / "中文 logs")
    view = RuntimeLogView(feed)
    calls = []
    monkeypatch.setattr(log_view.QDesktopServices, "openUrl", lambda url: calls.append(url.toLocalFile()) or False)
    assert not view.openFolder() and not calls
    assert str(feed.directory) in view.folderFeedback.text()
    feed.directory.mkdir()
    assert not view.openFolder() and calls == [str(feed.directory).replace("\\", "/")]
    assert "打开失败" in view.folderFeedback.text() and "已请求" not in view.folderFeedback.text()
    monkeypatch.setattr(log_view.QDesktopServices, "openUrl", lambda url: True)
    assert view.openFolder() and "已请求系统打开" in view.folderFeedback.text()
    feed.beginClose()


def testContinuousBusinessPageSessionJobAndDraftSurviveTools(qtApp, tmp_path):
    project, _document = currentProductionProject(tmp_path / "image-project", mode="continuous", autoStart=True)
    window = OperatorWindow(project, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages and window.pages.displayed or window.error)
        assert not window.error
        waitFor(lambda: not window.busy and window.state["state"] == "RUNNING")
        job, pages, session, hub = window.controller.jobId, window.pages, window.session, window.hub
        window.showSettings()
        panel = window.startupPage.storage
        panel.nextEdit.setText(str(tmp_path / "unsaved"))
        window.showLogs()
        window.logView.levelCombo.setCurrentIndex(1)
        waitFor(lambda: bool(window.logFeed.records))
        window.backFromTool()
        assert window.contentStack.currentWidget() is window.startupPage and "未保存" in panel.feedback.text()
        assert panel.save()  # Saving remains available while running.
        window.backFromTool()
        assert window.contentStack.currentWidget() is window.businessHost
        assert (window.controller.jobId, window.pages, window.session, window.hub) == (job, pages, session, hub)
        assert not session.stop.is_set() and window.stopButton.isEnabled() and not window.openButton.isEnabled()
        assert window.controller.runtime.operationalLogWriter.directory == tmp_path / "data/logs"
        window.showLogs()
        assert window.logView.levelCombo.currentIndex() == 1
        window.showSettings()
        QTest.mouseClick(window.startupPage.allLogsButton, Qt.LeftButton)
        assert window.logView.levelCombo.currentIndex() == 0
        window.stopDetection()
        waitFor(lambda: not window.busy and window.state["canStart"])
        assert window.controller.jobId == job
    finally:
        closeWindow(window)


def testParallelWorkflowToolsKeepEveryJobPageHubAndSession(qtApp, tmp_path):
    from copy import deepcopy
    from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
    from emo_master.core.project.models import ProjectDocument
    from emo_master.core.project.test_delivery import trustedRegistry
    from emo_master.core.project.runtime_directory import prepareRuntimeProject
    from emo_master.core.workflow.compiler import WorkflowCompiler

    project, document = currentProductionProject(tmp_path / "parallel-images", mode="continuous", autoStart=True)
    payload = document.model_dump(mode="json")
    payload["workflows"]["second"] = deepcopy(payload["workflows"]["main"])
    payload["workflows"]["second"]["nodes"][2]["params"]["outputPath"] = "outputs/second.png"
    payload["workflowOrder"].append("second")
    payload["runtime"]["startupWorkflowIds"] = ["main", "second"]
    presentation = payload["presentation"]
    presentation["resultScopes"]["second-root"] = dict(entryWorkflowId="second", scopeWorkflowId="second")
    presentation["dataSources"]["second-image"] = dict(kind="node_output", resultScopeId="second-root", workflowId="second",
        nodeId="load", port="image", expectedType="image")
    presentation["pages"]["second"] = dict(name="Second image", components=[
        dict(componentId="image2", type="image", bindings={"image": "second-image"})])
    presentation["pageOrder"].append("second")
    document = ProjectDocument.model_validate(payload)
    saveProject(project, document.model_dump(mode="json"))
    assert loadProjectDocument(project) == document
    registry = trustedRegistry()
    WorkflowCompiler(registry).compile(prepareRuntimeProject(document, project.parent, registry))
    window = OperatorWindow(project, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: len(window.workflowPages) == 2 or window.error)
        assert not window.error
        for index, key in enumerate(("main", "second")):
            window.workflowTabs.setCurrentIndex(index)
            waitFor(lambda: bool(window.workflowPages[key].displayed))
        waitFor(lambda: not window.busy and all(row["state"] == "RUNNING" for row in window.state["workflows"].values()))
        jobs = {key: owned.jobId for key, owned in window.controller.sessions.items()}
        pages, sessions, hubs = dict(window.workflowPages), dict(window.workflowSessions), dict(window.workflowHubs)
        window.workflowTabs.setCurrentIndex(1)
        window.showSettings()
        window.startupPage.storage.nextEdit.setText(str(tmp_path / "draft"))
        window.showLogs()
        window.backFromTool()
        window.backFromTool()
        assert window.contentStack.currentWidget() is window.businessHost and window.workflowTabs.currentIndex() == 1
        assert window.workflowPages == pages and window.workflowSessions == sessions and window.workflowHubs == hubs
        assert {key: owned.jobId for key, owned in window.controller.sessions.items()} == jobs
        assert window.stopButton.isEnabled()
        assert all(not session.stop.is_set() for session in sessions.values())
    finally:
        closeWindow(window)


def testUnavailableWriterShowsRealFailureAndWindowOnlyDiagnostics(qtApp, tmp_path):
    target = tmp_path / "data"
    target.mkdir()
    (target / "logs").write_text("path is a file")
    window = OperatorWindow(dataRoot=target, preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.controller is not None and not window.busy)
        window.showLogs()
        waitFor(lambda: window.logFeed.worker is None)
        writer = window.controller.runtime.operationalLogWriter
        assert not writer.enabled and writer.failureMessage and writer.activePath is None
        assert "日志 writer：不可用" in window.logView.statusLabel.text()
        assert "未写入日志文件" in window.logView.diagnostics.toPlainText()
        assert not window.controller.runtime.jobRepository.all()
        assert not window.logView.openFolder() and "打开失败" in window.logView.folderFeedback.text()
    finally:
        closeWindow(window)


def testLogsOpenedDuringInitialLoadCanReturnToNewBusinessPage(qtApp, tmp_path, monkeypatch):
    from emo_master.apps.operator_runtime.controller import ProductionRuntime
    project, _document = currentProductionProject(tmp_path / "image-project")
    entered, release = threading.Event(), threading.Event()
    load = ProductionRuntime.load

    def delayed(owner, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return load(owner, *args, **kwargs)

    monkeypatch.setattr(ProductionRuntime, "load", delayed)
    window = OperatorWindow(project, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(entered.is_set)
        window.showLogs()
        release.set()
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        assert not window.error and window.contentStack.currentWidget() is window.logView
        assert window.businessHost in window.toolHistory
        window.backFromTool()
        assert window.contentStack.currentWidget() is window.startupPage
        window.backFromTool()
        assert window.contentStack.currentWidget() is window.businessHost
        assert window.pages is not None and not window.controller.runtime.jobRepository.all()
    finally:
        release.set()
        closeWindow(window)


def testCloseWaitsForInFlightBoundedReaderWithoutLeavingThread(qtApp, tmp_path, monkeypatch):
    window = OperatorWindow(dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    entered, release = threading.Event(), threading.Event()
    try:
        waitFor(lambda: not window.busy and window.logFeed.worker is None and bool(window.logFeed.records))
        read = window.logFeed.reader.read

        def delayed(*args):
            entered.set()
            assert release.wait(5)
            return read(*args)

        monkeypatch.setattr(window.logFeed.reader, "read", delayed)
        window.logFeed.refresh()
        waitFor(entered.is_set)
        window.close()
        waitMs(150)
        assert window.closing and not window.done and not window.logFeed.timer.isActive()
        release.set()
        waitFor(lambda: window.done)
        assert window.logFeed.idle and window.controller.closed
    finally:
        release.set()
        closeWindow(window)
