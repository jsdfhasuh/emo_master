"""Page-edge insertion uses the real viewport path and distinguishes source/target."""
import pytest
from PySide2.QtCore import QEventLoop, QPoint, QPointF, QRect, Qt, QTimer
from PySide2.QtGui import QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, settle  # noqa: F401
from test_responsive_drop_preview import install, rootRects
from emo_master.apps.designer.page_designer.tools import mime
from emo_master.core.presentation.models import ResponsiveLayout


def verticalPage(editor):
    page = editor.store.snapshot().pages[editor.pageId]
    page.layout = ResponsiveLayout(direction='vertical')
    for item in page.components:
        item.responsive.preferredHeight = 70
    page.components[0].type = 'page_title'
    install(editor, page)


@pytest.mark.parametrize('zoom', [.75, 1, 1.5])
@pytest.mark.parametrize('finish', ['drop', 'escape'])
@pytest.mark.parametrize('terminal', [False, True])
def testFirstTitleExtendsCanvasAndCommitsActualPosition(responsivePage, zoom, finish, terminal, tmp_path):  # noqa: F811
    coordinator, editor, keys = responsivePage
    verticalPage(editor)
    canvas, tools = editor.canvas, editor.tools.responsive
    if terminal:
        canvas.setPreviewSize(768, 640)
        settle()
    tools.select([keys[0]])
    canvas.setZoomFactor(zoom)
    settle()
    canvas.centerOn(canvas._viewportCenter() - QPointF(0, 250 / zoom))
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    bounds, size, transform = rootRects(editor), editor.renderer.size(), canvas.transform()
    center, autoFit = canvas._viewportCenter(), canvas.autoFit
    viewport = canvas.viewport()
    card = tools.cards()[keys[0]]
    hotspot = QPoint(card.width() // 2, 8)
    point = viewport.mapFromGlobal(canvas.widgetScreenPoint(card, hotspot)) - QPoint(0, round(60 * zoom))
    data = mime({'move': keys[0], 'moves': [keys[0]], 'hotspot': [hotspot.x(), hotspot.y()]})
    enter = QDragEnterEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(viewport, enter)
    settle()
    assert enter.isAccepted() and tools.preview is not None
    assert not tools.preview.extension.isEmpty()
    assert editor.renderer.size() == size and rootRects(editor) == bounds
    assert canvas.transform() == transform and canvas.autoFit == autoFit
    assert coordinator.session.payload() == before and len(coordinator.session._undo) == history
    assert set(tools.preview.origins) == {keys[0]}
    assert tools.preview.placements[keys[0]].top() < tools.preview.origins[keys[0]].top()
    assert not any(mark.isVisible() for mark in tools.decorations)
    assert set(tools.preview.placements) == {keys[0]}
    candidate = dict(tools.candidateRects)
    # Drag farther up: the actual saved top and target must follow the pointer.
    point -= QPoint(0, round(20 * zoom))
    for _ in range(2):
        move = QDragMoveEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(viewport, move)
        settle()
        assert move.isAccepted() and tools.preview is not None
    assert tools.candidateTop < -70
    candidate, candidateTop = dict(tools.candidateRects), tools.candidateTop
    screenTarget = tools.preview.placements[keys[0]]
    if finish == 'escape':
        QTest.keyClick(canvas, Qt.Key_Escape)
        assert coordinator.session.payload() == before and len(coordinator.session._undo) == history
    else:
        drop = QDropEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(viewport, drop)
        assert drop.isAccepted()
        settle()
        page = editor.store.snapshot().pages[editor.pageId]
        assert [item.componentId for item in page.components] == keys
        assert page.canvasTop == page.components[0].responsive.canvasOffsetY == candidateTop
        assert editor.renderer.height() == size.height() - (0 if terminal else candidateTop)
        if terminal:
            scroll = editor.renderer.pages[editor.pageId]
            assert scroll.viewport().width() == 768 and scroll.viewport().height() == 640
            assert scroll.verticalScrollBar().maximum() >= -candidateTop
        actualCard = tools.cards()[keys[0]]
        actualScreen = canvas.rendererRectOnViewport(QRect(actualCard.mapTo(editor.renderer, QPoint()), actualCard.size()))
        assert max(abs(a - b) for a, b in zip(actualScreen.getRect(), screenTarget.getRect())) <= 2
        assert len(coordinator.session._undo) == history + 1
        for key, actual in rootRects(editor).items():
            assert max(abs(a - b) for a, b in zip(actual.getRect(), candidate[key].getRect())) <= 2
        coordinator.history()
        assert coordinator.session.payload() == before
        coordinator.history(True)
        assert editor.store.snapshot().pages[editor.pageId].canvasTop == candidateTop
        assert coordinator.window.saveProjectToDirectory(str(tmp_path / 'expanded'))
        assert coordinator.window.loadProjectDirectory(str(tmp_path / 'expanded'))
        reopened = coordinator.session.document().presentation.pages[editor.pageId]
        assert reopened.canvasTop == reopened.components[0].responsive.canvasOffsetY == candidateTop
    settle()
    assert tools.preview is None
    assert not canvas.componentDragActive
    if finish == 'escape':
        assert canvas.transform() == transform and canvas.autoFit == autoFit
        assert (canvas._viewportCenter() - center).manhattanLength() <= 2


def testDragFeedbackPaintsDifferentSourceAndTargetLines(responsivePage):  # noqa: F811
    _coordinator, editor, keys = responsivePage
    verticalPage(editor)
    tools = editor.tools.responsive
    moving = keys[1]
    tools.select([moving])
    target = tools.cards()[keys[0]]
    tools.drop({'move': moving}, target, QPoint(target.width() // 2, 3), preview=True)
    preview = tools.preview
    source = preview.origins[moving]
    destination = preview.placements[moving]
    rendered = preview.grab().toImage()
    # Inspect the actual strokes, rather than only testing configured colors.
    originColors = {rendered.pixelColor(x, source.top() + 4).name()
                    for x in range(source.left() + 8, source.left() + 40)}
    targetColors = {rendered.pixelColor(x, destination.top() + 1).name()
                    for x in range(destination.left() + 8, destination.left() + 40)}
    assert '#b45309' in originColors
    assert '#16845b' in targetColors
    assert not any(mark.isVisible() for mark in tools.decorations)
    tools.cancel()
    assert all(mark.isVisible() for mark in tools.decorations)


def testTopAreaAcceptsPaletteAndLeavingCanvasCancels(responsivePage):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    canvas, tools = editor.canvas, editor.tools.responsive
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    center = canvas._viewportCenter()
    data = mime({'kind': 'page_title'})
    point = QPoint(canvas.viewport().width() // 2, 2)
    for _ in range(2):
        event = QDragEnterEvent(point, Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(canvas.viewport(), event)
        assert event.isAccepted() and tools.preview is not None
        assert tools.preview.origins == {} and tools.candidateRects
        QApplication.sendEvent(canvas.viewport(), QDragLeaveEvent())
        settle()
        assert tools.preview is None
        assert (canvas._viewportCenter() - center).manhattanLength() <= 2
    assert coordinator.session.payload() == before and len(coordinator.session._undo) == history


def testLeavingTopAreaForNestedContainerUsesItsActualTarget(responsivePage):  # noqa: F811
    coordinator, editor, keys = responsivePage
    verticalPage(editor)
    tools, canvas = editor.tools.responsive, editor.canvas
    group = tools.commands().group(editor.pageId, keys[1:3], 'vertical')
    editor.refresh()
    settle()
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    data = mime({'move': keys[0]})
    viewport = canvas.viewport()
    enter = QDragEnterEvent(QPoint(viewport.width() // 2, 2), Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(viewport, enter)
    assert tools.preview is not None and not tools.preview.extension.isEmpty()
    target = tools.cards()[group]
    local = QPoint(target.width() // 2, 4)
    point = viewport.mapFromGlobal(canvas.widgetScreenPoint(target, local))
    move = QDragMoveEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(viewport, move)
    assert move.isAccepted()
    assert tools.lastTarget[0]['parentId'] == group
    center = canvas._viewportCenter()
    expected = dict(tools.candidateRects)
    drop = QDropEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(viewport, drop)
    assert drop.isAccepted()
    settle()
    assert (canvas._viewportCenter() - center).manhattanLength() <= 2
    page = editor.store.snapshot().pages[editor.pageId]
    container = next(item for item in page.components if item.componentId == group)
    assert keys[0] in {item.componentId for item in container.children}
    for key, rect in rootRects(editor).items():
        assert max(abs(a-b) for a, b in zip(rect.getRect(), expected[key].getRect())) <= 2
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert coordinator.session.payload() == before


def testCanvasBoundsRemainWhenTitleMovesBackIntoResponsiveFlow(responsivePage):  # noqa: F811
    coordinator, editor, keys = responsivePage
    verticalPage(editor)
    tools = editor.tools.responsive
    tools.select([keys[0]])
    tools.commands().place(editor.pageId, keys=[keys[0]], index=0, canvasTop=-120)
    editor.refresh()
    tools.select([keys[0]])
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    editor.tools.fields['text'].setText('保留扩展位置')
    editor.tools.commitPending()
    assert editor.store.snapshot().pages[editor.pageId].components[0].responsive.canvasOffsetY == -120
    tools.commands().place(editor.pageId, keys=[keys[0]], index=2)
    page = editor.store.snapshot().pages[editor.pageId]
    assert page.canvasTop == -120
    assert next(item for item in page.components if item.componentId == keys[0]).responsive.canvasOffsetY == 0
    assert len(coordinator.session._undo) == history + 2
    coordinator.history()
    coordinator.history()
    assert coordinator.session.payload() == before


def testHoldingTopEdgeContinuesExpansionAndCancelRestores(responsivePage):  # noqa: F811
    coordinator, editor, keys = responsivePage
    verticalPage(editor)
    tools, canvas = editor.tools.responsive, editor.canvas
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    center = canvas._viewportCenter()
    point = QPoint(canvas.viewport().width() // 2, 2)
    data = mime({'move': keys[0]})
    QApplication.sendEvent(canvas.viewport(), QDragEnterEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier))
    first = tools.candidateTop
    assert canvas.expansionTimer.isActive()
    loop = QEventLoop()
    QTimer.singleShot(80, loop.quit)
    loop.exec_()
    assert tools.candidateTop < first
    assert (canvas._viewportCenter() - center).manhattanLength() > 2
    tools.cancel()
    settle()
    assert not canvas.expansionTimer.isActive()
    assert (canvas._viewportCenter() - center).manhattanLength() <= 2
    assert coordinator.session.payload() == before and len(coordinator.session._undo) == history
