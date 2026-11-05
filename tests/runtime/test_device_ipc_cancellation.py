"""Normal Stop preserves device receipts across the real, non-retrying IPC."""
from concurrent.futures import Future
from contextlib import contextmanager
import json
import multiprocessing
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.communication.pipe_io import receiveBytes
from emo_master.apps.runtime.devices import ipc
from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested


@contextmanager
def _channel(execute):
    parent, child = multiprocessing.get_context("spawn").Pipe()
    cancellation = Event()
    bridge = ipc.DeviceBridge(SimpleNamespace(execute=execute), "job", parent, cancellation)
    proxy = ipc.DeviceProxy(child, cancellation)
    try:
        yield bridge, proxy, cancellation
    finally:
        cancellation.set()
        proxy.close()
        if bridge.thread.ident is not None:
            bridge.close()
        else:
            parent.close()


def _callInThread(proxy, action="write", *, params=None):
    result = Future()

    def call():
        try:
            result.set_result(proxy.call(action, deviceId="device", params=params or {}, cycleId="cycle"))
        except BaseException as error:
            result.set_exception(error)

    thread = Thread(target=call, name="test-device-proxy", daemon=True)
    thread.start()
    return thread, result


def _finishCall(thread, proxy, cancellation):
    cancellation.set()
    proxy.close()
    thread.join(5)
    assert not thread.is_alive(), "device proxy did not finish its cancelled call"


def testSentRequestCancelledBeforeBridgeReadDoesNotExecuteOrReplay(monkeypatch):
    sent = Event()
    requests, executed = [], []
    send = ipc.sendBytes

    def execute(_owner, _device, _action, _params, *, cancelled, **_fields):
        if cancelled():
            raise DeviceError("E_DEVICE_CANCELLED", "cancelled before device admission")
        executed.append(True)
        return {"written": True}

    with _channel(execute) as (bridge, proxy, cancellation):
        def sendAndObserve(connection, raw, cancelled, deadline):
            value = send(connection, raw, cancelled, deadline)
            if connection is proxy.connection:
                requests.append(json.loads(raw))
                sent.set()
            return value

        monkeypatch.setattr(ipc, "sendBytes", sendAndObserve)
        thread, result = _callInThread(proxy)
        try:
            assert sent.wait(2), "device request was not fully sent"
            assert bridge.sequence == 0
            cancellation.set()
            bridge.thread.start()
            with pytest.raises(CancellationRequested) as failure:
                result.result(5)
            assert failure.value.code == "E_CANCELLED"
            assert failure.value.outcome == "NOT_EXECUTED"
            assert isinstance(failure.value.__cause__, DeviceError)
            assert failure.value.__cause__.code == "E_DEVICE_CANCELLED"
            with pytest.raises(CancellationRequested):
                proxy.call("write", deviceId="device", params={}, cycleId="cycle")
            assert not executed
            assert len(requests) == 1 and proxy.sequence == bridge.sequence == 1
        finally:
            _finishCall(thread, proxy, cancellation)


def testDriverCancellationReceiptKeepsOutcomeAndDiagnostics():
    entered, reply = Event(), Event()
    executed = []
    diagnostics = {"driver": "test-device", "receipt": "native-cancel-confirmed"}

    def execute(_owner, _device, _action, _params, *, cancelled, **fields):
        executed.append(fields["requestId"])
        entered.set()
        assert reply.wait(5), "driver cancellation receipt was not released"
        assert cancelled()
        raise DeviceError("E_DEVICE_CANCELLED", "driver confirmed cancellation",
                          outcome="CANCELLED", diagnostics=diagnostics)

    with _channel(execute) as (bridge, proxy, cancellation):
        bridge.thread.start()
        thread, result = _callInThread(proxy)
        try:
            assert entered.wait(2), "device action did not reach the driver"
            cancellation.set()
            reply.set()
            with pytest.raises(CancellationRequested) as failure:
                result.result(5)
            assert failure.value.code == "E_CANCELLED"
            assert failure.value.outcome == "CANCELLED"
            assert failure.value.diagnostics == diagnostics
            cause = failure.value.__cause__
            assert isinstance(cause, DeviceError) and cause.code == "E_DEVICE_CANCELLED"
            assert cause.outcome == "CANCELLED" and cause.diagnostics == diagnostics
            assert len(executed) == 1 and proxy.sequence == bridge.sequence == 1
        finally:
            reply.set()
            _finishCall(thread, proxy, cancellation)


@pytest.mark.parametrize("cancelled", [False, True])
def testRealPeerLossKeepsUnknownOutcomeWithoutReplay(cancelled):
    parent, child = multiprocessing.get_context("spawn").Pipe()
    cancellation = Event()
    proxy = ipc.DeviceProxy(child, cancellation)
    thread, result = _callInThread(proxy)
    try:
        request = json.loads(receiveBytes(parent, ipc.MAX_BYTES, lambda: False, time.monotonic() + 2))
        assert request["kind"] == "device_action" and request["sequence"] == 1
        if cancelled:
            cancellation.set()
        parent.close()
        with pytest.raises(DeviceError) as failure:
            result.result(5)
        assert failure.value.code == "E_DEVICE_OUTCOME_UNKNOWN"
        assert failure.value.outcome == "UNKNOWN"
        assert proxy.closed and proxy.sequence == 1
    finally:
        parent.close()
        _finishCall(thread, proxy, cancellation)


def testUnlimitedCaptureStopWithoutReceiptHasBoundedUnknownOutcome(monkeypatch):
    # Shorten the real cancellation budget, not the pipe operation itself.
    monkeypatch.setattr(ipc, "CANCEL_RECEIPT_TIMEOUT", .05, raising=False)
    monkeypatch.setattr(ipc, "IPC_TIMEOUT", .1, raising=False)
    parent, child = multiprocessing.get_context("spawn").Pipe()
    cancellation = Event()
    proxy = ipc.DeviceProxy(child, cancellation)
    thread, result = _callInThread(proxy, "capture", params={"captureTimeoutMs": 0})
    try:
        request = json.loads(receiveBytes(parent, ipc.MAX_BYTES, lambda: False, time.monotonic() + 2))
        assert request["params"]["captureTimeoutMs"] == 0
        cancellation.set()
        # Keep the real peer open and silent while the cancelled infinite wait ends.
        with pytest.raises(DeviceError) as failure:
            result.result(2)
        assert failure.value.code == "E_DEVICE_OUTCOME_UNKNOWN"
        assert failure.value.outcome == "UNKNOWN"
        assert isinstance(failure.value.__cause__, TimeoutError)
        assert failure.value.diagnostics == dict(requestId=request["requestId"], deviceId="device",
            cycleId="cycle", action="capture", phase="receive", transportError="TimeoutError")
        assert proxy.closed and proxy.sequence == 1
    finally:
        parent.close()
        _finishCall(thread, proxy, cancellation)


def testCompletedActionKeepsSuccessWhenStopPrecedesReceiptAndDoesNotReplay():
    completed, reply = Event(), Event()
    executed = []
    receipt = {"written": True, "value": 42}

    def execute(_owner, _device, _action, _params, **fields):
        executed.append(fields["requestId"])
        completed.set()
        assert reply.wait(5), "completed device receipt was not released"
        return receipt

    with _channel(execute) as (bridge, proxy, cancellation):
        bridge.thread.start()
        thread, result = _callInThread(proxy)
        try:
            assert completed.wait(2), "device action did not complete"
            cancellation.set()
            reply.set()
            assert result.result(5) == receipt
            with pytest.raises(CancellationRequested) as failure:
                proxy.call("write", deviceId="device", params={}, cycleId="cycle")
            assert failure.value.outcome == "NOT_EXECUTED"
            assert len(executed) == 1 and proxy.sequence == bridge.sequence == 1
        finally:
            reply.set()
            _finishCall(thread, proxy, cancellation)
