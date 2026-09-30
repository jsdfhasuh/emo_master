"""Opt-in image demand over real normal/presentation loopback RPCs."""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

import grpc
import numpy as np
import pytest

from examples.runtime_pages_p3 import sampleProjectP3, pluginRoots
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.rpc import DisplayRpc
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.core.project.models import ProjectDocument
from tests.runtime.presentation.test_network import until
from tests.runtime.runtime_test_utils import waitForTerminal


@contextmanager
def demandBackend(root, monkeypatch, *, mode='normal', count=1, dual=False):
    root = Path(root)
    project = sampleProjectP3(root, count=count)
    if dual:
        raw = project.model_dump()
        presentation = raw['presentation']
        presentation['dataSources']['original'] = dict(presentation['dataSources']['image'], nodeId='select', port='image')
        presentation['dataSources']['image-alias'] = deepcopy(presentation['dataSources']['image'])
        presentation['pages']['detail']['components'].extend([
            {'componentId': 'detail-original', 'type': 'image', 'bindings': {'image': 'original'}, 'layout': {'row': 6}},
            {'componentId': 'detail-alias', 'type': 'image', 'bindings': {'image': 'image-alias'}, 'layout': {'row': 7}}])
        # A genuinely image-free live page still displays current formal metadata.
        presentation['pages']['numbers'] = {'name': 'Numbers', 'components': [
            {'componentId': 'only-count', 'type': 'number', 'bindings': {'value': 'count'}}]}
        presentation['pageOrder'].append('numbers')
        project = ProjectDocument.model_validate(raw)
    calls = Counter()
    assets = []
    for owner, name in [(DisplayRpc, name) for name in ('ReadAsset', 'Start', 'Subscribe', 'Prepare')]+[
            (RuntimeService, 'StartJob'), (RuntimeService, 'StopJob')]:
        original = getattr(owner, name)
        def counted(self, request, context, _original=original, _name=name):
            calls[_name] += 1
            if _name == 'ReadAsset':
                assets.append(request.resource_id)
            return _original(self, request, context)
        monkeypatch.setattr(owner, name, counted)
    runtime = RuntimeService(dbPath=root/'state.db', workspaceRoot=root/'jobs', pluginRootPaths=pluginRoots(root))
    presentation = PresentationService(runtime, root/'display')
    server = AioRuntimeServer(runtime, presentation)
    address = f'127.0.0.1:{server.port}'
    try:
        with grpc.insecure_channel(address) as connection:
            display = rpc.DisplayServiceStub(connection)
            if mode == 'normal':
                # Normal Start uses authored node parameters; resource binding
                # materialization belongs to isolated Prepare, not this path.
                for binding in project.resources.parameterBindings:
                    node = next(node for node in project.workflows[binding.target.workflowId].nodes
                                if node.nodeId == binding.target.nodeId)
                    node.params['imagePath'] = str(root/project.resources.items[binding.resourceId].path)
                (root/'project.json').write_text(project.model_dump_json(), encoding='utf-8')
                legacy = rpc.RuntimeServiceStub(connection)
                loaded = legacy.LoadProject(pb.LoadProjectRequest(project_path=str(root)), timeout=5)
                assert loaded.ok, loaded.message
                reply = legacy.StartJob(pb.StartJobRequest(project_id=project.project.projectId, capture_presentation=True,
                    start_request_id=uuid4().hex, expected_runtime_instance_id=runtime.runtimeInstanceId), timeout=5)
                assert reply.ok, reply.message
                job = reply.job_id
            else:
                prepared = display.Prepare(pb.DisplayPrepareRequest(project_json=project.model_dump_json(),
                    resource_root=str(root)), timeout=15)
                job = display.Start(pb.DisplayStartRequest(prepared_id=prepared.prepared_id), timeout=5).job_id
        yield SimpleNamespace(project=project, runtime=runtime, presentation=presentation,
                              server=server, address=address, jobId=job, calls=calls, assets=assets)
    finally:
        server.close()
        runtime.close()
        presentation.close()


def latest(session):
    return session.readSnapshot().scopes.get('root')


def idle(session):
    with session.lock:
        return not session._scheduled and session.pending.empty()


@pytest.mark.parametrize('mode', ['normal', 'presentation'])
def testZeroDemandKeepsFormalResultsAndResumesFinalCapturedResult(tmp_path, monkeypatch, mode):
    with demandBackend(tmp_path, monkeypatch, mode=mode, count=5) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        try:
            until(lambda: latest(session) and latest(session).result.identity.resultOrdinal >= 2)
            scope = latest(session)
            assert not scope.images and not scope.failures
            assert scope.imageStates == {'image': 'NOT_REQUESTED'}
            assert session.stats['decoded'] == backend.calls['ReadAsset'] == 0
            starts = backend.calls['Start'], backend.calls['StartJob'], backend.calls['StopJob']
            subscriptions = backend.calls['Subscribe']
            workers = tuple(session.threads)
            session.setImageDemand('gui', {'image'})
            until(lambda: latest(session) and 'image' in latest(session).images)
            assert not latest(session).imageStates
            session.setImageDemand('gui', set())
            until(lambda: idle(session))
            reads, decoded = backend.calls['ReadAsset'], session.stats['decoded']
            ordinal = latest(session).result.identity.resultOrdinal
            assert waitForTerminal(backend.runtime, backend.jobId).status == 'COMPLETED'
            until(lambda: latest(session) and latest(session).result.identity.resultOrdinal == 5)
            assert latest(session).result.identity.resultOrdinal >= ordinal + 2
            assert backend.calls['ReadAsset'] == reads and session.stats['decoded'] == decoded
            final = latest(session).result
            session.setImageDemand('gui', {'image'})
            until(lambda: latest(session) and 'image' in latest(session).images)
            scope = latest(session)
            assert scope.result == final and not scope.failures
            image = next(source.image for source in final.sources if source.sourceId == 'image')
            assert backend.assets[-1] == image.resourceId
            assert next(source.valueJson for source in final.sources if source.sourceId == 'count') == '2'
            assert scope.images['image'].shape == (120, 160, 3)
            before = backend.calls['ReadAsset']
            for _ in range(30):
                session.setImageDemand('gui', set())
                session.setImageDemand('gui', {'image'})
            assert backend.calls['ReadAsset'] == before and idle(session)
            assert (backend.calls['Start'], backend.calls['StartJob'], backend.calls['StopJob']) == starts
            assert backend.calls['Subscribe'] == subscriptions
            assert tuple(session.threads) == workers and len(workers) == 3 and session.pending.maxsize == 8
            assert len(backend.presentation.jobs) == 1
        finally:
            session.close()


def testIndependentHeadlessConsumerStillDecodesWithNoGuiInterest(tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch, count=3) as backend:
        gui = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        headless = DisplaySession(backend.address, backend.jobId)
        try:
            until(lambda: latest(gui) and latest(headless) and latest(headless).result.identity.resultOrdinal == 3)
            assert headless.stats['decoded'] == 3
            assert gui.stats['decoded'] == 0 and not latest(gui).images
            assert latest(headless).images['image'].shape == (120, 160, 3)
            assert latest(gui).result == latest(headless).result
            assert len(backend.presentation.jobs) == 1
        finally:
            gui.close()
            headless.close()


def testDemandOwnerUnionBoundsAndAliasReuse(tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        try:
            observed = []
            session.observe(observed.append)
            until(lambda: latest(session))
            session.setImageDemand('first', {'image'})
            until(lambda: 'image' in latest(session).images)
            assert backend.calls['ReadAsset'] == 1
            session.setImageDemand('second', {'original', 'image-alias'})
            session.removeImageDemand('first')
            until(lambda: {'image', 'original', 'image-alias'} <= set(latest(session).images))
            scope = latest(session)
            assert scope.images['image'] is scope.images['image-alias']
            assert not np.array_equal(scope.images['image'], scope.images['original'])
            assert backend.calls['ReadAsset'] == session.stats['decoded'] == 2
            assert observed == [scope.result]
            records = [row for row in session.records if row['key'] == scope.result.identity.resultKey]
            assert len(records) >= 3
            assert len({row['received_ns'] for row in records}) == 1
            assert len({row['owner_age_at_send_ms'] for row in records}) == 1
            with pytest.raises(ValueError, match='IMAGE_DEMAND_BUDGET'):
                session.setImageDemand('large', [str(index) for index in range(129)])
            for index in range(7):
                session.setImageDemand(str(index), set())
            with pytest.raises(ValueError, match='IMAGE_DEMAND_BUDGET'):
                session.setImageDemand('ninth', set())
            assert len(session._imageConsumers) == 8 and len(session._scheduled) <= 9
            session.setImageDemand('second', None)
            assert session._wantedImages is None
            session.removeImageDemand('second')
            assert session._wantedImages == frozenset()
            with pytest.raises(TypeError):
                scope.imageStates['image'] = 'LOADING'
        finally:
            session.close()


@pytest.mark.parametrize('change', ['hide', 'job', 'expiry'])
def testInFlightAssetIsBoundedAndCannotRestoreStaleResult(tmp_path, monkeypatch, change):
    from emo_master.clients.runtime import display_session as module
    with demandBackend(tmp_path, monkeypatch, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        entered, release = threading.Event(), threading.Event()
        original = module.decodePng
        def gated(content):
            entered.set()
            assert release.wait(5)
            return original(content)
        try:
            until(lambda: latest(session))
            scope = latest(session)
            monkeypatch.setattr(module, 'decodePng', gated)
            session.setImageDemand('gui', {'image', 'original', 'image-alias'})
            assert entered.wait(3)
            if change == 'hide':
                session.setImageDemand('gui', set())
            elif change == 'job':
                session.selectJob('absent-job')
            else:
                # Exercise the same authoritative expiry tombstone as the real
                # health RPC, without waiting for unrelated metadata pressure.
                view = session.readSnapshot()
                session._accept(pb.DisplaySnapshot(runtime_instance_id=view.runtimeInstanceId, job_id=view.jobId,
                    cursor=session.cursor, expired_scope_ordinals={'root': scope.result.identity.resultOrdinal}))
            release.set()
            until(lambda: idle(session))
            assert backend.calls['ReadAsset'] == session.stats['decoded'] == 1
            if change == 'hide':
                assert set(latest(session).images) <= {'image', 'image-alias', 'original'}
                assert len(latest(session).images) == 1
            else:
                assert not session.readSnapshot().scopes
            assert session.pending.qsize() <= 8 and len(session._scheduled) <= 9
        finally:
            release.set()
            session.close()


def testLateDemandSurfacesRealResourceExpiry(tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        try:
            until(lambda: latest(session))
            final = latest(session).result
            backend.presentation.assets.retain(set())
            session.setImageDemand('gui', {'image'})
            until(lambda: latest(session) and bool(latest(session).failures))
            assert latest(session).result == final
            assert 'RESOURCE_EXPIRED' in latest(session).failures['image']
            assert not latest(session).images and not latest(session).imageStates
            assert backend.calls['ReadAsset'] == 1 and session.stats['decoded'] == 0
        finally:
            session.close()


def testTwoPinsReadExactMissingAssetsWithinActualByteBudget(tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        try:
            until(lambda: latest(session))
            scope = latest(session)
            generation = session.readSnapshot().generation
            pins = session.pins()
            tickets = [pins.acquire(scope, generation, sourceIds={'image', 'image-alias', 'original'}) for _ in range(2)]
            until(lambda: all(pins.read(ticket).state == 'PINNED' for ticket in tickets))
            for ticket in tickets:
                frozen = pins.read(ticket).scope
                assert frozen.result == scope.result and not frozen.failures
                assert frozen.images['image'] is frozen.images['image-alias']
                assert len(frozen.images) == 3 and not frozen.imageStates
            assert pins.bytesHeld() == 2 * 2 * 120 * 160 * 3
            assert backend.calls['ReadAsset'] == session.stats['decoded'] == 4
            assert not latest(session).images  # pin work did not create live demand
            assert len(session.threads) == 3 and session.pending.maxsize == 8
            for ticket in tickets:
                pins.release(ticket)
            until(lambda: not pins.entries and backend.presentation.assets.stats()['lease_handles'] == 0)
        finally:
            session.close()


@pytest.mark.parametrize('change', ['hidden', 'cancel', 'generation'])
def testPendingPinSkipsAndRetiresAdmissionWithoutChangingFrozenIdentity(tmp_path, monkeypatch, change):
    with demandBackend(tmp_path, monkeypatch, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        entered, release = threading.Event(), threading.Event()
        try:
            until(lambda: latest(session))
            scope = latest(session)
            pins = session.pins()
            original = session.stub.AcquireLease
            def gate(*args, **kwargs):
                entered.set()
                assert release.wait(5)
                return original(*args, **kwargs)
            monkeypatch.setattr(session.stub, 'AcquireLease', gate)
            ticket = pins.acquire(scope, session.readSnapshot().generation, sourceIds={'image', 'original'})
            assert entered.wait(2)
            if change == 'hidden':
                pins.setActive(ticket, False)
            elif change == 'cancel':
                pins.release(ticket)
            else:
                session.selectJob('absent-job')
            release.set()
            if change == 'hidden':
                until(lambda: bool(pins.entries[ticket]['leases']))
                assert backend.calls['ReadAsset'] == session.stats['decoded'] == 0
                assert not pins.entries[ticket]['decodeQueued'] and session.pending.empty()
                pins.setActive(ticket, True)
                until(lambda: pins.read(ticket).state == 'PINNED')
                assert pins.read(ticket).scope.result == scope.result
                assert backend.calls['ReadAsset'] == 2
                pins.release(ticket)
            until(lambda: not pins.entries and backend.presentation.assets.stats()['lease_handles'] == 0)
            until(lambda: idle(session))
            if change != 'hidden':
                assert backend.calls['ReadAsset'] == session.stats['decoded'] == 0
        finally:
            release.set()
            session.close()


def grayAlphaPng(width, height, pixels=None):
    import struct
    import zlib
    def chunk(name, payload):
        return struct.pack('>I', len(payload)) + name + payload + struct.pack('>I', zlib.crc32(name + payload))
    header = struct.pack('>IIBBBBB', width, height, 8, 4, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + (chunk(b'IDAT', zlib.compress(pixels)) if pixels is not None else b'') + chunk(b'IEND', b''))


def testGrayAlphaPredecodeReservationAccountsForOpenCvExpansion(monkeypatch):
    import cv2
    from emo_master.clients.runtime.display_session import decodePng, pngDecodedBytes
    content = grayAlphaPng(2, 1, b'\x00\x22\xff\x77\x80')
    assert pngDecodedBytes(content) == 8
    image = decodePng(content)
    assert image.shape == (1, 2, 4) and image.nbytes == 8
    assert pngDecodedBytes(grayAlphaPng(2048, 1024)) == 8 * 1024 * 1024
    def forbidden(*args, **kwargs):
        raise AssertionError('oversize PNG must be rejected before allocation')
    monkeypatch.setattr(cv2, 'imdecode', forbidden)
    with pytest.raises(ValueError, match='decoded image budget'):
        decodePng(grayAlphaPng(2048, 1025))


@pytest.mark.parametrize('change', ['cancel', 'generation', 'expiry'])
def testQueuedPinAndThousandDemandChangesReleaseEveryAdmission(tmp_path, monkeypatch, change):
    from emo_master.clients.runtime import display_session as module
    with demandBackend(tmp_path, monkeypatch, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        entered, release = threading.Event(), threading.Event()
        original = module.decodePng
        def gated(content):
            entered.set()
            assert release.wait(5)
            return original(content)
        try:
            until(lambda: latest(session))
            scope = latest(session)
            monkeypatch.setattr(module, 'decodePng', gated)
            session.setImageDemand('gui', {'image'})
            assert entered.wait(3)
            pins = session.pins()
            ticket = pins.acquire(scope, session.readSnapshot().generation, 150 if change == 'expiry' else 30000,
                                  sourceIds={'original'})
            until(lambda: pins.entries.get(ticket, {}).get('decodeQueued'))
            for _ in range(1000):
                session.setImageDemand('gui', {'original'})
                session.setImageDemand('gui', set())
            assert session.pending.qsize() == 1 and len(session._scheduled) == 1
            if change == 'cancel':
                pins.release(ticket)
            elif change == 'generation':
                session.selectJob('absent-job')
            until(lambda: ticket not in pins.entries)
            release.set()
            until(lambda: idle(session))
            assert backend.calls['ReadAsset'] == session.stats['decoded'] == 1
            assert pins.bytesHeld() == 0 and not session._scheduled and session.pending.empty()
            assert not session._pinWaiting and len(session._imageConsumers) == 1
            assert backend.presentation.assets.stats()['lease_handles'] == 0
        finally:
            release.set()
            session.close()


@pytest.mark.parametrize('kind, encodedChannels, decodedChannels', [(0, 1, 1), (2, 3, 4)])
def testTransparencyHeaderBoundMatchesLockedOpenCv(kind, encodedChannels, decodedChannels):
    import struct
    import zlib
    from emo_master.clients.runtime.display_session import decodePng, pngDecodedBytes
    def chunk(name, payload):
        return struct.pack('>I', len(payload)) + name + payload + struct.pack('>I', zlib.crc32(name + payload))
    header = struct.pack('>IIBBBBB', 2, 1, 8, kind, 0, 0, 0)
    content = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
               + chunk(b'tRNS', b'\x00\x77' * encodedChannels)
               + chunk(b'IDAT', zlib.compress(b'\x00' + b'\x77' * (2 * encodedChannels))) + chunk(b'IEND', b''))
    assert pngDecodedBytes(content) == 2 * decodedChannels
    assert decodePng(content).nbytes == 2 * decodedChannels


def testAggregatePinReservationRejectsBeforeSecondDecode(tmp_path, monkeypatch):
    from emo_master.clients.runtime import display_session as module
    with demandBackend(tmp_path, monkeypatch) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        entered, release = threading.Event(), threading.Event()
        decode = module.decodePng
        def gated(content):
            entered.set()
            assert release.wait(5)
            return decode(content)
        try:
            until(lambda: latest(session))
            scope = latest(session)
            pins = session.pins()
            # Scale down only the test's aggregate capacity; the real decoder's
            # per-image cap and allocation/reservation path are unchanged.
            pins.limit = 120 * 160 * 3
            monkeypatch.setattr(module, 'decodePng', gated)
            first = pins.acquire(scope, session.generation, sourceIds={'image'})
            assert entered.wait(3)
            assert pins.bytesHeld() == pins.limit  # reserved before pixels exist
            second = pins.acquire(scope, session.generation, sourceIds={'image'})
            until(lambda: pins.entries[second]['decodeQueued'])
            release.set()
            until(lambda: pins.read(first).state == pins.read(second).state == 'PINNED')
            assert pins.read(first).scope.images['image'].nbytes == pins.limit
            assert pins.read(second).scope.failures == {'image': 'PIN_BUDGET'}
            assert not pins.read(second).scope.images
            assert backend.calls['ReadAsset'] == 2 and session.stats['decoded'] == 1
            assert pins.bytesHeld() == pins.limit
            for ticket in (first, second):
                pins.release(ticket)
            until(lambda: not pins.entries and backend.presentation.assets.stats()['lease_handles'] == 0)
        finally:
            release.set()
            session.close()


def testLeaseReadyPinGetsNextFreeSlotBeforeMoreLiveAdmission(tmp_path, monkeypatch):
    from emo_master.clients.runtime import display_session as module
    with demandBackend(tmp_path, monkeypatch, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        entered, release = threading.Event(), threading.Event()
        decode = module.decodePng
        def gated(content):
            entered.set()
            assert release.wait(5)
            return decode(content)
        try:
            until(lambda: latest(session))
            scope = latest(session)
            monkeypatch.setattr(module, 'decodePng', gated)
            session.setImageDemand('gui', {'image'})
            assert entered.wait(3)
            stale = (session.generation - 1, scope.result, 0, 0)
            for _ in range(session.pending.maxsize):
                session.pending.put_nowait(stale)
            pins = session.pins()
            ticket = pins.acquire(scope, session.generation, sourceIds={'original'})
            until(lambda: pins.entries[ticket]['leaseReady'] and session._pinWaiting)
            assert not pins.entries[ticket]['decodeQueued']
            # A popped stale generation models the decoder's next available
            # slot. The distinct candidate is only used to test refusal; its
            # payload must never enter the queue or be read.
            candidate = scope.result.model_copy(update={'identity': scope.result.identity.model_copy(
                update={'resultKey': 'must-not-enter-queue'})})
            with pins.lock, session.lock:
                assert session.pending.get_nowait() == stale
                assert not session._enqueueResult(candidate, 0, 0)
                assert session.pending.qsize() == 7
            until(lambda: pins.entries[ticket]['decodeQueued'])
            assert session.pending.qsize() == 8
            session.setImageDemand('gui', set())
            release.set()
            until(lambda: pins.read(ticket).state == 'PINNED' and idle(session))
            assert set(pins.read(ticket).scope.images) == {'original'}
            assert backend.calls['ReadAsset'] == session.stats['decoded'] == 2
            pins.release(ticket)
            until(lambda: not pins.entries)
        finally:
            release.set()
            session.close()
