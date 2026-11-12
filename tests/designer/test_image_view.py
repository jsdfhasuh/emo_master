"""Public image viewport interactions, independent of any operator ID."""
import pytest
from PySide2.QtCore import QEvent, QPoint, Qt
from PySide2.QtGui import QMouseEvent, QPixmap, QWheelEvent
from PySide2.QtTest import QTest

from emo_master.apps.designer.ui.image_view import ImageView


def wheel(app, view, delta=120):
    position = view.viewport().rect().center()
    QTest.mouseMove(view.viewport(), position)
    event = QWheelEvent(position, view.viewport().mapToGlobal(position), QPoint(), QPoint(0, delta),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    app.sendEvent(view.viewport(), event)


def drag(app, view, button, start, end):
    QTest.mousePress(view.viewport(), button, pos=start)
    app.sendEvent(view.viewport(), QMouseEvent(QEvent.MouseMove, end, Qt.NoButton, button, Qt.NoModifier))
    QTest.mouseRelease(view.viewport(), button, pos=end)


@pytest.fixture
def imageView(designerApplication):
    view = ImageView()
    view.resize(400, 300)
    image = QPixmap(640, 480)
    image.fill(Qt.green)
    view.setImage(image)
    view.show()
    designerApplication.processEvents()
    try:
        yield view
    finally:
        view.close()


@pytest.mark.parametrize("button", [Qt.LeftButton, Qt.MiddleButton])
def testWheelPanFitAndActualPreserveOriginalPixels(imageView, designerApplication, button):
    view = imageView
    key = view.pixmap().cacheKey()
    view.setZoom(1)
    wheel(designerApplication, view)
    assert view.zoom > 1
    before = view.mapToScene(view.viewport().rect().center())
    drag(designerApplication, view, button, QPoint(180, 140), QPoint(150, 110))
    after = view.mapToScene(view.viewport().rect().center())
    assert after.x() > before.x() and after.y() > before.y()
    view.fit()
    assert view.fitting and 0 < view.zoom < 1
    view.setZoom(1)
    assert view.zoom == 1 and not view.fitting
    assert view.pixmap().cacheKey() == key


def testEmptyAndZeroDeltaDoNotZoomAndReloadResetsView(imageView, designerApplication):
    view = imageView
    zoom = view.zoom
    wheel(designerApplication, view, 0)
    assert view.zoom == zoom
    view.setImage(None)
    wheel(designerApplication, view)
    assert view.pixmap().isNull() and view.zoom == 1
    image = QPixmap(640, 480)
    image.fill(Qt.red)
    view.setImage(image)
    assert view.fitting and 0 < view.zoom < 1


def testLargeImageFitsEvenBelowManualZoomLimit(imageView):
    image = QPixmap(8000, 100)
    image.fill(Qt.red)
    imageView.setImage(image)
    assert imageView.zoom < imageView.minimumZoom
    assert imageView.zoom * image.width() <= imageView.viewport().width()
