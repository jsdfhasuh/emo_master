"""Read online identity, then perform explicitly confirmed managed captures."""
from concurrent.futures import ThreadPoolExecutor
import json

from PySide2.QtCore import Qt, QTimer
from PySide2.QtGui import QPixmap
from PySide2.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox, QFormLayout, QDoubleSpinBox, QTextBrowser, QApplication

from .icon_map import icon

from emo_master.core.project.snapshots import revisionOf


class ManagedCameraDebugDialog(QDialog):
    def __init__(self, client, projectId, deviceId, definition, parent=None):
        super().__init__(parent)
        self.client, self.projectId, self.deviceId = client, projectId, deviceId
        self.definition = definition
        self.runtimeId = ""
        self.state = None
        self.sessionId = ""
        self.sequence = 0
        self.closed = False
        self.pending = None
        self.tuningVersion = 0
        self.previewVersion = 0
        self.watchSequence = 0
        self.productionActive = None
        self.pendingKind = 'open'
        self.previewPixmap = None
        self.lastProduction = None
        self.readOnReady = False
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="camera-diagnostic-ui")
        self.setWindowTitle("相机诊断 · " + definition.name)
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(240, 180)
        layout.addWidget(self.preview, 1)
        self.mode = QLabel('观看已有采集结果')
        self.mode.setWordWrap(True)
        layout.addWidget(self.mode)
        self.status = QLabel("尚未打开诊断会话")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.actual = QTextBrowser()
        self.actual.setMaximumHeight(130)
        layout.addWidget(self.actual)
        trialForm = QFormLayout()
        self.exposure, self.gain = QDoubleSpinBox(), QDoubleSpinBox()
        self.exposure.setRange(1, 10000000)
        self.exposure.setValue(float(definition.defaults.get('exposureUs', 8000)))
        self.exposure.setSuffix(' us')
        self.gain.setRange(0, 10000)
        self.gain.setValue(float(definition.defaults.get('gainRaw', 0)))
        trialForm.addRow('试调曝光', self.exposure)
        trialForm.addRow('试调增益', self.gain)
        layout.addLayout(trialForm)
        row = QHBoxLayout()
        self.captureButton = QPushButton("采集一帧")
        self.captureButton.setEnabled(False)
        self.captureButton.clicked.connect(self.capture)
        self.captureButton.setIcon(icon('camera'))
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        row.addWidget(self.captureButton)
        self.liveButton = QPushButton('实时预览')
        self.liveButton.setIcon(icon('play'))
        self.liveButton.setCheckable(True)
        self.liveButton.setEnabled(False)
        self.liveButton.setToolTip('非生产时逐次采集；生产启动后转为观看已有结果')
        self.liveButton.clicked.connect(self.liveToggled)
        row.addWidget(self.liveButton)
        self.watchButton = QPushButton()
        self.watchButton.setIcon(icon('refresh-cw'))
        self.watchButton.setToolTip('重新读取在线设备状态并观看')
        self.watchButton.setAccessibleName('重新读取在线设备状态并观看')
        self.watchButton.clicked.connect(self.restartWatching)
        row.addWidget(self.watchButton)
        self.readButton = QPushButton('读取实际参数')
        self.trialButton = QPushButton('试调采集并恢复')
        self.readButton.clicked.connect(lambda: self.diagnostic('read_parameters'))
        self.trialButton.clicked.connect(lambda: self.diagnostic('trial_capture'))
        for button in (self.readButton, self.trialButton):
            button.setEnabled(False)
            row.addWidget(button)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.ready)
        self.previewTimer = QTimer(self)
        self.previewTimer.setInterval(250)
        self.previewTimer.timeout.connect(self.updatePreview)
        self.finished.connect(self.dispose)
        self.pending = self.pool.submit(self.open)
        self.timer.start(40)

    def open(self):
        runtimeId, states = self.client.getDeviceStates(self.projectId)
        state = states.get(self.deviceId)
        if state is None:
            raise ValueError('设备尚未配置到 Runtime，请先比较配置')
        self.localMatches = state['definitionRevision'] == revisionOf(self.definition.model_dump())
        self.runtimeId, self.state = runtimeId, state
        result = self.call("open")
        self.sessionId = result["sessionId"]
        if self.closed:
            self.call("close")
        return result

    def call(self, action, changes=None):
        extra = dict(changes=changes) if changes is not None else {}
        return self.client.cameraDiagnostic(self.projectId, self.runtimeId, self.deviceId, self.state, action,
            sessionId=self.sessionId, sequence=self.watchSequence if action == 'watch' else self.sequence,
            confirmed=action in {"capture", "read_parameters", "trial_capture", 'preview_capture'}, **extra)

    def stopLive(self):
        self.liveButton.setChecked(False)
        self.liveButton.setIcon(icon('play'))

    def restartWatching(self):
        if self.pending is not None or self.closed:
            return
        self.stopLive()
        self.previewTimer.stop()
        def reopen():
            if self.sessionId:
                self.call('close')
            self.sessionId, self.sequence, self.watchSequence = '', 0, 0
            self.productionActive = None
            return self.open()
        self.pendingKind = 'open'
        self.pending = self.pool.submit(reopen)
        self.timer.start(40)

    def liveToggled(self, checked):
        if checked:
            if QMessageBox.question(self, '实时预览', '非生产时将逐次采集图像，不写入工程或在线配置。生产启动后停止主动取图并观看生产结果。继续？',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self.stopLive()
                return
            self.liveButton.setIcon(icon('square'))
        else:
            self.stopLive()
        self.updatePreview()

    def updatePreview(self):
        if (self.closed or self.pending is not None or not self.sessionId or not self.previewVersion
                or QApplication.activeModalWidget() not in {None, self}):
            return
        action = 'preview_capture' if self.liveButton.isChecked() and self.productionActive is False else 'watch'
        if action == 'preview_capture':
            self.sequence += 1
        self.pendingKind = action
        self.pending = self.pool.submit(self.call, action)
        for button in (self.captureButton, self.readButton, self.trialButton):
            button.setEnabled(False)
        self.timer.start(40)

    def diagnostic(self, action):
        if self.pending is not None or not self.sessionId or self.closed:
            return
        if self.tuningVersion < 1:
            self.status.setText('Runtime 不支持参数回读和试调，请升级 Runtime')
            return
        self.stopLive()
        message = ('将连接相机并读取实际参数，不触发采集。继续？' if action == 'read_parameters' else
                   '将临时设置曝光和增益、采集一帧，并恢复试调前参数。不会写入工程或在线配置。继续？')
        if QMessageBox.question(self, '相机诊断', message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        changes = dict(exposureMode='manual', exposureUs=self.exposure.value(), gainMode='manual', gainRaw=self.gain.value()) if action == 'trial_capture' else {}
        self.sequence += 1
        for button in (self.readButton, self.trialButton, self.captureButton):
            button.setEnabled(False)
        self.pending = self.pool.submit(self.call, action, changes)
        self.pendingKind = action
        self.status.setText('正在读取实际参数' if action == 'read_parameters' else '正在试调采集并恢复')
        self.timer.start(40)

    def capture(self):
        if self.pending is not None or not self.sessionId or self.closed:
            return
        if QMessageBox.question(self, "确认相机采集", "将使用在线设备默认参数采集一帧。软件触发会实际触发曝光；"
                "硬件触发会消费采集队列中的一帧。确认现场允许诊断采集？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.sequence += 1
        self.stopLive()
        for button in (self.captureButton, self.readButton, self.trialButton):
            button.setEnabled(False)
        self.status.setText("采集中")
        self.pending = self.pool.submit(self.call, "capture")
        self.pendingKind = 'capture'
        self.timer.start(40)

    def ready(self):
        if self.pending is None or not self.pending.done():
            return
        self.timer.stop()
        future, self.pending = self.pending, None
        try:
            result = future.result()
            metadata = result.get('metadata', {})
            self.tuningVersion = metadata.get('cameraTuningVersion', self.tuningVersion)
            self.previewVersion = metadata.get('cameraPreviewVersion', self.previewVersion)
            if self.pendingKind == 'watch':
                self.productionActive = metadata['productionActive']
                self.watchSequence = metadata['watchSequence']
                if self.productionActive:
                    self.stopLive()
                self.mode.setText('生产中 · 仅观看已有采集结果' if self.productionActive else
                    '非生产 · 实时预览' if self.liveButton.isChecked() else '非生产 · 观看上次生产结果 / 等待新帧')
            if metadata.get('source') == 'production':
                self.lastProduction = metadata
            if 'actual' in metadata:
                self.actual.setPlainText(json.dumps(metadata['actual'], ensure_ascii=False, indent=2))
                self.status.setText('实际参数已读取；未采集，未修改工程')
            if result.get("preview"):
                pixmap = QPixmap()
                if not pixmap.loadFromData(result["preview"], "JPEG"):
                    raise ValueError("诊断预览图像无效")
                self.previewPixmap = pixmap
                self.scalePreview()
                metadata = result["metadata"]
                acquisition = metadata.get("acquisition", {})
                self.status.setText(f"帧 {acquisition.get('blockId', metadata.get('frameId', '-'))} · 周期 {metadata.get('cycleId', '诊断试调')}\n"
                    f"曝光回读 {acquisition.get('actualExposureUs', '未提供')} us（非逐帧曝光证据）"
                    + (' · 试调前参数已恢复' if acquisition.get('restored') else ''))
                if metadata.get('source') == 'production':
                    from datetime import datetime
                    observed = datetime.fromtimestamp(metadata['observedAtMs'] / 1000).strftime('%H:%M:%S')
                    self.status.setText(self.status.text() + f"\n生产结果 · 任务 {metadata['owner']} · 执行 R{metadata['planRevision']} · {observed}")
                if acquisition.get('actualParameters'):
                    self.actual.setPlainText(json.dumps(acquisition['actualParameters'], ensure_ascii=False, indent=2))
            elif 'actual' not in metadata and self.pendingKind == 'open':
                self.status.setText("诊断会话已就绪；尚未连接或触发相机" +
                    ('' if self.localMatches else '；工程与在线设备不同，观看和采集使用在线配置'))
        except Exception as error:
            self.stopLive()
            self.status.setText(str(error))
            if self.pendingKind == 'watch':
                self.previewTimer.stop()
                self.mode.setText('观看已停止 · 画面为上次结果')
            elif self.pendingKind == 'preview_capture' and 'E_CAMERA_PRODUCTION_ACTIVE' in str(error):
                self.productionActive = True
                self.mode.setText('生产已启动 · 转为观看已有采集结果')
        self.captureButton.setEnabled(bool(self.sessionId) and not self.closed)
        for button in (self.readButton, self.trialButton):
            button.setEnabled(bool(self.sessionId) and not self.closed)
            button.setToolTip('' if self.tuningVersion else '需要支持参数诊断的新版 Runtime')
        self.liveButton.setEnabled(bool(self.sessionId) and bool(self.previewVersion) and not self.closed)
        if self.pendingKind == 'open' and self.previewVersion:
            self.previewTimer.start()
        if not self.previewVersion:
            self.liveButton.setToolTip('Runtime 不支持生产观看与实时预览，请升级 Runtime')
        if self.readOnReady:
            self.readOnReady = False
            self.diagnostic('read_parameters')

    def scalePreview(self):
        if self.previewPixmap is not None:
            self.preview.setPixmap(self.previewPixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scalePreview()

    def dispose(self, _result=0):
        self.closed = True
        self.timer.stop()
        self.previewTimer.stop()
        if self.sessionId:
            self.pool.submit(self.call, "close")
        self.pool.shutdown(wait=False)
