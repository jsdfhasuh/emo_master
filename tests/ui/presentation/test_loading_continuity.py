from dataclasses import replace
from types import SimpleNamespace

import pytest

from emo_master.core.presentation.results import ClosedSource
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages
from examples.runtime_pages_p3 import sampleProjectP3
from tests.ui.presentation.test_latest_sampling_contract import view


def loading(state, phase):
    scope = state.scopes['root']
    if phase == 'result':
        return replace(state, scopes={}, loading={'root': scope.result})
    return replace(state, scopes={'root': replace(scope, images={}, imageStates={'image': 'LOADING'})})


@pytest.fixture
def window(qtApp, tmp_path):
    window = RuntimePages(sampleProjectP3(tmp_path).presentation)
    window.show()
    try:
        yield window
    finally:
        window.close()


@pytest.mark.parametrize('phase', ['result', 'image'])
def testLoadingNextImageKeepsWholeDisplayedResultUntilReady(window, phase, qtApp):
    first, second = view(window, 1), view(window, 2)
    window.submit(first)
    image = window.widgets['overview']['overview-image'][1]
    count = window.widgets['overview']['overview-count'][1]
    pixels = image.image.cacheKey()
    records = len(window.records)
    pending = loading(second, phase)
    for _ in range(3):
        window.submit(pending)
        qtApp.processEvents()
        assert not image.image.isNull() and image.image.cacheKey() == pixels
        assert image.key == 'result-1' and count.text() == '1 个'
        assert window.displayed['root'] is first.scopes['root']
        assert window.lastView is pending
    assert len(window.records) == records
    assert '更新' in window.modeLabel.text()
    window.submit(second)
    assert image.key == 'result-2' and image.image.pixelColor(0, 0).red() == 2
    assert count.text() == '2 个' and window.displayed['root'] is second.scopes['root']
    assert '更新' not in window.modeLabel.text()


@pytest.mark.parametrize('phase', ['result', 'image'])
def testInitialLoadingStillShowsPlaceholder(window, phase):
    window.submit(loading(view(window, 1), phase))
    image = window.widgets['overview']['overview-image'][1]
    assert image.image.isNull() and '准备中' in image.message


@pytest.mark.parametrize('change', ['disconnect', 'generation', 'revision', 'expired', 'scope_error', 'failed'])
def testLoadingNeverMasksInvalidatedOrFailedResult(window, change):
    window.submit(view(window, 1))
    current = view(window, 2)
    if change == 'revision':
        scope = current.scopes['root']
        identity = scope.result.identity.model_copy(update={'executionRevision': 'c' * 64})
        current = replace(current, scopes={'root': replace(scope, result=scope.result.model_copy(update={'identity': identity}))})
    elif change == 'failed':
        scope = current.scopes['root']
        failed = ClosedSource(sourceId='image', state='UNAVAILABLE', reason='failed', reasonCode='NODE_FAILED')
        result = scope.result.model_copy(update={'sources': (scope.result.sources[0], failed),
                                                'status': 'FAILED', 'executionTerminal': 'FAILED'})
        current = replace(current, scopes={'root': replace(scope, result=result)})
    pending = loading(current, 'result')
    if change == 'disconnect':
        pending = replace(pending, connection='DISCONNECTED', detail='lost connection')
    elif change == 'generation':
        pending = replace(pending, generation=pending.generation + 1)
    elif change == 'expired':
        pending = replace(pending, expiredScopes={'root': 2})
    elif change == 'scope_error':
        pending = replace(pending, scopeErrors={'root': 'SOURCE_MISSING'})
    window.submit(pending)
    assert window.widgets['overview']['overview-image'][1].image.isNull()
    assert 'root' not in window.displayed


def testImageReadFailureReplacesRetainedFrameWithExplicitError(window):
    window.submit(view(window, 1))
    second = view(window, 2)
    window.submit(loading(second, 'image'))
    failed = replace(second.scopes['root'], images={}, failures={'image': 'RESOURCE_EXPIRED'}, imageStates={})
    window.submit(replace(second, scopes={'root': failed}))
    image = window.widgets['overview']['overview-image'][1]
    assert image.image.isNull() and '过期' in image.message
    assert window.displayed['root'] is failed
    assert window.widgets['overview']['overview-count'][1].text() == '2 个'


def testNavigationDoesNotReuseImageFromPreviousPage(window):
    window.submit(view(window, 1))
    window.submit(loading(view(window, 2), 'result'))
    window.navigate('detail')
    assert not window.displayed
    assert '准备中' in window.widgets['detail']['detail-count'][1].text()


def testNumbersOnlyPageDoesNotWaitForImageRead(window):
    config = window.config.model_dump()
    config['pages']['overview']['components'] = [component for component in config['pages']['overview']['components']
                                                if component['type'] != 'image']
    window.reload(type(window.config).model_validate(config))
    window.submit(view(window, 1))
    window.submit(loading(view(window, 2), 'image'))
    assert window.widgets['overview']['overview-count'][1].text() == '2 个'
    assert window.displayed['root'].result.identity.resultKey == 'result-2'


def withSecondImage(state):
    scope = state.scopes['root']
    source = scope.result.sources[-1]
    second = source.model_copy(update={'sourceId': 'second',
        'image': source.image.model_copy(update={'resourceId': source.image.resourceId + '-second'})})
    result = scope.result.model_copy(update={'sources': (*scope.result.sources, second),
                                             'expectedSourceIds': (*scope.result.expectedSourceIds, 'second')})
    return replace(state, scopes={'root': replace(scope, result=result,
        images={**scope.images, 'second': scope.images['image'].copy()})})


def testTwoWindowsSwapWithinExistingImageBudgetWithoutBlankPaints(qtApp, tmp_path):
    config = sampleProjectP3(tmp_path).presentation
    component = next(component for component in config.pages['overview'].components if component.type == 'image')
    config.dataSources['second'] = config.dataSources['image'].model_copy(update={'port': 'secondImage'})
    config.pages['overview'].components.append(component.model_copy(update={
        'componentId': 'second-image', 'bindings': {'image': 'second'},
        'layout': component.layout.model_copy(update={'row': 5})}))
    current = [None]
    first = RuntimePages(config)
    current[0] = withSecondImage(view(first, 1))
    session = SimpleNamespace(readSnapshot=lambda: current[0], imageDemand=False)
    hub = DisplayHub(session)
    hub.attach(first)
    first.hub = hub
    second = RuntimePages(config, hub=hub)
    try:
        first.show()
        second.show()
        hub.timer.stop()
        # Scale the 16 MiB live + 8 MiB conversion allowance to tiny test images.
        hub.imageLimit = current[0].scopes['root'].images['image'].nbytes * 3
        hub.tick()
        nextView = withSecondImage(view(first, 2))
        current[0] = loading(nextView, 'result')
        hub.tick()
        for window in (first, second):
            assert window.widgets['overview']['overview-image'][1].key == 'result-1'
        conversions = hub.conversions
        current[0] = nextView
        hub.tick()
        qtApp.processEvents()
        for window in (first, second):
            for name in ('overview-image', 'second-image'):
                image = window.widgets['overview'][name][1]
                assert image.key == 'result-2' and not image.image.isNull(), image.message
        assert hub.conversions == conversions + 2
        assert hub.imageBytes() <= hub.imageLimit
    finally:
        first.close()
        second.close()
