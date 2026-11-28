"""Operator-only entry with owned Runtime, automatic project loading and shutdown."""
from __future__ import annotations

import argparse
from collections import deque
import json
import multiprocessing
from pathlib import Path
import queue
import sys
import threading
import time

from PySide2.QtCore import QRect, QSettings, QTimer, Qt
from PySide2.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QProgressBar, QPushButton,
    QSizePolicy, QStackedWidget, QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.clients.runtime.history import HistoryClient
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages
from emo_master.ui.presentation.shell import AppearanceButton, configureHostButton
from emo_master.core.presentation.assets import pageAssets
from emo_master.apps.operator_runtime.settings import (
    DEFAULT_ROOT_KEY, LISTENER_SETTINGS_KEY, listenerArgumentOverrides, listenerSettingsJson, readListenerSettings,
    resolveDataRoot,
)
from emo_master.apps.operator_runtime.startup_page import StartupPage
from emo_master.apps.operator_runtime.log_reader import formatLocalTimestamp, safeRecord, safeText
from emo_master.apps.operator_runtime.log_view import LogFeed, RuntimeLogView
from emo_master.apps.operator_runtime.theme import applyOperatorTheme, setStatusTone
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.events.app_log import RuntimeApplicationLogWriter
from emo_panel_protocol.theme import LIGHT_THEME
from emo_master.ui.icons import icon
from emo_master.ui.theme import ACCENT, BACKGROUND, BORDER, MUTED, SURFACE, TEXT, fitWindowToScreen, lightPalette, uiFont
from emo_master.ui.widgets import ElidedLabel


STATE_NAMES = dict(EMPTY="未加载工程", READY="就绪", RUNNING="检测中", STOPPING="停止中", FAULT="故障")


class OperatorWindow(QWidget):
    def __init__(self, projectPath=None, *, dataRoot=None, preferences=None, runtimeFactory=ProductionRuntime,
                 listenerSettings=None, listenerOverrides=(), applicationLogWriter=None, dataRootResolution=None):
        super().__init__()
        self.setWindowTitle("EmoMaster Runtime")
        self.setObjectName("operatorWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            #operatorWindow {{ background: {LIGHT_THEME['page']}; }}
            #operatorBack {{ color: {LIGHT_THEME['text']}; background: {LIGHT_THEME['surface']};
                border: 1px solid {LIGHT_THEME['border']}; border-radius: 5px; padding: 4px 10px; min-height: 20px; }}
            #operatorBack:hover, #operatorBack:focus {{ color: {LIGHT_THEME['primary']};
                border-color: {LIGHT_THEME['primary']}; }}
            #operatorBack:pressed {{ background: {LIGHT_THEME['soft']['blue']}; }}
        """)
        self.resize(1060, 800)
        self.preferences = preferences or QSettings("EmoMaster", "Runtime")
        self.listenerSettings = listenerSettings or readListenerSettings(self.preferences)
        self.resolution = dataRootResolution or resolveDataRoot(dataRoot, saved=self.preferences.value(DEFAULT_ROOT_KEY, ""))
        self.applicationLogWriter = applicationLogWriter
        self._ownsApplicationLogWriter = applicationLogWriter is None
        self.runtimeFactory, self.explicitProject = runtimeFactory, projectPath
        self.projectPath = str(projectPath or self.preferences.value("projectPath", ""))
        self.controller = self.session = self.hub = self.pages = None
        self._historyViewContext = None
        self._historyViewState = {}
        self.sessionCoverage = None
        self.viewSession = self.viewRunInfo = self.viewPresentation = self.viewCoverage = None
        self.viewNotice = ''
        self.workflowSessions, self.workflowHubs, self.workflowPages, self.workflowControls = {}, {}, {}, {}
        self.workflowCoverages = {}
        self.workflowTabs = None
        self.selectedWorkflowIds = []
        self.busy = self.closing = self.done = False
        self.error = ""
        self.state = dict(state="EMPTY", canStart=False, canLoad=False, jobId="", message="")
        self.events = queue.Queue(maxsize=1)
        self.worker = None
        self.operationKind = None
        self.pendingCommand = None
        self.nextStatus = 0.0
        self.communication = self.communicationDialog = None
        self.startupDiagnostics = deque(maxlen=64)
        self.toolHistory = []
        self.pageFocus = {}
        self.initializationFailed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        self._buildShell(layout)
        self.contentStack = QStackedWidget()
        self.businessHost = QWidget()
        self.pageLayout = QVBoxLayout(self.businessHost)
        self.pageLayout.setContentsMargins(0, 0, 0, 0)
        self.startupPage = StartupPage(self.preferences, self.resolution, listenerSettings=self.listenerSettings,
                                       listenerOverrides=listenerOverrides)
        self.startupPage.chooseRequested.connect(self.chooseProject)
        self.startupPage.recentRequested.connect(self.loadProject)
        self.startupPage.setRecentProjects(self._recentProjects())
        self.startupPage.logsRequested.connect(lambda: self.showLogs(resetFilter=True))
        self.startupPage.retryRequested.connect(self.retryInitialization)
        self.logFeed = LogFeed(self.resolution.path / "logs", self)
        self.logView = RuntimeLogView(self.logFeed)
        applyOperatorTheme(self.logView, "operatorLog")
        self.logFeed.updated.connect(self._recentLogs)
        for page in (self.businessHost, self.startupPage, self.logView):
            self.contentStack.addWidget(page)
        self.contentStack.setCurrentWidget(self.startupPage)
        QApplication.instance().focusChanged.connect(self._rememberFocus)
        layout.addWidget(self.contentStack, 1)
        self._restoreWindowPreference()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(25)

        self._initialize()

    def _buildShell(self, layout):
        self.topBar = QWidget()
        self.topBar.setObjectName('operatorTopBar')
        self.topBar.setAttribute(Qt.WA_StyledBackground)
        self.topBar.setFont(uiFont())
        self.topBar.setPalette(lightPalette())
        self.topBar.setStyleSheet(f"""
            QWidget#operatorTopBar {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:8px; }}
            QLabel#operatorState {{ background:{BACKGROUND}; color:{MUTED}; border-radius:5px; padding:4px 7px; }}
            QPushButton[operatorTool="true"] {{ padding:5px 8px; border:1px solid {BORDER}; border-radius:5px; }}
            QPushButton[operatorTool="true"]:enabled:hover {{ background:{BACKGROUND}; border-color:{ACCENT}; }}
            QPushButton[operatorTool="true"]:focus {{ border-color:{ACCENT}; }}
        """)
        self.topBarLayout = QVBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(8, 6, 8, 6)
        self.topBarLayout.setSpacing(5)
        self.topBarRow = QHBoxLayout()
        self.topBarLayout.addLayout(self.topBarRow)
        self.backButton = self._button("返回", QStyle.SP_ArrowBack, self.backFromTool)
        self.backButton.setProperty('operatorTool', True)
        self.backButton.hide()
        self.topBarRow.addWidget(self.backButton)
        self.projectLabel = ElidedLabel("未加载工程")
        self.projectLabel.setFont(uiFont(bold=True))
        self.topBarRow.addWidget(self.projectLabel, 1)
        self.stateLabel = QLabel()
        self.stateLabel.setObjectName('operatorState')
        self.stateLabel.setTextFormat(Qt.PlainText)
        self.topBarRow.addWidget(self.stateLabel)

        self.topBarControlHost = QWidget()
        self.hostControls = QHBoxLayout(self.topBarControlHost)
        self.hostControls.setContentsMargins(0, 0, 0, 0)
        self.startButton = self._button("开始", QStyle.SP_MediaPlay, self.startDetection)
        self.stopButton = self._button("停止", QStyle.SP_MediaStop, self.stopDetection)
        self._controlDock = self.hostControls
        self.hostControls.addWidget(self.startButton)
        self.hostControls.addWidget(self.stopButton)
        self.topBarRow.addWidget(self.topBarControlHost)

        self.topBarActions = QWidget()
        tools = QHBoxLayout(self.topBarActions)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(5)
        self.projectButton = QPushButton(icon('folder-open'), '工程')
        self.projectMenu = QMenu(self.projectButton)
        self.projectButton.setMenu(self.projectMenu)
        self.openAction = self.projectMenu.addAction(icon('folder-open'), '选择工程…', self.chooseProject)
        self.reloadAction = self.projectMenu.addAction(icon('refresh-cw'), '重新加载', self.reloadProject)
        self.updateAction = self.projectMenu.addAction(icon('upload'), '更新工程…', self.choosePackage)
        self.projectMenu.addSeparator()
        self.targetsAction = self.projectMenu.addAction(icon('list'), '运行目标…', self.chooseTargets)
        self.viewRunsAction = self.projectMenu.addAction(icon('logs'), '查看已有运行…', self.viewRun)
        self.communicationButton = QPushButton(icon('cpu'), '设备')
        self.communicationButton.setToolTip('设备状态与控制')
        self.communicationButton.clicked.connect(self.showCommunication)
        self.logsButton = QPushButton(icon('logs'), '日志')
        self.logsButton.clicked.connect(self.showLogs)
        self.settingsButton = QPushButton(icon('sliders-horizontal'), '设置')
        self.settingsButton.setToolTip('Runtime 设置')
        self.settingsButton.clicked.connect(self.showSettings)
        for button in (self.projectButton, self.communicationButton, self.logsButton, self.settingsButton):
            button.setProperty('operatorTool', True)
            tools.addWidget(button)
        self.topBarRow.addWidget(self.topBarActions)
        layout.addWidget(self.topBar)

        self.contextBanner = QWidget()
        self.contextBanner.setObjectName('operatorContextBanner')
        self.contextBanner.setAttribute(Qt.WA_StyledBackground)
        self.contextBanner.setFont(uiFont())
        self.contextBanner.setPalette(lightPalette())
        self.contextBanner.setStyleSheet(f"""
            QWidget#operatorContextBanner {{ background:{BACKGROUND}; border:1px solid {BORDER}; border-radius:8px; }}
            QLabel {{ color:{TEXT}; }}
        """)
        context = QVBoxLayout(self.contextBanner)
        context.setContentsMargins(9, 5, 9, 5)
        context.setSpacing(3)
        feedback = QHBoxLayout()
        self.message = ElidedLabel()
        feedback.addWidget(self.message, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(90)
        self.progress.setAccessibleName('操作进行中')
        feedback.addWidget(self.progress)
        self.copyErrorButton = QPushButton('复制详情')
        self.copyErrorButton.clicked.connect(self._copyErrorDetails)
        feedback.addWidget(self.copyErrorButton)
        context.addLayout(feedback)
        observing = QHBoxLayout()
        self.viewingLabel = QLabel()
        self.viewingLabel.setTextFormat(Qt.PlainText)
        self.viewingLabel.setWordWrap(True)
        self.viewingLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        observing.addWidget(self.viewingLabel, 1)
        self.returnRunButton = self._button('返回当前运行', QStyle.SP_ArrowBack, self.returnToCurrentRun)
        observing.addWidget(self.returnRunButton)
        context.addLayout(observing)
        self.contextBanner.hide()
        layout.addWidget(self.contextBanner)

    def _fitTopBar(self):
        margins = self.topBarLayout.contentsMargins()
        buttons = (self.backButton, self.stateLabel, self.topBarControlHost, self.topBarActions)
        used = sum(widget.sizeHint().width() + self.topBarRow.spacing() for widget in buttons if not widget.isHidden())
        twoRows = used + margins.left() + margins.right() + 120 > self.topBar.width()
        if twoRows and self.topBarRow.indexOf(self.topBarActions) >= 0:
            self.topBarRow.removeWidget(self.topBarActions)
            self.topBarLayout.addWidget(self.topBarActions, 0, Qt.AlignRight)
        elif not twoRows and self.topBarRow.indexOf(self.topBarActions) < 0:
            self.topBarLayout.removeWidget(self.topBarActions)
            self.topBarRow.addWidget(self.topBarActions)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fitTopBar()

    def _restoreWindowPreference(self):
        geometry = self.preferences.value('window/geometry')
        screen = None
        if isinstance(geometry, QRect) and geometry.isValid():
            self.setGeometry(geometry)
            screen = QApplication.screenAt(geometry.center())
        self.winId()  # Measure the actual native window frame before clamping.
        if screen is None or not screen.availableGeometry().contains(self.frameGeometry()):
            fitWindowToScreen(self, screen=screen)
        if self.preferences.value('window/maximized', False, type=bool):
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

    def _recentProjects(self):
        try:
            entries = json.loads(self.preferences.value('recentProjects', '', type=str))
        except ValueError:
            return []
        if not isinstance(entries, list):
            return []
        projects = []
        for entry in entries[:5]:
            if isinstance(entry, str) and entry:
                projects.append((entry, Path(entry).stem))
            elif isinstance(entry, dict) and isinstance(entry.get('path'), str) and entry['path']:
                projects.append((entry['path'], str(entry.get('name') or Path(entry['path']).stem)))
        return projects

    def _recordRecentProject(self, path, name):
        if not path:
            return
        key = Path(path)
        projects = [(path, name), *(project for project in self._recentProjects() if Path(project[0]) != key)][:5]
        self.preferences.setValue('recentProjects', json.dumps([dict(path=path, name=name) for path, name in projects],
                                                               ensure_ascii=False))
        self.startupPage.setRecentProjects(projects)

    def _initialize(self):
        if self.applicationLogWriter is None:
            self.applicationLogWriter = RuntimeApplicationLogWriter(self.resolution.path / "logs")
        self.logFeed.writer = self.applicationLogWriter
        self.logFeed.applicationWriter = self.applicationLogWriter
        self.initializationFailed = False
        self.startupPage.retryButton.hide()
        self.startupPage.failureLabel.setText("")
        self.error = ""
        self.message.setText("正在准备 Runtime")
        self._diagnostic("operator.storage.resolved", "本次启动目标目录：" + str(self.resolution.path),
                         payload={"source": self.resolution.source, "dataRoot": str(self.resolution.path)})

        def initialize():
            self.controller = (self.runtimeFactory(self.resolution.path, listenerSettings=self.listenerSettings,
                runtimeInstanceId=self.applicationLogWriter.runtimeInstanceId)
                if self.runtimeFactory is ProductionRuntime else self.runtimeFactory(self.resolution.path))
            if self.explicitProject:
                self.controller.load(self.projectPath)
            elif self.projectPath or self.controller.runtime.projectConfiguration.current is not None:
                self.controller.load(self.projectPath, restoreOnline=True)
            return self.controller.status()
        self.launch("loaded", initialize)

    def retryInitialization(self):
        if self.busy or self.closing or self.controller is not None:
            return
        # Retry the same startup target, never a saved-but-not-yet-effective draft.
        self.closeApplicationLog()
        self._readyRecorded = False
        self._initialize()

    def closeApplicationLog(self):
        writer, self.applicationLogWriter = self.applicationLogWriter, None
        if writer is not None:
            writer.close()

    def _diagnostic(self, eventType, message, *, level="INFO", payload=None):
        event = RuntimeEvent(jobId="", eventType=eventType, message=safeText(message), level=level,
            timestampMs=int(time.time() * 1000), payloadJson=json.dumps(safeRecord(payload or {}), ensure_ascii=False))
        writer = self.applicationLogWriter
        accepted = bool(writer and writer.record(eventType, event.message, level=level, payload=safeRecord(payload or {})))
        self.startupDiagnostics.append((event, accepted))
        self._diagnosticText()

    def _diagnosticText(self):
        writer = self.applicationLogWriter
        self.logFeed.diagnostics = [
            formatLocalTimestamp({'timestampMs': event.timestampMs}) + " · " + event.level + " · " + event.message
            + ("（已提交 writer；落盘以实际文件为准）" if accepted and writer and writer.enabled and not writer.failureMessage
               else "（未写入日志文件 / writer 未确认写入）")
            + (" · 日志目录：" + str(writer.directory) if writer else "")
            for event, accepted in self.startupDiagnostics]
        self.logFeed.updated.emit()

    def _runtimeReady(self):
        self.logFeed.setSource(self.controller)
        self.logView.setRuntime(self.controller)
        self.startupPage.setCurrent(self.controller)
        if not getattr(self, "_readyRecorded", False):
            self._readyRecorded = True
            self._diagnostic("operator.storage.resolved", "当前有效目录：" + str(self.controller.dataRoot),
                             payload={"source": self.resolution.source})
            self._diagnostic("operator.runtime.ready", "Runtime 已就绪")

    def _recentLogs(self):
        rows = self.logFeed.records[-5:]
        text = "\n".join(f"{formatLocalTimestamp(row.data)} · {row.data.get('level', '')} · {row.data.get('message', '')}"
                         for row in rows)
        if text and self.logFeed.historyPath:
            text = self.logFeed.sourceText(fullPath=False) + "\n" + text
        if not text:
            text = self.logFeed.statusText()
        if self.startupPage.recentLogs.toPlainText() != text:
            self.startupPage.recentLogs.setPlainText(text)

    def _syncTool(self):
        widget = self.contentStack.currentWidget()
        self.backButton.setVisible(bool(self.toolHistory))
        backTarget = ("返回运行页面" if self.toolHistory and self.toolHistory[-1] is self.businessHost else
                      "返回设置或起始页" if self.toolHistory and self.toolHistory[-1] is self.startupPage else "返回日志")
        self.backButton.setToolTip(backTarget)
        self.backButton.setAccessibleName(backTarget)
        self.logFeed.setVisible(self.isVisible() and widget in (self.startupPage, self.logView))
        self.logView.setToolVisible(self.isVisible() and widget is self.logView)
        self._syncHostControlDock()

    def _showTool(self, widget):
        current = self.contentStack.currentWidget()
        if current is not widget:
            if widget in self.toolHistory:
                self.toolHistory = self.toolHistory[:self.toolHistory.index(widget)]
            else:
                self.toolHistory.append(current)
            self._switchContent(widget)
        self._syncTool()

    def _rememberFocus(self, old, new):
        for focused in (old, new):
            if focused is not None:
                for page in (self.businessHost, self.startupPage, self.logView):
                    if page.isAncestorOf(focused):
                        self.pageFocus[page] = focused
                        break
                else:
                    if (self.contentStack.currentWidget() is self.businessHost
                            and focused is not self.backButton and self.topBar.isAncestorOf(focused)):
                        self.pageFocus[self.businessHost] = focused

    def _switchContent(self, widget):
        import shiboken2
        focused = self.pageFocus.get(widget)
        self.contentStack.setCurrentWidget(widget)
        self._syncHostControlDock()
        if focused is not None and shiboken2.isValid(focused) and focused.isVisible() and focused.isEnabled():
            focused.setFocus(Qt.OtherFocusReason)

    def showSettings(self):
        if self.controller is not None and getattr(self, '_readyRecorded', False):
            self.startupPage.network.setCurrent(self.controller)
        self._showTool(self.startupPage)

    def showLogs(self, checked=False, *, resetFilter=False, deviceId=None, deviceName=''):
        if deviceId is not None:
            self.logView.setDevice(deviceId, deviceName)
        elif resetFilter:
            self.logView.resetFilter()
        else:
            self.logView.sources.setCurrentIndex(0)
        self._showTool(self.logView)

    def backFromTool(self):
        if self.toolHistory:
            self._switchContent(self.toolHistory.pop())
        self._syncTool()

    def showEvent(self, event):
        super().showEvent(event)
        self._syncTool()

    def hideEvent(self, event):
        self.logFeed.setVisible(False)
        self.logView.setToolVisible(False)
        super().hideEvent(event)

    def _button(self, text, icon, action):
        button = (AppearanceButton if icon in (QStyle.SP_MediaPlay, QStyle.SP_MediaStop) else QPushButton)(text)
        if not isinstance(button, AppearanceButton):
            button.setProperty("operatorShellAction", True)
        button.setIcon(self.style().standardIcon(icon))
        button.setToolTip(text)
        button.clicked.connect(action)
        return button

    def _ensureCommunication(self):
        if self.communicationDialog is not None or not self.controller:
            return
        from emo_master.clients.runtime.devices import DeviceManagementClient
        from emo_master.ui.device_management import DeviceManagementDialog
        self.communicationDialog = DeviceManagementDialog(DeviceManagementClient(self.controller.runtime),
            lambda: self.controller.document.project.projectId if self.controller.document else '', self)
        applyOperatorTheme(self.communicationDialog, "operatorDevices")
        self.communication = self.communicationDialog

    def showCommunication(self):
        self._ensureCommunication()
        if self.communicationDialog is not None:
            self.communicationDialog.show()
            self.communicationDialog.raise_()

    def launch(self, kind, operation):
        if self.busy:
            return
        self.busy = True
        self.operationKind = kind
        if kind != "status":
            self._controls()
        def work():
            try:
                value = operation()
                self.events.put((kind, value, ""))
            except Exception as error:
                self.events.put((kind, None, str(error)))
        self.worker = threading.Thread(target=work, name="operator-runtime-operation")
        self.worker.start()

    def _deferCommand(self, command):
        if not self.busy:
            return False
        if self.operationKind == "status" and not self.closing:
            self.pendingCommand = command
        return True

    def chooseProject(self):
        if self.closing or self._deferCommand(self.chooseProject) or not self.state["canLoad"]:
            return
        from emo_master.core.project.files import PROJECT_OPEN_FILTER
        path, _filter = QFileDialog.getOpenFileName(self, "打开项目", self.projectPath, PROJECT_OPEN_FILTER)
        if path:
            self.loadProject(path)

    def reloadProject(self):
        if self.projectPath:
            self.loadProject(self.projectPath)

    def choosePackage(self):
        if self.closing or self._deferCommand(self.choosePackage) or not self.state["canLoad"]:
            return
        path, _filter = QFileDialog.getOpenFileName(self, "更新工程", "", "Runtime project (*.vxpkg)")
        if path:
            self.updateProject(path)

    def updateProject(self, path):
        if self.closing or self._deferCommand(lambda: self.updateProject(path)) or not self.state["canLoad"]:
            return
        self._removePages()
        def update():
            self._closeSession()
            self.controller.installPackage(path)
            return self.controller.status()
        self.message.setText("正在更新工程")
        self.launch("loaded", update)

    def loadProject(self, path):
        if self.closing or self._deferCommand(lambda: self.loadProject(path)) or not self.state["canLoad"]:
            return
        self._removePages()
        def load():
            self._closeSession()
            self.controller.load(path)
            return self.controller.status()
        self.message.setText("正在加载工程")
        self.launch("loaded", load)

    def startDetection(self):
        if (self.closing or self._deferCommand(self.startDetection) or self.controller is None
                or not self.controller.document or not self._startableWorkflows(self.selectedWorkflowIds)):
            return
        if (len(self.controller.sessions) > 1 or len(self.selectedWorkflowIds) > 1
                or self.selectedWorkflowIds != [self.controller.document.entryWorkflowId]):
            return self.startWorkflows()
        if self.pages is None or self.pages.config.pageOrder:
            self._removePages()
        self._updateBasicPages(operation="starting")
        self.error = ""
        self.message.setText("正在启动")
        def start():
            self._closeSession()
            self.controller.start()
            document = self.controller.document
            if document.presentation and document.presentation.pageOrder:
                self.session = DisplaySession(self.controller.connectionProfile, self.controller.jobId,
                    imageDemand=True, expectedRuntimeInstanceId=self.controller.runtime.runtimeInstanceId,
                    projectId=document.project.projectId)
                self.sessionCoverage = self._captureCoverage(self.controller.jobId)
            return self.controller.status()
        self.launch("started", start)

    def stopDetection(self):
        if (self.closing or self._deferCommand(self.stopDetection)
                or self.state["state"] not in {"RUNNING", "STOPPING", "FAULT"}):
            return
        if (len(self.controller.sessions) > 1
                or next(iter(self.controller.sessions), None) != self.controller.document.entryWorkflowId):
            return self.stopWorkflows()
        target = self._stopChoice(self.controller.document.entryWorkflowId)
        if not target:
            return
        self.message.setText("正在停止")
        self._updateBasicPages(operation="stopping")
        def stop():
            self.controller.stop(jobId=target)
            return self.controller.status()
        self.launch("stopped", stop)

    def _stopChoice(self, workflowId):
        row = self.state.get('workflows', {}).get(workflowId, self.state)
        runs = [run for run in row.get('runs', []) if run['active']]
        if len(runs) > 1:
            labels = [f"{run['jobId']} · {run['state']}" for run in runs]
            selected = next((i for i, run in enumerate(runs) if run['jobId'] == row.get('jobId')), 0)
            value, ok = QInputDialog.getItem(self, '停止运行', '选择要停止的运行', labels, selected, False)
            return runs[labels.index(value)]['jobId'] if ok else ''
        return runs[0]['jobId'] if runs else row.get('jobId', '')

    def viewRun(self):
        if self.closing or self._deferCommand(self.viewRun) or not self.controller.document:
            return
        runs = self.state.get('runCatalog')
        if runs is None:
            runs = [dict(run, workflowId=key) for key, row in self.state.get('workflows', {}).items() for run in row.get('runs', [])]
        if not runs:
            self.viewNotice = '当前工程尚无运行记录；历史数据可直接在页面中查询'
            self._controls()
            return
        names = {key: workflow.name for key, workflow in self.controller.workflows.items()}
        labels = [f"{names.get(run['workflowId'], run['workflowId'])} · {run['jobId']} · {run['state']}"
                  + (f" · 槽位 {run['targetSlotId']}" if run.get('targetSlotId') else '') for run in runs]
        value, ok = QInputDialog.getItem(self, '查看已有运行', '选择运行记录（查看不改变停止目标）', labels, 0, False)
        if not ok:
            return
        selected = runs[labels.index(value)]
        workflowId = selected['workflowId']
        jobId = selected['jobId']
        self._removePages()
        def attach():
            from emo_master.apps.runtime.jobs.read_model import runState
            from emo_master.apps.runtime.presentation.rpc import DisplayRpc
            from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
            from emo_master.clients.runtime.dispatch_display import DispatchDisplaySession
            from emo_master.core.presentation.coverage import CaptureCoverage
            from emo_master.core.presentation.workflow_view import dispatchPair, presentationForWorkflows
            self._closeViewedSession()
            current = runState(self.controller.runtime, jobId)
            if current is None or current.project_id != self.controller.document.project.projectId:
                raise ValueError('此运行已不可用，请刷新后重新选择')
            metadata = list(DisplayRpc(self.controller.presentation).ListJobs(
                pb.DisplayEmpty(project_id=current.project_id), None).jobs)
            selectedMetadata = next((item for item in metadata if item.job_id == jobId), None)
            if selectedMetadata is None:
                raise ValueError('此运行记录已不可用，请刷新后重新选择')
            pair = dispatchPair(self.controller.document, selectedMetadata, metadata)
            roots = tuple(item.workflow_id for item in pair) if pair else (workflowId,)
            presentation = presentationForWorkflows(self.controller.document, roots)
            if pair:
                session = DispatchDisplaySession(self.controller.connectionProfile, *pair, presentation)
                coverage = session.coverage
            else:
                coverage = CaptureCoverage.fromMetadata(selectedMetadata)
                session = DisplaySession(self.controller.connectionProfile, jobId, imageDemand=True,
                    expectedRuntimeInstanceId=current.runtime_instance_id, projectId=current.project_id,
                    readResults=current.capture_enabled and not current.display_resources_released)
            self.viewSession, self.viewCoverage, self.viewPresentation = session, coverage, presentation
            self.viewRunInfo = dict(selected, roots=roots)
            return self.controller.status()
        self.message.setText('正在打开运行记录')
        self.launch('viewed', attach)

    def returnToCurrentRun(self):
        if self.closing or self._deferCommand(self.returnToCurrentRun) or self.viewSession is None:
            return
        self._removePages()
        def detach():
            self._closeViewedSession()
            return self.controller.status()
        self.message.setText('正在返回当前运行')
        self.launch('viewed', detach)

    def chooseTargets(self):
        if self.closing or self._deferCommand(self.chooseTargets) or not self.controller.document:
            return
        from emo_master.ui.run_targets_dialog import RunTargetsDialog
        dialog = RunTargetsDialog(self.controller.workflows, self.selectedWorkflowIds,
            self.controller.document.runtime.maxConcurrentJobs, self, editLimit=False,
            entryWorkflowId=self.controller.document.entryWorkflowId,
            projectTargets=self.controller.document.startupWorkflows(), document=self.controller.document)
        if dialog.exec_():
            self.selectedWorkflowIds = dialog.selectedWorkflowIds()
            self._controls()

    def startWorkflows(self, workflowId=None):
        if self.closing or self._deferCommand(lambda: self.startWorkflows(workflowId)):
            return
        keys = self._startableWorkflows([workflowId] if workflowId else self.selectedWorkflowIds)
        if not keys:
            return
        for key in keys:
            pages = self.workflowPages.get(key)
            if pages is None or pages.config.pageOrder:
                self._removeWorkflowPages(key)
        self._updateBasicPages(operation="starting", workflowIds=keys)
        self.error = ""
        def start():
            for key in keys:
                self._closeWorkflowSession(key)
            if workflowId is None:
                results = self.controller.startAll(keys)
            else:
                try:
                    results = {workflowId: dict(ok=True, jobId=self.controller.start(workflowId))}
                except Exception as error:
                    results = {workflowId: dict(ok=False, message=str(error))}
            for key, owned in self.controller.sessions.items():
                if key not in keys or not owned.jobId:
                    continue
                presentation = self.controller.presentationForWorkflow(key)
                if presentation and presentation.pageOrder and owned.jobId in self.controller.presentation.jobs:
                    self.workflowSessions[key] = DisplaySession(self.controller.connectionProfile, owned.jobId,
                        imageDemand=True, expectedRuntimeInstanceId=self.controller.runtime.runtimeInstanceId,
                        projectId=self.controller.document.project.projectId)
                    self.workflowCoverages[key] = self._captureCoverage(owned.jobId)
            state = self.controller.status()
            state["operationErrors"] = [f"{self.controller.workflows[key].name}: {row['message']}"
                for key, row in results.items() if not row["ok"]]
            return state
        self.message.setText("正在启动流程")
        self.launch("workflowsStarted", start)

    def stopWorkflows(self, workflowId=None):
        if self.closing or self._deferCommand(lambda: self.stopWorkflows(workflowId)):
            return
        target = self._stopChoice(workflowId) if workflowId is not None else None
        if workflowId is not None and not target:
            return
        def stop():
            if workflowId is None:
                results = self.controller.stopAll()
            else:
                try:
                    self.controller.stop(workflowId, jobId=target)
                    results = {workflowId: dict(ok=True)}
                except Exception as error:
                    results = {workflowId: dict(ok=False, message=str(error))}
            state = self.controller.status()
            state["operationErrors"] = [f"{self.controller.workflows[key].name if key in self.controller.workflows else key}: {row['message']}"
                for key, row in results.items() if not row["ok"]]
            return state
        self.error = ""
        self.message.setText("正在停止流程")
        self._updateBasicPages(operation="stopping",
                               workflowIds=[workflowId] if workflowId else None)
        self.launch("workflowsStopped", stop)

    def _removePages(self):
        for key in list(self.workflowPages):
            self._removeWorkflowPages(key)
        if self.workflowTabs:
            self.pageLayout.removeWidget(self.workflowTabs)
            self.workflowTabs.deleteLater()
            self.workflowTabs = None
            self.workflowControls.clear()
        if self.pages:
            self._saveHistoryState(self.pages)
            self._syncHostControlDock(detaching=True)
            self.pageLayout.removeWidget(self.pages)
            self.pages.close()
            self.pages.deleteLater()
            self.pages = None
        if self.hub:
            self.hub.deleteLater()
            self.hub = None

    def _historyStateFor(self, pages):
        context = pages.historyClient.context
        scope = (context.runtime_instance_id, context.project_id, context.history_definition_revision)
        if scope != self._historyViewContext:
            self._historyViewContext = scope
            self._historyViewState.clear()
        return self._historyViewState

    def _historyStateKey(self, pages, workflowId):
        if pages.hub is not None:
            if pages.hub.session is self.viewSession:
                return ('job', self.viewRunInfo['jobId'])
            return ('job', pages.hub.session.jobId)
        return ('workflow', workflowId or self.controller.document.entryWorkflowId)

    def _saveHistoryState(self, pages, workflowId=None):
        self._historyStateFor(pages)[self._historyStateKey(pages, workflowId)] = pages.historyReviewState()

    def _restoreHistoryState(self, pages, workflowId=None):
        state = self._historyStateFor(pages).get(self._historyStateKey(pages, workflowId))
        if state is not None:
            pages.restoreHistoryReviewState(state)

    def _showPages(self):
        document = self.controller.document
        if document is None:
            return
        if self.viewSession is not None:
            self._removePages()
            self.hub = DisplayHub(self.viewSession, self)
            self.pages = RuntimePages(self.viewPresentation, hub=self.hub, label='查看已有运行', parent=self,
                assets=pageAssets(document), projectRoot=self.controller.runtime.loadedProjectPath,
                historyClient=HistoryClient(lambda name, request: getattr(self.controller.runtime, name)(request, None),
                    runtimeInstanceId=self.controller.runtime.runtimeInstanceId, projectId=document.project.projectId,
                    presentation=document.presentation))
            self.pages.setCaptureCoverage(self.viewCoverage)
            self._restoreHistoryState(self.pages)
            self.pages.banner.hide()
            self.pageLayout.addWidget(self.pages)
            self.pages.show()
            self._businessReady()
            return
        if (len(self.controller.sessions) > 1
                or next(iter(self.controller.sessions), None) != document.entryWorkflowId):
            self._showWorkflowPages()
            self._businessReady()
            return
        self._removePages()
        if self.session:
            self.hub = DisplayHub(self.session, self)
        self.pages = RuntimePages(self.controller.presentationForWorkflow(document.entryWorkflowId),
            hub=self.hub, label=document.project.name, parent=self, assets=pageAssets(document),
            historyClient=HistoryClient(lambda name, request: getattr(self.controller.runtime, name)(request, None),
                runtimeInstanceId=self.controller.runtime.runtimeInstanceId, projectId=document.project.projectId,
                presentation=document.presentation),
            projectRoot=getattr(getattr(self.controller, 'runtime', None), 'loadedProjectPath', None))
        self.pages.setCaptureCoverage(self.sessionCoverage)
        self._restoreHistoryState(self.pages)
        self.pages.banner.hide()
        self.pageLayout.addWidget(self.pages)
        self.pages.show()
        self._businessReady()

    def _businessReady(self):
        if not self.toolHistory:
            self.contentStack.setCurrentWidget(self.businessHost)
        elif self.businessHost not in self.toolHistory:
            # A tool opened during initial Load must retain a route to the
            # business page that became available while the tool was visible.
            self.toolHistory.insert(0, self.businessHost)
        self._syncTool()

    def _removeWorkflowPages(self, key):
        pages = self.workflowPages.pop(key, None)
        if pages:
            self._saveHistoryState(pages, key)
            self.workflowControls[key]["layout"].removeWidget(pages)
            pages.close()
            pages.deleteLater()
        hub = self.workflowHubs.pop(key, None)
        if hub:
            hub.deleteLater()

    def _showWorkflowPages(self):
        if self.workflowTabs is None:
            if self.session:
                self.workflowSessions[self.controller.document.entryWorkflowId] = self.session
                self.workflowCoverages[self.controller.document.entryWorkflowId] = self.sessionCoverage
                self.session = None
                self.sessionCoverage = None
            if self.pages:
                self._saveHistoryState(self.pages)
                self._syncHostControlDock(detaching=True)
                self.pageLayout.removeWidget(self.pages)
                self.pages.close()
                self.pages.deleteLater()
                self.pages = None
            if self.hub:
                self.hub.deleteLater()
                self.hub = None
            self.workflowTabs = QTabWidget(self)
            self.pageLayout.addWidget(self.workflowTabs)
        for key in self.controller.sessions:
            if key not in self.workflowControls:
                workflow = self.controller.workflows[key]
                tab = QWidget()
                layout = QVBoxLayout(tab)
                toolbar = QHBoxLayout()
                label = QLabel()
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                toolbar.addWidget(label, 1)
                start = self._button("开始流程", QStyle.SP_MediaPlay, lambda checked=False, key=key: self.startWorkflows(key))
                stop = self._button("停止流程", QStyle.SP_MediaStop, lambda checked=False, key=key: self.stopWorkflows(key))
                toolbar.addWidget(start)
                toolbar.addWidget(stop)
                layout.addLayout(toolbar)
                self.workflowControls[key] = dict(layout=layout, label=label, start=start, stop=stop)
                self.workflowTabs.addTab(tab, workflow.name)
        for key in self.controller.sessions:
            workflow = self.controller.workflows[key]
            if key in self.workflowPages:
                continue  # Restarting the other workflow must not replace this view/hub/pins.
            session = self.workflowSessions.get(key)
            hub = DisplayHub(session, self) if session else None
            if hub:
                self.workflowHubs[key] = hub
            pages = RuntimePages(self.controller.presentationForWorkflow(key), hub=hub,
                                 label=workflow.name, parent=self.workflowTabs, assets=pageAssets(self.controller.document),
                                 historyClient=HistoryClient(lambda name, request: getattr(self.controller.runtime, name)(request, None),
                                     runtimeInstanceId=self.controller.runtime.runtimeInstanceId,
                                     projectId=self.controller.document.project.projectId,
                                     presentation=self.controller.document.presentation),
                                 projectRoot=getattr(getattr(self.controller, 'runtime', None), 'loadedProjectPath', None))
            pages.setCaptureCoverage(self.workflowCoverages.get(key))
            self._restoreHistoryState(pages, key)
            pages.banner.hide()
            self.workflowPages[key] = pages
            self.workflowControls[key]["layout"].addWidget(pages)
            pages.show()

    def _closeSession(self):
        self._closeViewedSession()
        for key in list(self.workflowSessions):
            self._closeWorkflowSession(key)
        if self.session:
            self.session.close()
            self.session = None
        self.sessionCoverage = None

    def _captureCoverage(self, jobId):
        from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
        from emo_master.apps.runtime.presentation.rpc import DisplayRpc
        from emo_master.core.presentation.coverage import CaptureCoverage
        jobs = DisplayRpc(self.controller.presentation).ListJobs(
            pb.DisplayEmpty(project_id=self.controller.document.project.projectId), None).jobs
        return CaptureCoverage.fromMetadata(next(job for job in jobs if job.job_id == jobId))

    def _refreshPublishedSessions(self):
        from emo_master.apps.runtime.jobs.read_model import runState
        from emo_master.core.presentation.workflow_view import presentationForWorkflows

        state = self.controller.status()
        # A publication changes the page, not the user's viewing or stop
        # selection. Keep active subscriptions and their frozen coverage.
        # Completed default results are retired as before; an explicit history
        # selection remains attached to its exact Job (or Dispatch pair).
        if self.viewSession is not None:
            roots = self.viewRunInfo['roots']
            if any(key not in self.controller.workflows for key in roots):
                self._closeViewedSession()
                self.viewNotice = '正在查看的流程已从工程删除；已返回当前运行页面'
            else:
                self.viewPresentation = presentationForWorkflows(self.controller.document, roots)
        if self.session is not None:
            run = runState(self.controller.runtime, self.session.jobId)
            if run is None or not run.active:
                self.session.close()
                self.session = self.sessionCoverage = None
        for key, session in list(self.workflowSessions.items()):
            run = runState(self.controller.runtime, session.jobId)
            if run is None or not run.active:
                self._closeWorkflowSession(key)
        return state

    def _closeViewedSession(self):
        if self.viewSession is not None:
            self.viewSession.close()
        self.viewSession = self.viewRunInfo = self.viewPresentation = self.viewCoverage = None
        self.viewNotice = ''

    def _closeWorkflowSession(self, key):
        session = self.workflowSessions.get(key)
        if session:
            session.close()
            del self.workflowSessions[key]
        self.workflowCoverages.pop(key, None)

    def _startableWorkflows(self, workflowIds):
        availability = self.state.get('workflowStart', {})
        return [key for key in workflowIds if availability.get(key, {}).get('canStart')]

    def _startBlockText(self, workflowIds):
        availability = self.state.get('workflowStart', {})
        reasons = dict.fromkeys(availability.get(key, {}).get('startBlockReason', '') for key in workflowIds)
        labels = {'E_MAX_CONCURRENT_JOBS': '并发运行名额不足', 'E_CONFIG_NOT_READY': 'Runtime 工程配置尚未就绪',
                  'E_PROJECT_NOT_LOADED': 'Runtime 尚未加载工程', 'E_RUNTIME_CLOSING': 'Runtime 正在关闭'}
        return '；'.join(f'{labels.get(reason, "Runtime 暂不可开始")}（{reason}）' for reason in reasons if reason)

    def _selectedStartBlock(self):
        if self._startableWorkflows(self.selectedWorkflowIds):
            return ''
        return self._startBlockText(self.selectedWorkflowIds)

    def _syncHostControlDock(self, *, detaching=False):
        document = self.controller.document if self.controller is not None else None
        shell = document.presentation.shell if document is not None and document.presentation else None
        business = self.contentStack.currentWidget() is self.businessHost
        dock = (self.pages.hostControls if not detaching and business and self.pages is not None
                and self.pages.config.pageOrder else self.hostControls)
        if dock is not self._controlDock:
            for button in (self.startButton, self.stopButton):
                self._controlDock.removeWidget(button)
                dock.addWidget(button)
            self._controlDock = dock

        rows = self.state.get('workflows', {})
        multi = len(rows) > 1
        compact = not business or multi
        compactChanged = compact != getattr(self, '_compactControls', None)
        if compactChanged:
            for button in (self.startButton, self.stopButton):
                button.setStyleSheet('')
                button.setMinimumSize(0, 0)
                button.setMaximumSize(16777215, 16777215)
                button.setProperty('pageControlAppearance', None)
                button.setProperty('pageControlIcon', None)
            self._compactControls = compact

        targets = '、'.join(self.controller.workflows[key].name for key in self.selectedWorkflowIds
                          if self.controller is not None and key in self.controller.workflows)
        controls = shell if not compact else None
        configureHostButton(self.startButton, controls.startControl if controls else None, kind='start',
            fallbackLabel=f"开始（{len(self.selectedWorkflowIds)} 个流程）" if len(self.selectedWorkflowIds) > 1 else '开始',
            tooltip=self._selectedStartBlock() or (f'启动已选流程：{targets}' if targets else '尚未选择启动流程'))
        configureHostButton(self.stopButton, controls.stopControl if controls else None, kind='stop',
            fallbackLabel='全部停止' if multi else '停止',
            tooltip=('软件停止 · 停止全部活动运行' if multi else '软件停止 · 停止当前控制运行；存在多个运行时选择停止对象'))
        if compact and compactChanged:
            self.startButton.setIcon(icon('play', SURFACE))
            self.stopButton.setIcon(icon('square'))

        showControls = document is not None and (shell is None or shell.showControls)
        active = self.state.get('activeJobIds') or any(row.get('activeJobIds') for row in rows.values())
        stopping = self.state.get('state') == 'STOPPING'
        canStop = bool(self.state.get('jobId') and not self.state.get('canLoad')) or any(
            row.get('jobId') and not row.get('canLoad') for row in rows.values())
        showStop = bool(showControls or (not business and (active or stopping or canStop)))
        self.startButton.setVisible(showControls)
        self.stopButton.setVisible(showStop)
        self.topBarControlHost.setVisible(dock is self.hostControls and (showControls or showStop))
        self._fitTopBar()

    def _failureDetails(self):
        return self.error or (self.state.get('message', '') if self.state.get('state') == 'FAULT' else '')

    def _copyErrorDetails(self):
        QApplication.clipboard().setText(self._failureDetails())

    def _syncContextBanner(self):
        state = self.state.get('state', '')
        self.stateLabel.setText(STATE_NAMES.get(state, state or '未知'))
        revision = self.state.get('documentRevision')
        self.stateLabel.setToolTip(f'配置版本 {revision}' if revision else self.stateLabel.text())
        observing = self.viewRunInfo is not None or bool(self.viewNotice)
        waiting = self.closing or (self.busy and self.operationKind != 'status')
        failure = self._failureDetails()
        blocked = state == 'READY' and bool(self._selectedStartBlock())
        showMessage = waiting or state in {'FAULT', 'STOPPING'} or bool(failure) or blocked
        self.message.setVisible(showMessage)
        self.message.setToolTip(failure or self.message.text())
        self.progress.setVisible(waiting or state == 'STOPPING')
        self.copyErrorButton.setVisible(bool(failure))
        self.viewingLabel.setVisible(observing)
        self.contextBanner.setVisible(showMessage or observing)

    def _controls(self):
        enabled = not self.busy and not self.closing and self.controller is not None
        self.openAction.setEnabled(enabled and self.state["canLoad"])
        self.reloadAction.setEnabled(enabled and self.state["canLoad"] and bool(self.projectPath))
        self.updateAction.setEnabled(enabled and self.state["canLoad"] and self.controller.document is not None)
        self.targetsAction.setEnabled(enabled and self.controller.document is not None)
        startable = self._startableWorkflows(self.selectedWorkflowIds)
        self.startButton.setEnabled(enabled and self.controller.document is not None and bool(startable))
        rows = self.state.get("workflows", {})
        multi = len(rows) > 1
        document = self.controller.document if self.controller is not None else None
        shell = document.presentation.shell if document is not None and document.presentation else None
        showControls = shell is None or shell.showControls
        self.stopButton.setEnabled(enabled and (any(row.get('activeJobIds') or row['jobId'] and not row['canLoad']
            for row in rows.values()) if multi else bool(self.state.get('activeJobIds') or self.state['jobId'])
            and not self.state['canLoad']))
        self.viewRunsAction.setEnabled(enabled and self.controller.document is not None)
        self.returnRunButton.setVisible(self.viewSession is not None)
        self.returnRunButton.setEnabled(enabled and self.viewSession is not None)
        if self.viewRunInfo is not None:
            # The status worker may already have adopted a publication while
            # the GUI is still scheduling retirement of the old viewing page.
            names = ' / '.join(self.controller.workflows[key].name if key in self.controller.workflows else key
                               for key in self.viewRunInfo['roots'])
            viewed = self.viewRunInfo['jobId']
            activeJobs = self.state.get('activeJobIds', [])
            if multi:
                control = '控制：全部运行'
            elif len(activeJobs) > 1:
                default = self.state.get('jobId') or activeJobs[0]
                control = f'停止时选择 · 默认 {default[:8]}'
            elif activeJobs:
                control = f'停止对象：{activeJobs[0][:8]}'
            else:
                control = '无活动运行'
            self.viewingLabel.setText(f'查看：{names} · {viewed[:8]}　|　{control}')
            self.viewingLabel.setToolTip(f'正在查看：{names} · {viewed}\n{control}')
        elif self.viewNotice:
            self.viewingLabel.setText(self.viewNotice)
            self.viewingLabel.setToolTip(self.viewNotice)
        self.settingsButton.setEnabled(not self.closing)
        self.logsButton.setEnabled(not self.closing)
        self.startupPage.setProjectLoadingEnabled(self.openAction.isEnabled())
        self.startupPage.retryButton.setEnabled(not self.busy and not self.closing)
        self.communicationButton.setEnabled(self.controller is not None and not self.closing)
        self._syncContextBanner()
        self._syncHostControlDock()
        names = dict(READY="就绪", RUNNING="检测中", STOPPING="停止中", FAULT="故障")
        for key, controls in self.workflowControls.items():
            controls["start"].setVisible(showControls)
            controls["stop"].setVisible(showControls)
            row = rows.get(key, {})
            controls["start"].setEnabled(enabled and row.get("canStart", False))
            controls["stop"].setEnabled(enabled and bool(row.get('activeJobIds') or row.get("jobId")) and not row.get("canLoad", True))
            workflowName = self.controller.workflows[key].name if key in self.controller.workflows else key
            blocked = self._startBlockText([key])
            configureHostButton(controls['start'], shell.startControl if shell else None, kind='start',
                fallbackLabel='开始流程', tooltip=blocked or f'启动流程：{workflowName}')
            configureHostButton(controls['stop'], shell.stopControl if shell else None, kind='stop',
                fallbackLabel='停止流程', tooltip=f'停止流程的活动运行：{workflowName}')
            controls["label"].setText(names.get(row.get("state"), "未加载") +
                (f"\nJob: {row['jobId']}" if row.get("jobId") else "") +
                (f"\n{row['message']}" if row.get("message") else "") + (f'\n{blocked}' if blocked else ''))
            controls["label"].setToolTip(f"流程 ID: {key}\n启动请求: {row.get('startRequestId', '')}")

    def _updateBasicPages(self, *, connected=None, connectionDetail="", operation="", workflowIds=None):
        connected = self.state.get("runtimeConnected", False) if connected is None else connected
        if self.pages is not None and (workflowIds is None
                or self.controller.document.entryWorkflowId in workflowIds):
            state = self.state
            if self.viewRunInfo is not None:
                selected = next((row for row in self.state.get('runCatalog', [])
                    if row['jobId'] == self.viewRunInfo['jobId']), self.viewRunInfo)
                state = dict(selected, jobStatus=selected['state'])
                operation = ''
            self.pages.setBasicStatus(state, connected=connected,
                connectionDetail=connectionDetail, operation=operation)
        for key, pages in self.workflowPages.items():
            if workflowIds is None or key in workflowIds:
                pages.setBasicStatus(self.state.get("workflows", {}).get(key, {}), connected=connected,
                    connectionDetail=connectionDetail, operation=operation)

    def _displayState(self):
        state = self.state['state']
        if not self.error:
            blocked = self._selectedStartBlock()
            self.message.setText(STATE_NAMES.get(state, state) +
                                 (f" · 配置版本 {self.state['documentRevision']}" if self.state.get('documentRevision') else '') +
                                 ("\n" + self.state["message"] if self.state["state"] == "FAULT" else "") +
                                 (f'\n暂不可开始：{blocked}' if blocked else ''))
        self._updateBasicPages()
        setStatusTone(self.message, 'red' if self.error or self.state['state'] == 'FAULT' else
                      {'READY': 'green', 'RUNNING': 'blue', 'STOPPING': 'amber'}.get(self.state['state'], 'neutral'))
        self._controls()

    def poll(self):
        if getattr(self, "_closeOperationDone", False):
            if self.logFeed.idle and self.logView.idle:
                self.done = True
                self.close()
            return
        if self.controller is not None and not getattr(self, "_readyRecorded", False):
            # Construction finished; project preparation may still be in flight.
            # Tools can already report the actual writer/paths without waiting for Load.
            self._runtimeReady()
        try:
            kind, value, error = self.events.get_nowait()
        except queue.Empty:
            if not self.busy and not self.closing and self.controller and time.monotonic() >= self.nextStatus:
                self.nextStatus = time.monotonic() + .3
                self.launch("status", self.controller.status)
            return
        self.worker.join()
        self.busy = False
        if error:
            self.pendingCommand = None
            self.error = safeText(error)
            self.message.setText("操作失败：" + self.error)
            setStatusTone(self.message, 'red')
            self.message.setToolTip(self.error)
            if kind == "loaded":
                if self.controller:
                    self._runtimeReady()
                else:
                    self.initializationFailed = True
                    writer = self.applicationLogWriter
                    status = ("错误已提交应用日志 writer；落盘以实际文件为准" if writer and writer.enabled and not writer.failureMessage
                              else "应用日志 writer 不可用 / 未确认写入")
                    self.startupPage.failureLabel.setText("本次初始化失败（" + status + "）：\n" + self.error
                        + "\n日志目录：" + str(writer.directory if writer else self.resolution.path / "logs"))
                    self.startupPage.retryButton.show()
                self._diagnostic("operator.project.load.failed" if self.controller else "operator.runtime.failed",
                                 self.error, level="ERROR", payload={"targetDataRoot": str(self.resolution.path)})
            if self.closing and kind != "closed":
                self.shutdown()
                return
            self.closing = False
            if kind == "closed":
                self.logFeed.closed = False
                self.logView.setRuntime(self.controller)
                self._syncTool()
            if kind == 'loaded' and self.controller and self.controller.document:
                self.projectPath = self.controller.runtime.loadedProjectFile
                self.projectLabel.setText(self.controller.document.project.name)
                self.projectLabel.setToolTip(self.projectPath)
                self._showPages()
            if kind == "status":
                self._updateBasicPages(connected=False, connectionDetail=error)
            if kind == 'viewed' and self.controller and self.controller.document:
                self._showPages()
            if self.controller:
                # Query off-thread before deciding whether Start is available.
                self.launch("status", self.controller.status)
            self._controls()
            return
        if kind == "closed":
            self._closeOperationDone = True
            return
        self.state = value
        if kind == "loaded":
            self._runtimeReady()
        revision = value.get("pagePublicationRevision") if isinstance(value, dict) else None
        previousRevision = getattr(self, "_pagePublicationRevision", None)
        if revision is not None:
            self._pagePublicationRevision = revision
        if revision is not None and previousRevision is None and kind == 'status':
            # A remote Designer can publish the first project while this
            # Runtime window is already open. Adopt its pages without taking
            # the local Load/autoStart action.
            self.selectedWorkflowIds = self.controller.document.startupWorkflows()
            kind = 'pagesPublished'
        if (revision is not None and previousRevision is not None and revision != previousRevision
                and kind not in {"loaded", "started", "workflowsStarted", "pagesPublished"}):
            self._removePages()
            self.message.setText('正在更新运行页面')
            self.launch("pagesPublished", self._refreshPublishedSessions)
            return
        self._ensureCommunication()
        if self.closing:
            self.shutdown()
            return
        if kind == "loaded" and self.controller.document:
            self.selectedWorkflowIds = self.controller.document.startupWorkflows()
        if kind in {"loaded", "started", "workflowsStarted", "pagesPublished", "viewed"} and self.controller.document:
            document = self.controller.document
            previousPath = self.projectPath
            self.projectPath = self.controller.runtime.loadedProjectFile
            if kind == 'loaded':
                self.viewNotice = ''
            self.projectLabel.setText(document.project.name)
            self.projectLabel.setToolTip(f'{document.project.name}\n{self.projectPath}')
            self.preferences.setValue("projectPath", self.projectPath)
            if kind == 'loaded' or (kind == 'pagesPublished' and self.projectPath != previousPath):
                self._recordRecentProject(self.projectPath, document.project.name)
            self.startupPage.setCurrent(self.controller)
            self.error = ""
            self._showPages()
        failures = value.get("operationErrors", [])
        if failures:
            self.error = "\n".join(failures)
            self.message.setText("部分流程操作失败（成功流程保持原状）：\n" + self.error)
        self._displayState()
        command, self.pendingCommand = self.pendingCommand, None
        if command:
            command()
        elif kind == "loaded" and self.controller.settings.autoStart:
            self.startDetection()

    def shutdown(self):
        if self.busy:
            return
        self.logView.beginClose()
        self.logFeed.beginClose()
        if self.communication is not None:
            self.communication.dispose()
            self.communication = None
        self._removePages()
        self.pendingCommand = None
        self.message.setText("正在停止并退出")
        def close():
            self._closeSession()
            if self.controller:
                self.controller.close()
        self.launch("closed", close)

    def closeEvent(self, event):
        if self.done:
            self.timer.stop()
            self.logView.finishClose()
            if self._ownsApplicationLogWriter:
                self.closeApplicationLog()
            self.preferences.setValue('window/geometry', self.normalGeometry())
            self.preferences.setValue('window/maximized', self.isMaximized())
            event.accept()
        else:
            event.ignore()
            self.closing = True
            self.message.setText('正在停止并退出')
            self._controls()
            self.shutdown()


def main(arguments=None):
    multiprocessing.freeze_support()
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if "--self-test" in arguments:
        from emo_master.apps.operator_runtime.validation import runValidationCommand
        return runValidationCommand(arguments)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", type=Path, help="Project directory, .emoproj, or legacy project.json")
    parser.add_argument("--data-root", type=Path, help="Runtime internal data, not business outputs")
    parser.add_argument("--check", action="store_true", help="Validate project without starting detection")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--export-package", type=Path, metavar="OUTPUT_DIR", help="Export a portable production project")
    actions.add_argument("--install-package", type=Path, metavar="PACKAGE", help="Install/update an offline project directory")
    parser.add_argument("--plugin-root", type=Path, action="append", help="Trusted plugin root; may repeat")
    access = parser.add_mutually_exclusive_group()
    access.add_argument("--allow-lan", dest="allow_lan", action="store_true", default=None,
                        help="Enable the configured fixed Runtime listener for trusted LAN clients")
    access.add_argument("--local-only", dest="allow_lan", action="store_false",
                        help="Use loopback and an on-demand dynamic port")
    parser.add_argument("--listen-host", help="Local listener IP, for example 0.0.0.0 or 192.168.1.20")
    parser.add_argument("--listen-port", type=int, help="Fixed port used when LAN access is enabled")
    parser.add_argument("--display-subscriptions-per-job", type=int, help="Display subscriptions per Job, 1 to 16; default 4")
    parser.add_argument("--save-listener-settings", action="store_true", help="Save listener settings and exit without starting Runtime")
    args = parser.parse_args(arguments)
    preferences = QSettings("EmoMaster", "Runtime")
    try:
        listener = readListenerSettings(preferences, allowLan=args.allow_lan, host=args.listen_host,
            port=args.listen_port, displaySubscriptionsPerJob=args.display_subscriptions_per_job)
    except ValueError as error:
        parser.error(str(error))
    if args.save_listener_settings:
        if args.project or args.check or args.export_package or args.install_package:
            parser.error("--save-listener-settings cannot be combined with project actions")
        preferences.setValue(LISTENER_SETTINGS_KEY, listenerSettingsJson(listener))
        preferences.sync()
        if preferences.status() != preferences.NoError:
            parser.error("无法保存 Runtime 监听设置：" + preferences.fileName())
        print("Runtime listener settings saved; no Runtime or Job started.")
        return 0
    if args.export_package or args.install_package or args.check:
        from emo_master import __version__
        from emo_master.core.plugin.registry import PluginRegistry
        from emo_master.apps.runtime.grpc_server.service import RuntimeService
        scan = PluginRegistry(__version__).scanRoots(args.plugin_root or [RuntimeService._builtinsRoot()])
    if args.export_package or args.install_package:
        if args.project is None or args.check:
            parser.error("package actions require an explicit project directory and cannot use --check")
        from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
        if args.export_package:
            print(buildRuntimePackage(args.project, args.export_package, registry=scan.activeOperators, deviceTypes=scan.deviceTypes))
        else:
            document = installRuntimePackage(args.install_package, args.project, registry=scan.activeOperators, deviceTypes=scan.deviceTypes)
            print(f"Installed {document.project.name}; no Job started.")
        return 0
    if args.check:
        if args.project is None:
            parser.error("--check requires an explicit project")
        from emo_master.core.project.files import resolveProjectFile
        from emo_master.core.project.migration import loadProjectPayload, migrateProjectPayload
        from emo_master.core.project.models import ProjectDocument, ProductionSettings
        from emo_master.core.project.runtime_directory import prepareRuntimeProject
        from emo_master.core.workflow.compiler import WorkflowCompiler
        source = resolveProjectFile(args.project.resolve(strict=True))
        document = ProjectDocument.model_validate(migrateProjectPayload(loadProjectPayload(source)))
        preferences = QSettings("EmoMaster", "Runtime")
        dataRoot = resolveDataRoot(args.data_root, saved=preferences.value(DEFAULT_ROOT_KEY, "")).path
        prepared = prepareRuntimeProject(document, source.parent, scan.activeOperators, deviceTypes=scan.deviceTypes,
            protectedPaths=(dataRoot / 'runtime.sqlite3', dataRoot / 'logs'))
        WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes).compile(prepared)
        settings = document.production or ProductionSettings()
        print(f"Project: {document.project.name}; mode: {settings.mode}; validation passed; no Job started.")
        return 0
    app = QApplication.instance()
    if app is None:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
        app = QApplication([])
        app.setStyle('Fusion')
    resolution = resolveDataRoot(args.data_root, saved=preferences.value(DEFAULT_ROOT_KEY, ""))
    writer = RuntimeApplicationLogWriter(resolution.path / "logs")
    window = None
    try:
        # Keep the origin with the resolved path instead of treating it as CLI input.
        window = OperatorWindow(args.project, dataRootResolution=resolution,
            preferences=preferences, listenerSettings=listener, listenerOverrides=listenerArgumentOverrides(args, listener),
            applicationLogWriter=writer,
            runtimeFactory=lambda dataRoot: ProductionRuntime(dataRoot, listenerSettings=listener,
                runtimeInstanceId=window.applicationLogWriter.runtimeInstanceId if window else writer.runtimeInstanceId,
                pluginRootPaths=tuple(map(str, args.plugin_root)) if args.plugin_root else None))
        window.show()
        return app.exec_()
    finally:
        if window is not None:
            window.closeApplicationLog()
        else:
            writer.close()


if __name__ == "__main__":
    raise SystemExit(main())
