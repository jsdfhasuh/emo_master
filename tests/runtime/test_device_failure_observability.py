"""Failure receipts locate the wait without claiming a native driver root cause."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from threading import Event
import time
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.devices import ipc
from emo_master.apps.runtime.communication import pipe_io
from emo_master.apps.runtime.devices.diagnostics import DeviceDiagnostics
from emo_master.apps.runtime.devices.executor import DeviceError, DeviceExecutor
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from tests.runtime.test_device_manager import manager as deviceManagerFixture, params
from tests.runtime.test_jsonl_logging import _records

manager = deviceManagerFixture


def testDeadlineReceiptKeepsDecisionTimeAndDoesNotReleaseOwner():
    executor = DeviceExecutor("wait-evidence")
    entered, release = Event(), Event()

    def held(*_args):
        entered.set()
        assert release.wait(5)

    try:
        operation = executor.submit("job", held, requestId="request")
        assert entered.wait(2)
        with executor.condition:
            operation.operationDeadline = time.monotonic() - 1
        with pytest.raises(DeviceError) as caught:
            executor.wait(operation)
        error = caught.value
        assert error.code == "E_DEVICE_OPERATION_TIMEOUT" and error.outcome == "UNKNOWN"
        receipt = error.diagnostics["deviceWait"]
        assert receipt["requestId"] == "request" and receipt["state"] == "EXECUTING"
        assert receipt["observedMonotonicMs"] >= receipt["operationDeadlineMonotonicMs"]
        assert receipt["startedMonotonicMs"] >= receipt["submittedMonotonicMs"]
        assert executor.active is operation and not operation.future.done()
        release.set()
        assert operation.future.result(2) is None
    finally:
        release.set()
        executor.close()


def testCaptureObserverStallIsDistinguishedFromDriverExecution(manager):
    executor = manager.executors["camera"]
    entered, release = Event(), Event()

    def heldObserver(*_args):
        with executor.condition:
            executor.active.operationDeadline = time.monotonic() - 1
        entered.set()
        assert release.wait(5)

    manager.frameObserver = heldObserver
    try:
        with ThreadPoolExecutor(1) as callers:
            pending = callers.submit(manager.execute, "a", "camera", "capture", params(),
                                     cycleId="cycle", requestId="observed-request")
            assert entered.wait(2)
            try:
                with pytest.raises(DeviceError) as caught:
                    pending.result(2)
                error = caught.value
                assert error.code == "E_DEVICE_OPERATION_TIMEOUT" and error.outcome == "UNKNOWN"
                receipt = error.diagnostics["deviceAction"]
                assert receipt["phase"] == "frame_observer"
                assert receipt["driverReturnedMonotonicMs"] <= receipt["framePublishedMonotonicMs"]
                assert receipt["framePublishedMonotonicMs"] <= receipt["observedMonotonicMs"]
                assert executor.active is not None and manager.frames.frames
            finally:
                release.set()
        active = executor.active
        if active is not None:
            active.future.result(2)
        manager.frameObserver = None
        assert manager.execute("b", "camera", "capture", params(), cycleId="next")["frameId"]
    finally:
        release.set()


@pytest.mark.parametrize("transportError", [OSError("injected reply deadline"), OSError(109, "peer closed")])
def testBridgeRecordsActualSendFailureAfterClosingConnection(monkeypatch, transportError):
    parent, child = multiprocessing.get_context("spawn").Pipe()
    records = []
    manager = SimpleNamespace(execute=lambda *_args, **_kwargs: {"completed": True})

    def record(value):
        assert parent.closed
        records.append(value)

    bridge = ipc.DeviceBridge(manager, "job", parent, Event(), onTransportError=record)
    send = ipc.sendBytes

    def failParent(connection, *args, **kwargs):
        if connection is parent:
            raise transportError
        return send(connection, *args, **kwargs)

    monkeypatch.setattr(ipc, "sendBytes", failParent)
    bridge.thread.start()
    try:
        child.send_bytes(json.dumps(dict(sequence=1, kind="device_action", action="capture",
            deviceId="camera", requestId="request", cycleId="cycle", params={"secret": "excluded"})).encode())
        bridge.thread.join(2)
        assert not bridge.thread.is_alive() and bridge.stopped.is_set()
        assert len(records) == 1
        record = records[0]
        assert record["requestId"] == "request" and record["cycleId"] == "cycle"
        assert record["deviceId"] == "camera" and record["action"] == "capture"
        detail = record["diagnostics"]["deviceIpc"]
        assert detail["phase"] == "send" and detail["transportError"] == "OSError"
        assert detail["transportMessage"] == str(transportError)
        assert detail["deadlineMonotonicMs"] >= detail["phaseStartedMonotonicMs"]
        assert detail["observedMonotonicMs"] >= detail["phaseStartedMonotonicMs"]
        assert "excluded" not in json.dumps(record) and "params" not in record
    finally:
        child.close()
        bridge.close()


def testIdlePeerClosureIsNotReportedAsDeviceFailure():
    parent, child = multiprocessing.get_context("spawn").Pipe()
    records = []
    bridge = ipc.DeviceBridge(SimpleNamespace(), "job", parent, Event(), onTransportError=records.append)
    bridge.thread.start()
    child.close()
    bridge.thread.join(2)
    try:
        assert not bridge.thread.is_alive() and not records
    finally:
        bridge.close()


def testIdleWindowsPipeClosureIsNotReportedAsDeviceFailure(monkeypatch):
    parent, child = multiprocessing.get_context("spawn").Pipe()
    records = []
    bridge = ipc.DeviceBridge(SimpleNamespace(), "job", parent, Event(), onTransportError=records.append)

    def windowsClosed(*_args):
        raise OSError(109, "通信 IPC 读写失败")

    monkeypatch.setattr(ipc, "receiveBytes", windowsClosed)
    bridge.thread.start()
    try:
        child.send_bytes(b"")
        bridge.thread.join(2)
        assert not bridge.thread.is_alive() and not records
    finally:
        child.close()
        bridge.close()


def testBridgeFailureReachesGlobalAndJsonlLogsWithFrozenOwnerScope(manager, monkeypatch, tmp_path):
    scope = dict(manager.scope)
    store = EventStore()
    writer = RuntimeJsonlLogWriter(tmp_path)
    store.addSink(writer.enqueue)
    manager.diagnostics.writer = writer
    manager.diagnostics.eventPublisher = store.append
    child = manager.openWorker("ipc-owner", ["camera"], Event(), multiprocessing.get_context("spawn"))
    bridge = manager.workers["ipc-owner"]
    send = ipc.sendBytes

    def failReply(connection, *args, **kwargs):
        if connection is bridge.connection:
            raise OSError("reply channel failed")
        return send(connection, *args, **kwargs)

    monkeypatch.setattr(ipc, "sendBytes", failReply)
    try:
        manager.scope = dict(scope, projectLoadGeneration="later-load")
        child.send_bytes(json.dumps(dict(sequence=1, kind="device_action", action="capture",
            deviceId="camera", requestId="persisted-request", cycleId="cycle", params=params())).encode())
        bridge.thread.join(2)
        assert not bridge.thread.is_alive()
        event, = [event for event in store.read("__runtime__") if event.eventType == "device.ipc.failed"]
        payload = json.loads(event.payloadJson)
        assert event.code == "E_DEVICE_IPC_CLOSED" and event.level == "ERROR"
        assert payload["jobId"] == "ipc-owner" and payload["requestId"] == "persisted-request"
        assert payload["projectLoadGeneration"] == scope["projectLoadGeneration"]
        assert payload["diagnostics"]["deviceIpc"]["transportMessage"] == "reply channel failed"
    finally:
        child.close()
        manager.scope = scope
        manager.retire("ipc-owner")
        writer.close()
    persisted, = [row for row in _records(tmp_path) if row["eventType"] == "device.ipc.failed"]
    assert persisted["payload"]["requestId"] == "persisted-request"
    assert persisted["payload"]["diagnostics"]["deviceIpc"]["phase"] == "send"


def testBoundedTransportAndWaitDetailsSurviveLogPreviewTruncation():
    diagnostics = DeviceDiagnostics()
    scope = dict(runtimeInstanceId="runtime", projectId="project", projectLoadGeneration="load")
    details = dict(deviceWait=dict(state="EXECUTING", observedMonotonicMs=1234),
        deviceAction=dict(phase="frame_observer", driverReturnedMonotonicMs=1200),
        deviceIpc=dict(phase="send", transportError="OSError"))
    row = diagnostics.append(scope, "camera", "device.action.failed", "unknown outcome",
        code="E_DEVICE_OPERATION_TIMEOUT", diagnostics=details, unrelated="x" * 3000)
    assert row["fieldsTruncated"] is True
    assert row["diagnostics"] == details


@pytest.mark.parametrize("serializationPause,transportPause,succeeds", [
    (0.0, 0.0, True), (2.25, 0.0, True), (0.0, 2.25, False),
])
def testReplySendBudgetStartsAfterSerialization(monkeypatch, serializationPause, transportPause, succeeds):
    clock = SimpleNamespace(now=100.0)
    clock.monotonic = lambda: clock.now
    monkeypatch.setattr(ipc, "time", clock)
    monkeypatch.setattr(pipe_io, "time", clock)
    parent, child = multiprocessing.get_context("spawn").Pipe()
    calls, failures, sends = [], [], []

    def execute(*_args, **kwargs):
        calls.append(kwargs["requestId"])
        return {"completed": True}

    bridge = ipc.DeviceBridge(SimpleNamespace(execute=execute), "job", parent, Event(),
                              onTransportError=failures.append)

    def encode(value):
        result = json.dumps(value)
        clock.now += serializationPause
        return result

    def send(connection, data, cancelled, deadline):
        sends.append((clock.now, deadline))
        clock.now += transportPause
        pipe_io.sendBytes(connection, data, cancelled, deadline)
        bridge.stopped.set()  # Stop only after the real reply has been sent.

    monkeypatch.setattr(ipc, "json", SimpleNamespace(loads=json.loads, dumps=encode))
    monkeypatch.setattr(ipc, "sendBytes", send)
    try:
        child.send_bytes(json.dumps(dict(sequence=1, kind="device_action", action="capture",
            deviceId="camera", requestId="one-request", cycleId="cycle", params={})).encode())
        bridge.run()
        assert parent.closed
        assert calls == ["one-request"]
        assert sends == [(100.0 + serializationPause, 102.0 + serializationPause)]
        if succeeds:
            assert child.poll(.2)
            assert json.loads(child.recv_bytes()) == {"ok": True, "result": {"completed": True}}
            assert failures == []
        else:
            # Windows poll() raises ERROR_BROKEN_PIPE for a closed peer;
            # recv_bytes() normalizes that same closure to EOFError.
            with pytest.raises(EOFError):
                child.recv_bytes()
            failure, = failures
            detail = failure["diagnostics"]["deviceIpc"]
            assert detail["phase"] == "send" and detail["transportError"] == "TimeoutError"
            assert detail["observedMonotonicMs"] > detail["deadlineMonotonicMs"]
    finally:
        parent.close()
        child.close()
