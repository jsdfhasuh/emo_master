"""Live result refreshes should invalidate changed content, not the whole window."""
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace
import time

import numpy as np
import pytest
from PySide2.QtCore import QEvent, QObject
from PySide2.QtGui import QImage

from emo_master.clients.runtime.view_state import ScopeView, SessionView
from emo_master.core.presentation.models import Presentation
from emo_master.core.presentation.results import ClosedResult, ClosedSource, ImageRef, ResultIdentity
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import ImageView, RuntimePages


def livePresentation():
    sources = {
        name: dict(kind='node_output', resultScopeId='root', workflowId='main',
                   nodeId='detect', port=name, expectedType=kind)
        for name, kind in [('image', 'image'), ('overlay', 'image'),
                           ('count', 'integer'), ('ok', 'boolean')]
    }
    return Presentation.model_validate(dict(
        defaultPageId='main', pageOrder=['main'], dataSources=sources,
        resultScopes={'root': dict(entryWorkflowId='main', scopeWorkflowId='main')},
        pages={'main': dict(name='Live preview', layout={'columns': 2}, resultScopeIds=['root'], components=[
            dict(componentId='image', type='image', bindings={'image': 'image'},
                 layout={'row': 0, 'column': 0}),
            dict(componentId='overlay', type='image', bindings={'image': 'overlay'},
                 layout={'row': 0, 'column': 1}),
            dict(componentId='count', type='number', bindings={'value': 'count'},
                 layout={'row': 1, 'column': 0}),
            dict(componentId='ok', type='indicator', bindings={'value': 'ok'},
                 props={'indicatorStates': {'true': dict(text='OK', color='green'),
                                            'false': dict(text='NG', color='red')}},
                 layout={'row': 1, 'column': 1}),
            dict(componentId='static', type='text', props={'text': 'Static reference'},
                 layout={'row': 2, 'column': 0, 'columnSpan': 2}),
        ])}))


def liveView(window, ordinal, *, pixels=None, ok=True):
    if pixels is None:
        pixels = np.full((24, 32, 3), ordinal % 256, dtype=np.uint8)
    key = f'result-{ordinal}'
    sources = tuple(ClosedSource(sourceId=name, state='AVAILABLE', image=ImageRef(
        resourceId=f'{name}-{ordinal}', ownerResultKey=key, byteSize=pixels.nbytes,
        sha256=f'{ordinal:064x}', mimeType='image/png',
        provenance=dict(frameIdentity=key, coordinateSpaceId='pixels', trust='unknown')))
        for name in ('image', 'overlay'))
    result = ClosedResult(identity=ResultIdentity(
        runtimeInstanceId='runtime', jobId='job', resultScopeId='root', invocationId=str(ordinal),
        resultKey=key, resultOrdinal=ordinal, executionRevision='a' * 64,
        capturePlanRevision=window.expectedCapture, mode='debug'),
        expectedSourceIds=('image', 'overlay', 'count', 'ok'),
        sources=(*sources, ClosedSource(sourceId='count', state='AVAILABLE', valueJson=str(ordinal)),
                 ClosedSource(sourceId='ok', state='AVAILABLE', valueJson='true' if ok else 'false')),
        status='COMPLETE', executionTerminal='COMPLETED')
    scope = ScopeView(result, {'image': pixels, 'overlay': pixels}, {}, time.perf_counter_ns())
    return SessionView(ordinal, 1, 'runtime', 'job', 'CONNECTED', '',
                       {'root': scope}, {}, {'root': ordinal})


@pytest.fixture
def liveWindow(qtApp):
    current = [SessionView(0, 1, 'runtime', 'job', 'CONNECTED', '', {}, {}, {})]
    session = SimpleNamespace(readSnapshot=lambda: current[0], imageDemand=False)
    hub = DisplayHub(session)
    window = RuntimePages(livePresentation(), hub=hub)
    window.show()
    hub.timer.stop()
    for ordinal in (1, 2):
        current[0] = liveView(window, ordinal)
        hub.tick()
        qtApp.processEvents()
        qtApp.processEvents()
    yield window, current
    window.close()
    hub.deleteLater()


class Events(QObject):
    def __init__(self, widget):
        super().__init__(widget)
        self.counts = Counter()
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Paint, QEvent.StyleChange):
            self.counts[event.type()] += 1
        return False


@pytest.mark.parametrize('change', ['same', 'key', 'pixels', 'message', 'clear'])
def testImageUpdateOnlyWhenDisplayChanges(qtApp, monkeypatch, change):
    widget = ImageView()
    image = QImage(32, 24, QImage.Format_RGB32)
    image.fill(0xff123456)
    widget.setImage(image, 'first')
    updates = []
    monkeypatch.setattr(widget, 'update', lambda: updates.append(True))
    nextImage, key, message = QImage(image), 'first', ''
    if change == 'key':
        key = 'second'
    elif change == 'pixels':
        nextImage.fill(0xff654321)
    elif change == 'message':
        message = 'changed status'
    elif change == 'clear':
        nextImage = QImage()
    widget.setImage(nextImage, key, message)
    assert len(updates) == (0 if change == 'same' else 1)
    assert (widget.image.cacheKey(), widget.key, widget.message) == (nextImage.cacheKey(), key, message)


def testNewFramesDoNotRepaintStaticContent(liveWindow, qtApp):
    window, current = liveWindow
    static = window.widgets['main']['static'][1]
    events = Events(static)
    for ordinal in range(3, 9):
        current[0] = liveView(window, ordinal)
        window.hub.tick()
        qtApp.processEvents()
        qtApp.processEvents()
    assert static.isVisible()
    assert window.displayed['root'].result.identity.resultOrdinal == 8
    assert events.counts[QEvent.Paint] == 0


def testUnchangedIndicatorStyleIsNotReapplied(liveWindow):
    window, current = liveWindow
    indicator = window.widgets['main']['ok'][1]
    events = Events(indicator)
    current[0] = liveView(window, 3)
    window.hub.tick()
    assert events.counts[QEvent.StyleChange] == 0
    current[0] = liveView(window, 4, ok=False)
    window.hub.tick()
    assert events.counts[QEvent.StyleChange] > 0
    assert indicator.text() == 'NG'
    assert '#af2734' in indicator.styleSheet()


def testHealthUpdateReusesImagesWithoutRequestingPaint(liveWindow, monkeypatch):
    window, current = liveWindow
    updates = []
    for name in ('image', 'overlay'):
        monkeypatch.setattr(window.widgets['main'][name][1], 'update', lambda: updates.append(True))
    conversions = window.hub.conversions
    current[0] = replace(current[0], revision=3, detail='health updated')
    window.hub.tick()
    assert not updates
    assert window.hub.conversions == conversions
    assert 'health updated' in window.status.text()


@pytest.mark.parametrize('reason,custom', [('OPTIONAL_ABSENT', True), ('BRANCH_SKIPPED', True),
                                        ('NODE_FAILED', False), ('EXPORT_TIMEOUT', False)])
def testImageEmptyTextDoesNotHideFailures(qtApp, reason, custom):
    config = livePresentation()
    config.pages['main'].components[0].props.emptyText = 'No face in this result'
    window = RuntimePages(config)
    try:
        view = liveView(window, 1)
        window.submit(view)
        scope = view.scopes['root']
        source = ClosedSource(sourceId='image', state='UNAVAILABLE', reason=reason, reasonCode=reason)
        result = scope.result.model_copy(update={'sources': (source, *scope.result.sources[1:]), 'status': 'INCOMPLETE'})
        window.submit(replace(view, scopes={'root': replace(scope, result=result, images={})}))
        widget = window.widgets['main']['image'][1]
        assert widget.image.isNull()
        assert (widget.message == 'No face in this result') == custom
        assert widget.toolTip() == reason
    finally:
        window.close()


def testSpanningCameraDoesNotShrinkCloseupBehindScalarRows(qtApp, monkeypatch):
    from PySide2.QtCore import QRect
    from emo_master.core.presentation.models import Placement
    screen = qtApp.primaryScreen()
    monkeypatch.setattr(screen, 'availableGeometry', lambda: QRect(0, 0, 1920, 1080))
    config = livePresentation()
    page = config.pages['main']
    page.layout.columns = 12
    page.components = page.components[:4]
    for component, position in zip(page.components, [
            dict(row=0, column=0, rowSpan=4, columnSpan=8),
            dict(row=0, column=8, rowSpan=3, columnSpan=4),
            dict(row=3, column=8, columnSpan=2), dict(row=3, column=10, columnSpan=2)]):
        component.layout = Placement(**position)
    window = RuntimePages(config)
    try:
        window.submit(liveView(window, 1))
        window.show()
        for width, height in [(1280, 800), (1024, 768)]:
            window.resize(width, height)
            qtApp.processEvents()
            assert (window.width(), window.height()) == (width, height)
            camera = window.widgets['main']['image'][1].parentWidget()
            closeup = window.widgets['main']['overlay'][1].parentWidget()
            count = window.widgets['main']['count'][1].parentWidget()
            assert closeup.height() > camera.height() * .6
            assert count.height() < camera.height() * .4
            assert not window.pages['main'].horizontalScrollBar().maximum()
    finally:
        window.close()


@pytest.mark.parametrize('motion', ['move', 'resize'])
def testLiveFramesPaintConsistentValuesDuringWindowMotion(liveWindow, qtApp, motion):
    window, current = liveWindow
    painted = []
    def onPaint(key, stamp):
        painted.append((key, window.widgets['main']['count'][1].text(),
                        window.displayed['root'].result.identity.resultKey,
                        window.widgets['main']['overlay'][1].key))
        window._painted(key, stamp)
    window.widgets['main']['image'][1].painted = onPaint
    for ordinal in range(3, 9):
        current[0] = liveView(window, ordinal)
        window.hub.tick()
        if motion == 'move':
            window.move(80 + ordinal * 4, 70 + ordinal * 3)
        else:
            window.resize(900 + ordinal * 6, 600 + ordinal * 4)
        qtApp.processEvents()
        qtApp.processEvents()
    assert painted
    for key, count, shown, overlay in painted:
        assert key == shown == overlay == f'result-{count}'
    assert painted[-1][0] == 'result-8'
    assert window.widgets['main']['image'][1].image.pixelColor(0, 0).red() == 8
    assert any(record.get('paint_ns') for record in window.records if record['key'] == 'result-8')
