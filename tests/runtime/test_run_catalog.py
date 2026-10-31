"""Current-run identity, desktop handoff and independent concurrent operations."""
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import grpc
import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime, WorkflowSession
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_panel_protocol.messages import BrowserJobState
from tests.runtime.production_fixture import productionProject, waitFor


def runtimeModel():
    runtime = RuntimeService.__new__(RuntimeService)
    runtime.runtimeInstanceId = 'instance'
    runtime._projectStateLock = threading.RLock()
    runtime.jobRepository = JobRepository(SimpleNamespace(getJob=lambda job: {
        'jobId': job, 'projectId': 'project', 'status': 'COMPLETED'}))
    runtime._jobStartRequestIds = {}
    runtime.productionMode = True
    runtime.loadedDocument = SimpleNamespace(project=SimpleNamespace(projectId='project'),
        workflows={'main': SimpleNamespace(name='Main')},
        production=SimpleNamespace(inputs={'part': 3}, mode='continuous'),
        startupWorkflows=lambda: ['main'], runtime=SimpleNamespace(gracefulStopTimeoutMs=20))
    runtime.projectConfiguration = SimpleNamespace(current={'documentRevision': 7})
    runtime.deviceManager = SimpleNamespace(dispatchGroups={})
    owned = set()
    runtime.jobSupervisor = SimpleNamespace(_lock=threading.RLock(), ownsJobResources=lambda job: job in owned)
    runtime._presentationOwner = SimpleNamespace(lock=threading.RLock(), jobs={})
    stopped = []

    def stop(job, mode, context):
        stopped.append(job)
        runtime.jobRepository.update(job, status='ABORTED')
        owned.discard(job)
        return SimpleNamespace(ok=True, status='ABORTED', message='stopped')

    runtime.jobManager = SimpleNamespace(stopOutcome=stop)
    return runtime, owned, stopped


def add(runtime, job, status='RUNNING', workflow='main'):
    runtime.jobRepository.create(JobRecord(job, 'project', 1, workflow, status=status))


def testCatalogKeepsConcurrentRunsAndRetirementSeparateFromTerminal():
    runtime, owned, _ = runtimeModel()
    add(runtime, 'one')
    add(runtime, 'two')
    add(runtime, 'retiring', 'COMPLETED')
    add(runtime, 'done', 'COMPLETED')
    owned.add('retiring')
    reply = runtime.ListRuns(pb.ListRunsRequest(runtime_instance_id='instance', project_id='project'), None)
    assert reply.ok and {row.job_id for row in reply.runs} == {'one', 'two', 'retiring'}
    assert next(row for row in reply.runs if row.job_id == 'retiring').owns_resources
    assert len(runtime.ListRuns(pb.ListRunsRequest(include_terminal=True), None).runs) == 4
    old = runtime.GetRunState(pb.GetRunStateRequest(runtime_instance_id='instance', project_id='project', job_id='persisted'), None)
    assert not old.ok and old.code == 'JOB_NOT_FOUND'
    changed = runtime.ListRuns(pb.ListRunsRequest(runtime_instance_id='old'), None)
    assert not changed.ok and changed.code == 'RESET_REQUIRED'
    info = runtime.GetRuntimeInfo(pb.RuntimeInfoRequest(), None)
    assert info.production_mode and info.document_revision == 7
    assert info.startup_workflow_ids == ['main'] and json.loads(info.production_inputs_json) == {'part': 3}


@pytest.mark.parametrize('changes,code', [
    ({'expected_runtime_instance_id': 'old'}, 'RESET_REQUIRED'),
    ({'expected_project_id': 'other'}, 'PROJECT_MISMATCH'),
    ({'expected_run_group_id': 'other-group'}, 'RUN_GROUP_MISMATCH'),
    ({'expected_project_id': ''}, 'PROJECT_MISMATCH'),
])
def testStopIdentityRejectsWithoutTouchingAnyRun(changes, code):
    runtime, _, stopped = runtimeModel()
    add(runtime, 'one')
    reply = runtime.StopJob(pb.StopJobRequest(job_id='one', **changes), None)
    assert not reply.ok and reply.message.startswith(code)
    assert not stopped and runtime.jobRepository.get('one').status == 'RUNNING'


def testLegacyStopAndExactStopBothPreserveOtherConcurrentRun():
    runtime, _, stopped = runtimeModel()
    add(runtime, 'one')
    add(runtime, 'two')
    state = runtime.GetRunState(pb.GetRunStateRequest(runtime_instance_id='instance',
        project_id='project', job_id='one'), None).run
    message = BrowserJobState(runtimeInstanceId=state.runtime_instance_id, projectId=state.project_id,
        jobId=state.job_id, runGroupId=state.run_group_id, status=state.status, active=state.active,
        ownsResources=state.owns_resources, displayResourcesReleased=state.display_resources_released,
        allowedActions=list(state.allowed_actions))
    assert message.runGroupId == ''
    request = pb.StopJobRequest(job_id=message.jobId, expected_runtime_instance_id=message.runtimeInstanceId,
        expected_project_id=message.projectId, expected_run_group_id=message.runGroupId)
    assert request.HasField('expected_run_group_id')
    reply = runtime.StopJob(request, None)
    assert reply.ok and stopped == ['one']
    assert reply.status == runtime.jobRepository.get('one').status == 'ABORTED'
    assert runtime.jobRepository.get('two').status == 'RUNNING'
    assert runtime.StopJob(pb.StopJobRequest(job_id='two'), None).ok


def testDispatchRootRemainsActiveWhileConsumerOwnsResources():
    runtime, owned, _ = runtimeModel()
    add(runtime, 'producer', 'COMPLETED')
    add(runtime, 'consumer', workflow='consume')
    owned.add('consumer')
    group = SimpleNamespace(groupId='group', source='producer', members={'producer', 'consumer'},
                            slots={'slot': SimpleNamespace(jobId='consumer')})
    runtime.deviceManager.dispatchGroups = {'producer': group, 'consumer': group}
    reply = runtime.GetRunState(pb.GetRunStateRequest(runtime_instance_id='instance', project_id='project', job_id='producer'), None)
    assert reply.ok and reply.run.active and reply.run.owns_resources and reply.run.run_group_id == 'group'
    assert 'stop' in reply.run.allowed_actions


def controllerModel(runtime):
    owner = ProductionRuntime.__new__(ProductionRuntime)
    owner.runtime = runtime
    owner.document = runtime.loadedDocument
    owner.sessions = {'main': WorkflowSession(jobId='old')}
    owner.operationLock = threading.RLock()
    owner.presentation = runtime._presentationOwner
    return owner


def testDesktopDoesNotGuessAmongNewConcurrentRunsAndStopsExactSelection():
    runtime, _, stopped = runtimeModel()
    add(runtime, 'old', 'COMPLETED')
    add(runtime, 'first')
    add(runtime, 'second')
    owner = controllerModel(runtime)
    state = owner._workflowStatus('main')
    assert state['selectionRequired'] and state['jobId'] == '' and state['state'] == 'RUNNING'
    with pytest.raises(ValueError, match='select the Job'):
        owner.stop('main')
    owner.stop('main', jobId='first')
    assert stopped == ['first'] and runtime.jobRepository.get('second').status == 'RUNNING'
    state = owner._workflowStatus('main')
    assert state['jobId'] == 'second' and not state['selectionRequired']
    owner.stopAll()
    assert stopped == ['first', 'second']


def testRealRemoteStartsReplaceRetiredDesktopSelectionAndStopIndependently(tmp_path):
    root = tmp_path / 'project'
    productionProject(root, save=False)
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(root)
        first = owner.start()
        waitFor(lambda: owner.runtime.jobRepository.get(first).status == 'RUNNING')
        owner.stop()
        assert owner.status()['state'] == 'READY'
        with grpc.insecure_channel(owner.address) as channel:
            remote = rpc.RuntimeServiceStub(channel)
            info = remote.GetRuntimeInfo(pb.RuntimeInfoRequest(), timeout=5)

            def start():
                reply = remote.StartJob(pb.StartJobRequest(project_id=info.project_id, workflow_id='main',
                    start_request_id=uuid4().hex, expected_runtime_instance_id=info.runtime_instance_id,
                    legacy_snapshot_policy='NONE'), timeout=10)
                assert reply.ok, reply.message
                return reply.job_id

            second = start()
            waitFor(lambda: owner.runtime.jobRepository.get(second).status == 'RUNNING')
            state = owner.status()
            assert state['state'] == 'RUNNING' and state['jobId'] == second and not state['canLoad']
            owner.stop('main', jobId=first)
            assert owner.runtime.jobRepository.get(second).status == 'RUNNING'
            assert owner.status()['jobId'] == second
            third = start()
            waitFor(lambda: owner.runtime.jobRepository.get(third).status == 'RUNNING')
            runs = remote.ListRuns(pb.ListRunsRequest(runtime_instance_id=info.runtime_instance_id,
                project_id=info.project_id), timeout=5)
            assert runs.ok and {row.job_id for row in runs.runs} == {second, third}
            owner.stop('main', jobId=second)
            assert owner.runtime.jobRepository.get(third).status == 'RUNNING'
            assert owner.status()['jobId'] == third
            owner.stopAll()
            assert not remote.ListRuns(pb.ListRunsRequest(project_id=info.project_id), timeout=5).runs
    finally:
        owner.close()


def testRemoteNonStartupRootIsVisibleWithoutSelectingTerminalHistory(tmp_path):
    from emo_master.apps.designer.state.project_store import saveProject
    from tests.runtime.current_production_fixture import currentProductionProject
    root = tmp_path / 'project'
    path, document = currentProductionProject(root, mode='continuous')
    document.workflows['auxiliary'] = document.workflows['main'].model_copy(deep=True)
    document.workflows['auxiliary'].name = 'Auxiliary'
    document.workflowOrder.append('auxiliary')
    saveProject(path, document.model_dump(mode='json'))
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(path)
        assert set(owner.sessions) == {'main'}
        with grpc.insecure_channel(owner.address) as channel:
            remote = rpc.RuntimeServiceStub(channel)
            started = remote.StartJob(pb.StartJobRequest(project_id=owner.document.project.projectId,
                workflow_id='auxiliary', start_request_id=uuid4().hex,
                expected_runtime_instance_id=owner.runtime.runtimeInstanceId, legacy_snapshot_policy='NONE'), timeout=10)
            assert started.ok, started.message
            waitFor(lambda: owner.runtime.jobRepository.get(started.job_id).status == 'RUNNING')
            state = owner.status()
            assert not state['canLoad'] and state['workflows']['auxiliary']['jobId'] == started.job_id
            owner.stop('auxiliary', jobId=started.job_id)
            owner.load(path)
            state = owner.status()
            assert state['canLoad'] and state['jobId'] == '' and set(owner.sessions) == {'main'}
            assert [(row['workflowId'], row['jobId']) for row in state['runCatalog']] == [('auxiliary', started.job_id)]
    finally:
        owner.close()
