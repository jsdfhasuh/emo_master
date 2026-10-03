"""Grid hit testing and widget-free measurement of a proposed resize."""
from PySide2.QtCore import QRect, QSize
from PySide2.QtWidgets import QGridLayout, QLayoutItem, QScrollArea


def columnAt(layout, columns, x):
    cells = [layout.cellRect(0, column) for column in range(columns)]
    if x < cells[0].left():
        return -1
    if x > cells[-1].right():
        return columns
    # Split spacing between neighbours, without assuming equal column widths.
    for index, cell in enumerate(cells[:-1]):
        if x < (cell.right() + 1 + cells[index + 1].left()) / 2:
            return index
    return columns - 1


class MeasuredItem(QLayoutItem):
    """Borrow size contracts only; never reparent or move the real widget."""
    def __init__(self, item):
        super().__init__(item.alignment())
        self.item = item
        self.rect = QRect()

    def sizeHint(self):
        return self.item.sizeHint()

    def minimumSize(self):
        return self.item.minimumSize()

    def maximumSize(self):
        return self.item.maximumSize()

    def expandingDirections(self):
        return self.item.expandingDirections()

    def hasHeightForWidth(self):
        return self.item.hasHeightForWidth()

    def heightForWidth(self, width):
        return self.item.heightForWidth(width)

    def minimumHeightForWidth(self, width):
        return self.item.minimumHeightForWidth(width)

    def isEmpty(self):
        return self.item.isEmpty()

    def setGeometry(self, rect):
        self.rect = QRect(rect)

    def geometry(self):
        return self.rect


def resizeBox(grid, card, columns, placement):
    """Use Qt's own layout solver with the candidate spans and final stretch row.

    No QWidget, renderer, session, callback or image copy is created. All proxy
    items are owned by this local layout and retired before returning a QRect.
    """
    source = grid.layout()
    measured = QGridLayout()
    measured.setContentsMargins(source.contentsMargins())
    measured.setHorizontalSpacing(source.horizontalSpacing())
    measured.setVerticalSpacing(source.verticalSpacing())
    for column in range(columns):
        measured.setColumnStretch(column, source.columnStretch(column))
        measured.setColumnMinimumWidth(column, source.columnMinimumWidth(column))
    items, target, end = [], None, 0
    for index in range(source.count()):
        original = source.itemAt(index)
        row, column, rows, cols = source.getItemPosition(index)
        proxy = MeasuredItem(original)
        items.append(proxy)
        if original.widget() is card:
            row, column, rows, cols = placement.row, placement.column, placement.rowSpan, placement.columnSpan
            target = proxy
        measured.addItem(proxy, row, column, rows, cols)
        end = max(end, row + rows)
    for row in range(end + 1):
        measured.setRowMinimumHeight(row, 64)
    measured.setRowStretch(end, 1)
    # A styled QFrame's border belongs to the widget, not its child layout.
    # Measuring against rect() can change word wrapping even for a 1px border.
    contents = grid.contentsRect()
    border = grid.size() - contents.size()
    minimum = measured.minimumSize().expandedTo(grid.minimumSize() - border)
    size = contents.size().expandedTo(minimum)
    viewport = grid.parentWidget()
    scroll = viewport.parentWidget() if viewport else None
    if isinstance(scroll, QScrollArea) and scroll.widget() is grid:
        available = scroll.maximumViewportSize()
        # Designer QSS can override scrollbar dimensions independently of the
        # platform style's PM_ScrollBarExtent.
        verticalWidth = scroll.verticalScrollBar().sizeHint().width()
        horizontalHeight = scroll.horizontalScrollBar().sizeHint().height()
        vertical = horizontal = False
        for _ in range(3):
            width = max(minimum.width(), available.width() - (verticalWidth if vertical else 0))
            height = max(minimum.height(), available.height() - (horizontalHeight if horizontal else 0))
            if measured.hasHeightForWidth():
                height = max(height, measured.minimumHeightForWidth(width))
            vertical = height > available.height() - (horizontalHeight if horizontal else 0)
            horizontal = width > available.width() - (verticalWidth if vertical else 0)
            size = QSize(width, height)
    if measured.hasHeightForWidth():
        size.setHeight(max(size.height(), measured.minimumHeightForWidth(size.width())))
    measured.setGeometry(QRect(contents.topLeft(), size))
    return QRect(target.geometry()) if target is not None else QRect()
