"""Latest-only read/decode ownership, late delivery and honest capability gaps."""
from types import SimpleNamespace
import threading

import pytest

from emo_master.apps.designer.services.inspection_images import InspectionImages, ImageSelection
from emo_master.apps.designer.ui.node_image_decode import decodeNodeImage
from emo_master.apps.runtime.preview.image_headers import imageHeader


def selection(port='image'):
    return ImageSelection('project', 'job', 'main', 'node', 'flow', 'invocation', 7, port)


class Client:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.exited = threading.Event()
        self.calls = []
        self.closed = []

    def inspectionSession(self, action, project, session=''):
        if action == 'close':
            self.closed.append(session)
        return SimpleNamespace(session_id='session', runtime_instance_id='runtime')

    def listNodePreviewSourcesWithMetadata(self, *args, **kwargs):
        source = SimpleNamespace(port='image', originJobId='job', originProjectRevision=7,
            workflowRunId='flow', nodeRunId='invocation', captureId='capture', workflowId='main',
            nodeId='node', sourceId='asset', width=1, height=1)
        return SimpleNamespace(sources=(source,), captureState='CURRENT_AVAILABLE', message='')

    def readInspectionAsset(self, project, session, asset, context):
        self.calls.append(asset)
        self.started.set()
        # Cancellation is requested, but actual blocked work still owns the
        # single worker until the test supervisor releases it.
        assert self.release.wait(3)
        self.exited.set()
        context.check()
        return bytearray(b'pixels'), 'image/png'


def testLatestOnlyAndResetRejectsLateWorkWithoutStartingAnotherWorker():
    client = Client()
    notices = []
    images = InspectionImages(client, lambda *_args: ('owned pixels', 4, 'PNG'), notices.append)
    try:
        assert images.ensureSession('project') == 'session'
        first = images.select(selection())
        assert client.started.wait(2)
        thread = images._thread
        for _ in range(200):
            images.select(selection('missing'))
        assert len(client.calls) == 1 and not client.exited.is_set()
        images.reset()
        assert images._thread is thread and images.take(first) is None
        client.release.set()
        images.close()
        assert client.exited.is_set() and not thread.is_alive()
        assert images._mailbox is None and images.decodedBytes == 0
        assert client.closed == ['session']
    finally:
        client.release.set()
        images.close()


def testSelectionDuringSessionOpeningDoesNotCancelExplicitStart():
    client = Client()
    entered, release = threading.Event(), threading.Event()
    method = client.inspectionSession
    def openSlow(action, *args):
        if action == 'open':
            entered.set()
            assert release.wait(3)
        return method(action, *args)
    client.inspectionSession = openSlow
    images = InspectionImages(client, lambda *_args: ('pixels', 4, 'PNG'), lambda *_args: None)
    accepted = []
    worker = threading.Thread(target=lambda: accepted.append(images.ensureSession('project')))
    try:
        worker.start()
        assert entered.wait(2)
        images.select(None)
        release.set()
        worker.join(3)
        assert accepted == ['session']
    finally:
        release.set()
        worker.join(3)
        images.close()


def testOlderRuntimeCapabilityIsExplicitAndDoesNotInventPixels():
    notices = []
    images = InspectionImages(object(), lambda *_args: pytest.fail('must not decode'), notices.append)
    try:
        assert images.ensureSession('project') == ''
        generation = images.select(selection())
        assert 'UNSUPPORTED' in images.take(generation)['message']
        assert images.reads == 0 and images._thread is None
    finally:
        images.close()


@pytest.mark.parametrize('extension, shape', [('.png', (5, 7, 3)), ('.jpg', (5, 7, 3)), ('.bmp', (5, 7, 3)),
                                            ('.png', (5, 7)), ('.png', (5, 7, 4))])
def testActualChannelsStrideDimensionsAndOwnedQImage(tmp_path, extension, shape):
    import cv2
    import numpy as np
    from PySide2.QtGui import QColor
    pixels = np.zeros(shape, np.uint8)
    if len(shape) == 3:
        pixels[..., 2] = 230  # BGR red, not blue.
        if shape[-1] == 4:
            pixels[..., 3] = 255
    else:
        pixels[:] = 123
    ok, encoded = cv2.imencode(extension, pixels)
    assert ok
    path = tmp_path / ('实际图片' + extension)
    path.write_bytes(encoded.tobytes())
    width, height, mime = imageHeader(path)
    payload = bytearray(path.read_bytes())
    image, used, actualFormat = decodeNodeImage(payload, width, height)
    del payload
    assert image.width() == 7 and image.height() == 5 and image.bytesPerLine() == 28
    assert used == 140 and actualFormat.lower() in mime
    color = QColor(image.pixel(0, 0))
    assert color.red() > color.blue() + 150 if len(shape) == 3 else color.red() == 123


def testCorruptDimensionsAndDecodeBudgetsDoNotReturnPartialImage():
    import cv2
    import numpy as np
    with pytest.raises(ValueError, match='CORRUPT'):
        decodeNodeImage(bytearray(b'corrupt image'), 7, 5)
    _, encoded = cv2.imencode('.png', np.zeros((5, 7, 3), np.uint8))
    with pytest.raises(ValueError, match='IDENTITY'):
        decodeNodeImage(bytearray(encoded.tobytes()), 8, 5)
    _, encoded = cv2.imencode('.png', np.zeros((1500, 1500), np.uint8))
    with pytest.raises(ValueError, match='DECODE_BUDGET'):
        decodeNodeImage(bytearray(encoded.tobytes()), 1500, 1500)
    with pytest.raises(ValueError, match='READ_BUDGET'):
        decodeNodeImage(bytearray(4 * 1024 * 1024 + 1), 1, 1)
