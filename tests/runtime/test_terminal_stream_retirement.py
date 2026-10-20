"""A terminal event is readable before its live bridge has retired."""
import threading
import time
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.finalization import RetirementReceipt
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
from tests.runtime.test_terminal_finalization_contract import Process, Channel
from tests.runtime.test_run_inspection_rpc import running


def testTerminalFollowStreamWaitsForItsLiveBridgeBeforeAllowingReload(tmp_path, monkeypatch):
    with running(tmp_path) as (runtime, client, project, session):
        supervisor = runtime.jobSupervisor
        terminal, release, boundary, ended = (threading.Event() for _ in range(4))
        consumed = supervisor.consumeWorkerEvent
        waiting = supervisor.waitForRetirement
        failures, events, waits = [], [], []

        def consume(jobId, event):
            consumed(jobId, event)
            if event.get('eventType') == 'job.completed':
                terminal.set()
                assert release.wait(5), 'test did not release the completed worker bridge'

        def wait(*args, **kwargs):
            waits.append(kwargs)
            boundary.set()
            return waiting(*args, **kwargs)

        def follow(jobId):
            try:
                events.extend(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id=jobId, follow=True), None))
            except BaseException as error:
                failures.append(error)
            finally:
                ended.set()
                boundary.set()

        monkeypatch.setattr(supervisor, 'consumeWorkerEvent', consume)
        monkeypatch.setattr(supervisor, 'waitForRetirement', wait)
        first = client.startJob(project, inspectionSessionId=session)
        assert first.ok, first.message
        observer = threading.Thread(target=follow, args=(first.job_id,))
        observer.start()
        try:
            assert terminal.wait(5)
            assert boundary.wait(5)
            assert not ended.is_set(), 'terminal follow stream ended while its worker bridge still owned resources'
            assert waits and waits[0]['jobId'] == first.job_id
            assert runtime.jobRepository.get(first.job_id).status == 'COMPLETED'
            assert supervisor.ownsJobResources(first.job_id)
            assert any(event.event_type == 'job.completed' for event in events)
            replay = list(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id=first.job_id), None))
            assert any(event.event_type == 'job.completed' for event in replay)
            assert len(waits) == 1, 'non-follow history must not wait for retirement'
            blocked = client.loadProject(project)
            assert not blocked.ok and 'stop and release running Jobs' in blocked.message
        finally:
            release.set()
            observer.join(5)
        assert not observer.is_alive() and not failures
        assert any(event.event_type == 'job.completed' for event in events)
        assert not supervisor.ownsJobResources(first.job_id)
        assert client.loadProject(project).ok
        second = client.startJob(project, inspectionSessionId=session)
        assert second.ok and second.job_id != first.job_id
        list(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id=second.job_id, follow=True), None))
        assert runtime.jobRepository.get(second.job_id).status == 'COMPLETED'
        assert not supervisor.ownsJobResources(second.job_id)


def testTerminalFollowCancellationLeavesLiveBridgeOwned(tmp_path, monkeypatch):
    with running(tmp_path) as (runtime, client, project, session):
        supervisor = runtime.jobSupervisor
        terminal, release, waiting, cancelled = (threading.Event() for _ in range(4))
        originalConsume, originalWait = supervisor.consumeWorkerEvent, supervisor.waitForRetirement
        failures, events = [], []

        def consume(jobId, event):
            originalConsume(jobId, event)
            if event.get('eventType') == 'job.completed':
                terminal.set()
                assert release.wait(5)

        def wait(*args, **kwargs):
            waiting.set()
            return originalWait(*args, **kwargs)

        def follow(jobId):
            try:
                events.extend(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id=jobId, follow=True),
                    SimpleNamespace(is_active=lambda: not cancelled.is_set())))
            except BaseException as error:
                failures.append(error)

        monkeypatch.setattr(supervisor, 'consumeWorkerEvent', consume)
        monkeypatch.setattr(supervisor, 'waitForRetirement', wait)
        first = client.startJob(project, inspectionSessionId=session)
        assert first.ok, first.message
        observer = threading.Thread(target=follow, args=(first.job_id,))
        observer.start()
        try:
            assert terminal.wait(5) and waiting.wait(5)
            assert any(event.event_type == 'job.completed' for event in events)
            cancelled.set()
            observer.join(2)
            assert not observer.is_alive() and not failures
            assert supervisor.ownsJobResources(first.job_id)
            assert supervisor.getProcess(first.job_id) is not None
            assert not supervisor._bridges[first.job_id].stopEvent.is_set()
        finally:
            cancelled.set()
            release.set()
            observer.join(5)
        originalWait(jobId=first.job_id)
        assert not supervisor.ownsJobResources(first.job_id)


def testScopedRetirementWaitNeverJoinsUnrelatedJob(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    supervisor = runtime.jobSupervisor
    target = threading.Thread(target=lambda: None)
    target.start()
    target.join()

    def unexpectedJoin(**kwargs):
        raise AssertionError('wait joined an unrelated active Job')

    supervisor._bridges.update(target=target, unrelated=SimpleNamespace(ident=1, join=unexpectedJoin))
    try:
        supervisor.waitForRetirement(timeoutSeconds=None, jobId='target')
        supervisor.waitForRetirement(timeoutSeconds=None, jobId='already-retired')
        assert set(supervisor._bridges) == {'target', 'unrelated'}
    finally:
        supervisor._bridges.clear()
        runtime.close()


def testFollowStreamDoesNotRepairOrReleaseFailedRetirement(tmp_path, monkeypatch):
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    supervisor = runtime.jobSupervisor
    record = JobRecord('failed-retirement', 'project', 1, 'main', status='COMPLETED')
    runtime.jobRepository.create(record)
    runtime.eventStore.append(record.jobId, 'job.completed', 'execution completed')
    receipt = RetirementReceipt()
    receipt.fault('callback', RuntimeError('retirement callback outcome is uncertain'))
    supervisor._retirements[record.jobId] = receipt
    repairs = []
    receipt.repair = lambda: repairs.append(record.jobId)
    try:
        events = list(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id=record.jobId, follow=True), None))
        assert any(event.event_type == 'job.completed' for event in events)
        assert supervisor.ownsJobResources(record.jobId)
        assert supervisor._retirements[record.jobId] is receipt and set(receipt.faults) == {'callback'}
        assert not repairs
        reply = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(tmp_path / 'project.json')), None)
        assert not reply.ok and 'stop and release running Jobs' in reply.message
    finally:
        # Remove only this injected uncertainty so ordinary Runtime teardown can finish.
        supervisor._retirements.pop(record.jobId)
        runtime.close()


@pytest.mark.parametrize('exitReason', ['cancelled', 'deadline', 'timeout'])
def testObserverCanLeaveWhileRetirementCallbackOwnsSupervisorLock(exitReason):
    repository = JobRepository()
    repository.create(JobRecord('target', 'project', 1, 'main', status='COMPLETED'))
    supervisor = JobSupervisor(repository, EventStore())
    entered, release, observing, cancelled = (threading.Event() for _ in range(4))
    errors, callbacks = [], []

    def callback(jobId):
        callbacks.append(jobId)
        entered.set()
        assert release.wait(5)

    def active():
        observing.set()
        return not cancelled.is_set()

    def observe():
        try:
            deadline = time.monotonic() + .15 if exitReason == 'deadline' else None
            timeout = .15 if exitReason == 'timeout' else None
            supervisor.waitForRetirement(timeoutSeconds=timeout, deadline=deadline, jobId='target',
                cancellation=SimpleNamespace(is_active=active))
        except BaseException as error:
            errors.append(error)

    supervisor.retiredCallback = callback
    process, channel = Process(alive=False), Channel()
    supervisor._handles['target'] = (process, threading.Event(), channel)
    bridge = threading.Thread(target=supervisor.bridgeStopped, args=('target',))
    supervisor._bridges['target'] = bridge
    observer = threading.Thread(target=observe)
    bridge.start()
    try:
        assert entered.wait(2)
        observer.start()
        assert observing.wait(2)
        if exitReason == 'cancelled':
            cancelled.set()
        observer.join(.5)
        assert not observer.is_alive(), 'observer remained blocked on the retirement callback lock'
        assert not errors and bridge.is_alive()
        assert callbacks == ['target']
        assert not supervisor._retirements['target'].callbackDone
    finally:
        release.set()
        bridge.join(2)
        if observer.ident is not None:
            observer.join(2)
    assert not bridge.is_alive() and not observer.is_alive()
    assert callbacks == ['target'] and process.closes == channel.closes == 1
    assert not supervisor.ownsJobResources('target')
