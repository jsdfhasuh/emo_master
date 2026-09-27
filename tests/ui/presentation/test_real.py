import time

from examples.runtime_pages_p3 import LocalDemo
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages


def until(app, condition, timeout=20):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(.01)
    raise AssertionError('Qt condition deadline')


def testRealAlternatingImagesOneJobTwoPagesAndSharedWindows(qtApp):
    backend=LocalDemo(count=4)
    session=DisplaySession(backend.address,backend.jobId)
    windows=[]
    try:
        until(qtApp,lambda:bool(session.readSnapshot().scopes))
        hub=DisplayHub(session)
        a=RuntimePages(backend.project.presentation,hub=hub)
        windows.append(a)
        a.show()
        until(qtApp,lambda:bool(a.displayed))
        assert a.widgets['overview']['overview-count'][1].text()=='2 个'
        first=a.displayed['root'].result.identity.resultKey
        assert not a.widgets['overview']['overview-image'][1].image.isNull()
        converted=hub.conversions
        a.navigate('detail')
        assert a.displayed['root'].result.identity.resultKey==first
        b=RuntimePages(backend.project.presentation,hub=hub)
        windows.append(b)
        b.show()
        assert hub.conversions==converted
        until(qtApp,lambda:a.widgets['detail']['detail-count'][1].text()=='3 个')
        assert a.displayed['root'].result==b.displayed['root'].result
        assert len(backend.presentation.jobs)==1
        assert session.stats['decoded']==2
        assert a.records[-1]['gui_ns']>=a.displayed['root'].readyNs
        a.close()
        assert not backend.runtime._closed and len(hub.windows)==1
        until(qtApp,lambda:b.widgets['overview']['overview-count'][1].text()=='2 个')
    finally:
        for window in windows:
            window.close()
        session.close()
        backend.close()
