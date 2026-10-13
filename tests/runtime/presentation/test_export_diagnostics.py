"""Deterministic exporter predicates; actual spawn/reap is in test_images."""
from collections import deque
import queue
import threading
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.presentation import exporter as module
from tests.runtime.presentation.test_closure_failures import assembler, opened, seal


@pytest.mark.parametrize("fault,stage,outcome", [
    ("queued", "queue_wait", "EXPORT_TIMEOUT"),
    ("waiting", "snapshot_wait", "EXPORT_TIMEOUT"),
    ("late_snapshot", "snapshot_after_deadline", "EXPORT_TIMEOUT"),
    ("snapshot_identity", "snapshot_reply", "EXPORT_FAILED"),
    ("snapshot_bytes", "snapshot_reply", "EXPORT_FAILED"),
    ("no_snapshot", "snapshot_reply", "EXPORT_FAILED"),
    ("copy_error", "snapshot_reply", "EXPORT_FAILED"),
    ("duplicate_snapshot", "encoder_reply", "EXPORT_FAILED"),
    ("completion_wait", "completion_wait", "EXPORT_TIMEOUT"),
    ("late_ok", "completion_after_deadline", "EXPORT_TIMEOUT"),
    ("encoder_error", "encoder_reply", "EXPORT_FAILED"),
    ("reap_failure", "encoder_reply", "EXPORT_FAILED"),
    ("identity", "encoder_reply", "EXPORT_FAILED"),
    ("normal", "encoder_reply", "AVAILABLE"),
])
@pytest.mark.parametrize("successor", [False, True])
def testExportPredicatesHaveBoundedTimelineAndExactlyOnceLaneRetirement(tmp_path, monkeypatch, fault, stage, outcome, successor):
    clock = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: int(clock[0] * 1e9))
    pool = object.__new__(module.ExportPool)
    pool.root, pool.stop = tmp_path, threading.Event()
    pool.stats = dict(completed=0, timed_out=0, failed=0, reaped=0)
    pool.errors, pool.diagnostics = [], deque(maxlen=64)
    credit = threading.BoundedSemaphore(1)
    assert credit.acquire(False)
    snapshotAccepted = fault not in {"queued", "waiting", "late_snapshot", "snapshot_identity",
                                     "snapshot_bytes", "no_snapshot", "copy_error"}
    waits = []

    class Connection:
        replies = 0

        def send(self, task):
            assert task["taskId"] == "task"

        def poll(self, remaining):
            assert 0 <= remaining <= .5
            waits.append(remaining)
            if self.replies == 1:
                assert snapshotAccepted
                if successor:
                    assert credit.acquire(False), "next producer must be able to own the released raw lane"
                    slot["pending"].add(0)
            return fault != "waiting" and not (fault == "completion_wait" and self.replies == 1)

        def recv(self):
            self.replies += 1
            if self.replies == 1:
                clock[0] = 11.0 if fault == "late_snapshot" else 10.2
                return dict(kind="complete" if fault in {"no_snapshot", "copy_error"} else "snapshot",
                            ok=fault != "copy_error", taskId="wrong" if fault == "snapshot_identity" else "task",
                            rawBytes=2 if fault == "snapshot_bytes" else 1, error="CopyError" if fault == "copy_error" else "")
            if fault == "late_ok":
                clock[0] = 11.0
            return dict(kind="snapshot" if fault == "duplicate_snapshot" else "complete",
                        ok=fault not in {"encoder_error", "reap_failure"}, taskId="wrong" if fault == "identity" else "task",
                        error="InjectedEncodingError" if fault == "encoder_error" else "")

    slot = dict(index=0, queue=queue.Queue(), quarantined=SimpleNamespace(value=False),
        process=object(), connection=Connection(), lock=threading.RLock(), pending={0}, laneFree=[credit], busy=True)
    task = dict(slot=0, lane=0, shape=[1, 1], key="key", jobId="job", sourceId="image", taskId="task",
                submittedNs=9_900_000_000, deadline=9.0 if fault == "queued" else 10.5)
    slot["queue"].put(task)
    callbacks = []

    def callback(task, path, result):
        assert slot["busy"], "callback still owns the staging file and slab assignment"
        acquired = credit.acquire(False)
        assert acquired == (not (snapshotAccepted and successor) and fault != "reap_failure"), "only a private snapshot, proved process exit, or no dispatch ends raw ownership"
        if acquired:
            credit.release()
        callbacks.append((task, path, result))
        pool.stop.set()

    pool.callback = callback
    def reap(value):
        if fault == "reap_failure":
            raise RuntimeError("injected inability to prove encoder exit")
        value["process"] = None
        pool.stats["reaped"] += 1
    pool._reap = reap
    pool._run(slot)
    assert len(callbacks) == 1 and callbacks[0][2] == outcome
    assert pool.stats["timed_out"] == (1 if outcome == "EXPORT_TIMEOUT" else 0)
    record = pool.diagnostics[-1]
    assert record["stage"] == stage and record["taskId"] == "task"
    assert record["resultKey"] == "key" and record["sourceId"] == "image" and record["budgetMs"] == 500
    if snapshotAccepted:
        assert record["snapshotReplyNs"] <= record["laneReleasedNs"] <= record["callbackStartedNs"]
        assert waits == pytest.approx([.5, .3]), "snapshot wait must consume the original deadline"
    else:
        assert record["laneReleasedNs"] <= record["callbackStartedNs"] <= record["retirementEndedNs"]
        assert record["laneReleaseProof"] == ("not_dispatched" if fault == "queued" else "encoder_reaped")
        if fault != "queued":
            assert record["reapedNs"] <= record["laneReleasedNs"]
    hasSuccessor = successor and snapshotAccepted
    assert record["laneReleased"] and slot["busy"] == (hasSuccessor or fault == "reap_failure")
    assert bool(slot["pending"]) == hasSuccessor
    assert slot["quarantined"].value == (fault == "reap_failure")
    assert credit.acquire(False) == (not hasSuccessor and fault != "reap_failure")
    assert not credit.acquire(False), "failed/duplicate completion must never release a successor's credit"
    assert pool.stats["reaped"] == (outcome != "AVAILABLE" and fault not in {"queued", "reap_failure"})
    if fault == "encoder_error":
        assert record["encoderErrorType"] == "InjectedEncodingError"
    if fault in {"identity", "snapshot_identity"}:
        assert record["actualTaskId"] == "wrong"
    assert len(pool.diagnostics) <= 64


def testAssetAdoptionFailureRetainsItsPhaseAndCorrelatedError(tmp_path):
    service, credit = assembler()
    key = opened(service, credit)
    def adopt(*_):
        raise OSError("injected adoption failure")
    service.assets.adopt = adopt
    task = dict(key=key, jobId="job", sourceId="n", provenance={},
                exportDiagnostic=dict(taskId="task", resultKey=key, stage="encoder_reply", outcome="AVAILABLE"))
    service._exported(task, tmp_path / "image.png", "AVAILABLE")
    service._consume("job", seal(key, {"sourceId": "n", "pendingImage": True}))
    result = service.store.history[-1]
    assert result.status == "INCOMPLETE" and result.sources[0].reasonCode == "EXPORT_FAILED"
    record = service.diagnostics[-1]
    assert record["path"] == "local-presentation-store" and record["unavailableSourceIds"] == ["n"]
    assert record["exports"][0]["stage"] == "asset_adoption"
    assert record["exports"][0]["adoptionError"]["type"] == "OSError"
    assert "injected adoption failure" in record["exports"][0]["adoptionError"]["message"]
    assert credit.released == 1 and not service.pending
