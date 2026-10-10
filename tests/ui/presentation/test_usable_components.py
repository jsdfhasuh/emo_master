"""Explicit empty/fault semantics, offline modes, and bounded Qt appearance."""
from dataclasses import replace
from types import SimpleNamespace
import math

import pytest

from emo_master.core.presentation.models import Props, Action
from emo_master.core.presentation.results import ClosedSource
from emo_master.ui.presentation.renderer import RuntimePages, surfaceBounds, SURFACE_LIMIT
from emo_master.ui.presentation.table import CollectionView
from examples.runtime_pages_p3 import sampleProjectP3
from tests.ui.presentation.test_renderer import resultView


@pytest.mark.parametrize('kind', ['number', 'text', 'indicator'])
def testLegalNullUsesEmptyTextButZeroFalseAndMissingDoNot(qtApp, tmp_path, kind):
    config = sampleProjectP3(tmp_path).presentation
    component = next(c for c in config.pages['overview'].components if c.type == 'number')
    component.type = kind
    component.props = Props(emptyText='未检测', indicatorStates={'false': {'text': 'NG', 'color': 'red'}})
    window = RuntimePages(config)
    widget = window.widgets['overview'][component.componentId][1]
    window.submit(resultView(count='null', capture=window.expectedCapture))
    assert widget.text() == '未检测'
    window.submit(resultView(2, count='false' if kind == 'indicator' else '0', capture=window.expectedCapture))
    assert widget.text() == ('NG' if kind == 'indicator' else '0')
    view = resultView(3, capture=window.expectedCapture)
    scope = view.scopes['root']
    for reason in ('OPTIONAL_ABSENT', 'BRANCH_SKIPPED', 'NODE_FAILED', 'RESOURCE_EXPIRED', 'EXPORT_TIMEOUT'):
        result = scope.result.model_copy(update={'sources': (ClosedSource(sourceId='count', state='UNAVAILABLE',
            reasonCode=reason, reason=reason),), 'status': 'INCOMPLETE'})
        window.submit(replace(view, scopes={'root': replace(scope, result=result)}))
        assert reason in widget.text() and '未检测' not in widget.text()
    window.submit(replace(view, connection='DISCONNECTED', detail='transport lost'))
    assert 'DISCONNECTED' in widget.text() and 'transport lost' in widget.text()
    window.close()


def testEmptyCollectionAndNullRemainDifferentFromFault(qtApp):
    table = CollectionView(Props(emptyText='未发现目标', columns=[{'title': 'value', 'fieldPath': []}]))
    table.submit([], 'empty', '')
    assert table.message.text() == '未发现目标 · 0 行'
    table.submit(None, 'null', '')
    assert table.message.text() == '未发现目标 · null'
    table.submit([0, False], 'values', '')
    assert [table.model.data(table.model.index(i, 0)) for i in range(2)] == ['0', 'false']
    table.submit([], 'broken', 'SOURCE_MISSING')
    assert table.message.text() == 'SOURCE_MISSING'
    table.close()


@pytest.mark.parametrize('width,height,ratio', [(1920,1080,1), (3440,1440,1), (3840,2160,2), (1080,1920,1.5)])
def testScreenAspectFitsUnchangedSurfaceReservation(width, height, ratio):
    actual = surfaceBounds(width, height, ratio)
    assert math.ceil(actual[0] * ratio) * math.ceil(actual[1] * ratio) * 4 <= SURFACE_LIMIT
    assert actual[0] <= width and actual[1] <= height
    if (width, height, ratio) == (1920, 1080, 1):
        assert actual == (1920, 1080)  # no old 1600x1000 product assumption


def testAppearanceAppliesAndSurfaceIsAccounted(qtApp, tmp_path):
    config = sampleProjectP3(tmp_path).presentation
    component = next(c for c in config.pages['overview'].components if c.type == 'number')
    component.props.fontSize = 32
    component.props.fontFamily = 'monospace'
    component.props.cardStyle = 'soft'
    component.props.textColor = 'amber'
    window = RuntimePages(config)
    window.show()
    qtApp.processEvents()
    widget = window.widgets['overview'][component.componentId][1]
    assert widget.font().pixelSize() == 32
    assert '#925900' in widget.styleSheet()
    assert '#edf3fa' in widget.parentWidget().styleSheet()
    assert window.surfaceBytes() <= SURFACE_LIMIT
    window.close()


def testDetailActionLocksActualGuiIdentityNotLatest(qtApp, tmp_path):
    window = RuntimePages(sampleProjectP3(tmp_path).presentation)
    displayed = resultView(1, capture=window.expectedCapture)
    latest = resultView(2, capture=window.expectedCapture)
    pinned = []
    pinStore = SimpleNamespace(read=lambda ticket: SimpleNamespace(state='PINNED', scope=pinned[0], error=''))
    session = SimpleNamespace(readSnapshot=lambda: latest, pins=lambda: pinStore)
    def freeze(owner, scope, generation, *, sourceIds=None):
        pinned.append(scope)
        return 'pin'
    hub = SimpleNamespace(freeze=freeze, session=session, resume=lambda owner: None, detach=lambda owner: None,
                          updateImageDemand=lambda: None)
    window.submit(displayed)
    window.hub = hub
    window.act(Action(type='navigate', pageId='detail', context='displayed_result', resultScopeId='root'))
    assert pinned[0].result.identity.resultKey == 'result-1'
    assert window.displayed['root'].result.identity.resultKey == 'result-1'
    with pytest.raises(ValueError, match='断开'):
        window.setSimulationState('OK')
    window.resumeLive()
    assert window.displayed['root'].result.identity.resultKey == 'result-2'
    window.close()


def testVisibleDpiAndScreenSignalsReapplySurfaceBudget(qtApp, monkeypatch):
    from PySide2.QtCore import QRect
    from emo_master.core.presentation.models import Presentation
    screen = qtApp.primaryScreen()
    monkeypatch.setattr(screen, 'availableGeometry', lambda: QRect(0, 0, 1920, 1080))
    ratio = [1.]
    window = RuntimePages(Presentation())
    monkeypatch.setattr(window, 'devicePixelRatioF', lambda: ratio[0])
    window.show()
    window.fitToAvailableScreen()
    assert (window.width(), window.height()) == (1920, 1080)
    ratio[0] = 2.
    window.windowHandle().screenChanged.emit(screen)
    qtApp.processEvents()
    assert window.surfaceBytes() <= SURFACE_LIMIT
    assert window.width() < 1920
    ratio[0] = 3.
    screen.logicalDotsPerInchChanged.emit(288.)
    qtApp.processEvents()
    assert window.surfaceBytes() <= SURFACE_LIMIT
    assert math.ceil(window.maximumWidth() * 3) * math.ceil(window.maximumHeight() * 3) * 4 <= SURFACE_LIMIT
    window.close()


def testImageAliasesShareOneQtConversionButDistinctAssetsDoNot(qtApp):
    import numpy as np
    from emo_master.clients.runtime.view_state import ScopeView
    from emo_master.core.presentation.results import ResultIdentity, ClosedResult, ImageRef, FrameProvenance
    from emo_master.ui.presentation.hub import DisplayHub
    identity = ResultIdentity(runtimeInstanceId='r', jobId='j', resultScopeId='root', invocationId='i',
        resultKey='result-alias', resultOrdinal=1, executionRevision='a'*64, capturePlanRevision='b'*64, mode='runtime')
    def asset(resource):
        return ImageRef(resourceId=resource, ownerResultKey=identity.resultKey, byteSize=64, sha256='c'*64,
            mimeType='image/png', provenance=FrameProvenance(frameIdentity=resource, coordinateSpaceId=resource, trust='unknown'))
    shared = asset('same-image')
    sources = tuple(ClosedSource(sourceId=f'alias{i}', state='AVAILABLE', image=shared) for i in range(4)) + (
        ClosedSource(sourceId='different', state='AVAILABLE', image=asset('another-image')),)
    result = ClosedResult(identity=identity, expectedSourceIds=tuple(s.sourceId for s in sources), sources=sources,
        status='COMPLETE', executionTerminal='COMPLETED')
    # A full8MiB decoded plane; four aliases previously exceeded24MiB Qt budget.
    pixels = np.frombuffer(bytes(8*1024*1024), dtype=np.uint8).reshape(2048, 4096)
    scope = ScopeView(result, {s.sourceId: pixels for s in sources}, {}, 0)
    hub = DisplayHub(SimpleNamespace())
    images = [hub.image(scope, f'alias{i}', pixels) for i in range(4)]
    assert hub.conversions == 1 and len({image.cacheKey() for image in images}) == 1
    assert hub.imageBytes() == 8*1024*1024
    separate = hub.image(scope, 'different', pixels)
    assert separate.cacheKey() != images[0].cacheKey()
    assert hub.conversions == 2 and hub.imageBytes() == 16*1024*1024
    assert hub.imageBytes() <= hub.imageLimit


def testConfiguredTextCannotLoadUnaccountedRichTextImages(qtApp, tmp_path):
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QLabel
    config = sampleProjectP3(tmp_path).presentation
    component = next(c for c in config.pages['overview'].components if c.type == 'number')
    component.type = 'text'
    component.bindings = {}
    component.props.text = '<img src="file:///not-a-runtime-asset.png">'
    component.props.title = '<b>plain title</b>'
    window = RuntimePages(config)
    widget = window.widgets['overview'][component.componentId][1]
    assert widget.textFormat() == Qt.PlainText and widget.text() == component.props.text
    assert all(label.textFormat() == Qt.PlainText for label in widget.parentWidget().findChildren(QLabel))
    window.close()


def testExpiredScopeIsVisibleAndAnExpiryOnlyUpdateReachesGui(qtApp, tmp_path):
    from emo_master.ui.presentation.hub import DisplayHub
    config = sampleProjectP3(tmp_path).presentation
    current = [SimpleNamespace(**{**resultView().__dict__, 'scopes': {}, 'expiredScopes': {}})]
    session = SimpleNamespace(readSnapshot=lambda: current[0])
    hub = DisplayHub(session)
    window = RuntimePages(config, hub=hub)
    window.show()
    hub.timer.stop()
    hub.tick()
    widget = window.widgets['overview']['overview-count'][1]
    assert '等待触发' in widget.text()
    current[0] = SimpleNamespace(**{**current[0].__dict__, 'expiredScopes': {'root': 1}})
    hub.tick()
    assert 'RESOURCE_EXPIRED' in widget.text() and not window.displayed
    current[0] = resultView(2, count='0', capture=window.expectedCapture)
    hub.tick()
    assert widget.text() == '0 个'
    window.close()
