"""One bounded asynchronous observer/controller shared by both Qt entry points."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import queue
import threading
import time
import os
from pathlib import Path
from uuid import uuid4

from PySide2.QtCore import QObject, QTimer, Signal
from PySide2.QtWidgets import QMessageBox

from emo_master.clients.runtime.calls import DisplayCallContext
from emo_master.ui.communication_panel import CommunicationSettingsDialog, summary


class CommunicationController(QObject):
    changed = Signal(str)

    def __init__(self, client, panel, project, save=None, navigate=None, parent=None):
        super().__init__(parent)
        self.client, self.panel, self.project = client, panel, project
        self.save, self.navigate = save, navigate
        self.state = None
        self.history = OrderedDict()
        self.contextKey = None
        self.identity = None
        self.observedAt = 0.0
        self.nextPoll = 0.0
        self.thread = None
        self.token = None
        self.events = queue.Queue(maxsize=1)
        self.message = ""
        self.closing = False
        self.unsynced = False
        self.settingsDialog = None
        self.disableSave = None
        panel.startButton.clicked.connect(lambda: self.control("START"))
        panel.stopButton.clicked.connect(self.confirmStop)
        panel.settingsButton.clicked.connect(self.settings)
        panel.syncButton.clicked.connect(self.syncSaved)
        panel.settingsButton.setVisible(save is not None)
        panel.syncButton.setVisible(save is not None)
        panel.navigateButton.clicked.connect(self.navigateRecord)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def _render(self):
        self.panel.setState(self.state, list(self.history.values()), busy=self.thread is not None, message=self.message)
        self.changed.emit(summary(self.state))

    def poll(self):
        project = self.project()
        if (self.thread is not None and self.state and self.state.get("projectId") and self.observedAt
                and time.monotonic() - self.observedAt > 2.5):
            self.state = None
            self.message = "实时观测已过期，Runtime 状态未知；下方保留历史记录。"
            self._render()
        key = (project.get("token"), project.get("projectId"), project.get("path"))
        if key != self.contextKey:
            self.contextKey = key
            self.state = self.identity = None
            self.history.clear()
            self.message = ""
            self.unsynced = False
            self.nextPoll = 0.0
            if self.settingsDialog is not None:
                self.settingsDialog.reject()
                self.settingsDialog = None
            self._render()
        if self.thread is not None and not self.thread.is_alive():
            self.thread.join()
            self.thread = None
            context, kind, value, error = self.events.get_nowait()
            if kind in {"PREPARE_DISABLE", "PREPARE_SAVE"}:
                self._finishDisablePreparation(context, value, error)
                return
            if kind in {"SYNC_DISABLE", "ABORT_DISABLE"}:
                transaction = self.disableSave
                if not error:
                    self.disableSave = None
                if transaction and transaction.get("dialog") is self.settingsDialog and self.settingsDialog:
                    self.settingsDialog.setEnabled(True)
                    if not error and kind == "SYNC_DISABLE":
                        self.settingsDialog.accept()
                    else:
                        self.settingsDialog.error.setText(error or transaction.get("error", "保存已取消"))
            if context == self.contextKey and not self.closing:
                if error:
                    self.state = None
                    self.message = ("配置已保存到项目，但 Runtime 尚未同步：" if kind in {"SYNC", "SYNC_DISABLE"} else
                                    "控制结果未知，请刷新状态；不要自动重复操作：" if kind in {"START", "STOP"} else "通信状态不可用：") + error
                    if (kind == "state" and not project["config"]["enabled"]
                            and error.startswith(("E_TCP_PROJECT_MISMATCH:", "E_TCP_UNSUPPORTED:"))):
                        self.state = dict(projectId=project["projectId"], enabled=False, state="STOPPED", localOnly=True,
                            effective=project["config"], runtimeInstanceId="—", serviceGeneration=0, channels=[])
                        self.message = "此项目不使用托管 TCP 通信。Runtime 尚未绑定此工程或不支持该功能。"
                else:
                    identity = (value["runtimeInstanceId"], value["projectLoadGeneration"])
                    if identity != self.identity:
                        self.history.clear()
                        self.identity = identity
                    for item in value.get("records", []):
                        self.history[item["sequence"]] = item
                    while len(self.history) > 512:
                        self.history.popitem(last=False)
                    self.state = value
                    self.observedAt = time.monotonic()
                    self.unsynced = value.get("desired") != project["config"]
                    if kind == "state" and not self.unsynced:
                        self.message = ""
                    if kind != "state":
                        self.message = "已保存并同步；服务未因保存而启动。" if kind in {"SYNC", "SYNC_DISABLE"} else ""
                    if kind == "ABORT_DISABLE":
                        self.message = transaction.get("error", "保存已取消；启动保护已撤销。") if transaction else ""
                    elif self.unsynced:
                        self.message = "项目配置与 Runtime 不同，请先同步已保存配置；禁止启动旧配置。"
                self._render()
            self.nextPoll = time.monotonic() + (0.4 if kind != "state" else 1.0)
        if self.closing or self.thread is not None or time.monotonic() < self.nextPoll:
            return
        if not project.get("path"):
            if self.state is None:
                self.message = "先保存或打开项目，再同步 Runtime。"
                self.state = dict(projectId="", enabled=False, state="STOPPED", effective=project["config"],
                                  runtimeInstanceId="—", serviceGeneration=0, channels=[])
                self._render()
            return
        after = self.state.get("sequence", 0) if self.state else 0
        self._submit("state", lambda token: self.client.communicationState(project["projectId"], after,
                     projectPath=project["path"], cancellation=token))

    def _submit(self, kind, operation):
        if (self.closing and kind != "ABORT_DISABLE") or self.thread is not None:
            return False
        key = self.contextKey
        self.token = DisplayCallContext()
        token = self.token
        def work():
            value, error = None, ""
            try:
                value = operation(token)
            except Exception as failure:
                error = f"{getattr(failure, 'code', '')}: {failure}"
            self.events.put((key, kind, value, error))
        self.thread = threading.Thread(target=work, name="project-communication-ui", daemon=True)
        self.thread.start()
        self._render()
        return True

    def control(self, action):
        if not self._stateMatchesProject():
            self.state = None
            self.message = "项目身份已改变，请先同步并刷新当前项目。"
            self.nextPoll = 0
            self._render()
            return
        if action == "START" and self.unsynced:
            self.message = "项目配置尚未同步，请到项目设置再次保存/同步后启动服务。"
            self._render()
            return
        if self.state is None or self.thread is not None or time.monotonic() - self.observedAt > 2.0:
            self.message = "请等待刷新确认 Runtime 状态后操作。"
            self.nextPoll = 0
            self._render()
            return
        state = deepcopy(self.state)
        self._submit(action, lambda token: self.client.controlCommunication(action, state, cancellation=token))

    def confirmStop(self):
        answer = QMessageBox.question(self.panel, "停止 TCP 服务", "将断开此项目所有 TCP 客户端，并使依赖此服务的未完成节点失败。\n停止服务不会撤销已发送的数据，也不会自动停止其他流程。是否继续？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.control("STOP")

    def settings(self):
        if self.settingsDialog is not None:
            self.settingsDialog.show()
            self.settingsDialog.raise_()
            return
        project = deepcopy(self.project())
        key = (project.get("token"), project.get("projectId"), project.get("path"))
        baseline = dict(project=project, state=deepcopy(self.state) if self._stateMatchesProject() else None)
        def save(config):
            current = self.project()
            if key != (current.get("token"), current.get("projectId"), current.get("path")):
                return "所属项目已改变，请关闭此设置页并在当前项目重新打开。"
            return self.saveSettings(config, baseline=baseline)
        dialog = CommunicationSettingsDialog(project["name"], project["config"], save, self.panel)
        self.settingsDialog = dialog
        dialog.finished.connect(lambda: setattr(self, "settingsDialog", None))
        dialog.show()

    def saveSettings(self, config, *, baseline=None):
        if self.closing or self.thread is not None:
            return "请等待当前通信查询或控制结束后再保存。"
        if self.disableSave is not None:
            return "上次配置保存尚未确认，请用“同步已保存配置”完成或撤销保护。"
        project = deepcopy(self.project())
        baseline = baseline or dict(project=deepcopy(project),
                                    state=deepcopy(self.state) if self._stateMatchesProject() else None)
        if baseline["project"]["config"] != project["config"]:
            return "通信配置已被其他编辑修改，尚未写入项目。请保留草稿，关闭设置后刷新并重新编辑。"
        if not config["enabled"] and (project["config"]["enabled"] or self._stateMatchesProject()
                                      or (project.get("path") and self.state is None)):
            if not self._stateMatchesProject():
                return "Runtime 尚未绑定当前项目，请先同步并刷新，再关闭开关。"
            if not self.state or time.monotonic() - self.observedAt > 2.0:
                return "Runtime 状态未知，不能关闭开关。请恢复连接并确认服务已停止。"
            if self.state["state"] not in {"STOPPED", "FAILED"} or self.state.get("ownsResources"):
                return "请先到通信面板停止服务，再关闭项目开关。"
            if self.state.get("localOnly"):
                return "请先同步此项目到 Runtime，再关闭开关。"
        if not project.get("path"):
            # First establish the new project's file using its UNCHANGED config.
            # The edited configuration is written only after Runtime reservation.
            error = self.save(project["config"])
            if error:
                return error
            project = deepcopy(self.project())
            if not project.get("path"):
                return "请先保存项目，再保存通信配置。"
        key = (project.get("token"), project.get("projectId"), project.get("path"))
        self.contextKey = key
        action = "PREPARE_SAVE" if config["enabled"] else "PREPARE_DISABLE"
        # Historical attribute name; now owns both ordinary and disable saves.
        self.disableSave = dict(config=deepcopy(config), project=project, state=deepcopy(baseline["state"]),
                                token=uuid4().hex, saved=False, attempted=False, dialog=self.settingsDialog)
        transaction = self.disableSave
        if self.settingsDialog:
            self.settingsDialog.setEnabled(False)

        def prepare(token):
            state = transaction["state"]
            if state is None:
                try:
                    state = self.client.communicationState(project["projectId"], projectPath=project["path"], cancellation=token)
                except Exception as failure:
                    if getattr(failure, "code", "") != "E_TCP_PROJECT_MISMATCH":
                        raise
                    reply = self.client.loadProject(project["path"])
                    if not reply.ok:
                        raise ValueError(reply.message)
                    state = self.client.communicationState(project["projectId"], projectPath=project["path"], cancellation=token)
            if state.get("desired") != baseline["project"]["config"]:
                raise ValueError("通信配置发生冲突，尚未写入项目。请保留草稿，刷新并重新编辑；不会覆盖其他客户端的配置。")
            transaction["state"] = state
            transaction["attempted"] = True
            return self.client.controlCommunication(action, state, saveToken=transaction["token"], cancellation=token)

        self._submit(action, prepare)
        return "正在校验配置版本并保护保存；此时尚未写入项目。"

    def _stateMatchesProject(self):
        project = self.project()
        return bool(self.state and not self.state.get("localOnly") and project.get("path")
                    and self.state.get("projectId") == project.get("projectId")
                    and self.state.get("projectPath") == os.path.normcase(str(Path(project["path"]).resolve())))

    def _finishDisablePreparation(self, context, state, error):
        transaction = self.disableSave
        if transaction is None:
            return
        current = self.project()
        key = (current.get("token"), current.get("projectId"), current.get("path"))
        if self.closing or context != key:
            error = error or "项目已改变或窗口正在关闭；未保存通信配置。"
        if not error and current["config"] != transaction["project"]["config"]:
            error = "通信配置发生冲突，尚未写入项目。请保留草稿，刷新并重新编辑。"
        if not error and not (state.get("savePending") or state.get("disableSavePending")):
            error = "Runtime 未确认保存保护，项目未修改。"
        if not error:
            transaction["state"] = state
            try:
                error = self.save(transaction["config"])
            except Exception as failure:
                error = str(failure)
        if error:
            if "E_TCP_CONFIG_STALE" in str(error):
                error = str(error) + "；并发配置冲突，尚未写入项目，草稿已保留。请先核对本地和 Runtime 配置，再重新打开设置。"
            transaction["error"] = str(error)
            if not transaction["attempted"]:
                self.disableSave = None
                self.message = str(error)
                if transaction["dialog"] is self.settingsDialog and self.settingsDialog:
                    self.settingsDialog.setEnabled(True)
                    self.settingsDialog.error.setText(str(error))
                self._render()
                return
            self._submit("ABORT_DISABLE", lambda token: self.client.controlCommunication(
                "ABORT_DISABLE", transaction["state"], saveToken=transaction["token"], cancellation=token))
        else:
            transaction["saved"] = True
            self.unsynced = True
            self._submit("SYNC_DISABLE", lambda token: self.client.controlCommunication(
                "SYNC", transaction["state"], config=transaction["config"],
                saveToken=transaction["token"], cancellation=token))

    def navigateRecord(self):
        record = self.panel.currentRecord()
        if self.navigate and record.get("source"):
            self.navigate(record["source"])

    def syncSaved(self):
        project = self.project()
        if not project.get("path") or self.thread is not None:
            self.message = "请先保存项目，并等待当前通信操作结束。"
            self._render()
            return
        if self.disableSave is not None and not self.disableSave["saved"]:
            transaction = self.disableSave
            self._submit("ABORT_DISABLE", lambda token: self.client.controlCommunication(
                "ABORT_DISABLE", transaction["state"], saveToken=transaction["token"], cancellation=token))
            return
        def sync(token):
            from pathlib import Path
            from emo_master.core.project.models import ProjectDocument
            from emo_master.core.project.communication import projectCommunication
            document = ProjectDocument.model_validate_json(Path(project["path"]).read_text(encoding="utf-8-sig"))
            config = projectCommunication(document).model_dump()
            if config != project["config"] or document.project.projectId != project["projectId"]:
                raise ValueError("通信草稿或项目身份与磁盘不同，请先在项目设置保存配置")
            try:
                state = self.client.communicationState(project["projectId"], projectPath=project["path"], cancellation=token)
            except Exception as failure:
                if getattr(failure, "code", "") != "E_TCP_PROJECT_MISMATCH":
                    raise
                if self.disableSave:
                    raise ValueError("项目身份已改变，不能用旧保存凭据重新加载或覆盖其他项目。") from failure
                reply = self.client.loadProject(project["path"])
                if not reply.ok:
                    raise ValueError(reply.message)
                state = self.client.communicationState(project["projectId"], projectPath=project["path"], cancellation=token)
            if self.disableSave:
                transaction = self.disableSave
                original = transaction["state"]
                identity = ("runtimeInstanceId", "projectLoadGeneration", "projectPath")
                if any(state.get(field) != original.get(field) for field in identity):
                    raise ValueError("Runtime 或项目身份已改变，请确认已保存配置后重新加载项目，不能复用旧保存凭据。")
                if not (state.get("savePending") or state.get("disableSavePending")):
                    if state.get("desired") == config:
                        return state  # Commit succeeded but its reply was lost; do not replay.
                    raise ValueError("保存保护已结束且配置发生冲突，保留本地文件，不覆盖 Runtime。")
                return self.client.controlCommunication("SYNC", original, config=config,
                    saveToken=transaction["token"], cancellation=token)
            saveToken = ""
            return self.client.controlCommunication("SYNC", state, config=config, saveToken=saveToken, cancellation=token)
        self.unsynced = True
        self._submit("SYNC_DISABLE" if self.disableSave else "SYNC", sync)

    def close(self):
        self.closing = True
        if self.thread is not None and not self.thread.is_alive():
            self.poll()
        if self.token is not None and self.disableSave is None:
            self.token.cancel()
        if self.thread is not None and self.thread.is_alive():
            return False
        self.timer.stop()
        closeOwner = getattr(self.client, "closeDisplayOwner", None)
        if callable(closeOwner):
            closeOwner("project-communication")
        if self.settingsDialog is not None:
            self.settingsDialog.reject()
        return True
