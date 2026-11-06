"""Deterministic startup failure with real collector, raw slab and owner methods.

The process/Pipe clock is controlled; this is a lifecycle counterexample, not
hardware or natural-load acceptance. No project fixture or timeout is changed.
"""
from collections import deque
import json
from multiprocessing.shared_memory import SharedMemory
import queue
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.presentation import exporter as module
from emo_master.apps.runtime.presentation.collector import ResultCollector


class Clock:
    def __init__(self):
        self.now = 10.0

    def monotonic(self):
        return self.now

    def monotonic_ns(self):
        return int(self.now * 1e9)


class Credit:
    def __init__(self):
        self.value = threading.BoundedSemaphore(1)
        self.releases = 0

    def acquire(self, blocking=True):
        return self.value.acquire(blocking)

    def release(self):
        self.releases += 1
        self.value.release()


class Context:
    """Preserve _spawn/_reap while controlling only native readiness/death."""
    def __init__(self, clock, replacement):
        self.clock, self.modes = clock, deque(('completion_timeout', replacement, 'healthy'))
        self.processes, self.polls, self.dispatched = [], [], []

    def Pipe(self):
        self.parent = Connection(self, self.modes.popleft())
        return self.parent, SimpleNamespace(close=lambda: None)

    def Process(self, target, args, name):
        self.parent.memoryName = args[1]
        process = Process(self.parent.mode, 100 + len(self.processes))
        self.processes.append(process)
        return process


class Process:
    def __init__(self, mode, pid):
        self.mode, self.number, self.pid = mode, pid, None
        self.alive, self.closed, self.exitcode = False, False, None

    def start(self):
        self.pid, self.alive = self.number, True

    def is_alive(self):
        return self.alive

    def terminate(self):
        if self.mode != 'unproved_reap':
            self.alive, self.exitcode = False, -15

    kill = terminate

    def join(self, timeout):
        assert timeout >= 0

    def close(self):
        assert not self.alive
        self.closed = True


class Connection:
    def __init__(self, context, mode):
        self.context, self.mode = context, mode
        self.messages = deque() if mode in {'ready_timeout', 'ready_eof', 'unproved_reap'} else deque(('ready',))
        self.closed = False

    def poll(self, timeout):
        self.context.polls.append((self.mode, timeout))
        if self.mode == 'ready_eof':
            self.context.clock.now += .6
            return True
        if self.messages:
            return True
        self.context.clock.now += timeout + .001
        return False

    def recv(self):
        if self.mode == 'ready_eof':
            raise EOFError('replacement exited before ready')
        return self.messages.popleft()

    def send(self, task):
        self.context.dispatched.append(task['resultOrdinal'])
        parent = self

        class Child:
            consumed = False

            def recv(self):
                if self.consumed:
                    return None
                self.consumed = True
                return task

            def send(self, reply):
                if reply != 'ready' and (parent.mode != 'completion_timeout' or reply['kind'] == 'snapshot'):
                    parent.messages.append(reply)

            def close(self):
                pass

        # Exercise the real shared-memory copy and PNG output. The first
        # completion ack alone is held back to enter the production timeout.
        module.encodeWorker(Child(), self.memoryName)

    def close(self):
        self.closed = True


def makePool(tmp_path, monkeypatch, replacement):
    clock = Clock()
    monkeypatch.setattr(module, 'time', clock)
    pool = object.__new__(module.ExportPool)
    pool.root, pool.stop, pool.worker = tmp_path, threading.Event(), module.encodeWorker
    pool.context = Context(clock, replacement)
    pool.errors, pool.diagnostics = [], deque(maxlen=64)
    pool.stats = dict(completed=0, timed_out=0, reaped=0, failed=0)
    memory, credit = SharedMemory(create=True, size=12), Credit()
    slot = dict(index=0, memory=memory, free=credit, laneFree=[credit], laneCount=1,
                queue=queue.Queue(maxsize=2), lock=threading.RLock(), pending=set(),
                pendingDescriptors={}, currentDiagnostic=None, process=None, connection=None,
                quarantined=SimpleNamespace(value=False), active=False, busy=False)
    pool.slots = [slot]
    pool._spawn(slot)
    collector = ResultCollector({'plan': '{}', 'slots': pool.descriptors()},
                                lambda event: pool.submit(event['descriptor']))
    callbacks, retired = [], []
    pool.retirementCallback = lambda record: retired.append(record)

    def capture(ordinal, value):
        return collector.image('image', np.full((2, 2, 3), value, np.uint8),
                               dict(identity=dict(resultKey=f'result-{ordinal}', jobId='job',
                                   resultScopeId='scope', resultOrdinal=ordinal, invocationId=f'call-{ordinal}'),
                                   rawBytes=0))

    def callback(task, path, outcome):
        callbacks.append((task['resultOrdinal'], outcome, dict(task['exportDiagnostic'])))
        if task['resultOrdinal'] == 33:
            assert task['exportDiagnostic']['laneReleaseProof'] == 'verified_private_snapshot'
            assert slot['process'] is None, 'old encoder must be proved reaped before the successor'
            assert capture(34, 173)['pendingImage']
        else:
            if path is not None:
                pixels = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
                assert np.all(pixels == 241), 'later normal task must encode its own fresh raw pixels'
            pool.stop.set()

    pool.callback = callback
    return pool, slot, credit, capture, callbacks, retired


@pytest.mark.parametrize('replacement', ['ready_timeout', 'ready_eof'])
def testReapedReplacementFailureRetiresSuccessorAndNextCaptureRetriesLocally(tmp_path, monkeypatch, replacement):
    pool, slot, credit, capture, callbacks, retired = makePool(tmp_path, monkeypatch, replacement)
    try:
        assert capture(33, 19)['pendingImage']
        pool._run(slot)
        assert not slot['quarantined'].value, 'proved-dead replacement must not quarantine a raw slab'
        assert slot['process'] is None and slot['connection'] is None
        assert not slot['pending'] and not slot['pendingDescriptors']
        assert not slot['active'] and not slot['busy']
        assert slot['queue'].empty() and slot['currentDiagnostic'] is None
        assert credit.releases == 2, 'each of the old snapshot and queued successor retires exactly once'
        assert credit.acquire(False) and not credit.acquire(False)
        credit.value.release()  # Return only the test's inspection reservation.
        assert callbacks[:2][0][:2] == (33, 'EXPORT_TIMEOUT')
        assert callbacks[1][0] == 34
        assert callbacks[1][2]['laneReleased']
        assert len(pool.context.processes) == 2, 'expired successor must not start another eager retry'
        assert all(process.closed and not process.alive for process in pool.context.processes)
        assert retired and retired[0]['retirementErrorType'] in {'RuntimeError', 'EOFError'}
        assert retired[0]['startupReapedNs'] <= retired[0]['retirementEndedNs']
        assert retired[0]['ownerPhase'] == 'retired' and retired[0]['laneReleased']
        if replacement == 'ready_timeout':
            assert callbacks[1][1] == 'EXPORT_TIMEOUT'
            assert callbacks[1][2]['laneReleaseProof'] == 'not_dispatched'
            assert pool.context.dispatched == [33]

        # A normal fresh request supplies one local retry; no pool reset,
        # blanket pending clear, semaphore reset or background retry is used.
        pool.stop.clear()
        assert capture(40, 241)['pendingImage']
        pool._run(slot)
        assert callbacks[-1][:2] == (40, 'AVAILABLE')
        assert pool.context.dispatched[-1] == 40
        assert len(pool.context.processes) == 3
        assert credit.releases == 3 and not slot['pending'] and not slot['busy']
        assert not slot['quarantined'].value
        assert len(retired) == 1, 'ordinary successful retirements produce no diagnostic event'
    finally:
        pool._reap(slot)
        slot['memory'].close()
        slot['memory'].unlink()


def testUnprovedReplacementDeathKeepsQueuedRawReservationQuarantined(tmp_path, monkeypatch):
    pool, slot, credit, capture, callbacks, retired = makePool(tmp_path, monkeypatch, 'unproved_reap')
    try:
        assert capture(33, 19)['pendingImage']
        pool._run(slot)
        assert slot['quarantined'].value and slot['process'].alive
        assert slot['pending'] == {0} and not credit.acquire(False)
        assert credit.releases == 1, 'unproved reader death must not release its successor reservation'
        assert [row[:2] for row in callbacks] == [(33, 'EXPORT_TIMEOUT'), (34, 'EXPORT_FAILED')]
        assert 'quarantined' in capture(40, 241)['reason']
        assert pool.context.dispatched == [33]
        assert len(pool.context.processes) == 2
    finally:
        slot['process'].mode = 'cleanup'
        pool._reap(slot)
        slot['memory'].close()
        slot['memory'].unlink()


def testFailedLocalRetryRetiresOnlyItsOwnCreditAndDoesNotLoop(tmp_path, monkeypatch):
    pool, slot, credit, capture, callbacks, retired = makePool(tmp_path, monkeypatch, 'ready_timeout')
    try:
        assert capture(33, 19)['pendingImage']
        pool._run(slot)
        assert not slot['quarantined'].value
        pool.context.modes.appendleft('ready_timeout')
        pool.stop.clear()
        assert capture(40, 241)['pendingImage']
        pool._run(slot)
        assert callbacks[-1][:2] == (40, 'EXPORT_FAILED')
        assert callbacks[-1][2]['stage'] == 'startup_wait'
        assert callbacks[-1][2]['laneReleaseProof'] == 'encoder_reaped'
        assert credit.releases == 3 and not slot['pending'] and not slot['busy']
        assert not slot['quarantined'].value and slot['process'] is None
        assert len(pool.context.processes) == 3, 'one startup attempt per fresh descriptor'
        assert pool.context.dispatched == [33]
        assert len(retired) == 2 and retired[-1]['startupErrorType'] == 'RuntimeError'

        pool.stop.clear()
        assert capture(41, 241)['pendingImage']
        pool._run(slot)
        assert callbacks[-1][:2] == (41, 'AVAILABLE')
        assert credit.releases == 4 and not slot['pending'] and not slot['busy']
        assert len(pool.context.processes) == 4
    finally:
        pool._reap(slot)
        slot['memory'].close()
        slot['memory'].unlink()


@pytest.mark.parametrize('replacement,level,phase', [
    ('ready_timeout', 'WARN', 'retired'), ('unproved_reap', 'ERROR', 'quarantined')])
def testRetirementFailurePersistsFinalProofInExistingEventStoreAndJsonl(
        tmp_path, monkeypatch, replacement, level, phase):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.apps.runtime.events.event_store import EventStore
    from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
    from emo_master.apps.runtime.presentation.service import PresentationService

    persistence = SqliteStore(tmp_path / 'events.db')
    persistence.initialize()
    events = EventStore(persistence)
    writer = RuntimeJsonlLogWriter(tmp_path / 'logs')
    events.addSink(writer.enqueue)
    service = object.__new__(PresentationService)
    service.runtime = SimpleNamespace(eventStore=events,
        jobRepository=SimpleNamespace(get=lambda jobId: SimpleNamespace(projectId='project')))
    pool, slot, credit, capture, callbacks, retired = makePool(tmp_path, monkeypatch, replacement)
    pool.retirementCallback = service._exportRetired
    try:
        assert capture(33, 19)['pendingImage']
        pool._run(slot)
        event, = events.read('job')
        assert (event.eventType, event.component, event.level, event.code) == (
            'presentation.export.retirement_failed', 'presentation', level, 'EXPORT_RETIREMENT_FAILED')
        assert event.projectId == 'project' and event.workflowRunId == 'call-33'
        diagnostic = json.loads(event.payloadJson)
        assert diagnostic['resultOrdinal'] == 33 and diagnostic['ownerPhase'] == phase
        assert diagnostic['laneReleaseProof'] == 'verified_private_snapshot'
        assert diagnostic['startupErrorType'] == 'RuntimeError'
        assert len(diagnostic['startupDetail']) <= 240 and len(diagnostic['retirementDetail']) <= 240
        assert diagnostic['retirementEndedNs'] >= diagnostic['callbackEndedNs']
        assert 'retirementErrorType' not in callbacks[0][2], 'published result metadata stays immutable'
        assert [record.payloadJson for record in persistence.listJobEventsAfter('job')] == [event.payloadJson]
        assert writer.close() is None
        lines = [json.loads(line) for path in (tmp_path / 'logs').glob('runtime-*.jsonl')
                 for line in path.read_text(encoding='utf-8').splitlines()]
        assert len(lines) == 1 and lines[0]['payload'] == diagnostic
    finally:
        writer.close()
        if slot['process'] is not None:
            slot['process'].mode = 'cleanup'
        pool._reap(slot)
        slot['memory'].close()
        slot['memory'].unlink()


def testRetirementDiagnosticSinkFailureDoesNotHoldRawOrChangeResult(tmp_path, monkeypatch):
    pool, slot, credit, capture, callbacks, retired = makePool(tmp_path, monkeypatch, 'ready_timeout')

    def failedSink(diagnostic):
        raise OSError('log unavailable')

    pool.retirementCallback = failedSink
    pool.errors = ['prior'] * 32
    try:
        assert capture(33, 19)['pendingImage']
        pool._run(slot)
        assert [row[:2] for row in callbacks] == [(33, 'EXPORT_TIMEOUT'), (34, 'EXPORT_TIMEOUT')]
        assert credit.releases == 2 and not slot['pending'] and not slot['busy']
        assert not slot['quarantined'].value and slot['process'] is None
        assert len(pool.errors) <= 32 and pool.errors[-1] == 'retirement diagnostic: OSError'
    finally:
        pool._reap(slot)
        slot['memory'].close()
        slot['memory'].unlink()
