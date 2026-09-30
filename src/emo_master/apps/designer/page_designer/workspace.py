"""Page management around the shared production renderer."""
from PySide2.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QPushButton, QInputDialog,
    QLabel, QMessageBox,
)
from PySide2.QtCore import Qt

from emo_master.ui.presentation.renderer import RuntimePages


class EditorPages(RuntimePages):
    """Keep every renderer navigation inside the editor's page boundary."""
    def __init__(self, workspace, *args, **kwargs):
        self.workspace = workspace
        self.reloading = False
        super().__init__(*args, **kwargs)

    def navigate(self, pageId, keepFrozen=False):
        if self.reloading or not hasattr(self.workspace, 'tools'):
            return super().navigate(pageId, keepFrozen=keepFrozen)
        return self.workspace.navigatePage(pageId, keepFrozen=keepFrozen)

    def act(self, action):
        if not self.editing and action is not None and action.type == 'navigate' and action.context == 'displayed_result':
            # Detail actions acquire a pin before navigate(), so reject the
            # form here, before they can change the current live/frozen view.
            try:
                self.workspace.tools.commitPending()
            except ValueError as error:
                self.workspace.message.setText(str(error))
                return
        return super().act(action)

    def reload(self, presentation):
        # Rebuilding the canvas is not a request to leave the current form.
        self.reloading = True
        try:
            super().reload(presentation)
        finally:
            self.reloading = False


class PageWorkspace(QWidget):
    def __init__(self, coordinator):
        super().__init__()
        self.coordinator = coordinator
        self.session = coordinator.session
        self.store = self.session.presentation
        self.pageId = None
        self.closed = False
        self.root = QHBoxLayout(self)
        sidebar = QVBoxLayout()
        self.root.addLayout(sidebar)
        sidebar.addWidget(QLabel('页面 · 默认首页标 ★'))
        self.pageList = QListWidget()
        self.pageList.setMaximumWidth(230)
        self.pageList.currentRowChanged.connect(self.choosePage)
        sidebar.addWidget(self.pageList)
        for title, command in [('新建页面', self.newPage), ('重命名', self.renamePage),
                ('复制页面', self.copyPage), ('上移', lambda: self.movePage(-1)),
                ('下移', lambda: self.movePage(1)), ('设为首页', self.defaultPage),
                ('删除页面', self.deletePage), ('撤销', lambda: coordinator.history()),
                ('重做', lambda: coordinator.history(True))]:
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, fn=command: self.run(fn))
            sidebar.addWidget(button)
        self.renderer = EditorPages(self, self.store.snapshot(), label='模拟布局预览 · 不运行算子、不写生产状态')
        self.renderer.editing = True
        self.root.addWidget(self.renderer, 1)
        self.message = QLabel('编辑模式：控件只选择，不执行运行动作')
        self.message.setWordWrap(True)
        sidebar.addWidget(self.message)
        self.observation = QLabel('页面只观察明确选择的任务；打开、切页和关闭不会启动检测')
        self.observation.setWordWrap(True)
        sidebar.addWidget(self.observation)
        from .tools import EditingTools
        self.tools = EditingTools(self, sidebar)
        self.refresh()

    def run(self, command):
        try:
            self.coordinator.sync()
            command()
            self.refresh()
        except (ValueError, KeyError) as error:
            self.message.setText(str(error))

    def refresh(self):
        p = self.store.snapshot()
        self.coordinator.window.setWindowTitle('视觉流程设计器' + (' *' if self.session.dirty else ''))
        if self.pageId not in p.pages:
            self.pageId = p.defaultPageId
        self.pageList.blockSignals(True)
        self.pageList.clear()
        for key in p.pageOrder:
            self.pageList.addItem(('★ ' if key == p.defaultPageId else '') + p.pages[key].name)
            self.pageList.item(self.pageList.count()-1).setData(Qt.UserRole, key)
        if self.pageId:
            self.pageList.setCurrentRow(p.pageOrder.index(self.pageId))
        self.pageList.blockSignals(False)
        self.renderer.reload(p)
        if self.pageId:
            self.renderer.navigate(self.pageId)
        if hasattr(self, 'tools'):
            self.tools.refresh()
        self.coordinator.preview.refreshCoverage()

    def choosePage(self, row):
        item = self.pageList.item(row)
        if item:
            self.renderer.navigate(item.data(Qt.UserRole))

    def _selectCurrentPage(self):
        self.pageList.blockSignals(True)
        row = next((row for row in range(self.pageList.count())
                    if self.pageList.item(row).data(Qt.UserRole) == self.pageId), -1)
        self.pageList.setCurrentRow(row)
        self.pageList.blockSignals(False)
        for key, button in self.renderer.buttons.items():
            button.setChecked(key == self.pageId)

    def navigatePage(self, pageId, keepFrozen=False):
        if pageId == self.pageId or pageId not in self.renderer.config.pages:
            return RuntimePages.navigate(self.renderer, pageId, keepFrozen=keepFrozen)
        try:
            # The form still belongs to the old page until this succeeds.
            self.tools.commitPending()
        except ValueError as error:
            self.message.setText(str(error))
            self._selectCurrentPage()
            return
        # A detail action has just pinned the displayed result. Rebuilding
        # would release that pin and replace it with the session's latest.
        # Pending form changes render when editing resumes instead.
        if not keepFrozen and self.renderer.config != self.store.snapshot():
            self.refresh()
        self.pageId = pageId
        RuntimePages.navigate(self.renderer, pageId, keepFrozen=keepFrozen)
        self._selectCurrentPage()
        self.tools.select(None)
        self.tools.dragStart = None
        self.tools.install()

    def newPage(self):
        name, ok = QInputDialog.getText(self, '新建页面', '页面名称')
        if ok and name:
            self.pageId = self.store.createPage(name)

    def renamePage(self):
        if self.pageId:
            name, ok = QInputDialog.getText(self, '重命名页面', '名称', text=self.store.snapshot().pages[self.pageId].name)
            if ok and name:
                self.store.renamePage(self.pageId, name)

    def copyPage(self):
        if self.pageId:
            self.pageId = self.store.copyPage(self.pageId)

    def movePage(self, delta):
        order = self.store.snapshot().pageOrder
        if self.pageId in order:
            index = order.index(self.pageId)
            target = index + delta
            if 0 <= target < len(order):
                order[index], order[target] = order[target], order[index]
                self.store.reorderPages(order)

    def defaultPage(self):
        if self.pageId:
            self.store.setDefaultPage(self.pageId)

    def deletePage(self):
        if not self.pageId:
            return
        repair = None
        if self.store.referencesTo(self.pageId):
            p = self.store.snapshot()
            keys = [key for key in p.pageOrder if key != self.pageId]
            name, ok = QInputDialog.getItem(self, '修复导航引用', '明确选择替代页面',
                [p.pages[key].name + ' · ' + key for key in keys], 0, False)
            if not ok:
                return
            repair = keys[[p.pages[key].name + ' · ' + key for key in keys].index(name)]
        elif QMessageBox.question(self, '删除页面', '删除当前页面？') != QMessageBox.Yes:
            return
        self.store.deletePage(self.pageId, repairTo=repair)

    def closeEvent(self, event):
        self.closed = True
        self.renderer.close()
        super().closeEvent(event)
