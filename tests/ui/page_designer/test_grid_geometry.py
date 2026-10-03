"""Review regressions: painted layout, pointer hit test and committed geometry agree."""
import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtWidgets import QApplication, QLabel

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.apps.designer.page_designer.canvas_tools import cellAt, cellBox
from emo_master.core.presentation.models import Placement
from emo_master.ui.presentation.renderer import RuntimePages


def settle():
    for _ in range(5):
        QApplication.processEvents()


def testUnequalColumnsUseActualCellsForDropAndPreview(designer):  # noqa: F811
    c, e, key = setupPage(designer, 'navigation_button')
    def configure(p):
        p.pages[e.pageId].layout.columns = 3
        _component(p, e.pageId, key).props.text = 'A long navigation button label forcing its minimum width'
    c.session.editPresentation(configure)
    e.refresh()
    designer.resize(1280, 720)
    settle()
    grid = e.renderer.pages[e.pageId].widget()
    first = grid.layout().cellRect(0, 0)
    point = QPoint(first.right() - 2, grid.layout().cellRect(1, 0).center().y())
    assert cellAt(grid, 3, point) == (1, 0)
    box = cellBox(grid, 3, Placement(row=1))
    assert box.left() == first.left() and box.right() == first.right()
    history = len(c.session._undo)
    e.tools.drop({'kind': 'number'}, grid, point)
    added = _component(e.store.snapshot(), e.pageId, e.tools.selected)
    assert added.layout.row == 1 and added.layout.column == 0
    assert len(c.session._undo) == history + 1
    assert cellAt(grid, 3, QPoint(-1, point.y()))[1] == -1
    assert cellAt(grid, 3, QPoint(grid.width(), point.y()))[1] == 3


@pytest.mark.parametrize('kind', ['number', 'image', 'table'])
@pytest.mark.parametrize('axes', ['y', 'xy'])
@pytest.mark.parametrize('nested', [False, True])
def testResizeGhostMatchesCommittedCardWithoutMovingLiveWidgets(designer, kind, axes, nested):  # noqa: F811
    c, e, key = setupPage(designer, kind)
    if nested:
        parent = e.tools.commands().add(e.pageId, 'container', 1, 0)
        c.session.editPresentation(lambda p: setattr(_component(p, e.pageId, parent).grid, 'columns', 2))
        e.tools.commands().move(e.pageId, key, 0, 0, parent)
    else:
        c.session.editPresentation(lambda p: setattr(p.pages[e.pageId].layout, 'columns', 2))
    e.refresh()
    designer.resize(1600, 900)
    settle()
    e.tools.select(key)
    selection = e.tools.selection
    handle = next(h for h in selection.handles if h.axes == axes)
    before, geometry, history = c.session.payload(), selection.card.geometry(), len(c.session._undo)
    selection.begin(handle, QPoint())
    selection.move(QPoint(selection.grid.width() // 2 if axes == 'xy' else 0, 64))
    assert selection.valid, e.message.text()
    ghost = e.tools.gridPreview.box
    assert ghost is not None
    assert c.session.payload() == before
    assert selection.card.geometry() == geometry
    selection.finish()
    settle()
    actual = e.tools.selection.card.geometry()
    assert abs(ghost.height() - actual.height()) <= 2, (ghost, actual)
    assert abs(ghost.width() - actual.width()) <= 2, (ghost, actual)
    assert len(c.session._undo) == history + 1
    c.history()
    assert c.session.payload() == before


def testContainerTitleSurvivesChildrenReopenAndStandaloneRendering(designer, tmp_path):  # noqa: F811
    c, e, key = setupPage(designer, 'container')
    e.tools.fields['title'].setText('Container <title>')
    e.tools.apply()
    child = e.tools.commands().add(e.pageId, 'text', 0, 0, key)
    e.refresh()
    settle()
    title = e.renderer.pages[e.pageId].findChild(QLabel, 'containerTitle')
    assert title.text() == 'Container <title>' and title.textFormat() == Qt.PlainText
    card = e.renderer.widgets[e.pageId][child][1].parentWidget()
    assert card.mapTo(title.parentWidget(), QPoint()).y() > title.geometry().bottom()
    assert card.parentWidget().layout().getItemPosition(0) == (0, 0, 1, 1)
    saved = e.store.snapshot()
    assert designer.saveProjectToDirectory(str(tmp_path / 'titled'))
    assert designer.loadProjectDirectory(str(tmp_path / 'titled'))
    assert c.session.document().presentation == saved
    runtime = RuntimePages(saved)
    runtime.show()
    settle()
    title = runtime.pages[saved.defaultPageId].findChild(QLabel, 'containerTitle')
    assert title.text() == 'Container <title>' and title.isVisible()
    card = runtime.widgets[saved.defaultPageId][child][1].parentWidget()
    assert card.mapTo(title.parentWidget(), QPoint()).y() > title.geometry().bottom()
    runtime.close()


def testShrinkingSpanPreviewAndCancellationPreserveState(designer):  # noqa: F811
    c, e, key = setupPage(designer, 'image')
    def configure(p):
        p.pages[e.pageId].layout.columns = 3
        _component(p, e.pageId, key).layout = Placement(rowSpan=3, columnSpan=2)
    c.session.editPresentation(configure)
    e.refresh()
    settle()
    e.tools.select(key)
    before, history = c.session.payload(), len(c.session._undo)
    selection = e.tools.selection
    handle = next(h for h in selection.handles if h.axes == 'xy')
    selection.begin(handle, QPoint())
    selection.move(QPoint(-selection.grid.width() // 3, -64))
    assert selection.valid
    assert selection.candidate == Placement(rowSpan=2, columnSpan=1)
    selection.cancel()
    assert c.session.payload() == before and len(c.session._undo) == history
    assert e.tools.gridPreview is None
    selection.begin(handle, QPoint())
    selection.move(QPoint(-selection.grid.width() // 3, -64))
    ghost = e.tools.gridPreview.box
    selection.finish()
    settle()
    actual = e.tools.selection.card.geometry()
    assert abs(ghost.height() - actual.height()) <= 2, (ghost, actual)
    assert abs(ghost.width() - actual.width()) <= 2, (ghost, actual)
    assert len(c.session._undo) == history + 1
