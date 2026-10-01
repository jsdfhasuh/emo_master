"""Two-phase terminal ownership at the real Supervisor/Runtime RPC boundary."""
from __future__ import annotations

from collections import Counter
import queue
import threading
import time

import grpc
import pytest

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.manager import JobManager
from emo_master.apps.runtime.jobs.models import JobProcessSpec, JobRecord
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
from emo_master.apps.runtime.presentation.service import PresentationService


class Process:
    pid = -1
    exitcode = 0

    def __init__(self, alive=True):
        self.alive = alive
        self.closes = 0
        self.terminations = 0

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def terminate(self):
        self.terminations += 1
        self.alive = False

    def close(self):
        self.closes += 1


class Channel(queue.Queue):
    def __init__(self):
        super().__init__()
        self.closes = 0

    def close(self):
        self.closes += 1


class Persistence:
    def __init__(self):
        self.updates = []
        self.fail = False

    def updateJobStatus(self, **changes):
        if changes['status'] in {'COMPLETED', 'FAILED', 'ABORTED'}:
            self.updates.append(dict(changes))
            if self.fail:
                raise RuntimeError('publication-error')


def setup_jobs(*ids):
    persistence = Persistence()
    repository = JobRepository(persistence)
    events = EventStore()
    supervisor = JobSupervisor(repository, events, gracefulStopTimeoutMs=0)
    runtime = RuntimeService.__new__(RuntimeService)
    runtime.jobRepository, runtime.eventStore = repository, events
    runtime.jobSupervisor = supervisor
    runtime.jobManager = JobManager(repository, events, supervisor)
    runtime.jobMessages = {}
    for job in ids:
        repository.create(JobRecord(job, 'project', 1, 'main', status='RUNNING'))
        supervisor._handles[job] = (Process(), threading.Event(), Channel())
    return runtime, supervisor, persistence


def threaded(action):
    errors = []
    def invoke():
        try:
            action()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=invoke)
    thread.start()
    return thread, errors


def consume(supervisor, job='a'):
    supervisor.consumeWorkerEvent(job, {'eventType': 'job.completed'})


def stop(runtime, job='a', mode='force', context=None):
    return runtime.StopJob(pb.StopJobRequest(job_id=job, mode=mode), context)


def assert_retired(supervisor):
    supervisor.shutdown()
    supervisor.waitForRetirement()
    supervisor.finishRetirements()
    assert not supervisor._ownedJobs()


def test_real_stop_rpc_does_not_acknowledge_memory_terminal_publication_failure():
    runtime, supervisor, persistence = setup_jobs('a')
    callback = []
    supervisor.terminalCallback = lambda *args: callback.append(args)
    persistence.fail = True
    with pytest.raises(RuntimeError, match='publication-error'):
        consume(supervisor)
    assert runtime.GetJobStatus(pb.GetJobStatusRequest(job_id='a'), None).status == 'COMPLETED'
    for _ in range(2):
        reply = stop(runtime)
        assert not reply.ok and reply.status == 'COMPLETED'
        assert reply.message == 'E_JOB_FINALIZATION_FAILED: publication'
    assert len(persistence.updates) == len(callback) == 1
    assert supervisor.ownsJobResources('a')
    assert supervisor._recoveries['a'].notification == 'NOT_ATTEMPTED'
    frozen = dict(persistence.updates[0])
    assert supervisor.recoverFinalizations() == ['a: publication']
    assert supervisor.ownsJobResources('a')
    persistence.fail = False
    assert supervisor.recoverFinalizations() == []
    assert persistence.updates == [frozen] * 3
    assert stop(runtime).ok
    assert len(callback) == 1
    assert [e.eventType for e in supervisor.eventStore.read('a')] == ['job.completed']
    assert_retired(supervisor)


@pytest.mark.parametrize('caller', ['event', 'exit', 'heartbeat', 'graceful', 'force-rpc', 'shutdown'])
@pytest.mark.parametrize('phase', ['callback', 'publication', 'notification', 'callback+publication'])
def test_attempt_failure_priority_and_once_only_repair(monkeypatch, caller, phase):
    runtime, supervisor, persistence = setup_jobs('a')
    calls = Counter()
    def callback(*args):
        calls['callback'] += 1
        if 'callback' in phase:
            raise RuntimeError('callback-error')
    supervisor.terminalCallback = callback
    persistence.fail = 'publication' in phase
    original_mark = supervisor.eventStore.markTerminal
    def mark(job):
        calls['notification'] += 1
        original_mark(job)  # Failure may occur after the terminal set changed.
        if phase == 'notification':
            raise RuntimeError('notification-error')
    monkeypatch.setattr(supervisor.eventStore, 'markTerminal', mark)
    process = supervisor.getProcess('a')
    if caller == 'exit':
        process.alive = False
    supervisor._heartbeat['a'] = 0
    actions = {
        'event': lambda: consume(supervisor),
        'exit': lambda: supervisor.processExited('a', 1),
        'heartbeat': lambda: supervisor.checkHeartbeat('a'),
        'graceful': lambda: supervisor._enforceGracefulStop('a', process),
        'force-rpc': lambda: stop(runtime),
        'shutdown': supervisor.shutdown,
    }
    primary = 'publication' if 'publication' in phase else phase
    if caller == 'force-rpc':
        reply = actions[caller]()
        assert not reply.ok and reply.message == 'E_JOB_FINALIZATION_FAILED: ' + primary
    else:
        with pytest.raises(RuntimeError, match=primary + '-error'):
            actions[caller]()
    assert calls['callback'] == 1 and not supervisor._finalizations and not supervisor._terminalFIFO
    if phase == 'callback':
        assert not supervisor._recoveries
        assert stop(runtime).ok
    else:
        receipt = supervisor._recoveries['a']
        assert receipt.callback is receipt.owner is None
        assert all(isinstance(v, str) for v in receipt.faults.values())
        if '+' in phase:
            assert set(receipt.faults) == {'callback', 'publication'}
        before = len(persistence.updates)
        expected_notifications = calls['notification']
        persistence.fail = False
        monkeypatch.setattr(supervisor.eventStore, 'markTerminal', original_mark)
        assert supervisor.recoverFinalizations() == []
        assert len(persistence.updates) == before + ('publication' in phase)
        assert calls['notification'] == expected_notifications
        assert calls['callback'] == 1
    assert_retired(supervisor)


def test_network_stop_cancellation_only_releases_waiter():
    runtime, supervisor, _ = setup_jobs('a')
    entered, release = threading.Event(), threading.Event()
    def callback(*args):
        entered.set()
        assert release.wait(3)
    supervisor.terminalCallback = callback
    owner, errors = threaded(lambda: consume(supervisor))
    server = AioRuntimeServer(runtime, None)
    channel = grpc.insecure_channel(f'127.0.0.1:{server.port}')
    call = None
    try:
        assert entered.wait(1)
        ticket = supervisor._finalizations['a']
        original_events = tuple(supervisor.eventStore.read('a'))
        cancel = supervisor._handles['a'][1]
        stub = rpc.RuntimeServiceStub(channel)
        call = stub.StopJob.future(pb.StopJobRequest(job_id='a', mode='force'), timeout=2)
        deadline = time.monotonic() + 1
        while not ticket.waiters and time.monotonic() < deadline:
            threading.Event().wait(.001)
        assert ticket.waiters and not call.done()
        assert call.cancel()
        deadline = time.monotonic() + 1
        while ticket.waiters and time.monotonic() < deadline:
            threading.Event().wait(.001)
        assert not ticket.waiters
        assert not ticket.done.is_set() and supervisor.ownsJobResources('a')
        assert not cancel.is_set() and tuple(supervisor.eventStore.read('a')) == original_events
        assert runtime.jobRepository.get('a').status == 'RUNNING'
        release.set()
        owner.join(2)
        assert not owner.is_alive() and not errors
        assert stub.StopJob(pb.StopJobRequest(job_id='a', mode='force'), timeout=1).ok
    finally:
        release.set()
        owner.join(2)
        if call is not None:
            call.cancel()
        channel.close()
        server.close()
        assert_retired(supervisor)


@pytest.mark.parametrize('target', ['a', 'b'])
@pytest.mark.parametrize('mutation', ['force', 'graceful', 'consume', 'heartbeat', 'exit', 'shutdown', 'start'])
def test_same_and_cross_job_reentry_rejected_before_side_effects(target, mutation):
    runtime, supervisor, persistence = setup_jobs('a', 'b')
    observations = []
    def callback(*args):
        before = (len(supervisor.eventStore.read(target)), len(persistence.updates),
                  supervisor._handles[target][1].is_set(), supervisor.getProcess(target).terminations)
        if mutation in {'force', 'graceful'}:
            reply = stop(runtime, target, mutation)
            assert not reply.ok and reply.message == 'E_FINALIZATION_REENTRANT'
        else:
            actions = {
                'consume': lambda: consume(supervisor, target),
                'heartbeat': lambda: supervisor.checkHeartbeat(target),
                'exit': lambda: supervisor.processExited(target, 0),
                'shutdown': supervisor.shutdown,
                'start': lambda: supervisor.startJob(JobProcessSpec('c', 'unused', 'main')),
            }
            with pytest.raises(RuntimeError, match='E_FINALIZATION_REENTRANT'):
                actions[mutation]()
        after = (len(supervisor.eventStore.read(target)), len(persistence.updates),
                 supervisor._handles[target][1].is_set(), supervisor.getProcess(target).terminations)
        assert before == after
        assert list(supervisor._finalizations) == ['a']
        observations.append(True)
    supervisor.terminalCallback = callback
    consume(supervisor)
    assert observations == [True]
    supervisor.terminalCallback = None
    assert_retired(supervisor)


def test_fifo_advances_after_failure_and_abandoned_queued_owner(monkeypatch):
    runtime, supervisor, persistence = setup_jobs('a', 'b', 'c')
    entered, release = threading.Event(), threading.Event()
    callbacks = []
    def callback(job, status):
        callbacks.append(job)
        if job == 'a':
            entered.set()
            assert release.wait(3)
            raise RuntimeError('callback-error')
    supervisor.terminalCallback = callback
    original_run = supervisor._runTerminal
    def run(ticket):
        if ticket.jobId == 'b':
            def interrupted(*args):
                raise RuntimeError('owner-interrupted')
            ticket.turn.wait = interrupted
        original_run(ticket)
    monkeypatch.setattr(supervisor, '_runTerminal', run)
    a, a_errors = threaded(lambda: consume(supervisor, 'a'))
    assert entered.wait(1)
    b, b_errors = threaded(lambda: consume(supervisor, 'b'))
    b.join(1)
    assert not b.is_alive() and str(b_errors[0]) == 'owner-interrupted'
    assert supervisor._recoveries['b'].failurePhase == 'CALLBACK_NOT_RUN/OWNER_ABANDONED'
    supervisor.consumeWorkerEvent('c', {'eventType': 'process.output'})
    assert len(supervisor.eventStore.read('c')) == 1
    c, c_errors = threaded(lambda: consume(supervisor, 'c'))
    try:
        assert callbacks == ['a']
        assert runtime.jobRepository.get('a').status == 'RUNNING'
    finally:
        release.set()
        a.join(2)
        c.join(2)
    assert str(a_errors[0]) == 'callback-error' and not c_errors
    assert callbacks == ['a', 'c']
    assert supervisor.recoverFinalizations() == ['b: CALLBACK_NOT_RUN/OWNER_ABANDONED']
    assert len(persistence.updates) == 2
    assert not supervisor._terminalFIFO
    # Deliberately unrecoverable receipt remains owned; do not pretend to close it.
    for job in ['a', 'b', 'c']:
        supervisor._handles[job][0].alive = False
    supervisor.finishRetirements()
    assert supervisor.ownsJobResources('b')


def test_unknown_retired_callback_and_native_close_failures_are_not_retried():
    runtime, supervisor, _ = setup_jobs('a')
    process, _, channel = supervisor._handles['a']
    calls = []
    def retired(job):
        calls.append(job)
        raise RuntimeError('retired-error')
    supervisor.retiredCallback = retired
    reply = stop(runtime)
    assert not reply.ok
    assert supervisor._retirements['a'].faults
    assert process.closes == channel.closes == 1
    assert supervisor.getProcess('a') is None
    for _ in range(2):
        assert stop(runtime).message == 'E_JOB_RETIREMENT_INCOMPLETE'
        assert supervisor.finishRetirements() == ['a: E_JOB_RETIREMENT_INCOMPLETE']
    assert calls == ['a'] and supervisor.ownsJobResources('a')

    runtime, supervisor, _ = setup_jobs('b')
    process, _, channel = supervisor._handles['b']
    def uncertain():
        process.closes += 1
        raise RuntimeError('native-close-unknown')
    process.close = uncertain
    assert not stop(runtime, 'b').ok
    assert supervisor.finishRetirements() == ['b: E_JOB_RETIREMENT_INCOMPLETE']
    assert process.closes == 1 and channel.closes == 0
    assert supervisor.ownsJobResources('b')


def test_fallback_remains_memory_first_and_repairs_notification_only(monkeypatch):
    runtime, supervisor, persistence = setup_jobs('a')
    callbacks = []
    def callback(job, status):
        callbacks.append(runtime.jobRepository.get(job).status)
        raise RuntimeError('suppressed-fallback-callback')
    supervisor.terminalCallback = callback
    def append(*args, **kwargs):
        raise RuntimeError('event-persistence')
    monkeypatch.setattr(supervisor.eventStore, 'append', append)
    supervisor.bridgeError('a', RuntimeError('original'))
    reply = stop(runtime)
    assert reply.ok and reply.status == 'FAILED' and 'E_EVENT_PERSISTENCE' in reply.message
    assert callbacks == ['FAILED'] and not persistence.updates
    assert not supervisor._ownedJobs()


@pytest.fixture
def runtime(tmp_path):
    value = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs', logDirectory=tmp_path / 'logs')
    yield value
    value.close()


def test_uncaptured_ticket_holds_actual_presentation_callback_epoch(runtime, tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def previous(job, status):
        calls.append('original')
        assert runtime.jobSupervisor.getProcess(job) is None
        entered.set()
        assert release.wait(3)
        raise RuntimeError('previous-failed')
    runtime.jobSupervisor.terminalCallback = previous
    presentation = PresentationService(runtime, tmp_path / 'presentation')
    presentation.previousTerminal = lambda *args: calls.append('mutated-field')
    runtime.jobRepository.create(JobRecord('uncaptured', 'project', 1, 'main', status='RUNNING'))
    thread, errors = threaded(lambda: consume(runtime.jobSupervisor, 'uncaptured'))
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError, match='callback epoch'):
            presentation.close()
        assert presentation.monitor.is_alive() and not presentation.closed
        assert runtime.jobSupervisor.terminalCallback is presentation._installedTerminal
    finally:
        release.set()
        thread.join(2)
    assert str(errors[0]) == 'previous-failed' and calls == ['original']
    # Restoration must use the captured registration, even if a public field changed.
    presentation.close()
    assert runtime.jobSupervisor.terminalCallback is previous


def test_runtime_close_reentry_precedes_project_lock_and_has_no_side_effects(runtime):
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    def callback(*args):
        with pytest.raises(RuntimeError, match='E_FINALIZATION_REENTRANT'):
            runtime.close()
        assert not runtime._closing and not runtime._maintenanceStop.is_set()
    runtime.jobSupervisor.terminalCallback = callback
    consume(runtime.jobSupervisor)


def test_runtime_close_pending_attempt_keeps_stores_and_retries_only_safe_steps(runtime, monkeypatch):
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    entered, release = threading.Event(), threading.Event()
    def callback(*args):
        entered.set()
        assert release.wait(4)
    runtime.jobSupervisor.terminalCallback = callback
    thread, errors = threaded(lambda: consume(runtime.jobSupervisor))
    assert entered.wait(1)
    closes = []
    original = runtime.previewAssetStore.close
    monkeypatch.setattr(runtime.previewAssetStore, 'close', lambda: (closes.append('assets'), original()))
    try:
        with pytest.raises(RuntimeError, match='FINALIZATION_PENDING'):
            runtime.close()
        assert not runtime._closed and runtime._closing and not closes
        assert runtime.sqliteStore._idleConnection is not None
        with pytest.raises(RuntimeError, match='E_RUNTIME_CLOSING'):
            runtime.StartJob(pb.StartJobRequest(), None)
    finally:
        release.set()
        thread.join(2)
    assert not errors
    runtime.close()
    assert runtime._closed and closes == ['assets']
    runtime.close()
    assert closes == ['assets']


def test_fresh_heartbeat_on_actual_bridge_keeps_live_process_and_queue():
    from emo_master.apps.runtime.jobs.event_bridge import EventBridge
    _, supervisor, _ = setup_jobs('a')
    process, cancel, channel = supervisor._handles['a']
    supervisor._heartbeat['a'] = time.time_ns() // 1_000_000
    checked, release = threading.Event(), threading.Event()
    errors = []
    bridge = EventBridge(supervisor, 'a', process, channel, cancel)
    supervisor._bridges['a'] = bridge
    def one_check():
        try:
            supervisor.checkHeartbeat('a')
            assert process.alive
            assert process.terminations == process.closes == channel.closes == 0
            assert supervisor.getProcess('a') is process
        except BaseException as error:
            errors.append(error)
        finally:
            checked.set()
            assert release.wait(2)
    bridge._run = one_check
    bridge.start()
    try:
        assert checked.wait(1)
        assert not errors, errors
    finally:
        release.set()
        bridge.join(2)
    assert not bridge.is_alive()


def test_graceful_watcher_exited_process_leaves_live_bridge_to_drain():
    _, supervisor, _ = setup_jobs('a')
    process, cancel, _ = supervisor._handles['a']
    process.alive = False
    class DrainingBridge:
        stopped = False
        def requestStop(self):
            self.stopped = True
        def is_alive(self):
            return True
    bridge = DrainingBridge()
    supervisor._bridges['a'] = bridge
    supervisor._enforceGracefulStop('a', process)
    assert not bridge.stopped and not cancel.is_set()
    assert supervisor.getProcess('a') is process
    assert not supervisor.eventStore.read('a')


def test_invalid_mode_and_unknown_job_keep_real_rpc_failed_status():
    runtime, supervisor, _ = setup_jobs('a')
    reply = stop(runtime, mode='invalid')
    assert not reply.ok and reply.status == 'FAILED'
    assert not supervisor.eventStore.read('a')
    reply = stop(runtime, job='missing')
    assert not reply.ok and reply.status == 'FAILED'
    assert_retired(supervisor)


def test_shutdown_later_prepare_failure_still_finishes_accepted_earlier_job(monkeypatch):
    _, supervisor, _ = setup_jobs('a', 'b')
    callbacks = []
    supervisor.terminalCallback = lambda job, status: callbacks.append(job)
    original = supervisor.eventStore.append
    failure = RuntimeError('b-append-failed')
    def append(jobId, *args, **kwargs):
        if jobId == 'b':
            raise failure
        return original(jobId, *args, **kwargs)
    monkeypatch.setattr(supervisor.eventStore, 'append', append)
    with pytest.raises(RuntimeError) as caught:
        supervisor.shutdown()
    assert caught.value is failure
    assert callbacks == ['a']
    assert supervisor.jobRepository.get('a').status == 'ABORTED'
    assert 'a' not in supervisor._recoveries and not supervisor._terminalFIFO
    monkeypatch.setattr(supervisor.eventStore, 'append', original)
    assert_retired(supervisor)


def test_fault_formatting_cannot_strand_claim_or_replace_primary(monkeypatch):
    class Unprintable(RuntimeError):
        def __str__(self):
            raise ValueError('broken str')
    _, supervisor, _ = setup_jobs('a', 'b')
    failure = Unprintable()
    def callback(*args):
        raise failure
    supervisor.terminalCallback = callback
    with pytest.raises(Unprintable) as caught:
        consume(supervisor)
    assert caught.value is failure and not supervisor._finalizations and not supervisor._terminalFIFO
    assert supervisor._receipts[-1].faults['callback']
    supervisor.terminalCallback = None
    original = supervisor._claimTerminal
    def interrupt_after_claim(*args, **kwargs):
        original(*args, **kwargs)
        raise failure
    monkeypatch.setattr(supervisor, '_claimTerminal', interrupt_after_claim)
    with pytest.raises(Unprintable) as caught:
        consume(supervisor, 'b')
    assert caught.value is failure
    assert not supervisor._finalizations and not supervisor._terminalFIFO
    assert supervisor._recoveries['b'].done.is_set()


def test_duplicate_terminal_heartbeat_and_exit_cannot_compete_with_pending_claim(monkeypatch):
    runtime, supervisor, _ = setup_jobs('a', 'b')
    entered, release = threading.Event(), threading.Event()
    waiting = threading.Event()
    original_wait = supervisor._waitFirstAttempt
    def wait(ticket, *args, **kwargs):
        if ticket.jobId == 'a':
            waiting.set()
        return original_wait(ticket, *args, **kwargs)
    monkeypatch.setattr(supervisor, '_waitFirstAttempt', wait)
    callbacks = []
    def callback(job, status):
        callbacks.append((job, status))
        if job == 'a':
            entered.set()
            assert release.wait(3)
    supervisor.terminalCallback = callback
    owner, errors = threaded(lambda: consume(supervisor))
    process, cancel, channel = supervisor._handles['a']
    b = graceful = None
    b_errors, graceful_errors = [], []
    try:
        assert entered.wait(1)
        for terminal in ['job.completed', 'job.failed', 'job.aborted']:
            supervisor.consumeWorkerEvent('a', {'eventType': terminal})
        supervisor._heartbeat['a'] = 0
        supervisor.checkHeartbeat('a')
        graceful, graceful_errors = threaded(lambda: supervisor._enforceGracefulStop('a', process))
        assert waiting.wait(1) and graceful.is_alive()
        supervisor.processExited('a', 0)
        supervisor.bridgeStopped('a')
        assert not cancel.is_set() and process.terminations == process.closes == channel.closes == 0
        assert callbacks == [('a', 'COMPLETED')]
        assert [e.eventType for e in supervisor.eventStore.read('a')] == ['job.completed']
        # Another job's true timeout still runs, but its terminal callback waits for FIFO.
        supervisor._heartbeat['b'] = 0
        b, b_errors = threaded(lambda: supervisor.checkHeartbeat('b'))
        deadline = time.monotonic() + 1
        while 'b' not in supervisor._finalizations and time.monotonic() < deadline:
            threading.Event().wait(.001)
        assert supervisor.getProcess('b').terminations == 1
        assert supervisor.jobRepository.get('a').status == 'RUNNING'
    finally:
        release.set()
        owner.join(2)
        if b is not None:
            b.join(2)
        if graceful is not None:
            graceful.join(2)
    assert not errors and not b_errors and not graceful_errors
    assert not owner.is_alive() and not b.is_alive() and not graceful.is_alive()
    assert callbacks == [('a', 'COMPLETED'), ('b', 'FAILED')]
    assert_retired(supervisor)


def test_fallback_pending_owns_quota_and_blocks_presentation_release(runtime, tmp_path, monkeypatch):
    presentation = PresentationService(runtime, tmp_path / 'presentation')
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    presentation.jobs['a'] = {'scopeIds': [], 'ordinals': []}
    entered, release = threading.Event(), threading.Event()
    original_callback = runtime.jobSupervisor.terminalCallback
    def callback(*args):
        original_callback(*args)
        entered.set()
        assert release.wait(3)
    runtime.jobSupervisor.terminalCallback = callback
    runtime.jobSupervisor.maxConcurrentJobs = 1
    def fail(*args, **kwargs):
        raise RuntimeError('event-persistence')
    monkeypatch.setattr(runtime.eventStore, 'append', fail)
    owner, errors = threaded(lambda: runtime.jobSupervisor.bridgeError('a', RuntimeError('first')))
    try:
        assert entered.wait(1)
        assert runtime.jobRepository.get('a').status == 'FAILED'
        with pytest.raises(ValueError, match='Worker still owns'):
            presentation.release('a')
        with pytest.raises(RuntimeError, match='MAX_CONCURRENT'):
            runtime.jobSupervisor.startJob(JobProcessSpec('b', 'unused', 'main'))
        fence = presentation.terminalStates['a']
    finally:
        release.set()
        owner.join(2)
    assert not errors
    assert runtime.jobSupervisor.recoverFinalizations() == []
    assert presentation.terminalStates['a'] == fence
    presentation.jobs.clear()
    runtime.jobSupervisor.terminalCallback = original_callback


def test_notification_failure_after_terminal_set_repairs_once_per_close(runtime, monkeypatch):
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    callback_calls = []
    runtime.jobSupervisor.terminalCallback = lambda *args: callback_calls.append(args)
    original = runtime.eventStore.markTerminal
    calls = []
    failing = [True]
    def mark(job):
        calls.append(job)
        original(job)
        if failing[0]:
            raise RuntimeError('notification-error')
    monkeypatch.setattr(runtime.eventStore, 'markTerminal', mark)
    with pytest.raises(RuntimeError, match='notification-error'):
        consume(runtime.jobSupervisor)
    for expected in [2, 3]:
        with pytest.raises(RuntimeError):
            runtime.close()
        assert len(calls) == expected and not runtime._closed
        assert len(callback_calls) == 1
    failing[0] = False
    runtime.close()
    assert len(calls) == 4 and runtime._closed and len(callback_calls) == 1


def test_unknown_partial_disposal_is_not_retried_or_declared_closed(runtime, monkeypatch):
    calls = []
    def uncertain():
        calls.append('close')
        raise RuntimeError('unknown-native-disposal')
    original = runtime.previewAssetStore.close
    monkeypatch.setattr(runtime.previewAssetStore, 'close', uncertain)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            runtime.close()
        assert not runtime._closed and runtime._runtimeDataLock._acquired
    assert calls == ['close']
    # Test-only resolution of injected unknown state so fixture threads can retire.
    monkeypatch.setattr(runtime.previewAssetStore, 'close', original)
    runtime._closeStages.pop('preview-assets')


def test_queue_close_failure_does_not_repeat_confirmed_process_close():
    runtime, supervisor, _ = setup_jobs('a')
    process, _, channel = supervisor._handles['a']
    def uncertain():
        channel.closes += 1
        raise RuntimeError('queue-close-unknown')
    channel.close = uncertain
    assert not stop(runtime).ok
    assert process.closes == channel.closes == 1
    for _ in range(2):
        assert supervisor.finishRetirements() == ['a: E_JOB_RETIREMENT_INCOMPLETE']
        supervisor.shutdown()
    assert process.closes == channel.closes == 1
    assert supervisor._retirements['a'].processClosed


def test_builtin_workspace_repair_uses_frozen_path_without_callback_replay(runtime, monkeypatch):
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    workspace = runtime.workspaceRoot / 'a'
    workspace.mkdir()
    (workspace / 'owned').write_text('owned')
    runtime._workspacePaths['a'] = workspace
    runtime.jobSupervisor._handles['a'] = (Process(), threading.Event(), Channel())
    original = runtime._removeOwnedWorkspace
    paths = []
    def partial(job, path):
        paths.append(path)
        original(job, path)
        if len(paths) == 1:
            raise RuntimeError('after-owned-removal')
    monkeypatch.setattr(runtime, '_removeOwnedWorkspace', partial)
    reply = stop(runtime)
    assert not reply.ok and runtime.jobSupervisor.ownsJobResources('a')
    receipt = runtime.jobSupervisor._retirements['a']
    assert receipt.callbackStarted and not receipt.callbackDone
    assert runtime.jobSupervisor.getProcess('a') is None and not workspace.exists()
    event_count = len(runtime.eventStore.read('a'))
    runtime.close()
    assert runtime._closed and paths == [workspace, workspace]
    assert len(runtime.eventStore.read('a')) == event_count
    assert not runtime.jobSupervisor.ownsJobResources('a')


def test_publication_failure_close_retries_exact_update_once_without_callback_replay(runtime, monkeypatch):
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    callback_calls, updates = [], []
    runtime.jobSupervisor.terminalCallback = lambda *args: callback_calls.append(args)
    original = runtime.sqliteStore.updateJobStatus
    failing = [True]
    def persist(**changes):
        updates.append(changes)
        if failing[0]:
            raise RuntimeError('publication-error')
        return original(**changes)
    monkeypatch.setattr(runtime.sqliteStore, 'updateJobStatus', persist)
    with pytest.raises(RuntimeError, match='publication-error'):
        consume(runtime.jobSupervisor)
    event_count = len(runtime.eventStore.read('a'))
    for expected in [2, 3]:
        with pytest.raises(RuntimeError):
            runtime.close()
        assert len(updates) == expected and not runtime._closed
        assert len(callback_calls) == 1
        assert len(runtime.eventStore.read('a')) == event_count
        assert not stop(runtime).ok
    failing[0] = False
    runtime.close()
    assert runtime._closed and len(updates) == 4
    assert all(value == updates[0] for value in updates)
    assert len(callback_calls) == 1 and stop(runtime).ok


def test_degraded_notification_repair_does_not_republish_or_replay_callback(monkeypatch):
    runtime, supervisor, persistence = setup_jobs('a')
    callbacks, marks = [], []
    supervisor.terminalCallback = lambda *args: callbacks.append(args)
    original_mark = supervisor.eventStore.markTerminal
    failing = [True]
    def append(*args, **kwargs):
        raise RuntimeError('event-persistence')
    def mark(job):
        marks.append(job)
        original_mark(job)
        if failing[0]:
            raise RuntimeError('notification-error')
    monkeypatch.setattr(supervisor.eventStore, 'append', append)
    monkeypatch.setattr(supervisor.eventStore, 'markTerminal', mark)
    with pytest.raises(RuntimeError, match='notification-error'):
        supervisor.bridgeError('a', RuntimeError('original'))
    assert not stop(runtime).ok and supervisor.ownsJobResources('a')
    assert len(callbacks) == len(marks) == 1 and not persistence.updates
    failing[0] = False
    assert supervisor.recoverFinalizations() == []
    assert len(callbacks) == 1 and len(marks) == 2 and not persistence.updates
    assert stop(runtime).ok
    supervisor.finishRetirements()
    assert not supervisor._ownedJobs()


def test_sync_grpc_without_deadline_waits_for_pending_attempt():
    from concurrent.futures import ThreadPoolExecutor
    runtime, supervisor, _ = setup_jobs('a')
    entered, release = threading.Event(), threading.Event()
    def callback(*args):
        entered.set()
        assert release.wait(3)
    supervisor.terminalCallback = callback
    owner, errors = threaded(lambda: consume(supervisor))
    pool = ThreadPoolExecutor(max_workers=2)
    server = grpc.server(pool)
    rpc.add_RuntimeServiceServicer_to_server(runtime, server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    call = None
    try:
        assert entered.wait(1)
        call = rpc.RuntimeServiceStub(channel).StopJob.future(pb.StopJobRequest(job_id='a', mode='force'))
        try:
            reply = call.result(timeout=.1)
        except grpc.FutureTimeoutError:
            pass
        else:
            pytest.fail("pending Stop returned before callback: " + str(reply))
        assert not supervisor._finalizations['a'].done.is_set()
        release.set()
        assert call.result(timeout=1).ok
        owner.join(1)
        assert not errors
    finally:
        release.set()
        owner.join(2)
        if call is not None:
            call.cancel()
        channel.close()
        server.stop(0).wait(2)
        pool.shutdown(wait=True)
        assert_retired(supervisor)


def test_presentation_public_mutators_reject_callback_owner_before_outer_lock(runtime, tmp_path):
    presentation = PresentationService(runtime, tmp_path / 'presentation')
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    original_callback = runtime.jobSupervisor.terminalCallback
    def callback(*args):
        for action in [lambda: presentation.consume('a', {'eventType': 'display.timing'}),
                       lambda: presentation.terminal('a', 'FAILED'),
                       lambda: presentation.release('a'), presentation.close,
                       lambda: runtime.jobManager.createJob('project', 1, 'main', jobId='b')]:
            with pytest.raises(RuntimeError, match='E_FINALIZATION_REENTRANT'):
                action()
        assert runtime.jobRepository.get('b') is None
        original_callback(*args)
    runtime.jobSupervisor.terminalCallback = callback
    consume(runtime.jobSupervisor)
    runtime.jobSupervisor.terminalCallback = original_callback


def test_restoring_installed_callback_does_not_hide_older_chain_owner(runtime, tmp_path):
    presentation = PresentationService(runtime, tmp_path / 'presentation')
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    entered, release = threading.Event(), threading.Event()
    original_callback = runtime.jobSupervisor.terminalCallback
    def wrapper(*args):
        original_callback(*args)
        entered.set()
        assert release.wait(3)
    runtime.jobSupervisor.terminalCallback = wrapper
    owner, errors = threaded(lambda: consume(runtime.jobSupervisor))
    try:
        assert entered.wait(1)
        runtime.jobSupervisor.terminalCallback = original_callback
        with pytest.raises(RuntimeError, match='callback epoch'):
            presentation.close()
        assert not presentation.closed and presentation.monitor.is_alive()
    finally:
        release.set()
        owner.join(2)
    assert not errors
    presentation.close()


def test_real_display_reader_drains_during_atomic_callback_close(runtime, tmp_path):
    presentation = PresentationService(runtime, tmp_path / 'presentation')
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='COMPLETED'))
    ready, release_read = threading.Event(), threading.Event()
    class GatedChannel(queue.Queue):
        first = True
        def get(self, timeout=None):
            if self.first:
                self.first = False
                ready.set()
                assert release_read.wait(3)
                return {'eventType': 'display.timing'}
            return super().get(timeout=timeout)
    config = {'queue': GatedChannel(), 'scopeIds': [], 'ordinals': []}
    presentation.jobs['a'] = config
    timing_events = []
    presentation.timings['a'] = timing_events
    presentation.terminalStates['a'] = ('COMPLETED', 0)
    reader = threading.Thread(target=presentation._read, args=('a', config), name='actual-display-reader')
    presentation.readers['a'] = reader
    original_join = reader.join
    joins = []
    def join(timeout=None):
        joins.append(timeout)
        release_read.set()  # Close has reached its real join boundary.
        original_join(timeout)
    reader.join = join
    reader.start()
    assert ready.wait(1)
    failure = None
    try:
        presentation.close()
    except BaseException as error:
        failure = error
    finally:
        release_read.set()
        original_join(2)
        if failure is not None:
            presentation.close()
    assert failure is None, str(failure)
    assert presentation.closed and not reader.is_alive() and len(timing_events) == 1
    assert joins == [2]


@pytest.mark.parametrize('failed_event', ['job.stopping', 'process.terminated', 'job.aborted'])
def test_runtime_close_preclaim_append_failure_keeps_shutdown_responsibility(runtime, monkeypatch, failed_event):
    supervisor = runtime.jobSupervisor
    for job in ['a', 'b']:
        runtime.jobRepository.create(JobRecord(job, 'project', 1, 'main', status='RUNNING'))
        supervisor._handles[job] = (Process(), threading.Event(), Channel())
    process, cancel, channel = supervisor._handles['b']
    callbacks = []
    supervisor.terminalCallback = lambda job, status: callbacks.append((job, status))
    original = runtime.eventStore.append
    failure = RuntimeError('before-b-terminal-claim')
    def append(jobId, eventType, *args, **kwargs):
        if jobId == 'b' and eventType == failed_event:
            raise failure
        return original(jobId, eventType, *args, **kwargs)
    monkeypatch.setattr(runtime.eventStore, 'append', append)
    try:
        with pytest.raises(RuntimeError) as caught:
            runtime.close()
        assert caught.value is failure
        assert callbacks == [('a', 'ABORTED')]
        assert runtime.jobRepository.get('b').status == 'STOPPING'
        assert 'b' not in supervisor._terminalEvents
        assert 'b' not in supervisor._finalizations and 'b' not in supervisor._recoveries
        assert supervisor.ownsJobResources('b')
        assert supervisor.getProcess('b') is process
        assert process.closes == channel.closes == 0
        assert not runtime._closed and runtime._runtimeDataLock._acquired
        assert 'preview-assets' not in runtime._closeStages
        if failed_event != 'process.terminated':
            with pytest.raises(RuntimeError) as repeated:
                runtime.close()
            assert repeated.value is failure
            assert callbacks == [('a', 'ABORTED')]
            assert supervisor.getProcess('b') is process
            assert process.closes == channel.closes == 0
            assert not runtime._closed and runtime._runtimeDataLock._acquired
    finally:
        monkeypatch.setattr(runtime.eventStore, 'append', original)
        runtime.close()
    assert runtime._closed and not supervisor.ownsJobResources('b')
    assert runtime.jobRepository.get('b').status == 'ABORTED'
    assert callbacks == [('a', 'ABORTED'), ('b', 'ABORTED')]
    assert process.closes == channel.closes == 1


@pytest.mark.parametrize('phase', ['callback', 'publication', 'notification', 'retirement'])
def test_first_stop_rpc_reports_accepted_keyerror_phase(monkeypatch, phase):
    runtime, supervisor, persistence = setup_jobs('a')
    failure = KeyError('failure-after-acceptance')
    def fail(*args, **kwargs):
        raise failure
    if phase == 'callback':
        supervisor.terminalCallback = fail
    elif phase == 'publication':
        original = persistence.updateJobStatus
        def persist(**changes):
            if changes['status'] == 'ABORTED':
                raise failure
            original(**changes)
        monkeypatch.setattr(persistence, 'updateJobStatus', persist)
    elif phase == 'notification':
        monkeypatch.setattr(supervisor.eventStore, 'markTerminal', fail)
    else:
        supervisor.retiredCallback = fail
    reply = stop(runtime)
    assert not reply.ok and reply.status == 'ABORTED'
    expected = ('E_JOB_RETIREMENT_INCOMPLETE' if phase == 'retirement'
                else 'E_JOB_FINALIZATION_FAILED: ' + phase)
    assert reply.message == expected


def test_runtime_close_waits_for_real_live_preview_terminal_publication(runtime):
    from emo_master.apps.runtime.preview.live import _LiveSession
    manager = runtime.livePreviewManager
    entered, release = threading.Event(), threading.Event()
    written = []
    original = manager.eventPublisher
    class FailedCamera:
        def executeNode(self, *args):
            raise RuntimeError('camera-failed')
    def publish(**event):
        entered.set()
        assert release.wait(5)
        written.append((runtime._closed, runtime._runtimeDataLock._acquired))
        return original(**event)
    manager.eventPublisher = publish
    session = _LiveSession('preview', 'project', 'main', 'camera', FailedCamera(), {}, manager._sessionTerminated)
    manager._sessions['preview'] = session
    session.start()
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError, match='preview capture thread did not stop'):
            runtime.close()
        assert not runtime._closed and runtime._runtimeDataLock._acquired
        assert 'preview-assets' not in runtime._closeStages
        assert session.thread.is_alive()
    finally:
        release.set()
        session.thread.join(2)
        runtime.close()
    assert written == [(False, True)]
    assert not session.thread.is_alive() and runtime._closed


def test_runtime_close_retains_unknown_live_preview_disposal_without_retry(runtime):
    from emo_master.apps.runtime.preview.live import _LiveSession
    manager = runtime.livePreviewManager
    disposed = []
    class FailedCamera:
        def executeNode(self, *args):
            raise RuntimeError('camera-failed')
        def disposeOperator(self):
            disposed.append('dispose')
            raise RuntimeError('unknown-camera-disposal')
    session = _LiveSession('preview', 'project', 'main', 'camera', FailedCamera(), {}, manager._sessionTerminated)
    manager._sessions['preview'] = session
    session.start()
    session.thread.join(2)
    assert not session.thread.is_alive()
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match='unknown-camera-disposal'):
                runtime.close()
            assert not runtime._closed and runtime._runtimeDataLock._acquired
            assert manager._sessions['preview'] is session
            assert manager._sessions['preview'].operator is session.operator
        assert disposed == ['dispose']
    finally:
        # Resolve only the injected unknown native outcome for fixture teardown.
        session._disposeError = None
        runtime.close()


def test_stop_missing_active_handle_preserves_failed_reply_without_mutation():
    runtime, supervisor, _ = setup_jobs()
    runtime.jobRepository.create(JobRecord('a', 'project', 1, 'main', status='RUNNING'))
    reply = stop(runtime)
    assert not reply.ok and reply.status == 'FAILED' and reply.message == 'job not found'
    assert runtime.jobRepository.get('a').status == 'RUNNING'
    assert not runtime.eventStore.read('a')


def test_runtime_close_retains_live_preview_failed_init_cleanup_owner(runtime):
    from tests.runtime.test_live_operator_preview import _descriptor
    manager = runtime.livePreviewManager
    disposed = []
    class FailedCamera:
        def initOperator(self, *args):
            raise RuntimeError('camera-init-failed')
        def disposeOperator(self):
            disposed.append(self)
            raise RuntimeError('unknown-init-cleanup')
    manager.operatorRegistry = {'vision.io.fake_camera': _descriptor(FailedCamera)}
    session_id, error = manager.open('vision.io.fake_camera', 'project', 'main', 'camera', {})
    assert session_id is None and 'camera-init-failed' in error and 'unknown-init-cleanup' in error
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match='unknown-init-cleanup'):
                runtime.close()
            assert not runtime._closed and runtime._runtimeDataLock._acquired
            assert len(manager._sessions) == 1
            retained = next(iter(manager._sessions.values()))
            assert retained.operator is disposed[0]
            assert not retained.thread.is_alive()
        assert len(disposed) == 1
    finally:
        # Resolve only the injected unknown native outcome for fixture teardown.
        for retained in manager._sessions.values():
            retained._disposeError = None
        runtime.close()
