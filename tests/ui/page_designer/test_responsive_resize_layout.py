"""A resize must change rendered geometry, including inside an equal-width row."""
import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, settle  # noqa: F401
from test_responsive_resize_edges import prepare
from emo_master.apps.designer.page_designer.responsive_editing import childrenFor, layoutFor
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import ResponsiveLayout


def configure(page, direction, nested):
    coordinator, editor, keys = page
    parent = None
    if nested:
        editor.tools.responsive.select(keys)
        editor.tools.responsive.group(direction)
        parent = editor.tools.selected

    def edit(p):
        root = p.pages[editor.pageId]
        if nested:
            root.layout = ResponsiveLayout()
        rules = layoutFor(p, editor.pageId, parent)
        rules.direction, rules.equalWidth = direction, True
        for item in childrenFor(p, editor.pageId, parent):
            item.type = 'text'
            item.props.title, item.props.text = '运行时间', '检测信息'
            item.responsive.preferredHeight = 96

    coordinator.session.editPresentation(edit)
    editor.refresh()
    settle()
    return parent, keys[0]


@pytest.mark.parametrize('direction', ['horizontal', 'wrap'])
@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('zoom', [.75, 1.5])
def testWidthDragChangesActualPreviewAndCardWithoutEditingSiblingFields(responsivePage, direction, nested, zoom):  # noqa: F811
    coordinator, editor, keys = responsivePage
    parent, key = configure(responsivePage, direction, nested)
    window, start, card, _local = prepare(editor, key, zoom, (1, 0))
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    originalWidth = card.width()
    end = start + QPoint(round((180 - originalWidth) * zoom), 0)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, end)
    settle()
    previewSize = editor.tools.responsive.preview.box.size()
    assert abs(previewSize.width() - 180) <= 2
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    settle()
    after = editor.store.snapshot()
    actual = editor.tools.responsive.cards()[key].size()
    assert actual == previewSize and abs(actual.width() - 180) <= 2
    assert not layoutFor(after, editor.pageId, parent).equalWidth
    assert editor.message.text() == '尺寸已应用；本组已取消等宽'
    assert len(coordinator.session._undo) == history + 1
    for sibling in keys[1:]:
        assert _component(after, editor.pageId, sibling) == _component(before, editor.pageId, sibling)
    coordinator.history()
    assert editor.store.snapshot() == before
    coordinator.history(True)
    settle()
    assert editor.store.snapshot() == after and editor.tools.responsive.cards()[key].size() == actual


@pytest.mark.parametrize('nested', [False, True])
def testHeightDragOverridesRowStretchButRetainsWidthAndEqualWidth(responsivePage, nested):  # noqa: F811
    coordinator, editor, keys = responsivePage
    parent, key = configure(responsivePage, 'horizontal', nested)
    coordinator.session.editPresentation(lambda p: setattr(
        _component(p, editor.pageId, keys[1]).responsive, 'preferredHeight', 260))
    editor.refresh()
    window, start, card, _local = prepare(editor, key, 1, (0, 1))
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    assert card.height() == 260
    width = card.width()
    end = start + QPoint(0, -60)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, end)
    settle()
    assert editor.tools.responsive.preview.box.height() == 200
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    settle()
    assert editor.tools.responsive.cards()[key].height() == 200
    assert editor.tools.responsive.cards()[key].width() == width
    assert layoutFor(editor.store.snapshot(), editor.pageId, parent).equalWidth
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before


@pytest.mark.parametrize('cancel', ['escape', 'return'])
def testCancelledWidthDragKeepsEqualWidthAndHistory(responsivePage, cancel):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    _parent, key = configure(responsivePage, 'wrap', True)
    window, start, _card, _local = prepare(editor, key, 1, (1, 0))
    coordinator.session.markSaved()
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    end = start + QPoint(-80, 0)
    QTest.mouseMove(window, end)
    if cancel == 'escape':
        QTest.keyClick(window, Qt.Key_Escape)
    else:
        end = start
        QTest.mouseMove(window, start)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    settle()
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
    assert not coordinator.session.dirty
    assert editor.message.text() == ('已取消尺寸调整' if cancel == 'escape' else '尺寸未改变')


def testResizedEqualWidthGroupSurvivesDesignerSaveReopen(responsivePage, tmp_path):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    parent, key = configure(responsivePage, 'horizontal', True)
    window, start, card, _local = prepare(editor, key, 1, (1, 0))
    end = start + QPoint(180 - card.width(), 0)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, end)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    settle()
    expected, pageId = editor.store.snapshot(), editor.pageId
    assert editor.tools.responsive.cards()[key].width() == 180
    directory = tmp_path / 'resized-equal-width'
    assert coordinator.window.saveProjectToDirectory(str(directory))
    assert coordinator.window.loadProjectDirectory(str(directory))
    coordinator.showPages()
    editor.pageId = pageId
    editor.refresh()
    settle()
    assert editor.store.snapshot() == expected
    assert not layoutFor(expected, pageId, parent).equalWidth
    assert editor.renderer.cards[pageId][key].width() == 180


def testResizeFeedbackUsesNaturalContentHeight(responsivePage):  # noqa: F811
    coordinator, editor, _keys = responsivePage
    _parent, key = configure(responsivePage, 'horizontal', False)

    def edit(p):
        item = _component(p, editor.pageId, key)
        item.props.fontSize = 32
        item.responsive.preferredHeight = 300

    coordinator.session.editPresentation(edit)
    editor.refresh()
    window, start, card, _local = prepare(editor, key, 1, (0, 1))
    end = start + QPoint(0, 64 - card.height())
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, end)
    settle()
    preview = editor.tools.responsive.preview
    actualSize = preview.box.size()
    assert actualSize.height() > 64
    assert f'{actualSize.width()} × {actualSize.height()}' in preview.message
    assert '高度保留内容所需空间' in preview.message
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    settle()
    assert editor.tools.responsive.cards()[key].size() == actualSize
