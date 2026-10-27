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

from PySide2.QtCore import QSettings, QTimer, Qt
from PySide2.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages
from emo_master.core.presentation.assets import pageAssets
from emo_master.apps.operator_runtime.settings import DEFAULT_ROOT_KEY, resolveDataRoot
from emo_master.apps.operator_runtime.startup_page import StartupPage
from emo_master.apps.operator_runtime.log_reader import safeRecord, safeText
from emo_master.apps.operator_runtime.log_view import LogFeed, RuntimeLogView
from emo_master.apps.runtime.events.models import RuntimeEvent


class OperatorWindow(QWidget):
    def __init__(self, projectPath=None, *, dataRoot=None, preferences=None, runtimeFactory=ProductionRuntime):
        super().__init__()
        self.setWindowTitle("EmoMaster Runtime")
        self.resize(1060, 800)
        self.preferences = preferences or QSettings("EmoMaster", "Runtime")
        self.resolution = resolveDataRoot(dataRoot, saved=self.preferences.value(DEFAULT_ROOT_KEY, ""))
        self.runtimeFactory, self.explicitProject = runtimeFactory, projectPath
        self.projectPath = str(projectPath or self.preferences.value("projectPath", ""))
        self.controller = self.session = self.hub = self.pages = None
        self.workflowSessions, self.workflowHubs, self.workflowPages, self.workflowControls = {}, {}, {}, {}
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
        header = QHBoxLayout()
        header.addWidget(QLabel("EmoMaster Runtime"))
        self.projectLabel = QLabel("未加载工程")
        self.projectLabel.setTextFormat(Qt.PlainText)
        self.projectLabel.setWordWrap(True)
        from PySide2.QtWidgets import QSizePolicy
        self.projectLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(self.projectLabel, 1)
        self.settingsButton = self._button("Runtime 设置", QStyle.SP_FileDialogDetailedView, self.showSettings)
        self.logsButton = self._button("查看日志", QStyle.SP_FileDialogContentsView, self.showLogs)
        header.addWidget(self.settingsButton)
        header.addWidget(self.logsButton)
        layout.addLayout(header)
        toolbar = QHBoxLayout()
        self.openButton = self._button("选择工程", QStyle.SP_DirOpenIcon, self.chooseProject)
        self.reloadButton = self._button("重新加载", QStyle.SP_BrowserReload, self.reloadProject)
        self.updateButton = self._button("更新工程", QStyle.SP_DialogSaveButton, self.choosePackage)
        self.targetsButton = self._button("运行目标…", QStyle.SP_FileDialogListView, self.chooseTargets)
        self.startButton = self._button("开始", QStyle.SP_MediaPlay, self.startDetection)
        self.stopButton = self._button("停止", QStyle.SP_MediaStop, self.stopDetection)
        self.communicationButton = self._button("设备状态与控制…", QStyle.SP_ComputerIcon, self.showCommunication)
        for button in (self.openButton, self.reloadButton, self.updateButton, self.targetsButton, self.startButton, self.stopButton):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        communicationRow = QHBoxLayout()
        communicationRow.addStretch()
        communicationRow.addWidget(self.communicationButton)
        layout.addLayout(communicationRow)
        self.message = QLabel("正在准备 Runtime")
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.message.setMaximumHeight(100)
        layout.addWidget(self.message)
        self.backButton = self._button("返回", QStyle.SP_ArrowBack, self.backFromTool)
        self.backButton.hide()
        layout.addWidget(self.backButton, 0, Qt.AlignLeft)
        self.contentStack = QStackedWidget()
        self.businessHost = QWidget()
        self.pageLayout = QVBoxLayout(self.businessHost)
        self.pageLayout.setContentsMargins(0, 0, 0, 0)
        self.startupPage = StartupPage(self.preferences, self.resolution)
        self.startupPage.chooseRequested.connect(self.chooseProject)
        self.startupPage.logsRequested.connect(lambda: self.showLogs(resetFilter=True))
        self.startupPage.retryRequested.connect(self.retryInitialization)
        self.logFeed = LogFeed(self.resolution.path / "logs", self)
        self.logView = RuntimeLogView(self.logFeed)
        self.logFeed.updated.connect(self._recentLogs)
        for page in (self.businessHost, self.startupPage, self.logView):
            self.contentStack.addWidget(page)
        self.contentStack.setCurrentWidget(self.startupPage)
        QApplication.instance().focusChanged.connect(self._rememberFocus)
        layout.addWidget(self.contentStack, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(25)

        self._initialize()

    def _initialize(self):
        self.initializationFailed = False
        self.startupPage.retryButton.hide()
        self.startupPage.failureLabel.clear()
        self.error = ""
        self.message.setText("正在准备 Runtime")
        self._diagnostic("operator.storage.resolved", "本次启动目标目录：" + str(self.resolution.path),
                         payload={"source": self.resolution.source, "dataRoot": str(self.resolution.path)})

        def initialize():
            self.controller = self.runtimeFactory(self.resolution.path)
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
        self._initialize()

    def _diagnostic(self, eventType, message, *, level="INFO", payload=None):
        event = RuntimeEvent(jobId="", eventType=eventType, message=safeText(message), level=level,
            timestampMs=int(time.time() * 1000), payloadJson=json.dumps(safeRecord(payload or {}), ensure_ascii=False))
        writer = self.controller.runtime.operationalLogWriter if self.controller else None
        accepted = bool(writer and writer.enqueue(event))
        self.startupDiagnostics.append((event, accepted))
        self._diagnosticText()

    def _diagnosticText(self):
        from datetime import datetime
        writer = self.controller.runtime.operationalLogWriter if self.controller else None
        self.logFeed.diagnostics = [
            datetime.fromtimestamp(event.timestampMs / 1000).isoformat(timespec="seconds") + " · " + event.level + " · " + event.message
            + ("（已提交 writer；落盘以实际文件为准）" if accepted and writer and writer.enabled and not writer.failureMessage
               else "（未写入日志文件 / writer 未确认写入）")
            for event, accepted in self.startupDiagnostics]

    def _runtimeReady(self):
        self.logFeed.setSource(self.controller)
        self.startupPage.setCurrent(self.controller)
        if not getattr(self, "_readyRecorded", False):
            self._readyRecorded = True
            self._diagnostic("operator.storage.resolved", "当前有效目录：" + str(self.controller.dataRoot),
                             payload={"source": self.resolution.source})
            self._diagnostic("operator.runtime.ready", "Runtime 已就绪")

    def _recentLogs(self):
        rows = self.logFeed.records[-5:]
        text = "\n".join(f"{row.data.get('timestamp', '')} · {row.data.get('level', '')} · {row.data.get('message', '')}" for row in rows)
        if text and self.logFeed.historyPath:
            text = self.logFeed.sourceText(fullPath=False) + "\n" + text
        if not text:
            text = self.logFeed.statusText()
        if self.startupPage.recentLogs.toPlainText() != text:
            self.startupPage.recentLogs.setPlainText(text)

    def _syncTool(self):
        widget = self.contentStack.currentWidget()
        self.backButton.setVisible(bool(self.toolHistory))
        self.backButton.setText("返回运行页面" if self.toolHistory and self.toolHistory[-1] is self.businessHost else
                                "返回设置 / 起始页" if self.toolHistory and self.toolHistory[-1] is self.startupPage else "返回日志")
        self.logFeed.setVisible(self.isVisible() and widget in (self.startupPage, self.logView))

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

    def _switchContent(self, widget):
        import shiboken2
        focused = self.pageFocus.get(widget)
        self.contentStack.setCurrentWidget(widget)
        if focused is not None and shiboken2.isValid(focused) and focused.isVisible() and focused.isEnabled():
            focused.setFocus(Qt.OtherFocusReason)

    def showSettings(self):
        self._showTool(self.startupPage)

    def showLogs(self, checked=False, *, resetFilter=False):
        if resetFilter:
            self.logView.resetFilter()
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
        super().hideEvent(event)

    def _button(self, text, icon, action):
        button = QPushButton(text)
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
        self.communication = self.communicationDialog
        self.communicationButton.setText('设备状态与控制…')

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
        if self.closing or self._deferCommand(self.startDetection) or not self.controller.document:
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
                self.session = DisplaySession(self.controller.address, self.controller.jobId,
                    imageDemand=True, expectedRuntimeInstanceId=self.controller.runtime.runtimeInstanceId,
                    projectId=document.project.projectId)
            return self.controller.status()
        self.launch("started", start)

    def stopDetection(self):
        if (self.closing or self._deferCommand(self.stopDetection)
                or self.state["state"] not in {"RUNNING", "STOPPING", "FAULT"}):
            return
        if (len(self.controller.sessions) > 1
                or next(iter(self.controller.sessions), None) != self.controller.document.entryWorkflowId):
            return self.stopWorkflows()
        self.message.setText("正在停止")
        self._updateBasicPages(operation="stopping")
        def stop():
            self.controller.stop()
            return self.controller.status()
        self.launch("stopped", stop)

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
        rows = self.state.get("workflows", {})
        keys = [key for key in ([workflowId] if workflowId else self.selectedWorkflowIds)
                if rows.get(key, {}).get("canStart", True)]
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
                    self.workflowSessions[key] = DisplaySession(self.controller.address, owned.jobId,
                        imageDemand=True, expectedRuntimeInstanceId=self.controller.runtime.runtimeInstanceId,
                        projectId=self.controller.document.project.projectId)
            state = self.controller.status()
            state["operationErrors"] = [f"{self.controller.workflows[key].name}: {row['message']}"
                for key, row in results.items() if not row["ok"]]
            return state
        self.message.setText("正在启动流程")
        self.launch("workflowsStarted", start)

    def stopWorkflows(self, workflowId=None):
        if self.closing or self._deferCommand(lambda: self.stopWorkflows(workflowId)):
            return
        def stop():
            if workflowId is None:
                results = self.controller.stopAll()
            else:
                try:
                    self.controller.stop(workflowId)
                    results = {workflowId: dict(ok=True)}
                except Exception as error:
                    results = {workflowId: dict(ok=False, message=str(error))}
            state = self.controller.status()
            state["operationErrors"] = [f"{self.controller.workflows[key].name}: {row['message']}"
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
            self.pageLayout.removeWidget(self.pages)
            self.pages.close()
            self.pages.deleteLater()
            self.pages = None
        if self.hub:
            self.hub.deleteLater()
            self.hub = None

    def _showPages(self):
        document = self.controller.document
        if document is None:
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
            hub=self.hub, label=document.project.name, parent=self, assets=pageAssets(document))
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
                self.session = None
            if self.pages:
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
                                 label=workflow.name, parent=self.workflowTabs, assets=pageAssets(self.controller.document))
            pages.banner.hide()
            self.workflowPages[key] = pages
            self.workflowControls[key]["layout"].addWidget(pages)
            pages.show()

    def _closeSession(self):
        for key in list(self.workflowSessions):
            self._closeWorkflowSession(key)
        if self.session:
            self.session.close()
            self.session = None

    def _closeWorkflowSession(self, key):
        session = self.workflowSessions.get(key)
        if session:
            session.close()
            del self.workflowSessions[key]

    def _controls(self):
        enabled = not self.busy and not self.closing and self.controller is not None
        self.openButton.setEnabled(enabled and self.state["canLoad"])
        self.reloadButton.setEnabled(enabled and self.state["canLoad"] and bool(self.projectPath))
        self.updateButton.setEnabled(enabled and self.state["canLoad"] and self.controller.document is not None)
        self.targetsButton.setEnabled(enabled and self.controller.document is not None)
        self.startButton.setEnabled(enabled and self.controller.document is not None and bool(self.selectedWorkflowIds) and any(
            self.state.get("workflows", {}).get(key, {}).get("canStart", True) for key in self.selectedWorkflowIds))
        rows = self.state.get("workflows", {})
        multi = len(rows) > 1
        self.startButton.setText(f"开始（{len(self.selectedWorkflowIds)} 个流程）" if len(self.selectedWorkflowIds) > 1 else "开始")
        self.stopButton.setText("全部停止" if multi else "停止")
        self.stopButton.setEnabled(enabled and (any(row["jobId"] and not row["canLoad"] for row in rows.values())
                                               if multi else bool(self.state["jobId"]) and not self.state["canLoad"]))
        self.settingsButton.setEnabled(not self.closing)
        self.logsButton.setEnabled(not self.closing)
        self.startupPage.chooseButton.setEnabled(self.openButton.isEnabled())
        self.startupPage.retryButton.setEnabled(not self.busy and not self.closing)
        self.communicationButton.setEnabled(self.controller is not None and not self.closing)
        names = dict(READY="就绪", RUNNING="检测中", STOPPING="停止中", FAULT="故障")
        for key, controls in self.workflowControls.items():
            row = rows.get(key, {})
            controls["start"].setEnabled(enabled and row.get("canStart", False))
            controls["stop"].setEnabled(enabled and bool(row.get("jobId")) and not row.get("canLoad", True))
            controls["label"].setText(names.get(row.get("state"), "未加载") +
                (f"\nJob: {row['jobId']}" if row.get("jobId") else "") +
                (f"\n{row['message']}" if row.get("message") else ""))
            controls["label"].setToolTip(f"流程 ID: {key}\n启动请求: {row.get('startRequestId', '')}")

    def _updateBasicPages(self, *, connected=None, connectionDetail="", operation="", workflowIds=None):
        connected = self.state.get("runtimeConnected", False) if connected is None else connected
        if self.pages is not None and (workflowIds is None
                or self.controller.document.entryWorkflowId in workflowIds):
            self.pages.setBasicStatus(self.state, connected=connected,
                connectionDetail=connectionDetail, operation=operation)
        for key, pages in self.workflowPages.items():
            if workflowIds is None or key in workflowIds:
                pages.setBasicStatus(self.state.get("workflows", {}).get(key, {}), connected=connected,
                    connectionDetail=connectionDetail, operation=operation)

    def _displayState(self):
        names = dict(EMPTY="未加载工程", READY="就绪", RUNNING="检测中", STOPPING="停止中", FAULT="故障")
        if not self.error:
            self.message.setText(names[self.state["state"]] +
                                 (f" · 配置版本 {self.state['documentRevision']}" if self.state.get('documentRevision') else '') +
                                 ("\n" + self.state["message"] if self.state["state"] == "FAULT" else ""))
        self._updateBasicPages()
        self._controls()

    def poll(self):
        if getattr(self, "_closeOperationDone", False):
            if self.logFeed.idle:
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
            self.message.setToolTip(self.error)
            if kind == "loaded":
                if self.controller:
                    self._runtimeReady()
                else:
                    self.initializationFailed = True
                    self.startupPage.failureLabel.setText("本次初始化失败（未写入日志文件）：\n" + self.error)
                    self.startupPage.retryButton.show()
                self._diagnostic("operator.project.load.failed" if self.controller else "operator.runtime.failed",
                                 self.error, level="ERROR", payload={"targetDataRoot": str(self.resolution.path)})
            if self.closing and kind != "closed":
                self.shutdown()
                return
            self.closing = False
            if kind == "closed":
                self.logFeed.closed = False
                self._syncTool()
            if kind == 'loaded' and self.controller and self.controller.document:
                self.projectPath = self.controller.runtime.loadedProjectFile
                self.projectLabel.setText(self.controller.document.project.name)
                self.projectLabel.setToolTip(self.projectPath)
                self._showPages()
            if kind == "status":
                self._updateBasicPages(connected=False, connectionDetail=error)
            if self.controller:
                # Query off-thread before deciding whether Start is available.
                self.launch("status", self.controller.status)
            else:
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
        if (revision is not None and previousRevision is not None and revision != previousRevision
                and kind not in {"loaded", "started", "workflowsStarted", "pagesPublished"}):
            self._removePages()
            def retireOldPageSession():
                self._closeSession()
                return self.controller.status()
            self.launch("pagesPublished", retireOldPageSession)
            return
        self._ensureCommunication()
        if self.closing:
            self.shutdown()
            return
        if kind == "loaded" and self.controller.document:
            self.selectedWorkflowIds = self.controller.document.startupWorkflows()
        if kind in {"loaded", "started", "workflowsStarted", "pagesPublished"} and self.controller.document:
            document = self.controller.document
            self.projectPath = self.controller.runtime.loadedProjectFile
            self.projectLabel.setText(document.project.name)
            self.projectLabel.setToolTip(self.projectPath)
            self.preferences.setValue("projectPath", self.projectPath)
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
        elif (kind == "loaded" and self.controller.settings.autoStart and self.state["canStart"]):
            self.startDetection()

    def shutdown(self):
        if self.busy:
            return
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
            event.accept()
        else:
            event.ignore()
            self.closing = True
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
    args = parser.parse_args(arguments)
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
    app = QApplication.instance() or QApplication([])
    window = OperatorWindow(args.project, dataRoot=args.data_root,
        runtimeFactory=lambda root: ProductionRuntime(root, pluginRootPaths=tuple(map(str, args.plugin_root)) if args.plugin_root else None))
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
