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


def testLateDecodeCannotRestorePreviousJob(qtApp,live,monkeypatch):
    from emo_master.clients.runtime import display_session as module
    backend,session=live
    entered=threading.Event()
    release=threading.Event()
    decode=module.decodePng
    def delayed(content):
        entered.set()
        if not release.wait(3):
            raise ValueError('test decode gate expired')
        return decode(content)
    monkeypatch.setattr(module,'decodePng',delayed)
    hub=DisplayHub(session)
    window=RuntimePages(backend.project.presentation,hub=hub)
    window.show()
    try:
        until(qtApp,entered.is_set)
        generation=session.readSnapshot().generation
        session.selectJob('missing-job')
        release.set()
        until(qtApp,lambda:session.readSnapshot().connection=='NOT_FOUND')
        hub.tick()
        assert session.readSnapshot().generation>generation
        assert not window.displayed and not session.readSnapshot().scopes
        assert any(not row['applied_to_live'] for row in session.records)
        session.selectJob(backend.jobId)
        until(qtApp,lambda:bool(window.displayed))
    finally:
        release.set()
        window.close()


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
        window.resumeLive()
        assert window.displayed
    finally:
        window.close()
