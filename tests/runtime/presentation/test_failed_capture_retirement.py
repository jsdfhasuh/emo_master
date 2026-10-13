"""A proved dead/undispatched reader must not retain raw during callbacks/restart.

Controlled scheduling, not a reconstruction of the historical CI failure.
"""
from collections import deque
import queue
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from emo_master.apps.runtime.presentation import exporter as module
from emo_master.apps.runtime.presentation.collector import ResultCollector


@pytest.mark.parametrize("fault", ["expired_queue", "invalid_snapshot", "snapshot_timeout"])
@pytest.mark.parametrize("gate", ["callback", "restart"])
def testFailedRawOwnerRetiresBeforeCallbackOrRestart(tmp_path, monkeypatch, fault, gate):
    clock = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: int(clock[0] * 1e9))
    pool = object.__new__(module.ExportPool)
    pool.root, pool.stop = tmp_path, threading.Event()
    pool.stats = dict(completed=0, timed_out=0, failed=0, reaped=0)
    pool.errors, pool.diagnostics = [], deque(maxlen=64)
    credit = threading.BoundedSemaphore(1)
    assert credit.acquire(False)
    resumed = threading.Event()
    entered = threading.Event()
    callbacks, failures = [], []

    class Connection:
        def send(self, task):
            assert task["taskId"] == "old"

        def poll(self, remaining):
            assert 0 <= remaining <= .5
            return fault != "snapshot_timeout"

        def recv(self):
            return {"kind": "snapshot", "ok": True, "taskId": "wrong", "rawBytes": 1}

    slot = dict(index=0, queue=queue.Queue(maxsize=2), quarantined=SimpleNamespace(value=False),
        process=object(), connection=Connection(), lock=threading.RLock(), pending={0},
        laneFree=[credit], busy=True, laneCount=1)
    task = dict(slot=0, lane=0, shape=[1, 1], key="old", jobId="job", sourceId="image", taskId="old",
                submittedNs=9_900_000_000, deadline=9.0 if fault == "expired_queue" else 10.5)
    slot["queue"].put(task)

    def waitAtGate():
        entered.set()
        assert resumed.wait(5), "test must release the controlled owner"

    def callback(task, path, outcome):
        callbacks.append(outcome)
        assert path is None
        assert slot["busy"], "raw retirement must not reassign the callback's slab"
        if fault != "expired_queue":
            assert slot["process"] is None, "raw can be returned only after actual reap"
        if gate == "callback":
            waitAtGate()
        # Stop after this task, without consuming a successor descriptor.
        pool.stop.set()

    def reap(value):
        value["process"] = None
        pool.stats["reaped"] += 1

    def restart(value):
        if gate == "restart":
            waitAtGate()
        value["process"] = object()

    # A restart gate needs _run to enter restart before stop is set.
    if gate == "restart":
        def callback(task, path, outcome):
            callbacks.append(outcome)
            assert path is None and slot["busy"]
            slot["process"] = None

        def restart(value):
            waitAtGate()
            value["process"] = object()
            pool.stop.set()

    pool.callback, pool._reap, pool._spawn = callback, reap, restart

    def run():
        try:
            pool._run(slot)
        except BaseException as error:
            failures.append(error)

    owner = threading.Thread(target=run)
    owner.start()
    try:
        assert entered.wait(5)
        assert credit.acquire(False), "proved retired raw must be reusable while callback/restart is gated"
        assert not credit.acquire(False), "retirement must return exactly one raw credit"
        slot["pending"].add(0)  # The next producer owns it but has not published yet.
        with pytest.raises(ValueError, match="owned"):
            pool.slots = [slot]
            pool.releaseSlot(0)
        resumed.set()
        owner.join(5)
        assert not owner.is_alive() and not failures
        assert not credit.acquire(False), "old finalization must never release its successor's reservation"
        assert slot["pending"] == {0} and slot["busy"]
        record = pool.diagnostics[-1]
        assert record["laneReleasedNs"] <= record["callbackStartedNs"]
        assert record["laneReleaseProof"] == ("not_dispatched" if fault == "expired_queue" else "encoder_reaped")
        assert callbacks == ["EXPORT_FAILED" if fault == "invalid_snapshot" else "EXPORT_TIMEOUT"]
    finally:
        resumed.set()
        owner.join(5)


def testRealFailedEncoderReturnsRawBeforeSlowCallback(tmp_path):
    from tests.runtime.presentation.test_images import brokenEncoder

    entered, resume = threading.Event(), threading.Event()
    checks = []

    def callback(task, path, outcome):
        checks.append((outcome, path is None, pool.slots[0]["process"] is None))
        entered.set()
        assert resume.wait(5)
        pool.stop.set()

    pool = module.ExportPool(tmp_path, callback, worker=brokenEncoder)
    descriptor = pool.configureLanes(0, 1)[0]
    collector = ResultCollector({"plan": '{"sources":{},"scopes":{}}', "slots": [descriptor]},
        lambda event: pool.submit(event["descriptor"]))
    pixels = np.full((2, 2, 3), 71, np.uint8)
    try:
        assert collector.image("image", pixels, {"identity": {"resultKey": "old", "jobId": "job"},
                                                "rawBytes": 0})["pendingImage"]
        assert entered.wait(5)
        assert checks == [("EXPORT_FAILED", True, True)]
        assert descriptor["free"].acquire(False), "the real process is dead, not just cancellation-requested"
        descriptor["free"].release()
        assert not pool.slots[0]["pending"] and pool.slots[0]["busy"]
    finally:
        resume.set()
        pool.close()
    assert all(slot["process"] is None and not slot["thread"].is_alive() for slot in pool.slots)


@pytest.mark.parametrize("fault", ["reap_unproved", "release_unknown"])
def testUnprovedRetirementIsQuarantinedAndNeverReturnsAnExtraCredit(tmp_path, fault):
    pool = object.__new__(module.ExportPool)
    pool.root, pool.stop = tmp_path, threading.Event()
    pool.stats = dict(completed=0, timed_out=0, failed=0, reaped=0)
    pool.errors, pool.diagnostics = [], deque(maxlen=64)
    credit = threading.BoundedSemaphore(1)
    assert credit.acquire(False)
    releaseCalls = []

    def release():
        releaseCalls.append(1)
        credit.release()
        raise OSError("release returned but acknowledgment was lost")

    class Connection:
        def send(self, task):
            pass

        def poll(self, remaining):
            return False

    slot = dict(index=0, queue=queue.Queue(), quarantined=SimpleNamespace(value=False),
        process=object(), connection=Connection(), lock=threading.RLock(), pending={0},
        laneFree=[SimpleNamespace(acquire=credit.acquire, release=release) if fault == "release_unknown" else credit],
        busy=True)
    import time
    slot["queue"].put(dict(slot=0, lane=0, shape=[1, 1], key="old", jobId="job", sourceId="image",
                           taskId="old", deadline=time.monotonic() + .5))

    def reap(value):
        if fault == "reap_unproved":
            raise RuntimeError("native process still owns raw")
        value["process"] = None
        pool.stats["reaped"] += 1

    def callback(task, path, outcome):
        assert outcome == "EXPORT_TIMEOUT" and path is None
        assert slot["quarantined"].value and not credit.acquire(False)
        pool.stop.set()

    pool._reap, pool.callback = reap, callback
    pool._run(slot)
    assert not credit.acquire(False) and slot["busy"] and slot["quarantined"].value
    assert releaseCalls == ([1] if fault == "release_unknown" else [])
    record = pool.diagnostics[-1]
    assert not record["laneReleased"]
    assert record["ownerPhase"] == "quarantined"
    assert record.get("laneReleaseErrorType") == ("OSError" if fault == "release_unknown" else None)


def testBusyCaptureKeepsLastAcquisitionIdentityWithoutReusingItsPixels():
    from multiprocessing.shared_memory import SharedMemory

    memory = SharedMemory(create=True, size=12)
    credit = threading.BoundedSemaphore(1)
    descriptor = dict(index=0, lane=0, capacity=12, offset=0, name=memory.name, free=credit)
    events = []
    collector = ResultCollector({"plan": '{"sources":{},"scopes":{}}', "slots": [descriptor]}, events.append)
    first = dict(identity=dict(resultKey="first", jobId="job", invocationId="call-1", resultOrdinal=1), rawBytes=0)
    second = dict(identity=dict(resultKey="second", jobId="job", invocationId="call-2", resultOrdinal=2), rawBytes=0)
    try:
        assert collector.image("image", np.full((2, 2, 3), 71, np.uint8), first)["pendingImage"]
        rejected = collector.image("image", np.full((2, 2, 3), 99, np.uint8), second)
        assert rejected["reasonCode"] == "BUDGET_EXCEEDED" and "busy" in rejected["reason"]
        observed = second["sourceObservations"]["image"]["capture"]
        assert observed["state"] == "BUSY" and observed["slot"] == observed["lane"] == 0
        previous = observed["lastProducerAcquisition"]
        assert previous["resultKey"] == "first" and previous["invocationId"] == "call-1"
        assert previous["acquiredNs"] == events[0]["descriptor"]["captureAcquiredNs"]
        assert previous["acquiredNs"] <= previous["publishedNs"] <= observed["observedNs"]
        assert bytes(memory.buf) == b"\x47" * 12
        assert not credit.acquire(False) and len(events) == 1 and len(collector.lastLaneCaptures) == 1
    finally:
        memory.close()
        memory.unlink()


def testOwnershipSnapshotIsReadOnlyBoundedAndDoesNotWaitForOwnerLock():
    pool = object.__new__(module.ExportPool)
    pool.diagnostics = deque((dict(jobId="job", resultKey=str(index)) for index in range(64)), maxlen=64)
    acquired = []

    class BusyLock:
        def acquire(self, blocking):
            acquired.append(blocking)
            return False

    pool.slots = [dict(index=0, lock=BusyLock())]
    snapshot = pool.ownershipSnapshot("job")
    assert acquired == [False]
    assert snapshot["slots"] == [{"slot": 0, "unknown": "owner_lock_busy"}]
    assert [row["resultKey"] for row in snapshot["recentRetirements"]] == ["60", "61", "62", "63"]
    assert len(pool.diagnostics) == 64
    snapshot["recentRetirements"][0]["resultKey"] = "changed"
    assert pool.diagnostics[60]["resultKey"] == "60"


@pytest.mark.parametrize("diagnosticFails", [False, True])
def testBusyFailureClosureKeepsBothAcquisitionAndParentEvidenceWithoutChangingWire(diagnosticFails):
    from tests.runtime.presentation.test_closure_failures import assembler, opened, seal

    service, credit = assembler()
    key = opened(service, credit)
    capture = dict(state="BUSY", slot=0, lane=0, observedNs=123,
                   lastProducerAcquisition=dict(resultKey="previous", acquiredNs=100))

    def snapshot(jobId):
        assert jobId == "job"
        if diagnosticFails:
            raise OSError("read-only diagnostic unavailable")
        return dict(semantics="non-atomic", recentRetirements=[dict(resultKey="previous", laneReleasedNs=124)])

    service.exporter = SimpleNamespace(ownershipSnapshot=snapshot)
    event = seal(key, dict(sourceId="n", state="UNAVAILABLE", reasonCode="BUDGET_EXCEEDED", reason="busy"))
    event["sourceObservations"] = {"n": {"capture": capture}}
    service._consume("job", event)
    result = service.store.history[-1]
    assert result.status == "INCOMPLETE" and result.sources[0].reasonCode == "BUDGET_EXCEEDED"
    record = service.diagnostics[-1]
    assert record["sourceObservations"]["n"]["capture"] == capture
    if diagnosticFails:
        assert record["laneOwnership"] == {"unknown": "OSError"}
    else:
        assert record["laneOwnership"]["recentRetirements"][0]["resultKey"] == "previous"
    service._finish(key)
    assert credit.released == 1 and not service.pending and not service.store.open


def testNextRealCollectorImageSurvivesFailedPreSnapshotOwnerWithoutBorrowingOldPixels(tmp_path):
    import cv2
    from multiprocessing.shared_memory import SharedMemory

    memory = SharedMemory(create=True, size=12)
    pool = object.__new__(module.ExportPool)
    pool.root, pool.stop = tmp_path, threading.Event()
    pool.stats = dict(completed=0, timed_out=0, failed=0, reaped=0)
    pool.errors, pool.diagnostics = [], deque(maxlen=64)
    entered, resume = threading.Event(), threading.Event()
    replies, decoded, outcomes, failures = [], {}, {}, []

    class Connection:
        def __init__(self, fail):
            self.fail = fail

        def send(self, task):
            if self.fail:
                replies.append(dict(kind="snapshot", ok=True, taskId="wrong", rawBytes=12))
                return

            class ChildConnection:
                consumed = False

                def recv(self):
                    if self.consumed:
                        return None
                    self.consumed = True
                    return task

                def send(self, reply):
                    if reply != "ready":
                        replies.append(reply)

                def close(self):
                    pass

            # Real private copy/PNG path; dispatch scheduling is controlled here.
            module.encodeWorker(ChildConnection(), memory.name)

        def poll(self, remaining):
            return bool(replies)

        def recv(self):
            return replies.pop(0)

    credit = threading.BoundedSemaphore(1)
    slot = dict(index=0, memory=memory, queue=queue.Queue(maxsize=2), quarantined=SimpleNamespace(value=False),
        process=object(), connection=Connection(True), lock=threading.RLock(), pending=set(),
        pendingDescriptors={}, laneFree=[credit], laneCount=1, busy=False, active=False)
    pool.slots = [slot]

    def reap(value):
        value["process"] = None
        pool.stats["reaped"] += 1

    def spawn(value):
        value["process"], value["connection"] = object(), Connection(False)

    def callback(task, path, outcome):
        outcomes[task["key"]] = outcome
        if task["key"] == "first":
            assert path is None and slot["process"] is None
            entered.set()
            assert resume.wait(5)
        else:
            assert outcome == "AVAILABLE", task["exportDiagnostic"]
            decoded[task["key"]] = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
            pool.stop.set()

    pool._reap, pool._spawn, pool.callback = reap, spawn, callback
    collector = ResultCollector({"plan": '{"sources":{},"scopes":{}}', "slots": pool.descriptors()},
        lambda event: pool.submit(event["descriptor"]))

    def run():
        try:
            pool._run(slot)
        except BaseException as error:
            failures.append(error)

    def capture(key, value):
        return collector.image("image", value, {"identity": {"resultKey": key, "jobId": "job"}, "rawBytes": 0})

    owner = threading.Thread(target=run)
    owner.start()
    second = np.full((2, 2, 3), 231, np.uint8)
    try:
        assert capture("first", np.full_like(second, 19))["pendingImage"]
        assert entered.wait(5)
        assert capture("second", second).get("pendingImage"), "dead predecessor must not reject the actual next source"
        assert capture("third", second)["reasonCode"] == "BUDGET_EXCEEDED", "real next owner still consumes exactly one lane"
        resume.set()
        owner.join(5)
        assert not owner.is_alive() and not failures
        assert outcomes == {"first": "EXPORT_FAILED", "second": "AVAILABLE"}
        np.testing.assert_array_equal(decoded["second"], second)
        assert not slot["pending"] and not slot["busy"]
        assert credit.acquire(False) and not credit.acquire(False)
        credit.release()
        assert all(row["laneReleased"] for row in pool.diagnostics)
    finally:
        resume.set()
        pool.stop.set()
        owner.join(5)
        memory.close()
        memory.unlink()
