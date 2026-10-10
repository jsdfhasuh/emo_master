"""Bounded read-only Job status, identity fences and legacy compatibility."""
from collections import Counter
from dataclasses import FrozenInstanceError
import threading
import time
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.presentation.rpc import DisplayRpc
from emo_master.clients.runtime import display_session as module
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.clients.runtime.view_state import SessionView


def until(check, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(.005)
    raise AssertionError('bounded status observation deadline')


class RpcFailure(grpc.RpcError):
    def __init__(self, code, detail):
        self._code, self._detail = code, detail

    def code(self):
        return self._code

    def details(self):
        return self._detail


class Context:
    def abort(self, code, detail):
        raise RpcFailure(code, detail)


def serverAdapter():
    persistence = SimpleNamespace(getJob=lambda job: {'jobId': job, 'projectId': 'project', 'status': 'COMPLETED'})
    repository = JobRepository(persistence)
    repository.create(JobRecord('current', 'project', 1, 'workflow', status='RUNNING'))
    # Listing or consulting persisted history is forbidden for this exact read.
    repository.all = lambda: pytest.fail('status traversed the Job catalog')
    owned = {'current'}
    runtime = SimpleNamespace(jobRepository=repository,
        jobSupervisor=SimpleNamespace(ownsJobResources=lambda job: job in owned))
    service = SimpleNamespace(runtime=runtime, runtimeInstanceId='runtime', lock=threading.RLock(), jobs={})
    return DisplayRpc(service), repository, owned


def request(**changes):
    return pb.DisplayJobRequest(**{'runtime_instance_id': 'runtime', 'project_id': 'project',
                                  'job_id': 'current', **changes})


def testExactStatusUsesCopiedCurrentJobWithoutCatalogOrHistoricalFallback():
    adapter, repository, owned = serverAdapter()
    reply = adapter.GetJob(request(), Context())
    assert (reply.runtime_instance_id, reply.project_id, reply.job_id, reply.status) == (
        'runtime', 'project', 'current', 'RUNNING')
    assert not reply.capture_enabled and not reply.resources_released
    assert not reply.sources_json and not reply.capture_definition_json
    copied = repository.getCurrentSnapshot('current')
    repository.update('current', status='COMPLETED')
    assert copied.status == 'RUNNING'
    assert adapter.GetJob(request(), Context()).status == 'COMPLETED'
    assert not adapter.GetJob(request(), Context()).resources_released
    owned.clear()
    assert adapter.GetJob(request(), Context()).resources_released
    assert repository.get('historical').status == 'COMPLETED'  # persistence positive control
    with pytest.raises(RpcFailure) as error:
        adapter.GetJob(request(job_id='historical'), Context())
    assert error.value.code() == grpc.StatusCode.NOT_FOUND


@pytest.mark.parametrize(('changes', 'code', 'detail'), [
    ({'project_id': ''}, grpc.StatusCode.INVALID_ARGUMENT, 'JOB_IDENTITY_REQUIRED'),
    ({'runtime_instance_id': ''}, grpc.StatusCode.INVALID_ARGUMENT, 'JOB_IDENTITY_REQUIRED'),
    ({'job_id': ''}, grpc.StatusCode.INVALID_ARGUMENT, 'JOB_IDENTITY_REQUIRED'),
    ({'runtime_instance_id': 'previous'}, grpc.StatusCode.FAILED_PRECONDITION, 'RESET_REQUIRED'),
    ({'project_id': 'other'}, grpc.StatusCode.FAILED_PRECONDITION, 'PROJECT_MISMATCH'),
    ({'job_id': 'missing'}, grpc.StatusCode.NOT_FOUND, 'JOB_NOT_FOUND'),
])
def testExactStatusRejectsWrongIdentity(changes, code, detail):
    adapter, _repository, _owned = serverAdapter()
    with pytest.raises(RpcFailure) as error:
        adapter.GetJob(request(**changes), Context())
    assert error.value.code() == code and error.value.details() == detail


def testCapturedStatusDoesNotInterpretTerminalAsRelease():
    adapter, repository, owned = serverAdapter()
    adapter.service.jobs['current'] = {'capture': {'sources': ['one']}, 'identity': {'mode': 'debug'}}
    repository.update('current', status='FAILED')
    owned.clear()
    reply = adapter.GetJob(request(), Context())
    assert reply.status == 'FAILED' and reply.mode == 'debug'
    assert reply.capture_enabled and not reply.resources_released


class Stream:
    def __init__(self):
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def __iter__(self):
        while not self.cancelled.wait(.01):
            pass
        raise RpcFailure(grpc.StatusCode.CANCELLED, 'cancelled')
        yield  # make a cancellable generator, without result traffic


class Stub:
    def __init__(self):
        self.instance = 'runtime'
        self.supported = True
        self.status = 'RUNNING'
        self.capture = True
        self.released = False
        self.counts = Counter()
        self.getAction = None
        self.snapshotAction = None
        self.capabilitiesAction = None
        self.streams = []

    def Capabilities(self, request, timeout):
        self.counts['capabilities'] += 1
        if self.capabilitiesAction:
            return self.capabilitiesAction(request)
        capabilities = ['snapshot', 'subscribe', 'asset_id']
        if self.supported:
            capabilities.append('job_status_v1')
        return pb.DisplayCapabilities(runtime_instance_id=self.instance, protocol_version='1.0', capabilities=capabilities)

    def GetJob(self, request, timeout):
        self.counts['get'] += 1
        if self.getAction:
            return self.getAction(request)
        return self.reply(request.job_id)

    def reply(self, job='current', **changes):
        return pb.DisplayJob(**{'runtime_instance_id': self.instance, 'project_id': 'project',
            'job_id': job, 'status': self.status, 'mode': 'runtime', 'capture_enabled': self.capture,
            'resources_released': self.released, **changes})

    def Snapshot(self, request, timeout):
        self.counts['snapshot'] += 1
        if self.snapshotAction:
            return self.snapshotAction(request)
        return pb.DisplaySnapshot(runtime_instance_id=self.instance, job_id=request.job_id)

    def Subscribe(self, request):
        self.counts['subscribe'] += 1
        stream = Stream()
        self.streams.append(stream)
        return stream


@pytest.fixture
def observer(monkeypatch):
    stub = Stub()
    sessions = []
    monkeypatch.setattr(module.grpc, 'insecure_channel', lambda *a, **k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module.rpc, 'DisplayServiceStub', lambda channel: stub)
    def create(*, pinned=True, **options):
        identity = dict(expectedRuntimeInstanceId='runtime', projectId='project') if pinned else {}
        session = DisplaySession('loopback', 'current', **identity, **options)
        sessions.append(session)
        return session
    yield stub, create
    for session in sessions:
        session.close()
    assert all(not thread.is_alive() for session in sessions for thread in session.threads)


def fresh(session, status):
    job = session.readSnapshot().job
    return job if job and job.availability == 'AVAILABLE' and job.status == status else None


def testLiveStatusChangesWithoutResultsAndKeepsExistingWorkerBudgets(observer):
    stub, create = observer
    session = create()
    workers = tuple(session.threads)
    for status in ('RUNNING', 'STOPPING', 'ABORTED'):
        stub.status = status
        value = until(lambda: fresh(session, status))
        assert value.jobId == 'current' and value.projectId == 'project'
    assert session.readSnapshot().connection == 'CONNECTED'
    assert not session.readSnapshot().scopes
    assert tuple(session.threads) == workers and len(workers) == 3 and session.pending.maxsize == 8
    with pytest.raises(FrozenInstanceError):
        value.status = 'RUNNING'
    assert SessionView(0, 0, '', '', '', '', {}, {}, {}).job is None


@pytest.mark.parametrize('released', [False, True])
def testStatusOnlyNeverReadsResultsEvenIfServerLaterClaimsCapture(observer, released):
    stub, create = observer
    stub.capture, stub.released = not released, released
    session = create(readResults=False)
    until(lambda: fresh(session, 'RUNNING'))
    stub.capture, stub.released, stub.status = True, False, 'COMPLETED'
    until(lambda: fresh(session, 'COMPLETED'))
    assert stub.counts['get'] >= 2
    assert not stub.counts['snapshot'] and not stub.counts['subscribe']
    assert session.readSnapshot().connection == 'CONNECTED' and not session.readSnapshot().scopes


def testAuthorityCanDisableResultsOnReleaseWithoutStoppingStatus(observer):
    stub, create = observer
    session = create()
    until(lambda: fresh(session, 'RUNNING') and stub.counts['subscribe'])
    stub.status, stub.capture, stub.released = 'COMPLETED', False, True
    until(lambda: fresh(session, 'COMPLETED') and session.readSnapshot().job.resourcesReleased)
    until(lambda: all(stream.cancelled.is_set() for stream in stub.streams))
    counts = stub.counts.copy()
    until(lambda: stub.counts['get'] > counts['get'])
    assert stub.counts['snapshot'] == counts['snapshot'] and stub.counts['subscribe'] == counts['subscribe']
    assert not session.readSnapshot().scopes


def testStatusFailureClearsRunningWhileSnapshotSuccessCannotConcealIt(observer):
    stub, create = observer
    session = create()
    until(lambda: fresh(session, 'RUNNING'))
    def unavailable(request):
        raise RpcFailure(grpc.StatusCode.UNAVAILABLE, 'status unavailable')
    stub.getAction = unavailable
    until(lambda: session.readSnapshot().job.availability == 'UNAVAILABLE')
    before = stub.counts['snapshot']
    until(lambda: stub.counts['snapshot'] > before)
    view = session.readSnapshot()
    assert view.job.status == '' and view.job.availability == 'UNAVAILABLE'
    assert view.connection == 'CONNECTED'
    stub.getAction = None
    until(lambda: fresh(session, 'RUNNING'))


@pytest.mark.parametrize('oldServer', ['capability', 'unimplemented'])
def testOldServerKeepsCaptureViewingWithUnavailableStatus(observer, oldServer):
    stub, create = observer
    if oldServer == 'capability':
        stub.supported = False
    else:
        def unimplemented(request):
            raise RpcFailure(grpc.StatusCode.UNIMPLEMENTED, 'old server')
        stub.getAction = unimplemented
    session = create()
    until(lambda: stub.counts['subscribe'] and session.readSnapshot().job.availability == 'UNAVAILABLE')
    assert session.readSnapshot().job.status == '' and session.readSnapshot().connection == 'CONNECTED'
    before = stub.counts['get']
    target = stub.counts['snapshot'] + 1
    until(lambda: stub.counts['snapshot'] >= target)
    assert stub.counts['get'] == before == (0 if oldServer == 'capability' else 1)


@pytest.mark.parametrize('failure', [False, True])
def testLateStatusCannotOverwriteNewSelectionIncludingSameJobId(observer, failure):
    stub, create = observer
    entered, release = threading.Event(), threading.Event()
    calls = [0]
    def delayed(request):
        calls[0] += 1
        if calls[0] == 1:
            entered.set()
            assert release.wait(3)
            if failure:
                raise RpcFailure(grpc.StatusCode.NOT_FOUND, 'old selection missing')
            return stub.reply(request.job_id, status='FAILED')
        return stub.reply(request.job_id, status='COMPLETED')
    stub.getAction = delayed
    session = create(readResults=False)
    try:
        assert entered.wait(2)
        session.selectJob('other', readResults=False)
        session.selectJob('current', readResults=False)
        release.set()
        value = until(lambda: fresh(session, 'COMPLETED'))
        assert value.jobId == 'current'
        assert not any(item[0] == 'NOT_FOUND' for item in session.errors)
    finally:
        release.set()


@pytest.mark.parametrize('part', ['status', 'capabilities', 'snapshot'])
def testRuntimeMismatchNeverMigratesPinnedObserver(observer, part):
    stub, create = observer
    session = create()
    until(lambda: fresh(session, 'RUNNING') and stub.counts['subscribe'])
    if part == 'status':
        stub.getAction = lambda request: stub.reply(request.job_id, runtime_instance_id='replacement')
    elif part == 'snapshot':
        stub.snapshotAction = lambda request: pb.DisplaySnapshot(runtime_instance_id='replacement', job_id=request.job_id)
    else:
        stub.instance = 'replacement'
        stub.streams[-1].cancel()
    until(lambda: session.readSnapshot().job.availability == 'RESET_REQUIRED')
    view = session.readSnapshot()
    assert view.runtimeInstanceId == 'runtime' and view.job.runtimeInstanceId == 'runtime'
    assert view.job.status == '' and view.connection == 'RESET_REQUIRED' and not view.scopes
    with session.lock:
        token = session._token()
    session._acceptJob(stub.reply(status='RUNNING', runtime_instance_id='runtime'), token)
    session._accept(pb.DisplaySnapshot(runtime_instance_id='runtime', job_id='current'))
    assert session.readSnapshot().job.availability == 'RESET_REQUIRED'


def testLateSuccessAfterCloseCannotRestoreState(observer):
    stub, create = observer
    session = create(readResults=False)
    until(lambda: fresh(session, 'RUNNING'))
    with session.lock:
        token = session._token()
    session.close()
    session._acceptJob(stub.reply(), token)
    session._accept(pb.DisplaySnapshot(runtime_instance_id='runtime', job_id='current'))
    assert session.readSnapshot().connection == 'CLOSED'
    assert session.readSnapshot().job.status == '' and not session.readSnapshot().scopes
    with pytest.raises(ValueError, match='closed'):
        session.selectJob('other')


@pytest.mark.parametrize('oldServer', ['capability', 'unimplemented'])
def testStatusOnlyOldServerStillChecksConnectionAndRuntimeIdentity(observer, oldServer):
    stub, create = observer
    if oldServer == 'capability':
        stub.supported = False
    else:
        def unimplemented(request):
            raise RpcFailure(grpc.StatusCode.UNIMPLEMENTED, 'old server')
        stub.getAction = unimplemented
    session = create(readResults=False)
    until(lambda: session.readSnapshot().job.availability == 'UNAVAILABLE')
    def disconnected(request):
        raise RpcFailure(grpc.StatusCode.UNAVAILABLE, 'offline')
    stub.capabilitiesAction = disconnected
    until(lambda: session.readSnapshot().connection == 'UNAVAILABLE')
    assert session.readSnapshot().job.status == ''
    stub.instance = 'replacement'
    stub.capabilitiesAction = None
    until(lambda: session.readSnapshot().connection == 'RESET_REQUIRED')
    assert not stub.counts['snapshot'] and not stub.counts['subscribe']


def testLegacyUnpinnedObserverRetainsRuntimeRestartCompatibility(observer):
    stub, create = observer
    session = create(pinned=False)
    until(lambda: session.readSnapshot().connection == 'CONNECTED' and stub.counts['subscribe'])
    old = session.readSnapshot()
    stub.instance = 'replacement'
    stub.streams[-1].cancel()
    until(lambda: session.readSnapshot().runtimeInstanceId == 'replacement'
          and session.readSnapshot().connection == 'CONNECTED')
    assert session.readSnapshot().generation > old.generation
    assert session.readSnapshot().job is None and not stub.counts['get']


@pytest.mark.parametrize('failure', [False, True])
def testLateCapabilitiesCannotPublishAcrossSelection(observer, failure):
    stub, create = observer
    entered, release = threading.Event(), threading.Event()
    calls = [0]
    def delayed(request):
        calls[0] += 1
        if calls[0] == 1:
            entered.set()
            assert release.wait(3)
            if failure:
                raise RpcFailure(grpc.StatusCode.UNAVAILABLE, 'obsolete endpoint response')
            return pb.DisplayCapabilities(runtime_instance_id='obsolete', protocol_version='wrong')
        return pb.DisplayCapabilities(runtime_instance_id='runtime', protocol_version='1.0',
            capabilities=['snapshot', 'subscribe', 'asset_id', 'job_status_v1'])
    stub.capabilitiesAction = delayed
    session = create(readResults=False)
    try:
        assert entered.wait(2)
        session.selectJob('selected', readResults=False)
        release.set()
        until(lambda: fresh(session, 'RUNNING'))
        assert session.readSnapshot().job.jobId == 'selected'
        assert session.readSnapshot().connection == 'CONNECTED'
        assert not session.errors
    finally:
        release.set()


def testRealNormalUncapturedJobStatusThroughBoundedRpc(channel, tmp_path, monkeypatch):
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from tests.runtime.presentation.test_normal_capture import load, request as startRequest
    from tests.runtime.runtime_test_utils import waitForTerminal
    _root, document = load(channel.runtime, tmp_path)
    started = channel.runtime.StartJob(startRequest(channel.runtime, capture=False), None)
    assert started.ok
    assert waitForTerminal(channel.runtime, started.job_id).status == 'COMPLETED'
    def forbidden(*args, **kwargs):
        pytest.fail('observation invoked execution or capture access')
    monkeypatch.setattr(channel.runtime, 'StartJob', forbidden)
    monkeypatch.setattr(channel.runtime, 'StopJob', forbidden)
    monkeypatch.setattr(channel, 'start', forbidden)
    monkeypatch.setattr(channel, 'release', forbidden)
    monkeypatch.setattr(DisplayRpc, 'Snapshot', forbidden)
    monkeypatch.setattr(DisplayRpc, 'Subscribe', forbidden)
    server = AioRuntimeServer(channel.runtime, channel)
    session = None
    try:
        session = DisplaySession(f'127.0.0.1:{server.port}', started.job_id,
            expectedRuntimeInstanceId=channel.runtimeInstanceId, projectId=document.project.projectId,
            readResults=False)
        value = until(lambda: fresh(session, 'COMPLETED'))
        assert value.captureEnabled is False and value.resourcesReleased is True
        assert session.readSnapshot().connection == 'CONNECTED'
        assert len(channel.runtime.jobRepository.all()) == 1
        assert channel.runtime.sqliteStore.getGlobalCounter(document.project.projectId, 'jobs').value == 1
        assert server.peak['control'] <= 4 and server.peak['display'] == 0
    finally:
        if session is not None:
            session.close()
        server.close()


def testRealCapturedTerminalStatusSurvivesOwnerRelease(channel, tmp_path, monkeypatch):
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from tests.runtime.presentation.test_normal_capture import load, release, request as startRequest
    from tests.runtime.runtime_test_utils import waitForTerminal
    _root, document = load(channel.runtime, tmp_path)
    started = channel.runtime.StartJob(startRequest(channel.runtime), None)
    assert started.ok
    assert waitForTerminal(channel.runtime, started.job_id).status == 'COMPLETED'
    def forbidden(*args, **kwargs):
        pytest.fail('observer invoked execution')
    monkeypatch.setattr(channel.runtime, 'StartJob', forbidden)
    monkeypatch.setattr(channel.runtime, 'StopJob', forbidden)
    monkeypatch.setattr(channel, 'start', forbidden)
    server = AioRuntimeServer(channel.runtime, channel)
    session = None
    try:
        session = DisplaySession(f'127.0.0.1:{server.port}', started.job_id,
            expectedRuntimeInstanceId=channel.runtimeInstanceId, projectId=document.project.projectId)
        until(lambda: fresh(session, 'COMPLETED') and session.readSnapshot().scopes)
        before = session.readSnapshot()
        assert before.job.captureEnabled and not before.job.resourcesReleased
        release(channel, started.job_id)  # explicit test owner, never the observer
        monkeypatch.setattr(channel, 'release', forbidden)
        until(lambda: fresh(session, 'COMPLETED') and session.readSnapshot().job.resourcesReleased)
        after = session.readSnapshot()
        assert not after.scopes and not after.loading and after.generation > before.generation
        assert after.connection == 'CONNECTED'
        assert len(channel.runtime.jobRepository.all()) == 1
        assert channel.runtime.sqliteStore.getGlobalCounter(document.project.projectId, 'jobs').value == 1
    finally:
        if session is not None:
            session.close()
        server.close()


@pytest.mark.parametrize('failure', [False, True])
def testReconnectFencesInFlightStatusSuccessAndFailure(observer, failure):
    stub, create = observer
    session = create()
    until(lambda: fresh(session, 'RUNNING') and stub.counts['subscribe'])
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    calls, stalePublications = [0], []
    originalAccept, originalUnavailable = session._acceptJob, session._jobUnavailable
    def inspect(reply, token):
        originalAccept(reply, token)
        if reply.status == 'FAILED':
            stalePublications.append(session.readSnapshot().job.status == 'FAILED')
            applied.set()
    def inspectError(state, detail):
        if detail == 'obsolete status failure':
            stalePublications.append(True)
        originalUnavailable(state, detail)
    session._acceptJob, session._jobUnavailable = inspect, inspectError
    def delayed(request):
        calls[0] += 1
        if calls[0] == 1:
            entered.set()
            assert release.wait(3)
            if failure:
                raise RpcFailure(grpc.StatusCode.NOT_FOUND, 'obsolete status failure')
            return stub.reply(request.job_id, status='FAILED')
        applied.set()
        return stub.reply(request.job_id, status='COMPLETED')
    stub.getAction = delayed
    try:
        assert entered.wait(2)
        epoch = session._connectionEpoch
        stub.streams[-1].cancel()
        until(lambda: session._connectionEpoch > epoch)
        release.set()
        assert applied.wait(2)
        assert not any(stalePublications)
        until(lambda: fresh(session, 'COMPLETED'))
    finally:
        release.set()


def testExplicitNewRuntimeSelectionResetsUnimplementedCapabilityCache(observer):
    stub, create = observer
    def capabilityByInstance(request):
        if request.runtime_instance_id == 'runtime':
            raise RpcFailure(grpc.StatusCode.UNIMPLEMENTED, 'old runtime')
        return stub.reply(request.job_id)
    stub.getAction = capabilityByInstance
    session = create(readResults=False)
    until(lambda: session.readSnapshot().job.availability == 'UNAVAILABLE')
    assert session._statusUnimplemented
    stub.instance = 'replacement'
    session.selectJob('current', expectedRuntimeInstanceId='replacement', projectId='project', readResults=False)
    value = until(lambda: fresh(session, 'RUNNING'))
    assert value.runtimeInstanceId == 'replacement' and not session._statusUnimplemented


@pytest.mark.parametrize('changes', [{'project_id': 'other'}, {'job_id': 'other'}, {'status': 'MAYBE'}])
def testMalformedStatusCannotKeepRunning(observer, changes):
    stub, create = observer
    session = create(readResults=False)
    until(lambda: fresh(session, 'RUNNING'))
    stub.getAction = lambda request: stub.reply(**changes)
    until(lambda: session.readSnapshot().job.availability == 'INVALID_STATUS')
    assert session.readSnapshot().job.status == ''
    assert not session.readSnapshot().scopes


def testUnchangedStatusDoesNotPublishEveryPollingTimestamp(observer):
    stub, create = observer
    session = create(readResults=False)
    until(lambda: fresh(session, 'RUNNING'))
    revision, calls = session.readSnapshot().revision, stub.counts['get']
    until(lambda: stub.counts['get'] >= calls + 2)
    assert session.readSnapshot().revision == revision


def testUnimplementedStatusCannotResumeKnownUnavailableResults(observer):
    stub, create = observer
    stub.capture = False
    session = create()
    until(lambda: fresh(session, 'RUNNING'))
    def unimplemented(request):
        raise RpcFailure(grpc.StatusCode.UNIMPLEMENTED, 'old endpoint')
    stub.getAction = unimplemented
    until(lambda: session.readSnapshot().job.availability == 'UNAVAILABLE')
    calls = stub.counts['capabilities']
    until(lambda: stub.counts['capabilities'] > calls)
    assert not stub.counts['snapshot'] and not stub.counts['subscribe']
    assert session.readSnapshot().job.status == ''
