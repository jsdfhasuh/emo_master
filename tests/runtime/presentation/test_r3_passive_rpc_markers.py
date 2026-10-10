"""Finite passive callbacks and tiny real cancellation cases; no benchmark."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import gc
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace
import weakref

import grpc
import pytest

from scripts import r3_asset_split_trace as split


@contextmanager
def patches():
    changes = []

    def patch(owner, name, value):
        changes.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)
    try:
        yield patch
    finally:
        for owner, name, value in reversed(changes):
            setattr(owner, name, value)


class Loop:
    def __init__(self):
        self.closed = False
        self.handles = []

    def call_soon(self, operation, *args):
        handle = SimpleNamespace(cancelled=False)
        handle.cancel = lambda: setattr(handle, "cancelled", True)
        handle.run = lambda: operation(*args)
        self.handles.append(handle)
        return handle

    def is_closed(self):
        return self.closed

    def is_running(self):
        return not self.closed


def metadata(token="one"):
    return {"call_id": token, "runtime_instance_id": "runtime"}


@contextmanager
def withoutCyclicGc():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


@pytest.mark.parametrize("passive", [False, True])
def testRetiredTraceIsReleasedWithoutCyclicGc(passive):
    with withoutCyclicGc():
        trace = split.AssetSplitTrace(passive_markers=passive)
        trace_ref = weakref.ref(trace)
        if passive:
            marker_ref = weakref.ref(trace.markers)
            loop = Loop()
            trace.markers.register_loop("runtime", loop)
            loop.closed = True
            trace.markers.retire()
            assert trace.markers.retirement_verified
        del trace
        assert trace_ref() is None
        if passive:
            assert marker_ref() is None


def queuedMarkers():
    trace = split.AssetSplitTrace(passive_markers=True)
    markers = trace.markers
    loop = Loop()
    markers.register_loop("runtime", loop)
    callbacks = []
    context = SimpleNamespace(peer=lambda: "private-peer", add_done_callback=callbacks.append)
    markers.handler(metadata(), context)
    markers.serialized(metadata(), 1, None)
    return trace, markers, loop, callbacks


def testQueuedCallbacksDrainAfterTraceExpiresWithoutCyclicGc(monkeypatch):
    with withoutCyclicGc():
        trace, markers, loop, callbacks = queuedMarkers()
        trace_ref = weakref.ref(trace)
        del trace
        assert trace_ref() is None

        def forbidden():
            raise AssertionError("An expired observer must not sample clocks")

        with monkeypatch.context() as patch:
            patch.setattr(split.time, "perf_counter_ns", forbidden)
            # Callback owners remain valid, but neither may retain the trace.
            # Repeated or late delivery is harmless after the entry was popped.
            for _ in range(2):
                callbacks[0](None)
                loop.handles[0].run()
        assert not markers.done and not markers.turns
        loop.closed = True
        markers.retire()
        assert markers.retirement_verified
        assert not markers.loops and not markers.loop_threads and not markers.peers


def testExpiredObserverRetiresOnlyAfterOriginalLoopCloses():
    with withoutCyclicGc():
        trace, markers, loop, callbacks = queuedMarkers()
        trace_ref = weakref.ref(trace)
        del trace
        assert trace_ref() is None
        markers.retire()
        assert not markers.retirement_verified
        assert markers.done and markers.turns and markers.loops
        assert not loop.handles[0].cancelled
        loop.closed = True
        markers.retire()
        assert not markers.retirement_verified
        assert loop.handles[0].cancelled
        assert not markers.done and not markers.turns and not markers.peers
        assert not markers.loops and not markers.loop_threads
        callbacks[0](None)
        loop.handles[0].run()
        assert markers.report()["pending_done"] == markers.report()["pending_turns"] == 0


def testCallbackClocksPrecedeObserverLockAndPop(monkeypatch):
    trace, markers, loop, callbacks = queuedMarkers()
    sampled = []

    def clock():
        sampled.append((bool(markers.done), bool(markers.turns)))
        return 100

    class Lock:
        def __enter__(self):
            assert len(sampled) == 1 + int(not markers.done)

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(split.time, "perf_counter_ns", clock)
    markers.lock = Lock()
    callbacks[0](None)
    loop.handles[0].run()
    assert sampled == [(True, True), (False, True)]
    assert not markers.done and not markers.turns
    assert len([row for row in trace.rows if row["stage"] in (
        "server.rpc_done_observed", "server.serializer_next_loop_turn")]) == 2


@pytest.mark.parametrize("callback", ["done", "turn"])
def testCallbackClockFailureStillDrainsPendingEntry(monkeypatch, callback):
    trace, markers, loop, callbacks = queuedMarkers()

    def broken():
        raise RuntimeError("clock unavailable")

    monkeypatch.setattr(split.time, "perf_counter_ns", broken)
    if callback == "done":
        callbacks[0](None)
        assert not markers.done
    else:
        loop.handles[0].run()
        assert not markers.turns
    assert trace.disabled and trace.diagnostic_errors == 1


@pytest.mark.parametrize("disabled", [False, True])
def testQueuedCallbacksDrainWhenRecordingFailsOrObserverIsDisabled(disabled):
    trace, markers, loop, callbacks = queuedMarkers()

    def broken(*_args, **_kwargs):
        raise RuntimeError("private observer fault")

    trace.record = broken
    trace.disabled = disabled
    callbacks[0](None)
    loop.handles[0].run()
    assert not markers.done and not markers.turns
    assert trace.disabled and trace.diagnostic_errors == (0 if disabled else 1)
    loop.closed = True
    markers.retire()
    assert markers.retirement_verified


def testPendingObserversNeverCancelRunningOwnerAndRemainIncompleteAfterShutdown():
    trace = split.AssetSplitTrace(passive_markers=True)
    loop = Loop()
    trace.markers.register_loop("runtime", loop)
    callbacks = []
    context = SimpleNamespace(peer=lambda: "ipv4:127.0.0.1:12345", add_done_callback=callbacks.append)
    trace.markers.handler(metadata(), context)
    trace.markers.serialized(metadata(), 1, None)
    trace.markers.retire()
    assert not trace.markers.retirement_verified
    assert not loop.handles[0].cancelled and callbacks
    assert trace.markers.done and trace.markers.turns
    loop.closed = True
    trace.markers.retire()
    assert loop.handles[0].cancelled
    assert not trace.markers.done and not trace.markers.turns and not trace.markers.peers
    assert not trace.markers.retirement_verified
    assert trace.counters["passive_callbacks_unmatched"] == 2


def testPeerAndPendingLimitsDoNotGrowAndNoRawPeerIsEmitted():
    trace = split.AssetSplitTrace(association_limit=1, passive_markers=True)
    loop = Loop()
    trace.markers.register_loop("runtime", loop)
    callbacks = []
    one = SimpleNamespace(peer=lambda: "private-peer-one", add_done_callback=callbacks.append)
    two = SimpleNamespace(peer=lambda: "private-peer-two", add_done_callback=callbacks.append)
    trace.markers.handler(metadata(), one)
    trace.markers.handler(metadata("two"), two)
    trace.markers.handler(metadata("three"), one)
    trace.markers.serialized(metadata(), 1, None)
    trace.markers.serialized(metadata("two"), 1, None)
    assert trace.counters["passive_peer_overflow"] == trace.counters["passive_done_overflow"] == trace.counters["passive_turn_overflow"] == 1
    assert len(trace.markers.peers) == len(trace.markers.done) == len(trace.markers.turns) == 1
    assert all("private-peer" not in str(row) for row in trace.rows)
    callbacks[0](one)
    loop.handles[0].run()
    loop.closed = True
    trace.markers.retire()
    assert trace.markers.retirement_verified


@pytest.mark.parametrize("phase", ["before_serialization", "after_serialization"])
def testDeadlineRetainsOriginalOutcomeAndQuotaOwnership(tmp_path, phase):
    import cv2
    import numpy as np
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
    from emo_master.apps.runtime.presentation.assets import AssetStore

    class Runtime:
        def __getattr__(self, _name):
            return lambda _request, _context: None

    entered, release = threading.Event(), threading.Event()
    trace = split.AssetSplitTrace(passive_markers=True)
    content = cv2.imencode(".png", np.full((4, 5, 3), 17, np.uint8))[1].tobytes()
    originals = grpc.insecure_channel, grpc.unary_unary_rpc_method_handler, hashlib.sha256, Path.read_bytes
    calls = []
    with patches() as patch, split.installed(trace, patch, tmp_path):
        assets = AssetStore(tmp_path / "assets")
        staging = tmp_path / "one.png"
        staging.write_bytes(content)
        asset = assets.adopt(staging, "job", "result", {})
        trace.register_result(SimpleNamespace(identity=SimpleNamespace(runtimeInstanceId="runtime", jobId="job",
            resultKey="result", resultOrdinal=9), sources=[SimpleNamespace(image=SimpleNamespace(
                resourceId=asset["resourceId"], byteSize=len(content), sha256=asset["sha256"]))]))
        read = assets.read

        def read_once(*args):
            calls.append(1)
            if phase == "before_serialization":
                entered.set()
                assert release.wait(2)
            return read(*args)
        patch(assets, "read", read_once)
        if phase == "after_serialization":
            serialized = trace.markers.serialized

            def hold_send(*args):
                serialized(*args)
                entered.set()
                assert release.wait(2)
            patch(trace.markers, "serialized", hold_send)
        server = AioRuntimeServer(Runtime(), SimpleNamespace(runtimeInstanceId="runtime", assets=assets))

        def client():
            channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
            try:
                return rpc.DisplayServiceStub(channel).ReadAsset(pb.DisplayAssetRequest(
                    runtime_instance_id="runtime", job_id="job", resource_id=asset["resourceId"]), timeout=.08)
            finally:
                channel.close()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(client)
                try:
                    assert entered.wait(2)
                    with pytest.raises(grpc.RpcError) as error:
                        future.result(timeout=2)
                    assert error.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
                    # An observer's done notification must never return the
                    # runtime quota while the real worker/send still owns it.
                    assert server.active["asset"] == 1
                finally:
                    release.set()
        finally:
            release.set()
            server.close()
            assert not any(server.active.values())
            assert not assets.readers
            assets.close()
    assert calls == [1]
    assert originals == (grpc.insecure_channel, grpc.unary_unary_rpc_method_handler, hashlib.sha256, Path.read_bytes)
    report = trace.payload()
    assert report["passive_markers"]["retirement_verified"]
    assert report["passive_markers"]["pending_done"] == report["passive_markers"]["pending_turns"] == 0
    assert report["stage_coverage"]["failed_rpc_calls"] == 1
    assert sum(row["stage"] == "server.rpc_done_observed" for row in trace.rows) == 1
    assert sum(row["stage"] == "server.serializer_next_loop_turn" for row in trace.rows) == (phase == "after_serialization")
