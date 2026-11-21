"""Native Qt acceptance against a real software Runtime and simulated camera."""
import json
import os
from pathlib import Path
from threading import Event

import grpc

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QPushButton

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.business_sqlite.backend import initialize
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
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


def _capture(dialog, name):
    target = os.environ.get("EMO_LOG_WORKBENCH_EVIDENCE_DIR")
    if target:
        directory = Path(target)
        directory.mkdir(parents=True, exist_ok=True)
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
        waitUntil(lambda: workbench._upper is not None and workbench.earlier.isEnabled(), timeout=10)
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
        _click(locate)
        waitUntil(lambda: workbench._detailEntry is not None or "尚未确认" in workbench.jobHint.text(), timeout=5)
        assert workbench._detailEntry is not None, (workbench.jobHint.text(), workbench.job.status,
            workbench._continuous, workbench.window.paused, warn.nodeRunId)
        assert workbench._detailEntry.nodeRunId == warn.nodeRunId

        _click(workbench.warning)
        waitUntil(lambda: ("event", done.eventId) not in workbench._items)
        _selectEntry(workbench, warn)
        assert workbench.events.currentItem().parent() is not None
        _click(workbench.filterToggle)
        assert workbench.filters.isVisible()
        _capture(dialog, "q1-while-warning.png")
        _click(workbench.filterToggle)
        workbench.mode.setCurrentIndex(1)
        assert workbench.events.currentItem().data(0, Qt.UserRole) == ("event", warn.eventId)
        workbench.mode.setCurrentIndex(0)
        _click(workbench.readContext)
        assert "本轮已加载上下文" in workbench.rangeStatus.text()
        _click(workbench.earlier)
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

        _click(workbench.warning)
        waitUntil(lambda: ("event", done.eventId) in workbench._items)
        _selectEntry(workbench, done)
        assert workbench.detail.rawTree.toPlainText() == json.dumps(done.rawRecord(), ensure_ascii=False, indent=2)
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
        print(f"Q1 Job {jobId} · WARN {warn.eventId} · completed {done.eventId} · SQLite {receipt.eventId}")
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
