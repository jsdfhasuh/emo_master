"""Whole-project migration inventory using the normal per-node binding wizard."""
from copy import deepcopy

from PySide2.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout)


class DeviceMigrationDialog(QDialog):
    def __init__(self, coordinator, requirements, parent=None):
        super().__init__(parent)
        self.c, self.requirements = coordinator, requirements
        self.session = coordinator.session
        self.setWindowTitle("旧设备节点迁移预览")
        self.resize(920, 560)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["工作流 / 节点", "算子", "设备绑定", "原参数 / 来源"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((180, 230, 180)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        layout.addWidget(self.details)
        self.table.itemSelectionChanged.connect(self.showDetails)
        self.configure = QPushButton("配置所选节点的设备与参数来源")
        self.configure.clicked.connect(self.configureSelected)
        layout.addWidget(self.configure)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rows = []
        self.refresh()

    def refresh(self):
        import json
        self.rows = [(wid, deepcopy(node)) for wid, flow in self.session.workflows.workflows.items()
                     for node in flow.nodes if node.get("operatorId") in self.requirements]
        pending = sum(not n.get("deviceBindings") for _wid, n in self.rows)
        self.status.setText(f"设备节点 {len(self.rows)} 个；未绑定 {pending} 个。修改仅进入草稿；保存时保留原工程 .bak。")
        self.table.setRowCount(len(self.rows))
        for row, (wid, node) in enumerate(self.rows):
            values = [wid + "/" + node["nodeId"], node["operatorId"],
                      json.dumps(node.get("deviceBindings") or "待迁移", ensure_ascii=False),
                      json.dumps({key: node.get(key, {}) for key in (
                          "params", "deviceOverrides", "deviceValueBindings", "globalVariableBindings")}, ensure_ascii=False)]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, col, item)
        if self.rows:
            self.table.selectRow(0)

    def showDetails(self):
        import json
        row = self.table.currentRow()
        self.details.setPlainText(json.dumps(self.rows[row][1], ensure_ascii=False, indent=2)
                                  if 0 <= row < len(self.rows) else "")

    def configureSelected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows) or self.c.session is not self.session:
            return
        wid, node = self.rows[row]
        window = self.c.window
        window.activateWorkflow(wid)
        from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog
        definition = window._operatorDefinition(node["operatorId"])
        if not node.get("paramSchema"):
            node["paramSchema"] = definition.get("paramSchema", {})
        requirements = {key: value.model_dump() if hasattr(value, "model_dump") else value
                        for key, value in self.requirements[node["operatorId"]].items()}
        dialog = DeviceNodeDialog(self.c, node, requirements, self, migrateLegacy=True)
        dialog.exec_()
        self.refresh()
