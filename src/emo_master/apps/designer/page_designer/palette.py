"""Small, native painted component swatches; no renderers, images or sessions."""
from PySide2.QtCore import Qt, QSize, QRect, QRectF
from PySide2.QtGui import QColor, QPen, QDrag, QPixmap, QPainter
from PySide2.QtWidgets import QListWidget, QListWidgetItem, QListView, QStyledItemDelegate, QStyle

COMPONENTS = (
    ('image', '图像', '展示绑定的结果图像'), ('number', '数值', '数量、测量值与单位'),
    ('text', '文字', '固定文字或绑定数据'), ('indicator', '判定指示', '按配置映射显示判定'),
    ('table', '集合表格', '按列展示集合并分页'), ('navigation_button', '导航按钮', '切页或查看已显示结果'),
    ('container', '容器', '组织组件及内部网格'), ('runtime_status', '连接状态', '展示客户端连接状态'),
)
TITLES = {kind: title for kind, title, _ in COMPONENTS}


def paintSwatch(painter, rect, kind):
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#94a3b8'), 1))
    painter.setBrush(QColor('#edf3fa'))
    painter.drawRoundedRect(QRectF(rect), 5, 5)
    painter.setPen(QColor('#2563eb'))
    if kind in ('image', 'container', 'table'):
        if kind == 'image':
            painter.drawEllipse(QRectF(rect.left()+12, rect.top()+8, 10, 10))
            painter.drawLine(rect.left()+8, rect.bottom()-8, rect.center().x(), rect.center().y())
            painter.drawLine(rect.center().x(), rect.center().y(), rect.right()-8, rect.bottom()-8)
        else:
            for fraction in (1/3, 2/3):
                x, y = int(rect.left()+rect.width()*fraction), int(rect.top()+rect.height()*fraction)
                painter.drawLine(x, rect.top()+5, x, rect.bottom()-5)
                painter.drawLine(rect.left()+5, y, rect.right()-5, y)
    else:
        value = {'number': '128', 'text': 'Aa 文字', 'indicator': '● OK',
                 'navigation_button': '下一页 →', 'runtime_status': '● 已连接'}[kind]
        font = painter.font()
        font.setPointSizeF(16 if kind == 'number' else 10)
        font.setBold(kind in ('number', 'indicator'))
        painter.setFont(font)
        if kind in ('indicator', 'runtime_status'):
            painter.setPen(QColor('#16804a'))
        painter.drawText(rect, Qt.AlignCenter, value)
    painter.restore()


class SwatchDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        box = option.rect.adjusted(3, 3, -3, -3)
        painter.setPen(QColor('#2563eb' if option.state & QStyle.State_Selected else '#d8dce3'))
        painter.setBrush(QColor('#eef5ff' if option.state & QStyle.State_Selected else '#ffffff'))
        painter.drawRoundedRect(box, 6, 6)
        paintSwatch(painter, box.adjusted(8, 7, -8, -30), index.data(Qt.UserRole))
        painter.setPen(QColor('#20242b'))
        painter.drawText(box.adjusted(3, box.height()-27, -3, -2), Qt.AlignCenter, index.data())
        painter.restore()


class Palette(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setSpacing(2)
        self.setMinimumWidth(0)
        self.setDragEnabled(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setItemDelegate(SwatchDelegate(self))
        for kind, title, description in COMPONENTS:
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, kind)
            item.setToolTip(description + ' · 拖入网格')
            self.addItem(item)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = 2 if self.viewport().width() >= 220 else 1
        self.setGridSize(QSize(max(96, (self.viewport().width()-8)//columns), 96))

    def search(self, text):
        for row in range(self.count()):
            item = self.item(row)
            item.setHidden(text.casefold() not in (item.text() + item.data(Qt.UserRole)).casefold())

    def startDrag(self, actions):
        from .tools import mime
        item = self.currentItem()
        if item:
            drag = QDrag(self)
            drag.setMimeData(mime({'kind': item.data(Qt.UserRole)}))
            ratio = self.devicePixelRatioF()
            pixmap = QPixmap(int(120*ratio), int(70*ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            paintSwatch(painter, QRect(0, 0, 120, 70), item.data(Qt.UserRole))
            painter.end()
            drag.setPixmap(pixmap)
            drag.exec_(Qt.CopyAction)
