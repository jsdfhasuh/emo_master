"""Changing only the multiselection must keep the active property form."""
import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, clickCard, settle  # noqa: F401
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import ResponsiveLayout


@pytest.mark.parametrize('invalid', [False, True])
def testDeselectOtherCardPreservesPendingPrimaryInputs(responsivePage, invalid):  # noqa: F811
    coordinator, editor, keys = responsivePage
    clickCard(editor, keys[0])
    clickCard(editor, keys[1], Qt.ControlModifier)
    assert editor.tools.responsive.selectedIds == keys[:2]
    assert editor.tools.selected == keys[1]

    coordinator.session.markSaved()
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    title = '保留当前组件的未应用标题'
    editor.tools.fields['title'].setText(title)
    editor.tools.fields['fontSize'].setValue(1 if invalid else 0)

    # Removing a different card leaves the form's primary component unchanged.
    clickCard(editor, keys[0], Qt.ControlModifier)
    assert editor.tools.responsive.selectedIds == [keys[1]]
    assert editor.tools.selected == keys[1]
    assert editor.tools.fields['title'].text() == title
    assert editor.tools.fields['fontSize'].value() == (1 if invalid else 0)
    assert editor.store.snapshot() == before
    assert len(coordinator.session._undo) == history
    assert not coordinator.session.dirty

    if invalid:
        # A genuine primary change still validates the form and leaves invalid
        # input available to correct without changing either selected object.
        clickCard(editor, keys[2])
        assert editor.tools.responsive.selectedIds == [keys[1]]
        assert editor.tools.selected == keys[1]
        assert editor.tools.fields['title'].text() == title
        assert editor.tools.fields['fontSize'].value() == 1
        assert editor.store.snapshot() == before
        assert len(coordinator.session._undo) == history
        editor.tools.fields['fontSize'].setValue(0)

    clickCard(editor, keys[2])
    assert editor.tools.responsive.selectedIds == [keys[2]]
    assert editor.tools.selected == keys[2]
    assert _component(editor.store.snapshot(), editor.pageId, keys[1]).props.title == title
    assert len(coordinator.session._undo) == history + 1


@pytest.mark.parametrize('destination', ['card', 'blank'])
def testPendingSelectionCommitRefreshesCanvasAndSavedMarker(responsivePage, destination):  # noqa: F811
    coordinator, editor, keys = responsivePage
    editor.canvas.setPreviewSize(1100, 720)
    def longPage(p):
        page = p.pages[editor.pageId]
        page.layout = ResponsiveLayout(direction='vertical')
        for item in page.components:
            item.responsive.preferredHeight = 260
    coordinator.session.editPresentation(longPage)
    editor.refresh()
    settle()
    editor.renderer.pages[editor.pageId].verticalScrollBar().setValue(150)
    assert editor.renderer.pages[editor.pageId].verticalScrollBar().value() == 150
    clickCard(editor, keys[1])
    coordinator.session.markSaved()
    coordinator.updateWindowTitle()
    saved = coordinator.session.payload()
    history = len(coordinator.session._undo)
    cleanTitle = coordinator.window.windowTitle()
    editor.tools.fields['title'].setText('Pending title accepted on selection')
    editor.tools.fields['fontSize'].setValue(32)
    if destination == 'card':
        clickCard(editor, keys[0])
    else:
        point = next(QPoint(x, y) for y in range(5, editor.canvas.viewport().height(), 25)
                     for x in range(5, editor.canvas.viewport().width(), 25)
                     if editor.canvas.rendererAt(QPoint(x, y))[0] is None)
        QTest.mouseClick(editor.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        settle()
        assert editor.tools.selected is None
    assert editor.renderer.config == editor.store.snapshot()
    assert editor.renderer.pages[editor.pageId].verticalScrollBar().value() == 150
    rendered = _component(editor.renderer.config, editor.pageId, keys[1])
    assert rendered.props.fontSize == 32
    assert rendered.props.title == 'Pending title accepted on selection'
    assert coordinator.session.dirty and coordinator.window.windowTitle() != cleanTitle
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    settle()
    assert coordinator.session.payload() == saved and not coordinator.session.dirty
    assert coordinator.window.windowTitle() == cleanTitle
    coordinator.history(True)
    settle()
    assert _component(editor.renderer.config, editor.pageId, keys[1]).props.fontSize == 32
