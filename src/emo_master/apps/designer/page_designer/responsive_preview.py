"""Measure candidate page geometry without moving or rebuilding live controls."""
from PySide2.QtCore import QPoint, QRect, QSize, Qt
from PySide2.QtWidgets import QFrame, QGridLayout, QLabel, QLayout, QLayoutItem, QSizePolicy, QWidget

from emo_master.core.presentation.models import ResponsiveLayout
from emo_master.ui.presentation.container_title import ContainerTitle
from emo_master.ui.presentation.renderer import appearanceStyle
from emo_master.ui.presentation.responsive_layout import ResponsiveWidgetLayout


class MeasuredItem(QLayoutItem):
    """Borrow native size measurements; geometry belongs only to this preview."""
    def __init__(self, source):
        super().__init__(source.alignment())
        self.source = source
        self.rect = QRect()

    def widget(self):
        return self.source.widget()

    def sizeHint(self):
        return self.source.sizeHint()

    def minimumSize(self):
        return self.source.minimumSize()

    def maximumSize(self):
        return self.source.maximumSize()

    def expandingDirections(self):
        return self.source.expandingDirections()

    def hasHeightForWidth(self):
        return self.source.hasHeightForWidth()

    def heightForWidth(self, width):
        return self.source.heightForWidth(width)

    def isEmpty(self):
        return False

    def setGeometry(self, rect):
        self.rect = QRect(rect)

    def geometry(self):
        return QRect(self.rect)


def candidateGeometry(renderer, pageId, candidatePage, prototypes=None):
    """Return (component rects in root-host coordinates, candidate root QSize).

    Existing leaf controls contribute their native QWidgetItem measurements.
    Disposable container shells reproduce frame/title/layout margins and native
    height-for-width behavior. No real control is reparented or resized.
    ``prototypes`` supplies native items for newly inserted palette controls.
    """
    nativeItems = dict(prototypes or {})
    for host in renderer.layoutHosts[pageId].values():
        layout = host.layout()
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is not None and widget.property('componentId'):
                nativeItems.setdefault(widget.property('componentId'), item)

    root = QWidget()
    root.setAttribute(Qt.WA_DontShowOnScreen)
    root.setAttribute(Qt.WA_ShowWithoutActivating)
    root.setFont(renderer.font())
    root.setStyleSheet(renderer.styleSheet())
    root.setMinimumHeight(240)
    containers = {}
    borrowed = {}

    def populate(host, components, rules):
        responsive = isinstance(rules, ResponsiveLayout)
        layout = ResponsiveWidgetLayout(host, rules) if responsive else QGridLayout(host)
        layout.setSpacing(rules.spacing)
        if not responsive:
            layout.setRowStretch(max((c.layout.row + c.layout.rowSpan for c in components), default=0), 1)
            for row in range(max((c.layout.row + c.layout.rowSpan for c in components), default=0)):
                layout.setRowMinimumHeight(row, 64)
            for column in range(rules.columns):
                layout.setColumnStretch(column, 1)
        for component in components:
            if component.type == 'container':
                card = QFrame(host)
                card.setObjectName('card')
                card.setProperty('componentId', component.componentId)
                card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                colors = {'plain': 'white', 'soft': '#edf3fa', 'outlined': 'transparent'}
                card.setStyleSheet('QFrame#card {background:' + colors[component.props.cardStyle] +
                    ';border:1px solid #c4d0de;border-radius:6px;}')
                content = card
                if component.props.title:
                    title = ContainerTitle(card, component.props.title,
                        appearanceStyle(component.props), editing=True)
                    content = title.body
                populate(content, component.children, component.grid)
                if not component.children:
                    card.setMinimumHeight(90)
                    placeholder = QLabel('容器 · 拖入组件', content)
                    if isinstance(component.grid, ResponsiveLayout):
                        content.layout().addWidget(placeholder)
                    else:
                        content.layout().addWidget(placeholder, 0, 0)
                if responsive:
                    card.setMinimumWidth(0)
                    card.layout().setSizeConstraint(QLayout.SetNoConstraint)
                    layout.addComponent(card, component)
                else:
                    cell = component.layout
                    layout.addWidget(card, cell.row, cell.column, cell.rowSpan, cell.columnSpan)
                containers[component.componentId] = card
            else:
                if component.componentId not in nativeItems:
                    raise ValueError('新组件缺少原生测量控件')
                item = MeasuredItem(nativeItems[component.componentId])
                if responsive:
                    layout.addComponentItem(item, component)
                else:
                    cell = component.layout
                    layout.addItem(item, cell.row, cell.column, cell.rowSpan, cell.columnSpan)
                borrowed[component.componentId] = (host, item)
        return layout

    def arrange(host):
        if host.layout() is not None:
            host.layout().setGeometry(host.contentsRect())
        for child in host.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
            arrange(child)

    try:
        layout = populate(root, candidatePage.components, candidatePage.layout)
        root.ensurePolished()
        # Always-on editor scrollbars make the available content width stable.
        scroll = renderer.pages[pageId]
        width = scroll.viewport().width()
        height = max(240, scroll.viewport().height(), layout.heightForWidth(width))
        root.resize(QSize(width, height))
        # Only this disposable shell is shown off-screen. Borrowed item setters
        # record rectangles and never touch the live controls.
        root.show()
        arrange(root)
        result = {key: QRect(card.mapTo(root, QPoint()), card.size())
                  for key, card in containers.items()}
        for key, (host, item) in borrowed.items():
            rect = item.geometry()
            result[key] = QRect(host.mapTo(root, rect.topLeft()), rect.size())
        return result, QSize(root.size())
    finally:
        root.close()
        root.deleteLater()
