"""Native gestures after pending edits and width-dependent container headings."""
import pytest
from PySide2.QtCore import QPoint, Qt, QEvent
from PySide2.QtGui import QMouseEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QWidget
from shiboken2 import isValid

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from test_grid_geometry import settle
from emo_master.apps.designer.main import applyDesignerStyle
from emo_master.apps.designer.state.presentation_store import _component


@pytest.fixture
def styled(designer):  # noqa: F811
    app = QApplication.instance()
    original = app.styleSheet()
    applyDesignerStyle(app)
    yield designer
    app.setStyleSheet(original)


def press(selection, axes):
    handle = next(h for h in selection.handles if h.axes == axes)
    start = handle.mapToGlobal(handle.rect().center())
    QTest.mousePress(handle, Qt.LeftButton, pos=handle.rect().center())
    return handle, start


def move(handle, globalPoint):
    # Pending properties can relocate the same handle during mousePress.
    # Keep the physical pointer trajectory, not its old widget-local position.
    event = QMouseEvent(QEvent.MouseMove, handle.mapFromGlobal(globalPoint), globalPoint,
                       Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(handle, event)


@pytest.mark.parametrize('finish', ['commit', 'escape', 'rebuild', 'close'])
def testPendingAppearanceKeepsGestureAndSingleLayoutTransaction(styled, tmp_path, finish):
    c, e, key = setupPage(styled, 'number')
    styled.resize(1280, 720)
    settle()
    e.tools.select(key)
    before = len(c.session._undo)
    e.tools.fields['title'].setText('本次检测数量与流程状态，请核对图像和数值是否属于同一次检测。' * 3)
    e.tools.fields['fontSize'].setValue(32)
    selection, oldCard = e.tools.selection, e.tools.selection.card
    handle, start = press(selection, 'y')
    assert e.tools.selection is selection and isValid(handle)
    assert selection.card is not oldCard and handle.parentWidget() is selection.card
    assert e.tools.retainedSelection is None
    assert e.renderer.config == e.store.snapshot()
    assert len(c.session._undo) == before + 1  # Accepted form, independent of resize.
    accepted = c.session.payload()
    target = start + QPoint(0, 64)
    move(handle, target)
    assert selection.valid and selection.candidate.rowSpan == 2
    ghost = e.tools.gridPreview.box
    assert c.session.payload() == accepted
    if finish == 'commit':
        QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.mapFromGlobal(target))
        settle()
        actual = e.tools.selection.card.geometry()
        assert abs(ghost.height() - actual.height()) <= 2, (ghost, actual)
        assert abs(ghost.width() - actual.width()) <= 2, (ghost, actual)
        assert len(c.session._undo) == before + 2
        after = c.session.payload()
        c.history()
        assert c.session.payload() == accepted
        c.history(True)
        assert c.session.payload() == after
        assert styled.saveProjectToDirectory(str(tmp_path / 'saved'))
        assert styled.loadProjectDirectory(str(tmp_path / 'saved'))
        assert c.session.payload()['presentation'] == after['presentation']
    else:
        if finish == 'escape':
            QTest.keyClick(handle, Qt.Key_Escape)
            QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.mapFromGlobal(target))
        elif finish == 'rebuild':
            e.refresh()
        else:
            e.close()
        assert c.session.payload() == accepted and len(c.session._undo) == before + 1
        assert e.tools.gridPreview is None
    assert QWidget.mouseGrabber() is None
    assert QWidget.keyboardGrabber() is None


def testInvalidPendingAppearanceDoesNotStartResize(styled):
    c, e, key = setupPage(styled, 'number')
    settle()
    e.tools.select(key)
    before, history = c.session.payload(), len(c.session._undo)
    e.tools.fields['fontSize'].setValue(1)  # Model permits automatic or 8..48.
    selection = e.tools.selection
    handle, start = press(selection, 'y')
    move(handle, start + QPoint(0, 64))
    QTest.mouseRelease(handle, Qt.LeftButton)
    assert selection.active is None and e.tools.gridPreview is None
    assert c.session.payload() == before and len(c.session._undo) == history
    assert e.tools.fields['fontSize'].value() == 1
    assert QWidget.mouseGrabber() is None and QWidget.keyboardGrabber() is None
    e.tools.fields['fontSize'].setValue(0)


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('fontSize', [0, 24])
def testContainerTitleWrapUsesSameLiveAndPreviewLayout(styled, nested, fontSize):
    c, e, key = setupPage(styled, 'container')
    if nested:
        outer = e.tools.commands().add(e.pageId, 'container', 1, 0)
        c.session.editPresentation(lambda p: setattr(_component(p, e.pageId, outer).grid, 'columns', 2))
        e.tools.commands().move(e.pageId, key, 0, 0, outer)
    else:
        c.session.editPresentation(lambda p: setattr(p.pages[e.pageId].layout, 'columns', 2))
    child = e.tools.commands().add(e.pageId, 'number', 0, 0, key)
    e.refresh()
    e.tools.select(key)
    e.tools.fields['title'].setText('This container has a long descriptive title ' * 8)
    e.tools.fields['fontSize'].setValue(fontSize)
    e.tools.apply()
    e.refresh()
    styled.resize(1280, 720)
    settle()
    e.tools.select(key)
    selection = e.tools.selection
    before, history = c.session.payload(), len(c.session._undo)
    delta = QPoint(selection.card.parentWidget().width() // 2, 0)
    handle, start = press(selection, 'x')
    move(handle, start + delta)
    assert selection.valid
    ghost = e.tools.gridPreview.box
    assert c.session.payload() == before
    QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.mapFromGlobal(start + delta))
    settle()
    card = e.tools.selection.card
    actual = card.geometry()
    assert abs(ghost.height() - actual.height()) <= 2, (ghost, actual)
    assert abs(ghost.width() - actual.width()) <= 2, (ghost, actual)
    label = card.containerTitle.label
    childCard = e.renderer.widgets[e.pageId][child][1].parentWidget()
    assert childCard.mapTo(card, QPoint()).y() > label.geometry().bottom()
    assert len(c.session._undo) == history + 1
    c.history()
    assert c.session.payload() == before


def testTitledContainerGridAcceptsDropAndChildResize(styled):
    c, e, parent = setupPage(styled, 'container')
    e.tools.fields['title'].setText('Named container')
    e.tools.fields['columns'].setValue(2)
    e.tools.apply()
    e.refresh()
    settle()
    e.tools.select(parent)
    grid = e.tools.selection.card.containerTitle.body
    point = grid.layout().cellRect(0, 0).center()
    e.tools.drop({'kind': 'number'}, grid, point)
    child = e.tools.selected
    e.refresh()
    settle()
    assert _component(e.store.snapshot(), e.pageId, parent).children[0].componentId == child
    selection = e.tools.selection
    handle, start = press(selection, 'x')
    move(handle, start + QPoint(grid.width() // 2, 0))
    assert selection.valid and selection.columns == 2
    QTest.keyClick(handle, Qt.Key_Escape)
    QTest.mouseRelease(handle, Qt.LeftButton)
