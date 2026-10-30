"""Native stable-widget adapter around the shared responsive geometry solver."""
from PySide2.QtCore import QRect, QSize, Qt
from PySide2.QtWidgets import QLayout

from emo_master.core.presentation.models import ResponsiveItem
from emo_master.core.presentation.responsive import Measurement, solveResponsive


class ResponsiveWidgetLayout(QLayout):
    def __init__(self, parent, rules):
        super().__init__(parent)
        self.rules = rules
        self._items = []
        self._components = {}
        self.childRects = {}
        self.setContentsMargins(9, 9, 9, 9)
        self.setSpacing(rules.spacing)

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def addComponent(self, widget, component):
        self._components[widget] = component
        self.addWidget(widget)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if not 0 <= index < len(self._items):
            return None
        item = self._items.pop(index)
        self._components.pop(item.widget(), None)
        self.invalidate()
        return item

    def expandingDirections(self):
        return Qt.Horizontal

    def hasHeightForWidth(self):
        return True

    def _measurements(self):
        result = []
        for index, item in enumerate(self._items):
            widget = item.widget()
            component = self._components.get(widget)
            hint, minimum = item.sizeHint(), item.minimumSize()
            # Widget height contracts are retained, but a wide table's minimum
            # width must not force the outer page wider than its viewport.
            height = (lambda width, item=item: max(0, item.heightForWidth(round(width)))) if item.hasHeightForWidth() else None
            result.append(Measurement(component.componentId if component else str(index),
                component.responsive if component else ResponsiveItem(), hint.width(), hint.height(),
                0, minimum.height(), height))
        return result

    def heightForWidth(self, width):
        margins = self.contentsMargins()
        _boxes, height = solveResponsive(self.rules, self._measurements(),
            max(0, width - margins.left() - margins.right()))
        return round(height) + margins.top() + margins.bottom()

    def sizeHint(self):
        measures = self._measurements()
        width = max((max(m.responsive.minWidth, m.responsive.preferredWidth) for m in measures), default=240)
        return QSize(round(width + 18), self.heightForWidth(width + 18))

    def minimumSize(self):
        return QSize(0, 32)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        margins = self.contentsMargins()
        inner = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        measures = self._measurements()
        boxes, _height = solveResponsive(self.rules, measures, inner.width(), inner.height())
        self.childRects = {}
        for item, measured, box in zip(self._items, measures, boxes):
            target = QRect(round(inner.x() + box.x), round(inner.y() + box.y),
                           max(0, round(box.width)), max(0, round(box.height)))
            self.childRects[measured.key] = target
            item.setGeometry(target)

    def updateRules(self, rules):
        self.rules = rules
        self.setSpacing(rules.spacing)
        self.invalidate()
