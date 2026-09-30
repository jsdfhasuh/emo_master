from pathlib import Path
import threading
import time

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.clients.runtime.display_session import DisplaySession


def until(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError("condition deadline")


@pytest.fixture
def network(channel):
    server = AioRuntimeServer(channel.runtime, channel)
    client = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    try:
        yield server, client
    finally:
        client.close()
        server.close()


def testFormalTwoClientsOneSpawnJob(network, channel, sample, tmp_path):
    server, transport = network
    stub = rpc.DisplayServiceStub(transport)
    prepared = stub.Prepare(pb.DisplayPrepareRequest(project_json=sample(tmp_path).model_dump_json(), resource_root=str(tmp_path)), timeout=15)
    assert len(stub.ListJobs(pb.DisplayEmpty()).jobs) == 0
    job = stub.Start(pb.DisplayStartRequest(prepared_id=prepared.prepared_id), timeout=5).job_id
    clients = [DisplaySession(f"127.0.0.1:{server.port}", job) for _ in range(2)]
    try:
        until(lambda: all(c.latest.get("root") for c in clients))
        a, b = [c.latest["root"] for c in clients]
        assert a[0] == b[0] and a[0].status == "COMPLETE"
        assert a[0].sources[0].valueJson == "2"
        assert not a[2] and not b[2]
        assert (a[1]["image"] == b[1]["image"]).all()
        clients[0].close()
        clients.pop(0)
        assert len(stub.ListJobs(pb.DisplayEmpty()).jobs) == 1
        assert stub.Snapshot(pb.DisplayRequest(job_id=job), timeout=.5).reset_required
        image = a[0].sources[1].image
        request = pb.DisplayAssetRequest(runtime_instance_id=channel.runtimeInstanceId, job_id=job, resource_id=image.resourceId, ttl_ms=30000)
        lease = stub.AcquireLease(request, timeout=.5)
        assert stub.ReadAsset(request, timeout=.5).sha256 == image.sha256
        stub.ReleaseLease(lease, timeout=.5)
        reconnect = DisplaySession(f"127.0.0.1:{server.port}", job)
        clients.append(reconnect)
        until(lambda: reconnect.latest.get("root"))
        assert reconnect.latest["root"][0] == a[0]
    finally:
        for client in clients:
            client.close()
    until(lambda: server.active["display"] == 0)
    assert not channel.runtime._closed


@pytest.mark.parametrize("failure", ["construct", "next", "close"])
def testCancelledLegacyWorkKeepsQuotaUntilCleanup(channel, failure):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    count = [0]
    lock = threading.Lock()

    def block():
        with lock:
            count[0] += 1
            if count[0] == 2:
                entered.set()
        release.wait(10)

    class Iterator:
        def __iter__(self):
            return self

        def __next__(self):
            if failure == "next":
                block()
            raise StopIteration

        def close(self):
            if failure == "close":
                block()
                closed.set()
                raise RuntimeError("injected close failure")
            closed.set()

    def stream(request, context):
        if failure == "construct":
            block()
        iterator = Iterator()
        return iterator

    channel.runtime.StreamJobEvents = stream
    server = AioRuntimeServer(channel.runtime, channel)
    transport = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    try:
        stub = rpc.RuntimeServiceStub(transport)
        calls = [stub.StreamJobEvents(pb.StreamJobEventsRequest(job_id="fault")) for _ in range(2)]
        assert entered.wait(2)
        until(lambda: server.active["events"] == 2)
        for call in calls:
            call.cancel()
        time.sleep(.05)
        assert server.active["events"] == 2
        with pytest.raises(grpc.RpcError) as error:
            next(stub.StreamJobEvents(pb.StreamJobEventsRequest(job_id="excess"), timeout=.5))
        assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert rpc.DisplayServiceStub(transport).Capabilities(pb.DisplayEmpty(), timeout=.2).protocol_version == "1.0"
        release.set()
        until(lambda: server.active["events"] == 0)
        assert closed.is_set()
    finally:
        release.set()
        transport.close()
        server.close()


def testLegacyUploadReadCancelAndLimit(network, channel, sample, tmp_path):
    import json
    import cv2
    import numpy as np
    server, transport = network
    stub = rpc.RuntimeServiceStub(transport)
    old = sample(tmp_path).model_dump()
    old.pop("presentation")
    old.pop("resources")
    old["schemaVersion"] = "2.1"
    old["workflows"]["main"]["nodes"][1]["params"] = {"imagePath": str(tmp_path / "input.png")}
    (tmp_path / "project.json").write_text(json.dumps(old), encoding="utf-8")
    assert stub.LoadProject(pb.LoadProjectRequest(project_path=str(tmp_path)), timeout=5).ok
    content = (tmp_path / "input.png").read_bytes()

    def chunks(data=content):
        for offset in range(0, len(data), 256 * 1024):
            yield pb.PreviewUploadChunk(filename="input.png", project_id="p2-fixture", content=data[offset:offset + 256 * 1024])

    uploaded = stub.UploadPreviewImage(chunks(), timeout=5)
    assert uploaded.ok
    downloaded = b"".join(chunk.content for chunk in stub.StreamPreviewAsset(pb.GetPreviewAssetRequest(
        asset_id=uploaded.asset_id, project_id="p2-fixture"), timeout=5))
    assert np.array_equal(cv2.imdecode(np.frombuffer(downloaded, np.uint8), cv2.IMREAD_COLOR), cv2.imread(str(tmp_path / "input.png")))
    exceeded = stub.UploadPreviewImage(chunks(b"x" * (64 * 1024 * 1024 + 1)), timeout=15)
    assert not exceeded.ok and "64 MiB" in exceeded.message
    gate = threading.Event()

    def partial():
        yield pb.PreviewUploadChunk(content=content[:50], project_id="p2-fixture", filename="input.png")
        gate.wait(10)
        yield pb.PreviewUploadChunk(content=content[50:])
    uploads = []
    before = set(channel.runtime.previewAssetStore._assets)
    try:
        # A response can precede asynchronous spool retirement. Start the
        # controlled saturation phase only after the earlier uploads release
        # their quota, so active == 2 refers to these two gated requests.
        until(lambda: server.active["bulk"] == 0)
        uploads = [stub.UploadPreviewImage.future(partial()) for _ in range(2)]
        until(lambda: server.active["bulk"] == 2)
        assert all(not upload.done() for upload in uploads), "controlled upload ended before quota probe"
        with pytest.raises(grpc.RpcError) as error:
            stub.UploadPreviewImage(chunks(), timeout=.5)
        assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        for upload in uploads:
            upload.cancel()
        gate.set()
        until(lambda: server.active["bulk"] == 0)
        assert set(channel.runtime.previewAssetStore._assets) == before
    finally:
        gate.set()
        for upload in uploads:
            upload.cancel()


def testC1ThroughC4RealSpawnAndClassifiedLoopback(tmp_path):
    from examples.runtime_pages_p2 import pacedProject, pluginRoots
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.presentation.service import PresentationService
    runtime = RuntimeService(dbPath=tmp_path / "state.db", workspaceRoot=tmp_path / "jobs", pluginRootPaths=pluginRoots(tmp_path))
    presentation = PresentationService(runtime, tmp_path / "display")

    def simulatedCamera(request, context):
        assert request.session_id == "SIMULATED-NO-DEVICE"
        while context.is_active():
            yield pb.OperatorPreviewFrame(session_id=request.session_id, width=160, height=120)
            time.sleep(.05)
    runtime.StreamOperatorPreviewFrames = simulatedCamera
    server = AioRuntimeServer(runtime, presentation)
    transport = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    display = rpc.DisplayServiceStub(transport)
    legacy = rpc.RuntimeServiceStub(transport)
    jobs = []
    calls = []
    try:
        prepared = display.Prepare(pb.DisplayPrepareRequest(project_json=pacedProject(tmp_path, count=10000).model_dump_json(), resource_root=str(tmp_path)), timeout=15)
        for label, jobCount, displayCount, cameras in [("C1", 1, 1, 0), ("C2", 1, 1, 1), ("C3", 1, 2, 1), ("C4", 2, 2, 1)]:
            while len(jobs) < jobCount:
                jobs.append(display.Start(pb.DisplayStartRequest(prepared_id=prepared.prepared_id), timeout=5).job_id)
            until(lambda: all(runtime.jobRepository.get(job).status == "RUNNING" for job in jobs))
            for job in jobs:
                call = legacy.StreamJobEvents(pb.StreamJobEventsRequest(job_id=job, follow=True))
                assert next(call).job_id == job
                calls.append(call)
            for index in range(displayCount):
                call = display.Subscribe(pb.DisplayRequest(job_id=jobs[index % jobCount]))
                assert next(call).job_id in jobs
                calls.append(call)
            if cameras:
                call = legacy.StreamOperatorPreviewFrames(pb.StreamOperatorPreviewFramesRequest(session_id="SIMULATED-NO-DEVICE"))
                assert next(call).width == 160
                calls.append(call)
            until(lambda: server.active["display"] == displayCount and server.active["events"] == jobCount)
            latencies = []
            for _ in range(10):
                start = time.monotonic()
                assert legacy.GetJobStatus(pb.GetJobStatusRequest(job_id=jobs[0]), timeout=.2).ok
                latencies.append((time.monotonic() - start) * 1000)
            assert max(latencies) < 200
            print(label, "control_ms", latencies, "active", server.active)
            if label == "C4":
                for excess in [display.Subscribe(pb.DisplayRequest(job_id=jobs[0]), timeout=.5),
                    legacy.StreamJobEvents(pb.StreamJobEventsRequest(job_id=jobs[0], follow=True), timeout=.5),
                    legacy.StreamOperatorPreviewFrames(pb.StreamOperatorPreviewFramesRequest(session_id="SIMULATED-NO-DEVICE"), timeout=.5)]:
                    with pytest.raises(grpc.RpcError) as error:
                        next(excess)
                    assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
            for call in calls:
                call.cancel()
            calls.clear()
            until(lambda: server.active["display"] == server.active["events"] == server.active["camera"] == 0)
        assert all(runtime.jobRepository.get(job).status == "RUNNING" for job in jobs)
    finally:
        for call in calls:
            call.cancel()
        transport.close()
        server.close()
        runtime.close()
        presentation.close()


def testReadOnlyClientImportsNoQtOrRuntimeImplementation():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c", "from emo_master.clients.runtime.display_session import DisplaySession; import sys; assert not any(k.startswith(('PySide2', 'emo_master.apps.designer', 'emo_master.apps.runtime.workflow', 'emo_master.apps.runtime.presentation')) for k in sys.modules)"], capture_output=True, text=True, timeout=15,
                            cwd=Path(__file__).resolve().parents[3] / "src")
    assert result.returncode == 0, result.stderr


def testServerStopDoesNotPretendBlockedWorkEnded(channel):
    gate, entered = threading.Event(), threading.Event()

    def blocked(request, context):
        entered.set()
        gate.wait(10)
        return iter(())
    channel.runtime.StreamJobEvents = blocked
    server = AioRuntimeServer(channel.runtime, channel)
    transport = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    call = rpc.RuntimeServiceStub(transport).StreamJobEvents(pb.StreamJobEventsRequest(job_id="fault"))
    try:
        assert entered.wait(2)
        with pytest.raises(TimeoutError, match="owns quota"):
            server.close(timeout=.05)
        assert server.active["events"] == 1
    finally:
        gate.set()
        call.cancel()
        transport.close()
        server.close()


def testRealClientSnapshotReplayRecoversLostFinalUpdate(network, channel, sample, tmp_path, monkeypatch):
    from emo_master.apps.runtime.presentation.rpc import DisplayRpc
    original = DisplayRpc._snapshot
    lostPush = threading.Event()

    def lost(self, request, context, incremental=False):
        reply = original(self, request, context, incremental)
        if incremental and not request.replay and reply.results:
            reply.ClearField("results")
            lostPush.set()
        return reply
    # Simulate lost pushes only. Health snapshots request replay explicitly.
    monkeypatch.setattr(DisplayRpc, "_snapshot", lost)
    prepared = channel.prepare(sample(tmp_path), tmp_path)
    job = channel.start(prepared.snapshot.snapshotId)
    server, _transport = network
    client = DisplaySession(f"127.0.0.1:{server.port}", job)
    try:
        until(lambda: client.latest.get("root"))
        assert lostPush.wait(2)
        assert client.latest["root"][0].sources[0].valueJson == "2"
        assert client.latest["root"][1]["image"].shape == (120, 160, 3)
        # Inject the fault in one outgoing real RPC. Mutating client.cursor races
        # an in-flight normal reply, which can overwrite it before it is sent.
        baselineResets = client.stats["resets"]
        snapshotRpc = client.stub.Snapshot
        acceptSnapshot = client._accept
        injectionLock = threading.Lock()
        injected = []
        acceptedInjection = threading.Event()

        def outOfRange(request, *args, **kwargs):
            with injectionLock:
                inject = not injected
                if inject:
                    injected.append({"reset_required": None})
            if inject:
                fault = pb.DisplayRequest()
                fault.CopyFrom(request)
                fault.after_cursor = 10**6
                reply = snapshotRpc(fault, *args, **kwargs)
                injected[0]["reset_required"] = reply.reset_required
                injected[0]["reply"] = reply
                return reply
            return snapshotRpc(request, *args, **kwargs)

        def acceptInjected(snapshot):
            with client.lock:
                before = client.stats["resets"]
                result = acceptSnapshot(snapshot)
                if injected and injected[0].get("reply") is snapshot:
                    injected[0]["accepted_reset_delta"] = client.stats["resets"] - before
                    acceptedInjection.set()
                return result

        monkeypatch.setattr(client, "_accept", acceptInjected)
        monkeypatch.setattr(client.stub, "Snapshot", outOfRange)
        until(acceptedInjection.is_set)
        assert len(injected) == 1 and injected[0]["reset_required"] is True
        assert injected[0]["accepted_reset_delta"] == 1
        assert client.stats["resets"] > baselineResets
        until(lambda: client.latest.get("root") and "image" in client.latest["root"][1])
        assert client.latest["root"][0].sources[0].valueJson == "2"
        assert client.latest["root"][1]["image"].shape == (120, 160, 3)
        assert len(channel.jobs) == 1
    finally:
        client.close()


def testFullDecodeQueueDoesNotPermanentlyLoseFinalResult(network, channel, sample, tmp_path, monkeypatch):
    import queue
    original = DisplaySession._accept
    rejected = threading.Event()

    def accept(self, snapshot):
        with self.lock:
            if snapshot.results and not rejected.is_set():
                put = self.pending.put_nowait

                def full(task):
                    raise queue.Full

                self.pending.put_nowait = full
                try:
                    original(self, snapshot)
                    rejected.set()
                finally:
                    self.pending.put_nowait = put
            else:
                original(self, snapshot)

    monkeypatch.setattr(DisplaySession, "_accept", accept)
    prepared = channel.prepare(sample(tmp_path), tmp_path)
    job = channel.start(prepared.snapshot.snapshotId)
    server, _transport = network
    client = DisplaySession(f"127.0.0.1:{server.port}", job)
    try:
        until(lambda: client.latest.get("root"))
        assert rejected.is_set() and client.stats["dropped"] >= 1
        assert client.latest["root"][0].sources[0].valueJson == "2"
        assert client.latest["root"][1]["image"].shape == (120, 160, 3)
        assert len(channel.jobs) == 1
    finally:
        client.close()
