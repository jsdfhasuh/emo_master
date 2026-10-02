"""Native mouse events exercise one-transaction resize and cancellation."""
from PySide2.QtCore import Qt, QPoint, QEvent
from PySide2.QtGui import QMouseEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication
import pytest

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from emo_master.apps.designer.state.presentation_store import _component


def motion(handle, delta):
    local = handle.rect().center() + delta
    event = QMouseEvent(QEvent.MouseMove, local, handle.mapToGlobal(local), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(handle, event)


@pytest.mark.parametrize('axes', ['x', 'y', 'xy'])
def testNativeResizeIsSingleUndoAndSaveReopen(designer, tmp_path, axes):  # noqa: F811
    c, e, key = setupPage(designer, 'number')
    c.session.editPresentation(lambda p: setattr(p.pages[e.pageId].layout, 'columns', 3))
    e.refresh()
    QApplication.processEvents()
    e.tools.select(key)
    selection = e.tools.selection
    handle = next(h for h in selection.handles if h.axes == axes)
    before, history = c.session.payload(), len(c.session._undo)
    QTest.mousePress(handle, Qt.LeftButton, pos=handle.rect().center())
    delta = QPoint(round(selection.grid.width()/3), 90)
    motion(handle, delta)
    assert selection.valid, e.message.text()
    assert c.session.payload() == before
    assert e.tools.gridPreview is not None
    QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.rect().center()+delta)
    QApplication.processEvents()
    item = _component(e.store.snapshot(), e.pageId, key)
    assert (item.layout.columnSpan > 1) == ('x' in axes)
    assert (item.layout.rowSpan > 1) == ('y' in axes)
    assert len(c.session._undo) == history + 1
    after = c.session.payload()
    c.history()
    assert c.session.payload() == before
    c.history(True)
    assert c.session.payload() == after
    saved = e.store.snapshot()
    assert designer.saveProjectToDirectory(str(tmp_path/'resized'))
    assert designer.loadProjectDirectory(str(tmp_path/'resized'))
    assert c.session.document().presentation == saved


@pytest.mark.parametrize('cancel', ['escape', 'overlap', 'bounds', 'rebuild'])
def testInvalidOrCancelledResizeLeavesNoHistory(designer, cancel):  # noqa: F811
    c, e, key = setupPage(designer, 'number')
    c.session.editPresentation(lambda p: setattr(p.pages[e.pageId].layout, 'columns', 2))
    if cancel == 'overlap':
        e.tools.commands().add(e.pageId, 'text', 0, 1)
    e.refresh()
    QApplication.processEvents()
    e.tools.select(key)
    selection = e.tools.selection
    handle = selection.handles[0]
    before, history = c.session.payload(), len(c.session._undo)
    QTest.mousePress(handle, Qt.LeftButton, pos=handle.rect().center())
    motion(handle, QPoint(selection.grid.width() * (2 if cancel == 'bounds' else 1)//2, 0))
    if cancel == 'escape':
        QTest.keyClick(handle, Qt.Key_Escape)
    elif cancel == 'rebuild':
        e.refresh()
    else:
        assert not selection.valid
        QTest.mouseRelease(handle, Qt.LeftButton)
    assert c.session.payload() == before
    assert len(c.session._undo) == history
    assert e.tools.gridPreview is None


def testRefreshTimerAndHandlesRetireOnClose(designer):  # noqa: F811
    _c, e, key = setupPage(designer)
    e.tools.select(key)
    e.tools.laterRefresh()
    assert e.tools.refreshTimer.isActive()
    e.close()
    assert not e.tools.refreshTimer.isActive()
    assert e.tools.selection is None and e.tools.gridPreview is None
