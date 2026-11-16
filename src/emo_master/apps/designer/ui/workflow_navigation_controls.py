"""Fixed navigation commands and searchable, width-aware reference locations."""
from PySide2.QtCore import QEvent, QPoint, QSize, Qt
from PySide2.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QStyle, QToolButton, QTreeWidgetItem, QVBoxLayout, QWidget

from .widgets import ElidedLabel
from .workflow_relationship_tree import WorkflowRelationshipTree


class WorkflowNavigationControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('workflowNavigationControls')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)
        self.back = QToolButton(self)
        self.forward = QToolButton(self)
        for button in (self.back, self.forward):
            button.setFixedSize(32, 30)
            layout.addWidget(button)
        self.location = ElidedLabel('当前位置', self)
        layout.addWidget(self.location, 1)
        self.definition = QToolButton(self)
        self.references = QToolButton(self)
        for button in (self.definition, self.references):
            layout.addWidget(button)

    def bindActions(self, actions):
        # The QAction owns the icon. A later enable/tooltip refresh otherwise
        # replaces a button-local icon with the action's empty icon and clips
        # the text "后退" inside the fixed-width button.
        for key, icon in (('navigate_back', QStyle.SP_ArrowBack), ('navigate_forward', QStyle.SP_ArrowForward)):
            actions[key].setIcon(self.style().standardIcon(icon))
        for button, key in ((self.back, 'navigate_back'), (self.forward, 'navigate_forward'),
                            (self.definition, 'definition'), (self.references, 'references')):
            button.setDefaultAction(actions[key])
            button.setAccessibleName(actions[key].text())
        for button in (self.back, self.forward):
            button.setIconSize(QSize(18, 18))
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)


class NavigationLocationsPopup(QFrame):
    """Modeless popup: search results carry stable IDs, not menu row indices."""
    def __init__(self, parent):
        super().__init__(parent, Qt.Popup)
        self.setObjectName('navigationLocationsPopup')
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        self.title = ElidedLabel('', self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('搜索工作流、节点名称或 ID…')
        self.search.setAccessibleName('搜索跳转位置')
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        self.tree = WorkflowRelationshipTree(self)
        self.tree.setVerticalScrollMode(WorkflowRelationshipTree.ScrollPerPixel)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setAccessibleName('工作流跳转位置')
        self.empty = QLabel('没有匹配的位置', self)
        for widget in (self.title, self.search, self.tree, self.empty):
            layout.addWidget(widget)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._chooseCurrent)
        self.tree.itemClicked.connect(self._choose)
        self.tree.itemActivated.connect(self._choose)
        self._navigate = None

    def present(self, title, rows, navigate, anchor):
        self._navigate = navigate
        self.title.setText(title)
        self.title.setAccessibleName(title)
        self.search.clear()
        self.tree.clear()
        hierarchical = any(payload.get('children') for _label, payload in rows)
        self.tree.setRootIsDecorated(hierarchical)
        self.tree.setIndentation(14 if hierarchical else 0)
        for label, payload in rows:
            self._addLocation(self.tree.invisibleRootItem(), label, payload)
        self.tree.expandAll()
        self._filter('')
        screen = anchor.screen().availableGeometry()
        self.resize(min(560, screen.width()), min(430, screen.height()))
        point = anchor.mapToGlobal(QPoint(0, anchor.height()))
        self.move(min(max(screen.left(), point.x()), screen.right() - self.width() + 1),
                  min(max(screen.top(), point.y()), screen.bottom() - self.height() + 1))
        self.show()
        self.search.setFocus()

    def _addLocation(self, parent, label, payload):
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, Qt.UserRole, {key: value for key, value in payload.items()
                                    if key not in {'children', 'tooltip', 'searchText'}})
        tooltip = payload.get('tooltip', label)
        item.setToolTip(0, tooltip)
        item.setData(0, Qt.UserRole + 1, (label + '\n' + payload.get('searchText', tooltip)).casefold())
        self.tree.styleItem(item, payload)
        for childLabel, childPayload in payload.get('children', []):
            self._addLocation(item, childLabel, childPayload)

    def _items(self, root):
        yield root
        for index in range(root.childCount()):
            yield from self._items(root.child(index))

    def _filter(self, text):
        query = text.strip().casefold()
        visible = []
        matches = []
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            items = list(self._items(root))
            matching = [item for item in items if query in item.data(0, Qt.UserRole + 1)]
            root.setHidden(not matching)
            if matching:
                visible.extend(items)
                matches.extend(matching)
                if query:
                    # A matching inner step must retain its whole caller path.
                    for item in items:
                        item.setExpanded(True)
        current = self.tree.currentItem()
        candidates = matches if query else visible
        if current not in candidates:
            self.tree.setCurrentItem(candidates[0] if candidates else None)
        if query and self.tree.currentItem() is not None:
            self.tree.scrollToItem(self.tree.currentItem())
        self.empty.setVisible(not visible)

    def _choose(self, item, _column=0):
        if item is None or item.isHidden() or not self.isVisible():
            return
        parent = item.parent()
        while parent is not None:
            if parent.isHidden() or not parent.isExpanded():
                return
            parent = parent.parent()
        payload = item.data(0, Qt.UserRole)
        self.hide()
        if self._navigate is not None:
            self._navigate(payload)

    def _chooseCurrent(self):
        self._choose(self.tree.currentItem())

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Down:
            self.tree.setFocus()
            return True
        return super().eventFilter(obj, event)
