"""Real Qt interaction/ownership checks for the canvas toolbox."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401 - preload Windows dependency DLLs before Qt

from PySide2.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide2.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QMessageBox
import shiboken2

from emo_master.apps.designer.ui.floating_toolbox import FloatingToolbox
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.operator_bubble import OPERATOR_MIME_TYPE
from tests.designer.qt_wait import waitForCatalog


class Settings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def makeWindow(settings=None):
    calls = []
    operators = [SimpleNamespace(
        operatorId=operatorId, displayName=title, category=category,
        inputPorts={'image': 'image'}, outputPorts={'image': 'image'}, paramSchema={},
    ) for operatorId, title, category in (
        ('vision.test.input', '本地图像输入', '其他'),
        ('vision.test.resize', '图像缩放', '预处理'),
        ('vision.test.long', '非常长的算子名称' * 30, '检测'),
    )]
    client = SimpleNamespace(listOperators=lambda: operators,
                             createJob=lambda *_args: calls.append('job'))
    window = MainWindow(client, settingsStore=settings or Settings())
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.resize(1280, 720)
    window.show()
    waitForCatalog(window)
    QApplication.processEvents()
    return window, calls


@pytest.fixture
def toolboxWindow(designerApplication):
    window, calls = makeWindow()
    yield window, calls
    if shiboken2.isValid(window):
        window.close()


@pytest.mark.parametrize('width,height', [(640, 499), (1000, 600), (1600, 900)])
def testOverlayKeepsCanvasAndTabsStationary(toolboxWindow, width, height):
    window, calls = toolboxWindow
    window.resize(width, height)
    QApplication.processEvents()
    canvas = window.flowView.viewport().geometry()
    tabs = window.workflowTabs.geometry()
    payload = window.pageCoordinator.session.payload()
    for _ in range(3):
        QTest.mouseClick(window.sidebarToggleButton, Qt.LeftButton)
        QApplication.processEvents()
        assert window.floatingToolbox.isExpanded()
        assert window.sidebarContainer.isHidden()
        assert window.getMainSplitterSizes()[0] == 0
        assert window.flowView.viewport().geometry() == canvas
        assert window.workflowTabs.geometry() == tabs
        assert window.flowView.viewport().rect().contains(window.floatingToolbox.geometry())
        QTest.mouseClick(window.floatingToolbox.closeButton, Qt.LeftButton)
        QApplication.processEvents()
        assert not window.floatingToolbox.isExpanded()
        assert window.flowView.viewport().geometry() == canvas
    assert window.pageCoordinator.session.payload() == payload
    assert calls == []


def testLibraryReusesCatalogSearchAndRepeatedCategorySelection(toolboxWindow):
    window, _ = toolboxWindow
    window.expandSidebar()
    bubble = window.operatorBubble
    assert not bubble.isWindow() and bubble.window() is window
    QTest.mouseClick(window.categoryButtons['预处理'], Qt.LeftButton)
    assert bubble.getVisibleOperatorIds() == ['vision.test.resize']
    QTest.mouseClick(window.categoryButtons['预处理'], Qt.LeftButton)
    assert window.floatingToolbox.isExpanded() and bubble.isVisible()
    QTest.mouseClick(window.categoryButtons['全部'], Qt.LeftButton)
    QTest.keyClicks(bubble.searchInput, 'resize')
    assert bubble.getVisibleOperatorIds() == ['vision.test.resize']
    bubble.searchInput.clear()
    assert 'vision.test.long' in bubble.getVisibleOperatorIds()
    longButton = next(button for button in bubble._buttons if button._operatorId == 'vision.test.long')
    assert '非常长的算子名称' * 30 in longButton.toolTip()
    assert bubble._columns == 1
    bubble.searchInput.setText('missing-operator')
    assert bubble.emptyLabel.isVisible() and bubble.getVisibleOperatorIds() == []


def testClickAddsOneNodeThroughExistingProjectTransaction(toolboxWindow):
    window, calls = toolboxWindow
    window.expandSidebar()
    window.operatorBubble.searchInput.setText('resize')
    before = len(window.flowModel.nodes)
    history = len(window.pageCoordinator.session._undo)
    window.operatorBubble._buttons[0].click()
    assert len(window.flowModel.nodes) == before + 1
    node = next(node for node in window.flowModel.nodes.values() if node.operatorId == 'vision.test.resize')
    assert node.operatorId == 'vision.test.resize'
    assert len(window.pageCoordinator.session._undo) == history + 1
    window.pageCoordinator.history()
    assert len(window.flowModel.nodes) == before
    assert window.floatingToolbox.isExpanded()
    assert calls == []


def testLibraryListsOperatorsBeforeSystemTemplatesButKeepsRecentsFirst(toolboxWindow):
    window, _ = toolboxWindow
    window.expandSidebar()
    bubble = window.operatorBubble
    ids = bubble.getVisibleOperatorIds()
    assert max(ids.index(value) for value in ids if value.startswith('vision.')) < min(
        ids.index(value) for value in ids if value.startswith('system.'))
    bubble.setRecentOperatorIds(['system.repeat'])
    assert bubble.getVisibleOperatorIds()[0] == 'system.repeat'


def testDragUsesExistingMimeAndActualViewportDrop(toolboxWindow, monkeypatch):
    import emo_master.apps.designer.ui.operator_bubble as cards
    window, calls = toolboxWindow
    window.expandSidebar()
    window.operatorBubble.searchInput.setText('resize')
    button = window.operatorBubble._buttons[0]
    dropped = []
    before = len(window.flowModel.nodes)
    history = len(window.pageCoordinator.session._undo)
    viewport = window.flowView.viewport()
    destination = QPoint(viewport.width() - 70, viewport.height() // 2)
    expected = window.flowView.mapToScene(destination)

    def transfer(drag, *_args):
        mime = drag.mimeData()
        dropped.append(json.loads(bytes(mime.data(OPERATOR_MIME_TYPE))))
        enter = QDragEnterEvent(destination, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(viewport, enter)
        move = QDragMoveEvent(destination, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(viewport, move)
        drop = QDropEvent(QPointF(destination), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(viewport, drop)
        assert drop.isAccepted()
        return Qt.CopyAction

    monkeypatch.setattr(cards.QDrag, 'exec_', transfer)
    start = QPoint(20, 20)
    QTest.mousePress(button, Qt.LeftButton, pos=start)
    event = QMouseEvent(QEvent.MouseMove, QPointF(start + QPoint(40, 0)),
                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(button, event)
    QTest.mouseRelease(button, Qt.LeftButton, pos=start + QPoint(40, 0))
    assert dropped[0]['operatorId'] == 'vision.test.resize'
    assert len(window.flowModel.nodes) == before + 1
    assert len(window.pageCoordinator.session._undo) == history + 1
    node = next(node for node in window.flowModel.nodes.values() if node.operatorId == 'vision.test.resize')
    nodeItem = window.flowScene._nodeItems[node.nodeId]
    assert nodeItem.pos() == expected
    assert calls == []


def testEscapeOnlyCollapsesToolboxAndCanvasClicksKeepItOpen(toolboxWindow):
    window, calls = toolboxWindow
    window.expandSidebar()
    QTest.mouseClick(window.flowView.viewport(), Qt.LeftButton,
                     pos=QPoint(window.flowView.viewport().width() - 30, 30))
    assert window.floatingToolbox.isExpanded()
    window.operatorBubble.searchInput.setFocus()
    QTest.keyClick(window.operatorBubble.searchInput, Qt.Key_Escape)
    assert window.isVisible() and not window.floatingToolbox.isExpanded()
    assert calls == []


@pytest.mark.parametrize('position', [QPoint(-500, -300), QPoint(100_000, 100_000)])
def testHeaderDragIsClampedAndStoredOutsideProject(toolboxWindow, position):
    window, _ = toolboxWindow
    window.expandSidebar()
    toolbox = window.floatingToolbox
    before = window.pageCoordinator.session.payload()
    undo = len(window.pageCoordinator.session._undo)
    handle = toolbox.dragHandle
    local = QPoint(10, 14)
    start = handle.mapToGlobal(local)
    end = start + position - toolbox.pos()
    for kind, globalPoint, buttons in ((QEvent.MouseButtonPress, start, Qt.LeftButton),
                                       (QEvent.MouseMove, end, Qt.LeftButton),
                                       (QEvent.MouseButtonRelease, end, Qt.NoButton)):
        event = QMouseEvent(kind, QPointF(handle.mapFromGlobal(globalPoint)), QPointF(globalPoint),
                           Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton,
                           buttons, Qt.NoModifier)
        QApplication.sendEvent(handle, event)
    assert window.flowView.viewport().rect().contains(toolbox.geometry())
    assert window.settingsStore.value(FloatingToolbox.positionKey) == [toolbox.x(), toolbox.y()]
    assert window.pageCoordinator.session.payload() == before
    assert len(window.pageCoordinator.session._undo) == undo
    window.resize(640, 499)
    QApplication.processEvents()
    assert window.flowView.viewport().rect().contains(toolbox.geometry())


@pytest.mark.parametrize('stored', [[200, 80], ['nan', 3], [float('inf'), 2], [1], 'bad'])
def testPositionRestoreAndMalformedPreferences(stored, designerApplication):
    settings = Settings()
    settings.setValue(FloatingToolbox.positionKey, stored)
    window, _ = makeWindow(settings)
    window.expandSidebar()
    QApplication.processEvents()
    assert window.flowView.viewport().rect().contains(window.floatingToolbox.geometry())
    if stored == [200, 80]:
        assert settings.value(FloatingToolbox.positionKey) == stored
        window.resize(1280, 720)
        QApplication.processEvents()
        assert window.flowView.viewport().width() >= 520
        assert window.floatingToolbox.x() == 200
    else:
        assert window.floatingToolbox.pos() == window.flowView.viewport().pos() + QPoint(12, 12)
    window.close()


def testShortCanvasCanScrollToEveryCategoryAndCard(toolboxWindow):
    window, _ = toolboxWindow
    window.resize(640, 319)
    QApplication.processEvents()
    window.expandSidebar()
    QApplication.processEvents()
    toolbox = window.floatingToolbox
    assert window.flowView.viewport().rect().contains(toolbox.geometry())
    bar = toolbox.libraryScroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    QApplication.processEvents()
    assert window.operatorBubble._scroll.viewport().height() >= 60
    assert toolbox.closeButton.isVisible()
    assert toolbox.visibleCanvasRect().contains(toolbox.geometry())
    assert toolbox.rect().contains(toolbox.tabs.geometry())
    for button in window.categoryButtons.values():
        toolbox.libraryScroll.ensureWidgetVisible(button, 0, 0)
        QApplication.processEvents()
        assert not button.visibleRegion().isEmpty()


def testAncestorScrollingKeepsToolboxWithinVisibleCanvas(toolboxWindow):
    window, _ = toolboxWindow
    window.resize(640, 319)
    window.expandSidebar()
    QApplication.processEvents()
    toolbox = window.floatingToolbox
    before = window.pageCoordinator.session.payload()
    flowRoot = window.pageCoordinator.stack.currentWidget()
    if hasattr(flowRoot, 'verticalScrollBar'):
        bar = flowRoot.verticalScrollBar()
        assert bar.maximum() > 0
        for position in (bar.maximum(), 0):
            bar.setValue(position)
            QApplication.processEvents()
            assert toolbox.visibleCanvasRect().contains(toolbox.geometry())
    else:
        assert toolbox.visibleCanvasRect().contains(toolbox.geometry())
    assert window.pageCoordinator.session.payload() == before


def testDependenciesNodesAndWorkspaceSwitchReuseOwners(toolboxWindow, monkeypatch):
    window, calls = toolboxWindow
    window.expandSidebar()
    toolbox = window.floatingToolbox
    before = window.pageCoordinator.session.payload()
    for index, container in ((1, window.dependencyTreeContainer), (2, window.nodeListContainer)):
        toolbox.tabs.setCurrentIndex(index)
        QApplication.processEvents()
        assert container.isVisible()
    toolbox.tabs.setCurrentIndex(0)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.Yes)
    coordinator = window.pageCoordinator
    coordinator.showPages()
    QApplication.processEvents()
    assert not toolbox.isVisible() and not window.operatorBubble.isVisible()
    coordinator.showFlow()
    QApplication.processEvents()
    assert toolbox.isVisible() and toolbox.isExpanded()
    assert window.floatingToolbox is toolbox
    assert coordinator.session.payload()['workflows'] == before['workflows']
    assert calls == []


def testPanningZoomingAndFittingNeverMoveTheOverlay(toolboxWindow):
    window, _ = toolboxWindow
    window.expandSidebar()
    original = window.floatingToolbox.geometry()
    for center in (QPointF(1800, 1800), QPointF(-1800, -1800), QPointF(0, 0)):
        window.flowView.centerOn(center)
        window.flowView.setZoomFactor(1.8)
        QApplication.processEvents()
        assert window.floatingToolbox.geometry() == original
    window.focusGraphContent()
    QApplication.processEvents()
    assert window.floatingToolbox.geometry() == original


def testOwnerCloseStopsPendingIconRefreshAndDeletesChildren(toolboxWindow):
    window, _ = toolboxWindow
    window.expandSidebar()
    toolbox, bubble = window.floatingToolbox, window.operatorBubble
    bubble._iconBindingTimer.start(0)
    window.close()
    assert not toolbox.isVisible() and not bubble._iconBindingTimer.isActive()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shiboken2.isValid(toolbox) and not shiboken2.isValid(bubble)
