"""Real Qt navigation: prove displacement and visibility, not just pan flags."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRectF, QSettings, Qt, QTimer
from PySide2.QtGui import QFont, QFontDatabase, QMouseEvent, QWheelEvent
from PySide2.QtWidgets import QApplication


from tests.designer.navigation_fixture import saveNavigationProject


def settle():
    loop = QEventLoop()
    QTimer.singleShot(60, loop.quit)
    loop.exec_()


def center(view):
    return view.mapToScene(view.viewport().rect().center())


def blankPoint(view):
    width, height = view.viewport().width(), view.viewport().height()
    for y in (35, height - 35, height / 3, height * .7):
        for x in (width / 2, width * .7, width * .3):
            if view.itemAt(int(x), int(y)) is None:
                return QPointF(x, y)
    raise AssertionError('fixture has no blank area for the drag')


def drag(view, dx=-150, dy=-40):
    start = blankPoint(view)
    end = start + QPointF(dx, dy)
    before = center(view)
    startGlobal = QPointF(view.viewport().mapToGlobal(start.toPoint()))
    endGlobal = QPointF(view.viewport().mapToGlobal(end.toPoint()))
    QApplication.sendEvent(view.viewport(), QMouseEvent(QEvent.MouseButtonPress, start, startGlobal, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert view.isPanning()
    QApplication.sendEvent(view.viewport(), QMouseEvent(QEvent.MouseMove, end, endGlobal, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    QApplication.sendEvent(view.viewport(), QMouseEvent(QEvent.MouseButtonRelease, end, endGlobal, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    settle()
    actual = center(view) - before
    scale = view.getZoomFactor()
    assert actual.x() * scale == pytest.approx(-dx, abs=2)
    assert actual.y() * scale == pytest.approx(-dy, abs=2)
    assert not view.isPanning()


@pytest.fixture
def readableFont(designerApplication):
    # Windows offscreen QPA does not discover system fonts automatically.
    app = designerApplication
    previous, family = app.font(), getattr(app, '_designerFontFamily', None)
    path = Path('C:/Windows/Fonts/msyh.ttc')
    if path.exists():
        key = QFontDatabase.addApplicationFont(str(path))
        assert key >= 0
        app._designerFontFamily = QFontDatabase.applicationFontFamilies(key)[0]
        app.setFont(QFont(app._designerFontFamily, 10))
    yield
    app._designerFontFamily = family
    app.setFont(previous)


@pytest.fixture
def window(ownedDesignerWindow, tmp_path, readableFont):
    def forbidden(*a, **k):
        raise AssertionError('canvas navigation must not execute Runtime/devices')
    # A successful load reply is local only; no Runtime connection is created.
    w = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [],
                            loadProject=lambda _: SimpleNamespace(ok=True, message='stub'), startJob=forbidden),
                            settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    w.show()
    settle()
    w.resize(1280, 900)
    settle()
    return w


@pytest.mark.parametrize('zoom', [.02, .1, .25, 1, 3])
def testBlankDragReallyMovesAtEveryZoomWithoutEditingGraph(window, zoom):
    view = window.flowView
    view.setZoomFactor(zoom)
    before = window.pageCoordinator.session.payload()
    positions = window.flowScene.getNodePositions()
    overlay = window.floatingToolbox.geometry()
    for dx, dy in [(-150, -40), (150, 40), (120, -80)]:
        drag(view, dx, dy)
    assert window.pageCoordinator.session.payload() == before
    assert window.flowScene.getNodePositions() == positions
    assert window.floatingToolbox.geometry() == overlay


def testRepeatedPansExtendBothDirectionsBeyondOriginalBoundary(window):
    view = window.flowView
    view.setZoomFactor(1)
    view.centerOn(0, 0)
    for _ in range(24):
        drag(view, -200, -160)
    assert center(view).x() > 4000 and center(view).y() > 3000
    for _ in range(48):
        drag(view, 200, 160)
    assert center(view).x() < -4000 and center(view).y() < -3000


@pytest.mark.parametrize('point', [(12000, 9000), (-14000, -11000)])
def testCenterOnCanRestoreFarViewportAndResizeWithoutClamping(window, point):
    view = window.flowView
    view.setZoomFactor(.12)
    view.centerOn(*point)
    settle()
    assert (center(view) - QPointF(*point)).manhattanLength() * view.getZoomFactor() < 3
    before = center(view)
    window.resize(1000, 650)
    settle()
    assert (center(view) - before).manhattanLength() * view.getZoomFactor() < 3
    drag(view)


def testSceneChangesMakeMovedNodesAndEdgesReachableWithoutAutoFit(window):
    original = set(window.flowModel.nodes)
    window.addNodeFromOperatorPayload(dict(operatorId='test.echo', displayName='First',
                inputPorts={}, outputPorts={'value': 'number'}, paramSchema={}), sceneX=0, sceneY=0)
    first, = set(window.flowModel.nodes) - original
    window.addNodeFromOperatorPayload(dict(operatorId='test.echo', displayName='Second',
                inputPorts={'value': 'number'}, outputPorts={}, paramSchema={}), sceneX=400, sceneY=0)
    second, = set(window.flowModel.nodes) - original - {first}
    edge = window.connectPorts(first, 'value', second, 'value')
    assert edge is not None
    window.flowScene.renderEdge(edge)
    window.flowScene._nodeItems[second].setPos(8000, -6000)
    settle()
    assert window.flowView.sceneRect().contains(window.flowScene.itemsBoundingRect())
    window.flowView.centerOn(window.flowScene._nodeItems[second].sceneBoundingRect().center())
    visible = window.flowView.mapToScene(window.flowView.viewport().rect()).boundingRect()
    assert visible.contains(window.flowScene._nodeItems[second].sceneBoundingRect())
    assert window.flowScene._edgeItems


def testArrangeFitAndSmallZoomPanOnWidePortableWorkflow(window, tmp_path):
    target = saveNavigationProject(tmp_path)
    original = target.read_bytes()
    assert window.loadProjectDirectory(str(target), confirmed=True)
    window.activateWorkflow('station1-frame')
    window.autoLayoutNodes()
    for _ in range(10):
        settle()
        if window.flowView._pendingFit is None:
            break
    assert window.flowView._pendingFit is None
    content = QRectF(*window.flowScene.getContentBounds())
    assert content.right() > 2000  # Regression fixture: original scene stopped here.
    visible = window.flowView.mapToScene(window.flowView.viewport().rect()).boundingRect()
    assert visible.adjusted(-1, -1, 1, 1).contains(content)
    assert (center(window.flowView) - content.center()).manhattanLength() * window.flowView.getZoomFactor() < 3
    output = Path(os.environ.get('EMO_CANVAS_QA_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(output / 'arranged-fit.png'))
    before = window.pageCoordinator.session.payload()
    window.flowView.setZoomFactor(.18)
    drag(window.flowView)
    assert window.grab().save(str(output / 'small-zoom-pan.png'))
    # Inspect the formerly inaccessible right edge at a readable zoom, using
    # the same blank-canvas drag path rather than moving any nodes.
    window.flowView.setZoomFactor(.65)
    window.flowView.centerOn(content.center())
    while center(window.flowView).x() < content.right() - 300:
        drag(window.flowView, -180, 0)
    visible = window.flowView.mapToScene(window.flowView.viewport().rect()).boundingRect()
    assert visible.left() < content.right() - 80 < visible.right()
    assert window.grab().save(str(output / 'right-side-reachable.png'))
    assert window.pageCoordinator.session.payload() == before
    assert target.read_bytes() == original


def testFarViewSurvivesWorkflowSwitchAndProjectReopenWithoutGraphEdits(window, tmp_path):
    from emo_master.apps.designer.state.project_store import saveProject
    from tests.runtime.test_workflow_debug_control import nestedProject

    target = tmp_path / 'navigation.emoproj'
    payload = nestedProject()
    for workflow, x, y in [('main', 11800, -8500), ('child', -9000, 6000)]:
        payload['workflows'][workflow]['layout'] = {'nodePositions': {
            node['nodeId']: {'x': x + i * 350, 'y': y}
            for i, node in enumerate(payload['workflows'][workflow]['nodes'])}}
    saveProject(target, payload)
    raw = target.read_bytes()
    assert window.loadProjectDirectory(str(target), confirmed=True)
    window.flowView.setZoomFactor(.12)
    window.flowView.centerOn(12000, -8500)
    settle()
    assert (center(window.flowView) - QPointF(12000, -8500)).manhattanLength() * .12 < 3
    before = window.pageCoordinator.session.payload()
    expected = center(window.flowView)
    window.activateWorkflow('child')
    window.flowView.setZoomFactor(.3)
    window.flowView.centerOn(-9000, 6000)
    window.activateWorkflow('main')
    settle()
    assert window.flowView.getZoomFactor() == pytest.approx(.12)
    assert (center(window.flowView) - expected).manhattanLength() * .12 < 3
    window._saveWorkflowViewState()
    assert window.loadProjectDirectory(str(target), confirmed=True)
    settle()
    assert window.flowView.getZoomFactor() == pytest.approx(.12)
    assert (center(window.flowView) - expected).manhattanLength() * .12 < 3
    assert window.pageCoordinator.session.payload() == before
    assert not window.pageCoordinator.session.dirty
    assert target.read_bytes() == raw


def testUserPanCancelsPendingFitRatherThanSnappingBack(window):
    view = window.flowView
    view.setZoomFactor(.1)
    view.fitContent((9000, 6000, 1000, 800))
    # Press/move/release before the queued fit has executed.
    drag(view)
    assert view._pendingFit is None
    assert view.getZoomFactor() == pytest.approx(.1)


def testFitDiscardsDistantNavigationExtentAndDoesNotKeepScheduling(window):
    view = window.flowView
    view.centerOn(1000000, -1000000)
    view.fitContent((0, 0, 600, 300))
    settle()
    assert view._pendingFit is None
    assert view.sceneRect().width() < 10000 and view.sceneRect().height() < 10000
    assert (center(view) - QPointF(300, 150)).manhattanLength() < 3
    before = view.sceneRect(), center(view)
    settle()
    assert not view._navigationTimer.isActive()
    assert (view.sceneRect(), center(view)) == before


@pytest.mark.parametrize('zoom', [.02, .18, 1])
def testRealWheelEventKeepsZoomAndPanUsable(window, zoom):
    view = window.flowView
    view.setZoomFactor(zoom)
    view.centerOn(8000, -6000)
    point = QPoint(350, 200)
    event = QWheelEvent(QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
                        QPoint(), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    QApplication.sendEvent(view.viewport(), event)
    assert event.isAccepted()
    assert view.getZoomFactor() == pytest.approx(zoom * 1.15)
    assert view.sceneRect().contains(view.mapToScene(view.viewport().rect()).boundingRect())
    drag(view)


def testNodeAndPortDragsDoNotBecomeCanvasPans(window):
    from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel

    view, scene = window.flowView, window.flowScene
    keys = []
    for name, x in [('source', 7000), ('target', 7600)]:
        key = window.flowModel.addNode('test.echo', name, {'in': 'number'}, {'out': 'number'})
        scene.addFlowNode(FlowNodeViewModel(key, name, x, -6000, {'in': 'number'}, {'out': 'number'}))
        keys.append(key)
    view.setZoomFactor(.6)
    view.centerOn(7400, -5900)
    settle()
    before = center(view)

    def mouse(kind, point, button, buttons):
        QApplication.sendEvent(view.viewport(), QMouseEvent(kind, QPointF(point),
                              QPointF(view.viewport().mapToGlobal(point)), button, buttons, Qt.NoModifier))

    start = view.mapFromScene(scene._portItems[(keys[0], 'output', 'out')].sceneBoundingRect().center())
    end = view.mapFromScene(scene._portItems[(keys[1], 'input', 'in')].sceneBoundingRect().center())
    assert view.itemAt(start) is scene._portItems[(keys[0], 'output', 'out')]
    mouse(QEvent.MouseMove, start, Qt.NoButton, Qt.NoButton)
    mouse(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    assert not view.isPanning()
    assert scene._dragSourcePort is scene._portItems[(keys[0], 'output', 'out')]
    mouse(QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton)
    mouse(QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    settle()
    assert (keys[0], 'out', keys[1], 'in') in scene._edgeItems
    node = scene._nodeItems[keys[1]]
    old = node.pos()
    grab = view.mapFromScene(old + QPointF(80, 15))
    mouse(QEvent.MouseButtonPress, grab, Qt.LeftButton, Qt.LeftButton)
    assert scene.mouseGrabberItem() is node and not view.isPanning()
    mouse(QEvent.MouseMove, grab + QPoint(0, 54), Qt.NoButton, Qt.LeftButton)
    mouse(QEvent.MouseButtonRelease, grab + QPoint(0, 54), Qt.LeftButton, Qt.NoButton)
    settle()
    assert node.pos().y() - old.y() == pytest.approx(90, abs=2)
    assert (center(view) - before).manhattanLength() * .6 < 3
