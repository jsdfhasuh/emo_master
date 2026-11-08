"""One live-image recovery after an authoritative health Snapshot; no job rerun."""
import threading

import cv2
import grpc
import numpy as np
import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.presentation.rpc import wireResult
from emo_master.clients.runtime.display_session import DisplaySession
from tests.runtime.presentation.test_image_demand_client import demandBackend, idle, latest
from tests.runtime.presentation.test_live_decisions import result
from tests.runtime.presentation.test_network import until
from tests.runtime.runtime_test_utils import waitForTerminal


@pytest.mark.parametrize('imageDemand,failures', [(True, 1), (False, 1), (True, 2)])
def testCompletedJobRecoversExactAssetOnceOverRealRpc(tmp_path, monkeypatch, imageDemand, failures):
    with demandBackend(tmp_path, monkeypatch, count=1) as backend:
        assert waitForTerminal(backend.runtime, backend.jobId).status == 'COMPLETED'
        original = until(lambda: next(iter(backend.presentation.store.latest.values()), None))
        image = next(source.image for source in original.sources if source.sourceId == 'image')
        expected = cv2.imread(str(backend.presentation.assets.items[image.resourceId]['path']))
        assert expected is not None and expected.shape == (120, 160, 3)
        realRead, attempts = backend.presentation.assets.read, []

        def temporaryFailure(job, resource):
            attempts.append((job, resource))
            if len(attempts) <= failures:
                raise TimeoutError('injected temporary asset read')
            return realRead(job, resource)

        monkeypatch.setattr(backend.presentation.assets, 'read', temporaryFailure)
        session = DisplaySession(backend.address, backend.jobId, imageDemand=imageDemand, readResults=False)
        observed = []
        session.observe(observed.append)
        if imageDemand:
            session.setImageDemand('gui', {'image'})
        session.selectJob(backend.jobId, readResults=True)
        try:
            until(lambda: session.stats['read_failed'] == failures and latest(session)
                  and (latest(session).images if failures == 1 else len(attempts) == 2))
            until(lambda: idle(session))
            current = latest(session)
            assert current.result == original
            assert next(source for source in current.result.sources if source.sourceId == 'image').state == 'AVAILABLE'
            assert attempts == [(backend.jobId, image.resourceId)] * 2
            assert backend.calls['ReadAsset'] == 2
            if failures == 1:
                np.testing.assert_array_equal(current.images['image'], expected)
                assert not current.failures and not session._imageRetries
                assert session.stats['decoded'] == 1
            else:
                assert not current.images and 'injected temporary asset read' in current.failures['image']
                assert session.stats['decoded'] == 0
                session.setImageDemand('gui', set())
                session.setImageDemand('gui', {'image'})
            revision = session.revision
            until(lambda: session.revision >= revision + 3)
            assert backend.calls['ReadAsset'] == len(attempts) == 2
            assert observed == [original]
            assert session.readSnapshot().connection == 'CONNECTED'
            assert backend.calls['StartJob'] == 1 and backend.calls['StopJob'] == 0
            assert backend.runtime.jobRepository.get(backend.jobId).status == 'COMPLETED'
        finally:
            session.close()
            assert not session._imageRetries


class ReadError(grpc.RpcError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail

    def code(self):
        return self.status

    def details(self):
        return self.detail


@pytest.fixture
def session(monkeypatch, request):
    # Drive only the existing health acceptance point explicitly; the bounded
    # production queue/decoder still run. Real RPC recovery is exercised above.
    for name in ('_receive', '_health'):
        monkeypatch.setattr(DisplaySession, name, lambda self: None)
    value = DisplaySession('127.0.0.1:1', 'job', imageDemand=getattr(request, 'param', True),
                           expectedRuntimeInstanceId='runtime', projectId='project')
    try:
        yield value
    finally:
        value.close()
        assert not value._imageRetries


def snapshot(value, **changes):
    return pb.DisplaySnapshot(runtime_instance_id='runtime', job_id='job', cursor=2,
                              results=[wireResult(value)], **changes)


def health(session, value, reply=None):
    with session.lock:
        for accepted in session._accept(reply or snapshot(value)) or ():
            session._retryImages(accepted)


def failFirst(session, monkeypatch, error):
    attempts = []
    pixels = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)

    def read(value, image):
        attempts.append(image.resourceId)
        if len(attempts) == 1:
            raise error
        return pixels

    monkeypatch.setattr(session, '_readImage', read)
    value = result(1)
    session._accept(snapshot(value))
    until(lambda: latest(session))
    session.setImageDemand('gui', {'image'})
    until(lambda: latest(session).failures)
    return value, attempts


@pytest.mark.parametrize('error,retry', [
    (ReadError(grpc.StatusCode.UNAVAILABLE, 'temporary transport'), True),
    (ReadError(grpc.StatusCode.RESOURCE_EXHAUSTED, 'asset'), True),
    (ReadError(grpc.StatusCode.RESOURCE_EXHAUSTED, 'control'), False),
    (ReadError(grpc.StatusCode.NOT_FOUND, 'RESOURCE_EXPIRED'), False),
    (ReadError(grpc.StatusCode.CANCELLED, 'cancelled'), False),
    (ValueError('asset digest/size mismatch'), False),
    (ValueError('image decoding failed or exceeded budget'), False),
])
def testOnlyTransientAssetFailuresCanRecover(session, monkeypatch, error, retry):
    value, attempts = failFirst(session, monkeypatch, error)
    until(lambda: idle(session))
    health(session, value)
    until(lambda: idle(session))
    assert len(attempts) == (2 if retry else 1)
    assert bool(latest(session).images) is retry
    assert bool(latest(session).failures) is not retry
    assert not session._imageRetries


@pytest.mark.parametrize('invalid', ['old_cursor', 'changed_asset', 'subscription', 'no_demand', 'seen_evicted'])
def testRecoveryRequiresCurrentHealthAndInterest(session, monkeypatch, invalid):
    value, attempts = failFirst(session, monkeypatch, ReadError(grpc.StatusCode.UNAVAILABLE, 'temporary'))
    until(lambda: idle(session))
    if invalid == 'old_cursor':
        reply = snapshot(value)
        reply.cursor = 1
        health(session, value, reply)
    elif invalid == 'changed_asset':
        source = value.sources[0]
        changed = value.model_copy(update={'sources': (source.model_copy(update={
            'image': source.image.model_copy(update={'resourceId': 'unconfirmed-asset'})}),)})
        health(session, changed)
    elif invalid == 'subscription':
        session._accept(snapshot(value))
    elif invalid == 'no_demand':
        session.setImageDemand('gui', set())
        health(session, value)
        session.setImageDemand('gui', {'image'})
    else:
        # Other scopes may rotate the bounded seen deque; latest still owns this
        # immutable result and must not re-enqueue it as an ordinary first read.
        session.seen.clear()
    until(lambda: idle(session))
    assert len(attempts) == 1 and latest(session).failures
    health(session, value)
    until(lambda: latest(session).images and idle(session))
    assert attempts == ['asset-1', 'asset-1']


@pytest.mark.parametrize('boundary', ['scheduled', 'queued_then_hidden'])
def testRecoveryDoesNotLoseAuthorizationAtDecoderBoundary(session, monkeypatch, boundary):
    entered, release = threading.Event(), threading.Event()
    original = session._decodeLive

    def gated(*args):
        if boundary == 'queued_then_hidden' and len(args) == 5:
            entered.set()
            assert release.wait(3)
        original(*args)
        if boundary == 'scheduled' and latest(session) and latest(session).failures and not entered.is_set():
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(session, '_decodeLive', gated)
    try:
        value, attempts = failFirst(session, monkeypatch, ReadError(grpc.StatusCode.UNAVAILABLE, 'temporary'))
        if boundary == 'scheduled':
            assert entered.wait(2)
            health(session, value)
            assert set(session._imageRetries.values()) == {'waiting'}
        else:
            until(lambda: idle(session))
            health(session, value)
            assert entered.wait(2)
            session.setImageDemand('gui', set())
        release.set()
        until(lambda: idle(session))
        assert len(attempts) == 1 and latest(session).failures
        assert set(session._imageRetries.values()) == {'waiting'}
        session.setImageDemand('gui', {'image'})
        assert len(attempts) == 1
        health(session, value)
        until(lambda: latest(session).images and idle(session))
        assert len(attempts) == 2
    finally:
        release.set()


@pytest.mark.parametrize('fence', ['newer', 'job', 'runtime', 'reset', 'expired'])
def testRetiringLiveResultClearsRecoveryIntent(session, monkeypatch, fence):
    value, attempts = failFirst(session, monkeypatch, ReadError(grpc.StatusCode.UNAVAILABLE, 'temporary'))
    until(lambda: idle(session))
    assert session._imageRetries
    session.setImageDemand('gui', set())
    if fence == 'job':
        session.selectJob('other')
    else:
        reply = snapshot(result(2) if fence == 'newer' else value)
        if fence == 'runtime':
            reply.runtime_instance_id = 'other-runtime'
        elif fence == 'reset':
            reply.reset_required = True
        elif fence == 'expired':
            reply.expired_scope_ordinals['root'] = 1
        session._accept(reply)
    until(lambda: idle(session))
    assert not session._imageRetries
    assert attempts == ['asset-1']


@pytest.mark.parametrize('session', [True, False], indirect=True, ids=['gui', 'headless'])
def testQueuedOldRecoveryCannotReadAfterNewResult(session, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = session._decodeLive

    def gated(*args):
        if len(args) == 5:
            entered.set()
            assert release.wait(3)
        original(*args)

    monkeypatch.setattr(session, '_decodeLive', gated)
    try:
        value, attempts = failFirst(session, monkeypatch, ReadError(grpc.StatusCode.UNAVAILABLE, 'temporary'))
        until(lambda: idle(session))
        health(session, value)
        assert entered.wait(2)
        session._accept(snapshot(result(2)))
        assert not session._imageRetries
        release.set()
        until(lambda: latest(session) and latest(session).result.identity.resultOrdinal == 2
              and latest(session).images and idle(session))
        assert attempts == ['asset-1', 'asset-2']
    finally:
        release.set()


def testLateOldFailureCannotRecreateRetiredRecoveryIntent(session, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def read(value, image):
        attempts.append(image.resourceId)
        if image.resourceId == 'asset-1':
            entered.set()
            assert release.wait(3)
            raise ReadError(grpc.StatusCode.UNAVAILABLE, 'late old failure')
        return np.zeros((2, 2, 3), dtype=np.uint8)

    monkeypatch.setattr(session, '_readImage', read)
    try:
        session._accept(snapshot(result(1)))
        until(lambda: latest(session))
        session.setImageDemand('gui', {'image'})
        assert entered.wait(2)
        session._accept(snapshot(result(2)))
        release.set()
        until(lambda: latest(session) and latest(session).result.identity.resultOrdinal == 2
              and latest(session).images and idle(session))
        assert not session._imageRetries
        assert attempts == ['asset-1', 'asset-2']
        assert any(row['live_decision'] == 'SUPERSEDED' and row['failures'] for row in session.records)
    finally:
        release.set()
