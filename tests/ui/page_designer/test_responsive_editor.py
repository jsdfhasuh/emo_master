"""Current-format page editing through the real Designer workspace."""
import json

import pytest
from PySide2.QtCore import QEvent, QPoint, QPointF, Qt
from PySide2.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QImage, QColor, QMouseEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QInputDialog

from test_workspace import designer  # noqa: F401
from emo_master.core.presentation.models import ResponsiveLayout, TableColumn
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.apps.designer.page_designer.tools import MIME, mime


def settle():
    for _ in range(6):
        QApplication.processEvents()


def clickCard(editor, key, modifiers=Qt.NoModifier):
    card = editor.renderer.cards[editor.pageId][key]
    point = card.mapTo(editor.renderer, card.rect().center())
    scenePoint = editor.canvas.proxy.mapToScene(QPointF(point))
    QTest.mouseClick(editor.canvas.viewport(), Qt.LeftButton, modifiers, editor.canvas.mapFromScene(scenePoint))
    settle()


@pytest.fixture
def responsivePage(designer, monkeypatch):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('响应式运行页', True))
    editor.run(editor.newPage)
    assert editor.pageId is not None, editor.message.text()
    keys = [editor.tools.responsive.commands().place(editor.pageId, kind=kind)[0]
            for kind in ('text', 'number', 'text', 'number')]
    coordinator.session.editPresentation(lambda p: setattr(p.pages[editor.pageId], 'layout',
        ResponsiveLayout(direction='wrap', equalWidth=True)))
    editor.refresh()
    designer.resize(1500, 900)
    settle()
    return coordinator, editor, keys


def testNewPageUsesProxyCanvasAndWidthPreviewReflowsWithoutMutation(responsivePage):
    coordinator, editor, keys = responsivePage
    document = coordinator.session.document()
    assert document.schemaVersion == '2.8'
    assert document.presentation.schemaVersion == '1.1'
    assert editor.canvas.proxy.widget() is editor.renderer
    assert editor.renderer.config.pages[editor.pageId].layout.type == 'responsive'
    cards = dict(editor.renderer.cards[editor.pageId])
    coordinator.session.markSaved()
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    rows = []
    geometry = []
    # Include page chrome/margins: two 240px cards plus spacing need more
    # than a 540px outer renderer, whose content area is only 482px wide.
    for width in (1100, 600, 340, 1100):
        editor.previewWidth.setValue(width)
        settle()
        assert editor.renderer.width() == width
        assert all(editor.renderer.cards[editor.pageId][key] is cards[key] for key in keys)
        rows.append(len({cards[key].y() for key in keys}))
        host = editor.renderer.layoutHosts[editor.pageId][None]
        geometry.append((width, host.width(), [cards[key].geometry().getRect() for key in keys]))
        assert all(0 <= card.x() and card.geometry().right() < host.width() for card in cards.values())
        assert coordinator.session.payload() == before
        assert len(coordinator.session._undo) == history and not coordinator.session.dirty
    assert rows == [1, 2, 4, 1], geometry
    editor.canvas.setZoomFactor(.1)
    coordinator.window.designerActions.invoke('fit')
    settle()
    assert editor.canvas.getZoomFactor() > .1
    assert coordinator.session.payload() == before and len(coordinator.session._undo) == history


def testNativeCtrlSelectionGroupUngroupAndUndoPreserveComponentIds(responsivePage):
    coordinator, editor, keys = responsivePage
    clickCard(editor, keys[0])
    clickCard(editor, keys[1], Qt.ControlModifier)
    assert set(editor.tools.responsive.selectedIds) == set(keys[:2])
    assert editor.tools.selectionCount() == 2
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    editor.tools.responsive.group('horizontal')
    settle()
    groupId = editor.tools.selected
    group = _component(editor.store.snapshot(), editor.pageId, groupId)
    assert group.type == 'container' and group.grid.direction == 'horizontal'
    assert [item.componentId for item in group.children] == keys[:2]
    assert len(coordinator.session._undo) == history + 1
    grouped = editor.store.snapshot()
    coordinator.history()
    assert editor.store.snapshot() == before
    coordinator.history(True)
    assert editor.store.snapshot() == grouped
    editor.tools.responsive.select([groupId])
    editor.tools.responsive.ungroup()
    settle()
    assert [item.componentId for item in editor.store.snapshot().pages[editor.pageId].components] == keys
    assert len(coordinator.session._undo) == history + 2


def testRealPageMenuAlignmentPreservesUnselectedSiblingAndHiddenFlow(responsivePage):
    coordinator, editor, keys = responsivePage
    # Vertical layout has horizontal per-component alignment, so partial
    # selection must not silently change the parent or the remaining siblings.
    coordinator.session.editPresentation(lambda p: setattr(p.pages[editor.pageId], 'layout',
        ResponsiveLayout(direction='vertical')))
    editor.refresh()
    editor.tools.responsive.select(keys[:2])
    registry = coordinator.window.designerActions
    flow = coordinator.window.flowScene.getNodePositions()
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    assert registry.blockedReason('align_right') == ''
    registry.invoke('align_right')
    after = editor.store.snapshot()
    for key in keys[:2]:
        assert _component(after, editor.pageId, key).responsive.alignSelf == 'end'
    for key in keys[2:]:
        assert _component(after, editor.pageId, key) == _component(before, editor.pageId, key)
    assert after.pages[editor.pageId].layout == before.pages[editor.pageId].layout
    assert coordinator.window.flowScene.getNodePositions() == flow
    assert len(coordinator.session._undo) == history + 1
    registry.invoke('align_right')
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before
    editor.tools.responsive.select(keys)
    registry.invoke('distribute_vertical')
    assert editor.store.snapshot().pages[editor.pageId].layout.justify == 'space-between'


def testCrossContainerPlacementReordersAndSaveReopenRetainsLayout(responsivePage, tmp_path):
    coordinator, editor, keys = responsivePage
    editor.tools.responsive.select(keys[:2])
    editor.tools.responsive.group('vertical')
    groupId = editor.tools.selected
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    commands = editor.tools.responsive.commands()
    assert commands.place(editor.pageId, keys=[keys[2]], parentId=groupId, index=1) == [keys[2]]
    editor.refresh()
    moved = editor.store.snapshot()
    assert [item.componentId for item in _component(moved, editor.pageId, groupId).children] == [keys[0], keys[2], keys[1]]
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before
    coordinator.history(True)
    assert editor.store.snapshot() == moved
    commands.place(editor.pageId, keys=[keys[2]], parentId=None, index=0)
    editor.refresh()
    ordered = editor.store.snapshot()
    assert [item.componentId for item in ordered.pages[editor.pageId].components] == [keys[2], groupId, keys[3]]
    directory = tmp_path / 'responsive'
    assert coordinator.window.saveProjectToDirectory(str(directory))
    assert coordinator.window.loadProjectDirectory(str(directory))
    assert coordinator.session.document().schemaVersion == '2.8'
    assert coordinator.session.document().presentation == ordered


def testPreviewAndResizeEscapeDoNotMutateDraft(responsivePage):
    coordinator, editor, keys = responsivePage
    editor.tools.responsive.select([keys[0]])
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    target = editor.renderer.cards[editor.pageId][keys[2]]
    editor.tools.responsive.drop({'move': keys[0]}, target, target.rect().center(), preview=True)
    assert editor.tools.responsive.preview is not None
    assert editor.store.snapshot() == before
    assert editor.tools.responsive.cancel()
    assert editor.tools.responsive.preview is None and editor.store.snapshot() == before
    handle = editor.tools.responsive.handles[0]
    # Use the proxy's actual scene mapping to keep the simulated pointer in
    # the same coordinates as the editor after zooming.
    card = editor.renderer.cards[editor.pageId][keys[0]]
    rendererPoint = card.mapTo(editor.renderer, card.rect().bottomRight() - QPoint(3, 3))
    start = editor.canvas.viewport().mapToGlobal(editor.canvas.mapFromScene(editor.canvas.proxy.mapToScene(QPointF(rendererPoint))))
    editor.tools.responsive.beginResize(keys[0], handle, start)
    editor.tools.responsive.moveResize(start + QPoint(40, 30))
    assert editor.tools.responsive.preview is not None
    QTest.keyClick(editor.canvas, Qt.Key_Escape)
    assert editor.tools.responsive.resize is None and editor.tools.responsive.preview is None
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history


def testNativeDropCommitsPendingTitleOnceAndLeavingPreviewCancels(responsivePage):
    coordinator, editor, keys = responsivePage
    editor.tools.responsive.select([keys[0]])
    target = editor.renderer.cards[editor.pageId][keys[2]]
    point = target.rect().center()
    data = mime({'move': keys[0], 'moves': [keys[0]]})
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    enter = QDragEnterEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted() and editor.tools.responsive.preview is not None
    QApplication.sendEvent(target, QDragLeaveEvent())
    assert editor.tools.responsive.preview is None
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
    editor.tools.fields['title'].setText('保留未应用标题')
    enter = QDragEnterEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    drop = QDropEvent(QPointF(point), Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, drop)
    assert drop.isAccepted(), editor.message.text()
    settle()
    after = editor.store.snapshot()
    assert [item.componentId for item in after.pages[editor.pageId].components] == [keys[1], keys[2], keys[0], keys[3]]
    assert _component(after, editor.pageId, keys[0]).props.title == '保留未应用标题'
    assert len(coordinator.session._undo) == history + 2
    coordinator.history()
    assert [item.componentId for item in editor.store.snapshot().pages[editor.pageId].components] == keys
    assert _component(editor.store.snapshot(), editor.pageId, keys[0]).props.title == '保留未应用标题'
    coordinator.history()
    assert editor.store.snapshot() == before


def testNestedNativeImageAndTableKeepStateDuringWidthChanges(responsivePage):
    coordinator, editor, _keys = responsivePage
    commands = editor.tools.responsive.commands()
    groupId = commands.place(editor.pageId, kind='container')[0]
    imageId = commands.place(editor.pageId, kind='image', parentId=groupId)[0]
    tableId = commands.place(editor.pageId, kind='table', parentId=groupId)[0]
    def configure(p):
        p.pages[editor.pageId].layout = ResponsiveLayout(direction='vertical')
        group = _component(p, editor.pageId, groupId)
        group.props.title = '检测结果'
        group.grid = ResponsiveLayout(direction='wrap')
        table = _component(p, editor.pageId, tableId)
        table.props.columns = [TableColumn(title='检测值', fieldPath=['value'])]
        table.props.pageSize = 2
    coordinator.session.editPresentation(configure)
    editor.refresh()
    settle()
    image = editor.renderer.widgets[editor.pageId][imageId][1]
    table = editor.renderer.widgets[editor.pageId][tableId][1]
    bitmap = QImage(160, 90, QImage.Format_RGB32)
    bitmap.fill(QColor('green'))
    image.setImage(bitmap, 'local-image')
    table.submit([{'value': i} for i in range(5)], 'local-table', '')
    table.move(1)
    assert table.model.page == 1
    state = coordinator.session.payload()
    rows = []
    for width in (1100, 340, 1100):
        editor.previewWidth.setValue(width)
        settle()
        assert editor.renderer.widgets[editor.pageId][imageId][1] is image
        assert editor.renderer.widgets[editor.pageId][tableId][1] is table
        assert image.image.cacheKey() == bitmap.cacheKey() and image.key == 'local-image'
        assert table.model.page == 1 and table.model.key == 'local-table'
        host = editor.renderer.layoutHosts[editor.pageId][groupId]
        cards = editor.renderer.cards[editor.pageId]
        assert cards[imageId].geometry().right() < host.width()
        assert cards[tableId].geometry().right() < host.width()
        rows.append(cards[imageId].y() == cards[tableId].y())
        assert coordinator.session.payload() == state
    assert rows == [True, False, True]


def testInvalidPendingFormRejectsNativeDropAndAllowsCorrection(responsivePage):
    coordinator, editor, keys = responsivePage
    editor.tools.responsive.select([keys[0]])
    editor.tools.fields['title'].setText('保留输入后再移动')
    editor.tools.fields['fontSize'].setValue(1)
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    target = editor.renderer.cards[editor.pageId][keys[2]]
    point = target.rect().center()
    data = mime({'move': keys[0]})
    def drop():
        enter = QDragEnterEvent(point, Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(target, enter)
        event = QDropEvent(QPointF(point), Qt.MoveAction, data, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(target, event)
        return event
    assert not drop().isAccepted()
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
    assert editor.tools.fields['fontSize'].value() == 1
    assert editor.tools.fields['title'].text() == '保留输入后再移动'
    editor.tools.fields['fontSize'].setValue(0)
    assert drop().isAccepted(), editor.message.text()
    settle()
    assert _component(editor.store.snapshot(), editor.pageId, keys[0]).props.title == '保留输入后再移动'
    assert len(coordinator.session._undo) == history + 2


def testProxyHandleResizeUsesNativePointerAndCommitsOnce(responsivePage):
    coordinator, editor, keys = responsivePage
    editor.tools.responsive.select([keys[0]])
    settle()
    handle = editor.tools.responsive.handles[0]
    local = handle.mapTo(editor.renderer, handle.rect().center())
    start = editor.canvas.mapFromScene(editor.canvas.proxy.mapToScene(QPointF(local)))
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    card = editor.renderer.cards[editor.pageId][keys[0]]
    size = card.size()
    QTest.mousePress(editor.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    assert editor.tools.responsive.resize is not None
    end = start + QPoint(45, 30)
    viewport = editor.canvas.viewport()
    QApplication.sendEvent(viewport, QMouseEvent(QEvent.MouseMove, QPointF(end),
        QPointF(viewport.mapToGlobal(end)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    assert editor.store.snapshot() == before
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    settle()
    item = _component(editor.store.snapshot(), editor.pageId, keys[0]).responsive
    assert item.width == 'content' and item.preferredWidth > size.width()
    assert item.preferredHeight > size.height()
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert editor.store.snapshot() == before


@pytest.mark.parametrize('kind', ['number', 'image', 'table'])
@pytest.mark.parametrize('nested', [False, True])
def testPendingAppearanceResizeGhostMatchesAppliedGeometry(responsivePage, kind, nested):
    coordinator, editor, keys = responsivePage
    commands = editor.tools.responsive.commands()
    commands.delete(editor.pageId, keys)
    parentId = commands.place(editor.pageId, kind='container')[0] if nested else None
    key = commands.place(editor.pageId, kind=kind, parentId=parentId)[0]
    coordinator.session.editPresentation(lambda p: setattr(p.pages[editor.pageId], 'layout',
        ResponsiveLayout(direction='vertical')))
    editor.refresh()
    editor.previewWidth.setValue(800)
    settle()
    editor.tools.responsive.select([key])
    editor.tools.fields['title'].setText('本次检测数量与流程状态，请核对图像和数值是否属于同一次检测。' * 3)
    editor.tools.fields['fontSize'].setValue(32)
    history = len(coordinator.session._undo)
    handle = editor.tools.responsive.handles[0]
    local = handle.mapTo(editor.renderer, handle.rect().center())
    start = editor.canvas.mapFromScene(editor.canvas.proxy.mapToScene(QPointF(local)))
    viewport = editor.canvas.viewport()
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    assert editor.tools.responsive.resize is not None
    assert _component(editor.store.snapshot(), editor.pageId, key).props.fontSize == 32
    assert len(coordinator.session._undo) == history + 1
    accepted = editor.store.snapshot()
    # A narrower card must account for the newly accepted wrapped title.
    end = start + QPoint(-80, 40)
    QApplication.sendEvent(viewport, QMouseEvent(QEvent.MouseMove, QPointF(end),
        QPointF(viewport.mapToGlobal(end)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    ghost = editor.tools.responsive.preview.box
    assert editor.store.snapshot() == accepted
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    settle()
    actual = editor.renderer.cards[editor.pageId][key].geometry()
    assert abs(ghost.width() - actual.width()) <= 2, (ghost, actual)
    assert abs(ghost.height() - actual.height()) <= 2, (ghost, actual)
    assert len(coordinator.session._undo) == history + 2
    coordinator.history()
    assert editor.store.snapshot() == accepted


@pytest.mark.parametrize('guides,snap,alt,after', [
    (True, True, False, True), (False, True, False, True),
    (True, False, False, False), (True, True, True, False), (False, False, False, False),
])
def testPageMoveGuidesAndSnapAreIndependentAndAltBypasses(responsivePage, monkeypatch, guides, snap, alt, after):
    coordinator, editor, keys = responsivePage
    coordinator.session.editPresentation(lambda p: setattr(p.pages[editor.pageId], 'layout',
        ResponsiveLayout(direction='vertical')))
    editor.refresh()
    editor.previewWidth.setValue(800)
    settle()
    editor.tools.responsive.select([keys[0]])
    editor.tools.setAlignmentGuidesEnabled(guides)
    editor.tools.setAlignmentSnapEnabled(snap)
    monkeypatch.setattr(QApplication, 'keyboardModifiers', lambda: Qt.AltModifier if alt else Qt.NoModifier)
    source, target = [editor.renderer.cards[editor.pageId][key] for key in (keys[0], keys[2])]
    data = {'move': keys[0], 'hotspot': [source.width() // 2, source.height() // 2]}
    point = target.rect().center() - QPoint(0, 2)
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    result = editor.tools.responsive.drop(data, target, point, preview=True)
    assert result['index'] == (3 if after else 2)
    assert bool(editor.tools.responsive.preview.lines) is guides
    assert editor.store.snapshot() == before and len(coordinator.session._undo) == history
    editor.tools.responsive.cancel()


def testPageCanvasDeleteUndoAndPropertyInputKeepTheirOwnKeyboardScope(responsivePage):
    coordinator, editor, keys = responsivePage
    window = coordinator.window
    window.activateWindow()
    editor.tools.responsive.select([keys[0]])
    editor.canvas.setFocus()
    settle()
    assert QApplication.activeWindow() is window
    assert window.designerActions.keyboardAllowed('delete')
    before = editor.store.snapshot()
    QTest.keyClick(editor.canvas.viewport(), Qt.Key_Delete)
    settle()
    assert keys[0] not in editor.renderer.cards[editor.pageId]
    QTest.keyClick(editor.canvas.viewport(), Qt.Key_Z, Qt.ControlModifier)
    settle()
    assert editor.store.snapshot() == before
    editor.tools.responsive.select([keys[0]])
    field = editor.tools.fields['title']
    field.setText('abc')
    field.setFocus()
    field.setCursorPosition(1)
    settle()
    assert not window.designerActions.keyboardAllowed('delete')
    QTest.keyClick(field, Qt.Key_Delete)
    assert field.text() == 'ac'
    assert editor.store.snapshot() == before
    QTest.keyClick(field, Qt.Key_Z, Qt.ControlModifier)
    assert field.text() == 'abc' and editor.store.snapshot() == before


def testResizeHandleClickWithoutMovementLeavesDocumentAndHistoryUnchanged(responsivePage):
    coordinator, editor, keys = responsivePage
    editor.tools.responsive.select([keys[0]])
    settle()
    coordinator.session.markSaved()
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    handle = editor.tools.responsive.handles[0]
    local = handle.mapTo(editor.renderer, handle.rect().center())
    point = editor.canvas.mapFromScene(editor.canvas.proxy.mapToScene(QPointF(local)))
    QTest.mousePress(editor.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    assert editor.tools.responsive.resize is not None
    QTest.mouseRelease(editor.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    settle()
    assert editor.tools.responsive.resize is None
    assert editor.tools.responsive.preview is None
    assert editor.store.snapshot() == before
    assert len(coordinator.session._undo) == history
    assert not coordinator.session.dirty


@pytest.mark.parametrize('operation', ['group', 'ungroup', 'align'])
def testArrangementMenusRebuildPageOnlyOnce(responsivePage, monkeypatch, operation):
    coordinator, editor, keys = responsivePage
    if operation == 'align':
        coordinator.session.editPresentation(lambda p: setattr(p.pages[editor.pageId], 'layout',
            ResponsiveLayout(direction='vertical')))
        editor.refresh()
    editor.tools.responsive.select(keys[:2])
    if operation == 'ungroup':
        editor.tools.responsive.group('horizontal')
    settle()
    editor.tools.updateGroupActions()
    registry = coordinator.window.designerActions
    registry.refresh()
    reloads = []
    original = editor.renderer.reload

    def reload(presentation):
        reloads.append(presentation)
        original(presentation)

    monkeypatch.setattr(editor.renderer, 'reload', reload)
    before, history = editor.store.snapshot(), len(coordinator.session._undo)
    action = {'group': editor.tools.groupActions[0], 'ungroup': editor.tools.ungroupAction,
              'align': registry.actions['align_right']}[operation]
    assert action.isEnabled()
    action.trigger()
    settle()
    assert editor.store.snapshot() != before
    assert len(coordinator.session._undo) == history + 1
    assert len(reloads) == 1


@pytest.mark.parametrize('zoom', [.5, 1.5])
@pytest.mark.parametrize('multiple', [False, True])
def testPageDragImageFollowsZoomAndKeepsLogicalMimeHotspot(responsivePage, monkeypatch, zoom, multiple):
    coordinator, editor, keys = responsivePage
    from emo_master.apps.designer.page_designer import responsive_tools

    def edit(p):
        page = p.pages[editor.pageId]
        page.layout = ResponsiveLayout(direction='vertical')
        for item, width, height in zip(page.components, [200, 400], [100, 200]):
            item.responsive.width = 'content'
            item.responsive.alignSelf = 'start'
            item.responsive.preferredWidth = width
            item.responsive.preferredHeight = height

    coordinator.session.editPresentation(edit)
    editor.refresh()
    captures = {}

    class CapturedDrag:
        def __init__(self, source):
            captures['source'] = source

        def setMimeData(self, value):
            captures['payload'] = json.loads(bytes(value.data(MIME)))

        def setPixmap(self, value):
            captures['pixmap'] = value

        def setHotSpot(self, value):
            captures['hotspot'] = value

        def exec_(self, action):
            captures['action'] = action

        def deleteLater(self):
            pass

    monkeypatch.setattr(responsive_tools, 'QDrag', CapturedDrag)
    selected = keys[:2] if multiple else [keys[1]]
    pressed = keys[1]
    editor.tools.responsive.select(selected)
    editor.canvas.setZoomFactor(zoom)
    settle()
    cards = editor.renderer.cards[editor.pageId]
    card = cards[pressed]
    hotspot = card.rect().center()
    expectedHotspot = QPoint(round(hotspot.x() * zoom), round(hotspot.y() * zoom))

    image, imageHotspot = editor.tools.responsive.dragImage(pressed, hotspot)
    assert abs(image.width() / image.devicePixelRatioF() - card.width() * zoom) <= 1
    assert abs(image.height() / image.devicePixelRatioF() - card.height() * zoom) <= 1
    assert (imageHotspot - expectedHotspot).manhattanLength() <= 2

    # Exercise the real move-to-QDrag path without entering an operating-system
    # drag loop; MIME placement uses layout units while the image uses screen units.
    start = editor.canvas.viewport().mapToGlobal(editor.canvas.mapFromScene(
        editor.canvas.proxy.mapToScene(QPointF(card.mapTo(editor.renderer, hotspot)))))
    editor.tools.responsive.pointer = (pressed, start, hotspot)
    end = start + QPoint(QApplication.startDragDistance() + 10, 0)
    event = QMouseEvent(QEvent.MouseMove, QPointF(hotspot), QPointF(end),
        Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    assert editor.tools.responsive.eventFilter(card, event)
    assert captures['source'] is editor.canvas
    assert captures['action'] == Qt.MoveAction
    assert captures['pixmap'].size() == image.size()
    assert captures['hotspot'] == imageHotspot
    assert captures['payload']['move'] == pressed
    assert captures['payload']['moves'] == selected
    assert captures['payload']['hotspot'] == [hotspot.x(), hotspot.y()]

    movingBoxes = []

    def captureMovingBox(moving, *args, **kwargs):
        movingBoxes.append(moving)
        return 0, 0, ()

    monkeypatch.setattr(responsive_tools, 'snapOffset', captureMovingBox)
    editor.tools.setAlignmentGuidesEnabled(True)
    target = cards[keys[2]]
    editor.tools.responsive.target(captures['payload'], target, target.rect().center())
    assert len(movingBoxes) == 1
    assert (movingBoxes[0].width, movingBoxes[0].height) == (card.width(), card.height())
    if multiple:
        assert card.size() != cards[keys[0]].size()
