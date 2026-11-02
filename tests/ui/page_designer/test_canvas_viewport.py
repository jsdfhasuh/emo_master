"""Real page viewport size, auto fit and manual camera state are independent."""
import pytest
from PySide2.QtCore import QEventLoop, QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide2.QtTest import QTest

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, settle  # noqa: F401


@pytest.mark.parametrize('legacy', [False, True], ids=['responsive', 'legacy-grid'])
def testFitShowsWholeLongPageWithoutInternalScrollbars(responsivePage, legacy):  # noqa: F811
    from emo_master.core.presentation.models import Grid, ResponsiveLayout
    coordinator, editor, keys = responsivePage
    def configure(p):
        page = p.pages[editor.pageId]
        page.layout = Grid() if legacy else ResponsiveLayout()
        for index, item in enumerate(page.components):
            if legacy:
                item.layout.row = index * 4
                item.layout.rowSpan = 4
            else:
                item.responsive.preferredHeight = 300
    coordinator.session.editPresentation(configure)
    editor.refresh()
    settle()
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    editor.canvas.fitPage()
    settle()
    canvas = editor.canvas
    scroll = editor.renderer.pages[editor.pageId]
    assert scroll.verticalScrollBar().maximum() == 0
    assert scroll.horizontalScrollBar().maximum() == 0
    assert not scroll.overlayVertical.isVisible() and not scroll.overlayHorizontal.isVisible()
    assert editor.renderer.height() > canvas.viewport().height()
    assert canvas.getZoomFactor() < 1 and canvasBoxFits(canvas)
    for card in editor.renderer.cards[editor.pageId].values():
        box = QRect(card.mapTo(editor.renderer, QPoint()), card.size())
        assert canvas.viewport().rect().adjusted(-1, -1, 1, 1).contains(canvas.rendererRectOnViewport(box))
    # Enlarging changes only the view; the whole logical page remains available.
    canvas.setZoomFactor(1.25)
    last = editor.renderer.cards[editor.pageId][keys[-1]]
    local = last.mapTo(editor.renderer, last.rect().center())
    canvas.centerOn(canvas.proxy.mapToScene(QPointF(local)))
    settle()
    assert not scroll.overlayVertical.isVisible()
    delta = canvas.mapFromScene(canvas.proxy.mapToScene(QPointF(local))) - canvas.viewport().rect().center()
    assert abs(delta.x()) <= 2 and abs(delta.y()) <= 2
    assert coordinator.session.payload() == before and len(coordinator.session._undo) == history


def testFixedTerminalKeepsOverflowAndFollowingWindowRemovesIt(responsivePage):  # noqa: F811
    from emo_master.core.presentation.models import ResponsiveLayout
    coordinator, editor, _keys = responsivePage
    def configure(p):
        page = p.pages[editor.pageId]
        page.layout = ResponsiveLayout()
        for item in page.components:
            item.responsive.preferredHeight = 300
    coordinator.session.editPresentation(configure)
    editor.refresh()
    before = coordinator.session.payload()
    for _ in range(2):
        editor.canvas.setPreviewSize(768, 640)
        settle()
        scroll = editor.renderer.pages[editor.pageId]
        assert scroll.viewport().size() == QSize(768, 640)
        assert scroll.overlayVertical.isVisible() and scroll.verticalScrollBar().maximum() > 0
        scroll.verticalScrollBar().setValue(150)
        editor.canvas.setPreviewSize()
        settle()
        assert scroll.verticalScrollBar().value() == scroll.verticalScrollBar().maximum() == 0
        assert not scroll.overlayVertical.isVisible()
        assert canvasBoxFits(editor.canvas)
    assert coordinator.session.payload() == before


def testContentHeightChangeKeepsViewUntilExplicitFit(responsivePage):  # noqa: F811
    from emo_master.core.presentation.models import ResponsiveLayout
    coordinator, editor, _keys = responsivePage
    editor.canvas.fitPage()
    settle()
    zoom, center = editor.canvas.getZoomFactor(), editor.canvas._viewportCenter()
    def configure(p):
        page = p.pages[editor.pageId]
        page.layout = ResponsiveLayout()
        for item in page.components:
            item.responsive.preferredHeight = 350
    coordinator.session.editPresentation(configure)
    editor.refresh()
    settle()
    assert editor.canvas.getZoomFactor() == zoom
    # Qt scrollbars quantize camera positions in viewport pixels, at any zoom.
    delta = editor.canvas.mapFromScene(editor.canvas._viewportCenter()) - editor.canvas.mapFromScene(center)
    assert delta.manhattanLength() <= 2
    scroll = editor.renderer.pages[editor.pageId]
    assert scroll.verticalScrollBar().maximum() == 0
    history = len(coordinator.session._undo)
    coordinator.window.designerActions.invoke('fit')
    settle()
    assert editor.canvas.getZoomFactor() < zoom and canvasBoxFits(editor.canvas)
    assert len(coordinator.session._undo) == history


def testFollowingPageFillsCanvasAndPanelHeadersFloatAtTop(responsivePage):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    canvas = editor.canvas
    # This short-page case needs room for the content regardless of screen size.
    canvas.setFixedSize(1100, 700)
    canvas.fitPage()
    settle()
    scroll = editor.renderer.pages[editor.pageId]
    layout = scroll.widget().layout()
    assert layout.hasHeightForWidth()
    assert 0 <= layout.heightForWidth(scroll.viewport().width()) <= scroll.viewport().height()
    assert editor.renderer.size() == canvas.viewport().size() - QSize(16, 16)
    center = canvas.mapFromScene(canvas.proxy.sceneBoundingRect().center())
    assert (center - canvas.viewport().rect().center()).manhattanLength() <= 2
    assert canvas.getZoomFactor() == pytest.approx(1)
    assert editor.renderer.layout().contentsMargins().top() == 12
    bounds = editor.renderer.geometry()
    viewport = canvas.viewport().geometry()
    transform = canvas.transform()
    payload, history = coordinator.session.payload(), len(coordinator.session._undo)
    for panel in (editor.floatingToolbox, editor.floatingProperties):
        panel.setExpanded(False)
    settle()
    for panel in (editor.floatingToolbox, editor.floatingProperties):
        assert panel.isVisible()
        assert panel.parentWidget() is editor.canvasHost
        assert not editor.renderer.isAncestorOf(panel)
        assert panel.y() == 12 and panel.height() == 46
        assert panel.toggleButton.isVisible() and panel.dragHandle.isVisible()
        assert not panel.contentScroll.isVisible()
        button = panel.toggleButton
        hit = editor.canvasHost.childAt(button.mapTo(editor.canvasHost, button.rect().center()))
        assert hit is button
        QTest.mouseClick(button, Qt.LeftButton)
        settle()
        assert panel.isExpanded() and panel.contentScroll.isVisible()
        QTest.mouseClick(panel.closeButton, Qt.LeftButton)
        settle()
        assert not panel.isExpanded() and button.isVisible()
        assert editor.renderer.geometry() == bounds
        assert canvas.viewport().geometry() == viewport and canvas.transform() == transform
    assert editor.floatingToolbox.x() == 12
    assert editor.canvasHost.width() - editor.floatingProperties.geometry().right() - 1 == 12
    assert coordinator.session.payload() == payload and len(coordinator.session._undo) == history


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
