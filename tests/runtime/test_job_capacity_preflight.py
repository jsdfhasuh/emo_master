"""Capacity rejection reports the whole group without disturbing existing Jobs."""
import json

import pytest

from examples.dispatch_queue_demo import saveDemo
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.core.contracts.device_driver import DeviceError
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_production_outcomes import waitFor


def capacityProject(tmp_path, *, consumers, limit):
    store = WorkflowStore()
    if consumers:
        store.addWorkflow('Consumer', workflowId='consumer')
    payload = store.toPayload('Capacity preflight')
    payload['runtime']['maxConcurrentJobs'] = limit
    if consumers:
        payload['workflows']['main']['nodes'].insert(1, dict(nodeId='dispatch', kind='dispatch',
            params=dict(targets=[dict(targetSlotId=f'slot-{index}', workflowId='consumer',
                                     inputs={}, queueCapacity=4) for index in range(consumers)])))
    document = ProjectDocument.model_validate(payload)
    path = tmp_path / 'capacity.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    assert loadProjectDocument(path) == document
    return path


@pytest.mark.parametrize('consumers,active,limit', [(0, 2, 2), (1, 0, 1), (2, 0, 2), (1, 2, 2)])
def testCapacityRejectionReportsCountsBeforeDevicePreflight(tmp_path, monkeypatch, consumers, active, limit):
    path = capacityProject(tmp_path, consumers=consumers, limit=limit)
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        validated = service.ValidateProject(pb.ValidateProjectRequest(project_id=service.loadedProjectId), None)
        assert validated.ok, validated.errors
        owned = {f'existing-{index}' for index in range(active)}
        for jobId in sorted(owned):
            service.jobRepository.create(JobRecord(jobId, service.loadedProjectId, 1, 'main', status='RUNNING'))
        before = service.jobRepository.all()
        with monkeypatch.context() as patch:
            patch.setattr(service.jobSupervisor, '_ownedJobs', lambda: owned)
            patch.setattr(service.deviceManager, 'preflight', lambda *_: pytest.fail('capacity must reject first'))
            reply = service.StartJob(pb.StartJobRequest(workflow_id='main', start_request_id='rejected',
                expected_runtime_instance_id=service.runtimeInstanceId), None)
        assert not reply.ok and reply.status == 'REJECTED' and not reply.job_id
        assert reply.message.startswith('E_MAX_CONCURRENT_JOBS:')
        assert 'E_TCP_PREFLIGHT' not in reply.message
        assert f'required={1 + consumers}' in reply.message and f'active={active}' in reply.message
        assert f'limit={limit}' in reply.message
        diagnostic = json.loads(reply.diagnostics_json)
        assert diagnostic == dict(code='E_MAX_CONCURRENT_JOBS', capacity=dict(
            requiredJobs=1 + consumers, consumerJobs=consumers, activeJobs=active,
            maxConcurrentJobs=limit, availableJobs=max(0, limit - active)))
        lookup = service.GetStartRequest(pb.StartRequestLookup(
            runtime_instance_id=service.runtimeInstanceId, start_request_id='rejected'), None)
        assert lookup == reply
        assert service.jobRepository.all() == before
        assert not service.jobSupervisor._handles and not service.deviceManager.workers
    finally:
        service.close()


@pytest.mark.parametrize('active,limit', [(0, 3), (8, None)])
def testAvailableCapacityPreservesDevicePreflightError(tmp_path, monkeypatch, active, limit):
    path = capacityProject(tmp_path, consumers=2, limit=limit)
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok

        def preflight(*_args):
            raise DeviceError('E_DEVICE_NOT_CONNECTED', 'fixture device is disconnected')

        with monkeypatch.context() as patch:
            patch.setattr(service.jobSupervisor, '_ownedJobs', lambda: {str(index) for index in range(active)})
            patch.setattr(service.deviceManager, 'preflight', preflight)
            reply = service.StartJob(pb.StartJobRequest(workflow_id='main'), None)
        assert reply.status == 'REJECTED' and reply.message.startswith('E_DEVICE_NOT_CONNECTED:')
        assert 'MAX_CONCURRENT' not in reply.message and not reply.diagnostics_json
        assert not service.jobRepository.all() and not service.deviceManager.workers
    finally:
        service.close()


def testRejectedSecondRunLeavesExistingGroupRunningAndCanRetryAfterStop(tmp_path):
    path = saveDemo(tmp_path / 'project')
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        started = service.StartJob(pb.StartJobRequest(workflow_id='main'), None)
        assert started.ok, started.message
        group = service.deviceManager.dispatchGroups[started.job_id]
        waitFor(lambda: all(service.jobRepository.get(key).status == 'RUNNING' for key in group.members))
        rejected = service.StartJob(pb.StartJobRequest(workflow_id='main'), None)
        assert rejected.status == 'REJECTED' and not rejected.job_id
        assert rejected.message.startswith('E_MAX_CONCURRENT_JOBS:')
        assert json.loads(rejected.diagnostics_json)['capacity'] == dict(
            requiredJobs=2, consumerJobs=1, activeJobs=2, maxConcurrentJobs=2, availableJobs=0)
        assert len(service.jobRepository.all()) == 2
        assert all(service.jobRepository.get(key).status == 'RUNNING' for key in group.members)
        assert not group.stopping
        assert service.StopJob(pb.StopJobRequest(job_id=started.job_id), None).ok
        waitFor(lambda: not service.jobSupervisor._ownedJobs(), timeout=15)
        restarted = service.StartJob(pb.StartJobRequest(workflow_id='main'), None)
        assert restarted.ok, restarted.message
        assert restarted.job_id != started.job_id
        assert service.StopJob(pb.StopJobRequest(job_id=restarted.job_id), None).ok
        waitFor(lambda: not service.jobSupervisor._ownedJobs(), timeout=15)
    finally:
        service.close()
