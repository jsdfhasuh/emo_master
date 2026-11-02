"""Parent navigation selects real groups without losing pending property edits."""
import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, settle  # noqa: F401
from emo_master.apps.designer.state.presentation_store import _component


def nestedGroups(page):
    coordinator, editor, keys = page
    editor.tools.responsive.select(keys[:2])
    editor.tools.responsive.group('horizontal')
    inner = editor.tools.selected
    editor.tools.responsive.select([inner, keys[2]])
    editor.tools.responsive.group('vertical')
    outer = editor.tools.selected

    def name(p):
        _component(p, editor.pageId, inner).props.title = '检测统计'
        _component(p, editor.pageId, outer).props.title = '运行总览'

    coordinator.session.editPresentation(name)
    editor.refresh()
    editor.tools.responsive.select([keys[0]])
    editor.floatingProperties.setExpanded(True)
    settle()
    return keys[0], inner, outer


def parentClick(editor):
    assert editor.tools.parentButton.isVisible()
    QTest.mouseClick(editor.tools.parentButton, Qt.LeftButton)
    settle()


def testNavigateNestedParentsThenPageWithoutEditingHistoryOrZoom(responsivePage):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    _child, inner, outer = nestedGroups(responsivePage)
    editor.canvas.setZoomFactor(.75)
    coordinator.session.markSaved()
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    assert editor.tools.parentLabel.text() == '所属组：检测统计'
    assert editor.tools.parentButton.text() == '选择父组'
    parentClick(editor)
    assert editor.tools.selected == inner and editor.tools.responsive.selectedIds == [inner]
    assert editor.tools.responsiveProperties.groupPanel.isVisible()
    assert editor.tools.responsiveProperties.direction.currentData() == 'horizontal'
    assert editor.tools.parentLabel.text() == '所属组：运行总览'
    parentClick(editor)
    assert editor.tools.selected == outer and editor.tools.responsive.selectedIds == [outer]
    assert editor.tools.responsiveProperties.direction.currentData() == 'vertical'
    assert editor.tools.parentButton.text() == '选择页面'
    parentClick(editor)
    assert editor.tools.selected is None and not editor.tools.responsive.selectedIds
    assert editor.tools.title.text() == '页面属性' and editor.tools.parentRow.isHidden()
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
    assert not coordinator.session.dirty and editor.canvas.getZoomFactor() == .75


@pytest.mark.parametrize('invalid', [False, True])
def testParentNavigationAcceptsValidPendingEditsAndRetainsInvalidInput(responsivePage, invalid):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    child, inner, _outer = nestedGroups(responsivePage)
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    editor.tools.fields['title'].setText('保留的控件标题')
    if invalid:
        editor.tools.fields['fontSize'].setValue(1)
        parentClick(editor)
        assert editor.tools.selected == child and editor.tools.responsive.selectedIds == [child]
        assert editor.tools.fields['title'].text() == '保留的控件标题'
        assert editor.tools.fields['fontSize'].value() == 1
        assert editor.tools.propertyError.isVisible()
        assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
        editor.tools.fields['fontSize'].setValue(0)
    parentClick(editor)
    assert editor.tools.selected == inner and editor.tools.responsive.selectedIds == [inner]
    assert _component(editor.store.snapshot(), editor.pageId, child).props.title == '保留的控件标题'
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before


def testParentPropertiesApplyToGroupAndUndoRestoresChildren(responsivePage):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    _child, inner, _outer = nestedGroups(responsivePage)
    parentClick(editor)
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    editor.tools.responsiveProperties.equalWidth.setChecked(True)
    editor.tools.responsiveProperties.spacing.setValue(24)
    QTest.mouseClick(editor.tools.applyButton, Qt.LeftButton)
    settle()
    group = _component(editor.store.snapshot(), editor.pageId, inner)
    assert group.grid.equalWidth and group.grid.spacing == 24
    assert group.children == _component(before, editor.pageId, inner).children
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before


def testReparentRefreshUpdatesDestinationAndUnnamedLabel(responsivePage):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    child, _inner, outer = nestedGroups(responsivePage)
    coordinator.session.editPresentation(lambda p: setattr(_component(p, editor.pageId, outer).props, 'title', ''))
    editor.tools.responsive.commands().place(editor.pageId, keys=[child], parentId=outer)
    editor.refresh()
    settle()
    assert editor.tools.parentLabel.text() == '所属组：未命名容器'
    before = editor.store.snapshot()
    parentClick(editor)
    assert editor.tools.selected == outer and editor.store.snapshot() == before
