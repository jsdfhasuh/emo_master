"""Local durable diagnostics and Runtime global errors reach the existing log window."""
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.designer.services.global_logs import RuntimeGlobalLogs
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.devices.diagnostics import DeviceDiagnostics
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.designer.qt_wait import waitUntil
from tests.designer.test_runtime_log_usability import Client, Settings


def readFiles(directory, pattern):
    return [json.loads(line) for path in directory.glob(pattern)
            for line in path.read_text(encoding="utf-8").splitlines()]


class GlobalService(rpc.RuntimeServiceServicer):
    def __init__(self, directory):
        self.runtimeInstanceId = "test-runtime"
        self.sqliteStore = SqliteStore(directory / "test.sqlite3")
        self.sqliteStore.initialize()
        self.events = EventStore(self.sqliteStore, runtimeInstanceId=self.runtimeInstanceId)
        self.writer = RuntimeJsonlLogWriter(directory)
        self.events.addSink(self.writer.enqueue)
        self.devices = DeviceDiagnostics()
        self.devices.writer = self.writer
        self.devices.eventPublisher = self.events.append
        self.requests = []

    _toProtoEvent = RuntimeService._toProtoEvent

    def QueryRuntimeLogs(self, request, context):
        self.requests.append((request.runtime_instance_id, request.after_event_id))
        if getattr(self, "queryGate", None) is not None:
            self.queryStarted.set()
            assert self.queryGate.wait(5)
        if getattr(self, "legacy", False):
            context.abort(grpc.StatusCode.UNIMPLEMENTED, "old Runtime")
        return RuntimeService.QueryRuntimeLogs(self, request, context)

    def StreamJobEvents(self, request, context):
        self.requests.append((request.job_id, request.after_sequence))
        for event in self.events.follow(request.job_id, request.after_sequence, cancellation=context):
            yield pb.JobEvent(job_id=event.jobId, sequence=event.sequence, event_type=event.eventType,
                level=event.level, code=event.code, message=event.message, project_id=event.projectId,
                payload_json=event.payloadJson, iteration_path_json=event.iterationPathJson,
                timestamp_ms=event.timestampMs)


@pytest.fixture
def globalServer(tmp_path):
    service = GlobalService(tmp_path / "runtime-logs")
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    rpc.add_RuntimeServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield service, rpc.RuntimeServiceStub(channel)
    channel.close()
    server.stop(0).wait(3)
    service.writer.close()


def testClientDiagnosticsPersistWithStructuredContextAfterClearAndClose(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.appendLocalEvent(dict(level="ERROR", eventType="presentation.client.image.failed",
        code="IMAGE_DECODE", message="图片解码失败", jobId="job", projectId="project",
        workflowId="main", nodeId="camera", payload={"sourceId": "image", "reason": "invalid PNG"}),
        source="page_designer")
    writer = window._logResources["writer"]
    assert writer.waitUntilProcessed("job", writer.sequence)
    window.openLogDialog()
    window.logDialog.view.areaTabs.setCurrentIndex(2)
    window.logDialog.view._applyPendingEntries()
    assert window.logDialog.view._visibleEntries[-1].code == "IMAGE_DECODE"
    assert window.logDialog.view.logDirectoryButton.isEnabled()
    directory = window._localLogDirectory
    window.logDialog.view.clearView()
    assert not window.logEntries
    assert window.close()
    rows = readFiles(directory, "designer-*.jsonl")
    error = next(row for row in rows if row["code"] == "IMAGE_DECODE")
    assert error["source"] == "page_designer" and error["jobId"] == "job"
    assert error["workflowId"] == "main" and error["nodeId"] == "camera"
    assert error["payload"] == {"sourceId": "image", "reason": "invalid PNG"}
    assert error["designerSessionId"] and not writer._thread.is_alive()


def testFailedLocalLogDirectoryReportsOnceWithoutRecursion(ownedDesignerWindow, monkeypatch, tmp_path):
    target = tmp_path / "not-a-directory"
    target.write_text("kept", encoding="utf-8")
    monkeypatch.setenv("EMO_DESIGNER_LOG_DIR", str(target))
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.appendPageLog("ERROR", "原始页面操作失败")
    window._pollOperationalLogs()
    window._pollOperationalLogs()
    assert len([row for row in window.logEntries if row.code == "DESIGNER_LOGFILE_FAILED"]) == 1
    assert any(row.message == "原始页面操作失败" for row in window.logEntries)
    assert target.read_text(encoding="utf-8") == "kept"


def testNativeDeviceFailureAndRecoveryReachGlobalWindowAndJsonl(ownedDesignerWindow, globalServer):
    service, stub = globalServer
    client = Client()
    client.runtimeService = stub
    window = ownedDesignerWindow(client, settingsStore=Settings())
    window._pollOperationalLogs()
    window.openLogDialog()
    waitUntil(lambda: window._logResources["reader"].cursor is not None)
    scope = {"projectId": "test-project", "runtimeInstanceId": "test-runtime", "projectLoadGeneration": "1"}
    service.devices.append(scope, "camera", "device.connect.failed", "模拟连接失败", code="E_DEVICE_CONNECT")
    service.devices.append(scope, "camera", "device.connect.completed", "模拟连接恢复")

    def delivered():
        window._pollOperationalLogs()
        return len([row for row in window.logEntries if row.eventType.startswith("device.connect.")]) == 2

    waitUntil(delivered)
    window.logDialog.view._applyPendingEntries()
    rows = [row for row in window.logEntries if row.eventType.startswith("device.connect.")]
    assert [(row.level, row.code) for row in rows] == [("ERROR", "E_DEVICE_CONNECT"), ("INFO", "DEVICE_RECOVERED")]
    assert rows[0].payload["deviceId"] == "camera"
    assert service.writer.waitUntilProcessed("__runtime__", 2)
    persisted = readFiles(service.writer.directory, "runtime-*.jsonl")
    assert len(persisted) == 2
    assert [(row["level"], row["code"]) for row in persisted] == [(row.level, row.code) for row in rows]
    window._stopGlobalLogs()
    window._pollOperationalLogs()
    waitUntil(lambda: len(service.requests) >= 2)
    waitUntil(lambda: service.requests[-1] == ("test-runtime", 2))
    window._pollOperationalLogs()
    assert len([row for row in window.logEntries if row.eventType.startswith("device.connect.")]) == 2
    reader = window._logResources["reader"]
    assert window.close()
    assert not reader.thread.is_alive()


def testGlobalObserverDoesNotReserveRunStreamsOrBlockRuntimeSwitch(globalServer):
    service, stub = globalServer
    client = RuntimeClient(runtimeService=stub)
    reader = RuntimeGlobalLogs(client.runtimeService)
    candidate = RuntimeClient(runtimeService=SimpleNamespace())
    try:
        waitUntil(lambda: reader.cursor is not None)
        assert client._activeStreams == {} and client._rpcCalls == 0
        client.switchConnection(candidate)
        assert client.runtimeService is candidate.runtimeService
    finally:
        reader.close()
        client.close()
    assert not reader.thread.is_alive()


def testEmbeddedGlobalReaderCancelsWithoutClosingRuntime(tmp_path):
    service = GlobalService(tmp_path / "logs")
    reader = RuntimeGlobalLogs(service)
    try:
        waitUntil(lambda: reader.cursor is not None)
        service.events.append("__runtime__", "node.preview.failed", "模拟预览错误", level="ERROR")
        waitUntil(lambda: not reader.events.empty())
        kind, event = reader.take()[0]
        assert kind == "event" and event["eventType"] == "node.preview.failed"
    finally:
        reader.close()
        service.writer.close()
    assert not reader.thread.is_alive()


def testInjectedWriterOwnershipIsRespectedOnCloseAndDestroyed(ownedDesignerWindow, tmp_path):
    from emo_master.apps.designer.services.local_log import DesignerLogWriter, closeLogResources
    writer = DesignerLogWriter(tmp_path / "injected")
    count = []
    close = writer.close
    def trackedClose():
        count.append(True)
        return close()
    writer.close = trackedClose
    window = ownedDesignerWindow(Client(), settingsStore=Settings(), localLogWriter=writer,
                                 localLogDirectory=writer.directory)
    window.appendRuntimeLog("INFO", "borrowed writer")
    assert window.close()
    closeLogResources(window._logResources)
    assert not count and writer._thread.is_alive()
    writer.close()
    assert count == [True]


def testInjectedWriterInitializationFailureReachesWindowOnce(ownedDesignerWindow, tmp_path):
    from emo_master.apps.designer.services.local_log import DesignerLogWriter
    blocked = tmp_path / "blocked"
    blocked.write_text("unchanged", encoding="utf-8")
    writer = DesignerLogWriter(blocked)
    window = ownedDesignerWindow(Client(), settingsStore=Settings(), localLogWriter=writer,
                                 localLogDirectory=blocked)
    try:
        window._pollOperationalLogs()
        window._pollOperationalLogs()
        assert len([row for row in window.logEntries if row.code == "DESIGNER_LOGFILE_FAILED"]) == 1
        assert blocked.read_text(encoding="utf-8") == "unchanged"
    finally:
        assert window.close()
        writer.close()


def testRuntimeHistoryUsesGlobalIdsAndDoesNotAcceptLiveAppend(ownedDesignerWindow, globalServer):
    service, stub = globalServer
    service.events.append("job-a", "node.log", "old project A", projectId="old", nodeId="node-a")
    service.events.append("job-b", "node.log", "old project B", projectId="old", nodeId="node-b")
    client = RuntimeClient(runtimeService=stub)
    window = ownedDesignerWindow(client, settingsStore=Settings())
    window.openLogDialog()
    view = window.logDialog.view
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData("runtime"))
    view.historyInputs["projectId"].setText("old")
    view.queryHistory()
    waitUntil(lambda: view._historyFuture is None)
    assert [entry.message for entry in view._entries] == ["old project A", "old project B"]
    assert [entry.sequence for entry in view._entries] == [1, 1]
    assert [entry.eventId for entry in view._entries] == [1, 2]
    assert all(entry.originApp == "runtime" and entry.component == "operator" for entry in view._entries)
    assert view._historyReply["runtimeInstanceId"] == "test-runtime"
    window.appendRuntimeEvent(dict(eventType="node.log", jobId="live", message="live must stay in live cache", eventId=3))
    window.appendRuntimeLog("ERROR", "client error must stay in live cache")
    assert len(view._entries) == 2
    assert any(entry.message == "live must stay in live cache" for entry in window.logEntries)
    view.rangeCombo.setCurrentIndex(0)
    assert any(entry.message == "live must stay in live cache" for entry in view._entries)


def testRuntimeHistoryBusinessFailureKeepsRecordsAndCursor(ownedDesignerWindow):
    class QueryClient(Client):
        runtimeService = object()
        def queryRuntimeLogs(self, **_options):
            return dict(ok=False, code="EVENT_TOO_LARGE", message="large", runtimeInstanceId="known",
                        upperEventId=22, nextEventId=5, hasMore=True, events=[])
    window = ownedDesignerWindow(QueryClient(), settingsStore=Settings())
    window.openLogDialog()
    view = window.logDialog.view
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData("runtime"))
    from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry
    previous = StructuredLogEntry.fromRuntimeEvent(dict(eventType="node.log", message="previous", eventId=5))
    view.setEntries([previous])
    view._historyReply = dict(ok=True, runtimeInstanceId="known", upperEventId=22, nextEventId=5, hasMore=True)
    view.queryHistory(nextPage=True)
    waitUntil(lambda: view._historyFuture is None)
    assert view._historyReply["nextEventId"] == 5 and view._entries == [previous]
    assert "EVENT_TOO_LARGE" in view.historyStatus.text()


def testHistoryExportReadsCompleteFixedRangeAndCancellationKeepsTarget(ownedDesignerWindow, tmp_path, monkeypatch):
    import threading
    from PySide2.QtTest import QTest
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QFileDialog
    class QueryClient(Client):
        runtimeService = object()
        def __init__(self):
            self.calls, self.block, self.entered, self.release = [], False, threading.Event(), threading.Event()
        def queryRuntimeLogs(self, **options):
            self.calls.append(options)
            if self.block:
                self.entered.set()
                assert self.release.wait(5)
            after = options.get("afterEventId", 0)
            return dict(ok=True, code="OK", message="", runtimeInstanceId="same", upperEventId=3,
                nextEventId=2 if after == 0 else 3, hasMore=after == 0,
                events=[dict(eventId=eventId, event=dict(eventType="node.log", message=str(eventId), sequence=1))
                        for eventId in ([1, 2] if after == 0 else [3])])
    client = QueryClient()
    window = ownedDesignerWindow(client, settingsStore=Settings())
    window.openLogDialog()
    view = window.logDialog.view
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData("runtime"))
    view._historyFilters = {"projectId": "historic-project"}
    view._historyReply = dict(ok=True, runtimeInstanceId="same", upperEventId=3, nextEventId=2, hasMore=True)
    target = tmp_path / "history.jsonl"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a: (str(target), "JSON Lines (*.jsonl)"))
    QTest.mouseClick(view.historyExportButton, Qt.LeftButton)
    waitUntil(lambda: view._historyFuture is None)
    assert len(target.read_text(encoding="utf-8").splitlines()) == 3
    assert [call["afterEventId"] for call in client.calls] == [0, 2]
    assert all(call["upperEventId"] == 3 and call["runtimeInstanceId"] == "same" and
               call["projectId"] == "historic-project" for call in client.calls)
    assert "已导出 3 条" in view.historyStatus.text()
    original = target.read_bytes()
    client.block = True
    QTest.mouseClick(view.historyExportButton, Qt.LeftButton)
    waitUntil(client.entered.is_set)
    QTest.mouseClick(view.historyCancelButton, Qt.LeftButton)
    client.release.set()
    waitUntil(lambda: view._historyFuture is None)
    assert target.read_bytes() == original and "未报告成功" in view.historyStatus.text()


def testDesignerLocalHistoryAndTargetChangeRejectOldReturn(ownedDesignerWindow, tmp_path):
    import threading
    from emo_master.apps.designer.services.local_log import DesignerLogWriter
    class SlowClient(Client):
        runtimeService = object()
        def __init__(self):
            self.entered, self.release = threading.Event(), threading.Event()
        def queryRuntimeLogs(self, **_options):
            self.entered.set()
            assert self.release.wait(5)
            return dict(ok=True, code="OK", message="", runtimeInstanceId="old", upperEventId=1,
                        nextEventId=1, hasMore=False, events=[dict(eventId=1, event=dict(message="obsolete"))])
    directory = tmp_path / "own-designer-logs"
    writer = DesignerLogWriter(directory)
    writer.record("designer.startup.failed", "local failure", level="ERROR")
    assert writer.close() is None
    originalPath = next(directory.glob("designer-*.jsonl"))
    client = SlowClient()
    window = ownedDesignerWindow(client, settingsStore=Settings(), localLogDirectory=directory)
    window.openLogDialog()
    view = window.logDialog.view
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData("local"))
    selected = view.localFileCombo.findData(str(originalPath))
    assert selected > 0
    view.localFileCombo.setCurrentIndex(selected)
    view.queryHistory()
    waitUntil(lambda: view._historyFuture is None)
    assert len(view._entries) == 1 and view._entries[0].message == "local failure"
    assert view._entries[0].originApp == "designer" and view._entries[0].eventId is None
    view.table.setCurrentCell(0, 0)
    assert "local failure" in view.detailViewer.toPlainText()
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData("runtime"))
    view.queryHistory()
    waitUntil(client.entered.is_set)
    client.runtimeService = object()
    view.setRuntimeClient(client)
    client.release.set()
    waitUntil(lambda: view._historyFuture is None)
    assert not view._entries and not view._historyReply
    assert "未报告成功" in view.historyStatus.text()


def testHistoryFailedFilterKeepsSuccessfulPagingAndExportScope(ownedDesignerWindow, tmp_path, monkeypatch):
    from PySide2.QtWidgets import QFileDialog
    class QueryClient(Client):
        runtimeService = object()
        def __init__(self):
            self.calls, self.failB = [], True
        def queryRuntimeLogs(self, **options):
            self.calls.append(options)
            project, after = options.get("projectId"), options.get("afterEventId", 0)
            if project == "B" and self.failB:
                return dict(ok=False, code="READ_FAILED", message="retry allowed", runtimeInstanceId="same",
                            upperEventId=0, nextEventId=after, hasMore=False, events=[])
            first, upper = (5, 22) if project == "A" else (1, 4)
            return dict(ok=True, code="OK", message="", runtimeInstanceId="same", upperEventId=upper,
                nextEventId=first if after == 0 else upper, hasMore=after == 0,
                events=[dict(eventId=first if after == 0 else first + 1,
                    event=dict(eventType="node.log", message=project + str(after), projectId=project))])
    client = QueryClient()
    window = ownedDesignerWindow(client, settingsStore=Settings())
    window.openLogDialog()
    view = window.logDialog.view
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData("runtime"))
    view.historyInputs["projectId"].setText("A")
    view.queryHistory()
    waitUntil(lambda: view._historyFuture is None)
    view.historyInputs["projectId"].setText("B")
    view.queryHistory()
    waitUntil(lambda: view._historyFuture is None)
    assert "READ_FAILED" in view.historyStatus.text() and view._entries[0].projectId == "A"
    assert "仍保留上次成功范围" in view.historyStatus.text() and '"projectId": "A"' in view.historyStatus.text()
    view.queryHistory(nextPage=True)
    waitUntil(lambda: view._historyFuture is None)
    nextRequest = dict(client.calls[-1])
    target = tmp_path / "scope.jsonl"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a: (str(target), "JSON Lines (*.jsonl)"))
    exportStart = len(client.calls)
    view.exportHistory()
    waitUntil(lambda: view._historyFuture is None)
    exportRequests = client.calls[exportStart:]
    assert nextRequest["projectId"] == "A" and nextRequest["afterEventId"] == 5 and nextRequest["upperEventId"] == 22
    assert [request["afterEventId"] for request in exportRequests] == [0, 5]
    assert all(request["projectId"] == "A" and request["upperEventId"] == 22 for request in exportRequests)
    assert [json.loads(line)["projectId"] for line in target.read_text(encoding="utf-8").splitlines()] == ["A", "A"]
    client.failB = False
    view.queryHistory()
    waitUntil(lambda: view._historyFuture is None)
    assert client.calls[-1].get("afterEventId", 0) == 0 and "upperEventId" not in client.calls[-1]
    assert view._historyFilters == {"projectId": "B"} and view._historyReply["upperEventId"] == 4
    assert view._entries[0].projectId == "B"
    view.queryHistory(nextPage=True)
    waitUntil(lambda: view._historyFuture is None)
    assert client.calls[-1]["afterEventId"] == 1 and client.calls[-1]["upperEventId"] == 4
    exportStart = len(client.calls)
    view.exportHistory()
    waitUntil(lambda: view._historyFuture is None)
    assert [request["afterEventId"] for request in client.calls[exportStart:]] == [0, 1]
    assert all(request["projectId"] == "B" and request["upperEventId"] == 4 for request in client.calls[exportStart:])
    assert [json.loads(line)["projectId"] for line in target.read_text(encoding="utf-8").splitlines()] == ["B", "B"]


@pytest.mark.parametrize("changedInstance", [False, True], ids=["same-instance", "restarted-instance"])
def testReaderErrorRetryKeepsMatchingCursorAndInstance(ownedDesignerWindow, tmp_path, changedInstance):
    service = GlobalService(tmp_path / "query-retry")
    query = service.QueryRuntimeLogs
    stopped = []
    def controllable(request, context):
        if stopped:
            return pb.QueryRuntimeLogsReply(ok=False, code="READ_FAILED", runtime_instance_id=service.runtimeInstanceId,
                next_event_id=request.after_event_id, message="ordinary read error")
        return query(request, context)
    service.QueryRuntimeLogs = controllable
    client = Client()
    client.runtimeService = service
    window = ownedDesignerWindow(client, settingsStore=Settings())
    try:
        window._pollOperationalLogs()
        first = window._logResources["reader"]
        waitUntil(lambda: first.cursor is not None)
        service.events.append("job", "node.log", "before stop")
        def delivered():
            window._pollOperationalLogs()
            return any(row.message == "before stop" for row in window.logEntries)
        waitUntil(delivered)
        cursor, instance = first.cursor, first.runtimeInstanceId
        stopped.append(True)
        waitUntil(first.finished.is_set)
        stopped.clear()
        if changedInstance:
            service.runtimeInstanceId = "new-runtime"
        requestStart = len(service.requests)
        window._pollOperationalLogs(retry=True)
        replacement = window._logResources["reader"]
        waitUntil(lambda: len(service.requests) > requestStart)
        assert service.requests[requestStart] == (instance, cursor)
        if changedInstance:
            waitUntil(lambda: replacement.runtimeInstanceId == "new-runtime" and replacement.cursor is not None)
            window._pollOperationalLogs()
            assert not any(row.message == "before stop" for row in window.logEntries)
        service.events.append("job", "node.log", "after reconnect")
        def resumed():
            window._pollOperationalLogs()
            return any(row.message == "after reconnect" for row in window.logEntries)
        waitUntil(resumed)
        assert len([row for row in window.logEntries if row.message == "after reconnect"]) == 1
    finally:
        assert window.close()
        service.writer.close()


def testLegacyJobSourceRestoresAfterUnimplementedAndDoesNotLeakAcrossTargets(ownedDesignerWindow, globalServer, tmp_path):
    service, stub = globalServer
    service.legacy = True
    client = Client()
    client.runtimeService = stub
    window = ownedDesignerWindow(client, settingsStore=Settings())
    supported = GlobalService(tmp_path / "supported")
    task = SimpleNamespace(event_type="node.log", message="legacy task event", job_id="job", node_id="",
                           level="INFO", timestamp_ms=1234)
    try:
        window._pollOperationalLogs()
        waitUntil(lambda: window._logResources["reader"].unsupported)
        window._pollOperationalLogs()
        window.runtimeController._onRuntimeEvent(task)
        assert len([row for row in window.logEntries if row.message == task.message]) == 1
        assert any(row.code == "UNIMPLEMENTED" for row in window.logEntries)
        client.runtimeService = supported
        window._pollOperationalLogs()
        waitUntil(lambda: window._logResources["reader"].cursor is not None)
        window.runtimeController._onRuntimeEvent(task)
        assert not any(row.message == task.message for row in window.logEntries)
        supported.events.append("job", "node.log", task.message)
        def delivered():
            window._pollOperationalLogs()
            return any(row.message == task.message for row in window.logEntries)
        waitUntil(delivered)
        window.runtimeController._onRuntimeEvent(task)
        assert len([row for row in window.logEntries if row.message == task.message]) == 1
        client.runtimeService = stub
        window._pollOperationalLogs()
        waitUntil(lambda: window._logResources["reader"].unsupported)
        window.runtimeController._onRuntimeEvent(task)
        assert len([row for row in window.logEntries if row.message == task.message]) == 1
    finally:
        assert window.close()
        supported.writer.close()


@pytest.mark.parametrize("legacy", [True, False], ids=["legacy-release", "query-single-source"])
def testShortJobBeforeQuerySupportConfirmationUsesOneSource(ownedDesignerWindow, globalServer, legacy):
    import threading
    service, stub = globalServer
    service.legacy = legacy
    service.queryGate, service.queryStarted = threading.Event(), threading.Event()
    client = Client()
    client.runtimeService = stub
    window = ownedDesignerWindow(client, settingsStore=Settings())
    task = SimpleNamespace(event_type="node.log", message="short Job completed before query reply", job_id="job",
                           node_id="", level="INFO", timestamp_ms=1234)
    try:
        window._pollOperationalLogs()
        waitUntil(service.queryStarted.is_set)
        window.runtimeController._onRuntimeEvent(task)
        assert not any(row.message == task.message for row in window.logEntries)
        service.queryGate.set()
        reader = window._logResources["reader"]
        waitUntil(lambda: reader.supported is not None)
        window._pollOperationalLogs()
        if legacy:
            assert len([row for row in window.logEntries if row.message == task.message]) == 1
            window._pollOperationalLogs()
            assert len([row for row in window.logEntries if row.message == task.message]) == 1
        else:
            assert not any(row.message == task.message for row in window.logEntries)
            service.events.append("job", "node.log", task.message)
            def delivered():
                window._pollOperationalLogs()
                return any(row.message == task.message for row in window.logEntries)
            waitUntil(delivered)
            assert len([row for row in window.logEntries if row.message == task.message]) == 1
    finally:
        service.queryGate.set()
        assert window.close()
