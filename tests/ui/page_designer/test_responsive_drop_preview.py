"""Drop candidates match committed native geometry without touching live controls."""
import pytest
from PySide2.QtCore import QPoint, QRect
from PySide2.QtWidgets import QApplication

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, settle  # noqa: F401
from emo_master.apps.designer.page_designer import responsive_preview
from emo_master.core.presentation.models import Component, Props, ResponsiveLayout


def rootRects(editor):
    root = editor.renderer.layoutHosts[editor.pageId][None]
    return {key: QRect(card.mapTo(root, QPoint()), card.size())
            for key, card in editor.renderer.cards[editor.pageId].items()}


def install(editor, page):
    editor.session.editPresentation(lambda p: p.pages.__setitem__(editor.pageId, page))
    editor.refresh()
    settle()


def compareDrop(editor, payload, target, point):
    tools = editor.tools.responsive
    cards = dict(editor.renderer.cards[editor.pageId])
    widgets = dict(editor.renderer.widgets[editor.pageId])
    originalRects = rootRects(editor)
    activeWindow = QApplication.activeWindow()
    before, history = editor.session.payload(), len(editor.session._undo)
    tools.drop(payload, target, point, preview=True)
    rects, size = dict(tools.candidateRects), tools.candidateSize
    assert editor.session.payload() == before
    assert len(editor.session._undo) == history
    assert rootRects(editor) == originalRects
    assert editor.renderer.cards[editor.pageId] == cards
    assert editor.renderer.widgets[editor.pageId] == widgets
    assert QApplication.activeWindow() is activeWindow
    tools.drop(payload, target, point)
    editor.refresh()
    settle()
    actual = rootRects(editor)
    for key in cards:
        expectedValues, actualValues = rects[key].getRect(), actual[key].getRect()
        assert max(abs(a - b) for a, b in zip(expectedValues, actualValues)) <= 2, (key, rects[key], actual[key])
    # Preview and commit intentionally allocate independent IDs for new groups
    # or palette controls, but their number and visible rectangles must agree.
    predictedNew = sorted(rect.getRect() for key, rect in rects.items() if key not in cards)
    actualNew = sorted(rect.getRect() for key, rect in actual.items() if key not in cards)
    assert len(predictedNew) == len(actualNew)
    for predicted, committed in zip(predictedNew, actualNew):
        assert max(abs(a - b) for a, b in zip(predicted, committed)) <= 2, (predicted, committed)
    root = editor.renderer.layoutHosts[editor.pageId][None]
    assert abs(size.width() - root.width()) <= 2
    assert abs(size.height() - root.height()) <= 2, (size, root.size())
    assert len(editor.session._undo) == history + 1
    return rects, size


@pytest.mark.parametrize('width', [800, 360])
def testAutomaticGroupingPreviewMatchesMovedAndUnselectedCards(responsivePage, width):  # noqa: F811
    _coordinator, editor, keys = responsivePage
    page = editor.store.snapshot().pages[editor.pageId]
    page.layout = ResponsiveLayout(direction='vertical')
    install(editor, page)
    editor.previewWidth.setValue(width)
    settle()
    old = rootRects(editor)
    target = editor.renderer.cards[editor.pageId][keys[1]]
    rects, _size = compareDrop(editor, {'move': keys[0]}, target, QPoint(2, target.height() // 2))
    assert rects[keys[2]] != old[keys[2]]


def testCrossContainerPreviewMeasuresWrappedTitlesAndBothParents(responsivePage):  # noqa: F811
    _coordinator, editor, keys = responsivePage
    page = editor.store.snapshot().pages[editor.pageId]
    items = page.components
    page.layout = ResponsiveLayout(direction='vertical')
    page.components = [
        Component(componentId='left', type='container',
                  props=Props(title='检测输入区域与当前工件的说明文字 ' * 3, fontSize=28),
                  grid=ResponsiveLayout(direction='vertical'), children=items[:2]),
        Component(componentId='right', type='container',
                  props=Props(title='检测结果', fontSize=24),
                  grid=ResponsiveLayout(direction='wrap'), children=items[2:]),
    ]
    install(editor, page)
    editor.previewWidth.setValue(500)
    settle()
    target = editor.renderer.cards[editor.pageId][keys[2]]
    compareDrop(editor, {'move': keys[1]}, target, target.rect().center())


def testMultiselectionIntoContainerPreviewMatchesNativeLayout(responsivePage):  # noqa: F811
    _coordinator, editor, keys = responsivePage
    page = editor.store.snapshot().pages[editor.pageId]
    items = page.components
    page.layout = ResponsiveLayout(direction='vertical')
    page.components = [*items[:2], Component(componentId='target', type='container',
        grid=ResponsiveLayout(direction='wrap'), children=items[2:])]
    install(editor, page)
    editor.previewWidth.setValue(800)
    settle()
    target = editor.renderer.cards[editor.pageId][keys[2]]
    compareDrop(editor, {'move': keys[0], 'moves': keys[:2]}, target, target.rect().center())


def testPreviewRecomputesTallPageExtentWhenGroupingReducesRows(responsivePage):  # noqa: F811
    _coordinator, editor, keys = responsivePage
    page = editor.store.snapshot().pages[editor.pageId]
    page.layout = ResponsiveLayout(direction='vertical')
    for item in page.components:
        item.responsive.preferredHeight = 230
    install(editor, page)
    editor.previewWidth.setValue(1100)
    settle()
    root = editor.renderer.layoutHosts[editor.pageId][None]
    oldHeight = root.height()
    scroll = editor.renderer.pages[editor.pageId]
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    assert scroll.verticalScrollBar().value() > 0
    target = editor.renderer.cards[editor.pageId][keys[3]]
    _rects, size = compareDrop(editor, {'move': keys[0], 'moves': keys[:3]},
                             target, QPoint(2, target.height() // 2))
    assert size.height() < oldHeight


@pytest.mark.parametrize('kind', ['text', 'number', 'image', 'table', 'container'])
def testPalettePreviewMeasuresNativePrototype(responsivePage, kind):  # noqa: F811
    _coordinator, editor, keys = responsivePage
    target = editor.renderer.cards[editor.pageId][keys[2]]
    compareDrop(editor, {'kind': kind}, target, target.rect().center())


def testRepeatedTargetCachesGeometryAndCancelPreservesDocument(responsivePage, monkeypatch):  # noqa: F811
    coordinator, editor, keys = responsivePage
    calls = []
    original = responsive_preview.candidateGeometry

    def capture(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(responsive_preview, 'candidateGeometry', capture)
    coordinator.session.markSaved()
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    target = editor.renderer.cards[editor.pageId][keys[2]]
    tools = editor.tools.responsive
    for _ in range(3):
        tools.drop({'move': keys[0]}, target, target.rect().center(), preview=True)
    assert len(calls) == 1
    assert tools.preview is not None and tools.candidateRects
    tools.cancel()
    assert tools.preview is None
    assert coordinator.session.payload() == before
    assert len(coordinator.session._undo) == history
    assert not coordinator.session.dirty


def testPalettePrototypeIsReusedWhenInsertionCandidateChanges(responsivePage):  # noqa: F811
    _coordinator, editor, keys = responsivePage
    tools = editor.tools.responsive
    first = editor.renderer.cards[editor.pageId][keys[0]]
    last = editor.renderer.cards[editor.pageId][keys[2]]
    firstArgs = tools.drop({'kind': 'image'}, first, first.rect().center(), preview=True)
    prototype = tools.prototype
    widget = next(iter(prototype.widgets['prototype'].values()))[1]
    secondArgs = tools.drop({'kind': 'image'}, last, last.rect().center(), preview=True)
    assert secondArgs != firstArgs
    assert tools.prototype is prototype
    assert next(iter(prototype.widgets['prototype'].values()))[1] is widget
    tools.cancel()
    assert tools.prototype is None


def testPreviewAndCancelKeepExistingImageAndTableState(responsivePage):  # noqa: F811
    coordinator, editor, keys = responsivePage
    page = editor.store.snapshot().pages[editor.pageId]
    page.components[0].type = 'image'
    page.components[1].type = 'table'
    page.components[1].props.pageSize = 1
    install(editor, page)
    image = editor.renderer.widgets[editor.pageId][keys[0]][1]
    table = editor.renderer.widgets[editor.pageId][keys[1]][1]
    table.move(1)
    table.table.selectRow(0)
    assert table.model.page == 1
    imageKey, selectedRows = image.image.cacheKey(), table.table.selectionModel().selectedRows()
    state = coordinator.session.payload()
    cards = dict(editor.renderer.cards[editor.pageId])
    target = cards[keys[2]]
    tools = editor.tools.responsive
    tools.drop({'move': keys[0]}, target, target.rect().center(), preview=True)
    tools.cancel()
    assert editor.renderer.widgets[editor.pageId][keys[0]][1] is image
    assert editor.renderer.widgets[editor.pageId][keys[1]][1] is table
    assert editor.renderer.cards[editor.pageId] == cards
    assert image.image.cacheKey() == imageKey
    assert table.model.page == 1
    assert table.table.selectionModel().selectedRows() == selectedRows
    assert coordinator.session.payload() == state
