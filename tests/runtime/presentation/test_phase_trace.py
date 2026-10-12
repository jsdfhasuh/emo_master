"""Opt-in evidence must not change result/credit semantics or wait for an owner."""
import json
import multiprocessing
import threading

import pytest

from emo_master.apps.runtime.presentation.phase_trace import (
    LocalPhaseTrace, SharedPhaseTrace, captureFailureSnapshot,
)
from tests.runtime.presentation.test_capture_mailbox_recovery import _Harness


def traced(root, **kwargs):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    h = _Harness(root, **kwargs)
    h.config["phaseTrace"] = SharedPhaseTrace(multiprocessing.get_context("spawn"))
    h.service.phaseTraces = {"job": LocalPhaseTrace()}
    h.collector = ResultCollector(h.config, h.emit)
    h.runner.resultCollector = h.collector
    return h


def phases(trace):
    return [row["phase"] for row in trace.snapshot()["records"]]


def testExportAvailableWithoutSealIsNotAClosedResult(tmp_path):
    from emo_master.apps.runtime.workflow.context import RunContext
    import numpy as np
    h = traced(tmp_path)
    try:
        context = RunContext("job", "detect", "invocation-1")
        h.collector.begin(context)
        h.collector.output(context, {"image": np.zeros((2, 2, 3), np.uint8), "count": 7}, workflow=True)
        h.drain()
        assert not h.results() and h.credits() == 7
        snapshot = captureFailureSnapshot(h.service, "job")
        assert snapshot["state"]["open"][0]["hasSeal"] is False
        assert snapshot["state"]["open"][0]["exported"] == ["scope-0-image"]
        assert "finish.wait_seal" in phases(h.service.phaseTraces["job"])
        assert "scope.end" not in phases(h.config["phaseTrace"])
        h.collector.end(context, "COMPLETED")
        h.drain()
        assert len(h.results()) == 1 and h.credits() == 8
        rows = h.config["phaseTrace"].snapshot()["records"]
        assert "scope.end" in phases(h.config["phaseTrace"])
        assert "seal.sent" in phases(h.config["phaseTrace"])
        key = h.results()[0].identity.resultKey
        assert {r["resultKey"] for r in rows} == {key}
        assert all(r["invocationId"] == "invocation-1" and r["jobId"] == "job" for r in rows)
        assert "store.closed" in phases(h.service.phaseTraces["job"])
    finally:
        h.close()


def testSealWaitingForImageAndRepeatedFinishKeepOneCredit(tmp_path):
    h = traced(tmp_path)
    try:
        h.service.exporter.hold = True
        h.run(1)
        h.drain()
        key = next(iter(h.service.pending))
        for _ in range(100):
            h.service._finish(key)
        snap = captureFailureSnapshot(h.service, "job")
        assert snap["state"]["open"][0]["waitingSources"] == ["scope-0-image"]
        assert phases(h.service.phaseTraces["job"]).count("finish.wait_sources") == 1
        assert h.credits() == 7
        h.service.exporter.finish()
        h.service._finish(key)
        assert h.credits() == 8 and len(h.results()) == 1
        assert "finish.wait_seal" not in phases(h.service.phaseTraces["job"])
    finally:
        h.close()


def testValidationFailureRecordsErrorTypeWithoutScalarPayload(tmp_path, monkeypatch):
    h = traced(tmp_path, image=False)
    original = h.service.store.close
    calls = []

    def failOnce(*args):
        calls.append(args)
        if len(calls) == 1:
            raise ValueError("PRIVATE_PAYLOAD_MUST_NOT_APPEAR")
        return original(*args)

    monkeypatch.setattr(h.service.store, "close", failOnce)
    try:
        h.run(1)
        h.drain()
        rows = h.service.phaseTraces["job"].snapshot()["records"]
        assert any(r["phase"] == "store.validation_error" and r["errorType"] == "ValueError" for r in rows)
        assert "PRIVATE_PAYLOAD" not in json.dumps(rows)
        assert h.results()[0].status == "INCOMPLETE" and h.credits() == 8
        assert len(calls) == 2
    finally:
        h.close()


@pytest.mark.parametrize("shared", [False, True])
def testTraceHasFixedBudgetPreservesFirstEntriesAndNeverSerializesPayload(shared):
    trace = SharedPhaseTrace(multiprocessing.get_context("spawn")) if shared else LocalPhaseTrace()
    for index in range(1000):
        trace.record("test", {"jobId": "job", "resultOrdinal": 1}, index=index,
                     payload="PRIVATE", unexpected=object())
    value = trace.snapshot()
    assert len(value["records"]) == trace.limit
    assert value["truncated"] is True
    assert [row["sequence"] for row in value["records"]] == list(range(1, trace.limit + 1))
    assert "PRIVATE" not in json.dumps(value)
    assert len(json.dumps(value)) < 128 * 1024


def testSnapshotNeverWaitsForStoreLock(tmp_path):
    h = traced(tmp_path, image=False)
    ready, stop = threading.Event(), threading.Event()

    def owner():
        with h.service.store.lock:
            ready.set()
            stop.wait()

    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert ready.wait(2)
        snapshot = captureFailureSnapshot(h.service, "job")
        assert snapshot["state"] == {"unknown": "store_lock_unavailable"}
        assert snapshot["observedNs"] > 0
    finally:
        stop.set()
        thread.join(2)
        assert not thread.is_alive()
        h.close()


def _writeTrace(trace, ready):
    trace.record("scope.begin", {"jobId": "job", "invocationId": "child"})
    ready.set()


def testSpawnProducerTraceReadableWithoutPresentationQueue():
    context = multiprocessing.get_context("spawn")
    trace, ready = SharedPhaseTrace(context), context.Event()
    process = context.Process(target=_writeTrace, args=(trace, ready))
    try:
        process.start()
        assert ready.wait(10)
        process.join(10)
        assert process.exitcode == 0
        rows = trace.snapshot()["records"]
        assert rows[0]["pid"] == process.pid and rows[0]["invocationId"] == "child"
        # An interrupted next slot never publishes a length. Do not deserialize it.
        trace.data[trace.capacity] = 255
        assert trace.snapshot()["records"] == rows
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        process.close()


def testFullSealTraceKeepsOriginalFailureAndRetriedIdentity(tmp_path):
    h = traced(tmp_path, image=False)
    try:
        h.block_seals = True
        h.run(1)
        h.drain()
        original = next(iter(h.collector.pendingSeals))
        failed = [r for r in h.config["phaseTrace"].snapshot()["records"] if r["phase"] == "seal.send_failed"]
        assert len(failed) == 1 and failed[0]["errorType"] == "Full"
        assert h.credits() == 7
        h.block_seals = False
        h.run(2)
        h.drain()
        rows = h.config["phaseTrace"].snapshot()["records"]
        assert {r["resultKey"] for r in rows} == {original}
        assert phases(h.config["phaseTrace"]).count("seal.sent") == 1
        assert len(h.results()) == 2 and h.credits() == 8
        assert not h.collector.traceIdentities
    finally:
        h.close()


def testBrokenOptionalObserverDoesNotChangeCaptureResult(tmp_path, monkeypatch):
    h = traced(tmp_path, image=False)
    def broken(*_, **__):
        raise OSError("observer failed")
    monkeypatch.setattr(h.config["phaseTrace"], "record", broken)
    monkeypatch.setattr(h.service.phaseTraces["job"], "record", broken)
    try:
        h.run(1)
        h.drain()
        assert h.results()[0].status == "COMPLETE" and h.credits() == 8
        assert h.runner.captureErrors == 0
    finally:
        h.close()


def testOptionalTraceAllocationFailureCannotRejectJob(channel, tmp_path, monkeypatch):
    from emo_master.apps.runtime.presentation import phase_trace
    from tests.runtime.presentation.test_normal_capture import load, request, release
    from tests.runtime.runtime_test_utils import waitForTerminal
    monkeypatch.setenv("EMO_PRESENTATION_TRACE", "1")
    def unavailable(*_):
        raise OSError("private allocation context")
    monkeypatch.setattr(phase_trace, "SharedPhaseTrace", unavailable)
    load(channel.runtime, tmp_path)
    reply = channel.runtime.StartJob(request(channel.runtime), None)
    assert reply.ok, reply.message
    assert waitForTerminal(channel.runtime, reply.job_id).status == "COMPLETED"
    value = captureFailureSnapshot(channel, reply.job_id)
    assert value["producer"] == {"unknown": "OSError"}
    assert "private allocation context" not in json.dumps(value)
    release(channel, reply.job_id)
    assert not channel.phaseTraces
