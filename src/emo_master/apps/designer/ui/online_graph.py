"""Snapshot-only monitor. Never holds or mutates the Designer's edit session."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (QComboBox, QDialog, QGraphicsView, QHBoxLayout, QLabel,
    QPushButton, QSplitter, QTextEdit, QVBoxLayout)

from emo_master.apps.designer.ui.flow_scene import FlowScene, FlowNodeViewModel, FlowEdgeViewModel
from emo_master.apps.designer.ui.icon_map import icon
from emo_master.core.workflow.loop_contracts import interfacePortTypes


class OnlineGraphDialog(QDialog):
    def __init__(self, client, projectId, parent=None, *, launchSnapshot=None):
        super().__init__(parent)
        self.client, self.projectId = client, projectId
        self.pending = None
        self.data = None
        self.instance = launchSnapshot["runtimeInstanceId"] if launchSnapshot else None
        self.graphIdentity = None
        self.selectionEpoch = 0
        self.lastRefresh = 0.0
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="online-graph")
        self.launchSnapshot = deepcopy(launchSnapshot)
        self.command = None
        self.commandKind = ""
        self.startedJobs = {}
        self.uncertainRequests = {}
        self.launched = False
        self.setWindowTitle("在线 / 历史流程 · 只读")
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.revision, self.job, self.workflow, self.node, self.event = (QComboBox() for _ in range(5))
        self.revision.addItem("当前在线", 0)
        if launchSnapshot is not None:
            revision = launchSnapshot["documentRevision"]
            self.revision.addItem(f"文档 R{revision}", revision)
            self.revision.setCurrentIndex(1)
        self.job.addItem("配置快照", "")
        self.refreshButton = QPushButton()
        self.refreshButton.setIcon(icon("refresh-cw"))
        self.refreshButton.setToolTip("刷新在线快照与结果")
        self.refreshButton.setAccessibleName("刷新在线快照与结果")
        for widget in (self.revision, self.job, self.workflow, self.refreshButton):
            bar.addWidget(widget)
        layout.addLayout(bar)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.runStatus = QLabel()
        self.runStatus.setWordWrap(True)
        self.runStatus.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.runStatus)
        controls = QHBoxLayout()
        self.stopButton, self.stopAllButton, self.reconcileButton = (QPushButton() for _ in range(3))
        for button, glyph, label in ((self.stopButton, "square", "停止所选在线任务"),
                (self.stopAllButton, "square", "停止本窗口启动的任务"),
                (self.reconcileButton, "refresh-cw", "查询原启动请求")):
            button.setIcon(icon(glyph))
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setFixedSize(32, 32)
            controls.addWidget(button)
            button.setVisible(launchSnapshot is not None)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.stopButton.clicked.connect(self.stopSelected)
        self.stopAllButton.clicked.connect(self.stopLaunched)
        self.reconcileButton.clicked.connect(self.reconcile)
        split = QSplitter(Qt.Horizontal)
        self.scene = FlowScene()
        self.scene.setParent(self)
        self.view = QGraphicsView(self.scene)
        self.view.setInteractive(False)
        self.view.setAcceptDrops(False)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        split.addWidget(self.view)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        split.addWidget(self.details)
        split.setSizes([780, 320])
        layout.addWidget(split, 1)
        selection = QHBoxLayout()
        selection.addWidget(self.node)
        selection.addWidget(self.event, 1)
        layout.addLayout(selection)
        self.refreshButton.clicked.connect(self.refresh)
        self.revision.currentIndexChanged.connect(self.selectionChanged)
        self.job.currentIndexChanged.connect(self.selectionChanged)
        self.workflow.currentIndexChanged.connect(self.render)
        self.node.currentIndexChanged.connect(self.showDetails)
        self.event.currentIndexChanged.connect(self.showDetails)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        self.refresh()

    def startOnline(self):
        if self.launched or self.launchSnapshot is None or self.command is not None:
            return
        from emo_master.apps.designer.services.online_run import startOnline
        self.launched = True
        self.commandKind = "start"
        self.command = self.pool.submit(startOnline, self.client, self.launchSnapshot)
        self.runStatus.setText("正在启动已比较的在线版本")

    def stopSelected(self):
        if self.command is None and self.job.currentData():
            self.commandKind = "stop"
            self.command = self.pool.submit(self.client.stopJob, self.job.currentData())

    def stopLaunched(self):
        if self.command is None and self.startedJobs:
            self.commandKind = "stopAll"
            jobs = tuple(self.startedJobs)
            def stop():
                results = []
                for job in jobs:
                    try:
                        reply = self.client.stopJob(job)
                        results.append(f"{job}: {reply.message}")
                    except Exception as error:
                        results.append(f"{job}: {error}")
                return results
            self.command = self.pool.submit(stop)

    def reconcile(self):
        if self.command is None and self.uncertainRequests:
            self.commandKind = "start"
            requests = dict(self.uncertainRequests)
            def query():
                results = []
                for requestId, workflowId in requests.items():
                    try:
                        reply = self.client.getStartRequest(requestId, self.launchSnapshot["runtimeInstanceId"])
                        results.append(dict(workflowId=workflowId, requestId=requestId, reply=reply))
                    except Exception as error:
                        results.append(dict(workflowId=workflowId, requestId=requestId, error=str(error)))
                return results
            self.command = self.pool.submit(query)

    def pollCommand(self):
        idle = self.command is None
        self.stopButton.setEnabled(idle and bool(self.job.currentData()))
        self.stopAllButton.setEnabled(idle and bool(self.startedJobs))
        self.reconcileButton.setEnabled(idle and bool(self.uncertainRequests))
        if self.command is None or not self.command.done():
            return
        future, self.command = self.command, None
        try:
            result = future.result()
            if self.commandKind == "start":
                messages = []
                for item in result:
                    reply, requestId = item.get("reply"), item["requestId"]
                    if reply is None or reply.status in {"UNKNOWN", "RESET_REQUIRED"}:
                        self.uncertainRequests[requestId] = item["workflowId"]
                    else:
                        self.uncertainRequests.pop(requestId, None)
                    if reply is not None and reply.job_id:
                        self.startedJobs[reply.job_id] = item["workflowId"]
                        if self.job.findData(reply.job_id) < 0:
                            self.job.addItem(item["workflowId"] + " · " + reply.job_id[:8], reply.job_id)
                        self.job.setCurrentIndex(self.job.findData(reply.job_id))
                    messages.append(item["workflowId"] + ": " + (reply.message if reply is not None else item["error"])
                                    + " · " + requestId)
                revision = self.launchSnapshot["documentRevision"]
                if self.revision.findData(revision) < 0:
                    self.revision.addItem(f"文档 R{revision}", revision)
                self.revision.setCurrentIndex(self.revision.findData(revision))
                self.runStatus.setText("\n".join(messages))
            elif self.commandKind == "stopAll":
                self.runStatus.setText("\n".join(result))
            else:
                self.runStatus.setText(result.message)
        except Exception as error:
            self.runStatus.setText(str(error))

    def selectionChanged(self):
        self.selectionEpoch += 1
        if self.pending is None:
            self.refresh()

    def refresh(self):
        if self.pending is not None:
            return
        self.requestEpoch = self.selectionEpoch
        self.lastRefresh = time.monotonic()
        self.pending = self.pool.submit(self.client.getOnlineGraphSnapshot, self.projectId,
                                       self.revision.currentData() or 0, self.job.currentData() or "")
        self.refreshButton.setEnabled(False)

    @staticmethod
    def replaceChoices(combo, choices, selected):
        combo.blockSignals(True)
        combo.clear()
        for label, value in choices:
            combo.addItem(label, value)
        index = combo.findData(selected)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def poll(self):
        self.pollCommand()
        if self.pending is None and self.isVisible() and self.job.currentData() and time.monotonic() - self.lastRefresh > .5:
            self.refresh()
        if self.pending is None or not self.pending.done():
            return
        future, self.pending = self.pending, None
        self.refreshButton.setEnabled(True)
        if self.requestEpoch != self.selectionEpoch:
            self.refresh()
            return
        try:
            data = future.result()
            if self.instance is not None and self.instance != data["runtimeInstanceId"]:
                raise ValueError("Runtime 实例已更换，请重新打开在线历史图")
            self.instance = data["runtimeInstanceId"]
            self.data = deepcopy(data)
            snapshot = data["snapshot"]
            if not self.projectId:
                self.projectId = snapshot['payload']['project']['projectId']
            self.replaceChoices(self.revision, [("当前在线", 0)] + [
                (f"文档 R{r['documentRevision']} / 执行 R{r['executionRevision']}", r["documentRevision"])
                for r in data["history"]], self.revision.currentData())
            self.replaceChoices(self.job, [("配置快照", "")] + [
                (f"{j['workflowId']} · {j['status']} · {j['jobId'][:8]}", j["jobId"]) for j in data["jobs"]], self.job.currentData())
            payload = snapshot["payload"]
            order = list(dict.fromkeys([*payload.get("workflowOrder", []), *payload["workflows"]]))
            self.replaceChoices(self.workflow, [(payload["workflows"][k]["name"], k)
                                for k in order if k in payload["workflows"]],
                                self.workflow.currentData() or payload.get("entryWorkflowId"))
            self.status.setText(f"只读 · {self.projectId} · 文档 R{snapshot['documentRevision']} / 执行 R{snapshot['executionRevision']}"
                                + (" · 当前选择版本无保留结果" if self.job.currentData() and not data["events"] else ""))
            self.render()
        except Exception as error:
            self.data = None
            self.graphIdentity = None
            self.scene.clearGraph()
            self.details.clear()
            self.status.setText(str(error))

    def render(self):
        if self.data is None:
            return
        key = self.workflow.currentData()
        flow = self.data["snapshot"]["payload"]["workflows"].get(key)
        if flow is None:
            return
        identity = (self.instance, self.data["snapshot"]["documentRevision"], key)
        if identity != self.graphIdentity:
            self.scene.clearGraph()
            for index, node in enumerate(flow["nodes"]):
                position = flow.get("layout", {}).get("nodePositions", {}).get(node["nodeId"], {})
                ports = self.data["ports"][key][node["nodeId"]]
                self.scene.addFlowNode(FlowNodeViewModel(node["nodeId"], node.get("displayName") or node["nodeId"],
                    float(position.get("x", index % 3 * 280)), float(position.get("y", index // 3 * 180)),
                    interfacePortTypes(ports["inputs"]), interfacePortTypes(ports["outputs"]),
                    node.get("operatorId") or "", node["kind"]))
            for edge in flow["edges"]:
                self.scene.renderEdge(FlowEdgeViewModel(edge["fromNode"], edge["fromPort"], edge["toNode"], edge["toPort"]))
            if any(node["nodeId"] not in flow.get("layout", {}).get("nodePositions", {}) for node in flow["nodes"]):
                self.scene.layoutNodesFlow()
            else:
                self.scene.refreshAllRoutes()
            self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20), Qt.KeepAspectRatio)
            self.graphIdentity = identity
        self.replaceChoices(self.node, [(n.get("displayName") or n["nodeId"], n["nodeId"]) for n in flow["nodes"]],
                            self.node.currentData())
        events = [("仅配置", None)] + [(f"#{e['sequence']} · {e['nodeId']} · {e['eventType']} · {e['payload']['cycleId'][:8]}", e)
                   for e in self.data["events"] if e["workflowId"] == key]
        self.replaceChoices(self.event, events, self.event.currentData())
        self.showDetails()

    def showDetails(self):
        if self.data is None:
            return
        flow = self.data["snapshot"]["payload"]["workflows"].get(self.workflow.currentData())
        if flow is None:
            return
        event = self.event.currentData()
        nodeId = event["nodeId"] if event and event["nodeId"] else self.node.currentData()
        node = next((n for n in flow["nodes"] if n["nodeId"] == nodeId), None)
        self.details.setPlainText(json.dumps(dict(node=node, result=event), ensure_ascii=False, indent=2))

    def reject(self):
        if self.isWindow():
            super().reject()

    def done(self, result):
        if self.command is not None:
            self.runStatus.setText("操作尚未返回；请先核对结果")
            return
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().done(result)
