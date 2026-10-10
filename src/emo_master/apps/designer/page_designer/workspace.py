"""Page management around the shared production renderer."""
from PySide2.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QPushButton, QInputDialog,
    QLabel, QMessageBox, QSplitter, QTabWidget, QToolButton, QMenu,
)
from PySide2.QtCore import Qt
from shiboken2 import isValid

from emo_master.ui.presentation.renderer import RuntimePages


class ObserverPages(RuntimePages):
    """Designer-owned read-only window, including partial-construction cleanup."""
    def __init__(self, *args, **kwargs):
        try:
            super().__init__(*args, **kwargs)
            self.setWindowFlag(Qt.Window, True)
            self.setAttribute(Qt.WA_DeleteOnClose, True)
        except BaseException:
            # Construction is always hub-free and hidden. closeEvent requires
            # a complete renderer, so retire the native object directly.
            if isValid(self):
                self.deleteLater()
            raise


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
        if hasattr(self.workspace, 'tools'):
            self.workspace.tools.clearSelection()
        self.reloading = True
        try:
            super().reload(presentation)
            for button in self.buttons.values():
                button.hide()
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
        self.compact = False
        self.expandedSizes = None
        self.root = QHBoxLayout(self)
        self.splitter = QSplitter(Qt.Horizontal)
        self.root.addWidget(self.splitter)
        self.leftPanel = QWidget()
        self.leftPanel.setMinimumWidth(180)
        sidebar = QVBoxLayout(self.leftPanel)
        sidebar.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(self.leftPanel)
        header = QHBoxLayout()
        heading = QLabel('页面列表')
        heading.setObjectName('panelTitle')
        heading.setToolTip('最终展示界面的页面，例如检测总览和结果详情')
        header.addWidget(heading, 1)
        new = QToolButton()
        new.setText('新建')
        new.setToolTip('新建页面')
        new.clicked.connect(lambda: self.run(self.newPage))
        header.addWidget(new)
        more = QToolButton()
        more.setText('更多')
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        for title, command in [('重命名', self.renamePage), ('复制页面', self.copyPage),
                ('上移', lambda: self.movePage(-1)), ('下移', lambda: self.movePage(1)),
                ('设为首页', self.defaultPage), ('删除页面', self.deletePage)]:
            action = menu.addAction(title)
            action.triggered.connect(lambda _checked=False, fn=command: self.run(fn))
        more.setMenu(menu)
        header.addWidget(more)
        sidebar.addLayout(header)
        self.pageList = QListWidget()
        self.pageList.setObjectName('pageList')
        self.pageList.currentRowChanged.connect(self.choosePage)
        sidebar.addWidget(self.pageList)
        self.libraryTabs = QTabWidget()
        self.libraryTabs.setObjectName('pageLibraryTabs')
        sidebar.addWidget(self.libraryTabs, 1)
        self.centerPanel = QWidget()
        self.centerPanel.setMinimumWidth(280)
        self.centerLayout = QVBoxLayout(self.centerPanel)
        self.centerLayout.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(self.centerPanel)
        self.renderer = EditorPages(self, self.store.snapshot(), label='模拟布局预览 · 不运行算子、不写生产状态')
        self.renderer.editing = True
        self.centerLayout.addWidget(self.renderer, 1)
        self.renderer.setEditorHost()
        self.empty = QWidget()
        emptyLayout = QVBoxLayout(self.empty)
        emptyLayout.addStretch()
        hint = QLabel('添加页面，开始设计检测结果的展示界面')
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        emptyLayout.addWidget(hint)
        create = QPushButton('新建第一个页面')
        create.clicked.connect(lambda: self.run(self.newPage))
        emptyLayout.addWidget(create, 0, Qt.AlignCenter)
        emptyLayout.addStretch()
        self.centerLayout.addWidget(self.empty, 1)
        self.renderer.fitButton.setParent(self.centerPanel)
        self.renderer.fitButton.setText('适应画布')
        canvasTools = QHBoxLayout()
        canvasTools.addStretch()
        canvasTools.addWidget(self.renderer.fitButton)
        self.centerLayout.addLayout(canvasTools)
        self.message = QLabel('拖入组件开始排版；选择组件设置属性')
        self.message.setWordWrap(True)
        self.centerLayout.addWidget(self.message)
        self.observation = QLabel('页面只观察明确选择的任务；打开、切页和关闭不会启动检测')
        self.observation.setWordWrap(True)
        sidebar.addWidget(self.observation)
        self.details = QWidget()
        self.detailsLayout = QVBoxLayout(self.details)
        self.detailsLayout.setContentsMargins(0, 0, 0, 0)
        self.detailsLayout.addWidget(self.observation)
        for widget in (self.renderer.status, self.renderer.jobStatus, self.renderer.identity,
                       self.renderer.modeLabel, self.renderer.resumeButton):
            self.detailsLayout.addWidget(widget)
        self.centerLayout.addWidget(self.details)
        self.details.hide()
        from .tools import EditingTools
        self.tools = EditingTools(self, sidebar)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(1, False)
        saved = coordinator.window.settingsStore.value('pageDesigner/splitter', [260, 700, 340])
        try:
            self.splitter.setSizes([int(v) for v in saved])
        except (TypeError, ValueError):
            self.splitter.setSizes([260, 700, 340])
        self.splitter.splitterMoved.connect(self.savePanelSizes)
        self.refresh()

    def savePanelSizes(self, *_args):
        self.coordinator.window.settingsStore.setValue('pageDesigner/splitter', self.splitter.sizes())

    def togglePanel(self, index):
        sizes = self.splitter.sizes()
        sizes[index] = (260 if index == 0 else 340) if sizes[index] == 0 else 0
        if self.compact and sizes[index]:
            sizes[2 if index == 0 else 0] = 0
        self.splitter.setSizes(sizes)
        self.savePanelSizes()
        self.coordinator.chrome.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'tools'):
            return
        compact = self.width() < 800
        if compact != self.compact:
            self.compact = compact
            if compact:
                self.expandedSizes = self.splitter.sizes()
                self.splitter.setSizes([0, max(280, self.width() - 280), 260])
            elif self.expandedSizes:
                self.splitter.setSizes(self.expandedSizes)
            self.coordinator.chrome.update()
        self.resizePageList()

    def resizePageList(self):
        rowHeight = max(28, self.pageList.sizeHintForRow(0))
        rows = max(1, min(6, self.pageList.count(), max(1, self.height() // (3 * rowHeight))))
        self.pageList.setFixedHeight(rowHeight * rows + 4)

    def run(self, command):
        try:
            self.coordinator.sync()
            command()
            self.refresh()
        except (ValueError, KeyError) as error:
            from .property_panel import editorMessage
            self.message.setText(editorMessage(str(error)))
            self.message.setToolTip(str(error))

    def openObserver(self):
        # Unlike an edit command this must not refresh the source canvas:
        # refreshing releases a displayed-result pin and changes its context.
        try:
            return self.coordinator.preview.openObserver()
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
            self.pageList.addItem(('首页 · ' if key == p.defaultPageId else '') + p.pages[key].name)
            self.pageList.item(self.pageList.count()-1).setData(Qt.UserRole, key)
        if self.pageId:
            self.pageList.setCurrentRow(p.pageOrder.index(self.pageId))
        self.pageList.blockSignals(False)
        self.resizePageList()
        self.empty.setVisible(not p.pages)
        self.renderer.setVisible(bool(p.pages))
        self.renderer.fitButton.setVisible(bool(p.pages))
        self.renderer.reload(p)
        self.coordinator.preview.refreshObserver(p)
        if self.pageId:
            self.renderer.navigate(self.pageId)
        if hasattr(self, 'tools'):
            self.tools.refresh()
        self.coordinator.preview.refreshCoverage()
        self.coordinator.chrome.update()

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
        self.tools.shutdown()
        self.renderer.close()
        super().closeEvent(event)
