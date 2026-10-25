"""Offline structured target/mapping editor for built-in Dispatch and Join."""
from copy import deepcopy
from uuid import uuid4

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument, WorkflowNode
from emo_master.core.workflow.dispatch_contracts import dispatchPorts, groupSlots
from emo_master.core.workflow.loop_contracts import interfacePortTypes


class DispatchEditor(QDialog):
    def __init__(self, payload, workflowId, kind, parent=None, *, nodeId=None):
        super().__init__(parent)
        self.payload = migrateProjectPayload(payload, enableDevices=True)
        self.workflowId, self.kind = workflowId, kind
        self.nodeId = nodeId or "node-" + uuid4().hex
        self.resultPayload = None
        self.setWindowTitle("Dispatch 目标与映射" if kind == "dispatch" else "Join 汇合")
        self.resize(720, 480)
        self.setMinimumSize(480, 320)
        layout = QVBoxLayout(self)
        nodes = self.payload["workflows"][workflowId]["nodes"]
        current = next((n for n in nodes if n["nodeId"] == nodeId), {})
        self.tabs = QTabWidget()
        self.rows = []
        if kind == "dispatch":
            params = current.get("params", {})
            policyForm = QFormLayout()
            self.queuePolicy = QComboBox()
            self.queuePolicy.addItem("报错", "error")
            self.queuePolicy.addItem("等待容量", "wait")
            self.queuePolicy.addItem("跳过本次投递", "skip")
            self.queuePolicy.setCurrentIndex(self.queuePolicy.findData(params.get("queueFullPolicy", "error")))
            self.waitTimeout = QSpinBox()
            self.waitTimeout.setRange(0, 2147483647)
            self.waitTimeout.setSpecialValueText("等待至有容量或取消")
            self.waitTimeout.setSuffix(" ms")
            self.waitTimeout.setValue(params.get("waitTimeoutMs", 0))
            policyForm.addRow("队列满时", self.queuePolicy)
            policyForm.addRow("等待超时", self.waitTimeout)
            def updatePolicy():
                visible = self.queuePolicy.currentData() == "wait"
                self.waitTimeout.setVisible(visible)
                policyForm.labelForField(self.waitTimeout).setVisible(visible)
            self.queuePolicy.currentIndexChanged.connect(updatePolicy)
            updatePolicy()
            layout.addLayout(policyForm)
            toolbar = QHBoxLayout()
            for glyph, label, callback in (("plus", "添加目标槽", self.addTarget), ("trash-2", "删除目标槽", self.removeTarget)):
                button = QPushButton()
                button.setIcon(icon(glyph))
                button.setToolTip(label)
                button.setAccessibleName(label)
                button.clicked.connect(callback)
                toolbar.addWidget(button)
            toolbar.addStretch()
            layout.addLayout(toolbar)
            layout.addWidget(self.tabs)
            for target in current.get("params", {}).get("targets", [{}]):
                self.addTarget(target)
        else:
            form = QFormLayout()
            self.dispatch = QComboBox()
            for node in nodes:
                if node["kind"] == "dispatch":
                    self.dispatch.addItem(node.get("displayName") or node["nodeId"], node["nodeId"])
            selected = current.get("params", {}).get("dispatchNodeId")
            if selected:
                self.dispatch.setCurrentIndex(self.dispatch.findData(selected))
            self.timeout = QSpinBox()
            self.timeout.setRange(1, 1200000)
            self.timeout.setSuffix(" ms")
            self.timeout.setValue(current.get("params", {}).get("timeoutMs", 30000))
            form.addRow("分发节点", self.dispatch)
            form.addRow("等待超时", self.timeout)
            layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def addTarget(self, target=None):
        target = target if isinstance(target, dict) else {}
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        slot = QLineEdit(target.get("targetSlotId", "slot-" + uuid4().hex[:8]))
        workflow = QComboBox()
        for key, value in self.payload["workflows"].items():
            if key != self.workflowId:
                workflow.addItem(value["name"] + " (" + key + ")", key)
        if target.get("workflowId"):
            workflow.setCurrentIndex(workflow.findData(target["workflowId"]))
        capacity = QSpinBox()
        capacity.setRange(1, 64)
        capacity.setValue(target.get("queueCapacity", 4))
        form.addRow("目标槽 ID", slot)
        form.addRow("工作流", workflow)
        form.addRow("等待队列容量", capacity)
        layout.addLayout(form)
        mapping = QTableWidget(0, 3)
        mapping.setHorizontalHeaderLabels(["目标输入", "类型", "Dispatch 输入"])
        mapping.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(mapping)
        def refresh():
            value = self.payload["workflows"].get(workflow.currentData(), {})
            ports = interfacePortTypes(value.get("inputs", {}))
            mapping.setRowCount(len(ports))
            for row, (key, kind) in enumerate(ports.items()):
                for column, text in enumerate((key, kind, target.get("inputs", {}).get(key, key))):
                    item = QTableWidgetItem(text)
                    if column < 2:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    mapping.setItem(row, column, item)
        workflow.currentIndexChanged.connect(refresh)
        refresh()
        self.rows.append((page, slot, workflow, capacity, mapping))
        self.tabs.addTab(page, slot.text())
        slot.textChanged.connect(lambda text: self.tabs.setTabText(self.tabs.indexOf(page), text))
        self.tabs.setCurrentWidget(page)

    def removeTarget(self):
        page = self.tabs.currentWidget()
        self.rows = [row for row in self.rows if row[0] is not page]
        self.tabs.removeTab(self.tabs.currentIndex())
        if page is not None:
            page.deleteLater()

    def candidate(self):
        value = deepcopy(self.payload)
        nodes = value["workflows"][self.workflowId]["nodes"]
        current = next((n for n in nodes if n["nodeId"] == self.nodeId), None)
        node = deepcopy(current) if current else dict(nodeId=self.nodeId, kind=self.kind, displayName=self.kind.title())
        if self.kind == "dispatch":
            targets, inputs = [], {}
            for _page, slot, workflow, capacity, table in self.rows:
                mapping = {}
                for row in range(table.rowCount()):
                    port, kind, source = (table.item(row, column).text().strip() for column in range(3))
                    if not source or (source in inputs and inputs[source] != kind):
                        raise ValueError("输入名称为空或共享输入类型不一致")
                    inputs[source], mapping[port] = kind, source
                targets.append(dict(targetSlotId=slot.text().strip(), workflowId=workflow.currentData(),
                                    queueCapacity=capacity.value(), inputs=mapping))
            params = dict(targets=targets)
            if self.queuePolicy.currentData() == "wait":
                params.update(queueFullPolicy="wait", waitTimeoutMs=self.waitTimeout.value())
            elif self.queuePolicy.currentData() == "skip":
                params.update(queueFullPolicy="skip")
            node.update(params=params, inputPorts=inputs)
        else:
            node.update(params=dict(dispatchNodeId=self.dispatch.currentData(), timeoutMs=self.timeout.value()))
        if current is not None:
            nodes[nodes.index(current)] = node
        else:
            nodes.append(node)
        document = ProjectDocument.model_validate(value)
        node["inputPorts"], node["outputPorts"] = dispatchPorts(document, self.workflowId, WorkflowNode.model_validate(node))
        groupSlots(document, self.workflowId)
        return value

    def accept(self):
        try:
            self.resultPayload = self.candidate()
        except (ValueError, KeyError, TypeError) as error:
            self.error.setText(str(error))
            return
        super().accept()
