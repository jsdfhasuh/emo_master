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
from tests.designer.qt_wait import waitUntil
from tests.designer.test_runtime_log_usability import Client, Settings


def readFiles(directory, pattern):
    return [json.loads(line) for path in directory.glob(pattern)
            for line in path.read_text(encoding="utf-8").splitlines()]


class GlobalService(rpc.RuntimeServiceServicer):
    def __init__(self, directory):
        self.events = EventStore()
        self.writer = RuntimeJsonlLogWriter(directory)
        self.events.addSink(self.writer.enqueue)
        self.devices = DeviceDiagnostics()
        self.devices.writer = self.writer
        self.devices.eventPublisher = self.events.append
        self.requests = []

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
    assert service.requests[-1] == ("__runtime__", 2)
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
        waitUntil(lambda: bool(service.requests))
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
        service.events.append("__runtime__", "node.preview.failed", "模拟预览错误", level="ERROR")
        waitUntil(lambda: not reader.events.empty())
        kind, event = reader.take()[0]
        assert kind == "event" and event["eventType"] == "node.preview.failed"
    finally:
        reader.close()
        service.writer.close()
    assert not reader.thread.is_alive()
