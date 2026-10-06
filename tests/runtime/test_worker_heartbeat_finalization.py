from __future__ import annotations

import multiprocessing
import queue
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
import emo_master.apps.runtime.jobs.heartbeat as heartbeatModule
import emo_master.apps.runtime.jobs.worker_main as workerModule
from tests.runtime.test_heartbeat_liveness import _withCell


def _stubComputation(monkeypatch, run):
    class Document:
        @staticmethod
        def model_validate(snapshot):
            return SimpleNamespace(project=SimpleNamespace(revision=1))

    class Registry:
        def __init__(self, **kwargs):
            pass

        def scanRoots(self, roots):
            return SimpleNamespace(activeOperators={})

    class Compiler:
        def __init__(self, **kwargs):
            pass

        def compile(self, *args, **kwargs):
            return object()

    class Runner:
        def __init__(self, **kwargs):
            pass

        def run(self, *args):
            return run()

    for name, value in (("ProjectDocument", Document), ("PluginRegistry", Registry),
                        ("WorkflowCompiler", Compiler), ("WorkflowRunner", Runner),
                        ("ArtifactStore", lambda path: object())):
        monkeypatch.setattr(workerModule, name, value)


class _DrainQueue:
    def __init__(self):
        self.events = []
        self.closed = False
        self.draining = threading.Event()
        self.release = threading.Event()

    def put(self, event):
        assert not self.closed, "heartbeat enqueue raced queue close"
        self.events.append(event)

    put_nowait = put

    def close(self):
        self.closed = True

    def join_thread(self):
        self.draining.set()
        assert self.release.wait(3), "test did not release the fake feeder"


def _spec(tmp_path, cell):
    path = tmp_path / "project.json"
    path.write_text("{}", encoding="utf-8")
    return JobProcessSpec("job-stop", str(path), "main", heartbeatIntervalMs=50,
                          legacySnapshotPolicy="NONE", heartbeatCell=cell)


@pytest.mark.parametrize("outcome", ["completed", "failed", "aborted"])
def testCellPulsesThroughFeederDrainWithoutPostTerminalEvents(monkeypatch, tmp_path, outcome):
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)

    def run():
        if outcome == "failed":
            raise RuntimeError("computation failed")
        if outcome == "aborted":
            raise CancellationRequested("cancelled")

    _stubComputation(monkeypatch, run)
    events = _DrainQueue()
    spec = _spec(tmp_path, cell)
    errors = []

    def execute():
        try:
            workerModule.runJobProcess(spec, threading.Event(), events)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert events.draining.wait(1)
        assert worker.is_alive()
        assert events.events[-1]["eventType"] == f"job.{outcome}"
        count = len(events.events)
        clock[0] = 6000
        deadline = time.monotonic() + 1
        while cell.read() != 6000 and time.monotonic() < deadline:
            threading.Event().wait(.005)
        assert cell.read() == 6000
        supervisor.checkHeartbeat("job-stop")
        assert not process.terminated
        assert repository.get("job-stop").status == "RUNNING"
        assert len(events.events) == count
    finally:
        events.release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert bool(errors) is (outcome == "failed")
    assert not any(thread.name == "runtime-heartbeat-job-stop" for thread in threading.enumerate())


@pytest.mark.parametrize("outcome", ["completed", "failed", "aborted"])
def testCellPulsesWhileTerminalEnqueueWaitsForCapacity(monkeypatch, tmp_path, outcome):
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    waiting, release = threading.Event(), threading.Event()

    def run():
        if outcome == "failed":
            raise RuntimeError("computation failed")
        if outcome == "aborted":
            raise CancellationRequested("cancelled")

    _stubComputation(monkeypatch, run)

    class TerminalQueue(_DrainQueue):
        def put(self, event):
            if event["eventType"] == f"job.{outcome}":
                waiting.set()
                assert release.wait(3), "test did not release terminal enqueue"
            super().put(event)

    events, errors = TerminalQueue(), []

    def execute():
        try:
            workerModule.runJobProcess(_spec(tmp_path, cell), threading.Event(), events)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert waiting.wait(1)
        count = len(events.events)
        for sample in (6000, 12000):
            clock[0] = sample
            deadline = time.monotonic() + 1
            while cell.read() != sample and time.monotonic() < deadline:
                threading.Event().wait(.005)
            assert cell.read() == sample
            supervisor.checkHeartbeat("job-stop")
            assert not process.terminated and repository.get("job-stop").status == "RUNNING"
        assert len(events.events) == count
    finally:
        release.set()
        events.release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert bool(errors) is (outcome == "failed")
    assert events.events[-1]["eventType"] == f"job.{outcome}"
    assert not any(thread.name == "runtime-heartbeat-job-stop" for thread in threading.enumerate())


def testPublisherDeathDuringDrainStillFailsAtOriginalDeadline(monkeypatch, tmp_path):
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    _stubComputation(monkeypatch, lambda: None)

    def publisherExits(jobId, projectId, workflowId, eventQueue, stop, interval, heartbeat, *args):
        heartbeat.publish()
        eventQueue.put({"eventType": "process.heartbeat"})

    monkeypatch.setattr(workerModule, "heartbeatLoop", publisherExits)
    events = _DrainQueue()
    worker = threading.Thread(target=workerModule.runJobProcess,
                              args=(_spec(tmp_path, cell), threading.Event(), events))
    worker.start()
    try:
        assert events.draining.wait(1)
        clock[0] = 5000
        supervisor.checkHeartbeat("job-stop")
        assert not process.terminated
        clock[0] = 5001
        supervisor.checkHeartbeat("job-stop")
        assert process.terminated
        assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"
    finally:
        events.release.set()
        worker.join(2)
    assert not worker.is_alive()


@pytest.mark.parametrize("failure", ["close", "join_thread"])
def testDrainFailurePropagatesAndStopsHeartbeat(monkeypatch, tmp_path, failure):
    _, _, _, _, cell, _ = _withCell(monkeypatch)
    _stubComputation(monkeypatch, lambda: None)
    events = _DrainQueue()

    def fail():
        raise RuntimeError("feeder failure")

    monkeypatch.setattr(events, failure, fail)
    with pytest.raises(RuntimeError, match="feeder failure"):
        workerModule.runJobProcess(_spec(tmp_path, cell), threading.Event(), events)
    assert events.events[-1]["eventType"] == "job.completed"
    assert not any(thread.name == "runtime-heartbeat-job-stop" for thread in threading.enumerate())


def testLegacyDirectQueueCallerDoesNotAcquireFeederJoin(monkeypatch, tmp_path):
    _stubComputation(monkeypatch, lambda: None)
    events = _DrainQueue()
    workerModule.runJobProcess(_spec(tmp_path, None), threading.Event(), events)
    assert not events.closed and not events.draining.is_set()
    assert events.events[-1]["eventType"] == "job.completed"


def _spawnedDrainingWorker(spec, cancel, queue, drainStarted, returned, clock):
    # Both processes use the same simulated monotonic clock, so advancing the
    # parent does not create artificial silence in a healthy child publisher.
    heartbeatModule.monotonicMs = lambda: clock.value
    patch = pytest.MonkeyPatch()

    def produce():
        for index in range(5):
            queue.put({"eventType": "operator.log", "message": "x" * 65536,
                       "payload": {"index": index}})

    _stubComputation(patch, produce)

    class QueueOwner:
        def put(self, event):
            queue.put(event)

        def put_nowait(self, event):
            queue.put_nowait(event)

        def close(self):
            queue.close()

        def join_thread(self):
            drainStarted.set()
            queue.join_thread()

    workerModule.runJobProcess(spec, cancel, QueueOwner())
    returned.set()


def testSpawnedWorkerKeepsLivenessUntilTerminalBacklogDrains(monkeypatch, tmp_path):
    supervisor, repository, store, _, cell, _ = _withCell(monkeypatch)
    context = multiprocessing.get_context("spawn")
    clock = context.Value("q", 0)
    monkeypatch.setattr(heartbeatModule, "monotonicMs", lambda: clock.value)
    monkeypatch.setattr("emo_master.apps.runtime.jobs.supervisor.monotonicMs", lambda: clock.value)
    events, cancel = context.Queue(), context.Event()
    drainStarted, returned = context.Event(), context.Event()
    spec = replace(_spec(tmp_path, cell), jobId="job-stop")
    process = context.Process(target=_spawnedDrainingWorker,
                              args=(spec, cancel, events, drainStarted, returned, clock))
    process.start()
    supervisor._handles["job-stop"] = (process, cancel, events)
    consumed = []
    try:
        assert drainStarted.wait(5)
        assert process.is_alive() and not returned.is_set()
        assert supervisor.ownsJobResources("job-stop")
        # More than the unchanged timeout passes with no queue consumption;
        # the worker is still publishing from inside its existing feeder wait.
        clock.value = 6000
        deadline = time.monotonic() + 1
        while cell.read() != 6000 and time.monotonic() < deadline:
            threading.Event().wait(.005)
        assert cell.read() == 6000
        supervisor.checkHeartbeat("job-stop")
        assert process.is_alive() and repository.get("job-stop").status == "RUNNING"
        while not repository.get("job-stop").isTerminal:
            event = events.get(timeout=2)
            consumed.append(event["eventType"])
            supervisor.consumeWorkerEvent("job-stop", event)
        process.join(5)
        assert process.exitcode == 0 and returned.is_set()
        with pytest.raises(queue.Empty):
            events.get(timeout=.05)
        supervisor.processExited("job-stop", 0)
        assert repository.get("job-stop").status == "COMPLETED"
        assert consumed[-1] == "job.completed"
        assert [event.eventType for event in store.read("job-stop")] == consumed
        assert not supervisor.ownsJobResources("job-stop")
        assert "job-stop" not in supervisor._heartbeatCells
    finally:
        try:
            if process.is_alive():
                process.terminate()
                process.join(2)
            process.close()
        except ValueError:
            pass
        events.close()
        events.join_thread()
