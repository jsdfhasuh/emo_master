import time
import threading

import pytest

from examples.runtime_pages_p3 import LocalDemo
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.core.presentation.models import Action
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages
from tests.ui.presentation.test_real import until


@pytest.fixture
def live():
    backend=LocalDemo(count=8)
    session=DisplaySession(backend.address,backend.jobId)
    try:
        yield backend,session
    finally:
        session.close()
        backend.close()


def testAtomicReadIsImmutableAndLateObserverNeedsNoNewResult(qtApp,live):
    backend,session=live
    until(qtApp,lambda:bool(session.readSnapshot().scopes))
    view=session.readSnapshot()
    scope=view.scopes['root']
    with pytest.raises(TypeError):
        scope.images['other']=scope.images['image']
    with pytest.raises(ValueError):
        scope.images['image'].flags.writeable=True
    key=scope.result.identity.resultKey
    window=RuntimePages(backend.project.presentation,hub=DisplayHub(session))
    window.show()
    assert window.displayed['root'].result.identity.resultKey==key
    window.close()


def testDetailsPinGuiValueWhileAnotherWindowContinues(qtApp,live):
    backend,session=live
    hub=DisplayHub(session)
    a=RuntimePages(backend.project.presentation,hub=hub)
    b=RuntimePages(backend.project.presentation,hub=hub)
    a.show()
    b.show()
    try:
        until(qtApp,lambda:bool(a.displayed))
        displayed=a.displayed['root']
        hub.timer.stop()
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            latest=session.readSnapshot().scopes.get('root')
            if latest and latest.result.identity.resultOrdinal>displayed.result.identity.resultOrdinal:
                break
            time.sleep(.01)  # intentionally don't pump GUI; background proceeds
        assert latest.result.identity.resultKey!=displayed.result.identity.resultKey
        ledger=hub.stats()
        assert ledger['live_ui_decoded_bytes']>=latest.images['image'].nbytes+displayed.images['image'].nbytes
        assert ledger['decoded_retention_accounted_bytes']<=ledger['decoded_retention_reserved']
        a.act(Action(type='navigate',pageId='detail',context='displayed_result',resultScopeId='root'))
        hub.timer.start()
        until(qtApp,lambda:session.pins().read(a.frozen).state=='PINNED')
        until(qtApp,lambda:b.displayed and b.displayed['root'].result.identity.resultOrdinal>displayed.result.identity.resultOrdinal)
        assert a.displayed['root'].result.identity.resultKey==displayed.result.identity.resultKey
        assert hub.stats()['pin_decoded_bytes']>0
        assert backend.presentation.assets.stats()['lease_handles']==1
        a.resumeLive()
        until(qtApp,lambda:backend.presentation.assets.stats()['lease_handles']==0)
        until(qtApp,lambda:a.displayed and a.displayed['root'].result==b.displayed['root'].result)
        assert len(backend.presentation.jobs)==1
    finally:
        a.close()
        b.close()


def testFiniteLeaseExpiryRejectsUnboundedPins(qtApp,live):
    backend,session=live
    until(qtApp,lambda:bool(session.readSnapshot().scopes))
    view=session.readSnapshot()
    pins=session.pins()
    one=pins.acquire(view.scopes['root'],view.generation,150)
    two=pins.acquire(view.scopes['root'],view.generation,150)
    with pytest.raises(ValueError,match='PIN_BUDGET'):
        pins.acquire(view.scopes['root'],view.generation,150)
    until(qtApp,lambda:pins.read(one).state=='EXPIRED' and pins.read(two).state=='EXPIRED')
    until(qtApp,lambda:pins.bytesHeld()==0 and backend.presentation.assets.stats()['lease_handles']==0)


def testDisconnectReconnectJobSwitchAndBindingMismatch(qtApp,live):
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    backend,session=live
    hub=DisplayHub(session)
    window=RuntimePages(backend.project.presentation,hub=hub)
    window.show()
    try:
        until(qtApp,lambda:bool(window.displayed))
        old=session.readSnapshot()
        port=backend.server.port
        backend.server.close()
        backend.server=None
        until(qtApp,lambda:session.readSnapshot().connection!='CONNECTED')
        hub.tick()
        assert not window.displayed
        assert window.widgets['overview']['overview-image'][1].image.isNull()
        backend.server=AioRuntimeServer(backend.runtime,backend.presentation,port=port)
        until(qtApp,lambda:session.readSnapshot().connection=='CONNECTED' and bool(window.displayed))
        session.selectJob('missing-job')
        until(qtApp,lambda:session.readSnapshot().connection=='NOT_FOUND')
        hub.tick()
        assert not window.displayed and session.readSnapshot().generation>old.generation
        session.selectJob(backend.jobId)
        until(qtApp,lambda:bool(window.displayed))
        incompatible=backend.project.presentation.model_copy(deep=True)
        incompatible.dataSources['count'].nodeId='different-count-node'
        wrong=RuntimePages(incompatible,hub=hub)
        wrong.show()
        try:
            assert 'CAPTURE_REVISION_MISMATCH' in wrong.widgets['overview']['overview-count'][1].text()
            assert not wrong.displayed
        finally:
            wrong.close()
        assert len(backend.presentation.jobs)==1
    finally:
        window.close()


def testHiddenAndClosedViewsDoNotConvertOrAccessDeletedWidgets(qtApp,live):
    backend,session=live
    hub=DisplayHub(session)
    window=RuntimePages(backend.project.presentation,hub=hub)
    window.show()
    until(qtApp,lambda:bool(window.displayed))
    window.hide()
    conversions=hub.conversions
    before=session.stats['decoded']
    until(qtApp,lambda:session.stats['decoded']>before)
    assert hub.conversions==conversions and not window.displayed
    window.show()
    assert window.displayed
    window.close()
    window.deleteLater()
    qtApp.processEvents()
    hub.tick()
    assert not hub.windows and not hub.timer.isActive()
    assert not backend.runtime._closed


@pytest.mark.parametrize('switchJob', [False, True])
def testLateDecodeCannotRestorePreviousJob(qtApp,monkeypatch,switchJob):
    from emo_master.clients.runtime import display_session as module
    backend=LocalDemo(count=1)
    session=window=None
    entered=threading.Event()
    release=threading.Event()
    decode=module.decodePng
    def delayed(content):
        entered.set()
        if not release.wait(3):
            raise ValueError('test decode gate expired')
        return decode(content)
    try:
        # Install the gate before any consumer thread exists. A finite job may
        # finish before the test starts; its retained result remains observable.
        until(qtApp,lambda:bool(backend.presentation.store.snapshot(backend.jobId)['results']))
        result=backend.presentation.store.snapshot(backend.jobId)['results'][0]
        assert result.status=='COMPLETE'
        key=result.identity.resultKey
        monkeypatch.setattr(module,'decodePng',delayed)
        session=DisplaySession(backend.address,backend.jobId)
        until(qtApp,entered.is_set)
        generation=session.readSnapshot().generation
        if switchJob:
            session.selectJob('missing-job')
        release.set()
        until(qtApp,lambda:any(row['key']==key for row in session.records))
        row=next(row for row in session.records if row['key']==key)
        # A timeout/read error must never count as a successful generation fence.
        assert row['decoded']==['image'] and not row['failures']
        assert row['applied_to_live'] is (not switchJob)
        hub=DisplayHub(session)
        window=RuntimePages(backend.project.presentation,hub=hub)
        window.show()
        if switchJob:
            until(qtApp,lambda:session.readSnapshot().connection=='NOT_FOUND')
            hub.tick()
            assert session.readSnapshot().generation>generation
            assert not window.displayed and not session.readSnapshot().scopes
            assert window.widgets['overview']['overview-image'][1].image.isNull()
            session.selectJob(backend.jobId)
        # Same-generation release is the positive control for the exact result.
        until(qtApp,lambda:bool(window.displayed))
        assert window.displayed['root'].result.identity.resultKey==key
    finally:
        release.set()
        if window is not None:
            window.close()
        try:
            if session is not None:
                session.close()
        finally:
            backend.close()


def testExpiredViewClearsImageWithoutFallingBackToLive(qtApp,live):
    backend,session=live
    hub=DisplayHub(session)
    window=RuntimePages(backend.project.presentation,hub=hub)
    window.show()
    try:
        until(qtApp,lambda:bool(window.displayed))
        window.frozen=hub.freeze(window,window.displayed['root'],window.lastView.generation,150)
        window.frozenGeneration=window.lastView.generation
        until(qtApp,lambda:'EXPIRED' in window.modeLabel.text())
        assert not window.displayed
        assert window.widgets['overview']['overview-image'][1].image.isNull()
        assert 'CONNECTED' in window.widgets['overview']['overview-status'][1].text()
        window.resumeLive()
        assert window.displayed
    finally:
        window.close()


@pytest.mark.parametrize('fault',['timeout','corrupt'])
def testRealAssetFailureIsNotHiddenByComplete(qtApp,live,monkeypatch,fault):
    backend,session=live
    original=backend.presentation.assets.read
    def broken(*args):
        if fault=='timeout':
            raise TimeoutError('controlled read deadline')
        data,sha=original(*args)
        return data[:-1],sha
    monkeypatch.setattr(backend.presentation.assets,'read',broken)
    hub=DisplayHub(session)
    window=RuntimePages(backend.project.presentation,hub=hub)
    window.show()
    try:
        until(qtApp,lambda:window.displayed and bool(window.displayed['root'].failures))
        scope=window.displayed['root']
        image=window.widgets['overview']['overview-image'][1]
        assert scope.result.status=='COMPLETE'
        assert image.image.isNull() and image.message
        assert window.widgets['overview']['overview-count'][1].text() in ('2 个','3 个')
        monkeypatch.setattr(backend.presentation.assets,'read',original)
        until(qtApp,lambda:not image.image.isNull())
    finally:
        window.close()


def testRealMalformedJsonInvalidatesWindow(qtApp,live,monkeypatch):
    from emo_master.apps.runtime.presentation import rpc
    backend,session=live
    hub=DisplayHub(session)
    window=RuntimePages(backend.project.presentation,hub=hub)
    window.show()
    try:
        until(qtApp,lambda:bool(window.displayed))
        original=rpc.wireResult
        def invalid(result):
            wire=original(result)
            for source in wire.sources:
                if source.source_id=='count':
                    source.value_json='1e999'
            return wire
        monkeypatch.setattr(rpc,'wireResult',invalid)
        until(qtApp,lambda:session.readSnapshot().connection=='INVALID_RESULT')
        hub.tick()
        assert not window.displayed
        assert 'INVALID_RESULT' in window.widgets['overview']['overview-count'][1].text()
    finally:
        window.close()


def testIncompatibleCapabilitiesDoNotAppearHealthy(qtApp,monkeypatch):
    from emo_master.apps.runtime.presentation.rpc import DisplayRpc
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    monkeypatch.setattr(DisplayRpc,'Capabilities',lambda self,request,context:pb.DisplayCapabilities(protocol_version='incompatible'))
    backend=LocalDemo(count=2)
    session=DisplaySession(backend.address,backend.jobId)
    window=RuntimePages(backend.project.presentation,hub=DisplayHub(session))
    window.show()
    try:
        until(qtApp,lambda:session.readSnapshot().connection=='INCOMPATIBLE')
        window.hub.tick()
        assert 'INCOMPATIBLE' in window.status.text() and not window.displayed
        assert len(backend.presentation.jobs)==1
    finally:
        window.close()
        session.close()
        backend.close()


def testBorrowedLauncherClosesOwnSessionWithoutStartingOrStoppingRuntime(qtApp,live,monkeypatch):
    from scripts.p3_demo import Launcher
    backend,_session=live
    def forbidden(*args,**kwargs):
        raise AssertionError('read-only viewer attempted to start a Job')
    monkeypatch.setattr(backend.presentation,'start',forbidden)
    launcher=Launcher(backend.address,backend.jobId,backend.project)
    launcher.show()
    until(qtApp,lambda:launcher.hub and any(w.displayed for w in launcher.hub.windows))
    assert '只读观察已有任务' in launcher.message.text()
    # Repeated windows must retire, with only current observers held by the hub.
    for _ in range(3):
        window=next(iter(launcher.hub.windows))
        window.close()
        launcher.openWindow()
        assert len(launcher.hub.windows)==1
    launcher.close()
    until(qtApp,lambda:launcher.done)
    assert not backend.runtime._closed and len(backend.presentation.jobs)==1
    assert all(not thread.is_alive() for thread in launcher.session.threads)
