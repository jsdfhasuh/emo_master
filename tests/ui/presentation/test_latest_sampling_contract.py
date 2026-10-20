"""Controlled GUI schedules, not a reconstruction of historic Windows gaps."""
from dataclasses import replace
import hashlib
from types import SimpleNamespace
import time

import numpy as np

from emo_master.core.presentation.results import ClosedResult, ClosedSource, FrameProvenance, ImageRef
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages
from examples.runtime_pages_p3 import sampleProjectP3
from tests.ui.presentation.test_real import until
from tests.ui.presentation.test_renderer import resultView


def view(window, ordinal):
    pixels = np.full((8, 12, 3), ordinal, dtype=np.uint8)
    state = resultView(ordinal, str(ordinal), image=pixels, capture=window.expectedCapture)
    result = state.scopes['root'].result
    ref = ImageRef(resourceId=f'image-{ordinal}', ownerResultKey=result.identity.resultKey,
        byteSize=pixels.nbytes, sha256=hashlib.sha256(pixels.tobytes()).hexdigest(), mimeType='image/png',
        provenance=FrameProvenance(frameIdentity=f'frame-{ordinal}', coordinateSpaceId='pixels', trust='unknown'))
    result = ClosedResult(identity=result.identity, expectedSourceIds=('count', 'image'),
        sources=(*result.sources, ClosedSource(sourceId='image', state='AVAILABLE', image=ref)),
        status='COMPLETE', executionTerminal='COMPLETED')
    return replace(state, scopes={'root': replace(state.scopes['root'], result=result)})


def testHubSamplesLatestRatherThanArchivingEveryAppliedView(qtApp, tmp_path):
    config = sampleProjectP3(tmp_path).presentation
    current = [resultView(capture='b' * 64)]
    session = SimpleNamespace(readSnapshot=lambda: current[0], imageDemand=False)
    hub = DisplayHub(session)
    first = RuntimePages(config, hub=hub)
    second = RuntimePages(config, hub=hub)
    try:
        first.show()
        second.show()
        until(qtApp, lambda: first.isVisible() and second.isVisible())
        hub.timer.stop()  # Explicit schedule, not a slower production timer.
        current[0] = view(first, 1)
        hub.tick()
        for window in (first, second):
            window.records.clear()
        current[0] = view(first, 2)  # Applied to the session between GUI ticks.
        current[0] = view(first, 3)
        hub.tick()
        for window in (first, second):
            assert window.displayed['root'].result.identity.resultOrdinal == 3
            assert {r['key'] for r in window.records} == {'result-3'}
            assert window.hub is hub
        assert hub.coalesced == 1
        assert len(hub.windows) == 2
    finally:
        first.close()
        second.close()
        hub.timer.stop()


def testTwoCommitsBeforePaintRetainBothCommitsButOnlyPaintLatest(qtApp, tmp_path):
    window = RuntimePages(sampleProjectP3(tmp_path).presentation)
    try:
        window.show()
        window.submit(view(window, 1))
        until(qtApp, lambda: any(r.get('paint_ns') for r in window.records if r['key'] == 'result-1'))
        window.records.clear()
        # No event processing between these synchronous GUI commits. QWidget
        # update() is queued; the second image may replace the first before paint.
        window.submit(view(window, 2))
        window.submit(view(window, 3))
        assert {r['key'] for r in window.records} == {'result-2', 'result-3'}
        assert not any(r.get('paint_ns') for r in window.records)
        until(qtApp, lambda: any(r.get('paint_ns') for r in window.records if r['key'] == 'result-3'))
        assert not any(r.get('paint_ns') for r in window.records if r['key'] == 'result-2')
        assert window.displayed['root'].result.identity.resultOrdinal == 3
        image = window.widgets['overview']['overview-image'][1]
        assert image.key == 'result-3' and image.image.pixelColor(0, 0).red() == 3
        assert all(r['ready_ns'] <= r['gui_ns'] <= r.get('paint_ns', time.perf_counter_ns()) for r in window.records)
    finally:
        window.close()
