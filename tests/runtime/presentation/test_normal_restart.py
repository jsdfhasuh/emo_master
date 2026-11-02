"""A normal remote Start can follow Stop while final display messages drain."""
from threading import Event
from uuid import uuid4

import grpc
import pytest

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from tests.runtime.current_production_fixture import currentProductionProject
from tests.runtime.production_fixture import waitFor


@pytest.fixture
def captureRuntime(tmp_path):
    path, document = currentProductionProject(tmp_path / 'project', mode='continuous')
    document.runtime.maxConcurrentJobs = 1
    saveProject(path, document.model_dump(mode='json'))
    owner = ProductionRuntime(tmp_path / 'data')
    try:
        owner.load(path)
        with grpc.insecure_channel(owner.address) as channel:
            yield owner, rpc.RuntimeServiceStub(channel)
    finally:
        owner.close()


def startRequest(owner):
    return pb.StartJobRequest(project_id=owner.document.project.projectId, workflow_id='main',
        capture_presentation=True, legacy_snapshot_policy='NONE', start_request_id=uuid4().hex,
        expected_runtime_instance_id=owner.runtime.runtimeInstanceId)


def testRunningCaptureQuotaRejectsWithoutWaiting(captureRuntime, monkeypatch):
    owner, stub = captureRuntime
    first = stub.StartJob(startRequest(owner), timeout=10)
    assert first.ok, first.message
    waitFor(lambda: owner.presentation.store.snapshot(first.job_id)['results'])

    def unexpectedWait(*args, **kwargs):
        pytest.fail('running Jobs must not wait for display retirement')
    monkeypatch.setattr(owner.presentation, 'waitForLogDrain', unexpectedWait)
    rejected = stub.StartJob(startRequest(owner), timeout=10)
    assert not rejected.ok and rejected.status == 'REJECTED' and not rejected.job_id
    assert 'quota' in rejected.message
    assert len(owner.runtime.jobRepository.all()) == 1
    assert owner.runtime.jobRepository.get(first.job_id).status == 'RUNNING'


@pytest.mark.parametrize('cancel', [False, True], ids=['restart', 'cancel-wait'])
def testRemoteRestartWaitsForStoppedDisplayRetirement(captureRuntime, monkeypatch, cancel):
    owner, stub = captureRuntime
    display = owner.presentation
    first = stub.StartJob(startRequest(owner), timeout=10)
    assert first.ok, first.message
    waitFor(lambda: display.store.snapshot(first.job_id)['results'])
    readerAtFence, releaseReader, admissionTried, cancellationSeen = Event(), Event(), Event(), Event()
    originalFence, originalRelease = display._fence, display.release
    originalWait = display.waitForLogDrain

    def gatedFence(jobId, terminal):
        if jobId == first.job_id:
            readerAtFence.set()
            assert releaseReader.wait(10), 'test did not release the final display reader'
        originalFence(jobId, terminal)

    def observedRelease(jobId):
        try:
            return originalRelease(jobId)
        except ValueError:
            if jobId == first.job_id:
                admissionTried.set()
            raise

    def observedWait(jobId, *, timeoutSeconds, isActive):
        def active():
            result = isActive()
            if not result:
                cancellationSeen.set()
            return result
        return originalWait(jobId, timeoutSeconds=timeoutSeconds, isActive=active)

    monkeypatch.setattr(display, '_fence', gatedFence)
    monkeypatch.setattr(display, 'release', observedRelease)
    monkeypatch.setattr(display, 'waitForLogDrain', observedWait)
    try:
        owner.stop(jobId=first.job_id)
        assert owner.status()['canLoad']
        assert not owner.runtime.jobSupervisor.ownsJobResources(first.job_id)
        assert readerAtFence.wait(5) and display.readers[first.job_id].is_alive()
        request = startRequest(owner)
        future = stub.StartJob.future(request, timeout=10)
        assert admissionTried.wait(5)
        if cancel:
            assert future.cancel()
            with pytest.raises(grpc.FutureCancelledError):
                future.result()
            assert cancellationSeen.wait(5)
        releaseReader.set()
        if cancel:
            reply = stub.GetStartRequest(pb.StartRequestLookup(
                runtime_instance_id=owner.runtime.runtimeInstanceId,
                start_request_id=request.start_request_id), timeout=10)
            assert not reply.ok and not reply.job_id and reply.status == 'REJECTED'
            assert 'E_CANCELLED' in reply.message
            assert len(owner.runtime.jobRepository.all()) == 1
        else:
            reply = future.result(timeout=10)
            assert reply.ok, reply.message
            assert reply.job_id != first.job_id
            assert first.job_id not in display.jobs and first.job_id not in display.readers
            assert len(owner.runtime.jobRepository.all()) == 2
            # Reconcile the same request without creating another execution.
            assert stub.StartJob(request, timeout=10).job_id == reply.job_id
            assert len(owner.runtime.jobRepository.all()) == 2
    finally:
        releaseReader.set()
