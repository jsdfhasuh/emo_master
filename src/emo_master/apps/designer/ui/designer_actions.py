"""One keyboard/action adapter for the existing project and canvas commands."""
from PySide2.QtCore import QEvent, QObject, QTimer, Qt
from PySide2.QtGui import QKeySequence
from PySide2.QtWidgets import (
    QAction, QApplication, QAbstractItemView, QAbstractSpinBox,
    QComboBox, QLineEdit, QMenu, QPlainTextEdit, QTextEdit, QWidget,
)
from shiboken2 import isValid

from .action_state import flowEditAllowed


SHORTCUTS = {
    'open': ('Ctrl+O',), 'save': ('Ctrl+S',),
    'save_as': ('Ctrl+Shift+S',),
    'undo': ('Ctrl+Z',), 'redo': ('Ctrl+Shift+Z', 'Ctrl+Y'),
    'copy': ('Ctrl+D',), 'delete': ('Delete', 'Backspace'),
    'configure': ('F2',), 'results': ('Ctrl+Return', 'Ctrl+Enter'),
    'operator_debug': (),
    'workflow_debug': (),
    'run': ('F5',), 'stop': ('F6',), 'layout': ('Ctrl+L',),
    'fit': ('Home',), 'logs': ('Ctrl+Shift+L',), 'preview': ('Ctrl+Shift+P',),
    'navigate_back': ('Alt+Left',), 'navigate_forward': ('Alt+Right',),
    'definition': ('F12',), 'references': ('Shift+F12',),
}
ALIGNMENT_MODES = (('left', '左对齐'), ('hcenter', '水平居中'), ('right', '右对齐'),
                   ('top', '顶对齐'), ('vcenter', '垂直居中'), ('bottom', '底对齐'))
ALIGNMENT_PREFERENCES = {
    'alignment_guides': ('动态参考线', 'ui/canvas/alignment_guides', 'setAlignmentGuidesEnabled'),
    'alignment_snap': ('轻微吸附', 'ui/canvas/alignment_snap', 'setAlignmentSnapEnabled'),
}
ARRANGEMENT_KEYS = {f'align_{mode}' for mode, _title in ALIGNMENT_MODES} | {
    'distribute_horizontal', 'distribute_vertical'}
SHORTCUTS.update({key: () for key in (*ALIGNMENT_PREFERENCES, *ARRANGEMENT_KEYS)})
CANVAS_KEYS = {'copy', 'delete', 'configure', 'results', 'layout', 'fit'}
NAVIGATION_KEYS = {'navigate_back', 'navigate_forward', 'definition', 'references'}
TEXT_KEYS = CANVAS_KEYS | NAVIGATION_KEYS | {'undo', 'redo'}
INPUT_WIDGETS = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox, QAbstractItemView)


class DesignerActions(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.menus = getattr(window, '_workspaceMenus', None) or {
            menu.title(): menu for menu in window.mainMenuBar.findChildren(
                QMenu, options=Qt.FindDirectChildrenOnly)}
        self.actions = {}
        self.commands = {}
        self.refreshTimer = QTimer(self)
        self.refreshTimer.setSingleShot(True)
        self.refreshTimer.timeout.connect(self.refresh)
        coordinator = window.pageCoordinator
        chrome = getattr(coordinator, 'chrome', None)
        toolbar = window._toolbarActions
        for key, name in [('open', '加载项目'), ('save', '保存项目'), ('run', '开始运行'),
                          ('stop', '停止运行'), ('layout', '自动布局'), ('logs', '打开日志')]:
            self.adopt(key, toolbar[name])
        self.adopt('save_as', window._menuActions['项目另存为…'])
        for key, titles in [('undo', {'撤销', '撤销项目编辑'}), ('redo', {'重做', '重做项目编辑'})]:
            action = getattr(chrome, key, None) if chrome else None
            if action is None:
                action = next((a for a in window.mainToolbar.actions() if a.text() in titles), None)
            if action is None:
                action = self.create(key, '撤销' if key == 'undo' else '重做',
                                     lambda key=key: coordinator.history(key == 'redo'))
                self.menus['编辑'].addAction(action)
            else:
                self.adopt(key, action)
        self.installAlignmentActions()
        self.create('copy', '复制所选算子', self.copy)
        self.replaceMenuAction('复制所选算子', self.actions['copy'])
        self.create('delete', '删除选中元素', self.delete)
        self.create('configure', '配置算子…', self.configure)
        self.create('operator_debug', '算子调试…', self.debugOperator)
        self.create('workflow_debug', '流程调试…', self.window.openWorkflowDebug)
        self.create('results', '查看节点结果', self.showResults)
        self.create('fit', '适应画布', self.fit)
        self.replaceMenuAction('聚焦画布内容', self.actions['fit'])
        for key in ('delete', 'configure', 'operator_debug', 'workflow_debug'):
            self.menus['编辑'].addAction(self.actions[key])
        self.menus['视图'].addAction(self.actions['results'])
        navigation = window.workflowNavigation
        for key, title, command in (
                ('navigate_back', '后退', lambda: navigation.move(-1)),
                ('navigate_forward', '前进', lambda: navigation.move(1)),
                ('definition', '进入子工作流', navigation.goToDefinition),
                ('references', '查找调用来源', navigation.showReferences)):
            self.menus['视图'].addAction(self.create(key, title, command))
        if chrome:
            preview = chrome.previewAction
            oldFit = chrome.fit
            self.replaceAction(oldFit, self.actions['fit'])
        else:
            preview = self.create('preview', '预览页面', self.preview)
            self.menus['视图'].addAction(preview)
        self.adopt('preview', preview)
        # Keep public legacy handles, but only canonical QAction shortcuts fire.
        window.deleteShortcut.setKey(QKeySequence())
        window.backspaceDeleteShortcut.setKey(QKeySequence())
        window.deleteShortcut.setEnabled(False)
        window.backspaceDeleteShortcut.setEnabled(False)
        window.flowScene.selectionChanged.connect(self.scheduleRefresh)
        coordinator.stack.currentChanged.connect(self.scheduleRefresh)
        app = QApplication.instance()
        app.installEventFilter(self)
        app.focusChanged.connect(self.scheduleRefresh)
        self.refresh()

    def installAlignmentActions(self):
        menu = self.menus['编辑']
        existing = menu.actions()
        redo = self.actions['redo']
        index = existing.index(redo) + 1 if redo in existing else 0
        anchor = existing[index] if index < len(existing) else None
        menu.insertSeparator(anchor)
        for key, (title, setting, _method) in ALIGNMENT_PREFERENCES.items():
            action = self.create(key, title, lambda key=key: self.saveAlignmentPreference(key))
            action.setCheckable(True)
            value = self.window.settingsStore.value(setting, True)
            action.setChecked(value if isinstance(value, bool) else str(value).lower() in {'1', 'true'})
            menu.insertAction(anchor, action)
        self.alignmentMenu = QMenu('对齐', menu)
        self.distributionMenu = QMenu('等距分布', menu)
        menu.insertMenu(anchor, self.alignmentMenu)
        menu.insertMenu(anchor, self.distributionMenu)
        for index, (mode, title) in enumerate(ALIGNMENT_MODES):
            if index == 3:
                self.alignmentMenu.addSeparator()
            self.alignmentMenu.addAction(self.create(f'align_{mode}', title,
                lambda mode=mode: self.arrangeSelection(mode)))
        for axis, title in (('horizontal', '水平等距分布'), ('vertical', '垂直等距分布')):
            self.distributionMenu.addAction(self.create(f'distribute_{axis}', title,
                lambda axis=axis: self.arrangeSelection(axis, distribute=True)))
        menu.insertSeparator(anchor)
        for current in (menu, self.alignmentMenu, self.distributionMenu):
            current.setToolTipsVisible(True)
            current.aboutToShow.connect(self.refresh)
        self.applyAlignmentPreferences()

    def applyAlignmentPreferences(self):
        editor = self.pageEditor()
        targets = [self.window.flowScene]
        if editor is not None:
            targets.append(editor.tools)
        for key, (_title, _setting, method) in ALIGNMENT_PREFERENCES.items():
            enabled = self.actions[key].isChecked()
            for target in targets:
                setter = getattr(target, method, None)
                if callable(setter):
                    setter(enabled)

    def saveAlignmentPreference(self, key):
        self.window.settingsStore.setValue(ALIGNMENT_PREFERENCES[key][1], self.actions[key].isChecked())
        self.applyAlignmentPreferences()

    def arrangeSelection(self, mode, *, distribute=False):
        editor = self.pageEditor()
        if editor:
            method = editor.tools.distributeSelection if distribute else editor.tools.alignSelection
            editor.run(lambda: method(mode), refresh=False)
            return
        scene = self.window.flowScene
        try:
            method = scene.distributeSelectedNodes if distribute else scene.alignSelectedNodes
            method(mode)
        except ValueError as error:
            self.window.statusBar().showMessage(str(error), 7000)

    def adopt(self, key, action):
        self.actions[key] = action
        action.setShortcuts([QKeySequence(value) for value in SHORTCUTS[key]])
        action.setShortcutContext(Qt.WindowShortcut)
        action.setAutoRepeat(False)
        self.window.addAction(action)
        return action

    def create(self, key, title, command):
        action = QAction(title, self.window)
        self.commands[key] = command
        action.triggered.connect(lambda _checked=False: self.invoke(key))
        return self.adopt(key, action)

    def replaceMenuAction(self, name, action):
        old = self.window._menuActions.get(name)
        if old is not None:
            self.replaceAction(old, action)
        self.window._menuActions[name] = action

    def replaceAction(self, old, new):
        for menu in self.menus.values():
            if old in menu.actions():
                menu.insertAction(old, new)
                menu.removeAction(old)
        chrome = getattr(self.window.pageCoordinator, 'chrome', None)
        if chrome:
            for collection in (chrome.flowActions, chrome.pageActions):
                if old in collection:
                    collection.remove(old)

    def pageEditor(self):
        coordinator = self.window.pageCoordinator
        return coordinator.editor if coordinator.pageActive() else None

    def selectedNode(self):
        key = self.window.flowScene.getSelectedNodeId()
        return self.window.flowModel.nodes.get(key) if key else None

    def blockedReason(self, key):
        w = self.window
        if not isValid(w) or w.runtimeController._closed or w.runtimeController._closing:
            return 'Designer 已关闭'
        editor = self.pageEditor()
        pages = editor is not None
        if key in ALIGNMENT_PREFERENCES:
            return ''
        if key in ARRANGEMENT_KEYS:
            if pages:
                required = 'distributeSelection' if key.startswith('distribute_') else 'alignSelection'
                if not callable(getattr(editor.tools, required, None)):
                    return '当前页面编辑器尚不支持此排列操作'
                if not editor.renderer.editing:
                    return '请返回编辑模式后排列组件'
                count = editor.tools.selectionCount()
            else:
                if not flowEditAllowed(w):
                    return '请在可编辑的流程画布中排列节点'
                if w.flowScene.isAlignmentDragging() or w.flowScene.isRenamingNode():
                    return '请先完成或取消当前拖动或标题编辑'
                count = len(w.flowScene.getSelectedNodeIds())
            minimum = 3 if key.startswith('distribute_') else 2
            if count < minimum:
                return f'请至少选择 {minimum} 个对象'
            reason = getattr(editor.tools, 'arrangementBlockedReason', None) if pages else None
            return reason(key) if callable(reason) else ''
        if key in {'undo', 'redo'}:
            if w.isJobRunning:
                return '任务运行中不能撤销或重做项目'
            history = w.pageCoordinator.session._redo if key == 'redo' else w.pageCoordinator.session._undo
            return '' if history else '暂无编辑记录'
        if key == 'save_as':
            return '请结束当前任务后再另存项目' if w.isJobRunning else ''
        if key == 'save':
            return ''
        if key == 'open':
            return '请结束当前任务后再打开其他项目' if w.isJobRunning else ''
        if key == 'preview':
            return '' if pages and editor.store.snapshot().pages else '仅在有页面的页面设计中预览'
        if key == 'fit':
            return '' if not pages or editor.store.snapshot().pages else '尚无页面'
        if key in {'copy', 'delete'} and pages:
            selection = getattr(editor.tools, 'retainedSelection', None)
            if selection is not None and selection.active:
                return '请先完成或取消当前拖动'
            if not editor.renderer.editing:
                return '请返回编辑模式后修改组件'
            return '' if editor.tools.selected else '请先选择页面组件'
        if key == 'run':
            if w.hasActiveDebugSession():
                return '请先结束当前调试'
            return '' if w.loadedProjectPath is not None and w.runtimeController.canStart() else '尚未加载项目或所选任务正在运行'
        if key == 'stop':
            return '' if w.runtimeController.canStop() else '没有可停止的当前任务'
        if pages:
            return '仅流程设计可用'
        if key in NAVIGATION_KEYS:
            navigation = w.workflowNavigation
            if key == 'navigate_back':
                return '' if navigation.canMove(-1) else '没有可后退的浏览位置'
            if key == 'navigate_forward':
                return '' if navigation.canMove(1) else '没有可前进的浏览位置'
            if key == 'definition':
                return '' if navigation.targets() else '请选择引用子工作流的调用或循环节点'
            return ''
        if key == 'logs':
            return ''
        if key == 'results':
            coordinator = w.nodeResultCoordinator
            return '' if self.selectedNode() and coordinator and not coordinator.closed else '请先选择有结果面板的节点'
        if not flowEditAllowed(w):
            return '任务运行中可查看结果；请结束运行后再修改流程'
        if key in {'layout', 'workflow_debug'}:
            return ''
        node = self.selectedNode()
        if key == 'delete':
            deletable = any(not w.flowModel.isBoundaryNode(nodeId) for nodeId in w.flowScene.getSelectedNodeIds())
            return '' if deletable or w.flowScene.getSelectedEdgeKeys() else '请选择可删除的节点或连线'
        if node is None:
            return '请只选择一个节点'
        if w.flowModel.isBoundaryNode(node.nodeId):
            return '工作流输入/输出请通过工作流接口配置'
        if key == 'copy' and node.kind != 'operator':
            return '仅支持复制普通算子；控制流程请复制整个工作流'
        if key == 'operator_debug' and node.kind != 'operator':
            return '仅普通算子可孤立调试'
        return ''

    def refresh(self):
        if not isValid(self.window):
            return
        self.applyAlignmentPreferences()
        pages = self.pageEditor() is not None
        self.actions['copy'].setText('复制选中组件' if pages else '复制所选算子')
        self.actions['delete'].setText('删除选中组件' if pages else '删除选中元素')
        for key, action in self.actions.items():
            reason = self.blockedReason(key)
            action.setEnabled(not reason)
            if key not in {'undo', 'redo'}:
                shortcut = f' ({self.shortcutLabel(key)})' if key in NAVIGATION_KEYS else ''
                title = self.window.runTargetsToolTip() if key == 'run' else action.text()
                action.setToolTip((reason or title) + shortcut)
        self.window.deleteShortcut.setEnabled(False)
        self.window.backspaceDeleteShortcut.setEnabled(False)

    def scheduleRefresh(self, *_args):
        if isValid(self.window) and not self.window.runtimeController._closed:
            if not self.refreshTimer.isActive():
                self.refreshTimer.start(0)

    def invoke(self, key):
        # Recheck at execution, even when the caller retained an old QAction.
        if self.blockedReason(key):
            self.refresh()
            return
        if key in self.commands:
            self.commands[key]()
        else:
            self.actions[key].trigger()
        self.refresh()

    def copy(self):
        editor = self.pageEditor()
        if editor:
            editor.run(editor.tools.copy)
        else:
            self.window.duplicateSelectedNode()

    def delete(self):
        editor = self.pageEditor()
        if editor:
            editor.run(editor.tools.delete)
        else:
            self.window.deleteSelectedElements()

    def configure(self):
        self.window.openNodeParamDialog(self.selectedNode().nodeId)

    def debugOperator(self):
        self.window.openNodeParamDialog(self.selectedNode().nodeId)
        command = getattr(self.window.nodeParamDialog, 'openDebug', None)
        if callable(command):
            command()

    def preview(self):
        # Legacy workspaces report an unavailable shared Job in their message
        # area. Do not let that expected validation error escape a Qt slot.
        self.pageEditor().openObserver()

    def showResults(self):
        self.window.nodeResultCoordinator.showPanel()

    def fit(self):
        editor = self.pageEditor()
        if editor:
            editor.canvas.fitPage()
        else:
            self.window.focusGraphContent()

    def shortcutLabel(self, key):
        return QKeySequence(SHORTCUTS[key][0]).toString(QKeySequence.NativeText) if SHORTCUTS[key] else ''

    def inputFocused(self):
        widget = QApplication.focusWidget()
        while widget is not None and widget is not self.window:
            if isinstance(widget, INPUT_WIDGETS):
                return True
            widget = widget.parentWidget()
        return False

    def keyboardAllowed(self, key):
        app = QApplication.instance()
        if app.activeWindow() is not self.window or app.activeModalWidget() or app.activePopupWidget():
            return False
        if key in TEXT_KEYS and self.inputFocused():
            return False
        if key in CANVAS_KEYS:
            focus = app.focusWidget()
            editor = self.pageEditor()
            surfaces = (editor.canvas, editor.renderer) if editor else (self.window.flowView,)
            return focus is self.window or any(focus is surface or
                (focus is not None and surface.isAncestorOf(focus)) for surface in surfaces)
        return True

    def eventKey(self, event):
        sequence = QKeySequence(int(event.modifiers()) | event.key())
        return next((key for key, values in SHORTCUTS.items()
                     if any(sequence == QKeySequence(value) for value in values)), None)

    def eventFilter(self, watched, event):
        if not isValid(self.window):
            return False
        if event.type() == QEvent.Shortcut and watched in self.actions.values():
            key = next(key for key, action in self.actions.items() if action is watched)
            if self.blockedReason(key) or not self.keyboardAllowed(key):
                return True
        if not isinstance(watched, QWidget) or not (watched is self.window or self.window.isAncestorOf(watched)):
            return False
        if event.type() == QEvent.ShortcutOverride:
            key = self.eventKey(event)
            if key is not None:
                self.refresh()
                if not self.keyboardAllowed(key) or self.blockedReason(key):
                    event.accept()  # Preserve the focused widget's native key handling.
                    return True
        elif event.type() == QEvent.MouseButtonPress:
            editor = self.pageEditor()
            if editor and editor.renderer.editing and not self.inputWidget(watched):
                if watched is editor.renderer or editor.renderer.isAncestorOf(watched):
                    editor.renderer.setFocusPolicy(Qt.StrongFocus)
                    editor.renderer.setFocus(Qt.MouseFocusReason)
        elif event.type() == QEvent.Hide and watched is self.window:
            self.refreshTimer.stop()
        elif event.type() in (QEvent.MouseButtonRelease, QEvent.Show):
            self.scheduleRefresh()
        return False

    @staticmethod
    def inputWidget(widget):
        while widget is not None:
            if isinstance(widget, INPUT_WIDGETS):
                return True
            widget = widget.parentWidget()
        return False
