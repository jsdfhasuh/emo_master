"""Shared input-set and complete-result commands for both debug windows."""
from copy import deepcopy
from pathlib import Path
from threading import Event

from PySide2.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QWidget

from emo_master.apps.designer.services.debug_artifacts import exportRawResult, exportResult, loadFixture, saveFixture
from emo_master.apps.runtime.operator_debug.data import companionPort
from .widgets import WrapLabel


class DebugActionFeedback:
    """Action failures survive healthy session polls and unrelated successes."""
    def __init__(self, parent):
        self.label = WrapLabel('', parent)
        self.errors = {}
        self.message = ''
        self.label.hide()

    def failure(self, name, message):
        self.errors[name] = message
        self.message = ''
        self.render()

    def success(self, name, message=''):
        self.errors.pop(name, None)
        self.message = message
        self.render()

    def progress(self, message):
        self.message = message
        self.render()

    def render(self):
        text = '\n'.join([*self.errors.values(), *([self.message] if self.message else [])])
        self.label.setText(text)
        self.label.setToolTip(text)
        self.label.setVisible(bool(text))


class DebugArtifactTools:
    def __init__(self, window, identity, parameters, selection):
        self.window = window
        self.identity, self.parameters, self.selection = identity, parameters, selection
        self.cancelled = Event()
        self.pendingInput = None
        self.activeInputs = {}
        self.panel = QWidget(window)
        bar = QHBoxLayout(self.panel)
        bar.setContentsMargins(0, 0, 0, 0)
        self.save = QPushButton('保存命名输入集…')
        self.load = QPushButton('载入输入集…')
        self.export = QPushButton('导出完整结果…', window)
        self.raw = QPushButton('保存原始数据…', window)
        self.raw.setToolTip('保存原始 PNG / 完整 JSON，不包含身份元信息；需要复核调用与轮次时请导出完整结果包。')
        self.export.setToolTip('保存单个原子结果包，包含原始 PNG / 完整 JSON 与调用身份 metadata.json；不是截图或当前树页。')
        for button, callback in ((self.save, self.saveInputs), (self.load, self.loadInputs)):
            bar.addWidget(button)
            button.clicked.connect(callback)
        bar.addStretch()
        self.export.clicked.connect(self.exportSelection)
        self.raw.clicked.connect(self.exportRawSelection)

    def inputContext(self):
        # Keep both the Runtime generation and the exact input widgets selected
        # before a native dialog starts its nested event loop.
        return deepcopy(self.window.connection.identity), dict(self.window.rows)

    def submitInput(self, name, work, context, description):
        if self.window.disposed or self.window.closing or self.cancelled.is_set():
            return False
        if self.pendingInput is not None:
            self.window.error(ValueError('已有选定输入等待处理；未提交新选择：' + description), action=name)
            return False
        self.pendingInput = (name, work, context, description)
        self.window.actionFeedback.progress('已保留选择，等待当前操作完成：' + description)
        self.drainInputs()
        refresh = getattr(self.window, 'refreshControls', None) or self.window.refresh
        refresh()
        return True

    def drainInputs(self):
        if self.pendingInput is None:
            return
        window = self.window
        name, work, (identity, rows), description = self.pendingInput
        if window.closing or window.disposed:
            self.pendingInput = None
            return
        if (window.connection.uncertain or not window.connection.attached() or not window.valid()
                or identity != window.connection.identity or rows != window.rows or window.state != 'READY'):
            self.pendingInput = None
            window.error(ValueError('会话或输入端口已改变，未上传或替换输入；请重新选择：' + description), action=name)
            return
        if window.busy or getattr(window, 'refreshPending', False):
            return
        # Dequeue before dispatch. A failed/unknown upload is never resent.
        self.pendingInput = None
        self.activeInputs[name] = (identity, rows)
        if window.submit(name, work):
            window.actionFeedback.progress('正在处理已选输入：' + description)
        else:
            self.activeInputs.pop(name, None)
            window.error(ValueError('当前不能提交，未上传或替换输入；请重新选择：' + description), action=name)

    def finishInput(self, name):
        context = self.activeInputs.pop(name, None)
        if context is not None and context != self.inputContext():
            raise ValueError('处理期间会话或输入端口已改变，未替换任何输入；请重新选择')

    def completeFile(self, name, value):
        self.finishInput(name)
        self.window.rows[name[5:]].setReference(value, value['assetRef'])
        self.window.actionFeedback.success(name, '完整输入已上传：' + name[5:])

    def submitSave(self, name, work, path):
        if not self.window.submit(name, work):
            self.window.error(ValueError('当前有操作处理中，未保存；请重试：' + str(path)), action=name)

    def refresh(self, ready, idle):
        self.save.setEnabled(ready)
        self.load.setEnabled(ready)
        self.export.setEnabled(idle and self.selection() is not None)
        self.raw.setEnabled(self.export.isEnabled())

    def _savePath(self, title, initial, suffix, filterText):
        path, _ = QFileDialog.getSaveFileName(self.window, title, initial, filterText)
        if path and not path.lower().endswith(suffix):
            path += suffix
            if Path(path).exists() and QMessageBox.question(self.window, '覆盖调试文件',
                '文件已存在，是否覆盖？\n' + path, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return ''
        return path

    def saveInputs(self):
        if not self.save.isEnabled():
            return
        try:
            rows = {}
            for port, row in self.window.rows.items():
                rows[port] = dict(type=row.portType, mode=['missing', 'value', 'null', 'source'][row.mode.currentIndex()],
                                  wire=row.wire(), companionPort=companionPort(port, self.window.rows))
            identity, parameters = deepcopy(self.identity()), deepcopy(self.parameters())
            path = self._savePath('保存命名输入集', '调试输入.emofixture', '.emofixture', 'EmoMaster 输入集 (*.emofixture)')
            if path:
                self.submitSave('artifact:save', lambda: saveFixture(path, Path(path).stem, rows, identity,
                    parameters, self.window.connection, cancelled=self.cancelled.is_set), path)
        except Exception as error:
            self.window.error(error, action='artifact:save')

    def loadInputs(self):
        if not self.load.isEnabled():
            return
        context = self.inputContext()
        path, _ = QFileDialog.getOpenFileName(self.window, '载入完整输入集', '', 'EmoMaster 输入集 (*.emofixture)')
        if not path:
            return
        types = {port: row.portType for port, row in context[1].items()}
        self.submitInput('artifact:load', lambda: loadFixture(path, types, self.window.connection,
            cancelled=self.cancelled.is_set), context, path)

    def exportSelection(self):
        if not self.export.isEnabled():
            return
        try:
            selection = deepcopy(self.selection())
            if selection is None:
                raise ValueError('请先选择完整结果')
            path = self._savePath('导出原始数据与调用身份', '调试结果.emodebug.zip', '.emodebug.zip',
                                  '完整结果包 (*.emodebug.zip)')
            if path:
                self.submitSave('artifact:export', lambda: exportResult(path, selection,
                    self.window.connection, cancelled=self.cancelled.is_set), path)
        except Exception as error:
            self.window.error(error, action='artifact:export')

    def exportRawSelection(self):
        if not self.raw.isEnabled():
            return
        selection = deepcopy(self.selection())
        if selection is None:
            return
        suffix = '.png' if selection.get('mime') == 'image/png' else '.json'
        path = self._savePath('保存完整原始数据（身份信息请另导出完整结果包）', 'value' + suffix,
                              suffix, '完整原始数据 (*' + suffix + ')')
        if path:
            self.submitSave('artifact:raw', lambda: exportRawResult(path, selection,
                self.window.connection, cancelled=self.cancelled.is_set), path)

    def completed(self, name, value):
        if name == 'artifact:load':
            self.finishInput(name)
            if {port: row.portType for port, row in self.window.rows.items()} != {port: row['type'] for port, row in value['values'].items()}:
                raise ValueError('载入期间端口已改变，未替换任何输入')
            # Entire archive and all uploads succeeded before replacing any row.
            for port, item in value['values'].items():
                self.window.rows[port].restoreWire(item['mode'], item['wire'], item['label'])
            self.window.actionFeedback.success(name, '输入集已整体载入：' + value['name'] + '；保存参数仅供核对，不修改当前工程参数')
            self.panel.setToolTip('输入集保存参数：' + str(value['parameters']))
        elif name in {'artifact:save', 'artifact:export', 'artifact:raw'}:
            self.window.actionFeedback.success(name, '完整调试数据已保存：' + value)
