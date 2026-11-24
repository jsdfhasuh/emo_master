"""Native Qt acceptance against a real software Runtime and simulated camera."""
import json
import os
from pathlib import Path
from threading import Event
from uuid import uuid4

import grpc

from PySide2.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide2.QtGui import QMouseEvent, QWheelEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QAbstractItemView, QPushButton

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.business_sqlite.backend import initialize
from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup, Slot
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.models import JobRecord, nowMs
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.integer_add.operator import IntegerAddOperator
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator
from tests.designer.qt_wait import waitUntil
from tests.designer.test_runtime_log_usability import Settings
from tests.runtime.test_loop_cycle_configuration import loopProject


def _click(button):
    assert button.isVisible() and button.isEnabled(), button.text()
    QTest.mouseClick(button, Qt.LeftButton)


def scrollDownToEnd(tree):
    viewport = tree.viewport()
    assert viewport.isVisible()
    point = viewport.rect().center()
    bar = tree.verticalScrollBar()
    while True:
        previous = bar.value()
        atEnd = previous == bar.maximum()
        event = QWheelEvent(point, viewport.mapToGlobal(point), QPoint(), QPoint(0, -120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QCoreApplication.sendEvent(viewport, event)
        if atEnd:
            return
        assert bar.value() > previous


def _capture(dialog, name):
    target = os.environ.get("EMO_LOG_WORKBENCH_EVIDENCE_DIR")
    if target:
        directory = Path(target)
        directory.mkdir(parents=True, exist_ok=True)
        QCoreApplication.processEvents()
        assert dialog.grab().save(str(directory / name))


def _project(path, service):
    payload = loopProject()
    camera = payload["devices"]["definitions"]["camera"]
    camera.setdefault("policies", {})["operationTimeoutMs"] = 30000
    body = payload["workflows"]["body"]
    body["nodes"].extend([
        dict(nodeId="sum", operatorId=IntegerAddOperator.meta.operatorId, params=dict(leftValue=2, rightValue=3)),
        dict(nodeId="record", operatorId=SqliteWriterOperator.meta.operatorId, params=dict(
            configVersion=1, databasePath="logs/records.sqlite3", table="records", failurePolicy="stop",
            mappings=[dict(column="value", storageType="INTEGER", missing="error",
                source=dict(kind="node_output", nodeId="sum", port="value"))])),
    ])
    payload["dependencies"]["operators"] = [
        dict(operatorId=item.operatorId, version=item.version)
        for item in (IntegerAddOperator.meta, SqliteWriterOperator.meta)
    ]
    body["name"] = "采图、求和与记录"
    payload["workflows"]["main"]["name"] = "软件 While 走查"
    document = ProjectDocument.model_validate(payload)
    (path.parent / "logs").mkdir()
    initialize(path.parent / "logs/records.sqlite3", "records",
               [dict(name="value", storageType="INTEGER", nullable=False)])
    saveProject(path, document.model_dump(mode="json"))
    saved = loadProjectDocument(path)
    assert saved == document and saved.schemaVersion == "2.10" and saved.presentation.schemaVersion == "1.3"
    scan = service.pluginScanResult
    WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes).compile(saved)
    return document.project.projectId


def _selectEntry(workbench, entry):
    item = workbench._items[("event", entry.eventId)]
    parent = item.parent()
    while parent is not None:
        parent.setExpanded(True)
        parent = parent.parent()
    tree = workbench.events
    tree.scrollToItem(item)
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualItemRect(item).center())
    assert workbench._detailEntry.eventId == entry.eventId


def _runDebugFromDesignerAndReturn(window, jobId):
    from shiboken2 import isValid

    window.navigateToWorkflow("body", "value")
    window.openNodeParamDialog("value")
    editor = window.nodeParamDialog
    assert editor.key.workflowId == "body" and editor.key.nodeId == "value"
    try:
        _click(editor._debugButton)
        debug = editor._debugWindow
        waitUntil(lambda: debug.run.isEnabled() or debug.state == "FAULTED", timeout=12)
        assert debug.run.isEnabled(), debug.status.text()
        _click(debug.run)
        waitUntil(lambda: len(debug.records) == 1 and debug.run.isEnabled(), timeout=12)
        firstId = debug.records[0]["executionId"]
        assert debug.records[0]["outputs"] == {"value": 12}
        editor._schemaForm.setSchema(editor.context.paramSchema, {"value": 9})
        _click(debug.run)
        waitUntil(lambda: len(debug.records) == 2 and debug.run.isEnabled(), timeout=12)
        assert debug.records[0]["outputs"] == {"value": 9}
        debug.tabs.setCurrentIndex(3)
        finished = next(debug.logs.item(i) for i in range(debug.logs.count()) if
                        debug.logs.item(i).data(Qt.UserRole).get("type") == "execution.finished"
                        and debug.logs.item(i).data(Qt.UserRole).get("executionId") == firstId)
        debug.logs.setCurrentItem(finished)
        _click(debug.logResult)
        raw = json.loads(debug.resultDetail.rawTree.toPlainText())
        assert raw["executionId"] == firstId and raw["sessionId"] == debug.debugSessionId
        assert raw["sourceIdentity"]["generation"] == debug.debugGeneration
        assert raw["outputs"] == {"value": 12} and "jobId" not in raw
        _capture(debug, "q1-operator-debug-result.png")
        debug.resultDetail.tabs.setCurrentIndex(1)
        _capture(debug, "q1-operator-debug-raw.png")
        _click(debug.end)
        waitUntil(lambda: not isValid(debug), timeout=12)
    finally:
        editor.forceClose()
    window.openLogDialog()
    assert window.logDialog.isVisible() and window.logDialog.jobLogs.job.job_id == jobId


def _walkRelatedJobs(dialog, service, client, projectId):
    """Use existing run-group snapshots to make the overlapping statuses deterministic."""
    workbench = dialog.jobLogs
    source, detection, storage, independent = (str(uuid4()) for _ in range(4))
    started = nowMs() - 120000
    revision = service.loadedDocument.project.revision
    records = service.jobRepository
    records.create(JobRecord(source, projectId, revision, "main", acceptedAtMs=started))
    records.update(source, status="COMPLETED", startedAtMs=started, endedAtMs=started + 20000)
    for index in range(20):
        other = f"q1-recent-{index}"
        service.sqliteStore.insertJob(other, projectId, workflowId="recent-fixture")
        service.sqliteStore.updateJobStatus(other, "COMPLETED", endedAtMs=started + 21000 + index)
    for jobId, status in ((detection, "RUNNING"), (storage, "STOPPING"), (independent, "RUNNING")):
        records.create(JobRecord(jobId, projectId, revision, "body", status=status,
                                 startedAtMs=started + 19000))
    group = RunGroup(source, {"detect": Slot("body", 1, detection), "save": Slot("body", 1, storage)},
                     service.deviceManager, service.loadedDocument)
    records.update(independent, runGroupId="another-group", sourceJobId=source)
    with service.jobSupervisor._lock:
        service.deviceManager.dispatchGroups.update((jobId, group) for jobId in group.members)

    def catalogItems(section):
        root, = workbench.directory.findItems(section, Qt.MatchExactly)
        items = {}
        for index in range(root.childCount()):
            item = root.child(index)
            jobId = item.toolTip(0)
            if jobId:
                items[jobId] = item
        return items

    def topTimelineKey():
        item = workbench.events.itemAt(0, 1)
        assert item is not None
        return item.data(0, Qt.UserRole)

    def scrollTimelineTo(row):
        QCoreApplication.processEvents()
        tree = workbench.events
        target = tree.topLevelItem(row)
        tree.scrollToItem(target, QAbstractItemView.PositionAtTop)
        scroll = tree.verticalScrollBar()
        assert scroll.minimum() < scroll.value() < scroll.maximum()
        key = topTimelineKey()
        assert key == target.data(0, Qt.UserRole)
        return key

    try:
        sourceRun, loopRun = (uuid4().hex for _ in range(2))
        iteration = dict(projectId=projectId, workflowId="main", workflowRunId=sourceRun, nodeId="source-marker",
                         nodeRunId=loopRun, iterationPath=(0,))
        service.eventStore.append(source, "loop.iteration.started", "关联往返夹具第 1 轮",
                                  timestampMs=started + 900, payload={"iteration": 0}, **iteration)
        for index in range(56):
            service.eventStore.append(source, "node.log", f"关联往返滚动夹具 {index + 1:02d}", level="WARN",
                                      timestampMs=started + 920 + index * 10, **iteration)
        service.eventStore.append(source, "node.log", "关联往返定位夹具", level="WARN",
                                  timestampMs=started + 1155, payload={"data": {"sample": 42}}, **iteration)
        service.eventStore.append(source, "node.log", "本轮 INFO 记录夹具", level="INFO",
                                  timestampMs=started + 1205, payload={"data": {"sample": 43}}, **iteration)
        service.eventStore.append(detection, "node.log", "独立检测任务夹具", projectId=projectId,
                                 workflowId="body", timestampMs=started + 21000)
        back = next(button for button in workbench.jobPage.findChildren(QPushButton)
                    if button.text() == "返回运行任务")
        _click(back)
        waitUntil(lambda: source in workbench.runs and workbench.pending is None, timeout=10)
        active = catalogItems("运行中")
        ended = catalogItems("最近已结束")
        assert set(active) >= {detection, storage, independent} and source not in active
        assert active[detection].text(2) == "运行中" and active[storage].text(2) == "停止中"
        assert active[detection].text(0) == active[independent].text(0)
        item = ended[source]
        assert item.text(2) == "已完成" and "还有 2 个关联任务未结束" in item.text(5)
        assert source not in {row.job_id for row in workbench.recent}
        workbench.directory.setCurrentItem(item)
        state = client.getRunState(service.runtimeInstanceId, projectId, source)
        assert state.ok and state.run.active and state.run.run_group_id == group.groupId
        assert "stop" in state.run.allowed_actions and workbench.stopButton.isVisible()
        assert workbench.catalogRelatedButton.isVisible()
        _capture(dialog, "q1-task-catalog.png")
        _click(workbench.openButton)
        waitUntil(lambda: workbench._upper is not None and not workbench._opening, timeout=10)
        terminalTitle = workbench.jobTitle.text()
        assert "已完成" in terminalTitle and "20" in terminalTitle

        workbench.warning.setChecked(True)
        workbench.search.setText("关联往返")
        _click(workbench.filterToggle)
        _click(workbench.technicalToggle)
        workbench.inputs["nodeId"].setText("source-marker")
        _click(workbench.technicalToggle)
        _click(workbench.filterToggle)
        workbench.mode.setCurrentIndex(1)
        warning, = [entry for entry in workbench.window.entries if entry.message == "关联往返定位夹具"]
        info, = [entry for entry in workbench.window.entries if entry.message == "本轮 INFO 记录夹具"]
        waitUntil(lambda: ("event", warning.eventId) in workbench._items)
        assert ("event", info.eventId) not in workbench._items
        _selectEntry(workbench, warning)
        originalTop = scrollTimelineTo(4)
        _click(workbench.readContext)
        assert "本轮已加载上下文" in workbench.rangeStatus.text()
        _selectEntry(workbench, info)
        contextTop = scrollTimelineTo(9)
        assert originalTop != contextTop
        assert contextTop != ("event", info.eventId) and originalTop != ("event", warning.eventId)
        _click(workbench.relatedButton)
        assert workbench.pages.currentWidget() is workbench.relatedPage
        related = {workbench.relatedJobs.topLevelItem(i).toolTip(1): workbench.relatedJobs.topLevelItem(i)
                   for i in range(workbench.relatedJobs.topLevelItemCount())}
        assert set(related) == {detection, storage}
        assert related[detection].text(2) == "运行中" and related[storage].text(2) == "停止中"
        _capture(dialog, "q1-related-jobs.png")
        workbench.relatedJobs.setCurrentItem(related[detection])
        _click(workbench.openRelatedButton)
        waitUntil(lambda: workbench.job.job_id == detection and workbench._upper is not None
                  and not workbench._opening, timeout=10)
        assert any(entry.message == "独立检测任务夹具" for entry in workbench.window.entries)
        _click(workbench.returnSourceButton)
        waitUntil(lambda: workbench.job.job_id == source and not workbench._opening
                  and workbench._detailEntry is not None, timeout=10)
        assert workbench.mode.currentIndex() == 1 and workbench.warning.isChecked()
        assert workbench.search.text() == "关联往返" and workbench.inputs["nodeId"].text() == "source-marker"
        assert "本轮已加载上下文" in workbench.rangeStatus.text()
        assert workbench._detailEntry.eventId == info.eventId and workbench.jobTitle.text() == terminalTitle
        assert workbench.events.currentItem().data(0, Qt.UserRole) == ("event", info.eventId)
        assert json.loads(workbench.detail.rawTree.toPlainText())["eventId"] == info.eventId
        waitUntil(lambda: workbench.drawer.isVisible() and workbench.drawer.width() >= workbench.drawer.minimumWidth())
        QCoreApplication.processEvents()
        assert topTimelineKey() == contextTop
        _capture(dialog, "q1-related-return.png")
        _click(workbench.returnContext)
        assert ("event", info.eventId) not in workbench._items
        assert workbench.events.currentItem().data(0, Qt.UserRole) == ("event", warning.eventId)
        assert workbench._detailEntry.eventId == warning.eventId
        assert json.loads(workbench.detail.rawTree.toPlainText())["eventId"] == warning.eventId
        assert topTimelineKey() == originalTop
        _capture(dialog, "q1-related-return-to-filter.png")
        _click(back)
        waitUntil(lambda: workbench.pending is None, timeout=10)
        assert workbench.directory.currentItem().toolTip(0) == source
        records.update(detection, status="COMPLETED", endedAtMs=nowMs())
        records.update(storage, status="ABORTED", endedAtMs=nowMs())
        workbench._refreshCatalog()
        waitUntil(lambda: source not in workbench.runs and workbench.pending is None, timeout=10)
        assert source not in catalogItems("最近已结束")
        print(f"Q1 related fixture source {source} · members {detection}, {storage} · restored INFO {info.eventId}"
              f" at top {contextTop[1]} · returned to WARN {warning.eventId} at top {originalTop[1]}")
    finally:
        records.update(independent, status="ABORTED", endedAtMs=nowMs())
        with service.jobSupervisor._lock:
            for jobId in group.members:
                service.deviceManager.dispatchGroups.pop(jobId, None)


def testNativeWhileLogsWarningDeviceReturnAndTimeline(ownedDesignerWindow, tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs",
                             logDirectory=tmp_path / "runtime-logs")
    server = channel = window = None
    first, releaseFirst = Event(), Event()
    second, releaseSecond = Event(), Event()
    try:
        path = tmp_path / "software-while.emoproj"
        projectId = _project(path, service)
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        driver = service.deviceManager.drivers["camera"]
        execute = driver.execute

        def controlledCapture(*args, **kwargs):
            if driver.sequence == 0:
                first.set()
                assert releaseFirst.wait(25)
            elif driver.sequence == 1:
                context = kwargs["operationContext"]
                driver.runtimeContext.events("device.action.retry", "软件相机测试告警", level="WARN",
                    code="Q1_SOFTWARE_WARN", action="capture", jobId=context.jobId, source=context.source,
                    requestId=context.requestId, channelId="software-camera", dataHex="E4BDA0E5A5BD")
                second.set()
                assert releaseSecond.wait(25)
            return execute(*args, **kwargs)

        driver.execute = controlledCapture
        server, port, _ = createRuntimeServer(port=0, runtimeService=service)
        server.start()
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        grpc.channel_ready_future(channel).result(timeout=5)
        client = RuntimeClient(rpc.RuntimeServiceStub(channel))
        window = ownedDesignerWindow(client, settingsStore=Settings(), localLogDirectory=tmp_path / "designer-logs")
        assert window.loadProjectDirectory(str(path), confirmed=True)
        assert window._currentProjectId() == projectId and "body" in window.workflowStore.workflows
        window.show()
        window.openLogDialog()
        dialog = window.logDialog
        dialog.resize(1400, 860)
        workbench = dialog.jobLogs
        assert [dialog.sources.tabText(i) for i in range(dialog.sources.count())] == [
            "Runtime 运行日志", "Designer 本机日志"]
        demo = {"模拟场景", "YOLO 执行摘要", "整数加法", "SQLite 主动日志", "单算子调试", "通用数据演示"}
        assert not demo.intersection(button.text() for button in dialog.findChildren(QPushButton))
        waitUntil(lambda: workbench.watermark is not None and workbench.capabilities is not None, timeout=10)

        started = client.startJob(projectId, "main", inputs={"count": 0})
        assert started.ok, started.message
        waitUntil(first.is_set, timeout=10)
        jobId = started.job_id
        # Populate an earlier page without spending hours in the test. The loop,
        # operator lifecycle, SQLite receipt and device event are Runtime output.
        for number in range(207):
            service.eventStore.append(jobId, "test.history.fixture", f"Q1 pagination {number}",
                                      projectId=projectId, level="DEBUG")
        workbench._refreshCatalog()
        waitUntil(lambda: jobId in workbench.runs, timeout=10)
        active = workbench.directory.topLevelItem(0)
        assert active.childCount() == 1
        workbench.directory.setCurrentItem(active.child(0))
        _click(workbench.openButton)
        waitUntil(lambda: workbench._upper is not None and workbench._canReadEarlier(), timeout=10)
        releaseFirst.set()
        waitUntil(second.is_set, timeout=10)

        def find(kind, *, node="", code=""):
            return next((entry for entry in workbench.window.entries if entry.eventType == kind
                         and (not node or entry.nodeId == node) and (not code or entry.code == code)), None)

        waitUntil(lambda: find("device.action.retry", code="Q1_SOFTWARE_WARN") is not None, timeout=10)
        warn = find("device.action.retry", code="Q1_SOFTWARE_WARN")
        done = find("node.completed", node="sum")
        receipt = next(entry for entry in workbench.window.entries
                       if entry.eventType == "node.log" and entry.nodeId == "record"
                       and "receipt" in entry.payload.get("data", {}))
        assert done is not None and warn.iterationPath == (1,) and receipt.iterationPath == (0,)
        assert done.rawRecord()["payload"]["ioSummary"]["outputs"]["items"][0]["value"]["value"] == 5
        assert warn.workflowRunId and warn.parentWorkflowRunId and warn.nodeRunId

        _click(workbench.infoToggle)
        assert jobId in workbench.info.text()
        _click(workbench.infoToggle)
        assert workbench.info.isHidden()
        locate = next(button for button in workbench.findChildren(QPushButton) if button.text() == "定位当前")
        waitUntil(lambda: workbench.job.status == "RUNNING", timeout=10)
        _click(locate)
        waitUntil(lambda: workbench._detailEntry is not None or "尚未确认" in workbench.jobHint.text(), timeout=5)
        assert workbench._detailEntry is not None, (workbench.jobHint.text(), workbench.job.status,
            workbench._continuous, workbench.window.paused, warn.nodeRunId)
        assert workbench._detailEntry.nodeRunId == warn.nodeRunId

        _click(workbench.warning)
        waitUntil(lambda: ("event", done.eventId) not in workbench._items)
        _selectEntry(workbench, warn)
        assert workbench.events.currentItem().parent() is not None
        call = workbench._items[("workflow", warn.workflowRunId)]
        assert warn.workflowRunId not in call.text(0) and warn.workflowRunId in call.toolTip(0)
        _click(workbench.filterToggle)
        assert workbench.filters.isVisible()
        _click(workbench.technicalToggle)
        assert workbench.technicalFilters.isVisible()
        workbench.inputs["workflowRunId"].setText(warn.workflowRunId)
        workbench.inputs["iterationPath"].setText("0")
        waitUntil(lambda: ("event", warn.eventId) not in workbench._items)
        workbench.inputs["iterationPath"].setText("1")
        waitUntil(lambda: ("event", warn.eventId) in workbench._items)
        _selectEntry(workbench, warn)
        _click(workbench.technicalToggle)
        assert workbench.technicalFilters.isHidden() and workbench.inputs["iterationPath"].text() == "1"
        _capture(dialog, "q1-while-warning.png")
        nodeInput = workbench.inputs["nodeId"]
        candidate = nodeInput.completer()
        candidate.setCompletionPrefix("capture")
        assert candidate.completionCount() == 1 and candidate.currentCompletion() == warn.nodeId
        nodeInput.setText(candidate.currentCompletion())
        candidate.activated["QModelIndex"].emit(candidate.completionModel().index(0, 0))
        assert workbench.inputs["workflowId"].text() == warn.workflowId
        nodeInput.clear()
        workbench.inputs["workflowId"].clear()
        _click(workbench.filterToggle)
        dialog.resize(1040, 660)
        QCoreApplication.processEvents()
        handle = workbench.split.handle(1)
        center = handle.rect().center()
        oldSizes = workbench.split.sizes()
        target = center - QPoint(40, 0)
        QTest.mousePress(handle, Qt.LeftButton, pos=center)
        drag = QMouseEvent(QEvent.MouseMove, QPointF(target), QPointF(handle.mapToGlobal(target)),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(handle, drag)
        QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.rect().center())
        assert workbench.split.sizes() != oldSizes
        assert workbench.filterToggle.isVisible() and workbench.detail.tabs.tabBar().isVisible()
        _capture(dialog, "q1-narrow-split.png")
        dialog.resize(1400, 860)
        workbench.mode.setCurrentIndex(1)
        assert workbench.events.currentItem().data(0, Qt.UserRole) == ("event", warn.eventId)
        workbench.mode.setCurrentIndex(0)
        _click(workbench.readContext)
        assert "本轮已加载上下文" in workbench.rangeStatus.text()
        scrollDownToEnd(workbench.events)
        waitUntil(lambda: any(row.eventType == "test.history.fixture" and row.message == "Q1 pagination 0"
                             for row in workbench.window.entries), timeout=10)
        _click(workbench.returnContext)
        assert workbench.warning.isChecked()

        _click(workbench.deviceButton)
        waitUntil(lambda: workbench.devices.records.topLevelItemCount() > 0, timeout=10)
        assert workbench.devices.page["projectLoadGeneration"] == service.deviceManager.scope["projectLoadGeneration"]
        filterIndex = workbench.devices.filter.findData("software-camera")
        assert filterIndex > 0
        workbench.devices.filter.setCurrentIndex(filterIndex)
        workbench.devices.records.setCurrentItem(workbench.devices.records.topLevelItem(0))
        assert "E4BDA0E5A5BD" in workbench.devices.details.toPlainText().replace(" ", "")
        workbench.devices.format.setCurrentIndex(1)
        assert "你好" in workbench.devices.details.toPlainText()
        _capture(dialog, "q1-device-cache.png")
        back = next(button for button in workbench.devicePage.findChildren(QPushButton) if button.text() == "返回事件")
        _click(back)
        assert workbench._detailEntry.eventId == warn.eventId and workbench.warning.isChecked()

        workbench.inputs["workflowRunId"].clear()
        workbench.inputs["iterationPath"].clear()
        _click(workbench.warning)
        waitUntil(lambda: ("event", done.eventId) in workbench._items)
        _selectEntry(workbench, done)
        assert workbench.detail.rawTree.toPlainText() == json.dumps(done.rawRecord(), ensure_ascii=False, indent=2)
        status, = workbench.detail.readable.findItems("状态", Qt.MatchExactly)
        assert status.text(1) == "已完成" and done.rawRecord()["payload"]["status"] == "COMPLETED"
        assert "输出摘要" in [workbench.detail.readable.topLevelItem(i).text(0)
                             for i in range(workbench.detail.readable.topLevelItemCount())]
        _capture(dialog, "q1-execution-summary.png")
        _selectEntry(workbench, receipt)
        receiptField, = workbench.detail.readable.findItems("receipt", Qt.MatchExactly | Qt.MatchRecursive)
        receiptField.setExpanded(True)
        assert receiptField.childCount() and receiptField.child(0).text(0)
        workbench.detail.tabs.setCurrentIndex(1)
        assert '"receipt"' in workbench.detail.rawTree.toPlainText()
        _click(workbench.detail.copyRecordButton)
        from PySide2.QtWidgets import QApplication
        assert json.loads(QApplication.clipboard().text()) == receipt.rawRecord()
        close = next(button for button in workbench.drawer.findChildren(QPushButton) if button.text() == "关闭详情")
        selected = workbench.events.currentItem()
        _click(close)
        assert workbench.drawer.isHidden() and workbench.events.currentItem() is selected

        releaseSecond.set()
        waitUntil(lambda: workbench.window.newCount > 0, timeout=10)
        latest = next(button for button in workbench.findChildren(QPushButton) if button.text() == "返回最新")
        _click(latest)
        waitUntil(lambda: workbench._upper is not None and not workbench.window.paused, timeout=10)
        reply = client.stopJob(jobId, expectedRuntimeInstanceId=service.runtimeInstanceId,
                               expectedProjectId=projectId)
        assert reply.ok and reply.status == "STOPPING", reply.message
        waitUntil(lambda: workbench.job.status in {"COMPLETED", "ABORTED"}, timeout=12)
        terminalTitle = workbench.jobTitle.text()
        workbench._updateTimes()
        assert workbench.jobTitle.text() == terminalTitle and "估算" not in terminalTitle
        _click(locate)
        assert "尚未确认" in workbench.jobHint.text()
        _runDebugFromDesignerAndReturn(window, jobId)
        assert [record.jobId for record in service.jobRepository.all()] == [jobId]
        print(f"Q1 Job {jobId} · WARN {warn.eventId} · completed {done.eventId} · SQLite {receipt.eventId}")
        _walkRelatedJobs(dialog, service, client, projectId)
    finally:
        releaseFirst.set()
        releaseSecond.set()
        if window is not None:
            window.close()
        if channel is not None:
            channel.close()
        if server is not None:
            server.stop(0).wait(5)
        service.close()
