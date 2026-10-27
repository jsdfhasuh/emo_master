"""Real loopback gRPC + Runtime + spawn Jobs + independent TCP client processes.

Set TCP_REVIEW_EVIDENCE_DIR to retain client JSON/JSONL and server snapshots.
Fault injection is separately labelled; ordinary wire tests do not fake sockets.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import queue
import select
import socket
import subprocess
import sys
import threading
import time

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.clients.runtime.communication import CommunicationClient, CommunicationClientError
from tests.runtime.tcp_port_probe import assertTcpPortReleased
from tests.runtime.test_managed_tcp import waitUntil
from tests.runtime.test_tcp_service_integration import currentPayload as payload, managementPayload


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts" / "tcp_loopback_client.py"


class ExternalClient:
    def __init__(self, port, directory, name, source="127.0.0.1", *options):
        self.replies = queue.Queue()
        self.log = (directory / f"{name}.jsonl").open("w", encoding="utf-8")
        self.stderr = (directory / f"{name}.stderr.txt").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(PROBE), "--port", str(port), "--source", source,
             "--interactive", "--output", str(directory / f"{name}.json"), *options],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, encoding="utf-8", cwd=ROOT,
        )

        def collect():
            for line in self.process.stdout:
                self.log.write(line)
                self.log.flush()
                self.replies.put(json.loads(line))
            self.replies.put(None)

        self.reader = threading.Thread(target=collect, name=f"probe-reader-{name}", daemon=True)
        self.reader.start()
        try:
            self.ready = self.reply()
            assert self.ready["action"] == "ready"
            assert self.ready["pid"] != os.getpid()
        except BaseException:
            self.close()
            raise

    def reply(self):
        result = self.replies.get(timeout=6)
        assert result is not None, "Probe process exited without reply; inspect its stderr/report"
        assert result["ok"], result
        return result

    def command(self, action, **fields):
        self.process.stdin.write(json.dumps(dict(action=action, **fields)) + "\n")
        self.process.stdin.flush()
        return self.reply()

    def received(self, expected):
        waitUntil(lambda: self.command("snapshot")["receivedBytes"] >= len(expected), 10)
        state = self.command("snapshot")
        assert state["receivedBytes"] == len(expected), state
        assert state["sha256"] == hashlib.sha256(expected).hexdigest(), state
        assert state["prefixHex"] == expected[:4096].hex(), state
        return state

    def close(self):
        try:
            if self.process.poll() is None:
                self.command("quit")
                self.process.wait(timeout=5)
            assert self.process.returncode == 0
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.reader.join(3)
            self.process.stdin.close()
            self.process.stdout.close()
            self.log.close()
            self.stderr.close()
        assert not self.reader.is_alive()


class Rig:
    def __init__(self, directory):
        self.directory = directory
        self.runtime = RuntimeService(dbPath=directory / "runtime.db", workspaceRoot=directory / "jobs")
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.server = grpc.server(self.pool)
        runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(self.runtime, self.server)
        self.grpcPort = self.server.add_insecure_port("127.0.0.1:0")
        self.server.start()
        self.channel = grpc.insecure_channel(f"127.0.0.1:{self.grpcPort}")
        grpc.channel_ready_future(self.channel).result(timeout=5)
        self.stub = runtime_pb2_grpc.RuntimeServiceStub(self.channel)
        self.management = CommunicationClient(self.stub)
        self.clients = []
        self.notes = {}

    @property
    def manager(self):
        return self.runtime.communicationManager

    def load(self, value, filename="probe.emoproj"):
        self.value = value
        self.port = value["communication"]["tcpServer"]["port"]
        path = self.directory / filename
        path.write_text(json.dumps(value), encoding="utf-8")
        reply = self.stub.LoadProject(pb.LoadProjectRequest(project_path=str(path)), timeout=10)
        assert reply.ok, reply.message
        self.path = path
        return path

    def state(self):
        return self.management.communicationState("tcp-project", projectPath=str(self.path))

    def control(self, action, **fields):
        return self.management.controlCommunication(action, self.state(), **fields)

    def client(self, source="127.0.0.1", *options):
        client = ExternalClient(self.port, self.directory, f"client-{len(self.clients)}", source, *options)
        self.clients.append(client)
        return client

    def startJob(self, channel="front", data="OK", **inputs):
        reply = self.stub.StartJob(pb.StartJobRequest(project_id="tcp-project", workflow_id=channel,
                                  inputs_json=json.dumps(inputs or dict(data=data))), timeout=10)
        assert reply.ok, reply.message
        return reply.job_id

    def send(self, data):
        session = self.manager.acquire('legacy-probe', 'tcp-server', 'front', 1000, lambda: False)
        from uuid import uuid4
        receipt = self.manager.send('legacy-probe', uuid4().hex, session, data.encode(), lambda: False)
        assert receipt['outcome'] == 'SENT_TO_TRANSPORT', receipt

    def terminal(self, job, status="COMPLETED"):
        waitUntil(lambda: self.runtime.jobRepository.get(job).isTerminal
                  and not self.runtime.jobSupervisor.ownsJobResources(job), 20)
        record = self.runtime.jobRepository.get(job)
        assert record.status == status, asdict(record)
        return record

    def finish(self):
        before = self.manager.snapshot()
        with self.manager._lock:
            sockets = [peer.sock for peer in self.manager._peers.values()]
            if self.manager._listener is not None:
                sockets.append(self.manager._listener)
            threads = tuple(self.manager._threads)
        jobs = [asdict(record) for record in self.runtime.jobRepository.all()]
        try:
            for client in self.clients:
                client.close()
        finally:
            self.management.closeDisplayOwner("test")
            self.channel.close()
            self.server.stop(0).wait()
            self.pool.shutdown()
            self.runtime.close()
        after = self.manager.snapshot()
        self.notes.update(serverBeforeClose=before, serverAfterClose=after, jobs=jobs,
                          clientExitCodes=[client.process.returncode for client in self.clients])
        (self.directory / "server-evidence.json").write_text(json.dumps(self.notes, indent=2), encoding="utf-8")
        assert not self.manager._threads and not self.manager._workers
        assert not after["ownsResources"]
        assert all(sock.fileno() == -1 for sock in sockets)
        assert not any(thread.is_alive() for thread in threads)
        assert all(client.process.poll() == 0 for client in self.clients)
        # A new real listener can own the released address (not merely an empty state field).
        if hasattr(self, "port"):
            assertTcpPortReleased(self.port)


@pytest.fixture
def rig(tmp_path, request):
    evidence = os.environ.get("TCP_REVIEW_EVIDENCE_DIR")
    directory = Path(evidence) / request.node.name if evidence else tmp_path
    directory.mkdir(parents=True, exist_ok=True)
    owner = Rig(directory)
    try:
        yield owner
    finally:
        owner.finish()


class ManagedRig(Rig):
    @property
    def manager(self):
        driver = self.runtime.deviceManager.drivers.get('server')
        return driver.backend if driver is not None else getattr(self, 'closedBackend', self.runtime.communicationManager)

    def load(self, value, filename='probe.emoproj'):
        self.value = value
        self.port = value['devices']['definitions']['server']['connection']['port']
        self.path = self.directory / filename
        self.path.write_text(json.dumps(value), encoding='utf-8')
        reply = self.stub.LoadProject(pb.LoadProjectRequest(project_path=str(self.path)), timeout=10)
        assert reply.ok, reply.message
        return self.path

    def state(self):
        reply = self.stub.GetDeviceStates(pb.GetDeviceStatesRequest(project_id='tcp-project'), timeout=5)
        assert reply.ok, reply.message
        state = json.loads(reply.states_json)['server']['driverState']
        diagnostics = self.stub.GetDeviceDiagnostics(pb.GetDeviceDiagnosticsRequest(
            runtime_instance_id=reply.runtime_instance_id, project_id=reply.project_id,
            project_load_generation=reply.project_load_generation, device_id='server'), timeout=5)
        assert diagnostics.ok, diagnostics.message
        state['records'] = json.loads(diagnostics.diagnostics_json)['records']
        return state

    def control(self, action, **fields):
        assert not fields and action in {'START', 'STOP'}
        from emo_master.clients.runtime.devices import DeviceManagementClient
        client = DeviceManagementClient(self.stub)
        runtimeId, states = client.getDeviceStates('tcp-project')
        client.controlDevice('tcp-project', runtimeId, 'server', states['server'],
            'connect' if action == 'START' else 'disconnect', confirmed=True)
        return self.state()

    def finish(self):
        self.closedBackend = self.manager
        super().finish()
        assert not self.runtime.deviceManager.workers


@pytest.fixture
def managedRig(tmp_path):
    owner = ManagedRig(tmp_path)
    try:
        yield owner
    finally:
        owner.finish()


def fillKernelSendBuffer(rig):
    """Only test setup: fill the real socket until non-writable, without mocks.

    Do not assume SO_SNDBUF or the first large send implies TCP backpressure:
    Windows loopback accepts megabytes even when the requested buffer is 4096.
    """
    waitUntil(lambda: "front" in rig.manager._peers)
    sock = rig.manager._peers["front"].sock
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    total = 0
    deadline = time.monotonic() + 3
    blockedSince = None
    while time.monotonic() < deadline:
        _, writable, _ = select.select([], [sock], [], 0)
        if writable:
            blockedSince = None
            try:
                total += sock.send(b"P" * 65536)
            except BlockingIOError:
                pass
            assert total <= 32 * 1024 * 1024, "Loopback did not reach bounded pressure"
        else:
            blockedSince = blockedSince or time.monotonic()
            if time.monotonic() - blockedSince >= .15:
                rig.notes.update(testOnlySendBuffer=sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF),
                                 testOnlyPrefillBytes=total)
                return
            time.sleep(.005)
    raise AssertionError("Did not establish real kernel TCP pressure")


def testRealJobsExactBytesLongConnectionRoutingAndReload(managedRig):
    rig = managedRig
    value = payload()
    flow = deepcopy(value["workflows"]["front"])
    flow["inputs"] = dict(x="number", y="number")
    flow["nodes"].insert(2, dict(nodeId="format", kind="operator",
                               operatorId="communication.coordinate_message_format"))
    flow["edges"] = [edge for edge in flow["edges"] if edge["fromNode"] != "input"] + [
        dict(fromNode="input", fromPort=p, toNode="format", toPort=p) for p in ("x", "y")] + [
        dict(fromNode="format", fromPort="data", toNode="send", toPort="data")]
    value["workflows"]["formatted"] = flow
    value["workflowOrder"].append("formatted")
    rig.load(value)
    assert rig.state()["state"] == "STOPPED"
    first = rig.startJob("formatted", x=12.3454, y=-6.78)
    second = rig.startJob("rear", "'0,0EN'")
    assert rig.state()["state"] == "LISTENING"
    front = rig.client("127.0.0.1", "--read-chunk", "1", "--read-delay-ms", "1")
    rear = rig.client("127.0.0.2")
    frontBytes, rearBytes = b"'12.345,-6.78EY'", b"'0,0EN'"
    front.received(frontBytes)
    rear.received(rearBytes)
    rig.terminal(first)
    rig.terminal(second)
    initial = rig.state()
    duplicate = rig.client()
    unknown = rig.client("127.0.0.3")
    for rejected in (duplicate, unknown):
        waitUntil(lambda: not rejected.command("snapshot")["connected"])
        assert rejected.command("snapshot")["receivedBytes"] == 0
    for index, data in enumerate(("0,0EX", "'0,0EX'", "'0,0EN'", "'1.2,-0.0EY'", "A", "B")):
        target, client = ("front", front) if index % 2 == 0 else ("rear", rear)
        rig.terminal(rig.startJob(target, data))
        if target == "front":
            frontBytes += data.encode()
            client.received(frontBytes)
        else:
            rearBytes += data.encode()
            client.received(rearBytes)
    rejected = rig.stub.LoadProject(pb.LoadProjectRequest(project_path=str(rig.path)), timeout=5)
    assert not rejected.ok and 'E_CONFIG_ACTIVE' in rejected.message
    current = rig.state()
    assert current["serviceGeneration"] == initial["serviceGeneration"]
    assert [item["connectionGeneration"] for item in current["channels"]] == [
        item["connectionGeneration"] for item in initial["channels"]]
    front.command("send", hex=b"PROBE-ACK\x00\xff".hex())
    waitUntil(lambda: any(r.get("kind") == "rx" and r.get("dataHex") == "50 52 4F 42 45 2D 41 43 4B 00 FF"
                         for r in rig.state()["records"]))
    assert not rig.manager._workers
    assert len(front.command("snapshot")["connections"]) == 1
    assert len(rear.command("snapshot")["connections"]) == 1
    rig.notes.update(frontExpectedHex=frontBytes.hex(), rearExpectedHex=rearBytes.hex(),
                     formattedBytes=len(b"'12.345,-6.78EY'"))


@pytest.mark.parametrize("rst", [False, True], ids=["graceful", "reset"])
def testReconnectRejectsOldSessionWithoutReplay(managedRig, rst):
    rig = managedRig
    rig.load(payload("manual"))
    rig.control("START")
    client = rig.client()
    old = rig.manager.acquire("probe-lease", "tcp-server", "front", 1000, lambda: False)
    rig.terminal(rig.startJob(data="BEFORE"))
    client.received(b"BEFORE")
    client.command("close", rst=rst)
    waitUntil(lambda: not rig.manager._peers)
    client.command("connect")
    current = rig.manager.acquire("probe-lease", "tcp-server", "front", 1000, lambda: False)
    assert current["connectionGeneration"] > old["connectionGeneration"]
    stale = rig.manager.send("probe-lease", "old-result", old, b"MUST-NOT-ARRIVE", lambda: False)
    assert stale["outcome"] == "NOT_SENT" and stale["code"] == "E_TCP_SESSION_EXPIRED"
    rig.terminal(rig.startJob(data="AFTER"))
    client.received(b"BEFOREAFTER")
    time.sleep(.25)
    client.received(b"BEFOREAFTER")
    rig.notes["staleReceipt"] = stale


def testRealSlowClientTimeoutDoesNotBlockOtherChannel(managedRig):
    rig = managedRig
    value = payload("manual")
    value['devices']['definitions']['server']['policies'].update(sendTimeoutMs=1000, maxMessageBytes=2097152)
    rig.load(value)
    rig.control("START")
    slow = rig.client("127.0.0.1", "--no-read", "--receive-buffer", "1024")
    fast = rig.client("127.0.0.2")
    fillKernelSendBuffer(rig)
    slowJob = rig.startJob(data="X" * 2097152)
    waitUntil(lambda: bool(rig.manager._peers.get("front") and rig.manager._peers["front"].queuedCount), 10)
    start = time.monotonic()
    fastJob = rig.startJob("rear", "RESPONSIVE")
    fast.received(b"RESPONSIVE")
    rig.terminal(fastJob)
    rig.terminal(slowJob, "FAILED")
    elapsed = time.monotonic() - start
    assert elapsed < 8
    receipt = [r["receipt"] for r in rig.state()["records"] if r.get("kind") == "tx" and r["channelId"] == "front"][-1]
    assert receipt["code"] == "E_TCP_SEND_TIMEOUT"
    assert receipt["outcome"] in {"NOT_SENT", "OUTCOME_UNKNOWN"}
    assert 0 <= receipt["bytesSent"] < 2097152
    if receipt["outcome"] == "NOT_SENT":
        assert receipt["bytesSent"] == 0
    else:
        assert "front" not in rig.manager._peers
    assert rig.state()["state"] == "LISTENING"
    assert "rear" in rig.manager._peers
    assert slow.command("snapshot")["receivedBytes"] == 0
    slow.command("close")
    waitUntil(lambda: "front" not in rig.manager._peers)
    slow.command("resume")
    slow.command("connect")
    rig.terminal(rig.startJob(data="NEW-ONLY"))
    slow.received(b"NEW-ONLY")
    rig.notes.update(slowReceipt=receipt, elapsedAfterQueueSeconds=elapsed)


@pytest.mark.parametrize("mode", ["graceful", "force", "service-stop"])
def testCancelOrServiceStopDuringRealBackpressure(managedRig, mode):
    rig = managedRig
    value = payload("manual")
    value['devices']['definitions']['server']['policies'].update(sendTimeoutMs=10000, maxMessageBytes=2097152)
    rig.load(value)
    rig.control("START")
    blocked = rig.client("127.0.0.1", "--no-read", "--receive-buffer", "1024")
    fillKernelSendBuffer(rig)
    job = rig.startJob(data="DO-NOT-REPLAY")
    waitUntil(lambda: bool(rig.manager._peers.get("front") and rig.manager._peers["front"].queuedCount), 10)
    started = time.monotonic()
    if mode == "service-stop":
        rig.control("STOP")
        rig.terminal(job, "FAILED")
        assert rig.state()["state"] == "STOPPED"
        rig.control("START")
    else:
        reply = rig.stub.StopJob(pb.StopJobRequest(job_id=job, mode=mode), timeout=5)
        assert reply.ok, reply.message
        rig.terminal(job, "ABORTED")
        assert rig.state()["state"] == "LISTENING"
    elapsed = time.monotonic() - started
    assert elapsed < 5, "Cancellation waited for the 10-second network timeout"
    blocked.command("close")
    waitUntil(lambda: "front" not in rig.manager._peers)
    blocked.command("resume")
    blocked.command("connect")
    rig.terminal(rig.startJob(data="FRESH"))
    blocked.received(b"FRESH")
    rig.notes.update(stopMode=mode, stopSeconds=elapsed)


@pytest.mark.parametrize("mode", ["graceful", "force"])
def testCancelledWaitCannotSendToLaterClient(managedRig, mode):
    rig = managedRig
    value = payload()
    value["workflows"]["front"]["nodes"][1]["deviceOverrides"] = {'tcp': {'timeoutMs': 60000}}
    rig.load(value)
    job = rig.startJob(data="CANCELLED-NEVER-SEND")
    waitUntil(lambda: rig.runtime.jobRepository.get(job).status == "RUNNING", 10)
    reply = rig.stub.StopJob(pb.StopJobRequest(job_id=job, mode=mode), timeout=10)
    assert reply.ok, reply.message
    rig.terminal(job, "ABORTED")
    assert rig.state()["state"] == "LISTENING"
    client = rig.client()
    rig.terminal(rig.startJob(data="NEW"))
    client.received(b"NEW")
    assert not any(r.get("kind") == "tx" and r["receipt"]["jobId"] == job for r in rig.state()["records"])


def testStopRestartDisableGuardAndSameIdReplacement(rig):
    value = managementPayload("manual")
    rig.load(value)
    rig.control("START")
    client = rig.client()
    rig.send('ONE')
    client.received(b"ONE")
    original = rig.state()
    rig.control("STOP")
    waitUntil(lambda: not client.command("snapshot")["connected"])
    saved = rig.control("PREPARE_DISABLE", saveToken="external-review-save")
    for action in ("START", "SYNC"):
        with pytest.raises(CommunicationClientError) as error:
            rig.management.controlCommunication(action, saved, config=value["communication"])
        assert error.value.code == "E_TCP_SAVE_PENDING"
    disabled = deepcopy(value["communication"])
    disabled["enabled"] = False
    rig.control("SYNC", config=disabled, saveToken="external-review-save")
    assert not rig.state()["enabled"] and not rig.state()["disableSavePending"]
    with pytest.raises(CommunicationClientError) as error:
        rig.management.controlCommunication("START", original)
    assert error.value.code == "E_TCP_CONFIG_STALE"
    rig.control("SYNC", config=value["communication"])
    rig.control("START")
    client.command("connect")
    rig.send('TWO')
    client.received(b"ONETWO")
    current = rig.state()
    rig.load(value, "same-id-another-path.emoproj")
    waitUntil(lambda: not client.command("snapshot")["connected"])
    assert rig.state()["state"] == "STOPPED"
    assert rig.state()["projectLoadGeneration"] != current["projectLoadGeneration"]
    with pytest.raises(CommunicationClientError) as error:
        rig.management.controlCommunication("START", current)
    assert error.value.code == "E_TCP_STALE_CONTEXT"
    rig.control("START")
    client.command("connect")
    rig.send('THREE')
    client.received(b"ONETWOTHREE")


def testProbeRefusesNonLoopback():
    for address in ("192.168.0.1", "0.0.0.0", "::1", "example.com"):
        result = subprocess.run([sys.executable, str(PROBE), "--host", address, "--port", "12345"],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
