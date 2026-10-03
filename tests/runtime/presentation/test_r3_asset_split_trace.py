"""Small accounting and real loopback transport tests; no benchmark workload."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import grpc
import pytest


SPEC = importlib.util.spec_from_file_location("r3_asset_split_trace",
    Path(__file__).resolve().parents[3] / "scripts" / "r3_asset_split_trace.py")
assert SPEC is not None and SPEC.loader is not None
split = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(split)


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


def request(resource="resource", runtime="runtime"):
    return SimpleNamespace(runtime_instance_id=runtime, job_id="job", resource_id=resource)


def register(trace, resource="resource", key="result", size=32):
    trace.register_result(SimpleNamespace(identity=SimpleNamespace(runtimeInstanceId="runtime",
        jobId="job", resultKey=key, resultOrdinal=9), sources=[SimpleNamespace(image=SimpleNamespace(
            resourceId=resource, byteSize=size))]))


def testFiniteRowsAssociationsAndConflictsNeverGuess():
    trace = split.AssetSplitTrace(row_limit=1, association_limit=1, identity_limit=1)
    register(trace)
    register(trace, resource="overflow")
    register(trace, key="conflict")
    assert not trace.identity(request())["resource_match"]
    one = trace.new_call(request(), 1)
    assert one is not None
    assert trace.new_call(request(), 2) is None
    with pytest.raises(ValueError, match="private text"):
        trace.call("test.outer", one, lambda: trace.call("test.inner", one,
            lambda: (_ for _ in ()).throw(ValueError("private text"))))
    payload = trace.payload()
    assert len(payload["rows"]) == 1 and payload["dropped_rows"] == 1
    assert payload["counters"]["identity_overflow"] == 1
    assert payload["counters"]["identity_conflicts"] == 1
    assert payload["counters"]["tokens_overflow"] == 1
    assert "private text" not in json.dumps(payload)
    assert trace.local.metadata is None and trace.local.stage == ""
    assert payload["performance_verdict"] == "NOT_EVALUATED"


def testPublicBindingKeepsOtherMethodsAndEveryCallOption():
    seen = {}

    class Callable:
        def __call__(self, req, *args, **kwargs):
            seen.update(request=req, args=args, kwargs=kwargs)
            return seen["deserialize"](b"wire")

        def future(self, *args, **kwargs):
            return args, kwargs

    inner_call = Callable()

    class Channel:
        def unary_unary(self, method, *args, **kwargs):
            seen["method"] = method
            if "response_deserializer" in kwargs:
                seen["deserialize"] = kwargs["response_deserializer"]
            return inner_call

    trace = split.AssetSplitTrace()
    register(trace)
    channel = split._Channel(Channel(), trace)
    assert channel.unary_unary("/unrelated", marker=True) is inner_call
    call = channel.unary_unary(split.METHOD, request_serializer=lambda value: value,
        response_deserializer=lambda value: value, _registered_method=True)
    req = request()
    credentials, compression = object(), object()
    assert call(req, timeout=.5, metadata=(("authorization", "private-auth-value"), ("custom-bin", b"private-bin-value")),
        credentials=credentials, wait_for_ready=True, compression=compression) == b"wire"
    options = seen["kwargs"]
    assert options["timeout"] == .5 and options["credentials"] is credentials
    assert options["compression"] is compression and options["wait_for_ready"] is True
    assert options["metadata"][:2] == (("authorization", "private-auth-value"), ("custom-bin", b"private-bin-value"))
    assert options["metadata"][2][0] == split.HEADER
    assert "private-auth-value" not in json.dumps(trace.payload()) and "private-bin-value" not in json.dumps(trace.payload())
    assert {row["stage"] for row in trace.rows} == {"client.asset_rpc_split", "client.protobuf_deserialize"}
    assert len({row["call_id"] for row in trace.rows}) == 1
    assert not trace.tokens


def testUnsupportedAndConcurrentDeserializerAssociationIsExplicit():
    trace = split.AssetSplitTrace()
    register(trace)
    active = {}

    class Inner:
        def __call__(self, req, *args, **kwargs):
            return kwargs

        def future(self, *args, **kwargs):
            return "unchanged future"

    call = split._Unary(Inner(), trace, 1, active)
    assert call.future(request(), timeout=.5) == "unchanged future"
    assert None in active
    call(request(), timeout=.5, metadata=((split.HEADER, "user-supplied"),))
    assert trace.counters["reserved_header_conflict"] == 1
    assert not trace.tokens


def testCoverageReportsMissingWithoutChangingDenominators():
    trace = split.AssetSplitTrace()
    register(trace)
    meta = trace.new_call(request(), 1)
    trace.call("client.asset_rpc_split", meta, lambda: None)
    coverage = trace.payload()["stage_coverage"]
    assert coverage["successful_rpc_calls"] == 1
    assert coverage["missing_success_stages"]["server.protobuf_serialize"] == 1
    assert "expected" not in trace.payload() and "denominators" not in trace.payload()


def testReplacedAssociationRemainsIncompleteAfterDrain(tmp_path):
    trace = split.AssetSplitTrace()
    trace.source_before = trace.source_after = {"test-source.py": "a"*64}
    assert trace.associate("replies", 123, {"call_id": "first"})
    assert trace.associate("replies", 123, {"call_id": "second"})
    assert trace.take("replies", 123) == {"call_id": "second"}
    assert not any(trace.payload()["outstanding_associations"].values())
    summary = trace.save(tmp_path / "split.json")
    assert not summary["complete"]
    assert summary["counter_failures"] == {"replies_replaced": 1}


class FakeRuntime:
    def __getattr__(self, _name):
        return lambda _request, _context: None


@pytest.mark.parametrize("consumers,fault", [(1, None), (2, None), (1, "record"), (1, "take"), (1, "associate")])
@pytest.mark.parametrize("passive", [False, True])
def testRealGeneratedLoopbackPairsSameResourceAndRestoresHooks(tmp_path, consumers, fault, passive):
    import cv2
    import numpy as np
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
    from emo_master.apps.runtime.presentation.assets import AssetStore
    from emo_master.clients.runtime import display_session

    originals = grpc.insecure_channel, grpc.unary_unary_rpc_method_handler, hashlib.sha256, Path.read_bytes
    trace = split.AssetSplitTrace(passive_markers=passive)
    pixels = np.full((4, 5, 3), 17, np.uint8)
    ok, encoded = cv2.imencode(".png", pixels)
    assert ok
    content = encoded.tobytes()
    barrier = threading.Barrier(consumers)
    with patches() as patch:
        with split.installed(trace, patch, tmp_path):
            assets = AssetStore(tmp_path / "assets")
            staging = tmp_path / "stage.png"
            staging.write_bytes(content)
            asset = assets.adopt(staging, "job", "result", {})
            register(trace, asset["resourceId"], size=len(content))
            presentation = SimpleNamespace(runtimeInstanceId="runtime", assets=assets)
            server = AioRuntimeServer(FakeRuntime(), presentation)
            injected = []
            if fault:
                def broken(*_args, **_kwargs):
                    injected.append(fault)
                    raise RuntimeError("private observer fault")
                setattr(trace, fault, broken)

            def read(_index):
                channel = grpc.insecure_channel(f"127.0.0.1:{server.port}",
                    options=[("grpc.max_receive_message_length", 9*1024*1024)])
                try:
                    stub = rpc.DisplayServiceStub(channel)
                    assert isinstance(stub, rpc.DisplayServiceStub)
                    trace.local.decoder = True
                    barrier.wait(timeout=3)
                    reply = stub.ReadAsset(pb.DisplayAssetRequest(runtime_instance_id="runtime",
                        job_id="job", resource_id=asset["resourceId"]), timeout=.5,
                        metadata=(("existing-test", "preserved"),))
                    assert hashlib.sha256(reply.content).hexdigest() == asset["sha256"]
                    assert np.array_equal(display_session.decodePng(reply.content), pixels)
                    return reply.content
                finally:
                    trace.local.decoder = False
                    channel.close()
            try:
                with ThreadPoolExecutor(max_workers=consumers) as pool:
                    assert list(pool.map(read, range(consumers))) == [content]*consumers
            finally:
                server.close()
                assets.close()
    assert originals == (grpc.insecure_channel, grpc.unary_unary_rpc_method_handler, hashlib.sha256, Path.read_bytes)
    payload = trace.payload()
    if fault:
        assert injected == [fault] and payload["diagnostic_errors"] == 1
        assert payload["instrumentation_disabled"]
        assert "private observer fault" not in json.dumps(payload)
        return
    assert payload["stage_coverage"]["successful_rpc_calls"] == consumers
    assert payload["stage_coverage"]["missing_success_stages"] == {}
    assert payload["outstanding_associations"] == {"requests": 0, "replies": 0, "tokens": 0}
    assert len({row["call_id"] for row in trace.rows}) == consumers
    for row in trace.rows:
        assert row["resource_id"] == asset["resourceId"] and row["ordinal"] == 9
        assert row["runtime_instance_id"] == "runtime" and row["job_id"] == "job"
        assert row["resource_match"] and row["runtime_matches_local_server"]
        if row["stage"] in ("server.handler", "server.dispatch_to_worker", "server.peer_observed", "server.rpc_done_observed"):
            assert row["thread_cpu_ns"] is None
        elif hasattr(split.time, "thread_time_ns"):
            assert row["thread_cpu_ns"] is not None and row["thread_cpu_ns"] >= 0
    assert not any("path" in row or "content" in row for row in trace.rows)
    if passive:
        assert payload["passive_markers"]["retirement_verified"]
        assert payload["passive_markers"]["pending_done"] == payload["passive_markers"]["pending_turns"] == 0
        assert "127.0.0.1" not in json.dumps(payload)
        for token in {row["call_id"] for row in trace.rows}:
            stages = {row["stage"]: row for row in trace.rows if row["call_id"] == token}
            assert set(split.PASSIVE_STAGES).issubset(stages)
            assert stages["server.serializer_next_loop_turn"]["start_ns"] == stages["server.protobuf_serialize"]["end_ns"]
            assert stages["server.rpc_done_observed"]["start_ns"] >= stages["server.handler"]["end_ns"]
    assert all(not value for name, value in payload["counters"].items()
               if "unmatched" in name or "overflow" in name or "conflict" in name)
    trace.save(tmp_path / "split.json")
    assert json.loads((tmp_path / "split.json").read_text())["role"] == "asset_split"


@pytest.mark.parametrize("passive", [False, True])
def testExceptionRestoresScopedWrappers(tmp_path, passive):
    before = grpc.insecure_channel, hashlib.sha256, Path.read_bytes
    trace = split.AssetSplitTrace(passive_markers=passive)
    with pytest.raises(RuntimeError, match="deliberate"):
        with patches() as patch, split.installed(trace, patch, tmp_path):
            raise RuntimeError("deliberate")
    assert before == (grpc.insecure_channel, hashlib.sha256, Path.read_bytes)
    assert trace.source_before == trace.source_after


@pytest.mark.parametrize("fails", [False, True])
def testRecordFaultPreservesExactOnceResultAndOriginalException(fails):
    trace = split.AssetSplitTrace()
    called = []
    sentinel = ValueError("original operation failure") if fails else object()

    def operation():
        called.append(1)
        if fails:
            raise sentinel
        return sentinel

    def broken(*_args, **_kwargs):
        raise RuntimeError("observer failure")
    trace.record = broken
    if fails:
        with pytest.raises(ValueError) as error:
            trace.call("test.operation", {}, operation)
        assert error.value is sentinel
    else:
        assert trace.call("test.operation", {}, operation) is sentinel
    assert called == [1] and trace.disabled and trace.diagnostic_errors == 1


def testResultObserverFaultReturnsOriginalObjectOnce(tmp_path):
    from emo_master.clients.runtime import display_session
    trace = split.AssetSplitTrace()
    sentinel, calls = object(), []

    def original(_wire):
        calls.append(1)
        return sentinel

    def broken(_result):
        raise RuntimeError("observer failure")
    trace.register_result = broken
    with patches() as patch:
        patch(display_session, "decodeResult", original)
        with split.installed(trace, patch, tmp_path):
            assert display_session.decodeResult(object()) is sentinel
    assert calls == [1] and trace.disabled and trace.diagnostic_errors == 1
