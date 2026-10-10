"""Shared input-set and complete-result commands for both debug windows."""
from copy import deepcopy
from pathlib import Path
from threading import Event

from PySide2.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QWidget

from emo_master.apps.designer.services.debug_artifacts import exportRawResult, exportResult, loadFixture, saveFixture
from emo_master.apps.runtime.operator_debug.data import companionPort


class DebugArtifactTools:
    def __init__(self, window, identity, parameters, selection):
        self.window = window
        self.identity, self.parameters, self.selection = identity, parameters, selection
        self.cancelled = Event()
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
                self.window.submit('artifact:save', lambda: saveFixture(path, Path(path).stem, rows, identity,
                    parameters, self.window.connection, cancelled=self.cancelled.is_set))
        except Exception as error:
            self.window.error(error)

    def loadInputs(self):
        if not self.load.isEnabled():
            return
        path, _ = QFileDialog.getOpenFileName(self.window, '载入完整输入集', '', 'EmoMaster 输入集 (*.emofixture)')
        if not path:
            return
        types = {port: row.portType for port, row in self.window.rows.items()}
        self.window.submit('artifact:load', lambda: loadFixture(path, types, self.window.connection,
            cancelled=self.cancelled.is_set))

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
                self.window.submit('artifact:export', lambda: exportResult(path, selection,
                    self.window.connection, cancelled=self.cancelled.is_set))
        except Exception as error:
            self.window.error(error)

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
            self.window.submit('artifact:raw', lambda: exportRawResult(path, selection,
                self.window.connection, cancelled=self.cancelled.is_set))

    def completed(self, name, value):
        if name == 'artifact:load':
            if {port: row.portType for port, row in self.window.rows.items()} != {port: row['type'] for port, row in value['values'].items()}:
                raise ValueError('载入期间端口已改变，未替换任何输入')
            # Entire archive and all uploads succeeded before replacing any row.
            for port, item in value['values'].items():
                self.window.rows[port].restoreWire(item['mode'], item['wire'], item['label'])
            self.window.status.setText('输入集已整体载入：' + value['name'] + '；保存参数仅供核对，不修改当前工程参数')
            self.panel.setToolTip('输入集保存参数：' + str(value['parameters']))
        elif name in {'artifact:save', 'artifact:export', 'artifact:raw'}:
            self.window.status.setText('完整调试数据已保存：' + value)
