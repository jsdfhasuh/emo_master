"""Changing only the multiselection must keep the active property form."""
import pytest
from PySide2.QtCore import Qt

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, clickCard  # noqa: F401
from emo_master.apps.designer.state.presentation_store import _component


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
