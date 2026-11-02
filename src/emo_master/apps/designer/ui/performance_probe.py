"""Manual capture for both workspaces; no timer or event filter while inactive."""
import logging
from pathlib import Path
import platform
from time import get_clock_info

from PySide2 import __version__ as PYSIDE_VERSION
from PySide2.QtCore import QEvent, QObject, Qt, QTimer, QUrl, qVersion
from PySide2.QtGui import QDesktopServices, QKeySequence
from PySide2.QtWidgets import (QAction, QApplication, QCheckBox, QDialog, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from emo_master import __version__
from emo_master.apps.designer.services.performance_probe import DesignerPerformanceProbe, sourceIdentity


class PerformanceProbeController(QObject):
    EVENTS = {QEvent.MouseMove: 'mouseMove', QEvent.MouseButtonPress: 'mousePress',
        QEvent.MouseButtonRelease: 'mouseRelease', QEvent.Wheel: 'wheel',
        QEvent.DragMove: 'dragMove', QEvent.Drop: 'drop', QEvent.Paint: 'paint',
        QEvent.LayoutRequest: 'layout', QEvent.Resize: 'resize',
        QEvent.GraphicsSceneMouseMove: 'sceneMouseMove',
        QEvent.GraphicsSceneDragMove: 'sceneDragMove'}

    def __init__(self, coordinator):
        super().__init__(coordinator.window)
        self.c = coordinator
        self.window = coordinator.window
        self.dialog: QDialog | None = None
        self.recorder: DesignerPerformanceProbe | None = None
        self.destination = None
        self.outputRoot = Path(self.window._localLogDirectory) / 'designer-probes'
        self.beatTimer = QTimer(self)
        self.beatTimer.setTimerType(Qt.PreciseTimer)
        self.beatTimer.setInterval(DesignerPerformanceProbe.INTERVAL_MS)
        self.beatTimer.timeout.connect(self.heartbeat)
        self.statusTimer = QTimer(self)
        self.statusTimer.setInterval(500)
        self.statusTimer.timeout.connect(self.updateStatus)
        self.limitTimer = QTimer(self)
        self.limitTimer.setSingleShot(True)
        self.limitTimer.timeout.connect(self.stop)
        self.markAction = QAction('标记 Designer 卡顿', self.window)
        self.markAction.setShortcut(QKeySequence('Ctrl+Alt+F9'))
        self.markAction.setEnabled(False)
        self.markAction.triggered.connect(self.mark)
        self.window.addAction(self.markAction)

    @property
    def running(self):
        return self.recorder is not None and self.recorder.running

    def show(self):
        if self.dialog is None:
            self.buildDialog()
        assert self.dialog is not None
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def buildDialog(self):
        self.dialog = QDialog(self.window)
        self.dialog.setWindowTitle('Designer 性能探针')
        self.dialog.setModal(False)
        self.dialog.resize(620, 360)
        layout = QVBoxLayout(self.dialog)
        intro = QLabel('流程设计和页面设计共用。开始后回到画布复现卡顿，再停止并导出。\n'
                       '关闭此窗口会继续记录，到时自动停止；不修改工程或撤销历史。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        options = QHBoxLayout()
        options.addWidget(QLabel('自动停止'))
        self.duration = QSpinBox()
        self.duration.setRange(5, 300)
        self.duration.setValue(60)
        self.duration.setSuffix(' 秒')
        options.addWidget(self.duration)
        self.detailed = QCheckBox('记录函数耗时')
        self.detailed.setChecked(True)
        self.detailed.setToolTip('使用已有流程探针的 cProfile/pstats 分析方式；会增加采集开销。\n'
                                '关闭后仍记录界面延迟和卡顿时的 Python 调用栈，适合对照采集。')
        options.addWidget(self.detailed)
        options.addStretch()
        layout.addLayout(options)
        self.status = QLabel('尚未开始记录')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.startButton = QPushButton('开始记录')
        self.markButton = QPushButton('标记卡顿')
        self.markButton.setToolTip('也可在画布按 Ctrl+Alt+F9，记录你感到卡顿的时刻')
        self.stopButton = QPushButton('停止并导出')
        for button, command in ((self.startButton, self.start), (self.markButton, self.mark),
                                (self.stopButton, self.stop)):
            button.setAutoDefault(False)
            button.clicked.connect(command)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        note = QLabel('操作画布时可按 Ctrl+Alt+F9 标记卡顿。报告包含响应延迟、函数耗时、调用次数和调用栈。\n'
                      '采集不含工程内容、控件文字、图片或绑定值；定时器延迟不等于显示帧率。')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        self.folderLabel = QLineEdit(str(self.outputRoot))
        self.folderLabel.setReadOnly(True)
        layout.addWidget(self.folderLabel)
        files = QHBoxLayout()
        self.folderButton = QPushButton('更改保存目录…')
        self.exportButton = QPushButton('重新导出报告')
        self.openButton = QPushButton('打开报告目录')
        for button, command in ((self.folderButton, self.chooseFolder),
                                (self.exportButton, self.export), (self.openButton, self.openFolder)):
            button.setAutoDefault(False)
            button.clicked.connect(command)
            files.addWidget(button)
        layout.addLayout(files)
        self.updateControls()

    def workspace(self):
        return 'page' if self.c.pageActive() else 'flow'

    def viewMetadata(self):
        result = {'workspace': self.workspace(), 'windowSize': [self.window.width(), self.window.height()],
                  'devicePixelRatio': self.window.devicePixelRatioF(),
                  'flowNodes': len(self.window.flowScene._nodeItems),
                  'flowEdges': len(self.window.flowScene._edgeItems)}
        editor = self.c.editor
        if editor is not None:
            page = editor.renderer.config.pages.get(editor.pageId)
            result['page'] = {'layout': page.layout.type if page else None,
                'components': len(editor.renderer.cards.get(editor.pageId, {})),
                'zoom': editor.canvas.getZoomFactor(), 'previewMode': editor.previewMode.currentData(),
                'terminalSize': [editor.canvas.previewWidth, editor.canvas.previewHeight],
                'rendererSize': [editor.renderer.width(), editor.renderer.height()]}
        return result

    def start(self):
        if self.running:
            return
        # Collect source identity before the timed capture. No project snapshot or
        # model traversal in the event filter / heartbeat hot paths.
        metadata = {'source': sourceIdentity(), 'applicationVersion': __version__,
            'python': platform.python_version(), 'platform': platform.system(),
            'qt': qVersion(), 'pyside': PYSIDE_VERSION,
            'cpuClockResolutionSeconds': get_clock_info('thread_time').resolution,
            'initialView': self.viewMetadata()}
        recorder = DesignerPerformanceProbe(metadata, detailed=self.detailed.isChecked())
        try:
            recorder.start()
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.recorder = recorder
        self.recorder.lastWorkspace = self.workspace()
        self.destination = None
        QApplication.instance().installEventFilter(self)
        self.beatTimer.start()
        self.statusTimer.start()
        self.limitTimer.start(self.duration.value() * 1000)
        self.updateControls()
        self.updateStatus()

    def heartbeat(self):
        if self.running:
            assert self.recorder is not None
            self.recorder.heartbeat(self.workspace())

    def mark(self):
        if self.running:
            assert self.recorder is not None
            self.recorder.mark(self.workspace())
            self.updateStatus()

    def eventFilter(self, obj, event):
        name = self.EVENTS.get(event.type())
        if not name or not self.running:
            return False
        assert self.recorder is not None and self.dialog is not None
        if isinstance(obj, QWidget):
            if obj is self.dialog or self.dialog.isAncestorOf(obj):
                return False
            belongs = obj is self.window or self.window.isAncestorOf(obj)
            editor = self.c.editor
            if not belongs and editor is not None:
                # Proxy-embedded widgets have a separate QWidget parent chain.
                belongs = obj is editor.renderer or editor.renderer.isAncestorOf(obj)
        else:
            belongs = obj is self.window.flowScene or (
                self.c.editor is not None and obj is self.c.editor.canvas.scene())
        if belongs:
            self.recorder.event(self.workspace() + '.' + name)
        return False

    def stop(self):
        if not self.running:
            return
        assert self.recorder is not None
        self.beatTimer.stop()
        self.statusTimer.stop()
        self.limitTimer.stop()
        QApplication.instance().removeEventFilter(self)
        self.recorder.stop()
        self.recorder.metadata['finalView'] = self.viewMetadata()
        self.updateControls()
        self.export()

    def export(self):
        if self.recorder is None or self.running:
            return
        try:
            self.destination = self.recorder.export(self.outputRoot)
        except OSError as error:
            self.status.setText(f'导出失败：{error}\n记录仍保留在内存，可更改目录后重新导出。')
            logging.getLogger(__name__).warning('Designer probe export failed: %s', error)
            self.window.statusBar().showMessage('性能探针报告导出失败，请打开探针窗口更改目录后重试', 15000)
        else:
            self.folderLabel.setText(str(self.destination))
            self.status.setText('记录已停止，报告已导出。将 report.json 和 summary.md 一起提供即可排查。')
            self.window.statusBar().showMessage('Designer 性能探针报告已导出，可在“视图 → Designer 性能探针”中打开', 12000)
        self.updateControls()

    def updateStatus(self):
        if self.running:
            assert self.recorder is not None
            elapsed = self.recorder.clock() - self.recorder.started
            self.status.setText(f'正在记录 · {elapsed:.0f} / {self.duration.value()} 秒 · '
                                f'已标记 {len(self.recorder.marks)} 次\n'
                                '请回到画布操作。关闭此窗口后仍会自动停止并导出。')

    def updateControls(self):
        if self.dialog is None:
            return
        active = self.running
        # Keep a failed export recoverable before starting a replacement capture.
        unsaved = self.recorder is not None and not active and self.destination is None
        self.startButton.setEnabled(not active and not unsaved)
        self.markButton.setEnabled(active)
        self.markAction.setEnabled(active)
        self.stopButton.setEnabled(active)
        self.duration.setEnabled(not active)
        self.detailed.setEnabled(not active)
        self.folderButton.setEnabled(not active)
        self.exportButton.setEnabled(self.recorder is not None and not active)
        self.openButton.setEnabled(self.destination is not None)

    def chooseFolder(self):
        directory = QFileDialog.getExistingDirectory(self.dialog, '保存性能报告', str(self.outputRoot))
        if directory:
            self.outputRoot = Path(directory)
            self.folderLabel.setText(str(self.outputRoot))

    def openFolder(self):
        if self.destination is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.destination.resolve())))

    def shutdown(self):
        self.stop()
        if self.dialog is not None:
            self.dialog.close()
