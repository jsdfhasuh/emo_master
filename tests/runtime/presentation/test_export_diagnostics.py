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
    ("waiting", "completion_wait", "EXPORT_TIMEOUT"),
    ("late_ok", "completion_after_deadline", "EXPORT_TIMEOUT"),
    ("encoder_error", "encoder_reply", "EXPORT_FAILED"),
    ("identity", "encoder_reply", "EXPORT_FAILED"),
    ("normal", "encoder_reply", "AVAILABLE"),
])
def testExportPredicatesHaveBoundedTimelineAndExactlyOnceLaneRetirement(tmp_path, monkeypatch, fault, stage, outcome):
    clock = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: int(clock[0] * 1e9))
    pool = object.__new__(module.ExportPool)
    pool.root, pool.stop = tmp_path, threading.Event()
    pool.stats = dict(completed=0, timed_out=0, failed=0, reaped=0)
    pool.errors, pool.diagnostics = [], deque(maxlen=64)
    credit = threading.BoundedSemaphore(1)
    assert credit.acquire(False)

    class Connection:
        def send(self, task):
            assert task["taskId"] == "task"

        def poll(self, remaining):
            assert 0 <= remaining <= .5
            return fault != "waiting"

        def recv(self):
            if fault == "late_ok":
                clock[0] = 11.0
            return dict(ok=fault != "encoder_error", taskId="wrong" if fault == "identity" else "task",
                        error="InjectedEncodingError" if fault == "encoder_error" else "")

    slot = dict(index=0, queue=queue.Queue(), quarantined=SimpleNamespace(value=False),
        process=object(), connection=Connection(), lock=threading.RLock(), pending={0}, laneFree=[credit], busy=True)
    task = dict(slot=0, lane=0, shape=[1, 1], key="key", jobId="job", sourceId="image", taskId="task",
                submittedNs=9_900_000_000, deadline=9.0 if fault == "queued" else 10.5)
    slot["queue"].put(task)
    callbacks = []

    def callback(task, path, result):
        assert slot["busy"] and not credit.acquire(False), "callback cannot return producer lane early"
        callbacks.append((task, path, result))
        pool.stop.set()

    pool.callback = callback
    def reap(value):
        value["process"] = None
        pool.stats["reaped"] += 1
    pool._reap = reap
    pool._run(slot)
    assert len(callbacks) == 1 and callbacks[0][2] == outcome
    assert pool.stats["timed_out"] == (1 if outcome == "EXPORT_TIMEOUT" else 0)
    record = pool.diagnostics[-1]
    assert record["stage"] == stage and record["taskId"] == "task"
    assert record["resultKey"] == "key" and record["sourceId"] == "image" and record["budgetMs"] == 500
    assert record["callbackEndedNs"] <= record["laneReleasedNs"] <= record["retirementEndedNs"]
    assert record["laneReleased"] and not slot["busy"] and not slot["pending"]
    assert credit.acquire(False) and not credit.acquire(False)
    if fault == "encoder_error":
        assert record["encoderErrorType"] == "InjectedEncodingError"
    if fault == "identity":
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
