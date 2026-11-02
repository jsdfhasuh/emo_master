"""Visible overflow must never change the page's layout width."""
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtGui import QWheelEvent
from PySide2.QtWidgets import QWidget

from emo_master.ui.presentation.page_scroll import PageScrollArea


def testOverlayOverflowAndBothAxesKeepViewportStable(qtApp):
    scroll = PageScrollArea()
    body = QWidget()
    scroll.setWidget(body)
    scroll.resize(420, 300)
    scroll.show()
    qtApp.processEvents()
    available = scroll.viewport().size()
    body.resize(100, 100)
    qtApp.processEvents()
    assert not scroll.overlayVertical.isVisible()
    assert not scroll.overlayHorizontal.isVisible()
    body.resize(800, 900)
    qtApp.processEvents()
    assert scroll.viewport().size() == available
    assert scroll.overlayVertical.isVisible() and scroll.overlayHorizontal.isVisible()
    assert not scroll.overlayVertical.geometry().intersects(scroll.overlayHorizontal.geometry())
    scroll.overlayVertical.setValue(100)
    assert scroll.verticalScrollBar().value() == 100
    scroll.horizontalScrollBar().setValue(70)
    assert scroll.overlayHorizontal.value() == 70
    body.resize(100, 100)
    qtApp.processEvents()
    assert scroll.viewport().size() == available
    assert not scroll.overlayVertical.isVisible() and not scroll.overlayHorizontal.isVisible()


def testWheelRetainsNativeScrollBehavior(qtApp):
    scroll = PageScrollArea()
    body = QWidget()
    body.resize(100, 900)
    scroll.setWidget(body)
    scroll.resize(420, 300)
    scroll.show()
    qtApp.processEvents()
    point = QPointF(100, 100)
    event = QWheelEvent(point, QPointF(scroll.viewport().mapToGlobal(QPoint(100, 100))),
        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    qtApp.sendEvent(scroll.viewport(), event)
    assert scroll.verticalScrollBar().value() > 0
    assert scroll.overlayVertical.value() == scroll.verticalScrollBar().value()
