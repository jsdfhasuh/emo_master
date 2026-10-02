"""Characterize the existing Subscribe poll boundary, without measuring latency.

Only the RPC module's sleep reference is replaced by a controller gate. The real
Subscribe generator, _snapshot adapter, protobuf conversion and ResultStore run.
Event deadlines are deadlock guards, never evidence of a scheduling interval.
"""
from concurrent.futures import Future
import threading
import time
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.grpc_server.aio_entry import SyncContext
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.presentation import rpc as display_rpc
from emo_master.apps.runtime.presentation.store import ResultStore


class PollGate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.requested = []

    def sleep(self, seconds):
        self.requested.append(seconds)
        assert len(self.requested) == 1, "unexpected extra poll before delivery"
        self.entered.set()
        assert self.release.wait(5), "test controller did not release poll gate"


def closeScalar(store, ordinal):
    key = f"job-root-{ordinal}"
    assert store.begin({"identity": {
        "runtimeInstanceId": "runtime", "jobId": "job", "resultScopeId": "root",
        "invocationId": str(ordinal), "resultKey": key, "resultOrdinal": ordinal,
        "executionRevision": "a" * 64, "capturePlanRevision": "b" * 64,
        "mode": "runtime"}, "expected": ["count"]})
    assert store.close(key, [{"sourceId": "count", "state": "AVAILABLE",
                              "valueJson": str(ordinal)}], "COMPLETED")
    return key


def harness(monkeypatch):
    store = ResultStore()
    adapter = display_rpc.DisplayRpc(SimpleNamespace(
        store=store, jobs={"job": {}}, runtimeInstanceId="runtime"))
    snapshots = []
    originalSnapshot = adapter._snapshot

    def observeSnapshot(*args, **kwargs):
        result = originalSnapshot(*args, **kwargs)
        snapshots.append(result)
        return result

    monkeypatch.setattr(adapter, "_snapshot", observeSnapshot)
    gate = PollGate()
    # Do not patch time.sleep globally: ResultStore and all other code retain
    # the real time module. Preserve wireResult's real clock as well.
    monkeypatch.setattr(display_rpc, "time", SimpleNamespace(
        sleep=gate.sleep, perf_counter_ns=time.perf_counter_ns))
    context = SyncContext()
    request = pb.DisplayRequest(job_id="job", runtime_instance_id="runtime")
    stream = adapter.Subscribe(request, context)
    return store, stream, context, gate, snapshots


def nextInThread(stream):
    result = Future()

    def consume():
        try:
            result.set_result(next(stream))
        except BaseException as error:
            result.set_exception(error)

    thread = threading.Thread(target=consume, name="subscribe-poll-gate", daemon=True)
    thread.start()
    return thread, result


def retire(stream, context, gate, thread):
    context.cancelled.set()
    gate.release.set()
    thread.join(5)
    assert not thread.is_alive(), "Subscribe test consumer did not retire"
    stream.close()


def testPublicationAfterEmptySnapshotWaitsForNextPoll(monkeypatch):
    store, stream, context, gate, snapshots = harness(monkeypatch)
    thread, delivery = nextInThread(stream)
    try:
        assert gate.entered.wait(5), "Subscribe did not reach its poll boundary"
        # The original snapshot and conversion have completed before the gate.
        assert len(snapshots) == 1
        assert snapshots[0].cursor == 0
        assert not snapshots[0].results
        assert not snapshots[0].reset_required
        assert gate.requested == [0.02]

        key = closeScalar(store, 1)
        available = store.snapshot("job", cursor=0, incremental=True)
        assert available["cursor"] == 2  # Real begin + real close notifications.
        assert [r.identity.resultKey for r in available["results"]] == [key]
        # No elapsed-time assertion: the consumer cannot leave the known gate.
        # Store publication has no route to release that sleep continuation.
        assert not gate.release.is_set()
        assert not delivery.done()
        assert len(snapshots) == 1

        gate.release.set()
        message = delivery.result(timeout=5)
        assert len(snapshots) == 2
        assert message is snapshots[1]
        assert message.cursor == available["cursor"]
        assert message.results[0].identity.result_key == key
        assert message.results[0].sources[0].value_json == "1"
        assert not message.reset_required
    finally:
        retire(stream, context, gate, thread)


@pytest.mark.parametrize("burstSize", [4, 40])
def testEveryYieldAlsoRequiresPollBoundaryAndPreservesBoundedReplay(monkeypatch, burstSize):
    store, stream, context, gate, snapshots = harness(monkeypatch)
    closeScalar(store, 1)
    first = next(stream)
    assert first.cursor == 2
    assert first.results[0].identity.result_ordinal == 1
    assert gate.requested == []  # First snapshot is immediate.

    thread, delivery = nextInThread(stream)
    try:
        assert gate.entered.wait(5), "post-yield continuation skipped poll boundary"
        assert gate.requested == [0.02]
        assert len(snapshots) == 1  # No second snapshot precedes this cooldown.
        for ordinal in range(2, burstSize + 2):
            closeScalar(store, ordinal)
        expected = store.snapshot("job", cursor=first.cursor, incremental=True)
        assert expected["cursor"] == 2 * (burstSize + 1)
        assert len(store.events) <= 32
        assert len(store.history) <= 32
        assert len(store.latest) == 1
        assert store.metadataBytes() <= store.metadataLimit
        assert not gate.release.is_set()
        assert not delivery.done()
        assert len(snapshots) == 1

        gate.release.set()
        message = delivery.result(timeout=5)
        assert len(snapshots) == 2
        assert message.cursor == expected["cursor"]
        assert message.reset_required == expected["reset"]
        assert [r.identity.result_key for r in message.results] == [
            r.identity.resultKey for r in expected["results"]]
        if burstSize == 4:
            assert not message.reset_required
            assert [r.identity.result_ordinal for r in message.results] == [2, 3, 4, 5]
        else:
            assert message.reset_required
            assert [r.identity.result_ordinal for r in message.results] == [41]
    finally:
        retire(stream, context, gate, thread)
