"""Transient native selection handles and grid previews. No project state here."""
from PySide2.QtCore import Qt, QRect, QPoint, QEvent, QObject
from PySide2.QtGui import QPainter, QColor, QPen
from PySide2.QtWidgets import QWidget
from shiboken2 import isValid

from emo_master.core.presentation.models import Placement
from emo_master.apps.designer.state.presentation_store import _component
from .palette import TITLES
from .grid_geometry import columnAt, resizeBox


def describeError(error):
    message = str(error)
    for token, explanation in [('overlap', '位置重叠：网格已被其他组件占用'),
            ('exceeds grid columns', '位置越界：超出页面或容器列数'),
            ('greater_than_equal', '位置越界：行、列不能小于零'),
            ('less_than_equal', '跨度超过模型允许的上限')]:
        if token in message:
            return explanation
    return message.splitlines()[1] if '\n' in message else message


def cellAt(grid, columns, point):
    layout = grid.layout()
    layout.activate()
    column = columnAt(layout, columns, point.x())
    # Actual row geometry, including tall images and the explicit empty drop row.
    for row in range(layout.rowCount()):
        rect = layout.cellRect(row, max(0, min(columns - 1, column)))
        if point.y() <= rect.bottom():
            return row, column
    last = layout.cellRect(max(0, layout.rowCount() - 1), 0)
    row = max(0, layout.rowCount()) + max(0, (point.y() - last.bottom() - 1) // 64)
    return row, column


def cellBox(grid, columns, placement):
    layout = grid.layout()
    if not hasattr(layout, 'cellRect'):
        return grid.rect()
    layout.activate()
    def top(row):
        if row < layout.rowCount():
            return layout.cellRect(row, 0).top()
        last = layout.cellRect(max(0, layout.rowCount() - 1), 0)
        return last.bottom() + 1 + (row - layout.rowCount()) * 64
    y = top(placement.row)
    first = layout.cellRect(0, min(columns - 1, placement.column))
    last = layout.cellRect(0, min(columns - 1, placement.column + placement.columnSpan - 1))
    return QRect(first.left(), y, max(1, last.right() + 1 - first.left()),
                 max(24, top(placement.row + placement.rowSpan) - y))


class GridPreview(QWidget):
    def __init__(self, grid, columns, placement, message, valid, box=None):
        super().__init__(grid)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setProperty('editorDecoration', True)
        self.columns, self.placement, self.message, self.valid = columns, placement, message, valid
        self.box = box
        self.setGeometry(grid.rect())
        self.show()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor('#9dbbe8'), 1, Qt.DashLine))
        grid = self.parentWidget()
        if hasattr(grid.layout(), 'cellRect'):
            for column in range(self.columns):
                cell = grid.layout().cellRect(0, column)
                painter.drawLine(cell.left(), 0, cell.left(), self.height())
            painter.drawLine(cell.right() + 1, 0, cell.right() + 1, self.height())
        for row in range(min(130, grid.layout().rowCount() + 2) if hasattr(grid.layout(), 'rowCount') else 0):
            y = cellBox(grid, self.columns, Placement(row=row)).top()
            painter.drawLine(0, y, self.width(), y)
        color = QColor('#2672d9' if self.valid else '#c63145')
        painter.setPen(QPen(color, 2))
        color.setAlpha(45)
        painter.setBrush(color)
        box = (self.box if self.box is not None else cellBox(grid, self.columns, self.placement))
        box = box.intersected(self.rect()).adjusted(2, 2, -2, -2)
        painter.drawRect(box)
        painter.fillRect(QRect(0, 0, self.width(), 48), QColor('#eef4ff' if self.valid else '#fff0f0'))
        painter.drawText(QRect(8, 2, self.width()-16, 44), Qt.TextWordWrap, self.message)
        painter.end()


class Outline(QWidget):
    def __init__(self, card, text, valid=True):
        super().__init__(card)
        self.text, self.valid = text, valid
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setProperty('editorDecoration', True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor('#2672d9' if self.valid else '#c63145'), 2))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 5, 5)
        painter.fillRect(QRect(2, 2, self.width()-4, 22), QColor('#e2edff' if self.valid else '#ffeded'))
        painter.drawText(QRect(7, 2, self.width()-14, 22), Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(self.text, Qt.ElideRight, self.width()-14))
        painter.end()


class ResizeHandle(QWidget):
    def __init__(self, selection, axes):
        super().__init__(selection.card)
        self.selection, self.axes = selection, axes
        self.setProperty('editorDecoration', True)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setCursor({'x': Qt.SizeHorCursor, 'y': Qt.SizeVerCursor, 'xy': Qt.SizeFDiagCursor}[axes])
        self.setToolTip({'x': '拖动调整列跨度', 'y': '拖动调整行跨度', 'xy': '拖动调整行列跨度'}[axes] + '；Esc 取消')
        self.setStyleSheet('background:#2672d9; border:1px solid white;')

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            try:
                self.selection.begin(self, event.globalPos())
                self.grabKeyboard()
            except (ValueError, KeyError) as error:
                self.selection.tools.w.message.setText(str(error))
            event.accept()

    def mouseMoveEvent(self, event):
        if self.selection.active:
            self.selection.move(event.globalPos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.releaseKeyboard()
            if self.selection.active:
                self.selection.move(event.globalPos())
            self.selection.finish()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.releaseKeyboard()
            self.selection.cancel()
            event.accept()
        else:
            super().keyPressEvent(event)


class Selection(QObject):
    def __init__(self, tools, card, key):
        super().__init__(tools)
        self.tools, self.card, self.key = tools, card, key
        self.active = None
        self.candidate = None
        self.valid = False
        item = _component(tools.w.store.snapshot(), tools.w.pageId, key)
        source = tools.w.store.snapshot().dataSources.get(next(iter(item.bindings.values()), ''))
        summary = ('来源 ' + source.port) if source else '未绑定' if item.type in ('image', 'number', 'table', 'indicator') else '静态内容'
        self.outline = Outline(card, TITLES[item.type] + ' · ' + summary)
        self.handles = [ResizeHandle(self, axes) for axes in ('x', 'y', 'xy')]
        card.installEventFilter(self)
        self.position()

    def position(self):
        self.outline.setGeometry(self.card.rect())
        self.outline.show()
        self.outline.raise_()
        width, height = self.card.width(), self.card.height()
        for handle, rect in zip(self.handles, [QRect(width-10, height//2-8, 10, 16),
                QRect(width//2-8, height-10, 16, 10), QRect(width-14, height-14, 14, 14)]):
            handle.setGeometry(rect)
            handle.show()
            handle.raise_()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.position()
        if event.type() == QEvent.Hide:
            self.cancel()
        return False

    def begin(self, handle, point):
        self.tools.commitPending()
        self.original = _component(self.tools.w.store.snapshot(), self.tools.w.pageId, self.key)
        self.grid = self.card.parentWidget()
        parentId = self.grid.property('componentId')
        p = self.tools.w.store.snapshot()
        self.columns = (_component(p, self.tools.w.pageId, parentId).grid.columns if parentId
                        else p.pages[self.tools.w.pageId].layout.columns)
        self.start, self.active = QPoint(point), handle
        self.candidate, self.valid = self.original.layout.model_copy(), True
        self.endpoint = self.card.geometry().bottomRight()

    def move(self, point):
        if not self.active:
            return
        delta = point - self.start
        raw = self.original.layout.model_dump()
        if 'x' in self.active.axes:
            raw['columnSpan'] = max(1, raw['columnSpan'] + round(delta.x() / max(1, self.grid.width() / self.columns)))
        if 'y' in self.active.axes:
            raw['rowSpan'] = max(1, raw['rowSpan'] + round(delta.y() / 64))
        try:
            self.candidate = Placement(**raw)
            self.tools.commands().preview().update(self.tools.w.pageId, self.key,
                props=self.original.props, layout=self.candidate)
            self.valid, message = True, f"调整为 {raw['rowSpan']} 行 × {raw['columnSpan']} 列；松开应用，Esc 取消"
        except ValueError as error:
            self.valid, message = False, describeError(error)
        box = resizeBox(self.grid, self.card, self.columns, self.candidate) if self.valid else None
        self.tools.showGridPreview(self.grid, self.columns, self.candidate, message, self.valid, box=box)

    def finish(self):
        active, self.active = self.active, None
        self.tools.clearGridPreview()
        if active and self.valid and self.candidate != self.original.layout:
            try:
                self.tools.commands().update(self.tools.w.pageId, self.key,
                    props=self.original.props, layout=self.candidate)
                self.tools.laterRefresh()
            except (ValueError, KeyError) as error:
                self.tools.w.message.setText(str(error))

    def cancel(self):
        if self.active and isValid(self.active):
            self.active.releaseKeyboard()
        self.active = None
        self.tools.clearGridPreview()

    def dispose(self):
        self.cancel()
        if isValid(self.card):
            self.card.removeEventFilter(self)
        for widget in [self.outline, *self.handles]:
            if isValid(widget):
                widget.hide()
                widget.deleteLater()
        self.deleteLater()
