"""One action registry for the mutually exclusive designer workspaces."""
from PySide2.QtCore import QObject, QEvent
from PySide2.QtWidgets import QAction, QActionGroup

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.widgets import ElidedLabel
from .workspace_switch import WorkspaceSwitch


class WorkspaceChrome(QObject):
    def __init__(self, coordinator):
        super().__init__(coordinator.window)
        self._sizingTools = False
        self.c = coordinator
        w = coordinator.window
        self.window = w
        self.runtimeStatus = ElidedLabel('')
        w.statusBar().addPermanentWidget(self.runtimeStatus)
        self.menus = w._workspaceMenus
        self.flowActions = list(dict.fromkeys([
            a for key, a in {**w._toolbarActions, **w._menuActions}.items()
            if key not in {'加载项目', '打开项目', '新建项目…', '保存项目', '校验项目',
                           '开始运行', '停止运行', '停止全部', '运行目标与并发…',
                           '全局变量…', '通信服务…', '打开日志', '刷新算子'}]))
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
        self.menus['Runtime'] = w.menuBar().addMenu('Runtime')
        self.runtimeConnectionAction = self.action('连接 Runtime…', coordinator.connectRuntime)
        self.writeRuntimeAction = self.action('写入 Runtime…', coordinator.configureRuntime)
        self.monitorAction = self.action('在线监视…', coordinator.showOnline)
        for action in (self.runtimeConnectionAction, self.writeRuntimeAction, self.monitorAction):
            self.menus['Runtime'].addAction(action)
        self.deviceAction = self.action('设备管理', coordinator.showDevices)
        w.menuBar().addAction(self.deviceAction)
        w._menuBarGroups = ['文件', '运行', '编辑', '视图', 'Runtime', '设备管理']
        self.bindingsAction = self.action('工程资源 / 现场绑定…', coordinator.resourceBindings)
        self.menus['文件'].addAction(self.bindingsAction)
        self.selector = WorkspaceSwitch(self.workspaceActions, toolbar)
        toolbar.addWidget(self.selector)
        for name, title in [('加载项目', '打开项目'), ('开始运行', '运行流程')]:
            action = w._toolbarActions[name]
            action.setText(title)
        self.undo = self.action('撤销', lambda: coordinator.history(), shortcut='Ctrl+Z')
        self.redo = self.action('重做', lambda: coordinator.history(True), shortcut='Ctrl+Shift+Z')
        firstEdit = self.menus['编辑'].actions()[0]
        for action in (self.undo, self.redo):
            self.menus['编辑'].insertAction(firstEdit, action)
        self.menus['编辑'].insertSeparator(firstEdit)
        layout = w._toolbarActions['自动布局']
        layout.setText('整理流程')
        layout.setToolTip('整理当前流程布局，可撤销；不保存或写入 Runtime')
        self.previewAction = self.action('预览页面', self.preview)
        self.previewAction.setIcon(icon('maximize'))
        self.pageActions.append(self.previewAction)
        self.menus['视图'].addAction(self.previewAction)
        self.menus['文件'].addAction(w._toolbarActions['校验项目'])
        from .delivery import exportDialog
        self.menus['文件'].addAction(self.action('导出测试项目包…', lambda: self.command(lambda: exportDialog(coordinator))))
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
        self.publishPages = self.action('发布工作页面…', self.publish)
        self.menus['Runtime'].addSeparator()
        self.menus['Runtime'].addAction(self.publishPages)
        toolbar.installEventFilter(self)
        self.update()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.LayoutRequest, QEvent.FontChange, QEvent.StyleChange):
            self.resizeTools()
        return super().eventFilter(obj, event)

    def resizeTools(self):
        if self._sizingTools:
            return
        self._sizingTools = True
        try:
            self.selector.setCompact(self.window.mainToolbar.width() < 600)
        finally:
            self._sizingTools = False

    def action(self, title, command, shortcut=''):
        action = QAction(title, self.window)
        action.triggered.connect(lambda _checked=False: command() if action.isEnabled() else None)
        if shortcut:
            action.setShortcut(shortcut)
            self.window.addAction(action)
        return action

    def command(self, command):
        try:
            command()
        except (ValueError, KeyError) as error:
            self.c.preview.message(str(error))
        self.update()

    def flowCommand(self, command):
        if self.c.flowActive():
            self.command(command)

    def preview(self):
        if self.c.pageActive():
            self.command(self.c.preview.openObserver)

    def publish(self):
        self.c.configureRuntime(selectCategories={'presentation'})

    def syncPolicies(self, *_args):
        for action, value in self.policies:
            action.setChecked(self.window.nextRunLegacySnapshotPolicy == value)

    def update(self):
        pages = self.c.pageActive()
        flow = self.c.flowActive()
        index = 1 if pages else 0
        self.workspaceActions[index].setChecked(True)
        self.selector.setCurrentIndex(index)
        for action in self.flowActions:
            action.setVisible(flow)
            action.setEnabled(flow)
        self.window.deleteShortcut.setEnabled(flow and not self.c.deviceActive())
        self.window.backspaceDeleteShortcut.setEnabled(flow and not self.c.deviceActive())
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
            self.window._toolbarActions[key].setEnabled(allowed)
        self.window._menuActions['停止全部'].setEnabled(self.window.isJobRunning)
        for action, history in [(self.undo, self.c.session._undo), (self.redo, self.c.session._redo)]:
            action.setToolTip(action.text() + '项目编辑：' + self.historyDescription(history, redo=action is self.redo))
            action.setEnabled(bool(history) and not self.window.isJobRunning)
        client = self.window.runtimeClient
        from emo_master.apps.designer.services.runtime_client import runtimeConnectionDescription
        target = runtimeConnectionDescription(client)['label']
        text = 'Runtime：' + target + ' · ' + self.c.onlineSummary
        self.runtimeStatus.setText(text)
        self.runtimeStatus.setToolTip(text)
        actions = getattr(self.window, 'designerActions', None)
        if actions is not None:
            actions.refresh()
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
        if previous.get('devices') != current.get('devices'):
            changes.append('设备配置')
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
