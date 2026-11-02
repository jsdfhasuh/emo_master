"""QWindow input covers Qt grab routing, not visible-desktop/OS acceptance."""
import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QWidget

from test_responsive_editor import responsivePage, settle  # noqa: F401
from test_workspace import designer  # noqa: F401
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.apps.designer.ui.canvas_alignment import Box, snapOffset


def prepare(page, zoom, snap=False, corner=False):
    coordinator, editor, keys = page
    key = keys[1]  # number: its natural height can exceed the preferred height
    editor.canvas.window().resize(1800, 1000)
    editor.tools.responsive.select([key])
    coordinator.window.designerActions.actions['alignment_snap'].setChecked(snap)
    coordinator.window.designerActions.applyAlignmentPreferences()
    editor.canvas.setZoomFactor(zoom)
    settle()
    handle = editor.tools.responsive.handles[0]
    local = handle.mapTo(editor.renderer, handle.rect().center())
    editor.canvas.centerOn(editor.canvas.proxy.mapToScene(QPointF(local)))
    settle()
    return coordinator, editor, key, corner


def press(editor, corner=False):
    handle = editor.tools.responsive.handles[0]
    point = QPoint(2, 2) if corner else handle.rect().center()
    screen = editor.canvas.widgetScreenPoint(handle, point)
    window = editor.canvas.window().windowHandle()
    origin = window.mapFromGlobal(screen)
    QTest.mouseMove(window, origin)
    settle()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, origin)
    assert editor.tools.responsive.resize is not None
    # The proxy, not the embedded QWidget, must retain mouse routing outside
    # the handle/card. Direct sendEvent(viewport) tests cannot detect this bug.
    return window, origin


def release(window, point):
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point)
    settle()


@pytest.mark.parametrize('zoom', [.5, 1, 1.5])
@pytest.mark.parametrize('corner', [False, True], ids=['center', 'offset'])
@pytest.mark.parametrize('snap', [False, True])
@pytest.mark.parametrize('logicalDelta', [(-80, 40), (80, 40)])
def testWindowResizeDeltaPreviewCommitUndo(responsivePage, zoom, corner, snap, logicalDelta):  # noqa: F811
    coordinator, editor, key, _ = prepare(responsivePage, zoom, snap, corner)
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    window, origin = press(editor, corner)
    state = editor.tools.responsive.resize
    size, card = state['size'], state['card']
    delta = QPoint(round(logicalDelta[0] * zoom), round(logicalDelta[1] * zoom))
    expectedWidth = size.width() + logicalDelta[0]
    expectedHeight = size.height() + logicalDelta[1]
    if snap:
        references = [Box(other.x(), other.y(), other.width(), other.height()) for other in
                      editor.tools.responsive.cards().values()
                      if other.parentWidget() is card.parentWidget() and other is not card]
        dx, dy, _ = snapOffset(Box(card.x() + expectedWidth - 1,
                                  card.y() + expectedHeight - 1, 0, 0), references, 6 / zoom, 400 / zoom)
        expectedWidth += round(dx)
        expectedHeight += round(dy)
    QTest.mouseMove(window, origin + delta)
    settle()
    candidate = state['candidate']
    assert abs(candidate.preferredWidth - expectedWidth) <= 2
    assert abs(candidate.preferredHeight - expectedHeight) <= 2
    assert editor.store.snapshot() == before
    assert len(coordinator.session._undo) == history
    release(window, origin + delta)
    item = _component(editor.store.snapshot(), editor.pageId, key).responsive
    assert item == candidate
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before


@pytest.mark.parametrize('zoom', [.5, 1, 1.5])
@pytest.mark.parametrize('snap', [False, True])
def testWindowResizeZeroAndEscapeThenContinue(responsivePage, zoom, snap):  # noqa: F811
    coordinator, editor, key, _ = prepare(responsivePage, zoom, snap)
    coordinator.session.markSaved()
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    window, origin = press(editor)
    release(window, origin)
    assert editor.store.snapshot() == before
    assert len(coordinator.session._undo) == history
    assert not coordinator.session.dirty
    window, origin = press(editor)
    QTest.mouseMove(window, origin + QPoint(-40, 20))
    settle()
    assert editor.tools.responsive.preview is not None
    assert editor.tools.responsive.resize['candidate'] != editor.tools.responsive.resize['original']
    QTest.keyClick(window, Qt.Key_Escape)
    settle()
    assert editor.tools.responsive.resize is None
    assert editor.tools.responsive.preview is None
    assert QWidget.mouseGrabber() is None
    release(window, origin + QPoint(-40, 20))
    assert editor.store.snapshot() == before
    assert len(coordinator.session._undo) == history
    assert not coordinator.session.dirty
    # A cancelled held gesture must not poison the next real edit.
    window, origin = press(editor, True)
    QTest.mouseMove(window, origin + QPoint(-40, 20))
    release(window, origin + QPoint(-40, 20))
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before


@pytest.mark.parametrize('zoom', [.5, 1, 1.5])
def testWindowResizeKeepsProxyGrabAcrossPendingAppearance(responsivePage, zoom):  # noqa: F811
    coordinator, editor, key, _ = prepare(responsivePage, zoom)
    history = len(coordinator.session._undo)
    editor.tools.fields['title'].setText('accepted pending title')
    window, origin = press(editor)
    assert editor.canvas.scene().mouseGrabberItem() is editor.canvas.proxy
    assert QWidget.mouseGrabber() is None
    accepted = editor.store.snapshot()
    assert _component(accepted, editor.pageId, key).props.title == 'accepted pending title'
    assert len(coordinator.session._undo) == history + 1
    state = editor.tools.responsive.resize
    delta = QPoint(round(-80 * zoom), round(40 * zoom))
    QTest.mouseMove(window, origin + delta)
    settle()
    assert abs(state['candidate'].preferredWidth - state['size'].width() + 80) <= 2
    assert abs(state['candidate'].preferredHeight - state['size'].height() - 40) <= 2
    release(window, origin + delta)
    assert editor.tools.responsive.resize is None
    assert len(coordinator.session._undo) == history + 2
    coordinator.history()
    assert editor.store.snapshot() == accepted


@pytest.mark.parametrize('zoom', [.5, 1, 1.5])
def testWindowResizeSaveReopenKeepsLogicalDimensions(responsivePage, zoom, tmp_path):  # noqa: F811
    coordinator, editor, key, _ = prepare(responsivePage, zoom)
    logicalWidth = editor.renderer.width()
    window, origin = press(editor)
    size = editor.tools.responsive.resize['size']
    delta = QPoint(round(-80 * zoom), round(40 * zoom))
    QTest.mouseMove(window, origin + delta)
    release(window, origin + delta)
    item = _component(editor.store.snapshot(), editor.pageId, key).responsive.model_copy()
    assert abs(item.preferredWidth - size.width() + 80) <= 2
    assert abs(item.preferredHeight - size.height() - 40) <= 2
    assert editor.renderer.width() == logicalWidth
    pageId = editor.pageId
    path = tmp_path / 'resized'
    assert coordinator.window.saveProjectToDirectory(str(path))
    assert coordinator.window.loadProjectDirectory(str(path))
    restored = coordinator.session.document().presentation
    assert _component(restored, pageId, key).responsive == item
