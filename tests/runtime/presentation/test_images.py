import hashlib
from itertools import permutations
import time

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.presentation.exporter import ExportPool
from emo_master.apps.runtime.presentation.store import ResultStore
from emo_master.apps.runtime.presentation.collector import unavailable


def waitFor(test, timeout=15):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = test()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError("bounded wait expired")


def testRealImageAndCountOwnershipLeaseAndExpiry(channel, sample, tmp_path):
    record = channel.prepare(sample(tmp_path), tmp_path)
    job = channel.start(record.snapshot.snapshotId)
    result = waitFor(lambda: channel.store.snapshot(job)["results"])[0]
    assert result.status == "COMPLETE"
    count, image = result.sources
    assert count.valueJson == "2" and image.image.ownerResultKey == result.identity.resultKey
    data, digest = channel.assets.read(job, image.image.resourceId)
    assert hashlib.sha256(data).hexdigest() == digest == image.image.sha256
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    assert decoded.shape == (120, 160, 3)
    lease = channel.assets.lease(job, image.image.resourceId, 150)
    waitFor(lambda: channel.runtime.jobRepository.get(job).isTerminal)
    waitFor(lambda: not any(s["busy"] for s in channel.exporter.slots))
    channel.release(job)
    assert channel.assets.read(job, image.image.resourceId)[0] == data
    waitFor(lambda: channel.assets.stats()["assets"] == 0)
    channel.assets.release(lease)
    with pytest.raises(KeyError):
        channel.assets.read(job, image.image.resourceId)


def testOptionalImageNotSilentlyEnabled(channel, sample, tmp_path):
    record = channel.prepare(sample(tmp_path, overlay=False), tmp_path)
    job = channel.start(record.snapshot.snapshotId)
    result = waitFor(lambda: channel.store.snapshot(job)["results"])[0]
    assert result.status == "INCOMPLETE"
    assert result.sources[0].valueJson == "2"
    assert result.sources[1].reasonCode == "OPTIONAL_ABSENT"
    assert channel.exporter.stats["completed"] == 0


def item(ordinal, job="job", scope="scope"):
    return {"identity": {"runtimeInstanceId": "runtime", "jobId": job, "resultScopeId": scope,
                         "invocationId": str(ordinal), "resultKey": f"{job}-{scope}-{ordinal}",
                         "resultOrdinal": ordinal, "executionRevision": "a" * 64,
                         "capturePlanRevision": "b" * 64, "mode": "debug"}, "expected": ["n"]}


@pytest.mark.parametrize("order", list(permutations([1, 2, 3])))
def testStartOrderIsNotCompletionOrder(order):
    store = ResultStore()
    for n in [1, 2, 3]:
        store.begin(item(n))
    for n in order:
        sources = [unavailable("n", "NODE_FAILED")] if n == 3 else [{"sourceId": "n", "state": "AVAILABLE", "valueJson": str(n)}]
        assert store.close(f"job-scope-{n}", sources, "COMPLETED")
        assert not store.close(f"job-scope-{n}", sources, "COMPLETED")
        snapshot = store.snapshot("job")
        assert not snapshot["results"] or snapshot["results"][0].identity.resultOrdinal == 3
    assert store.snapshot("job")["results"][0].status == "INCOMPLETE"
    for job, scope in [("other", "scope"), ("job", "other")]:
        store.begin(item(1, job, scope))
        store.close(f"{job}-{scope}-1", [{"sourceId": "n", "state": "AVAILABLE", "valueJson": "0"}], "COMPLETED")
    assert store.snapshot("job")["high"] == {"scope": 3, "other": 1}


def blockedEncoder(connection, memoryName):
    """Fault only: task has started and never replies; owner must kill it."""
    connection.send("ready")
    connection.recv()
    while True:
        time.sleep(1)


def brokenEncoder(connection, memoryName):
    connection.send("ready")
    connection.recv()
    connection.send("corrupt")
    connection.close()


@pytest.mark.parametrize("worker,expected", [(blockedEncoder, "EXPORT_TIMEOUT"), (brokenEncoder, "EXPORT_FAILED")])
def testRunningExportIsReapedRepeatedly(tmp_path, worker, expected):
    results = []
    pool = ExportPool(tmp_path, lambda task, path, outcome: results.append(outcome), worker=worker)
    try:
        for index in range(3):
            slot = pool.slots[0]
            waitFor(lambda: slot["free"].acquire(False))
            oldPid = slot["process"].pid
            pool.submit({"slot": 0, "shape": [1, 1], "key": str(index)})
            waitFor(lambda: len(results) > index)
            waitFor(lambda: not slot["busy"])
            assert results[-1] == expected
            assert slot["process"].pid != oldPid
        assert pool.stats["reaped"] == 3
    finally:
        pool.close()
    assert all(s["process"] is None for s in pool.slots)


def testForceKillFencesUnsealedResult(channel, sample, tmp_path):
    from copy import deepcopy
    from emo_master.core.project.models import ProjectDocument
    raw = sample(tmp_path, image=False).model_dump()
    raw["workflows"]["child"] = deepcopy(raw["workflows"]["main"])
    raw["workflowOrder"].append("child")
    raw["workflows"]["main"]["nodes"].append({"nodeId": "long", "kind": "loop", "loop": {
        "bodyWorkflowId": "child", "mode": "repeat", "contractVersion": 1,
        "repeatCount": 10000, "maxIterations": 10000}})
    binding = deepcopy(raw["resources"]["parameterBindings"][0])
    binding["target"]["workflowId"] = "child"
    raw["resources"]["parameterBindings"].append(binding)
    record = channel.prepare(ProjectDocument.model_validate(raw), tmp_path)
    job = channel.start(record.snapshot.snapshotId)
    waitFor(lambda: bool(channel.store.open))
    channel.runtime.jobManager.stop(job, "force")
    result = waitFor(lambda: channel.store.snapshot(job)["results"])[0]
    assert result.status == "CANCELLED"
    assert result.sources[0].reasonCode == "EXECUTION_CANCELLED"
    assert not channel.store.open
    assert channel.resourceStats()["total_reserved"] <= 256 * 1024 * 1024


def testSealDeadlineAndLateExportCannotReplaceFailure(channel):
    key = item(1)["identity"]["resultKey"]
    import threading
    channel.jobs["job"] = {"credits": threading.BoundedSemaphore(1), "scopeIds": [], "slots": []}
    channel.jobs["job"]["credits"].acquire()
    try:
        channel.consume("job", {"eventType": "display.open", "item": item(1)})
        started = time.monotonic()
        channel.consume("job", {"eventType": "display.seal", "key": key, "terminal": "COMPLETED",
                                "sealedAt": started, "sources": [{"sourceId": "n", "pendingImage": True}]})
        result = waitFor(lambda: channel.store.snapshot("job")["results"], timeout=1)[0]
        assert .49 <= time.monotonic() - started < .8
        assert result.status == "INCOMPLETE" and result.sources[0].reasonCode == "EXPORT_TIMEOUT"
        channel._exported({"key": key}, None, "EXPORT_FAILED")
        assert channel.store.snapshot("job")["results"][0] is result
    finally:
        channel.jobs.pop("job")
