import json
import multiprocessing
import socket
import threading
import time

import pytest

from emo_master.apps.runtime.communication.manager import CommunicationManager
from emo_master.apps.runtime.communication.ipc import CommunicationProxy, WorkerBridge
from emo_master.core.contracts.tcp_service import TcpServiceError
from tests.core.project.test_project_communication import settings


def waitUntil(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def freePort():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def manager():
    owner = CommunicationManager("runtime-test")
    owner.configure("project", "path", settings(freePort()))
    yield owner
    owner.close()
    assert not owner._threads and not owner._workers and owner._listener is None


def connect(manager, ip="127.0.0.1"):
    sock = socket.socket()
    sock.bind((ip, 0))
    sock.settimeout(3)
    sock.connect(("127.0.0.1", manager.effective.tcpServer.port))
    return sock


def acquire(manager, job="job"):
    return manager.acquire(job, "tcp-server", "front", 1000, lambda: False)


def testSameProjectSyncKeepsConnectionAndPendingConfig(manager):
    manager.start()
    with connect(manager) as client:
        session = acquire(manager)
        generation = manager.serviceGeneration
        manager.configure("project", "path", manager.desired)
        assert manager.start()["serviceGeneration"] == generation
        result = manager.send("job", "1", session, b"'12.345,-6.78EY'", lambda: False)
        assert result["outcome"] == "SENT_TO_TRANSPORT" and not result["deviceConfirmed"]
        assert client.recv(100) == b"'12.345,-6.78EY'"
        changed = manager.desired.model_copy(deep=True)
        changed.tcpServer.port = freePort()
        manager.configure("project", "path", changed)
        assert manager.snapshot()["pending"]
        with pytest.raises(TcpServiceError, match="配置等待生效"):
            manager.start()
        disabled = changed.model_copy(update={"enabled": False})
        with pytest.raises(TcpServiceError, match="先停止"):
            manager.configure("project", "path", disabled)
        assert manager.state == "LISTENING"


def testReconnectAndForgedJobCannotReceiveOldResult(manager):
    manager.start()
    client = connect(manager)
    old = acquire(manager)
    client.close()
    waitUntil(lambda: not manager._peers)
    with connect(manager) as new:
        current = acquire(manager)
        assert current["connectionGeneration"] > old["connectionGeneration"]
        stale = manager.send("job", "2", old, b"OLD", lambda: False)
        forged = manager.send("other", "3", current, b"FORGED", lambda: False)
        assert stale["outcome"] == forged["outcome"] == "NOT_SENT"
        valid = manager.send("job", "4", current, b"NEW", lambda: False)
        assert valid["bytesSent"] == 3 and new.recv(10) == b"NEW"
        manager.revokeJob("job")
        assert manager.send("job", "5", current, b"NO", lambda: False)["outcome"] == "NOT_SENT"
        assert manager.state == "LISTENING"


def testRoutingRejectsUnknownAndDuplicateClients(manager):
    manager.start()
    with connect(manager) as client:
        acquire(manager)
        with connect(manager) as duplicate, connect(manager, "127.0.0.3") as unknown:
            assert duplicate.recv(1) == unknown.recv(1) == b""
        assert manager.send("job", "1", acquire(manager), b"OK", lambda: False)["outcome"] == "SENT_TO_TRANSPORT"
        assert client.recv(5) == b"OK"


def testBindConflictCancellationAndStopRelease(manager):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", manager.desired.tcpServer.port))
        blocker.listen()
        with pytest.raises(TcpServiceError) as error:
            manager.start()
        assert error.value.code == "E_TCP_BIND_FAILED" and manager.state == "FAILED"
    manager.start()
    cancel = threading.Event()
    errors = []
    def waiting():
        try:
            manager.acquire("job", "tcp-server", "front", 60000, cancel.is_set)
        except TcpServiceError as error:
            errors.append(error.code)
    thread = threading.Thread(target=waiting)
    thread.start()
    cancel.set()
    thread.join(1)
    assert not thread.is_alive() and errors == ["E_CANCELLED"]
    manager.stop()
    manager.stop()
    assert not manager._threads and manager.state == "STOPPED"
    manager.start()
    assert manager.serviceGeneration == 3


def testIpcDedupMismatchAndExpiredRequestNeverResend(manager, monkeypatch):
    from emo_master.apps.runtime.communication import ipc
    monkeypatch.setattr(ipc, "MAX_REPLIES", 1)
    manager.start()
    parent, child = multiprocessing.get_context("spawn").Pipe()
    bridge = WorkerBridge(manager, "job", parent, threading.Event())
    try:
        with connect(manager) as client:
            session = acquire(manager)
            request = dict(protocolVersion="1.0", sequence=1, action="send", session=session, dataBase64="QQ==", source={},
                           deadlineNs=time.monotonic_ns() + 10_000_000_000)
            raw = json.dumps(request).encode()
            assert bridge.dispatch(raw) == bridge.dispatch(raw)
            assert client.recv(3) == b"A"
            with pytest.raises(TcpServiceError) as error:
                bridge.dispatch(json.dumps(dict(request, dataBase64="Qg==")).encode())
            assert error.value.code == "E_TCP_REQUEST_MISMATCH"
            bridge.dispatch(json.dumps(dict(request, sequence=2)).encode())
            assert client.recv(3) == b"A"
            with pytest.raises(TcpServiceError) as error:
                bridge.dispatch(raw)
            assert error.value.code == "E_TCP_REQUEST_EXPIRED"
    finally:
        parent.close()
        child.close()


def testRealPipeProxyAndRevokeDoesNotStopListener(manager):
    manager.start()
    event = multiprocessing.get_context("spawn").Event()
    child = manager.openWorker("job", event, multiprocessing.get_context("spawn"))
    proxy = CommunicationProxy(child, event)
    try:
        with connect(manager) as client:
            session = proxy.acquire("tcp-server", "front", 1000)
            assert proxy.send(session, b"'0,0EN'", {})["outcome"] == "SENT_TO_TRANSPORT"
            assert client.recv(30) == b"'0,0EN'"
        event.set()
        manager.revokeJob("job")
        assert manager.state == "LISTENING" and not manager._workers
    finally:
        proxy.close()


def testQueueBoundAndPartialWriteUnknownAreDeterministic(manager, monkeypatch):
    from emo_master.apps.runtime.communication import manager as module
    manager.start()
    with connect(manager):
        session = acquire(manager)
        peer = manager._peers["front"]
        peer.queuedCount = module.MAX_QUEUED_SENDS
        full = manager.send("job", "1", session, b"abc", lambda: False)
        assert full["outcome"] == "NOT_SENT" and full["code"] == "E_TCP_BACKPRESSURE"
        peer.queuedCount = 0
        actual = peer.sock
        class Partial:
            count = 0
            def close(self):
                actual.close()
            def send(self, data):
                self.count += 1
                if self.count == 1:
                    return 1
                raise OSError("injected disconnect after partial write")
        peer.sock = Partial()
        monkeypatch.setattr(module.select, "select", lambda r, w, x, timeout: ([], w, []))
        try:
            receipt = manager.send("job", "2", session, b"abc", lambda: False)
            assert receipt["outcome"] == "OUTCOME_UNKNOWN" and receipt["bytesSent"] == 1
            assert peer.sock.count == 2
        finally:
            peer.sock = actual


def testConcurrentMessagesNeverInterleave(manager):
    manager.start()
    with connect(manager) as client:
        first, second = acquire(manager, "one"), acquire(manager, "two")
        messages = [b"A" * 150000, b"B" * 150000]
        gate = threading.Barrier(3)
        receipts = []
        def send(job, ref, data):
            gate.wait()
            receipts.append(manager.send(job, job + ":1", ref, data, lambda: False))
        workers = [threading.Thread(target=send, args=args) for args in
                   (("one", first, messages[0]), ("two", second, messages[1]))]
        for thread in workers:
            thread.start()
        gate.wait()
        data = b""
        while len(data) < 300000:
            data += client.recv(300000 - len(data))
        for thread in workers:
            thread.join(2)
            assert not thread.is_alive()
        assert data in (messages[0] + messages[1], messages[1] + messages[0])
        assert len(receipts) == 2 and all(r["outcome"] == "SENT_TO_TRANSPORT" for r in receipts)


def testLostIpcReceiptIsUnknownAndNeverAutomaticallyResent(manager, monkeypatch):
    from emo_master.apps.runtime.communication import ipc
    context = multiprocessing.get_context("spawn")
    manager.start()
    event = context.Event()
    child = manager.openWorker("job", event, context)
    proxy = CommunicationProxy(child, event)
    try:
        with connect(manager) as client:
            session = proxy.acquire("tcp-server", "front", 1000)
            bridge = manager._workers["job"]
            connection = bridge.connection
            originalSend = ipc.sendBytes
            def loseReply(target, raw, cancelled, deadline):
                if target is connection:
                    connection.close()
                    raise OSError("injected loss after completed dispatch")
                return originalSend(target, raw, cancelled, deadline)
            monkeypatch.setattr(ipc, "sendBytes", loseReply)
            with pytest.raises(TcpServiceError) as error:
                proxy.send(session, b"ONCE", {})
            assert error.value.receipt["outcome"] == "OUTCOME_UNKNOWN"
            assert client.recv(20) == b"ONCE"
            assert len([r for r, _ in manager._records if r["kind"] == "tx"]) == 1
            assert proxy.closed
    finally:
        proxy.close()


def testLeasesAndObservationHistoryAreBounded(manager, monkeypatch):
    from emo_master.apps.runtime.communication import manager as module
    monkeypatch.setattr(module, "MAX_LEASES", 1)
    monkeypatch.setattr(module, "MAX_RECORDS", 3)
    manager.start()
    with connect(manager):
        old = acquire(manager)
        acquire(manager)
        assert len(manager._leases) == 1
        assert manager.send("job", "1", old, b"NO", lambda: False)["outcome"] == "NOT_SENT"
        for i in range(10):
            manager._record("event", str(i))
        state = manager.snapshot(1)
        assert len(state["records"]) == 3 and state["gap"] and state["droppedRecords"] > 0


def testOldWorkerCannotAcquireFromRestartedService(manager):
    context = multiprocessing.get_context("spawn")
    manager.start()
    event = context.Event()
    proxy = CommunicationProxy(manager.openWorker("job", event, context), event)
    try:
        manager.stop()
        manager.start()
        with connect(manager):
            with pytest.raises(TcpServiceError) as error:
                proxy.acquire("tcp-server", "front", 1000)
            assert error.value.code == "E_CANCELLED"
    finally:
        proxy.close()


def testListenerThreadAdmissionFailureReleasesPort(manager, monkeypatch):
    spawn = manager._spawn
    def failed(*args):
        raise RuntimeError("thread admission injection")
    monkeypatch.setattr(manager, "_spawn", failed)
    with pytest.raises(TcpServiceError) as error:
        manager.start()
    assert error.value.code == "E_TCP_THREAD_FAILED" and manager._listener is None
    assert not manager._threads and manager.state == "FAILED"
    monkeypatch.setattr(manager, "_spawn", spawn)
    manager.start()


@pytest.mark.parametrize("message", ["'0,0EN'", "0,0EX", "'0,0EX'"])
def testOriginalEnExSamplesAreTransferredWithoutNormalization(manager, message):
    from emo_master.plugins.builtins._managed_tcp import TcpSessionSendOperator
    context = multiprocessing.get_context("spawn")
    manager.start()
    event = context.Event()
    proxy = CommunicationProxy(manager.openWorker("job", event, context), event)
    try:
        with connect(manager) as client:
            session = proxy.acquire("tcp-server", "front", 1000)
            result = TcpSessionSendOperator().executeNode(dict(session=session, data=message), {}, dict(communication=proxy))
            assert result["status"] == "ok", result
            assert client.recv(30) == message.encode("utf-8")
            assert result["diagnostics"]["tcpReceipt"]["bytesRequested"] == len(message.encode())
    finally:
        proxy.close()
