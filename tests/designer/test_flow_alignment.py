"""Native node gestures and explicit arrangement preserve graph/history semantics."""
import pytest
from PySide2.QtCore import QEvent, QPointF, Qt
from PySide2.QtGui import QMouseEvent, QTransform
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QGraphicsView

from emo_master.apps.designer.ui.canvas_alignment import Box, distributedOffsets
from emo_master.apps.designer.ui.designer_graphics_view import DesignerGraphicsView
from tests.designer.test_flow_layout_qt import addNode


def pointer(view, kind, scenePoint, *, modifiers=Qt.NoModifier):
    point = view.mapFromScene(scenePoint)
    button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
    QApplication.sendEvent(view.viewport(), QMouseEvent(kind, QPointF(point),
        QPointF(view.viewport().mapToGlobal(point)), button, buttons, modifiers))


@pytest.fixture
def alignmentCanvas(ownedFlowScene, designerApplication):
    scene = ownedFlowScene()
    addNode(scene, 'moving', 0, 0)
    addNode(scene, 'reference', 600, 100)
    view = QGraphicsView(scene)
    view.setSceneRect(-400, -400, 1800, 1200)
    view.resize(1100, 700)
    view.show()
    view.setFocus()
    designerApplication.processEvents()
    committed = []
    scene.editCompleted = lambda: committed.append(scene.getNodePositions())
    yield scene, view, committed
    view.close()
    view.setScene(None)


@pytest.mark.parametrize('guides,snap,alt,expected', [
    (True, True, False, 100), (False, True, False, 100),
    (True, False, False, 96), (True, True, True, 96), (False, False, False, 96),
])
def testGuideAndSnapPreferencesAreIndependentAndAltBypasses(alignmentCanvas, guides, snap, alt, expected):
    scene, view, committed = alignmentCanvas
    scene.setAlignmentGuidesEnabled(guides)
    scene.setAlignmentSnapEnabled(snap)
    start = QPointF(80, 15)
    end = start + QPointF(50, 96)
    modifiers = Qt.AltModifier if alt else Qt.NoModifier
    pointer(view, QEvent.MouseButtonPress, start)
    pointer(view, QEvent.MouseMove, end, modifiers=modifiers)
    assert scene._nodeItems['moving'].pos() == QPointF(50, expected)
    assert not committed
    guide = scene._alignmentGuideItem
    assert bool(guide is not None and guide.isVisible()) is guides
    if guides:
        assert guide.shape().isEmpty()  # No mouse hit: blank-canvas panning remains available.
        assert guide.path().elementCount() <= 4
    pointer(view, QEvent.MouseButtonRelease, end, modifiers=modifiers)
    assert len(committed) == 1
    assert not scene.isAlignmentDragging()
    assert guide is None or not guide.isVisible()
    # Programmatic positions and history restoration do not snap.
    scene._nodeItems['moving'].setPos(50, 96)
    assert scene._nodeItems['moving'].y() == 96


@pytest.mark.parametrize('scale', [.5, 1., 2.])
def testSnapToleranceUsesScreenPixels(alignmentCanvas, scale):
    scene, view, _committed = alignmentCanvas
    view.setTransform(QTransform().scale(scale, scale))
    start = QPointF(80, 15)
    pointer(view, QEvent.MouseButtonPress, start)
    # Keep the reference within the screen-space neighbour range at every zoom.
    pointer(view, QEvent.MouseMove, start + QPointF(200, 100 - 4 / scale))
    assert scene._nodeItems['moving'].y() == pytest.approx(100)
    pointer(view, QEvent.MouseMove, start + QPointF(200, 100 - 10 / scale))
    assert scene._nodeItems['moving'].y() == pytest.approx(100 - 10 / scale)
    pointer(view, QEvent.MouseButtonRelease, start + QPointF(200, 100 - 10 / scale))


def testGroupDragPreservesOffsetsAndEscapeRestoresWithoutHistory(alignmentCanvas):
    scene, view, committed = alignmentCanvas
    addNode(scene, 'other', 0, 180)
    for key in ('moving', 'other'):
        scene._nodeItems[key].setSelected(True)
    before = scene.getNodePositions()
    start = QPointF(80, 15)
    pointer(view, QEvent.MouseButtonPress, start)
    pointer(view, QEvent.MouseMove, start + QPointF(50, 96))
    assert scene._nodeItems['moving'].pos() == QPointF(50, 100)
    assert scene._nodeItems['other'].pos() == QPointF(50, 280)
    QTest.keyClick(view, Qt.Key_Escape)
    assert scene.getNodePositions() == before and not committed
    assert not scene.isAlignmentDragging() and scene.mouseGrabberItem() is None
    pointer(view, QEvent.MouseButtonRelease, start + QPointF(50, 96))
    assert not committed


def testClickAndReturnToOriginDoNotCommit(alignmentCanvas):
    scene, view, committed = alignmentCanvas
    start = QPointF(80, 15)
    pointer(view, QEvent.MouseButtonPress, start)
    pointer(view, QEvent.MouseButtonRelease, start)
    assert not committed
    pointer(view, QEvent.MouseButtonPress, start)
    pointer(view, QEvent.MouseMove, start + QPointF(30, 40))
    pointer(view, QEvent.MouseMove, start)
    pointer(view, QEvent.MouseButtonRelease, start)
    assert scene._nodeItems['moving'].pos() == QPointF() and not committed


@pytest.mark.parametrize('mode,attribute', [
    ('left', 'left'), ('hcenter', 'center_x'), ('right', 'right'),
    ('top', 'top'), ('vcenter', 'center_y'), ('bottom', 'bottom'),
])
def testSixAlignmentsOnlyMoveSelectionAndCommitOnce(ownedFlowScene, mode, attribute):
    scene = ownedFlowScene()
    addNode(scene, 'a', 0, 0)
    addNode(scene, 'b', 450, 190, count=4)
    addNode(scene, 'untouched', 1000, 50)
    for key in ('a', 'b'):
        scene._nodeItems[key].setSelected(True)
    committed = []
    scene.editCompleted = lambda: committed.append(scene.getNodePositions())
    assert scene.alignSelectedNodes(mode)
    boxes = [scene._alignmentBox(scene._nodeItems[key]) for key in ('a', 'b')]
    values = {'left': lambda b: b.x, 'right': lambda b: b.right,
              'top': lambda b: b.y, 'bottom': lambda b: b.bottom,
              'center_x': lambda b: b.x + b.width / 2, 'center_y': lambda b: b.y + b.height / 2}
    assert values[attribute](boxes[0]) == pytest.approx(values[attribute](boxes[1]))
    assert scene._nodeItems['untouched'].pos() == QPointF(1000, 50)
    assert len(committed) == 1
    assert not scene.alignSelectedNodes(mode)
    assert len(committed) == 1


@pytest.mark.parametrize('axis', ['horizontal', 'vertical'])
def testDistributionUsesEqualEdgeGapsAndKeepsEnds(ownedFlowScene, axis):
    scene = ownedFlowScene()
    for index, (key, count) in enumerate((('a', 1), ('b', 4), ('c', 2))):
        addNode(scene, key, (0, 330, 1200)[index], (0, 220, 900)[index], count)
        scene._nodeItems[key].setSelected(True)
    before = scene.getNodePositions()
    committed = []
    scene.editCompleted = lambda: committed.append(scene.getNodePositions())
    assert scene.distributeSelectedNodes(axis)
    after = scene.getNodePositions()
    assert after['a'] == before['a'] and after['c'] == before['c']
    boxes = [scene._alignmentBox(scene._nodeItems[key]) for key in ('a', 'b', 'c')]
    gaps = ([b.y - a.bottom for a, b in zip(boxes, boxes[1:])] if axis == 'vertical' else
            [b.x - a.right for a, b in zip(boxes, boxes[1:])])
    assert gaps[0] == pytest.approx(gaps[1])
    assert len(committed) == 1 and not scene.distributeSelectedNodes(axis)
    assert len(committed) == 1


def testInsufficientDistributionSpaceDoesNotMoveAnything():
    boxes = {'a': Box(0, 0, 100, 80), 'b': Box(50, 0, 200, 80), 'c': Box(200, 0, 100, 80)}
    with pytest.raises(ValueError, match='空间不足'):
        distributedOffsets(boxes, 'horizontal')


def testShiftBlankSelectsAndOrdinaryBlankStillPansAndHideCancels(ownedFlowScene, designerApplication):
    scene = ownedFlowScene()
    addNode(scene, 'one', 0, 0)
    addNode(scene, 'two', 400, 100)
    addNode(scene, 'outside', 1000, 0)
    view = DesignerGraphicsView(scene)
    view.resize(1100, 700)
    view.show()
    designerApplication.processEvents()
    view.centerOn(400, 150)
    committed = []
    scene.editCompleted = lambda: committed.append(scene.getNodePositions())
    try:
        start, end = QPointF(-30, -30), QPointF(700, 240)
        pointer(view, QEvent.MouseButtonPress, start, modifiers=Qt.ShiftModifier)
        pointer(view, QEvent.MouseMove, end, modifiers=Qt.ShiftModifier)
        pointer(view, QEvent.MouseButtonRelease, end, modifiers=Qt.ShiftModifier)
        assert set(scene.getSelectedNodeIds()) == {'one', 'two'}
        assert not view._rubberBandSelecting and not committed
        blank = QPointF(50, 300)
        pointer(view, QEvent.MouseButtonPress, blank)
        assert view.isPanning()
        pointer(view, QEvent.MouseButtonRelease, blank)
        assert not view.isPanning()
        before = scene.getNodePositions()
        pointer(view, QEvent.MouseButtonPress, QPointF(80, 15))
        pointer(view, QEvent.MouseMove, QPointF(130, 70))
        assert scene.getNodePositions() != before and scene.isAlignmentDragging()
        view.hide()
        assert scene.getNodePositions() == before and not scene.isAlignmentDragging()
        assert not committed
    finally:
        view.close()
        view.setScene(None)
