"""Display adaptation freezes pixels without changing business images."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter, inspectionPreview


@pytest.mark.parametrize('shape', [(1440, 2560, 3), (2160, 3840, 3)])
def testNoisyLargeImageFitsEncodedAndDecodedBudgetsWithoutChangingOriginal(shape):
    image = np.random.default_rng(7).integers(0, 256, shape, dtype=np.uint8)
    original = image.copy()
    initial = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.png', initial)
    assert ok and encoded.nbytes > 4 * 1024 * 1024
    payload, width, height = inspectionPreview(image)
    displayed = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    assert len(payload) <= 4 * 1024 * 1024 and width * height * 4 <= 8 * 1024 * 1024
    assert 0 < width < 1920 and 0 < height < 1080
    assert width / height == pytest.approx(shape[1] / shape[0], rel=.002)
    assert displayed.shape == (height, width, 3)
    assert np.array_equal(image, original)


def testPublishedPixelsAreFrozenBeforeDownstreamMutation(tmp_path):
    events = []
    writer = PreviewSnapshotWriter(tmp_path, jobId='job', projectRevision=7,
                                   inspectionPublisher=lambda **event: events.append(event))
    node = SimpleNamespace(nodeId='image', outputPorts={'image': 'image'})
    context = SimpleNamespace(workflowId='main', workflowRunId='flow', nodeRunId='first', iterationPath=(0,))
    image = np.zeros((12, 16, 3), np.uint8)
    image[..., 2] = 230
    writer.capture(node, {'image': image}, context)
    image[:] = 17
    entry = events[0]['payload']['entries'][0]
    path = writer.inspectionRoot / entry['relativePath']
    displayed = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    assert (entry['width'], entry['height']) == (16, 12)
    assert (entry['originalWidth'], entry['originalHeight']) == (16, 12)
    assert np.all(displayed[..., :2] == 0) and np.all(displayed[..., 2] == 230)
    legacy = writer.root / writer._entries[('main', 'image', 'image')]['relativePath']
    assert np.array_equal(cv2.imdecode(np.fromfile(legacy, np.uint8), cv2.IMREAD_COLOR), displayed)
    assert events[0]['eventType'] == 'inspection.snapshot' and entry['nodeRunId'] == 'first'
