"""A18: actual visible page interest gates client image work, not capture."""
import threading

from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.core.presentation.models import Action
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages
from tests.runtime.presentation.test_image_demand_client import demandBackend, latest, idle
from tests.ui.presentation.test_real import until


def imageReady(window, page='overview', component='overview-image'):
    return bool(window.displayed) and not window.widgets[page][component][1].image.isNull()


def testVisiblePageUnionSkipsHiddenSourcesWhileFormalResultsAdvance(qtApp, tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch, count=8, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        hub = DisplayHub(session)
        first = RuntimePages(backend.project.presentation, hub=hub)
        second = RuntimePages(backend.project.presentation, hub=hub)
        second.navigate('numbers')
        first.show()
        second.show()
        try:
            until(qtApp, lambda: imageReady(first))
            assert session._wantedImages == {'image'}
            assert backend.calls['ReadAsset'] == 1 and session.stats['decoded'] == 1
            assert not latest(session).failures and latest(session).imageStates['original'] == 'NOT_REQUESTED'
            workers = tuple(session.threads)
            starts = backend.calls['StartJob'], backend.calls['Start'], backend.calls['Subscribe']
            first.navigate('numbers')
            until(qtApp, lambda: idle(session))
            reads = backend.calls['ReadAsset']
            ordinal = latest(session).result.identity.resultOrdinal
            until(qtApp, lambda: latest(session) and latest(session).result.identity.resultOrdinal >= ordinal + 2)
            assert session._wantedImages == frozenset()
            assert backend.calls['ReadAsset'] == reads
            assert first.widgets['overview']['overview-image'][1].image.isNull()
            until(qtApp, lambda: latest(session) and first.displayed and second.displayed and
                  first.displayed['root'].result == second.displayed['root'].result == latest(session).result)
            assert first.widgets['numbers']['only-count'][1].text() in ('2', '3')
            first.navigate('detail')
            until(qtApp, lambda: imageReady(first, 'detail', 'detail-original'))
            scope = first.displayed['root']
            assert scope.images['image'] is scope.images['image-alias']
            assert not scope.failures and scope.result == latest(session).result
            second.navigate('overview')
            first.hide()
            assert session._wantedImages == {'image'}
            ordinal = latest(session).result.identity.resultOrdinal
            until(qtApp, lambda: latest(session) and latest(session).result.identity.resultOrdinal > ordinal and imageReady(second))
            assert set(latest(session).images) == {'image'}
            assert not first.displayed
            second.hide()
            until(qtApp, lambda: idle(session))
            reads = backend.calls['ReadAsset']
            ordinal = latest(session).result.identity.resultOrdinal
            until(qtApp, lambda: latest(session) and latest(session).result.identity.resultOrdinal == 8)
            assert ordinal < 8 and backend.calls['ReadAsset'] == reads
            first.show()
            until(qtApp, lambda: imageReady(first, 'detail', 'detail-original'))
            assert first.displayed['root'].result.identity.resultOrdinal == 8
            assert tuple(session.threads) == workers and session.pending.maxsize == 8
            assert (backend.calls['StartJob'], backend.calls['Start'], backend.calls['Subscribe']) == starts
            assert len(backend.presentation.jobs) == 1
            stats = hub.stats()
            assert stats['live_decoded_bytes'] <= 16 * 1024 * 1024
            assert stats['decoded_retention_accounted_bytes'] <= stats['decoded_retention_reserved']
        finally:
            first.close()
            second.close()
            session.close()


def testFrozenDetailReadsOldMissingImageWhileAnotherWindowAdvances(qtApp, tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch, count=4, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        hub = DisplayHub(session)
        first = RuntimePages(backend.project.presentation, hub=hub)
        second = RuntimePages(backend.project.presentation, hub=hub)
        first.show()
        second.show()
        try:
            until(qtApp, lambda: imageReady(first) and imageReady(second))
            displayed = first.displayed['root']
            assert 'original' not in displayed.images
            hub.timer.stop()
            # Do not submit a newer value to first before the detail action.
            until(qtApp, lambda: latest(session) and latest(session).result.identity.resultOrdinal > displayed.result.identity.resultOrdinal)
            first.act(Action(type='navigate', pageId='detail', context='displayed_result', resultScopeId='root'))
            hub.timer.start()
            until(qtApp, lambda: session.pins().read(first.frozen).state == 'PINNED')
            until(qtApp, lambda: imageReady(first, 'detail', 'detail-original') and
                  second.displayed['root'].result.identity.resultOrdinal > displayed.result.identity.resultOrdinal)
            frozen = first.displayed['root']
            assert frozen.result == displayed.result
            assert frozen.images['image'] is displayed.images['image']
            assert frozen.images['image'] is frozen.images['image-alias']
            original = next(source.image for source in displayed.result.sources if source.sourceId == 'original')
            assert original.resourceId in backend.assets
            assert 'original' not in latest(session).images
            assert session._wantedImages == {'image'}
            assert backend.presentation.assets.stats()['lease_handles'] == 2
            first.resumeLive()
            until(qtApp, lambda: latest(session) and imageReady(first, 'detail', 'detail-original') and first.displayed['root'].result == latest(session).result)
            until(qtApp, lambda: backend.presentation.assets.stats()['lease_handles'] == 0)
        finally:
            first.close()
            second.close()
            session.close()


def testHiddenFrozenDetailWaitsForVisibilityThenExpiresExplicitly(qtApp, tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        hub = DisplayHub(session)
        window = RuntimePages(backend.project.presentation, hub=hub)
        window.show()
        entered, release = threading.Event(), threading.Event()
        try:
            until(qtApp, lambda: imageReady(window))
            shown = window.displayed['root']
            original = session.stub.AcquireLease
            def gate(*args, **kwargs):
                entered.set()
                assert release.wait(5)
                return original(*args, **kwargs)
            monkeypatch.setattr(session.stub, 'AcquireLease', gate)
            window.act(Action(type='navigate', pageId='detail', context='displayed_result', resultScopeId='root'))
            assert entered.wait(2)
            window.hide()
            reads = backend.calls['ReadAsset']
            release.set()
            until(qtApp, lambda: bool(session.pins().entries[window.frozen]['leases']))
            assert backend.calls['ReadAsset'] == reads
            assert not session.pins().entries[window.frozen]['decodeQueued']
            window.show()
            until(qtApp, lambda: imageReady(window, 'detail', 'detail-original'))
            assert window.displayed['root'].result == shown.result
            assert backend.calls['ReadAsset'] == reads + 1
            window.resumeLive()
            until(qtApp, lambda: not session.pins().entries)
            window.frozen = hub.freeze(window, window.displayed['root'], window.lastView.generation, 150,
                                       sourceIds=window.imageSources())
            window.frozenGeneration = window.lastView.generation
            until(qtApp, lambda: 'EXPIRED' in window.modeLabel.text())
            assert not window.displayed and window.widgets['detail']['detail-original'][1].image.isNull()
            until(qtApp, lambda: session.pins().bytesHeld() == 0)
        finally:
            release.set()
            window.close()
            session.close()


def testThousandHideShowCyclesReuseSameImageAndReleaseDemandOwner(qtApp, tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        hub = DisplayHub(session)
        window = RuntimePages(backend.project.presentation, hub=hub)
        window.show()
        try:
            until(qtApp, lambda: imageReady(window))
            key = window.displayed['root'].result.identity.resultKey
            reads, decodes, conversions = backend.calls['ReadAsset'], session.stats['decoded'], hub.conversions
            for _ in range(1000):
                window.hide()
                window.show()
            assert imageReady(window) and window.displayed['root'].result.identity.resultKey == key
            assert (backend.calls['ReadAsset'], session.stats['decoded'], hub.conversions) == (reads, decodes, conversions)
            assert len(session._imageConsumers) == 1 and not session._scheduled and session.pending.empty()
            assert len(window.records) <= 256 and len(hub.cache) == 1
            window.close()
            assert not session._imageConsumers and not hub.windows and not hub.timer.isActive()
            assert not backend.runtime._closed
        finally:
            window.close()
            session.close()


def testDestroyedHubsRetireDemandTokensBeforeReplacement(qtApp, tmp_path, monkeypatch):
    from PySide2.QtCore import QCoreApplication, QEvent
    with demandBackend(tmp_path, monkeypatch) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        try:
            until(qtApp, lambda: latest(session))
            for _ in range(20):
                hub = DisplayHub(session)
                session.setImageDemand(hub.imageConsumer, set())
                assert len(session._imageConsumers) == 1
                hub.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                assert not session._imageConsumers
            assert backend.calls['ReadAsset'] == session.stats['decoded'] == 0
        finally:
            session.close()


def testDeletingOneWindowWithoutClosePreservesSurvivorDemand(qtApp, tmp_path, monkeypatch):
    from PySide2.QtCore import QCoreApplication, QEvent
    from shiboken2 import isValid
    with demandBackend(tmp_path, monkeypatch, count=3, dual=True) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        hub = DisplayHub(session)
        first = RuntimePages(backend.project.presentation, hub=hub)
        second = RuntimePages(backend.project.presentation, hub=hub)
        first.navigate('detail')
        first.show()
        second.show()
        try:
            until(qtApp, lambda: imageReady(first, 'detail', 'detail-original') and imageReady(second))
            ordinal = second.displayed['root'].result.identity.resultOrdinal
            first.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not isValid(first) and first not in hub.windows
            assert session._wantedImages == {'image'} and len(hub.windows) == 1
            until(qtApp, lambda: second.displayed and imageReady(second) and
                  second.displayed['root'].result.identity.resultOrdinal > ordinal)
            assert set(second.displayed['root'].images) == {'image'}
            second.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not hub.windows and not session._imageConsumers and not hub.timer.isActive()
        finally:
            if isValid(first):
                first.close()
            if isValid(second):
                second.close()
            session.close()


def testRepeatedAttachDetachKeepsOneDestructionCallback(qtApp, tmp_path, monkeypatch):
    with demandBackend(tmp_path, monkeypatch) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        hub = DisplayHub(session)
        window = RuntimePages(backend.project.presentation, hub=hub)
        try:
            until(qtApp, lambda: latest(session))
            link = window._displayHubRetirement
            for _ in range(1000):
                hub.attach(window)
                assert window._displayHubRetirement is link
                assert len(hub._windowLinks) == len(hub.windows) == 1
                hub.detach(window)
                assert not hub._windowLinks and not session._imageConsumers
                hub.attach(window)
            assert len(hub._windowLinks) == 1 and len(session._imageConsumers) == 1
            assert backend.calls['ReadAsset'] == session.stats['decoded'] == 0
        finally:
            window.close()
            session.close()


def testHubOwnerDeletionBeforeFirstWindowCloseRetiresQtFreeState(qtApp, tmp_path, monkeypatch):
    from PySide2.QtCore import QObject, QCoreApplication, QEvent
    with demandBackend(tmp_path, monkeypatch) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        owner = QObject()
        hub = DisplayHub(session, owner)
        window = RuntimePages(backend.project.presentation, hub=hub)
        window.show()
        try:
            until(qtApp, lambda: imageReady(window))
            window.frozen = hub.freeze(window, window.displayed['root'], window.lastView.generation)
            window.frozenGeneration = window.lastView.generation
            until(qtApp, lambda: session.pins().read(window.frozen).state == 'PINNED')
            owner.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert hub.lifecycle['retired'] and not hub._windowLinks
            assert window._displayHubRetirement['owner'] is None
            assert not session._imageConsumers
            window.close()  # first close, after its hub's native timer is gone
            assert window.hub is None and not hub.windows
            until(qtApp, lambda: session.pins().bytesHeld() == 0 and
                  backend.presentation.assets.stats()['lease_handles'] == 0)
        finally:
            window.close()
            session.close()
