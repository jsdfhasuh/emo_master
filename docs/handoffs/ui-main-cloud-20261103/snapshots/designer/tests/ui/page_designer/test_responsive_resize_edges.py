"""Selected borders expose resize cursors and use the native proxy input path."""
import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QWidget

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, settle  # noqa: F401
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import ResponsiveLayout

EDGES = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)]


def prepare(editor, key, zoom, edge):
    editor.tools.responsive.select([key])
    actions = editor.coordinator.window.designerActions
    actions.actions['alignment_snap'].setChecked(False)
    actions.applyAlignmentPreferences()
    editor.canvas.setZoomFactor(zoom)
    settle()
    card = editor.tools.responsive.cards()[key]
    x, y = edge
    inset = max(1, round(2 / zoom))
    local = QPoint(inset if x < 0 else card.width() - 1 - inset if x > 0 else card.width() // 3,
                   inset if y < 0 else card.height() - 1 - inset if y > 0 else card.height() // 3)
    scene = editor.canvas.proxy.mapToScene(QPointF(card.mapTo(editor.renderer, local)))
    editor.canvas.centerOn(scene)
    settle()
    window = editor.canvas.window().windowHandle()
    screen = editor.canvas.widgetScreenPoint(card, local)
    return window, window.mapFromGlobal(screen), card, local


@pytest.mark.parametrize('zoom', [.5, 1, 1.5])
@pytest.mark.parametrize('edge', EDGES)
def testBorderHoverCursorAndDirectionalResize(responsivePage, zoom, edge):  # noqa: F811
    coordinator, editor, keys = responsivePage
    def configure(p):
        page = p.pages[editor.pageId]
        page.layout = ResponsiveLayout()
        page.components[0].type = 'page_navigation'
        page.components[0].responsive.preferredHeight = 120
    coordinator.session.editPresentation(configure)
    editor.refresh()
    window, point, card, local = prepare(editor, keys[0], zoom, edge)
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    hit = card.childAt(local)
    assert hit is not None and hit in editor.tools.responsive.handles
    expectedCursor = Qt.SizeHorCursor if not edge[1] else Qt.SizeVerCursor if not edge[0] else (
        Qt.SizeFDiagCursor if edge[0] == edge[1] else Qt.SizeBDiagCursor)
    QTest.mouseMove(window, point)
    settle()
    assert editor.canvas.viewport().cursor().shape() == expectedCursor
    assert hit.toolTip()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point)
    state = editor.tools.responsive.resize
    assert state is not None and editor.canvas.scene().mouseGrabberItem() is editor.canvas.proxy
    assert QWidget.mouseGrabber() is None
    # Include perpendicular motion to ensure a side changes only its own axis.
    delta = QPoint(round((-40 if edge[0] > 0 else 40) * zoom), round((-30 if edge[1] < 0 else 30) * zoom))
    QTest.mouseMove(window, point + delta)
    settle()
    candidate = state['candidate']
    original = state['original']
    if edge[0]:
        assert abs(candidate.preferredWidth - state['size'].width() + 40) <= 2
    else:
        assert (candidate.width, candidate.alignSelf, candidate.preferredWidth) == (
            original.width, original.alignSelf, original.preferredWidth)
    if edge[1]:
        assert abs(candidate.preferredHeight - state['size'].height() - 30) <= 2
    else:
        assert candidate.preferredHeight == original.preferredHeight
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point + delta)
    settle()
    assert _component(editor.store.snapshot(), editor.pageId, keys[0]).responsive == candidate
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before


def testPendingPropertyRefreshAndEscapeKeepAllBorderHandles(responsivePage):  # noqa: F811
    coordinator, editor, keys = responsivePage
    window, point, _card, _local = prepare(editor, keys[0], .75, (-1, 0))
    editor.tools.fields['title'].setText('保留待应用标题')
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point)
    state = editor.tools.responsive.resize
    assert state is not None
    accepted, history = editor.store.snapshot(), len(coordinator.session._undo)
    assert _component(accepted, editor.pageId, keys[0]).props.title == '保留待应用标题'
    QTest.mouseMove(window, point + QPoint(30, 0))
    QTest.keyClick(window, Qt.Key_Escape)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point + QPoint(30, 0))
    settle()
    assert editor.tools.responsive.resize is None and QWidget.mouseGrabber() is None
    assert {handle.edges for handle in editor.tools.responsive.handles} == set(EDGES)
    assert editor.store.snapshot() == accepted and len(coordinator.session._undo) == history


@pytest.mark.parametrize('edge', EDGES[:4])
def testSidePerpendicularMotionDoesNotChangePageOrHistory(responsivePage, edge):  # noqa: F811
    coordinator, editor, keys = responsivePage
    window, point, _card, _local = prepare(editor, keys[0], .75, edge)
    coordinator.session.markSaved()
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    delta = QPoint(0, 30) if edge[0] else QPoint(30, 0)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point)
    assert editor.tools.responsive.resize is not None
    QTest.mouseMove(window, point + delta)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point + delta)
    settle()
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
    assert not coordinator.session.dirty
