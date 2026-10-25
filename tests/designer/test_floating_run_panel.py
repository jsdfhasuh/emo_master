"""Native geometry, run ownership and local preferences for the run overlay."""
from pathlib import Path

import pytest
from PySide2.QtCore import QCoreApplication, QEvent, QPoint, QSettings, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication
from shiboken2 import isValid

from tests.designer.test_floating_toolbox import makeWindow, Settings
from tests.designer.test_run_inspector_ui import windowWithNode, deliver


def settle():
    for _ in range(5):
        QApplication.processEvents()


@pytest.mark.parametrize('repeat', [0, 1])
def testDefaultSettingsUseFreshTemporaryFile(designerApplication, isolatedDesignerSettings, repeat):
    window, _ = windowWithNode()
    settings = window.settingsStore
    assert settings.format() == QSettings.IniFormat
    assert Path(settings.fileName()) == isolatedDesignerSettings
    assert not settings.contains(window.floatingRunPanel.stateKey)
    assert not window.floatingRunPanel.isExpanded()
    window.floatingRunPanel.setExpanded(True)
    settings.sync()
    window.close()

    restored, _ = windowWithNode()
    assert restored.floatingRunPanel.isExpanded()
    assert Path(restored.settingsStore.fileName()) == isolatedDesignerSettings
    restored.close()


@pytest.mark.parametrize('size', [(640, 360), (1000, 600), (1600, 900)])
def testEntireRunColumnFloatsWithoutChangingCanvasOrTransform(designerApplication, size):
    window, calls = makeWindow()
    window.resize(*size)
    settle()
    overlay = window.floatingRunPanel
    canvas = window.flowView.viewport().geometry()
    tabs = window.workflowTabs.geometry()
    transform = window.flowView.transform()
    center = window.flowView.mapToScene(window.flowView.viewport().rect().center())
    payload = window.pageCoordinator.session.payload()
    for _ in range(3):
        QTest.mouseClick(overlay.toggleButton, Qt.LeftButton)
        settle()
        assert overlay.isExpanded()
        assert overlay.isAncestorOf(window.summarySection)
        assert overlay.isAncestorOf(window.nodeResultCoordinator.panel)
        assert window.rightPanelSlot.isHidden()
        assert window.getMainSplitterSizes()[::2] == [0, 0]
        assert overlay.visibleCanvasRect().contains(overlay.geometry())
        assert window.flowView.viewport().geometry() == canvas
        assert window.workflowTabs.geometry() == tabs
        assert window.flowView.transform() == transform
        assert window.flowView.mapToScene(window.flowView.viewport().rect().center()) == center
        QTest.mouseClick(overlay.closeButton, Qt.LeftButton)
        settle()
        assert not overlay.isExpanded()
        assert not window.summarySection.isVisible()
        assert not window.nodeResultCoordinator.panel.isVisible()
        assert window.flowView.viewport().geometry() == canvas
    assert window.pageCoordinator.session.payload() == payload and calls == []


def testResultsAndRunningStateContinueWhileWholePanelIsCollapsed(designerApplication, monkeypatch):
    window, node = windowWithNode()
    window.show()
    overlay = window.floatingRunPanel
    overlay.setExpanded(True)
    runtime = window.runtimeController
    child = runtime.runs[''].controller
    stops = []
    monkeypatch.setattr(runtime, 'stopJob', lambda *_: stops.append('selected'))
    try:
        child._jobActive = True
        runtime._changed()
        window._setCurrentJobId('job')
        window.nodeResultCoordinator.accepted('job')
        deliver(window, node, value=12)
        results = window.nodeResultCoordinator.panel
        results.tabs.setCurrentWidget(results.inputs)
        selected = window.flowModel.selectedNodeId
        before = window.pageCoordinator.session.payload()
        overlay.setExpanded(False)
        settle()
        assert child._jobActive and overlay.stopButton.isEnabled()
        assert '运行中 1' == overlay.status.text()
        deliver(window, node, 'node.started', 3, 'second')
        deliver(window, node, sequence=4, execution='second', value=27)
        assert not overlay.isExpanded()
        assert 'count = 27' in results.values.toPlainText()
        assert results.tabs.currentWidget() is results.inputs
        assert window.currentJobId == 'job' and window.flowModel.selectedNodeId == selected
        overlay.setExpanded(True)
        settle()
        assert results.tabs.currentWidget() is results.inputs and child._jobActive
        assert window.pageCoordinator.session.payload() == before
        overlay.setExpanded(False)
        QTest.mouseClick(overlay.stopButton, Qt.LeftButton, pos=QPoint(8, 15))
        assert stops == ['selected'] and not overlay.isExpanded()
    finally:
        child._jobActive = False
        runtime._changed()
        window.close()


def testCollapsedStopMenuReachesOtherRunningTasks(designerApplication, monkeypatch):
    window, _ = makeWindow()
    runtime = window.runtimeController
    run = runtime._create('background')
    stopped = []
    monkeypatch.setattr(runtime, 'stopAll', lambda: stopped.append('all'))
    try:
        run.controller._jobActive = True
        runtime._changed()
        overlay = window.floatingRunPanel
        assert not runtime.canStop() and not overlay.stopButton.isEnabled()
        assert overlay.stopMenuButton.isEnabled()
        assert not window._toolbarActions['停止运行'].isEnabled()
        assert overlay.stopMenu.actions() == [window._menuActions['停止全部']]
        overlay.stopMenu.actions()[0].trigger()
        assert stopped == ['all'] and not overlay.isExpanded()
    finally:
        run.controller._jobActive = False
        runtime._changed()


def testRightEdgePositionAndExpandedStateRestoreLocally(designerApplication):
    settings = Settings()
    window, _ = makeWindow(settings)
    panel = window.floatingRunPanel
    before = window.pageCoordinator.session.payload()
    panel.moveWithinCanvas(QPoint(620, 68))
    panel._savePosition()
    edge = panel.geometry().right()
    panel.setExpanded(True)
    settle()
    assert panel.geometry().right() == edge
    saved = settings.value(panel.positionKey)
    assert settings.value(panel.stateKey) is True
    assert window.pageCoordinator.session.payload() == before
    window.close()
    restored, _ = makeWindow(settings)
    other = restored.floatingRunPanel
    assert other.isExpanded() and other._preferredPosition == QPoint(*saved)
    assert other.geometry().right() == edge
    restored.resize(640, 360)
    settle()
    assert other.visibleCanvasRect().contains(other.geometry())


def testRunOverlayRemainsStationaryOnPanAndRetiresWithOwner(designerApplication):
    window, _ = makeWindow()
    panel = window.floatingRunPanel
    panel.setExpanded(True)
    settle()
    origin = panel.pos()
    window.flowView.horizontalScrollBar().setValue(250)
    window.flowView.verticalScrollBar().setValue(-300)
    window.flowView.setZoomFactor(1.25)
    settle()
    assert panel.pos() == origin
    QApplication.setActiveWindow(window)
    panel.toggleButton.setFocus()
    settle()
    assert QApplication.focusWidget() is panel.toggleButton
    QTest.keyClick(panel.toggleButton, Qt.Key_Escape)
    assert not panel.isExpanded() and window.isVisible()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(panel)


def testExplicitResultCommandOpensTheWholePanelWithoutStartingJob(designerApplication):
    window, node = windowWithNode()
    window.show()
    overlay = window.floatingRunPanel
    overlay.setExpanded(False)
    window._setCurrentJobId('job')
    window.nodeResultCoordinator.accepted('job')
    deliver(window, node, value=4)
    assert not overlay.isExpanded()
    before = window.pageCoordinator.session.payload()
    window.designerActions.invoke('results')
    settle()
    assert overlay.isExpanded() and window.nodeResultCoordinator.panel.isVisible()
    assert 'count = 4' in window.nodeResultCoordinator.panel.values.toPlainText()
    assert window.pageCoordinator.session.payload() == before
    assert not window.runtimeController._jobActive


def testShortRunPanelCanScrollToSummaryAndResults(designerApplication):
    window, _ = makeWindow()
    window.resize(640, 360)
    panel = window.floatingRunPanel
    panel.setExpanded(True)
    settle()
    assert panel.contentScroll.verticalScrollBar().maximum() > 0
    for widget in (window.runningWorkflowCombo, window.nodeResultCoordinator.panelScroll):
        panel.contentScroll.ensureWidgetVisible(widget, 0, 0)
        settle()
        assert not widget.visibleRegion().isEmpty()
    assert panel.closeButton.isVisible() and panel.stopButton.isVisible()
