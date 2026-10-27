"""Lightweight launch chooser shown before importing Runtime or the editor."""
from dataclasses import replace
import os

from PySide2.QtCore import QSettings, Qt
from PySide2.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.services.runtime_connection import validateRuntimeTarget
from emo_master.apps.designer.startup_settings import (
    defaultDataRoot, initialStartupOptions, recentValues, saveStartupOptions,
)
from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.operator_runtime.settings import validateSavedDataRoot


INI_FORMAT = QSettings.IniFormat


def _label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return label


class DesignerStartupSettingsDialog(QDialog):
    def __init__(self, preferences=None):
        super().__init__()
        settingsPath = os.environ.get("EMO_DESIGNER_SETTINGS_PATH", "")
        self.preferences = preferences if preferences is not None else (
            QSettings(settingsPath, INI_FORMAT) if settingsPath else QSettings("emo_master", "designer"))
        if settingsPath:
            self.preferences.setFallbacksEnabled(False)
        self.initialOptions = initialStartupOptions(self.preferences)
        self._directoryExplicit = False
        self._selection = None
        self.setWindowTitle("Designer 启动设置")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(480)
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        title = QLabel("EmoMaster")
        title.setObjectName("startupHeroTitle")
        layout.addWidget(title)
        modes = QHBoxLayout()
        self.modeGroup = QButtonGroup(self)
        self.localButton = QPushButton("本机运行")
        self.remoteButton = QPushButton("连接外部 Runtime")
        for index, button in enumerate((self.localButton, self.remoteButton)):
            button.setCheckable(True)
            button.setObjectName("sideCategoryButton")
            self.modeGroup.addButton(button, index)
            modes.addWidget(button, 1)
        self.localButton.setIcon(icon("cpu"))
        self.remoteButton.setIcon(icon("plug"))
        layout.addLayout(modes)
        self.pages = QStackedWidget()
        local = QWidget()
        localLayout = QVBoxLayout(local)
        localLayout.setContentsMargins(0, 0, 0, 0)
        localLayout.addWidget(QLabel("运行数据目录"))
        row = QHBoxLayout()
        self.directoryCombo = QComboBox()
        self.directoryCombo.setEditable(True)
        self.directoryCombo.setInsertPolicy(QComboBox.NoInsert)
        self.directoryCombo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.directoryCombo.setMinimumContentsLength(12)
        self.directoryCombo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.directoryCombo.setAccessibleName("运行数据目录")
        self.directoryCombo.addItems(recentValues(self.preferences, "recentDataRoots"))
        self.directoryCombo.setCurrentText(str(self.initialOptions.dataRoot))
        row.addWidget(self.directoryCombo, 1)
        self.browseButton = QToolButton()
        self.browseButton.setIcon(icon("folder-open"))
        self.browseButton.setToolTip("选择运行数据目录")
        self.browseButton.setAccessibleName("选择运行数据目录")
        self.browseButton.setFixedSize(34, 34)
        row.addWidget(self.browseButton)
        self.defaultButton = QToolButton()
        self.defaultButton.setIcon(icon("refresh-cw"))
        self.defaultButton.setToolTip("使用默认运行目录")
        self.defaultButton.setAccessibleName("使用默认运行目录")
        self.defaultButton.setFixedSize(34, 34)
        row.addWidget(self.defaultButton)
        localLayout.addLayout(row)
        self.pathDetails = QPlainTextEdit()
        self.pathDetails.setReadOnly(True)
        self.pathDetails.setObjectName("startupPaths")
        self.pathDetails.setAccessibleName("实际运行路径")
        self.pathDetails.setMinimumHeight(90)
        self.pathDetails.setMaximumHeight(130)
        localLayout.addWidget(self.pathDetails)
        localLayout.addStretch()
        self.pages.addWidget(local)
        remote = QWidget()
        remoteLayout = QVBoxLayout(remote)
        remoteLayout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.targetCombo = QComboBox()
        self.targetCombo.setEditable(True)
        self.targetCombo.setInsertPolicy(QComboBox.NoInsert)
        self.targetCombo.setAccessibleName("Runtime 地址")
        self.targetCombo.addItems(recentValues(self.preferences, "recentRuntimeTargets"))
        self.targetCombo.setCurrentText(self.initialOptions.runtimeTarget)
        self.targetCombo.lineEdit().setPlaceholderText("127.0.0.1:50051")
        form.addRow("Runtime 地址", self.targetCombo)
        remoteLayout.addLayout(form)
        self.remoteInfo = _label("本机运行目录：不使用\n在线配置与任务：由目标 Runtime 管理")
        self.remoteInfo.setObjectName("mutedText")
        remoteLayout.addWidget(self.remoteInfo)
        remoteLayout.addStretch()
        self.pages.addWidget(remote)
        layout.addWidget(self.pages, 1)
        self.errorLabel = _label()
        self.errorLabel.setObjectName("startupSettingsError")
        self.errorLabel.hide()
        layout.addWidget(self.errorLabel)
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancelButton = QPushButton("取消")
        self.cancelButton.setIcon(icon("x"))
        self.cancelButton.clicked.connect(self.reject)
        self.startButton = QPushButton("启动")
        self.startButton.setObjectName("startupPrimaryAction")
        self.startButton.setIcon(icon("play", "#ffffff"))
        self.startButton.setDefault(True)
        self.startButton.clicked.connect(self.accept)
        footer.addWidget(self.cancelButton)
        footer.addWidget(self.startButton)
        layout.addLayout(footer)
        self.modeGroup.buttonClicked[int].connect(self._modeChanged)
        self.directoryCombo.editTextChanged.connect(self._directoryChanged)
        self.directoryCombo.activated[str].connect(self.selectDirectory)
        self.targetCombo.editTextChanged.connect(self._clearError)
        self.browseButton.clicked.connect(self._browse)
        self.defaultButton.clicked.connect(lambda: self.selectDirectory(str(defaultDataRoot())))
        index = 1 if self.initialOptions.mode == "remote" else 0
        self.modeGroup.button(index).setChecked(True)
        self._modeChanged(index)
        self._updatePaths()

    def _modeChanged(self, index):
        self.pages.setCurrentIndex(index)
        self._clearError()

    def _clearError(self, *_args):
        self.errorLabel.clear()
        self.errorLabel.hide()

    def _directoryChanged(self, _text):
        self._directoryExplicit = True
        self._clearError()
        self._updatePaths()

    def _localOptions(self):
        if not self._directoryExplicit:
            return replace(self.initialOptions, mode="local")
        root = validateSavedDataRoot(self.directoryCombo.currentText())
        return self.initialOptions.withDataRoot(root)

    def _updatePaths(self):
        self.directoryCombo.setToolTip(self.directoryCombo.currentText())
        try:
            options = self._localOptions()
        except (OSError, ValueError):
            self.pathDetails.setPlainText("运行目录尚未有效填写")
            return
        details = f"数据库：{options.dbPath}\n任务工作区：{options.workspaceRoot}\n日志：{options.logDirectory}"
        self.pathDetails.setPlainText(details)
        self.pathDetails.setToolTip(details)

    def selectDirectory(self, path):
        self._directoryExplicit = True
        self.directoryCombo.setCurrentText(path)
        self._updatePaths()
        self._clearError()

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择运行数据目录", self.directoryCombo.currentText())
        if path:
            self.selectDirectory(path)

    def execSelection(self):
        self._selection = None
        self.exec_()
        return self._selection

    def accept(self):
        try:
            if self.pages.currentIndex() == 0:
                options = self._localOptions()
            else:
                options = replace(self.initialOptions, mode="remote",
                                  runtimeTarget=validateRuntimeTarget(self.targetCombo.currentText()))
        except (OSError, ValueError) as error:
            self.setStartupError(error)
            return
        self._selection = options
        super().accept()

    def reject(self):
        self._selection = None
        super().reject()

    def setStartupError(self, error):
        self.errorLabel.setText(str(error))
        self.errorLabel.setToolTip(str(error))
        self.errorLabel.show()

    def rememberSuccessful(self, options):
        try:
            saveStartupOptions(self.preferences, options)
        except OSError as error:
            return "本次启动成功，但未记住启动设置。\n" + str(error)
        return ""
