"""Designer lifetime and explicit Stop against a real independent Runtime RPC host."""
from types import SimpleNamespace
import threading

import grpc
import pytest

from emo_master.apps.designer.controllers.runtime_controller import RuntimeController
from emo_master.apps.designer.controllers.workflow_runtime_controller import WorkflowRuntimeController
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.services.runtime_worker import RuntimeWorker, _runWorker
from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState
from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from tests.designer.qt_wait import waitUntil
from tests.runtime.current_production_fixture import currentProductionProject


@pytest.fixture
def remoteRuntime(tmp_path, designerApplication):
    path, document = currentProductionProject(tmp_path / 'project', mode='continuous')
    owner = ProductionRuntime(tmp_path / 'runtime')
    client = None
    try:
        owner.load(path)
        channel = grpc.insecure_channel(owner.address)
        stub = rpc.RuntimeServiceStub(channel)
        stops = []
        original = stub.StopJob

        def stop(request, **kwargs):
            stops.append(pb.StopJobRequest.FromString(request.SerializeToString()))
            return original(request, **kwargs)

        stub.StopJob = stop
        client = RuntimeClient(stub, ownedChannel=channel, runtimeTarget=owner.address)
        yield SimpleNamespace(owner=owner, client=client, stops=stops, path=path, document=document)
    finally:
        if client is not None:
            client.close()
        owner.close()
        designerApplication.processEvents()


def controllerFor(client, path, *, manager=False):
    state = SimpleNamespace(job=None, logs=[])
    callbacks = dict(runtimeClient=client, runtimePanelState=RuntimePanelState(),
        appendLog=lambda *args: state.logs.append(args), refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None, syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda *_: None, setCurrentJobId=lambda job: setattr(state, 'job', job),
        setIsJobRunning=lambda *_: None, getLoadedProjectPath=lambda: str(path),
        getCurrentJobId=lambda: state.job, getEntryWorkflowId=lambda: 'main',
        getLegacySnapshotPolicy=lambda: 'NONE')
    if manager:
        controller = WorkflowRuntimeController(getRunTargets=lambda: ['main'], getWorkflows=lambda: ['main'],
            getMaxJobs=lambda: None, selectState=lambda *_: None, **callbacks)
    else:
        controller = RuntimeController(**callbacks)
    return controller, state


def startRunning(controller, state, owner):
    controller.startJob()
    waitUntil(lambda: state.job is not None, timeout=10)
    waitUntil(lambda: owner.runtime.jobRepository.get(state.job).status == 'RUNNING', timeout=10)


@pytest.mark.parametrize('manager', [False, True])
def testClosingRemoteDesignerDetachesWithoutStoppingProduction(remoteRuntime, manager):
    runtime = remoteRuntime
    controller, state = controllerFor(runtime.client, runtime.path, manager=manager)
    try:
        startRunning(controller, state, runtime.owner)
        assert runtime.client._activeStreams or controller._worker is not None
        controller.close()
        assert runtime.stops == []
        assert runtime.owner.runtime.jobRepository.get(state.job).status == 'RUNNING'
        assert not runtime.owner.runtime._closed
        assert runtime.client._closed
        assert not runtime.client._activeStreams
        assert controller._worker is None
    finally:
        controller.close()


def testExplicitDesignerStopUsesAcceptedRunIdentity(remoteRuntime):
    runtime = remoteRuntime
    controller, state = controllerFor(runtime.client, runtime.path)
    try:
        startRunning(controller, state, runtime.owner)
        expected = runtime.client.getRunState(runtime.owner.runtime.runtimeInstanceId,
            runtime.document.project.projectId, state.job).run
        controller.stopJob()
        waitUntil(lambda: controller._stopWorker is None and not controller._jobActive, timeout=10)
        assert runtime.owner.runtime.jobRepository.get(state.job).status == 'ABORTED'
        assert len(runtime.stops) == 1
        request = runtime.stops[0]
        assert request.job_id == state.job
        assert request.expected_runtime_instance_id == expected.runtime_instance_id
        assert request.expected_project_id == expected.project_id
        assert request.HasField('expected_run_group_id')
        assert request.expected_run_group_id == expected.run_group_id
    finally:
        controller.close()


def testDesignerStopDoesNotReplaceAcceptedIdentityWithCurrentInstance(remoteRuntime):
    runtime = remoteRuntime
    controller, state = controllerFor(runtime.client, runtime.path)
    originalInstance = runtime.owner.runtime.runtimeInstanceId
    try:
        startRunning(controller, state, runtime.owner)
        runtime.owner.runtime.runtimeInstanceId = 'replacement-runtime'
        controller.stopJob()
        waitUntil(lambda: controller._stopWorker is None, timeout=10)
        assert runtime.stops[0].expected_runtime_instance_id == originalInstance
        assert runtime.owner.runtime.jobRepository.get(state.job).status == 'RUNNING'
        assert any('RESET_REQUIRED' in message for _, message in state.logs)
    finally:
        runtime.owner.runtime.runtimeInstanceId = originalInstance
        controller.close()


def testStopIdentityReadCanRetryWithoutForgettingAcceptedJob(remoteRuntime, monkeypatch):
    runtime = remoteRuntime
    calls = []
    original = runtime.client.getStopIdentity

    def resolve(job, instance):
        calls.append((job, instance))
        if len(calls) == 1:
            raise TimeoutError('temporary read failure after accepted Start')
        return original(job, instance)

    monkeypatch.setattr(runtime.client, 'getStopIdentity', resolve)
    controller, state = controllerFor(runtime.client, runtime.path)
    try:
        startRunning(controller, state, runtime.owner)
        controller.stopJob()
        waitUntil(lambda: controller._stopWorker is None and not controller._jobActive, timeout=10)
        assert calls == [(state.job, runtime.owner.runtime.runtimeInstanceId)] * 2
        assert len(runtime.stops) == 1
        assert runtime.stops[0].expected_project_id == runtime.document.project.projectId
    finally:
        controller.close()


@pytest.mark.parametrize('explicitForce', [False, True])
def testRemoteCloseFinishesOnlyTheStopAlreadyRequested(explicitForce):
    release = threading.Event()
    modes = []

    class Client:
        ownsExecutionHost = False

        def stopJob(self, job, mode):
            modes.append(mode)
            assert release.wait(3)
            return SimpleNamespace(ok=True, status='STOPPING' if mode == 'graceful' else 'ABORTED')

    controller, state = controllerFor(Client(), 'project')
    state.job = 'selected-job'
    controller._jobActive = True
    controller.runtimePanelState.updateJob('RUNNING', '')
    try:
        controller.stopJob()
        if explicitForce:
            controller.stopJob('force')
        release.set()
        controller.close()
        assert modes == (['graceful', 'force'] if explicitForce else ['graceful'])
    finally:
        release.set()
        controller.close()


@pytest.mark.parametrize('explicitStop', [False, True])
def testDetachingPendingStartDoesNotInventStopIntent(explicitStop):
    stopped = []
    identity = dict(expectedRuntimeInstanceId='original-runtime', expectedProjectId='original-project',
                    expectedRunGroupId='original-group')

    class Client:
        def prepareStart(self, capture):
            return 'original-runtime'

        def startJob(self, projectId, **kwargs):
            # Close arrives while Start is executing and before its reply is delivered.
            if explicitStop:
                worker.requestStopAfterStart()
            worker.requestStop()
            return SimpleNamespace(ok=True, job_id='accepted-job', runtime_instance_id='original-runtime')

        def getStopIdentity(self, job, instance):
            assert (job, instance) == ('accepted-job', 'original-runtime')
            return identity

        def stopJob(self, job, **kwargs):
            stopped.append((job, kwargs))

        def getJobStatus(self, job):
            return SimpleNamespace(status='ABORTED' if explicitStop else 'RUNNING', message='')

    worker = RuntimeWorker(Client(), 'a/local/project.emoproj')
    _runWorker(worker)
    assert worker._jobId == 'accepted-job'
    assert stopped == ([('accepted-job', dict(mode='graceful', **identity))] if explicitStop else [])


@pytest.mark.parametrize('closeImmediately', [False, True])
def testExplicitStopBeforeQueuedAcceptedDeliveryStillStopsAcceptedJob(closeImmediately):
    following, released = threading.Event(), threading.Event()
    identity = dict(expectedRuntimeInstanceId='original-runtime', expectedProjectId='original-project',
                    expectedRunGroupId='original-group')
    stopped = []

    class Stream:
        def __iter__(self):
            return self

        def __next__(self):
            following.set()
            assert released.wait(3)
            raise StopIteration

        def cancel(self):
            released.set()

        close = cancel

    class Client:
        ownsExecutionHost = False

        def prepareStart(self, capture, **kwargs):
            return 'original-runtime'

        def startJob(self, project, **kwargs):
            return SimpleNamespace(ok=True, job_id='accepted-job', runtime_instance_id='original-runtime')

        def getStopIdentity(self, job, instance):
            assert (job, instance) == ('accepted-job', 'original-runtime')
            return identity

        def iterJobEvents(self, job, **kwargs):
            return Stream()

        def stopJob(self, job, **kwargs):
            stopped.append((job, kwargs))

        def getJobStatus(self, job):
            return SimpleNamespace(status='ABORTED' if stopped else 'RUNNING', message='')

    controller, state = controllerFor(Client(), 'project')
    try:
        controller.startJob()
        assert following.wait(3)  # Deliberately leave Qt jobAccepted queued.
        assert state.job is None
        controller.stopJob()
        if closeImmediately:
            controller.close()
        else:
            waitUntil(lambda: controller._worker is None and not controller._jobActive)
        assert stopped == [('accepted-job', dict(mode='graceful', **identity))]
    finally:
        released.set()
        controller.close()


def testClosingDesignerStillCleansUpItsOwnedExecutionHost(remoteRuntime):
    runtime = remoteRuntime
    client = RuntimeClient(runtime.owner.runtime, ownedRuntimeService=runtime.owner.runtime)
    controller, state = controllerFor(client, runtime.path)
    try:
        startRunning(controller, state, runtime.owner)
        controller.close()
        assert runtime.owner.runtime._closed
        assert runtime.owner.runtime.jobRepository.get(state.job).status == 'ABORTED'
        assert client._closed
    finally:
        controller.close()


def testReadOnlyEndpointUsesExistingHostWithoutStartingOrPublishing(remoteRuntime):
    runtime = remoteRuntime
    assert runtime.client.ensureReadOnlyEndpoint() is runtime.client.connectionProfile
    client = RuntimeClient(runtime.owner.runtime)
    revision = runtime.owner.runtime.projectConfiguration.current['documentRevision']
    try:
        profile = client.ensureReadOnlyEndpoint()
        assert client.ensureReadOnlyEndpoint() is profile
        with grpc.insecure_channel(profile.target) as channel:
            info = rpc.RuntimeServiceStub(channel).GetRuntimeInfo(pb.RuntimeInfoRequest(), timeout=5)
        assert info.ok and info.runtime_instance_id == runtime.owner.runtime.runtimeInstanceId
        assert info.document_revision == revision
        assert runtime.owner.runtime.jobRepository.all() == []
        assert not client.ownsExecutionHost
    finally:
        client.close()
    assert not runtime.owner.runtime._closed


@pytest.mark.parametrize('operation', ['switch', 'close'])
def testReadOnlyEndpointCreationCoordinatesWithConnectionLifetime(monkeypatch, operation):
    from emo_master.apps.designer.services import runtime_client
    from emo_master.apps.runtime.grpc_server import aio_entry
    from emo_master.apps.runtime.grpc_server.service import RuntimeService

    entered, release, changing, finished = (threading.Event() for _ in range(4))
    closed, errors, profiles = [], [], []
    service = RuntimeService.__new__(RuntimeService)
    service._previewJobLock = threading.RLock()
    service._presentationOwner = object()

    class Server:
        port = 55111

        def __init__(self, *_):
            entered.set()
            assert release.wait(3)

        def close(self):
            closed.append('server')

    monkeypatch.setattr(aio_entry, 'AioRuntimeServer', Server)
    monkeypatch.setattr(runtime_client, 'createChannel', lambda _: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(rpc, 'DisplayServiceStub', lambda _: object())
    client = RuntimeClient(service)
    candidate = RuntimeClient(object(), runtimeTarget='127.0.0.1:55222')
    candidateService = candidate.runtimeService

    def expose():
        try:
            profiles.append(client.ensureReadOnlyEndpoint())
        except BaseException as error:
            errors.append(error)

    def change():
        changing.set()
        try:
            if operation == 'switch':
                client.switchConnection(candidate)
            else:
                client.close()
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    reader, changer = threading.Thread(target=expose), threading.Thread(target=change)
    reader.start()
    try:
        assert entered.wait(3)
        changer.start()
        assert changing.wait(3)
        assert not finished.is_set()
        release.set()
        reader.join(3)
        changer.join(3)
        assert not reader.is_alive() and not changer.is_alive()
        assert errors == []
        assert profiles[0].target == '127.0.0.1:55111'
        if operation == 'switch':
            assert client.runtimeService is candidateService
            assert client.connectionProfile.target == '127.0.0.1:55222'
            assert client._presentationServer is None
        else:
            assert client._closed and client._presentationServer is None
    finally:
        release.set()
        reader.join(3)
        if changer.ident is not None:
            changer.join(3)
        client.close()
        candidate.close()
    assert closed == ['server']
