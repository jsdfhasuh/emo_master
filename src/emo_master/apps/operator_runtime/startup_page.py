"""Operator home and next-launch local storage preference, independent of Designer."""
from pathlib import Path

from PySide2.QtCore import QSettings, Qt, Signal
from PySide2.QtWidgets import (
    QApplication, QBoxLayout, QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from emo_master.clients.runtime.connection import formatTarget
from emo_master.ui.widgets import ElidedLabel
from emo_master.ui.wrap_label import OptionalWrapLabel

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


class StorageSettings(QGroupBox):
    def __init__(self, preferences, resolution, parent=None):
        super().__init__("Runtime 本机目录", parent)
        self.preferences, self.resolution = preferences, resolution
        self.savedDefault = str(preferences.value(DEFAULT_ROOT_KEY, "") or "")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        header = QHBoxLayout()
        self.currentLabel = plainLabel()
        self.sourceLabel = ElidedLabel("来源：" + resolution.sourceLabel)
        self.sourceLabel.setProperty("operatorRole", "hint")
        self.sourceLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(self.currentLabel, 1)
        header.addWidget(self.sourceLabel, 1)
        layout.addLayout(header)
        currentRow = QHBoxLayout()
        self.currentPath = ElidedLabel(mode=Qt.ElideMiddle)
        self.copyCurrentButton = QPushButton("复制路径")
        self.copyCurrentButton.setProperty("operatorRole", "quiet")
        self.copyCurrentButton.setAccessibleName("复制本次 Runtime 目录路径")
        self.copyCurrentButton.clicked.connect(lambda: QApplication.clipboard().setText(self.currentPath.text()))
        currentRow.addWidget(self.currentPath, 1)
        currentRow.addWidget(self.copyCurrentButton)
        layout.addLayout(currentRow)
        self.detailsButton = QToolButton()
        self.detailsButton.setText("展开实际目录与覆盖诊断")
        self.detailsButton.setCheckable(True)
        self.details = QPlainTextEdit()
        self.details.setProperty("operatorRole", "path")
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(170)
        self.details.hide()
        self.detailsButton.toggled.connect(self.details.setVisible)
        layout.addWidget(self.detailsButton, alignment=Qt.AlignLeft)
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
        self.defaultButton.setProperty("operatorRole", "quiet")
        self.saveButton.setProperty("operatorRole", "primary")
        row.addWidget(self.browseButton)
        row.addWidget(self.defaultButton)
        row.addStretch()
        row.addWidget(self.saveButton)
        layout.addLayout(row)
        self.feedback = plainLabel()
        layout.addWidget(self.feedback)
        layout.addWidget(plainLabel("旧在线配置和历史日志仍留在原目录。", role="hint"))
        layout.addStretch()
        self.browseButton.clicked.connect(self.browse)
        self.defaultButton.clicked.connect(self.nextEdit.clear)
        self.saveButton.clicked.connect(self.save)
        self.nextEdit.textChanged.connect(self._draftChanged)
        self.setCurrent(None)
        self._draftChanged()

    def setCurrent(self, controller):
        root = Path(controller.dataRoot) if controller is not None else self.resolution.path
        self.currentLabel.setText("当前有效目录" if controller is not None else "本次启动目标目录（尚未就绪）")
        self.currentPath.setText(str(root))
        self.currentPath.setAccessibleName(str(root))
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
        path = self.nextEdit.text()
        self.nextEdit.setToolTip(path)
        unsaved = path != self.savedDefault
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
    def __init__(self, preferences, current=None, parent=None, *, listenerOverrides=()):
        super().__init__("Runtime 远程接入", parent)
        self.preferences = preferences
        self.saved = readListenerSettings(preferences)
        self.current = current or self.saved
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        self.currentLabel = plainLabel(role="path")
        layout.addWidget(self.currentLabel)
        self.overrideLabel = plainLabel("本次启动参数优先：" + "、".join(listenerOverrides) if listenerOverrides else "")
        layout.addWidget(self.overrideLabel)
        self.overrideLabel.setVisible(bool(listenerOverrides))
        self.savedLabel = plainLabel()
        layout.addWidget(self.savedLabel)
        self.allowLan = QCheckBox("下次启动允许局域网接入")
        self.allowLan.setChecked(self.saved.allowLan)
        layout.addWidget(self.allowLan)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(4)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.host = QLineEdit(self.saved.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.saved.port)
        self.subscriptions = QSpinBox()
        self.subscriptions.setRange(1, 16)
        self.subscriptions.setValue(self.saved.displaySubscriptionsPerJob)
        endpoint = QHBoxLayout()
        endpoint.addWidget(self.host, 1)
        portLabel = QLabel("固定端口")
        portLabel.setBuddy(self.port)
        endpoint.addWidget(portLabel)
        endpoint.addWidget(self.port)
        form.addRow("监听 IP", endpoint)
        form.addRow("每任务展示订阅数", self.subscriptions)
        layout.addLayout(form)
        layout.addWidget(plainLabel("0.0.0.0 监听全部 IPv4。连接未加密，仅用于受信任局域网。", role="hint"))
        self.saveButton = QPushButton("保存下次监听设置")
        self.saveButton.setProperty("operatorRole", "primary")
        self.saveButton.clicked.connect(self.save)
        layout.addWidget(self.saveButton, alignment=Qt.AlignRight)
        self.feedback = OptionalWrapLabel()
        layout.addWidget(self.feedback)
        self.allowLan.toggled.connect(self._draftChanged)
        self.host.textChanged.connect(self._draftChanged)
        self.port.valueChanged.connect(self._draftChanged)
        self.subscriptions.valueChanged.connect(self._draftChanged)
        self._refreshSaved()
        self._draftChanged()
        self.setCurrent(None)

    def _draftValues(self):
        return self.allowLan.isChecked(), self.host.text(), self.port.value(), self.subscriptions.value()

    def _draftChanged(self):
        allowLan = self.allowLan.isChecked()
        self.host.setEnabled(allowLan)
        self.port.setEnabled(allowLan)
        saved = self.saved
        dirty = self._draftValues() != (saved.allowLan, saved.host, saved.port, saved.displaySubscriptionsPerJob)
        self.feedback.setText("未保存；本次监听和任务不变。" if dirty else "")
        setStatusTone(self.feedback, "amber" if dirty else "neutral")

    def _refreshSaved(self):
        settings = self.saved
        fixed = formatTarget(settings.host, settings.port)
        network = f"局域网 · 固定监听 {fixed}" if settings.allowLan else f"仅本机 · 动态端口（固定项 {fixed}）"
        title = "已保存的下次设置" if self.preferences.value(LISTENER_SETTINGS_KEY, "") else "下次设置（默认）"
        self.savedLabel.setText(f"{title}：{network} · 每任务最多 {settings.displaySubscriptionsPerJob} 个展示订阅")

    def setCurrent(self, controller):
        server = getattr(controller, "server", None)
        settings = getattr(controller, "listenerSettings", self.current)
        headline = "本次有效设置"
        if server is not None:
            endpoint = f"已监听 {formatTarget(settings.listenHost, server.port)}"
        elif settings.allowLan:
            endpoint = f"目标监听 {formatTarget(settings.listenHost, settings.listenPort)}"
        else:
            endpoint = "按需分配动态端口"
        if controller is None:
            headline = "本次启动目标（尚未就绪）"
        access = "局域网" if settings.allowLan else "仅本机"
        self.currentLabel.setText(f"{headline}：{access} · {endpoint} · 每任务最多 {settings.displaySubscriptionsPerJob} 个展示订阅")

    def save(self):
        try:
            settings = ListenerSettings(*self._draftValues())
        except ValueError as error:
            self.feedback.setText("保存失败：" + str(error))
            setStatusTone(self.feedback, "red")
            return False
        previous = self.preferences.value(LISTENER_SETTINGS_KEY, "")
        preferences = QSettings(self.preferences.fileName(), self.preferences.format())
        preferences.setValue(LISTENER_SETTINGS_KEY, listenerSettingsJson(settings))
        preferences.sync()
        status = preferences.status()
        if status != QSettings.NoError:
            if previous:
                preferences.setValue(LISTENER_SETTINGS_KEY, previous)
            else:
                preferences.remove(LISTENER_SETTINGS_KEY)
            preferences.sync()
            reason = "无法写入本机监听设置" if status == QSettings.AccessError else "本机监听设置格式错误"
            self.feedback.setText(f"保存失败：{reason}（{preferences.fileName()}）")
            setStatusTone(self.feedback, "red")
            return False
        self.saved = settings
        self.host.setText(settings.host)
        self._refreshSaved()
        self.feedback.setText("已保存，下次启动生效；本次监听和任务保持运行。下次启动参数优先于保存设置。")
        setStatusTone(self.feedback, "green")
        return True


class StartupPage(QScrollArea):
    chooseRequested = Signal()
    recentRequested = Signal(str)
    logsRequested = Signal()
    retryRequested = Signal()

    def __init__(self, preferences, resolution, parent=None, *, listenerSettings=None, listenerOverrides=()):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setProperty("operatorRole", "page")
        contentLayout = QVBoxLayout(content)
        contentLayout.setContentsMargins(0, 0, 0, 0)
        self.heading = plainLabel("打开工程", role="title")
        contentLayout.addWidget(self.heading)
        self.columns = QBoxLayout(QBoxLayout.LeftToRight)
        self.columns.setSpacing(12)
        contentLayout.addLayout(self.columns, 1)

        self.projectGroup = QWidget()
        projectGroups = QVBoxLayout(self.projectGroup)
        projectGroups.setContentsMargins(0, 0, 0, 0)
        projectGroups.setSpacing(12)
        project = QGroupBox("工程")
        layout = QVBoxLayout(project)
        layout.setSpacing(8)
        self.chooseButton = QPushButton("选择工程…")
        self.chooseButton.setProperty("operatorRole", "primary")
        self.chooseButton.clicked.connect(self.chooseRequested)
        layout.addWidget(self.chooseButton, alignment=Qt.AlignLeft)
        self.recentProjects = QVBoxLayout()
        self._recentEntries: tuple[tuple[str, str], ...] = ()
        self.recentOpenButtons = []
        layout.addWidget(plainLabel("最近成功工程", role="section"))
        layout.addLayout(self.recentProjects)
        self.setRecentProjects(())
        self.archiveLabel = plainLabel("业务留档：未加载工程。", role="hint")
        layout.addWidget(self.archiveLabel)
        self.failureLabel = OptionalWrapLabel()
        setStatusTone(self.failureLabel, "red")
        layout.addWidget(self.failureLabel)
        self.retryButton = QPushButton("重试本次初始化")
        self.retryButton.clicked.connect(self.retryRequested)
        self.retryButton.hide()
        layout.addWidget(self.retryButton, alignment=Qt.AlignLeft)
        projectGroups.addWidget(project)

        recent = QGroupBox("最近日志")
        layout = QVBoxLayout(recent)
        layout.setSpacing(8)
        self.recentLogs = QPlainTextEdit()
        self.recentLogs.setReadOnly(True)
        self.recentLogs.setMaximumHeight(160)
        self.recentLogs.setPlaceholderText("暂无日志记录")
        layout.addWidget(self.recentLogs)
        self.allLogsButton = QPushButton("查看全部日志")
        self.allLogsButton.clicked.connect(self.logsRequested)
        layout.addWidget(self.allLogsButton, alignment=Qt.AlignLeft)
        projectGroups.addWidget(recent)
        projectGroups.addStretch()

        self.storage = StorageSettings(preferences, resolution)
        self.network = ListenerSettingsPanel(preferences, listenerSettings, listenerOverrides=listenerOverrides)
        self.settingsGroup = QWidget()
        settings = QVBoxLayout(self.settingsGroup)
        settings.setContentsMargins(0, 0, 0, 0)
        settings.setSpacing(8)
        settings.addWidget(self.storage)
        settings.addWidget(self.network)
        settings.addStretch()
        self.columns.addWidget(self.projectGroup, 1)
        self.columns.addWidget(self.settingsGroup, 1)
        self.setWidget(content)
        applyOperatorTheme(self, "operatorStartup")

    def resizeEvent(self, event):
        self.columns.setDirection(QBoxLayout.TopToBottom if event.size().width() < 920 else QBoxLayout.LeftToRight)
        super().resizeEvent(event)

    @property
    def recentPaths(self):
        return tuple(path for path, _name in self._recentEntries)

    def setRecentProjects(self, entries):
        normalized = []
        for entry in entries:
            path, name = entry if isinstance(entry, tuple) else (entry, "")
            normalized.append((str(path), name or Path(path).stem))
        recent = tuple(normalized)
        if self._recentEntries == recent and self.recentProjects.count():
            return
        self._recentEntries = recent
        self.recentOpenButtons.clear()
        while self.recentProjects.count():
            row = self.recentProjects.takeAt(0).widget()
            row.hide()
            row.deleteLater()
        if not recent:
            self.recentProjects.addWidget(plainLabel("暂无记录"))
        for path, title in recent:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            labels = QVBoxLayout()
            layout.addLayout(labels, 1)
            nameLabel = ElidedLabel(title, mode=Qt.ElideMiddle)
            nameLabel.setAccessibleName(title)
            labels.addWidget(nameLabel)
            pathLabel = ElidedLabel(path, mode=Qt.ElideMiddle)
            pathLabel.setAccessibleName(path)
            labels.addWidget(pathLabel)
            openButton = QPushButton("打开")
            openButton.setAccessibleName(f"打开工程 {title}")
            openButton.setToolTip(path)
            openButton.clicked.connect(lambda _checked=False, path=path: self.recentRequested.emit(path))
            openButton.setEnabled(self.chooseButton.isEnabled())
            layout.addWidget(openButton)
            self.recentOpenButtons.append(openButton)
            copyButton = QPushButton("复制路径")
            copyButton.setAccessibleName(f"复制工程 {title} 的路径")
            copyButton.clicked.connect(lambda _checked=False, path=path: QApplication.clipboard().setText(path))
            layout.addWidget(copyButton)
            self.recentProjects.addWidget(row)

    def setProjectLoadingEnabled(self, enabled):
        self.chooseButton.setEnabled(enabled)
        for button in self.recentOpenButtons:
            button.setEnabled(enabled)

    def setCurrent(self, controller):
        self.storage.setCurrent(controller)
        self.network.setCurrent(controller)
        document = controller.document if controller is not None else None
        self.archiveLabel.setText("业务留档：由工程输出/留档配置决定，无统一目录。" if document else "业务留档：未加载工程。")
        self.heading.setText("Runtime 设置" if document else "打开工程")
        first = self.settingsGroup if document else self.projectGroup
        if self.columns.indexOf(first) != 0:
            self.columns.removeWidget(first)
            self.columns.insertWidget(0, first, 1)
