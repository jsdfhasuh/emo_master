"""Temporary read-only control admission is retried within its original budget."""
import threading

import pytest

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.presentation.rpc import DisplayRpc
from emo_master.clients.runtime.display_session import DisplaySession
from tests.runtime.presentation.test_network import until


@pytest.mark.parametrize("method", ["Capabilities", "Snapshot"])
def testFiveSessionsRecoverOneBusyControlAdmissionWithoutHistoricalError(channel, monkeypatch, method):
    jobs = [f"control-read-{index}" for index in range(5)]
    channel.jobs.update({job: {"scopeIds": [], "scopeTerminals": {}} for job in jobs})
    release = threading.Event()
    rejected = threading.Event()
    entered = []
    lock = threading.Lock()
    originalRead = getattr(DisplayRpc, method)
    originalAdmit = AioRuntimeServer._admit

    def controlledRead(self, request, context):
        with lock:
            entered.append(True)
            hold = len(entered) <= 4
        if hold:
            assert release.wait(3), "the fifth read never reached the real control admission boundary"
        return originalRead(self, request, context)

    async def admitted(self, category, context):
        if category == "control" and self.active[category] == 4:
            rejected.set()
            release.set()
        return await originalAdmit(self, category, context)

    monkeypatch.setattr(DisplayRpc, method, controlledRead)
    monkeypatch.setattr(AioRuntimeServer, "_admit", admitted)
    server = AioRuntimeServer(channel.runtime, channel)
    clients = [DisplaySession(f"127.0.0.1:{server.port}", job) for job in jobs]
    try:
        assert rejected.wait(2), "controlled first denial was not exercised"
        until(lambda: server.active["display"] == 5
              and all(client.stream is not None and client.stream.is_active() for client in clients), timeout=3)
        assert not [error for client in clients for error in client.errors]
        assert server.peak["control"] <= 4
    finally:
        release.set()
        for client in clients:
            client.close()
        until(lambda: server.active["display"] == 0)
        server.close()
        channel.jobs.clear()
    assert all(not thread.is_alive() for client in clients for thread in client.threads)


class ClockStop:
    def __init__(self, clock, cancelled=False):
        self.clock, self.cancelled, self.waits = clock, cancelled, []

    def wait(self, seconds):
        self.waits.append(seconds)
        if not self.cancelled:
            self.clock[0] += seconds
        return self.cancelled


def readClient(monkeypatch, *, cancelled=False):
    from types import SimpleNamespace
    import emo_master.clients.runtime.display_session as module
    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = DisplaySession.__new__(DisplaySession)
    client.stop = ClockStop(clock, cancelled)
    return client, clock


def failure(code, details):
    import grpc

    class Error(grpc.RpcError):
        def code(self):
            return code

        def details(self):
            return details
    return Error()


def testTemporaryControlReadsKeepOneDeadlineAndSameRequest(monkeypatch):
    import grpc
    client, clock = readClient(monkeypatch)
    request, response = object(), object()
    busy = failure(grpc.StatusCode.RESOURCE_EXHAUSTED, "control")
    timeouts = []

    def method(value, timeout):
        assert value is request
        timeouts.append(timeout)
        clock[0] += .02  # This RPC time must also consume the original budget.
        if len(timeouts) < 3:
            raise busy
        return response

    assert client._controlRead(method, request) is response
    assert timeouts == pytest.approx([.5, .47, .43])
    assert clock[0] == pytest.approx(.09)


@pytest.mark.parametrize("rpcCost", [0, .49, .5])
def testPermanentBusyControlReadRaisesOriginalFailureWithinHalfSecond(monkeypatch, rpcCost):
    import grpc
    client, clock = readClient(monkeypatch)
    busy = failure(grpc.StatusCode.RESOURCE_EXHAUSTED, "control")
    timeouts = []

    def method(request, timeout):
        timeouts.append(timeout)
        clock[0] += min(timeout, rpcCost)
        raise busy

    with pytest.raises(grpc.RpcError) as error:
        client._controlRead(method, object())
    assert error.value is busy
    assert clock[0] == pytest.approx(.5)
    assert 1 <= len(timeouts) <= 13
    assert all(0 < timeout <= .5 for timeout in timeouts)
    assert timeouts == sorted(timeouts, reverse=True)


@pytest.mark.parametrize("code, detail", [
    ("RESOURCE_EXHAUSTED", "display per-Job subscriptions"),
    ("RESOURCE_EXHAUSTED", "bulk"),
    ("UNAVAILABLE", "control"),
    ("PERMISSION_DENIED", "control"),
])
def testOtherReadFailuresAreNeverRetried(monkeypatch, code, detail):
    import grpc
    client, clock = readClient(monkeypatch)
    rejected = failure(getattr(grpc.StatusCode, code), detail)
    calls = []

    def method(request, timeout):
        calls.append(timeout)
        raise rejected

    with pytest.raises(grpc.RpcError) as error:
        client._controlRead(method, object())
    assert error.value is rejected and len(calls) == 1
    assert not client.stop.waits and clock[0] == 0


def testClosingInterruptsControlReadBackoffWithoutAnotherRpc(monkeypatch):
    import grpc
    client, clock = readClient(monkeypatch, cancelled=True)
    busy = failure(grpc.StatusCode.RESOURCE_EXHAUSTED, "control")
    calls = []

    def method(request, timeout):
        calls.append(timeout)
        raise busy

    with pytest.raises(grpc.RpcError) as error:
        client._controlRead(method, object())
    assert error.value is busy and len(calls) == 1
    assert client.stop.waits == [.01] and clock[0] == 0


@pytest.mark.parametrize("outcome", ["exhaustion", "close"])
def testRealBusyControlReportsExhaustionOrClosesWithoutReleasingOthers(channel, monkeypatch, outcome):
    import grpc
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
    release, ready, rejected = threading.Event(), threading.Event(), threading.Event()
    entered, lock = [], threading.Lock()
    originalRead, originalAdmit = DisplayRpc.Capabilities, AioRuntimeServer._admit

    def controlledRead(self, request, context):
        with lock:
            entered.append(True)
            if len(entered) == 4:
                ready.set()
        assert release.wait(5)
        return originalRead(self, request, context)

    async def admitted(self, category, context):
        if category == "control" and self.active[category] == 4:
            rejected.set()
        return await originalAdmit(self, category, context)

    monkeypatch.setattr(DisplayRpc, "Capabilities", controlledRead)
    monkeypatch.setattr(AioRuntimeServer, "_admit", admitted)
    server = AioRuntimeServer(channel.runtime, channel)
    transport = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    stub = rpc.DisplayServiceStub(transport)
    calls = [stub.Capabilities.future(pb.DisplayEmpty(), timeout=5) for _ in range(4)]
    client = None
    try:
        assert ready.wait(2)
        client = DisplaySession(f"127.0.0.1:{server.port}", "busy-reader")
        assert rejected.wait(2)
        if outcome == "exhaustion":
            until(lambda: bool(client.errors), timeout=2)
            assert list(client.errors) == [("RESOURCE_EXHAUSTED", "control")]
        else:
            client.close()
            assert all(not thread.is_alive() for thread in client.threads)
        assert server.active["control"] == 4 and not release.is_set()
        assert server.peak["control"] == 4
    finally:
        if client is not None:
            client.close()
        release.set()
        for call in calls:
            assert call.result(timeout=3).protocol_version == "1.0"
        transport.close()
        until(lambda: server.active["control"] == 0)
        server.close()
