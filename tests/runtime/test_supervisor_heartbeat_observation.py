"""A watchdog decision uses the beginning of its shared-cell observation."""
from __future__ import annotations

import json
import multiprocessing
from threading import Event

import pytest

from emo_master.apps.runtime.jobs.heartbeat import HeartbeatCell, monotonicMs
from tests.runtime.test_heartbeat_liveness import _withCell
from tests.runtime.test_job_supervisor_contracts import _supervisor


def testReadDelayDoesNotExpireAnInitiallyFreshSample(monkeypatch):
    supervisor, repository, store, process, cell, clock = _withCell(monkeypatch)
    clock[0] = 900
    cell.publish()
    clock[0] = 1000
    read = cell.read

    def delayedRead():
        sample = read()
        clock[0] = 6350
        cell.publish()  # The producer continues while the reader is descheduled.
        return sample

    monkeypatch.setattr(cell, "read", delayedRead)
    supervisor.checkHeartbeat("job-stop")
    assert repository.get("job-stop").status == "RUNNING"
    assert not process.terminated
    assert supervisor._heartbeatMonotonic["job-stop"] == 900
    assert supervisor._heartbeatTimeouts["job-stop"] == 5000
    assert supervisor._heartbeatIntervals["job-stop"] == 500
    assert not store.read("job-stop")


def testSamplePublishedDuringReadWindowIsCachedWithoutRenewingItsTimestamp(monkeypatch):
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    clock[0] = 1000

    def delayedRead():
        clock[0] = 6350
        return 6300

    monkeypatch.setattr(cell, "read", delayedRead)
    supervisor.checkHeartbeat("job-stop")
    assert not process.terminated
    assert supervisor._heartbeatMonotonic["job-stop"] == 6300
    monkeypatch.setattr(cell, "read", lambda: None)
    clock[0] = 11300
    supervisor.checkHeartbeat("job-stop")
    assert not process.terminated
    clock[0] = 11301
    supervisor.checkHeartbeat("job-stop")
    assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"


def testAlreadyStaleSampleStillFailsAndKeepsObservationDiagnostics(monkeypatch):
    supervisor, repository, store, process, cell, clock = _withCell(monkeypatch)
    supervisor._heartbeatMonotonic["job-stop"] = 1000
    clock[0] = 6001

    def delayedRead():
        clock[0] = 11351
        return 1000

    monkeypatch.setattr(cell, "read", delayedRead)
    supervisor.checkHeartbeat("job-stop")
    assert process.terminated
    assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"
    failed = next(event for event in store.read("job-stop") if event.eventType == "job.failed")
    payload = json.loads(failed.payloadJson)
    assert payload == {"pid": 1234, "heartbeat": {
        "clock": "monotonic", "observationMs": 6001, "currentMs": 11351,
        "sampleMs": 1000, "lastGoodMs": 1000, "timeoutMs": 5000,
        "intervalMs": 500, "seen": False,
    }}
    assert failed.jobId == "job-stop" and failed.projectId == "project" and failed.workflowId == "main"


@pytest.mark.parametrize("lastGood,sample,observed,timedOut", [
    (1000, None, 6000, False),
    (1000, None, 6001, True),
    (1000, -1, 6001, True),
    (1000, 0, 6001, True),
    (1000, 999, 6001, True),
    (1000, 1000, 6001, True),
    (1000, 6002, 6001, True),
    (1000, 1_000_000_000, 6001, True),
    (1000, 1500, 6500, False),
    (1000, 1500, 6501, True),
    (1000, 6500, 6501, False),
    (1000, 6001, 6001, False),
    (0, None, 5000, False),
    (0, 0, 5001, True),
])
def testOriginalDeadlineAndSampleValidationRemainIntact(monkeypatch, lastGood, sample, observed, timedOut):
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    supervisor._heartbeatMonotonic["job-stop"] = lastGood
    monkeypatch.setattr(cell, "read", lambda: sample)
    clock[0] = observed
    supervisor.checkHeartbeat("job-stop")
    assert process.terminated is timedOut
    assert repository.get("job-stop").status == ("FAILED" if timedOut else "RUNNING")
    if not timedOut:
        expected = sample if sample is not None and lastGood < sample <= observed else lastGood
        assert supervisor._heartbeatMonotonic["job-stop"] == expected


@pytest.mark.parametrize("sample", [None, 900, 20000])
def testUnavailableStaleAndFutureReadsCannotRenewLastGood(monkeypatch, sample):
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    supervisor._heartbeatMonotonic["job-stop"] = 1000
    monkeypatch.setattr(cell, "read", lambda: sample)
    for observed in (5500, 5900, 6000):
        clock[0] = observed
        supervisor.consumeWorkerEvent("job-stop", {"eventType": "process.heartbeat"})
        supervisor.checkHeartbeat("job-stop")
        assert not process.terminated
        assert supervisor._heartbeatMonotonic["job-stop"] == 1000
    clock[0] = 6001
    supervisor.checkHeartbeat("job-stop")
    assert process.terminated
    assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"


def _publishHeartbeat(cell, ready, stop):
    while not stop.is_set():
        cell.publish()
        ready.set()
        stop.wait(.5)


def testSpawnedHeartbeatSurvivesParentPauseAfterRead(monkeypatch):
    supervisor, repository, _, _ = _supervisor()
    context = multiprocessing.get_context("spawn")
    cell = HeartbeatCell(context)
    ready, stop = context.Event(), context.Event()
    process = context.Process(target=_publishHeartbeat, args=(cell, ready, stop))
    process.start()
    try:
        assert ready.wait(5), "heartbeat producer did not start"
        supervisor._handles["job-stop"] = (process, stop, object())
        supervisor._heartbeatCells["job-stop"] = cell
        supervisor._heartbeatMonotonic["job-stop"] = monotonicMs()
        supervisor._heartbeatTimeouts["job-stop"] = 5000
        supervisor._heartbeatIntervals["job-stop"] = 500
        read, evidence = cell.read, {}

        def delayedRead():
            sample = read()
            before = monotonicMs()
            Event().wait(5.35)
            evidence.update(sampleMs=sample, actualSampleMs=read(), currentMs=monotonicMs(),
                            observationMs=before)
            return sample

        monkeypatch.setattr(cell, "read", delayedRead)
        supervisor.checkHeartbeat("job-stop")
        evidence["status"] = repository.get("job-stop").status
        evidence["actualAgeMs"] = evidence["currentMs"] - evidence["actualSampleMs"]
        print("spawned_heartbeat_observation=" + json.dumps(evidence, sort_keys=True))
        assert evidence["currentMs"] - evidence["observationMs"] >= 5300
        assert evidence["actualAgeMs"] < 1500, "producer did not remain healthy during the pause"
        assert repository.get("job-stop").status == "RUNNING"
        assert process.is_alive()
    finally:
        stop.set()
        try:
            process.join(2)
            if process.is_alive():
                process.terminate()
                process.join(2)
            process.close()
        except ValueError:
            pass  # The failed baseline has already reaped and closed the process.
        supervisor._handles.pop("job-stop", None)
