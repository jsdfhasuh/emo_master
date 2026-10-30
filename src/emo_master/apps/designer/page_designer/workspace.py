"""Page management around the shared production renderer."""
from PySide2.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QInputDialog,
    QLabel, QMessageBox, QTabWidget, QToolButton, QMenu, QSpinBox,
)
from PySide2.QtCore import QSize, Qt
from shiboken2 import isValid

from emo_master.ui.presentation.renderer import RuntimePages
from emo_master.apps.designer.ui.canvas_overlay import FloatingPanel
from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.widgets import ElidedLabel
from .navigation import PageTabs


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
        from emo_master.core.presentation.assets import pageAssets
        kwargs['assets'] = pageAssets(workspace.session.document(), workspace.coordinator.directory)
        kwargs['projectRoot'] = workspace.coordinator.directory
        super().__init__(*args, **kwargs)

    def fitToAvailableScreen(self, *, resize=True, screen=None):
        if hasattr(self.workspace, 'canvas'):
            self.workspace.canvas.updatePageSize()
            return
        return super().fitToAvailableScreen(resize=resize, screen=screen)

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
            from emo_master.core.presentation.assets import pageAssets
            self.assets = pageAssets(self.workspace.session.document(), self.workspace.coordinator.directory)
            self.setProjectRoot(self.workspace.coordinator.directory)
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
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(12, 12, 12, 12)
        self.root.setSpacing(8)
        self.pageTabs = PageTabs()
        self.pageTabs.currentChanged.connect(self.choosePage)
        self.pageTabs.addRequested.connect(lambda: self.run(self.newPage))
        self.root.addWidget(self.pageTabs)
        more = QToolButton()
        more.setIcon(icon('ellipsis'))
        more.setFixedSize(32, 32)
        more.setToolTip('当前页面操作')
        more.setAccessibleName('当前页面操作')
        more.setPopupMode(QToolButton.InstantPopup)
        more.setStyleSheet('QToolButton { padding: 0; } '
                          'QToolButton::menu-indicator { image: none; width: 0; height: 0; }')
        self.pageMore = more
        menu = QMenu(more)
        self.pageMenu = menu
        for title, command in [('重命名', self.renamePage), ('复制页面', self.copyPage),
                ('上移', lambda: self.movePage(-1)), ('下移', lambda: self.movePage(1)),
                ('设为首页', self.defaultPage), ('删除页面', self.deletePage)]:
            action = menu.addAction(title)
            action.triggered.connect(lambda _checked=False, fn=command: self.run(fn))
        more.setMenu(menu)
        self.pageTabs.layout().addWidget(more)
        self.pageTabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.pageTabs.tabBar().customContextMenuRequested.connect(self.pageContextMenu)
        self.libraryTabs = QTabWidget()
        self.libraryTabs.setObjectName('pageLibraryTabs')
        self.libraryTabs.setMinimumSize(0, 0)
        self.centerPanel = QWidget()
        self.centerPanel.setMinimumWidth(280)
        self.centerLayout = QVBoxLayout(self.centerPanel)
        self.centerLayout.setContentsMargins(0, 0, 0, 0)
        self.root.addWidget(self.centerPanel, 1)
        # Overlays are siblings of the renderer, never renderer descendants:
        # editing hit tests and drag/drop filters must only see page content.
        self.canvasHost = QWidget()
        canvasLayout = QVBoxLayout(self.canvasHost)
        canvasLayout.setContentsMargins(0, 0, 0, 0)
        self.centerLayout.addWidget(self.canvasHost, 1)
        self.renderer = EditorPages(self, self.store.snapshot(), label='模拟布局预览 · 不运行算子、不写生产状态')
        self.renderer.editing = True
        from .page_canvas import PageCanvas
        self.canvas = PageCanvas(self, self.renderer)
        canvasLayout.addWidget(self.canvas, 1)
        self.renderer.setEditorHost()
        pageInfo = QHBoxLayout()
        self.pageCaption = ElidedLabel()
        self.pageCaption.setObjectName('mutedText')
        pageInfo.addWidget(self.pageCaption, 1)
        self.renderer.banner.setObjectName('mutedText')
        self.renderer.banner.setWordWrap(False)
        pageInfo.addWidget(self.renderer.banner)
        self.centerLayout.insertLayout(0, pageInfo)
        # A stable handle lane keeps collapsed panels clear of the first row;
        # opening panels never changes these margins or the component grid.
        self.renderer.layout().setContentsMargins(12, 64, 12, 12)
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
        canvasLayout.addWidget(self.empty, 1)
        self.renderer.fitButton.setParent(self.centerPanel)
        self.renderer.fitButton.setText('适应画布')
        self.renderer.fitButton.clicked.disconnect()
        self.renderer.fitButton.clicked.connect(self.canvas.fitPage)
        canvasTools = QHBoxLayout()
        canvasTools.addWidget(QLabel('预览宽度'))
        self.previewWidth = QSpinBox()
        self.previewWidth.setRange(0, 2400)
        self.previewWidth.setSpecialValueText('跟随窗口')
        self.previewWidth.setSingleStep(40)
        self.previewWidth.setSuffix(' px')
        self.previewWidth.setToolTip('0 跟随窗口；改变预览宽度只重新排版，不修改工程')
        self.previewWidth.valueChanged.connect(self.canvas.setPreviewWidth)
        canvasTools.addWidget(self.previewWidth)
        for title, width in [('窄', 360), ('中', 768), ('宽', 1280), ('自动', 0)]:
            button = QPushButton(title)
            button.setToolTip('跟随当前窗口' if not width else f'预览 {width} 像素宽度')
            button.clicked.connect(lambda _checked=False, width=width: self.previewWidth.setValue(width))
            canvasTools.addWidget(button)
        canvasTools.addStretch()
        canvasTools.addWidget(self.renderer.fitButton)
        self.centerLayout.addLayout(canvasTools)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.centerLayout.addWidget(self.message)
        self.observation = QLabel('页面只观察明确选择的任务；打开、切页和关闭不会启动检测')
        self.observation.setWordWrap(True)
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
        self.tools = EditingTools(self)
        settings = coordinator.window.settingsStore
        self.floatingToolbox = FloatingPanel(self.canvasHost, self.canvasHost, settings,
            'pageDesigner/toolbox', '工具区', self.libraryTabs, expandedSize=QSize(308, 560),
            collapsedSize=QSize(148, 46))
        self.floatingProperties = FloatingPanel(self.canvasHost, self.canvasHost, settings,
            'pageDesigner/properties', '属性面板', self.propertyPanel,
            right=True, symbol='sliders-horizontal', expanded=True, expandedSize=QSize(340, 620))
        for panel in (self.floatingToolbox, self.floatingProperties):
            panel.expandedChanged.connect(lambda _: self.coordinator.chrome.update())
        self.refresh()

    def togglePanel(self, index):
        self.panel(index).toggle()

    def panel(self, index):
        return self.floatingToolbox if index == 0 else self.floatingProperties

    def pageContextMenu(self, point):
        bar = self.pageTabs.tabBar()
        index = bar.tabAt(point)
        if index < 0:
            return
        self.pageTabs.setCurrentIndex(index)
        if self.pageId == self.pageTabs.pageId(index):
            self.pageMenu.exec_(bar.mapToGlobal(point))

    def run(self, command, *, refresh=True):
        try:
            self.coordinator.sync()
            command()
            if refresh:
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
        self.coordinator.updateWindowTitle()
        if self.pageId not in p.pages:
            self.pageId = p.defaultPageId
        self.pageTabs.syncPages(p, self.pageId)
        self.pageCaption.setText('当前页面：' + p.pages[self.pageId].name if self.pageId else '未选择页面')
        self.pageMenu.setEnabled(bool(p.pages))
        self.pageMore.setEnabled(bool(p.pages))
        self.empty.setVisible(not p.pages)
        self.canvas.setVisible(bool(p.pages))
        self.renderer.setVisible(bool(p.pages))
        self.renderer.banner.setVisible(bool(p.pages))
        self.renderer.fitButton.setVisible(bool(p.pages))
        self.renderer.reload(p)
        self.coordinator.preview.refreshObserver(p)
        if self.pageId:
            self.renderer.navigate(self.pageId)
        if hasattr(self, 'tools'):
            self.tools.refresh()
        self.canvas.updatePageSize()
        self.coordinator.preview.refreshCoverage()
        self.coordinator.chrome.update()

    def choosePage(self, row):
        key = self.pageTabs.pageId(row)
        if key:
            self.renderer.navigate(key)

    def _selectCurrentPage(self):
        self.pageCaption.setText('当前页面：' + self.store.snapshot().pages[self.pageId].name)
        self.pageTabs.blockSignals(True)
        self.pageTabs.selectPage(self.pageId)
        self.pageTabs.blockSignals(False)
        self.renderer.syncNavigation()

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
