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
    waitFor(lambda: channel.runtime.jobRepository.get(job).isTerminal)
    waitFor(lambda: not any(s["busy"] for s in channel.exporter.slots))
    waitFor(lambda: not channel.readers[job].is_alive())
    from emo_master.apps.runtime.preview.store import _ioPath
    leftover = _ioPath(channel.root / "jobs" / job / "preview_staging" / "assets") / ("." + "a" * 64 + ".json." + "b" * 32 + ".tmp")
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_bytes(b"interrupted atomic preview write")
    assert leftover.exists()
    leases = [channel.assets.lease(job, image.image.resourceId, 30000) for _ in range(16)]
    with pytest.raises(ValueError, match="budget"):
        channel.assets.lease(job, image.image.resourceId, 30000)
    for handle in leases:
        channel.assets.release(handle)
    with pytest.raises(ValueError, match="budget"):
        channel.assets.lease(job, image.image.resourceId, 30001)
    lease = channel.assets.lease(job, image.image.resourceId, 150)
    channel.release(job)
    assert not leftover.exists()
    assert not (channel.root / "jobs" / job).exists()
    assert not any(result is not None and result.identity.jobId == job for _, result in channel.store.events)
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
    completed = []
    for n in order:
        sources = [unavailable("n", "NODE_FAILED")] if n == 3 else [{"sourceId": "n", "state": "AVAILABLE", "valueJson": str(n)}]
        assert store.close(f"job-scope-{n}", sources, "COMPLETED")
        assert not store.close(f"job-scope-{n}", sources, "COMPLETED")
        completed.append(n)
        snapshot = store.snapshot("job")
        assert snapshot["results"][0].identity.resultOrdinal == max(completed)
    assert store.snapshot("job")["results"][0].status == "INCOMPLETE"
    store.begin(item(4))
    assert store.snapshot("job")["results"][0].identity.resultOrdinal == 3
    assert store.snapshot("job")["high"]["scope"] == 4
    for job, scope in [("other", "scope"), ("job", "other")]:
        store.begin(item(1, job, scope))
        store.close(f"{job}-{scope}-1", [{"sourceId": "n", "state": "AVAILABLE", "valueJson": "0"}], "COMPLETED")
    assert store.snapshot("job")["high"] == {"scope": 4, "other": 1}


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


def testInterruptedMailboxAndCorruptionAreBounded():
    import multiprocessing
    import queue
    from emo_master.apps.runtime.presentation.mailbox import SharedMailbox
    mailbox = SharedMailbox(multiprocessing.get_context("spawn"))
    mailbox.states[0] = 1  # publisher killed during copy, never publishes READY
    started = time.perf_counter()
    with pytest.raises(queue.Empty):
        mailbox.get(timeout=.03)
    assert time.perf_counter() - started < .2
    mailbox.states[0] = 0
    mailbox.put_nowait({"value": "frozen"})
    mailbox.data[16] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        mailbox.get(timeout=.03)
    mailbox.put_nowait({"value": "next"})
    assert mailbox.get(timeout=.03) == {"value": "next"}
    for i in range(16):
        mailbox.put_nowait({"value": i})
    with pytest.raises(queue.Full):
        mailbox.put_nowait({"value": 17})
    assert [mailbox.get(timeout=.03)["value"] for _ in range(16)] == list(range(16))


def testTrustedFrameAdaptersDoNotGuessBySizeOrFileName():
    from types import SimpleNamespace
    from emo_master.apps.runtime.presentation.provenance import FrameTracker, ADAPTERS
    tracker = FrameTracker(ADAPTERS)
    a, b = np.zeros((5, 5), np.uint8), np.zeros((5, 5), np.uint8)
    loader = SimpleNamespace(operatorId="vision.io.image_loader")
    tracker.observe(loader, {}, {"image": a})
    tracker.observe(loader, {}, {"image": b})
    assert tracker.lookup(a)["frameIdentity"] != tracker.lookup(b)["frameIdentity"]
    overlay = a.copy()
    tracker.observe(SimpleNamespace(operatorId="vision.analysis.blob"), {"image": a}, {"overlay": overlay})
    assert tracker.lookup(overlay)["parentFrameIdentity"] == tracker.lookup(a)["frameIdentity"]
    assert tracker.lookup(overlay)["trust"] == "proven"
    tracker.observe(SimpleNamespace(operatorId="custom.multi-input"), {"a": a, "b": b}, {"image": overlay})
    assert tracker.lookup(overlay)["trust"] == "unknown"


def testNestedValueFreezeDoesNotFollowMutation():
    from emo_master.core.presentation.values import freezeValue
    import json
    value = {"items": [{"area": 1}, {"area": 2}]}
    frozen = freezeValue(value)
    value["items"][0]["area"] = 100
    assert json.loads(frozen)["items"][0]["area"] == 1


def testForceKillInsideStartedChildScope(tmp_path):
    from examples.runtime_pages_p2 import pacedProject, pluginRoots
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.presentation.service import PresentationService
    from emo_master.core.project.models import WorkflowNode
    runtime = RuntimeService(dbPath=tmp_path / "state.db", workspaceRoot=tmp_path / "jobs", pluginRootPaths=pluginRoots(tmp_path))
    presentation = PresentationService(runtime, tmp_path / "display")
    try:
        project = pacedProject(tmp_path, count=100)
        # Explicit fault gate inside the selected child scope, after it opens.
        project.workflows["detect"].nodes.append(WorkflowNode(nodeId="hold", operatorId="test.p2.pace"))
        prepared = presentation.prepare(project, tmp_path)
        job = presentation.start(prepared.snapshot.snapshotId)
        waitFor(lambda: bool(presentation.store.open))
        runtime.jobManager.stop(job, "force")
        result = waitFor(lambda: presentation.store.snapshot(job)["results"])[0]
        assert result.status == "CANCELLED"
        assert all(s.reasonCode == "EXECUTION_CANCELLED" for s in result.sources)
        assert not presentation.store.open
    finally:
        runtime.close()
        presentation.close()
