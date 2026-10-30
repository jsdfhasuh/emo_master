"""Commit pending appearance before capturing a native page or palette drag."""
import json

import pytest
from PySide2.QtCore import QEvent, QPoint, QPointF, Qt
from PySide2.QtGui import QMouseEvent
from PySide2.QtWidgets import QApplication

from test_responsive_editor import responsivePage, settle  # noqa: F401
from test_workspace import designer  # noqa: F401
from emo_master.apps.designer.page_designer import palette, responsive_tools
from emo_master.apps.designer.page_designer.tools import MIME
from emo_master.apps.designer.state.presentation_store import _component


@pytest.mark.parametrize('origin', ['canvas', 'palette'])
@pytest.mark.parametrize('valid', [True, False], ids=['valid-title', 'invalid-font'])
def testPendingAppearanceIsResolvedBeforeNativeDrag(responsivePage, monkeypatch, origin, valid):  # noqa: F811
    coordinator, editor, keys = responsivePage
    key = keys[0]
    editor.tools.responsive.select([key])
    editor.canvas.setZoomFactor(1)
    settle()
    originalCard = editor.renderer.cards[editor.pageId][key]
    originalHeight = originalCard.height()
    before = coordinator.session.payload()
    history = len(coordinator.session._undo)
    title = '本次检测数量与流程状态，请核对图像和数值是否属于同一次检测。' * 3
    editor.tools.fields['title'].setText(title)
    editor.tools.fields['fontSize'].setValue(32 if valid else 1)
    captures = {}

    class CapturedDrag:
        def __init__(self, source):
            captures['source'] = source
            captures['card'] = editor.renderer.cards[editor.pageId][key]
            captures['rendered'] = _component(editor.renderer.config, editor.pageId, key)
            captures['saved'] = _component(editor.store.snapshot(), editor.pageId, key)

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
    monkeypatch.setattr(palette, 'QDrag', CapturedDrag)
    if origin == 'canvas':
        hotspot = originalCard.rect().center()
        start = editor.canvas.viewport().mapToGlobal(editor.canvas.mapFromScene(
            editor.canvas.proxy.mapToScene(QPointF(originalCard.mapTo(editor.renderer, hotspot)))))
        editor.tools.responsive.pointer = (key, start, hotspot)
        end = start + QPoint(QApplication.startDragDistance() + 10, 0)
        event = QMouseEvent(QEvent.MouseMove, QPointF(hotspot), QPointF(end),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        assert editor.tools.responsive.eventFilter(originalCard, event)
    else:
        source = editor.tools.palette
        source.setCurrentRow(next(row for row in range(source.count())
                                  if source.item(row).data(Qt.UserRole) == 'text'))
        source.startDrag(Qt.CopyAction)

    if valid:
        assert captures['rendered'].props.title == captures['saved'].props.title == title
        assert captures['rendered'].props.fontSize == captures['saved'].props.fontSize == 32
        assert captures['card'] is not originalCard
        assert captures['card'].height() > originalHeight
        assert len(coordinator.session._undo) == history + 1
        if origin == 'canvas':
            image = captures['pixmap']
            assert abs(image.height() / image.devicePixelRatioF() - captures['card'].height()) <= 1
            assert captures['payload']['move'] == key
            assert captures['payload']['moves'] == [key]
            assert captures['action'] == Qt.MoveAction
        else:
            assert captures['payload'] == {'kind': 'text'}
            assert captures['action'] == Qt.CopyAction
    else:
        assert not captures
        assert coordinator.session.payload() == before
        assert len(coordinator.session._undo) == history
        assert editor.renderer.cards[editor.pageId][key] is originalCard
        assert editor.tools.fields['title'].text() == title
        assert editor.tools.fields['fontSize'].value() == 1
        assert editor.tools.responsive.selectedIds == [key]
        assert editor.message.text()
