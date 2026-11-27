"""Operator home and next-launch local storage preference, independent of Designer."""
from pathlib import Path

from PySide2.QtCore import QSettings, Qt, Signal
from PySide2.QtGui import QTextOption
from PySide2.QtWidgets import (
    QBoxLayout, QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from .settings import (DEFAULT_ROOT_KEY, LISTENER_SETTINGS_KEY, ListenerSettings, listenerSettingsJson,
                       readListenerSettings, runtimePaths, validateSavedDataRoot)
from .theme import applyOperatorTheme, setStatusTone


def plainLabel(text="", *, role=None):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    if role:
        label.setProperty("operatorRole", role)
    return label


class PathSummary(QPlainTextEdit):
    def __init__(self, text=""):
        super().__init__(text)
        self.setProperty("operatorRole", "path")
        self.setReadOnly(True)
        self.setWordWrapMode(QTextOption.WrapAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def text(self):
        return self.toPlainText()

    def setText(self, text):
        if self.toPlainText() != text:
            self.setPlainText(text)


class StorageSettings(QGroupBox):
    def __init__(self, preferences, resolution, parent=None):
        super().__init__("Runtime 本机目录", parent)
        self.preferences, self.resolution = preferences, resolution
        self.savedDefault = str(preferences.value(DEFAULT_ROOT_KEY, "") or "")
        layout = QVBoxLayout(self)
        self.currentLabel = PathSummary()
        self.currentLabel.setAccessibleName("当前 Runtime 本机目录")
        self.sourceLabel = plainLabel("来源：" + resolution.sourceLabel, role="hint")
        layout.addWidget(self.currentLabel)
        layout.addWidget(self.sourceLabel)
        self.detailsButton = QToolButton()
        self.detailsButton.setText("展开实际目录与覆盖诊断")
        self.detailsButton.setCheckable(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(170)
        self.details.hide()
        self.detailsButton.toggled.connect(self.details.setVisible)
        layout.addWidget(self.detailsButton)
        layout.addWidget(self.details)
        layout.addWidget(plainLabel("下次启动默认目录（只保存本机设置）"))
        self.nextEdit = QLineEdit(self.savedDefault)
        self.nextEdit.setPlaceholderText("留空代表系统默认；点击“使用系统默认”后保存")
        self.nextEdit.setAccessibleName("下次启动默认目录")
        layout.addWidget(self.nextEdit)
        row = QHBoxLayout()
        self.browseButton = QPushButton("选择目录…")
        self.defaultButton = QPushButton("使用系统默认")
        self.saveButton = QPushButton("保存下次设置")
        for button in (self.browseButton, self.defaultButton, self.saveButton):
            row.addWidget(button)
        layout.addLayout(row)
        self.feedback = plainLabel()
        layout.addWidget(self.feedback)
        layout.addWidget(plainLabel("更换目录后，旧在线配置和历史日志仍留在原目录。业务留档位置由工程配置。", role="hint"))
        layout.addStretch()
        self.browseButton.clicked.connect(self.browse)
        self.defaultButton.clicked.connect(self.nextEdit.clear)
        self.saveButton.clicked.connect(self.save)
        self.nextEdit.textChanged.connect(self._draftChanged)
        self.setCurrent(None)
        self._draftChanged()

    def setCurrent(self, controller):
        root = Path(controller.dataRoot) if controller is not None else self.resolution.path
        self.currentLabel.setText(("当前有效目录：\n" if controller is not None else "本次启动目标目录（尚未就绪）：\n") + str(root))
        self.currentLabel.setToolTip(str(root))
        lines = [f"{name}：{path}" for name, path in runtimePaths(root, controller).items()]
        lines.append(f"系统默认：{self.resolution.systemDefault}")
        lines.extend(f"{key}：不用于当前独立入口（数据库/日志路径由该入口显式指定）"
                     for key in self.resolution.ignoredVariables)
        self.details.setPlainText("\n".join(lines))

    def _savedMessage(self):
        target = "本机自定义目录（见输入框）" if self.savedDefault else "系统默认（展开目录诊断查看路径）"
        if self.resolution.overridden:
            return f"已保存：{target}\n仍被{self.resolution.sourceLabel}覆盖；本机默认不会优先生效。本次目录不变。"
        return f"已保存：{target}\n下次启动生效；本次目录不变。"

    def _draftChanged(self):
        unsaved = self.nextEdit.text() != self.savedDefault
        self.feedback.setText("未保存；本次目录不变。" if unsaved else self._savedMessage())
        setStatusTone(self.feedback, "amber" if unsaved else "neutral")

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "下次启动默认目录", self.nextEdit.text() or str(self.resolution.path))
        if path:
            self.nextEdit.setText(path)

    def save(self):
        changed = False
        try:
            raw = self.nextEdit.text()
            value = str(validateSavedDataRoot(raw)) if raw else ""
            # QSettings.status() retains the first error for the object's lifetime.
            # Check each save through a fresh handle at the same storage location.
            preferences = QSettings(self.preferences.fileName(), self.preferences.format())
            if value:
                preferences.setValue(DEFAULT_ROOT_KEY, value)
            else:
                preferences.remove(DEFAULT_ROOT_KEY)
            changed = True
            preferences.sync()
            status = preferences.status()
            if status != QSettings.NoError:
                reason = "无法写入本机设置" if status == QSettings.AccessError else "本机设置格式错误"
                raise OSError(reason + f"：{preferences.fileName()}")
        except (OSError, ValueError) as error:
            if changed:
                # QSettings shares pending values inside the process even after
                # a failed sync. Do not let a new window adopt an unconfirmed value.
                if self.savedDefault:
                    preferences.setValue(DEFAULT_ROOT_KEY, self.savedDefault)
                else:
                    preferences.remove(DEFAULT_ROOT_KEY)
                preferences.sync()
            self.feedback.setText("保存失败：" + str(error))
            setStatusTone(self.feedback, "red")
            return False
        self.savedDefault = value
        self.nextEdit.setText(value)
        self.feedback.setText(self._savedMessage())
        setStatusTone(self.feedback, "green")
        return True


class ListenerSettingsPanel(QGroupBox):
    def __init__(self, preferences, current=None, parent=None):
        super().__init__("Runtime 远程接入", parent)
        self.preferences = preferences
        self.current = current or readListenerSettings(preferences)
        self.saved = readListenerSettings(preferences)
        layout = QVBoxLayout(self)
        self.currentLabel = plainLabel(role="path")
        layout.addWidget(self.currentLabel)
        self.allowLan = QCheckBox("允许其他电脑通过局域网连接 Runtime")
        self.allowLan.setChecked(self.saved.allowLan)
        layout.addWidget(self.allowLan)
        form = QFormLayout()
        self.host = QLineEdit(self.saved.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.saved.port)
        self.subscriptions = QSpinBox()
        self.subscriptions.setRange(1, 16)
        self.subscriptions.setValue(self.saved.displaySubscriptionsPerJob)
        form.addRow("监听 IP", self.host)
        form.addRow("固定端口", self.port)
        form.addRow("每任务展示订阅数", self.subscriptions)
        layout.addLayout(form)
        layout.addWidget(plainLabel("0.0.0.0 监听本机全部 IPv4 地址。连接未加密，仅用于受信任局域网。"
                                   "关闭局域网接入时使用本机动态端口。", role="hint"))
        self.saveButton = QPushButton("保存下次监听设置")
        self.saveButton.clicked.connect(self.save)
        layout.addWidget(self.saveButton)
        self.feedback = plainLabel()
        layout.addWidget(self.feedback)
        self.allowLan.toggled.connect(self._enabled)
        self._enabled()
        self.setCurrent(None)

    def _enabled(self):
        self.host.setEnabled(self.allowLan.isChecked())
        self.port.setEnabled(self.allowLan.isChecked())

    def setCurrent(self, controller):
        server = getattr(controller, 'server', None) if controller is not None else None
        settings = getattr(controller, 'listenerSettings', self.current) if controller is not None else self.current
        from emo_master.clients.runtime.connection import formatTarget
        endpoint = formatTarget(settings.listenHost, server.port if server else settings.listenPort)
        self.currentLabel.setText(('本次已监听：' if server is not None else '本次启动设置：') +
            (endpoint if settings.allowLan or server else '仅本机，按需分配动态端口') +
            f'\n每任务最多 {settings.displaySubscriptionsPerJob} 个展示订阅')

    def save(self):
        previous = self.preferences.value(LISTENER_SETTINGS_KEY, "")
        preferences = QSettings(self.preferences.fileName(), self.preferences.format())
        try:
            settings = ListenerSettings(self.allowLan.isChecked(), self.host.text(), self.port.value(),
                                        self.subscriptions.value())
            preferences.setValue(LISTENER_SETTINGS_KEY, listenerSettingsJson(settings))
            preferences.sync()
            if preferences.status() != QSettings.NoError:
                raise OSError("无法写入本机监听设置：" + preferences.fileName())
        except (ValueError, OSError) as error:
            if previous:
                preferences.setValue(LISTENER_SETTINGS_KEY, previous)
            else:
                preferences.remove(LISTENER_SETTINGS_KEY)
            preferences.sync()
            self.feedback.setText("保存失败：" + str(error))
            setStatusTone(self.feedback, "red")
            return False
        self.saved = settings
        self.feedback.setText("已保存，下次启动生效；本次监听和任务保持运行。启动参数优先于保存设置。")
        setStatusTone(self.feedback, "green")
        return True


class StartupPage(QScrollArea):
    chooseRequested = Signal()
    logsRequested = Signal()
    retryRequested = Signal()

    def __init__(self, preferences, resolution, parent=None, *, listenerSettings=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setObjectName("operatorStartup")
        content = QWidget()
        self.columns = QBoxLayout(QBoxLayout.LeftToRight, content)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setSpacing(12)
        left = QWidget()
        leftLayout = QVBoxLayout(left)
        leftLayout.setContentsMargins(0, 0, 0, 0)
        leftLayout.setSpacing(12)
        project = QGroupBox("工程")
        layout = QVBoxLayout(project)
        self.chooseButton = QPushButton("选择工程…")
        self.chooseButton.setProperty("operatorRole", "primary")
        self.chooseButton.clicked.connect(self.chooseRequested)
        layout.addWidget(self.chooseButton, alignment=Qt.AlignLeft)
        self.recentProject = PathSummary("最近成功工程：\n" + str(preferences.value("projectPath", "") or "暂无"))
        self.recentProject.setAccessibleName("最近成功工程路径")
        layout.addWidget(self.recentProject)
        self.archiveLabel = plainLabel("业务留档：未加载工程。", role="hint")
        layout.addWidget(self.archiveLabel)
        self.failureLabel = plainLabel()
        setStatusTone(self.failureLabel, "red")
        layout.addWidget(self.failureLabel)
        self.retryButton = QPushButton("重试本次初始化")
        self.retryButton.clicked.connect(self.retryRequested)
        self.retryButton.hide()
        layout.addWidget(self.retryButton, alignment=Qt.AlignLeft)
        leftLayout.addWidget(project)
        recent = QGroupBox("最近日志")
        layout = QVBoxLayout(recent)
        self.recentLogs = QPlainTextEdit()
        self.recentLogs.setReadOnly(True)
        self.recentLogs.setMaximumHeight(160)
        self.recentLogs.setPlaceholderText("暂无日志记录")
        layout.addWidget(self.recentLogs)
        self.allLogsButton = QPushButton("查看全部日志")
        self.allLogsButton.clicked.connect(self.logsRequested)
        layout.addWidget(self.allLogsButton, alignment=Qt.AlignLeft)
        leftLayout.addWidget(recent)
        leftLayout.addStretch()
        self.storage = StorageSettings(preferences, resolution)
        self.network = ListenerSettingsPanel(preferences, listenerSettings)
        right = QWidget()
        rightLayout = QVBoxLayout(right)
        rightLayout.setContentsMargins(0, 0, 0, 0)
        rightLayout.setSpacing(12)
        rightLayout.addWidget(self.storage)
        rightLayout.addWidget(self.network)
        rightLayout.addStretch()
        self.columns.addWidget(left, 1)
        self.columns.addWidget(right, 1)
        self.setWidget(content)
        applyOperatorTheme(content, "operatorStartupContent")
        self.setStyleSheet("#operatorStartup { background: transparent; }")

    def resizeEvent(self, event):
        self.columns.setDirection(QBoxLayout.TopToBottom if event.size().width() < 920 else QBoxLayout.LeftToRight)
        super().resizeEvent(event)

    def setCurrent(self, controller):
        self.storage.setCurrent(controller)
        self.network.setCurrent(controller)
        document = controller.document if controller is not None else None
        self.archiveLabel.setText("业务留档：由工程输出/留档配置决定，无统一目录。" if document else "业务留档：未加载工程。")
        if document:
            self.recentProject.setText("最近成功工程：\n" + str(controller.runtime.loadedProjectFile))
