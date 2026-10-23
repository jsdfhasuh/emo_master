"""Snapshot-only monitor. Never holds or mutates the Designer's edit session."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
from html import escape

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QCheckBox,
    QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from emo_master.apps.designer.ui.flow_scene import FlowScene
from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.read_only_graph import (ReadOnlyFlowView, drawGraph, nodeDetails,
    nodeName, eventName, changedNodes, nodeConnections, displayValue)


class OnlineGraphDialog(QDialog):
    def __init__(self, client, projectId, parent=None, *, launchSnapshot=None, snapshotData=None, comparisonData=None):
        super().__init__(parent)
        self.client, self.projectId = client, projectId
        self.pending = None
        self.data = None
        self.staticData = snapshotData
        self.comparison = comparisonData
        self.selectedNodeId = None
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
        self.setWindowTitle("流程版本与运行记录 · 只读")
        self.resize(1280, 800)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.revision, self.job, self.workflow, self.node, self.event = (QComboBox() for _ in range(5))
        self.revision.addItem("当前在线", 0)
        self.compare = QComboBox()
        self.compare.addItem('上一版本', -1)
        self.compare.addItem('不对照', None)
        self.compare.addItem('当前在线版本', 0)
        if launchSnapshot is not None:
            revision = launchSnapshot["documentRevision"]
            self.revision.addItem(f"文档 R{revision}", revision)
            self.revision.setCurrentIndex(1)
        self.job.addItem("只看流程配置", "")
        self.refreshButton = QPushButton()
        self.refreshButton.setIcon(icon("refresh-cw"))
        self.refreshButton.setToolTip("刷新在线快照与结果")
        self.refreshButton.setAccessibleName("刷新在线快照与结果")
        for title, widget in (('查看版本', self.revision), ('对照版本', self.compare), ('流程', self.workflow)):
            bar.addWidget(QLabel(title))
            widget.setMinimumWidth(140)
            bar.addWidget(widget, 1)
        self.refreshButton.setFixedSize(32, 32)
        bar.addWidget(self.refreshButton)
        layout.addLayout(bar)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.runStatus = QLabel()
        self.runStatus.setWordWrap(True)
        self.runStatus.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.runStatus)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('运行记录'))
        controls.addWidget(self.job, 1)
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
        for glyph, label, action in [('plus', '放大流程', lambda: self.zoomViews(1.2)),
                ('minus', '缩小流程', lambda: self.zoomViews(1 / 1.2)),
                ('maximize', '适合窗口', self.fitViews)]:
            button = QPushButton()
            button.setIcon(icon(glyph))
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setFixedSize(32, 32)
            button.clicked.connect(action)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.stopButton.clicked.connect(self.stopSelected)
        self.stopAllButton.clicked.connect(self.stopLaunched)
        self.reconcileButton.clicked.connect(self.reconcile)
        split = QSplitter(Qt.Horizontal)
        self.scene = FlowScene()
        self.scene.setParent(self)
        self.view = ReadOnlyFlowView(self.scene)
        self.beforeScene = FlowScene()
        self.beforeScene.setParent(self)
        self.beforeView = ReadOnlyFlowView(self.beforeScene)
        graphPanel = QWidget()
        graphLayout = QVBoxLayout(graphPanel)
        graphLayout.setContentsMargins(0, 0, 0, 0)
        self.diffSummary = QLabel()
        self.diffSummary.setWordWrap(True)
        graphLayout.addWidget(self.diffSummary)
        graphSplit = QSplitter(Qt.Horizontal)
        self.beforePanel = QWidget()
        beforeLayout = QVBoxLayout(self.beforePanel)
        beforeLayout.setContentsMargins(0, 0, 0, 0)
        self.beforeTitle = QLabel('对照版本')
        beforeLayout.addWidget(self.beforeTitle)
        beforeLayout.addWidget(self.beforeView, 1)
        graphSplit.addWidget(self.beforePanel)
        afterPanel = QWidget()
        afterLayout = QVBoxLayout(afterPanel)
        afterLayout.setContentsMargins(0, 0, 0, 0)
        self.afterTitle = QLabel('所选版本')
        afterLayout.addWidget(self.afterTitle)
        afterLayout.addWidget(self.view, 1)
        graphSplit.addWidget(afterPanel)
        graphLayout.addWidget(graphSplit, 1)
        split.addWidget(graphPanel)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        detailPanel = QWidget()
        detailLayout = QVBoxLayout(detailPanel)
        detailLayout.setContentsMargins(0, 0, 0, 0)
        self.technicalDetails = QCheckBox('技术详情')
        self.technicalDetails.toggled.connect(self.showDetails)
        detailLayout.addWidget(self.technicalDetails)
        detailLayout.addWidget(self.details)
        split.addWidget(detailPanel)
        split.setSizes([900, 360])
        layout.addWidget(split, 1)
        selection = QHBoxLayout()
        selection.addWidget(QLabel('节点'))
        selection.addWidget(self.node, 1)
        selection.addWidget(QLabel('执行结果'))
        selection.addWidget(self.event, 1)
        layout.addLayout(selection)
        self.refreshButton.clicked.connect(self.refresh)
        self.revision.currentIndexChanged.connect(self.selectionChanged)
        self.compare.currentIndexChanged.connect(self.selectionChanged)
        self.job.currentIndexChanged.connect(self.selectionChanged)
        self.workflow.currentIndexChanged.connect(self.render)
        self.node.currentIndexChanged.connect(self.nodeSelectionChanged)
        self.event.currentIndexChanged.connect(self.showDetails)
        self.view.nodeClicked.connect(self.selectNode)
        self.beforeView.nodeClicked.connect(self.selectNode)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        if snapshotData is None:
            self.refresh()
        else:
            self.data = deepcopy(snapshotData)
            self.instance = self.data['runtimeInstanceId']
            for widget in (self.revision, self.compare, self.job, self.refreshButton):
                widget.setEnabled(False)
            self.revision.setItemText(0, '本地待写入的工程')
            self.compare.setItemText(0, '运行端原配置')
            payload = self.data['snapshot']['payload']
            flows = dict((comparisonData or {}).get('snapshot', {}).get('payload', {}).get('workflows', {}))
            flows.update(payload['workflows'])
            self.replaceChoices(self.workflow, [(flow['name'], key) for key, flow in flows.items()], payload.get('entryWorkflowId'))
            self.status.setText('只读对照：左侧是运行端原配置，右侧是本地待写入的内容')
            self.render()

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
        if self.pending is not None or self.staticData is not None:
            return
        self.requestEpoch = self.selectionEpoch
        self.lastRefresh = time.monotonic()
        revision, job, comparison = self.revision.currentData() or 0, self.job.currentData() or '', self.compare.currentData()
        def read():
            data = self.client.getOnlineGraphSnapshot(self.projectId, revision, job)
            target = comparison
            if target == -1:
                target = max((r['documentRevision'] for r in data['history']
                              if r['documentRevision'] < data['snapshot']['documentRevision']), default=None)
            other = None
            if target is not None and target != data['snapshot']['documentRevision']:
                other = self.client.getOnlineGraphSnapshot(self.projectId, target, '')
                if other['runtimeInstanceId'] != data['runtimeInstanceId']:
                    raise ValueError('运行端已更换，请重新打开版本对照')
                if other['snapshot']['payload']['project']['projectId'] != data['snapshot']['payload']['project']['projectId']:
                    raise ValueError('两个版本不属于同一工程，无法对照')
            return data, other
        self.pending = self.pool.submit(read)
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
            data, self.comparison = future.result()
            if self.instance is not None and self.instance != data["runtimeInstanceId"]:
                raise ValueError("Runtime 实例已更换，请重新打开在线历史图")
            self.instance = data["runtimeInstanceId"]
            self.data = deepcopy(data)
            snapshot = data["snapshot"]
            if not self.projectId:
                self.projectId = snapshot['payload']['project']['projectId']
            self.replaceChoices(self.revision, [("当前在线", 0)] + [
                (f"工程版本 {r['documentRevision']}", r["documentRevision"])
                for r in data["history"]], self.revision.currentData())
            payload = snapshot["payload"]
            self.replaceChoices(self.compare, [('上一版本', -1), ('不对照', None), ('当前在线版本', 0)] + [
                (f"工程版本 {r['documentRevision']}", r['documentRevision']) for r in data['history']], self.compare.currentData())
            states = dict(RUNNING='运行中', COMPLETED='已完成', SUCCEEDED='已完成', FAILED='失败', STOPPED='已停止', CANCELLED='已取消', QUEUED='等待运行')
            self.replaceChoices(self.job, [("只看流程配置", "")] + [
                (f"{payload['workflows'].get(j['workflowId'], {}).get('name', j['workflowId'])} · {states.get(j['status'], j['status'])} · {j['jobId'][:8]}", j['jobId']) for j in data['jobs']], self.job.currentData())
            flows = dict((self.comparison or {}).get('snapshot', {}).get('payload', {}).get('workflows', {}))
            flows.update(payload['workflows'])
            order = list(dict.fromkeys([*payload.get("workflowOrder", []), *flows]))
            self.replaceChoices(self.workflow, [(flows[k]['name'], k)
                                for k in order if k in flows],
                                self.workflow.currentData() or payload.get("entryWorkflowId"))
            self.status.setText(f"只读 · {payload['project'].get('name', self.projectId)} · 工程版本 {snapshot['documentRevision']}"
                                + (" · 当前选择版本无保留结果" if self.job.currentData() and not data["events"] else ""))
            self.render()
        except Exception as error:
            self.data = None
            self.graphIdentity = None
            self.scene.clearGraph()
            self.beforeScene.clearGraph()
            self.diffSummary.clear()
            self.details.clear()
            self.status.setText('Runtime 尚未加载工程；可从主界面“Runtime > 写入 Runtime…”首次下发'
                                if getattr(error, 'code', '') == 'E_CONFIG_NOT_LOADED' else str(error))

    def render(self):
        if self.data is None:
            return
        key = self.workflow.currentData()
        flow = self.data["snapshot"]["payload"]["workflows"].get(key)
        previous = (self.comparison or {}).get('snapshot', {}).get('payload', {}).get('workflows', {}).get(key)
        if flow is None and previous is None:
            return
        comparisonRevision = (self.comparison or {}).get('snapshot', {}).get('documentRevision')
        identity = (self.instance, self.data["snapshot"]["documentRevision"], key, comparisonRevision)
        if identity != self.graphIdentity:
            drawGraph(self.scene, flow, self.data.get('ports', {}).get(key, {}), previous or {} if self.comparison else None)
            drawGraph(self.beforeScene, previous, (self.comparison or {}).get('ports', {}).get(key, {}), flow or {}, baseline=True)
            self.beforePanel.setVisible(self.comparison is not None)
            self.beforeTitle.setText('运行端原配置' if self.staticData else f'对照：工程版本 {comparisonRevision}')
            self.afterTitle.setText('本地待写入' if self.staticData else f"所选：工程版本 {self.data['snapshot']['documentRevision']}")
            old = {n['nodeId']: n for n in (previous or {}).get('nodes', [])}
            new = {n['nodeId']: n for n in (flow or {}).get('nodes', [])}
            self.diffSummary.setText((f'新增 {len(new.keys() - old.keys())} 个 · 删除 {len(old.keys() - new.keys())} 个 · '
                f'修改 {len(changedNodes(flow, previous))} 个节点 · 连线 {len((previous or {}).get("edges", []))} → {len((flow or {}).get("edges", []))} 条'
                '    绿色：新增  红色：删除  黄色：修改') if self.comparison else '当前仅查看一个版本，没有可用的对照版本。')
            self.fitViews(readable=True)
            self.selectedNodeId = None
            self.graphIdentity = identity
        nodes = {n['nodeId']: n for n in (previous or {}).get('nodes', [])}
        nodes.update({n['nodeId']: n for n in (flow or {}).get('nodes', [])})
        self.replaceChoices(self.node, [(nodeName(n), n['nodeId']) for n in nodes.values()],
                            self.node.currentData())
        events = [("未选择执行结果", None)] + [(f"第 {e['sequence']} 条 · {nodeName(nodes[e['nodeId']]) if e['nodeId'] in nodes else e['nodeId']} · {eventName(e['eventType'])}", e)
                   for e in self.data["events"] if e["workflowId"] == key]
        self.replaceChoices(self.event, events, self.event.currentData())
        self.showDetails()

    def showDetails(self):
        if self.data is None:
            return
        flow = self.data["snapshot"]["payload"]["workflows"].get(self.workflow.currentData())
        previous = (self.comparison or {}).get('snapshot', {}).get('payload', {}).get('workflows', {}).get(self.workflow.currentData())
        event = self.event.currentData()
        nodeId = event["nodeId"] if event and event["nodeId"] else self.node.currentData()
        if event and self.node.currentData() != nodeId:
            self.node.blockSignals(True)
            self.node.setCurrentIndex(self.node.findData(nodeId))
            self.node.blockSignals(False)
        node = next((n for n in (flow or {}).get('nodes', []) if n['nodeId'] == nodeId), None)
        old = next((n for n in (previous or {}).get('nodes', []) if n['nodeId'] == nodeId), None)
        if self.technicalDetails.isChecked():
            self.details.setPlainText(json.dumps(dict(node=node, comparison=old, result=event), ensure_ascii=False, indent=2))
        else:
            html = nodeDetails(node, old, event, comparing=self.comparison is not None)
            if self.comparison and (node or old):
                html += '<h4>连线与位置对照</h4>'
                for label, graph in [('对照版本', previous), ('所选版本', flow)]:
                    html += '<p><b>' + label + '</b></p>'
                    if graph is None or not any(n['nodeId'] == nodeId for n in graph['nodes']):
                        html += '<p>此版本中没有该节点。</p>'
                        continue
                    names = {n['nodeId']: nodeName(n) for n in graph['nodes']}
                    links = nodeConnections(graph, nodeId)
                    html += '<p>' + ('<br>'.join(escape(f'{names.get(a, a)} · {b} → {names.get(c, c)} · {d}') for a, b, c, d in links) or '没有相连的节点') + '</p>'
                    position = graph.get('layout', {}).get('nodePositions', {}).get(nodeId)
                    if position:
                        html += '<p>画布位置：' + escape(displayValue(position)) + '</p>'
            self.details.setHtml(html)
        if nodeId != self.selectedNodeId:
            self.view.focusNode(nodeId)
            self.beforeView.focusNode(nodeId)
            self.selectedNodeId = nodeId

    def nodeSelectionChanged(self):
        self.event.blockSignals(True)
        self.event.setCurrentIndex(0)
        self.event.blockSignals(False)
        self.showDetails()

    def selectNode(self, nodeId):
        self.node.setCurrentIndex(self.node.findData(nodeId))
        self.nodeSelectionChanged()

    def zoomViews(self, factor):
        self.view.zoom(factor)
        self.beforeView.zoom(factor)

    def fitViews(self, *, readable=False):
        self.view.fitGraph(readable=readable)
        self.beforeView.fitGraph(readable=readable)

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
