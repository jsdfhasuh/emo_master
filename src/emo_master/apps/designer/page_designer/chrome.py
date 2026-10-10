"""One action registry for the mutually exclusive designer workspaces."""
from PySide2.QtCore import Qt, QObject, QEvent
from PySide2.QtWidgets import QAction, QActionGroup, QMenu, QToolButton, QStyle

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.widgets import ElidedLabel
from .workspace_switch import WorkspaceSwitch


class WorkspaceChrome(QObject):
    def __init__(self, coordinator):
        super().__init__(coordinator.window)
        self._sizingTools = False
        self._toolSizeKey = None
        self.c = coordinator
        w = coordinator.window
        self.window = w
        self.testStatus = ElidedLabel('')
        w.statusBar().addPermanentWidget(self.testStatus)
        self.menus = w._workspaceMenus
        self.flowActions = list(dict.fromkeys([
            a for key, a in {**w._toolbarActions, **w._menuActions}.items()
            if key not in {'加载项目', '打开项目', '新建项目…', '保存项目', '校验项目'}]))
        self.pageActions = []
        toolbar = w.mainToolbar
        for action in toolbar.actions():
            toolbar.removeAction(action)
        self.workspaceActions = []
        group = QActionGroup(self)
        for text, command in [('流程设计', coordinator.showFlow), ('页面设计', coordinator.showPages)]:
            action = QAction(text, self)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(command)
            self.workspaceActions.append(action)
        self.selector = WorkspaceSwitch(self.workspaceActions, toolbar)
        toolbar.addWidget(self.selector)
        toolbar.addSeparator()
        for name, attribute in [('加载项目', 'loadButton'), ('保存项目', 'saveProjectButton')]:
            action = w._toolbarActions[name]
            if name == '加载项目':
                action.setText('打开项目')
                action.setToolTip('打开项目')
            tool = self.addTool(action)
            setattr(w, attribute, tool)
        toolbar.addSeparator()
        # QToolBar overflows from the end. Keep the run controls ahead of edit
        # conveniences so even a narrow running window retains its Stop button.
        for name, attribute, title in [('开始运行', 'startButton', '运行流程'),
                                       ('停止运行', 'stopButton', '停止运行')]:
            action = w._toolbarActions[name]
            action.setText(title)
            tool = self.addTool(action)
            tool.setObjectName('primaryButton' if name == '开始运行' else 'dangerButton')
            setattr(w, attribute, tool)
        toolbar.addSeparator()
        self.undo = self.action('撤销', lambda: coordinator.history(), shortcut='Ctrl+Z')
        self.redo = self.action('重做', lambda: coordinator.history(True), shortcut='Ctrl+Shift+Z')
        for action in (self.undo, self.redo):
            self.addTool(action)
            self.menus['编辑'].insertAction(self.menus['编辑'].actions()[0], action)
        toolbar.addSeparator()
        self.testMenu = QMenu('图片测试', w)
        self.testActions = {}
        for title, command in [('选择测试图片', coordinator.importInput),
                               ('开始测试', coordinator.preview.startDebug),
                               ('停止测试', coordinator.preview.stopTest)]:
            action = self.action(title, lambda command=command: self.flowCommand(command))
            self.testMenu.addAction(action)
            self.testActions[title] = action
        self.testActions['开始测试'].setToolTip('使用当前配置和测试图片，不控制设备；仅支持已验证的图片处理算子')
        self.testAction = self.testMenu.menuAction()
        self.addTool(self.testAction).setPopupMode(QToolButton.InstantPopup)
        self.flowActions.append(self.testAction)
        layout = w._toolbarActions['自动布局']
        layout.setText('整理流程')
        w.autoLayoutButton = self.addTool(layout)
        # Existing controllers retain these command handles; their menu action
        # owns availability now that the former toolbar button is removed.
        for attribute, name in [('validateButton', '校验项目'), ('validateGraphButton', '校验流程图'),
                                ('refreshButton', '刷新算子'), ('openLogsButton', '打开日志')]:
            setattr(w, attribute, w._toolbarActions[name])
        self.previewAction = self.action('预览页面', self.preview)
        self.previewAction.setIcon(icon('maximize'))
        self.addTool(self.previewAction).setObjectName('primaryButton')
        self.pageActions.append(self.previewAction)
        self.menus['视图'].addAction(self.previewAction)
        self.menus['文件'].addAction(w._toolbarActions['校验项目'])
        from .delivery import exportDialog
        self.menus['文件'].addAction(self.action('导出测试项目包…', lambda: self.command(lambda: exportDialog(coordinator))))
        self.menus['运行'].addMenu(self.testMenu)
        self.menus['运行'].addAction(w._toolbarActions['刷新算子'])
        capture = self.menus['运行'].addMenu('结果采集设置')
        policies = QActionGroup(capture)
        self.policies = []
        for title, value in [('采集流程节点结果', 'ALL'), ('不采集流程节点结果', 'NONE')]:
            action = capture.addAction(title)
            action.setCheckable(True)
            action.setChecked(w.nextRunLegacySnapshotPolicy == value)
            action.setToolTip('仅影响下一次运行；不会改变当前运行或页面所需数据的采集')
            action.triggered.connect(lambda _checked=False, value=value:
                                     self.flowCommand(lambda: w.setNextRunLegacySnapshotPolicy(value)))
            policies.addAction(action)
            self.policies.append((action, value))
        self.flowActions.extend([capture.menuAction(), *self.testActions.values(), *policies.actions()])
        if w.legacySnapshotPolicyCombo:
            w.legacySnapshotPolicyCombo.currentIndexChanged.connect(self.syncPolicies)
        for title, index in [('组件与页面栏', 0), ('属性栏', 2)]:
            action = self.action(title, lambda index=index: self.c.editor.togglePanel(index) if self.c.pageActive() else None)
            action.setCheckable(True)
            self.menus['视图'].addAction(action)
            self.pageActions.append(action)
        self.fit = self.action('适应画布', lambda: self.c.editor.renderer.fitToAvailableScreen() if self.c.pageActive() else None)
        self.menus['视图'].addAction(self.fit)
        self.pageActions.append(self.fit)
        toolbar.installEventFilter(self)
        self.update()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.LayoutRequest, QEvent.FontChange, QEvent.StyleChange):
            self.resizeTools()
        return super().eventFilter(obj, event)

    def resizeTools(self):
        if self._sizingTools:
            return
        toolbar = self.window.mainToolbar
        buttons = (self.window.loadButton, self.window.saveProjectButton,
                   self.window.startButton, self.window.stopButton)
        key = (toolbar.width(), toolbar.font().toString(), toolbar.styleSheet(),
               self.selector.font().toString(), self.selector.sizeHint().width(),
               tuple((a.isVisible(), a.text()) for a in toolbar.actions()),
               tuple((b.font().toString(), b.iconSize().width(), b.iconSize().height()) for b in buttons))
        # Changing a button's style posts another LayoutRequest. A stable key
        # keeps both that callback and routine status refreshes idempotent.
        if key == self._toolSizeKey:
            return
        self._toolSizeKey = key
        self._sizingTools = True
        try:
            for button in buttons:
                button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            if toolbar.width() < 900:
                for button in buttons[:2]:
                    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            if self.c.pageActive():
                return
            for group in (buttons[:2], buttons[2:]):
                if self._runControlsWidth() <= toolbar.contentsRect().width():
                    break
                for button in group:
                    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        finally:
            self._sizingTools = False

    def _runControlsWidth(self):
        toolbar = self.window.mainToolbar
        layout = toolbar.layout()
        left, _top, right, _bottom = layout.getContentsMargins()
        style = toolbar.style()
        spacing = max(0, layout.spacing(), style.pixelMetric(QStyle.PM_ToolBarItemSpacing))
        width = left + right + style.pixelMetric(QStyle.PM_ToolBarExtensionExtent)
        if toolbar.isMovable():
            width += style.pixelMetric(QStyle.PM_ToolBarHandleExtent)
        count = 0
        for action in toolbar.actions():
            if action.isVisible():
                widget = toolbar.widgetForAction(action)
                if widget is not None:
                    width += max(widget.sizeHint().width(), widget.minimumWidth())
                    count += 1
            if action is self.window._toolbarActions['停止运行']:
                break
        return width + max(0, count - 1) * spacing

    def action(self, title, command, shortcut=''):
        action = QAction(title, self.window)
        action.triggered.connect(lambda _checked=False: command() if action.isEnabled() else None)
        if shortcut:
            action.setShortcut(shortcut)
            self.window.addAction(action)
        return action

    def addTool(self, action):
        self.window.mainToolbar.addAction(action)
        tool = self.window.mainToolbar.widgetForAction(action)
        tool.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tool.setAccessibleName(action.text())
        return tool

    def command(self, command):
        try:
            command()
        except (ValueError, KeyError) as error:
            self.c.preview.message(str(error))
        self.update()

    def flowCommand(self, command):
        if not self.c.pageActive():
            self.command(command)

    def preview(self):
        if self.c.pageActive():
            self.command(self.c.preview.openObserver)

    def syncPolicies(self, *_args):
        for action, value in self.policies:
            action.setChecked(self.window.nextRunLegacySnapshotPolicy == value)

    def update(self):
        pages = self.c.pageActive()
        self.testStatus.setVisible(not pages and self.c.preview.backend is not None)
        self.workspaceActions[int(pages)].setChecked(True)
        self.selector.setCurrentIndex(int(pages))
        self.menus['运行'].menuAction().setVisible(not pages)
        for action in self.flowActions:
            action.setVisible(not pages)
            action.setEnabled(not pages)
        self.window.deleteShortcut.setEnabled(not pages)
        self.window.backspaceDeleteShortcut.setEnabled(not pages)
        for action in self.pageActions:
            action.setVisible(pages)
            action.setEnabled(pages)
        if pages:
            self.previewAction.setEnabled(bool(self.c.editor.store.snapshot().pages))
            for action, index in zip(self.pageActions[1:3], (0, 2)):
                action.setChecked(self.c.editor.splitter.sizes()[index] > 0)
        runtime = self.window.runtimeController
        for key, allowed in [('开始运行', self.window.loadedProjectPath is not None and runtime.canStart()),
                             ('停止运行', runtime.canStop())]:
            self.window._toolbarActions[key].setEnabled(not pages and allowed)
        preview = self.c.preview
        self.testActions['开始测试'].setEnabled(not pages and not preview.busy and preview.backend is None)
        self.testActions['停止测试'].setEnabled(not pages and preview.backend is not None and not preview.busy)
        for action, history in [(self.undo, self.c.session._undo), (self.redo, self.c.session._redo)]:
            action.setToolTip(action.text() + '项目编辑：' + self.historyDescription(history, redo=action is self.redo))
        self.resizeTools()

    def historyDescription(self, history, *, redo=False):
        if not history:
            return '暂无记录'
        previous, current = history[-1], self.c.session.payload()
        if redo:
            previous, current = current, previous
        changes = []
        if previous.get('presentation') != current.get('presentation'):
            changes.append(self.pageChange(previous.get('presentation') or {}, current.get('presentation') or {}))
        if previous.get('workflows') != current.get('workflows'):
            changes.append('流程内容')
        if previous.get('resources') != current.get('resources'):
            changes.append('测试图片资源')
        return '、'.join(changes) or '项目设置'

    @staticmethod
    def pageChange(before, after):
        oldPages, pages = before.get('pages', {}), after.get('pages', {})
        for key in pages.keys() - oldPages.keys():
            return '添加页面「' + pages[key]['name'] + '」'
        for key in oldPages.keys() - pages.keys():
            return '删除页面「' + oldPages[key]['name'] + '」'
        if before.get('defaultPageId') != after.get('defaultPageId'):
            return '设置首页'
        if before.get('pageOrder') != after.get('pageOrder'):
            return '调整页面顺序'
        def components(items):
            result = {}
            for item in items:
                result[item['componentId']] = item
                result.update(components(item.get('children', [])))
            return result
        for key, page in pages.items():
            previous = oldPages[key]
            if previous == page:
                continue
            if previous['name'] != page['name']:
                return '重命名页面「' + page['name'] + '」'
            old, new = components(previous.get('components', [])), components(page.get('components', []))
            if new.keys() - old.keys():
                return '添加或复制组件'
            if old.keys() - new.keys():
                return '删除组件'
            for componentId, item in new.items():
                former = old[componentId]
                title = item.get('props', {}).get('title') or page['name']
                if former.get('bindings') != item.get('bindings'):
                    return '修改数据来源「' + title + '」'
                if former.get('layout') != item.get('layout'):
                    return '调整位置或大小「' + title + '」'
                if former != item:
                    return '修改组件属性「' + title + '」'
            return '修改页面属性「' + page['name'] + '」'
        return '修改页面设置'
