"""Deterministic bridge batching boundaries and Supervisor lifecycle regressions."""
from collections import deque
from types import SimpleNamespace
import queue
import threading

import pytest

from emo_master.apps.runtime.jobs.event_bridge import EventBridge
from tests.runtime.test_job_supervisor_contracts import _supervisor


def _event(number, event_type="node.completed"):
    return {"eventType": event_type, "message": str(number)}


class _Queue:
    def __init__(self, items):
        self.items = deque(items)
        self.blocking_reads = []
        self.nowait_reads = 0
        self.on_nowait = None

    def get(self, *, timeout):
        self.blocking_reads.append(timeout)
        if not self.items:
            raise queue.Empty
        return self.items.popleft()

    def get_nowait(self):
        self.nowait_reads += 1
        if self.on_nowait:
            self.on_nowait()
        if not self.items:
            raise queue.Empty
        return self.items.popleft()


class _Supervisor:
    def __init__(self):
        self.deliveries = []
        self.errors = []
        self.exits = []
        self.stops = []
        self.heartbeats = []

    def consumeWorkerEvent(self, job, event):
        self.deliveries.append(("single", [event]))

    def consumeWorkerEvents(self, job, events):
        self.deliveries.append(("batch", list(events)))

    def bridgeError(self, job, error):
        self.errors.append((job, error))

    def bridgeStopped(self, job):
        self.stops.append(job)

    def processExited(self, job, code):
        self.exits.append((job, code))

    def checkHeartbeat(self, job):
        self.heartbeats.append(job)


def _bridge(items, alive=False, supervisor=None):
    supervisor = supervisor or _Supervisor()
    events = _Queue(items)
    process = SimpleNamespace(is_alive=lambda: alive, exitcode=0)
    bridge = EventBridge(supervisor, "job", process, events, threading.Event())
    return bridge, supervisor, events


@pytest.mark.parametrize("barrier", ["job.started", "job.completed", "job.failed",
    "job.aborted", "job.custom", "display.image", "display.custom",
    "process.heartbeat", "sqlite.write.started", "sqlite.write.completed", "sqlite.write.custom"])
def testPrefetchedBarrierStaysInOrderDuringDeadProcessDrain(barrier):
    items = [_event(0), _event(1), _event(2, barrier), _event(3), _event(4)]
    bridge, supervisor, events = _bridge(items)
    bridge.run()
    assert supervisor.deliveries == [("batch", items[:2]), ("single", items[2:3]),
                                     ("batch", items[3:])]
    assert events.blocking_reads == [0.2]
    assert supervisor.exits == [("job", 0)]
    assert supervisor.stops == ["job"]
    assert not supervisor.errors


@pytest.mark.parametrize("count,sizes", [(32, [32]), (33, [32, 1]), (65, [32, 32, 1])])
def testQueuedCohortsAreBoundedAndFifo(count, sizes):
    items = [_event(i) for i in range(count)]
    bridge, supervisor, events = _bridge(items)
    bridge.run()
    assert [len(batch) for _, batch in supervisor.deliveries] == sizes
    assert [event for _, batch in supervisor.deliveries for event in batch] == items
    assert all(kind == ("single" if len(batch) == 1 else "batch")
               for kind, batch in supervisor.deliveries)
    assert events.blocking_reads == [0.2]


def testSingleEventIsDeliveredWithoutWaitingToFillBatch():
    item = _event(1)
    bridge, supervisor, events = _bridge([item], alive=True)

    def consume(job, event):
        supervisor.deliveries.append(("single", [event]))
        bridge.requestStop()

    supervisor.consumeWorkerEvent = consume
    bridge.run()
    assert supervisor.deliveries == [("single", [item])]
    assert events.blocking_reads == [0.2]
    assert events.nowait_reads == 1
    assert not supervisor.exits
    assert supervisor.stops == ["job"]


@pytest.mark.parametrize("invalid", [None, "noise", 7, ["event"], False])
def testNonDictionaryItemsAreDiscardedWithoutReordering(invalid):
    items = [_event(0), _event(1), invalid, _event(2), _event(3)]
    bridge, supervisor, _ = _bridge(items)
    bridge.run()
    assert [event for _, batch in supervisor.deliveries for event in batch] == items[:2] + items[3:]
    assert not supervisor.errors
    assert supervisor.stops == ["job"]


def testStopDuringCollectionDoesNotDeliverCollectedEventsOrReportExit():
    bridge, supervisor, events = _bridge([_event(i) for i in range(5)], alive=True)
    events.on_nowait = bridge.requestStop
    bridge.run()
    assert events.nowait_reads == 1
    assert not supervisor.deliveries
    assert not supervisor.exits
    assert not supervisor.heartbeats
    assert supervisor.stops == ["job"]


@pytest.mark.parametrize("during_drain", [False, True])
def testBatchFailureReportsBridgeErrorOnceWithoutReplaying(during_drain):
    ordinary = [_event(i) for i in range(3)]
    prefix = [_event(-1, "job.started")] if during_drain else []
    bridge, supervisor, _ = _bridge(prefix + ordinary)
    attempts = []
    failure = RuntimeError("batch commit failed")

    def fail(job, events):
        attempts.append(list(events))
        raise failure

    supervisor.consumeWorkerEvents = fail
    bridge.run()
    assert attempts == [ordinary]
    assert supervisor.deliveries == ([("single", prefix)] if prefix else [])
    assert supervisor.errors == [("job", failure)]
    assert supervisor.stops == ["job"]


def testSupervisorBatchPreservesContextAndIgnoresEventsAfterTerminal():
    supervisor, repository, store, process = _supervisor()
    ordinary = [dict(_event(i), payload={"index": i}, iterationPath=[1, "ignore", 2],
                     nodeId="node", workflowRunId="run") for i in range(2)]
    supervisor.consumeWorkerEvents("job-stop", ordinary)
    stored = store.read("job-stop")
    assert [item.message for item in stored] == ["0", "1"]
    assert all(item.projectId == "project" and item.workflowId == "main" for item in stored)
    assert all(item.iterationPathJson == "[1, 2]" and item.nodeId == "node" for item in stored)
    process.alive = False
    supervisor.consumeWorkerEvent("job-stop", _event(2, "job.completed"))
    before = store.read("job-stop")
    supervisor.consumeWorkerEvents("job-stop", ordinary)
    assert store.read("job-stop") == before
    assert repository.get("job-stop").status == "COMPLETED"


@pytest.mark.parametrize("barrier", ["job.started", "display.image", "process.heartbeat", "sqlite.write.completed"])
def testSupervisorRejectsBatchesContainingLifecycleBarriers(barrier):
    supervisor, _, store, _ = _supervisor()
    with pytest.raises(ValueError, match="invalid ordinary worker event batch"):
        supervisor.consumeWorkerEvents("job-stop", [_event(0), _event(1, barrier)])
    assert store.read("job-stop") == []


@pytest.mark.parametrize("count", [0, 33])
def testSupervisorRejectsInvalidBatchSizes(count):
    supervisor, _, store, _ = _supervisor()
    with pytest.raises(ValueError, match="invalid ordinary worker event batch"):
        supervisor.consumeWorkerEvents("job-stop", [_event(i) for i in range(count)])
    assert store.read("job-stop") == []


def testBatchAfterForcedCancellationDoesNotReopenRetiredJob():
    supervisor, repository, store, process = _supervisor()
    supervisor.consumeWorkerEvents("job-stop", [_event(0), _event(1)])
    assert supervisor.stopJob("job-stop", mode="force") == "ABORTED"
    before = store.read("job-stop")
    supervisor.consumeWorkerEvents("job-stop", [_event(2), _event(3)])
    assert store.read("job-stop") == before
    assert repository.get("job-stop").status == "ABORTED"
    assert process.terminated
    assert "job-stop" not in supervisor._handles


@pytest.mark.parametrize("watcherOrder", ["before_terminal", "after_terminal"])
def testGracefulCancellationStillAcceptsBatchBeforeAbortAndRetires(monkeypatch, watcherOrder):
    supervisor, repository, store, process = _supervisor()
    process.alive = False
    watcherEntered, releaseWatcher, watcherFinished = (threading.Event() for _ in range(3))
    watcherErrors = []
    original = supervisor._enforceGracefulStop

    def controlledWatcher(*args):
        watcherEntered.set()
        try:
            assert releaseWatcher.wait(5), "test did not release graceful watcher"
            original(*args)
        except BaseException as error:
            watcherErrors.append(error)
        finally:
            watcherFinished.set()

    monkeypatch.setattr(supervisor, "_enforceGracefulStop", controlledWatcher)
    try:
        assert supervisor.stopJob("job-stop", mode="graceful") == "STOPPING"
        assert watcherEntered.wait(5)
        if watcherOrder == "before_terminal":
            releaseWatcher.set()
            assert watcherFinished.wait(5)
        supervisor.consumeWorkerEvents("job-stop", [_event(0), _event(1)])
        supervisor.consumeWorkerEvent("job-stop", dict(_event(2, "job.aborted"), code="E_CANCELLED"))
        supervisor.consumeWorkerEvents("job-stop", [_event(3), _event(4)])
        types = [item.eventType for item in store.read("job-stop")]
        assert types.count("node.completed") == 2
        assert types.count("job.aborted") == 1
        assert repository.get("job-stop").errorCode == "E_CANCELLED"
        # Terminal publication is not bridge retirement. With the watcher
        # already finished (or still held), the old immediate-empty assertion
        # fails deterministically: this fake bridge has not reported its exit.
        assert "job-stop" in supervisor._handles
        if watcherOrder == "after_terminal":
            releaseWatcher.set()
            assert watcherFinished.wait(5)
        # Mirror both final actions of the real EventBridge. These notifications
        # must also be safe when a late watcher has already retired the handle.
        supervisor.processExited("job-stop", 0)
        supervisor.bridgeStopped("job-stop")
        assert "job-stop" not in supervisor._handles
        assert "job-stop" not in supervisor._bridges
        assert process.closed
        assert [item.eventType for item in store.read("job-stop")].count("job.aborted") == 1
    finally:
        releaseWatcher.set()
        assert watcherFinished.wait(5)
    assert watcherErrors == []
