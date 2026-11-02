"""Real page viewport size, auto fit and manual camera state are independent."""
import pytest
from PySide2.QtCore import QEventLoop, QPointF, QSize, Qt, QTimer
from PySide2.QtTest import QTest

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, settle  # noqa: F401


def testFollowingPageFillsCanvasAndPanelEntriesStayOutsidePage(responsivePage):  # noqa: F811
    _coordinator, editor, _keys = responsivePage
    canvas = editor.canvas
    canvas.fitPage()
    settle()
    assert editor.renderer.size() == canvas.viewport().size() - QSize(16, 16)
    center = canvas.mapFromScene(canvas.proxy.sceneBoundingRect().center())
    assert (center - canvas.viewport().rect().center()).manhattanLength() <= 2
    assert canvas.getZoomFactor() == pytest.approx(1)
    assert editor.renderer.layout().contentsMargins().top() == 12
    for panel, button in zip((editor.floatingToolbox, editor.floatingProperties), editor.panelButtons):
        panel.setExpanded(False)
        assert not panel.isVisible()
        assert button.parentWidget() is editor.centerPanel
        button.click()
        assert panel.isVisible()
        panel.setExpanded(False)


@pytest.mark.parametrize('width,height', [(360,640), (768,1024), (1280,720), (1920,1080)])
def testFixedTerminalViewportIsExactAndDoesNotMutateProject(responsivePage, monkeypatch, width, height):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    coordinator.session.markSaved()
    payload, history = coordinator.session.payload(), len(coordinator.session._undo)
    monkeypatch.setattr(editor.renderer, 'devicePixelRatioF', lambda: 2.)
    editor.previewWidth.setValue(width)
    editor.previewHeight.setValue(height)
    settle()
    scroll = editor.renderer.pages[editor.pageId]
    assert scroll.viewport().size() == QSize(width, height)
    firstSize = editor.renderer.size()
    coordinator.window.resize(1180, 760)
    settle()
    assert scroll.viewport().size() == QSize(width, height)
    assert editor.renderer.size() == firstSize
    assert canvasBoxFits(editor.canvas)
    assert coordinator.session.payload() == payload
    assert len(coordinator.session._undo) == history and not coordinator.session.dirty


def canvasBoxFits(canvas):
    rect = canvas.mapFromScene(canvas.proxy.sceneBoundingRect()).boundingRect()
    return canvas.viewport().rect().adjusted(-1, -1, 1, 1).contains(rect)


def testManualViewSurvivesRefreshAndFitRestoresAutomaticMode(responsivePage):  # noqa: F811
    coordinator, editor, keys = responsivePage
    canvas = editor.canvas
    canvas.setZoomFactor(.75)
    canvas.centerOn(canvas.proxy.sceneBoundingRect().center() + QPointF(120, 70))
    center = canvas._viewportCenter()
    editor.tools.select(keys[0])
    editor.refresh()
    settle()
    assert not canvas.autoFit
    assert canvas.getZoomFactor() == pytest.approx(.75)
    assert (canvas._viewportCenter() - center).manhattanLength() <= 2
    coordinator.window.resize(1250, 780)
    settle()
    assert canvas.getZoomFactor() == pytest.approx(.75)
    assert (canvas._viewportCenter() - center).manhattanLength() <= 2
    coordinator.window.designerActions.invoke('fit')
    settle()
    assert canvas.autoFit and canvas.getZoomFactor() == pytest.approx(1)
    assert canvasBoxFits(canvas)


def testWindowResizeWaitsForActiveComponentGesture(responsivePage):  # noqa: F811
    coordinator, editor, keys = responsivePage
    editor.tools.responsive.select([keys[0]])
    handle = editor.tools.responsive.handles[0]
    QTest.mousePress(handle, Qt.LeftButton, pos=handle.rect().center())
    assert editor.tools.responsive.resize is not None
    pageSize = editor.renderer.size()
    transform = editor.canvas.transform()
    coordinator.window.resize(1200, 760)
    loop = QEventLoop()
    QTimer.singleShot(30, loop.quit)
    loop.exec_()
    settle()
    assert editor.canvas._deferredSize
    assert editor.renderer.size() == pageSize
    assert editor.canvas.transform() == transform
    QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.rect().center())
    loop = QEventLoop()
    QTimer.singleShot(30, loop.quit)
    loop.exec_()
    settle()
    assert not editor.canvas._deferredSize
    assert editor.renderer.size() == editor.canvas.viewport().size() - QSize(16, 16)


def testEnteringAnotherPageRestoresFitWithoutLosingManualViewOnRefresh(responsivePage):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    first = editor.pageId
    second = editor.store.createPage('another viewport')
    editor.refresh()
    editor.canvas.setZoomFactor(.5)
    editor.refresh()
    settle()
    assert not editor.canvas.autoFit and editor.canvas.getZoomFactor() == pytest.approx(.5)
    editor.navigatePage(second)
    settle()
    assert editor.pageId != first and editor.canvas.autoFit
    assert editor.canvas.getZoomFactor() == pytest.approx(1)
    assert canvasBoxFits(editor.canvas)
    assert editor.renderer.currentPageId == second


def testFixedTerminalRefitsDisplayWhenCanvasChangesAndManualZoomStillWins(responsivePage):  # noqa: F811
    _coordinator, editor, _keys = responsivePage
    editor.previewWidth.setValue(1920)
    editor.previewHeight.setValue(1080)
    settle()
    canvas = editor.canvas
    canvas.setFixedSize(700, 500)
    settle()
    first = canvas.getZoomFactor()
    canvas.setFixedSize(500, 350)
    settle()
    assert canvas.getZoomFactor() < first
    assert editor.renderer.pages[editor.pageId].viewport().size() == QSize(1920, 1080)
    assert canvasBoxFits(canvas)
    canvas.setZoomFactor(.4)
    canvas.setFixedSize(600, 400)
    settle()
    assert not canvas.autoFit and canvas.getZoomFactor() == pytest.approx(.4)
