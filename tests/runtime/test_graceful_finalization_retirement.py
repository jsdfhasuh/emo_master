"""Two graceful enforcers meet at explicit finalization/retirement boundaries."""
from __future__ import annotations

import queue
import threading

import pytest

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
from tests.runtime.test_job_supervisor_contracts import _FakeBridge, _FakeProcess, _SilentQueue


@pytest.mark.parametrize('boundary', ['before-publication', 'published-pending', 'completed-before-reap'])
def test_duplicate_graceful_enforcer_observes_winning_attempt_then_retires(monkeypatch, boundary):
    repository, events = JobRepository(), EventStore()
    record = JobRecord('job-timeout', 'project', 1, 'main', status='RUNNING')
    repository.create(record)
    repository.create(JobRecord('other', 'project', 1, 'main', status='RUNNING'))
    supervisor = JobSupervisor(repository, events, gracefulStopTimeoutMs=0)
    process = _FakeProcess()
    supervisor._handles[record.jobId] = (process, threading.Event(), _SilentQueue())
    supervisor._bridges[record.jobId] = _FakeBridge()
    callbacks, retired, owner, failures = [], [], [], []
    supervisor.terminalCallback = lambda *args: callbacks.append(args)
    supervisor.retiredCallback = retired.append
    entered, release, decision, returned = (threading.Event() for _ in range(4))
    method_name = {'before-publication': '_publishTerminal', 'published-pending': '_notifyTerminal',
                   'completed-before-reap': '_runTerminal'}[boundary]
    original = getattr(supervisor, method_name)

    def gate(ticket):
        if boundary == 'completed-before-reap':
            original(ticket)
        owner.append(threading.current_thread())
        entered.set()
        assert release.wait(3)
        if boundary != 'completed-before-reap':
            return original(ticket)

    monkeypatch.setattr(supervisor, method_name, gate)
    original_wait = supervisor._waitFirstAttempt
    def wait(*args, **kwargs):
        decision.set()  # The duplicate reached its outside-lock wait decision.
        return original_wait(*args, **kwargs)
    monkeypatch.setattr(supervisor, '_waitFirstAttempt', wait)

    def enforce():
        try:
            supervisor._enforceGracefulStop(record.jobId, process)
        except BaseException as error:
            failures.append(error)
        finally:
            returned.set()
            decision.set()

    observed = threading.Event()
    def observe_other_job():
        try:
            assert supervisor.getProcess(record.jobId) is process
            supervisor.consumeWorkerEvent('other', {'eventType': 'process.output'})
        except BaseException as error:
            failures.append(error)
        finally:
            observed.set()
    probe = threading.Thread(target=observe_other_job, name='ordinary-job-progress')
    duplicate = threading.Thread(target=enforce, name='duplicate-graceful-enforcer')
    supervisor.stopJob(record.jobId, mode='graceful')
    try:
        assert entered.wait(1)
        if boundary == 'before-publication':
            assert record.status == 'STOPPING'
        else:
            assert record.status == 'ABORTED' and record.errorCode == 'E_STOP_TIMEOUT'
        assert process.terminated and supervisor.getProcess(record.jobId) is process
        duplicate.start()
        assert decision.wait(1)
        if boundary != 'completed-before-reap':
            assert not returned.is_set(), 'duplicate returned while winning terminal attempt was pending'
            assert supervisor.getProcess(record.jobId) is process
            assert not process.closed
            probe.start()
            assert observed.wait(1), 'duplicate waited while holding Supervisor'
            assert len(events.read('other')) == 1
        else:
            assert returned.wait(1)
            assert supervisor.getProcess(record.jobId) is None
            assert process.closed and retired == [record.jobId]
    finally:
        release.set()
        if duplicate.ident is not None:
            duplicate.join(2)
        for thread in owner:
            thread.join(2)
        if probe.ident is not None:
            probe.join(2)
    assert not duplicate.is_alive() and all(not thread.is_alive() for thread in owner)
    assert not failures
    assert supervisor.getProcess(record.jobId) is None
    assert callbacks == [(record.jobId, 'ABORTED')]
    assert retired == [record.jobId]
    assert [event.eventType for event in events.read(record.jobId)] == [
        'job.stopping', 'process.terminated', 'job.aborted']


@pytest.mark.parametrize('watcher_outcome', ['published-pending', 'publication-failed'])
def test_new_graceful_rpc_keeps_frozen_acceptance_when_watcher_advances(monkeypatch, watcher_outcome):
    from contextlib import contextmanager
    from tests.runtime.test_terminal_finalization_contract import setup_jobs, stop
    runtime, supervisor, persistence = setup_jobs('a')
    advanced, release = threading.Event(), threading.Event()
    owners, failures = [], []
    requester = threading.current_thread()
    original_attempt = supervisor._terminalAttempt
    @contextmanager
    def attempt(*args, **kwargs):
        with original_attempt(*args, **kwargs) as claims:
            yield claims
        if threading.current_thread() is requester:
            assert advanced.wait(1)  # Request has left Supervisor; let its watcher advance.
    monkeypatch.setattr(supervisor, '_terminalAttempt', attempt)
    original_notify = supervisor._notifyTerminal
    def notify(ticket):
        advanced.set()
        assert release.wait(3)
        return original_notify(ticket)
    if watcher_outcome == 'published-pending':
        monkeypatch.setattr(supervisor, '_notifyTerminal', notify)
    else:
        persistence.fail = True
    original_enforce = supervisor._enforceGracefulStop
    def enforce(*args):
        owners.append(threading.current_thread())
        try:
            original_enforce(*args)
        except BaseException as error:
            failures.append(error)
        finally:
            if watcher_outcome == 'publication-failed':
                advanced.set()
    monkeypatch.setattr(supervisor, '_enforceGracefulStop', enforce)
    try:
        reply = stop(runtime, mode='graceful')
        assert runtime.jobRepository.get('a').status == 'ABORTED'
        assert supervisor.ownsJobResources('a')
        assert reply.ok and reply.status == 'STOPPING' and reply.message == 'job stopping (graceful)'
    finally:
        release.set()
        for owner in owners:
            owner.join(2)
        persistence.fail = False
        supervisor.recoverFinalizations()
        supervisor.finishRetirements()
    assert all(not owner.is_alive() for owner in owners)
    assert len(failures) == (watcher_outcome == 'publication-failed')
    assert not supervisor.ownsJobResources('a')


def test_duplicate_graceful_waiter_keeps_failed_publication_owned_without_repair(monkeypatch):
    from tests.runtime.test_terminal_finalization_contract import setup_jobs, stop, threaded
    runtime, supervisor, persistence = setup_jobs('a')
    process = supervisor.getProcess('a')
    persistence.fail = True
    entered, release, waiting = (threading.Event() for _ in range(3))
    callbacks, primary = [], []
    supervisor.terminalCallback = lambda *args: callbacks.append(args)
    original_publish = supervisor._publishTerminal
    def publish(ticket):
        error = original_publish(ticket)  # Memory is terminal even though persistence failed.
        primary.append(error)
        entered.set()
        assert release.wait(3)
        return error
    monkeypatch.setattr(supervisor, '_publishTerminal', publish)
    original_wait = supervisor._waitFirstAttempt
    def wait(*args, **kwargs):
        waiting.set()
        return original_wait(*args, **kwargs)
    monkeypatch.setattr(supervisor, '_waitFirstAttempt', wait)
    owner, owner_errors = threaded(lambda: supervisor._enforceGracefulStop('a', process))
    observer = None
    try:
        assert entered.wait(1)
        observer, observer_errors = threaded(lambda: supervisor._enforceGracefulStop('a', process))
        assert waiting.wait(1)
        assert supervisor.getProcess('a') is process and not process.closes
    finally:
        release.set()
        owner.join(2)
        if observer is not None:
            observer.join(2)
    assert not owner.is_alive() and not observer.is_alive()
    assert owner_errors == primary and not observer_errors
    assert len(persistence.updates) == len(callbacks) == 1
    assert supervisor.getProcess('a') is process and not process.closes
    assert supervisor._recoveries['a'].notification == 'NOT_ATTEMPTED'
    assert stop(runtime).message == 'E_JOB_FINALIZATION_FAILED: publication'
    assert [event.eventType for event in supervisor.eventStore.read('a')] == ['process.terminated', 'job.aborted']
    persistence.fail = False
    assert supervisor.recoverFinalizations() == []
    assert supervisor.finishRetirements() == []
    assert not supervisor.ownsJobResources('a')


def test_duplicate_graceful_enforcer_keeps_actual_live_bridge_queue_owner():
    from emo_master.apps.runtime.jobs.event_bridge import EventBridge
    from tests.runtime.test_job_supervisor_contracts import _ClosableQueue, _supervisor
    supervisor, repository, _, process = _supervisor()
    supervisor.gracefulStopTimeoutMs = 0
    reading, release = threading.Event(), threading.Event()
    class ReadingQueue(_ClosableQueue):
        def get(self, timeout=None):
            reading.set()
            assert release.wait(3)
            assert self.closeCount == 0 and not process.closed
            raise queue.Empty
    channel = ReadingQueue()
    cancel = threading.Event()
    supervisor._handles['job-stop'] = (process, cancel, channel)
    bridge = EventBridge(supervisor, 'job-stop', process, channel, cancel)
    supervisor._bridges['job-stop'] = bridge
    retired = []
    supervisor.retiredCallback = retired.append
    bridge.start()
    try:
        assert reading.wait(1)
        supervisor._enforceGracefulStop('job-stop', process)
        assert repository.get('job-stop').status == 'ABORTED'
        supervisor._enforceGracefulStop('job-stop', process)
        assert bridge.is_alive() and bridge.stopEvent.is_set()
        assert supervisor.ownsJobResources('job-stop')
        assert supervisor.getProcess('job-stop') is process
        assert not process.closed and channel.closeCount == 0 and not retired
    finally:
        release.set()
        bridge.join(2)
    assert not bridge.is_alive() and not supervisor.ownsJobResources('job-stop')
    assert process.closed and channel.closeCount == 1 and retired == ['job-stop']


def test_graceful_observer_retirement_fault_is_owned_and_never_replayed(monkeypatch):
    from tests.runtime.test_terminal_finalization_contract import setup_jobs, stop, threaded
    runtime, supervisor, _ = setup_jobs('a')
    process = supervisor.getProcess('a')
    entered, release = threading.Event(), threading.Event()
    retired = []
    failure = RuntimeError('observer-native-retirement-unknown')
    def retire(job):
        retired.append(job)
        raise failure
    supervisor.retiredCallback = retire
    original_run = supervisor._runTerminal
    def run(ticket):
        original_run(ticket)
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(supervisor, '_runTerminal', run)
    owner, owner_errors = threaded(lambda: supervisor._enforceGracefulStop('a', process))
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError) as caught:
            supervisor._enforceGracefulStop('a', process)
        assert caught.value is failure
        assert supervisor.getProcess('a') is None and supervisor.ownsJobResources('a')
        assert stop(runtime).message == 'E_JOB_RETIREMENT_INCOMPLETE'
        supervisor._enforceGracefulStop('a', process)
        assert retired == ['a']
    finally:
        release.set()
        owner.join(2)
    assert not owner.is_alive() and not owner_errors
    assert retired == ['a'] and supervisor.ownsJobResources('a')
