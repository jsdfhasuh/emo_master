"""Operator-only entry with owned Runtime, automatic project loading and shutdown."""
from __future__ import annotations

import argparse
import multiprocessing
from pathlib import Path
import queue
import sys
import threading
import time

from PySide2.QtCore import QSettings, QTimer, Qt
from PySide2.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QPushButton, QStyle, QVBoxLayout, QWidget,
)

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages


class OperatorWindow(QWidget):
    def __init__(self, projectPath=None, *, dataRoot=None, preferences=None, runtimeFactory=ProductionRuntime):
        super().__init__()
        self.setWindowTitle("EmoMaster Runtime")
        self.resize(1060, 800)
        self.preferences = preferences or QSettings("EmoMaster", "Runtime")
        self.projectPath = str(projectPath or self.preferences.value("projectPath", ""))
        self.controller = self.session = self.hub = self.pages = None
        self.busy = self.closing = self.done = False
        self.error = ""
        self.state = dict(state="EMPTY", canStart=False, canLoad=False, jobId="", message="")
        self.events = queue.Queue(maxsize=1)
        self.worker = None
        self.operationKind = None
        self.pendingCommand = None
        self.nextStatus = 0.0
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.projectLabel = QLabel("未加载工程")
        self.projectLabel.setTextFormat(Qt.PlainText)
        self.projectLabel.setWordWrap(True)
        toolbar.addWidget(self.projectLabel, 1)
        self.openButton = self._button("选择工程", QStyle.SP_DirOpenIcon, self.chooseProject)
        self.reloadButton = self._button("重新加载", QStyle.SP_BrowserReload, self.reloadProject)
        self.updateButton = self._button("更新工程", QStyle.SP_DialogSaveButton, self.choosePackage)
        self.startButton = self._button("开始", QStyle.SP_MediaPlay, self.startDetection)
        self.stopButton = self._button("停止", QStyle.SP_MediaStop, self.stopDetection)
        for button in (self.openButton, self.reloadButton, self.updateButton, self.startButton, self.stopButton):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.message = QLabel("正在准备 Runtime")
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.pageLayout = QVBoxLayout()
        layout.addLayout(self.pageLayout, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(25)

        def initialize():
            self.controller = runtimeFactory(dataRoot)
            if self.projectPath:
                self.controller.load(self.projectPath)
            return self.controller.status()
        self.launch("loaded", initialize)

    def _button(self, text, icon, action):
        button = QPushButton(text)
        button.setIcon(self.style().standardIcon(icon))
        button.setToolTip(text)
        button.clicked.connect(action)
        return button

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
        self.projectPath = str(path)
        self._removePages()
        def load():
            self._closeSession()
            self.controller.load(path)
            return self.controller.status()
        self.message.setText("正在加载工程")
        self.launch("loaded", load)

    def startDetection(self):
        if self.closing or self._deferCommand(self.startDetection) or not self.state["canStart"]:
            return
        self._removePages()
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
        self.message.setText("正在停止")
        def stop():
            self.controller.stop()
            return self.controller.status()
        self.launch("stopped", stop)

    def _removePages(self):
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
        self._removePages()
        if self.session:
            self.hub = DisplayHub(self.session, self)
        self.pages = RuntimePages(document.presentation, hub=self.hub, label=document.project.name, parent=self)
        self.pages.banner.hide()
        self.pageLayout.addWidget(self.pages)
        self.pages.show()

    def _closeSession(self):
        if self.session:
            self.session.close()
            self.session = None

    def _controls(self):
        enabled = not self.busy and not self.closing and self.controller is not None
        self.openButton.setEnabled(enabled and self.state["canLoad"])
        self.reloadButton.setEnabled(enabled and self.state["canLoad"] and bool(self.projectPath))
        self.updateButton.setEnabled(enabled and self.state["canLoad"] and self.controller.document is not None)
        self.startButton.setEnabled(enabled and self.state["canStart"])
        self.stopButton.setEnabled(enabled and bool(self.state["jobId"]) and not self.state["canLoad"])

    def _displayState(self):
        names = dict(EMPTY="未加载工程", READY="就绪", RUNNING="检测中", STOPPING="停止中", FAULT="故障")
        if not self.error:
            self.message.setText(names[self.state["state"]] +
                                 ("\n" + self.state["message"] if self.state["state"] == "FAULT" else ""))
        self._controls()

    def poll(self):
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
            self.error = error
            self.message.setText("操作失败：" + error)
            self.closing = False
            if self.controller:
                # Query off-thread before deciding whether Start is available.
                self.launch("status", self.controller.status)
            else:
                self._controls()
            return
        if kind == "closed":
            self.done = True
            self.close()
            return
        self.state = value
        if self.closing:
            self.shutdown()
            return
        if kind in {"loaded", "started"} and self.controller.document:
            document = self.controller.document
            self.projectPath = self.controller.runtime.loadedProjectFile
            self.projectLabel.setText(document.project.name)
            self.projectLabel.setToolTip(self.projectPath)
            self.preferences.setValue("projectPath", self.projectPath)
            self.error = ""
            self._showPages()
        self._displayState()
        command, self.pendingCommand = self.pendingCommand, None
        if command:
            command()
        elif kind == "loaded" and self.controller.settings.autoStart and self.state["canStart"]:
            self.startDetection()

    def shutdown(self):
        if self.busy:
            return
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
    args = parser.parse_args(arguments)
    if args.export_package or args.install_package:
        if args.project is None or args.check:
            parser.error("package actions require an explicit project directory and cannot use --check")
        from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
        if args.export_package:
            print(buildRuntimePackage(args.project, args.export_package))
        else:
            document = installRuntimePackage(args.install_package, args.project)
            print(f"Installed {document.project.name}; no Job started.")
        return 0
    if args.check:
        if args.project is None:
            parser.error("--check requires an explicit project")
        runtime = ProductionRuntime(args.data_root)
        try:
            document = runtime.load(args.project)
            print(f"Project: {document.project.name}; mode: {runtime.settings.mode}; validation passed; no Job started.")
        finally:
            runtime.close()
        return 0
    app = QApplication.instance() or QApplication([])
    window = OperatorWindow(args.project, dataRoot=args.data_root)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
