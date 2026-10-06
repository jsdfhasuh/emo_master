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
    'undo': ('Ctrl+Z',), 'redo': ('Ctrl+Shift+Z', 'Ctrl+Y'),
    'copy': ('Ctrl+D',), 'delete': ('Delete', 'Backspace'),
    'configure': ('F2',), 'results': ('Ctrl+Return', 'Ctrl+Enter'),
    'run': ('F5',), 'stop': ('F6',), 'layout': ('Ctrl+L',),
    'fit': ('Home',), 'logs': ('Ctrl+Shift+L',), 'preview': ('Ctrl+Shift+P',),
}
CANVAS_KEYS = {'copy', 'delete', 'configure', 'results', 'layout', 'fit'}
TEXT_KEYS = CANVAS_KEYS | {'undo', 'redo'}
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
        self.create('copy', '复制所选算子', self.copy)
        self.replaceMenuAction('复制所选算子', self.actions['copy'])
        self.create('delete', '删除选中元素', self.delete)
        self.create('configure', '配置算子…', self.configure)
        self.create('results', '查看节点结果', self.showResults)
        self.create('fit', '适应画布', self.fit)
        self.replaceMenuAction('聚焦画布内容', self.actions['fit'])
        for key in ('delete', 'configure'):
            self.menus['编辑'].addAction(self.actions[key])
        self.menus['视图'].addAction(self.actions['results'])
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
        if key in {'undo', 'redo'}:
            if w.isJobRunning:
                return '任务运行中不能撤销或重做项目'
            history = w.pageCoordinator.session._redo if key == 'redo' else w.pageCoordinator.session._undo
            return '' if history else '暂无编辑记录'
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
        if pages:
            return '仅流程设计可用'
        if key == 'run':
            return '' if w.loadedProjectPath is not None and not w.isJobRunning else '尚未加载项目或任务正在运行'
        if key == 'stop':
            return '' if w.isJobRunning and w.currentJobId is not None else '没有可停止的当前任务'
        if key == 'logs':
            return ''
        if key == 'results':
            coordinator = w.nodeResultCoordinator
            return '' if self.selectedNode() and coordinator and not coordinator.closed else '请先选择有结果面板的节点'
        if not flowEditAllowed(w):
            return '任务运行中可查看结果；请结束运行后再修改流程'
        if key == 'layout':
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
        return ''

    def refresh(self):
        if not isValid(self.window):
            return
        pages = self.pageEditor() is not None
        self.actions['copy'].setText('复制选中组件' if pages else '复制所选算子')
        self.actions['delete'].setText('删除选中组件' if pages else '删除选中元素')
        for key, action in self.actions.items():
            reason = self.blockedReason(key)
            action.setEnabled(not reason)
            if key not in {'undo', 'redo'}:
                action.setToolTip(reason or action.text())
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

    def preview(self):
        # Legacy workspaces report an unavailable shared Job in their message
        # area. Do not let that expected validation error escape a Qt slot.
        self.pageEditor().openObserver()

    def showResults(self):
        w = self.window
        w.onNodeSelectionChanged()
        w.rightPanelContainer.show()
        coordinator = w.nodeResultCoordinator
        coordinator.panelScroll.ensureWidgetVisible(coordinator.panel)
        coordinator.panel.tabs.setFocus(Qt.OtherFocusReason)

    def fit(self):
        editor = self.pageEditor()
        if editor:
            editor.renderer.fitToAvailableScreen()
        else:
            self.window.focusGraphContent()

    def shortcutLabel(self, key):
        return QKeySequence(SHORTCUTS[key][0]).toString(QKeySequence.NativeText)

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
            surface = editor.renderer if editor else self.window.flowView
            return focus is self.window or focus is surface or (focus is not None and surface.isAncestorOf(focus))
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
