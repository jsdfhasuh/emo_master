"""Externally supervised real loopback compatibility and cancellation faults."""
import json
import threading
import time

import cv2
import grpc
import numpy as np

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService

from .network import DisplayFeed, IsolatedServer
from .runner_probe import image_project


def run(root):
    from .scenarios import wait_until, write_plugins
    image = np.random.default_rng(20260926).integers(0, 256, (480, 640, 3), np.uint8)
    image_path = root / "local.png"
    assert cv2.imwrite(str(image_path), image)
    (root / "project.json").write_text(json.dumps(image_project(image_path, False)), encoding="utf-8")
    service = RuntimeService(dbPath=root / "runtime.db", workspaceRoot=root / "jobs", pluginRootPaths=write_plugins(root))
    feed = DisplayFeed()
    server = IsolatedServer(service, feed)
    channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    stub = rpc.RuntimeServiceStub(channel)
    rows = []
    gates = []
    try:
        assert stub.LoadProject(pb.LoadProjectRequest(project_path=str(root)), timeout=5).ok
        content = image_path.read_bytes()
        def chunks(data=content):
            for offset in range(0, len(data), 256*1024):
                yield pb.PreviewUploadChunk(filename="local.png", project_id="p0-project", content=data[offset:offset+256*1024])
        reply = stub.UploadPreviewImage(chunks(), timeout=5)
        assert reply.ok, reply.message
        downloaded = b"".join(c.content for c in stub.StreamPreviewAsset(
            pb.GetPreviewAssetRequest(asset_id=reply.asset_id, project_id="p0-project"), timeout=5))
        assert np.array_equal(cv2.imdecode(np.frombuffer(downloaded, np.uint8), cv2.IMREAD_COLOR), image)
        rows.append(dict(case="legacy-upload-read-decode", status="PASS", bytes=len(downloaded)))
        before = set(service.previewAssetStore._assets) if hasattr(service.previewAssetStore, "_assets") else None
        # Original cumulative limit remains a structured PreviewAssetReply error.
        excess = stub.UploadPreviewImage(chunks(b"x" * (64*1024*1024+1)), timeout=15)
        assert not excess.ok and "64 MiB" in excess.message
        try:
            stub.UploadPreviewImage(iter([pb.PreviewUploadChunk(content=b"x"*(1024*1024+1))]), timeout=3)
        except grpc.RpcError as error:
            assert error.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        else:
            raise AssertionError("transport message limit")
        rows.append(dict(case="legacy-cumulative-and-chunk-limit", status="PASS"))
        gate = threading.Event()
        gates.append(gate)
        def partial():
            yield pb.PreviewUploadChunk(content=content[:1024], project_id="p0-project")
            gate.wait(10)
            yield pb.PreviewUploadChunk(content=content[1024:])
        uploads = [stub.UploadPreviewImage.future(partial(), timeout=10) for _ in range(2)]
        wait_until(lambda: server.active["bulk"] == 2)
        try:
            stub.UploadPreviewImage(chunks(), timeout=1)
        except grpc.RpcError as error:
            assert error.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        else:
            raise AssertionError("bulk admission")
        for upload in uploads:
            upload.cancel()
        gate.set()
        wait_until(lambda: server.active["bulk"] == 0)
        if before is not None:
            assert set(service.previewAssetStore._assets) == before
        rows.append(dict(case="legacy-partial-cancel-no-asset-and-bulk-limit", status="PASS"))

        # Each mode is a true iterator with separately controlled construction,
        # next and close, not an in-memory assertion about a fake server counter.
        for mode in ("construct-error", "construct-block", "first-next-block", "next-block", "close-error", "close-block", "serialize-error", "callback-block"):
            entered, gate, closed = threading.Event(), threading.Event(), threading.Event()
            gates.append(gate)
            class Iterator:
                count = 0
                def __iter__(self):
                    return self
                def __next__(self):
                    self.count += 1
                    if mode == "first-next-block" or (mode == "next-block" and self.count > 1):
                        entered.set()
                        gate.wait(10)
                        raise StopIteration
                    if self.count > 1:
                        raise StopIteration
                    return {"value": {1}} if mode == "serialize-error" else {"value": 1}
                def close(self):
                    closed.set()
                    if mode == "close-block":
                        entered.set()
                        gate.wait(10)
                    if mode == "close-error":
                        raise ValueError("injected close error")
            def method(request, context):
                if mode == "construct-error":
                    raise ValueError("injected constructor error")
                if mode == "construct-block":
                    entered.set()
                    gate.wait(10)
                if mode == "callback-block":
                    def callback():
                        entered.set()
                        gate.wait(10)
                    context.add_callback(callback)
                return Iterator()
            # Existing handler holds bound follow, route dynamically through feed.
            feed.test_method = method
            feed.follow_override = True
            call = channel.unary_stream("/p0.Display/StreamDisplayUpdates", request_serializer=lambda v: json.dumps(v).encode(),
                                        response_deserializer=json.loads)({"job": "fault"}, timeout=5)
            if mode in ("construct-block", "first-next-block"):
                wait_until(entered.is_set)
                call.cancel()
            else:
                try:
                    next(call)
                    if mode == "next-block":
                        # gRPC drives next independently after yielding first.
                        wait_until(entered.is_set)
                        call.cancel()
                    else:
                        try:
                            next(call)
                        except (StopIteration, grpc.RpcError):
                            pass
                except grpc.RpcError:
                    assert mode in ("construct-error", "serialize-error")
            if "block" in mode:
                wait_until(entered.is_set)
                assert server.active["display"] == 1
                start = time.perf_counter()
                stub.GetJobStatus(pb.GetJobStatusRequest(job_id="none"), timeout=1)
                assert time.perf_counter()-start < .2
                if mode == "close-block":
                    try:
                        server.close(timeout=.05)
                    except TimeoutError:
                        assert server.active["display"] == 1 and server.thread.is_alive()
                    else:
                        raise AssertionError("stop hid running close")
                    gate.set()
                    wait_until(lambda: server.active["display"] == 0)
                    server.close()
                    channel.close()
                    server = IsolatedServer(service, feed)
                    channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
                    stub = rpc.RuntimeServiceStub(channel)
                else:
                    gate.set()
            wait_until(lambda: server.active["display"] == 0)
            if mode != "construct-error":
                assert closed.is_set()
            rows.append(dict(case=mode, status="PASS", active_after=dict(server.active)))
        class BadStartup(IsolatedServer):
            async def _start(self):
                raise RuntimeError("injected startup")
        try:
            BadStartup(service, feed)
        except RuntimeError as error:
            assert "startup failed" in str(error)
        else:
            raise AssertionError("startup swallowed")
        rows.append(dict(case="startup-failure-cleanup", status="PASS"))
        return dict(rows=rows, compatibility_status="PASS", errors_observed=list(server.errors), peak=server.peak)
    finally:
        for gate in gates:
            gate.set()
        channel.close()
        server.close()
        service.close()
