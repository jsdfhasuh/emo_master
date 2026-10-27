"""Deterministic startup stops through the real Worker, IPC, and retirement path."""
from functools import partial
import json
import multiprocessing
from pathlib import Path
import queue
from threading import Event

import pytest

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.ipc import DeviceProxy
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.jobs import worker_main
from emo_master.apps.runtime.project_config.cycle_plan import CyclePlanSession
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
from emo_master.apps.runtime.workflow.context import RunContext
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_job_supervisor_contracts import _supervisor


BUILTINS = Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'


def _spec(tmp_path):
    payload = simulatedProject()
    path = tmp_path / 'project.emoproj'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return JobProcessSpec('job-stop', str(path), 'main', pluginRootPaths=(str(BUILTINS),),
        jobWorkspacePath=str(tmp_path / 'job'), projectId=payload['project']['projectId'],
        managedConfiguration=True, planRevision=1, legacySnapshotPolicy='NONE')


def _runAtBoundary(spec, cancelEvent, eventQueue, communicationPipe=None, devicePipe=None,
                   *, boundary, entered, release):
    """Spawn-only test barriers; the production Worker and transport still run."""
    from emo_master.apps.runtime.devices import ipc

    def park():
        entered.set()
        assert release.wait(20), 'startup barrier was not released'

    if boundary == 'before_started':
        original = worker_main._put

        def put(events, event):
            if event['eventType'] == 'job.started':
                park()
            return original(events, event)

        worker_main._put = put
    elif boundary == 'before_proxy':
        original = DeviceProxy._call

        def call(self, kind, action, **fields):
            if kind == 'framework_operation' and action == 'beginCycle':
                park()
            return original(self, kind, action, **fields)

        DeviceProxy._call = call
    elif boundary == 'before_io':
        original = ipc.sendBytes

        def send(connection, raw, cancelled, deadline):
            if json.loads(raw)['action'] == 'beginCycle':
                park()
            return original(connection, raw, cancelled, deadline)

        ipc.sendBytes = send
    else:
        assert boundary == 'before_receive'
        original = ipc.receiveBytes

        def receive(*args):
            park()
            return original(*args)

        ipc.receiveBytes = receive
    worker_main.runJobProcess(spec, cancelEvent, eventQueue, communicationPipe, devicePipe)


def testAlreadyCancelledWorkerAbortsBeforeStartup(tmp_path):
    cancellation = Event()
    cancellation.set()
    events = queue.Queue()
    worker_main.runJobProcess(_spec(tmp_path), cancellation, events)
    rows = list(events.queue)
    assert rows[-1]['eventType'] == 'job.aborted'
    assert rows[-1]['code'] == 'E_CANCELLED'
    assert not any(row['eventType'] in {'job.started', 'node.started'} for row in rows)


@pytest.mark.parametrize('boundary', ['before_started', 'before_proxy', 'before_io', 'before_receive'])
def testStartupStopRetiresAndNextSpawnReallyRuns(monkeypatch, tmp_path, boundary):
    import emo_master.apps.runtime.jobs.supervisor as supervisorModule

    context = multiprocessing.get_context('spawn')
    entered, release = context.Event(), context.Event()
    monkeypatch.setattr(supervisorModule, 'runJobProcess', partial(_runAtBoundary,
        boundary=boundary, entered=entered, release=release))
    spec = _spec(tmp_path)
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    supervisor = service.jobSupervisor
    retired = Event()
    retirement = []
    startedStates = []
    complete = supervisor._completeRetirement
    consume = supervisor.consumeWorkerEvent

    def completeRetirement(jobId, receipt):
        before = supervisor.ownsJobResources(jobId)
        complete(jobId, receipt)
        retirement.append((jobId, before, supervisor.ownsJobResources(jobId)))
        retired.set()

    def consumeEvent(jobId, event):
        consume(jobId, event)
        if event['eventType'] == 'job.started':
            record = service.jobRepository.get(jobId)
            startedStates.append((jobId, record.status, record.pid, record.startedAtMs))

    monkeypatch.setattr(supervisor, '_completeRetirement', completeRetirement)
    monkeypatch.setattr(supervisor, 'consumeWorkerEvent', consumeEvent)
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=spec.projectSnapshotPath), None)
        assert loaded.ok, loaded.message
        request = pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id='main')
        first = service.StartJob(request, None)
        assert first.ok, first.message
        assert entered.wait(20), 'real Worker did not reach the requested first-cycle boundary'
        assert service.deviceManager.drivers['camera'].sequence == 0
        stopped = service.StopJob(pb.StopJobRequest(job_id=first.job_id, mode='graceful'), None)
        assert stopped.ok and stopped.status == 'STOPPING', stopped
        assert supervisor.ownsJobResources(first.job_id)
        release.set()
        assert retired.wait(20), 'cancelled Worker resources did not retire'
        record = service.jobRepository.get(first.job_id)
        assert (record.status, record.errorCode) == ('ABORTED', 'E_CANCELLED')
        rows = service._eventsAfter(first.job_id, 0)
        assert [row.eventType for row in rows if row.eventType in
            {'job.completed', 'job.failed', 'job.aborted'}] == ['job.aborted']
        assert not any(row.eventType == 'node.started' for row in rows)
        assert retirement == [(first.job_id, True, False)]
        assert not service.deviceManager.workers and not service.projectConfiguration.cycles
        assert service.deviceManager.drivers['camera'].sequence == 0
        if boundary == 'before_started':
            assert startedStates[0][1] == 'STOPPING'
            assert startedStates[0][2] and startedStates[0][3] > 0
            types = [row.eventType for row in rows]
            assert types.index('job.stopping') < types.index('job.started') < types.index('job.aborted')

        # The next Job uses the normal target, not a manufactured completion.
        monkeypatch.setattr(supervisorModule, 'runJobProcess', worker_main.runJobProcess)
        retired.clear()
        second = service.StartJob(request, None)
        assert second.ok and second.job_id != first.job_id, second.message
        assert retired.wait(20), 'second real Worker did not finish and retire'
        assert service.jobRepository.get(second.job_id).status == 'COMPLETED'
        assert retirement[-1] == (second.job_id, True, False)
        assert service.deviceManager.drivers['camera'].sequence == 1
        captures = [json.loads(row.payloadJson) for row in service._eventsAfter(second.job_id, 0)
                    if row.eventType == 'node.completed' and row.nodeId == 'capture']
        assert len(captures) == 1 and captures[0]['outputs']['image']
        assert captures[0]['diagnostics']['deviceFrame']['shape'] == [5, 7, 3]
        assert not service.deviceManager.workers and not service.projectConfiguration.cycles
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.reservations
    finally:
        release.set()
        service.close()


@pytest.mark.parametrize('code,cancelled,outcome', [
    ('E_DEVICE_UNAVAILABLE', False, 'NOT_EXECUTED'),
    ('E_DEVICE_UNAVAILABLE', True, 'NOT_EXECUTED'),
    ('E_DEVICE_OUTCOME_UNKNOWN', True, 'UNKNOWN'),
    ('E_DEVICE_CANCELLED', False, 'NOT_EXECUTED'),
])
def testFrameworkFaultKeepsFailureAndDetailsEvenIfStopRacesReply(monkeypatch, tmp_path, code, cancelled, outcome):
    from emo_master.apps.runtime.devices import ipc

    cancellation = Event()
    events = queue.Queue()
    parent, child = multiprocessing.Pipe()
    diagnostics = {'requestId': 'first-cycle', 'detail': 'original fault'}

    def receive(*args):
        if cancelled:
            cancellation.set()
        return json.dumps(dict(ok=False, code=code, message='original framework fault',
                               outcome=outcome, diagnostics=diagnostics)).encode()

    monkeypatch.setattr(ipc, 'receiveBytes', receive)
    try:
        with pytest.raises(DeviceError) as error:
            worker_main.runJobProcess(_spec(tmp_path), cancellation, events, devicePipe=child)
        assert error.value.code == code and error.value.outcome == outcome
        assert error.value.diagnostics == diagnostics
        terminal = list(events.queue)[-1]
        assert terminal['eventType'] == 'job.failed' and terminal['code'] == code
        assert terminal['payload'] == dict(outcome=outcome, diagnostics=diagnostics)
        supervisor, repository, eventStore, process = _supervisor()
        process.alive = False
        if cancelled:
            repository.update('job-stop', status='STOPPING', stopMode='graceful')
        supervisor.consumeWorkerEvent('job-stop', terminal)
        assert repository.get('job-stop').status == 'FAILED'
        assert json.loads(eventStore.read('job-stop')[-1].payloadJson) == terminal['payload']
        supervisor.shutdown()
    finally:
        parent.close()
        child.close()


def testUncancelledSpawnedDeviceActionFailureStaysFailed(monkeypatch, tmp_path):
    spec = _spec(tmp_path)
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    retired = Event()
    complete = service.jobSupervisor._completeRetirement

    def completeRetirement(jobId, receipt):
        complete(jobId, receipt)
        retired.set()

    monkeypatch.setattr(service.jobSupervisor, '_completeRetirement', completeRetirement)
    diagnostics = {'driver': 'simulated', 'detail': 'capture failed'}

    def fail(*args, **kwargs):
        raise DeviceError('E_DEVICE_CAPTURE_FAILED', 'original capture fault',
                          outcome='UNKNOWN', diagnostics=diagnostics)

    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=spec.projectSnapshotPath), None)
        assert loaded.ok, loaded.message
        monkeypatch.setattr(service.deviceManager.drivers['camera'], 'execute', fail)
        started = service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id='main'), None)
        assert started.ok, started.message
        assert retired.wait(20), 'failed device action did not retire'
        record = service.jobRepository.get(started.job_id)
        assert (record.status, record.errorCode) == ('FAILED', 'E_DEVICE_CAPTURE_FAILED')
        failures = [json.loads(row.payloadJson) for row in service._eventsAfter(started.job_id, 0)
                    if row.eventType == 'node.failed']
        assert len(failures) == 1
        detail = failures[0]['diagnostics']['deviceOutcome']
        assert detail['code'] == 'E_DEVICE_CAPTURE_FAILED' and detail['outcome'] == 'UNKNOWN'
        assert failures[0]['diagnostics'] == dict(diagnostics, deviceOutcome=detail)
        terminal = [json.loads(row.payloadJson) for row in service._eventsAfter(started.job_id, 0)
                    if row.eventType == 'job.failed']
        assert terminal == [dict(outcome='UNKNOWN', diagnostics=dict(diagnostics, deviceOutcome=detail),
            previewSnapshots=dict(submitted=0, coalesced=0, written=0, failed=0, discarded=0, inspectionFailed=0,
                                  peakPending=0, peakBufferedBytes=0))]
        assert not service.deviceManager.workers and not service.projectConfiguration.cycles
    finally:
        service.close()


def testCleanupCancellationCannotReplaceOriginalCycleFailure():
    from types import SimpleNamespace

    original = DeviceError('E_DEVICE_FAILED', 'original capture fault', outcome='UNKNOWN',
                           diagnostics={'original': True})

    class Proxy:
        def framework(self, action, **fields):
            if action == 'beginCycle':
                return dict(cycleId='cycle', planRevision=1, ownsCycle=True)
            raise CancellationRequested('stop arrived during endCycle')

    runner = SimpleNamespace(operatorExecutor=SimpleNamespace(), compiledProject=object())
    plans = CyclePlanSession(runner, Proxy(), object(), None, {}, (), 1, lambda: False)
    with pytest.raises(DeviceError) as error:
        with plans.scope(RunContext.root('job', 'main')):
            raise original
    assert error.value is original
    assert error.value.outcome == 'UNKNOWN' and error.value.diagnostics == {'original': True}
