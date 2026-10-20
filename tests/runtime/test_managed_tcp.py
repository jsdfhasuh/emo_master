import json
import multiprocessing
import socket
import threading
import time

import pytest

from emo_master.apps.runtime.communication.manager import CommunicationManager
from emo_master.apps.runtime.communication.ipc import CommunicationProxy, WorkerBridge
from emo_master.core.contracts.tcp_service import TcpServiceError
from emo_master.core.contracts.device_driver import DeviceError
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



@pytest.fixture
def devices():
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from emo_master.apps.runtime.devices.manager import DeviceManager
    from emo_master.core.project.models import ProjectDocument
    from emo_master.core.project.runtime_package import trustedDeviceTypes
    from tests.runtime.test_tcp_device_integration import definition
    owner = DeviceManager(trustedDeviceTypes())
    payload = WorkflowStore().toPayload()
    server = definition(owner.types['device.tcp.server'], port=freePort(),
        channels=[dict(channelId='front', name='Front', allowedIps=['127.0.0.1'])])
    server['policies']['connect'] = 'manual'
    payload['devices']['definitions']['server'] = server
    owner.load(ProjectDocument.model_validate(payload))
    executor, operation = owner.beginControl('server', owner.executors['server'].generation, 'connect')
    executor.wait(operation)
    owner.authorize('job', ['server'])
    yield owner
    owner.retire('job')
    owner.close()


def deviceCall(proxy, devices, action, arguments):
    definition = devices.definitions['server']
    return proxy.call(action, deviceTypeId=definition.deviceTypeId, policies=definition.policies,
        deviceId='server', cycleId='cycle', params=definition.defaults, source={},
        selectors=dict(channelId='front'), arguments=arguments)

def testIpcDuplicateMismatchAndExpiredRequestNeverResend(devices):
    backend = devices.drivers['server'].backend
    parent, child = multiprocessing.get_context('spawn').Pipe()
    bridge = WorkerBridge(devices, 'job', parent, threading.Event())
    try:
        with connect(backend) as client:
            session = backend.acquire('job', 'tcp-server', 'front', 1000, lambda: False)
            request = dict(sequence=1, kind='device_action', requestId='first', deviceId='server',
                action='send', params=devices.definitions['server'].defaults, cycleId='cycle',
                arguments=dict(session=session, dataBase64='QQ=='), source={}, selectors=dict(channelId='front'),
                deadlineNs=time.monotonic_ns()+10_000_000_000)
            raw = json.dumps(request).encode()
            assert bridge.dispatch(raw)['outcome'] == 'SENT_TO_TRANSPORT'
            assert client.recv(3) == b'A'
            for duplicate in (raw, json.dumps(dict(request, arguments=dict(session=session, dataBase64='Qg=='))).encode()):
                with pytest.raises(DeviceError, match='never replayed'):
                    bridge.dispatch(duplicate)
            assert bridge.dispatch(json.dumps(dict(request, sequence=2, requestId='second')).encode())['outcome'] == 'SENT_TO_TRANSPORT'
            assert client.recv(3) == b'A'
            with pytest.raises(DeviceError) as error:
                bridge.dispatch(json.dumps(dict(request, sequence=3, deadlineNs=0)).encode())
            assert error.value.code == 'E_TCP_REQUEST_EXPIRED'
            client.settimeout(.1)
            with pytest.raises(socket.timeout):
                client.recv(1)
    finally:
        parent.close()
        child.close()



def testRealPipeProxyAndRevokeDoesNotStopListener(devices):
    event = threading.Event()
    backend = devices.drivers['server'].backend
    proxy = CommunicationProxy(devices.openTcpWorker('job', event, multiprocessing.get_context('spawn')), event)
    try:
        with connect(backend) as client:
            session = deviceCall(proxy, devices, 'acquire_session', {})
            assert deviceCall(proxy, devices, 'send', dict(session=session, dataBase64='JzAsMEVOJw=='))['outcome'] == 'SENT_TO_TRANSPORT'
            assert client.recv(30) == b"'0,0EN'"
        event.set()
        devices.retire('job')
        assert backend.state == 'LISTENING' and not devices.tcpWorkers
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


def testLostIpcReceiptIsUnknownAndNeverAutomaticallyResent(devices, monkeypatch):
    from emo_master.apps.runtime.communication import ipc
    event = threading.Event()
    backend = devices.drivers['server'].backend
    proxy = CommunicationProxy(devices.openTcpWorker('job', event, multiprocessing.get_context('spawn')), event)
    try:
        with connect(backend) as client:
            session = deviceCall(proxy, devices, 'acquire_session', {})
            connection = devices.tcpWorkers['job'].connection
            originalSend = ipc.sendBytes
            def loseReply(target, raw, cancelled, deadline):
                if target is connection:
                    connection.close()
                    raise OSError('injected loss after completed dispatch')
                return originalSend(target, raw, cancelled, deadline)
            monkeypatch.setattr(ipc, 'sendBytes', loseReply)
            with pytest.raises(DeviceError) as error:
                deviceCall(proxy, devices, 'send', dict(session=session, dataBase64='T05DRQ=='))
            assert error.value.diagnostics['tcpReceipt']['outcome'] == 'OUTCOME_UNKNOWN'
            assert client.recv(20) == b'ONCE'
            assert len([r for r, _ in backend._records if r['kind'] == 'tx']) == 1
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


def testOldWorkerCannotAcquireFromReplacedProjectScope(devices):
    event = threading.Event()
    parent, child = multiprocessing.get_context('spawn').Pipe()
    bridge = WorkerBridge(devices, 'job', parent, event)
    try:
        devices.scope = dict(devices.scope, projectLoadGeneration='replacement-load')
        request = dict(sequence=1, kind='device_action')
        with pytest.raises(DeviceError) as error:
            bridge.dispatch(json.dumps(request).encode())
        assert error.value.code == 'E_DEVICE_CANCELLED'
        assert not devices.services['server'].busy()
    finally:
        parent.close()
        child.close()



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
def testOriginalEnExSamplesAreTransferredWithoutNormalization(devices, message):
    from emo_master.plugins.builtins._managed_tcp import TcpSessionSendOperator
    event = threading.Event()
    backend = devices.drivers['server'].backend
    proxy = CommunicationProxy(devices.openTcpWorker('job', event, multiprocessing.get_context('spawn')), event)
    try:
        with connect(backend) as client:
            session = deviceCall(proxy, devices, 'acquire_session', {})
            result = TcpSessionSendOperator().executeNode(dict(session=session, data=message), {},
                dict(executeDevice=lambda _slot, action, arguments: deviceCall(proxy, devices, action, arguments),
                     deviceParameters=dict(tcp=devices.definitions['server'].defaults)))
            assert result['status'] == 'ok', result
            assert client.recv(30) == message.encode('utf-8')
            assert result['diagnostics']['tcpReceipt']['bytesRequested'] == len(message.encode())
            assert result['diagnostics']['tcpReceipt']['deviceId'] == 'server'
    finally:
        proxy.close()
