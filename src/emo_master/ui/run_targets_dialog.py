"""Shared transient workflow selection; no persisted ownership schema."""
from PySide2.QtCore import Qt
from PySide2.QtGui import QBrush, QColor
from PySide2.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QListWidget, QListWidgetItem, QSpinBox, QVBoxLayout)
from emo_master.ui.workflow_labels import workflowEntryMarker, workflowEntryToolTip


class RunTargetsDialog(QDialog):
    def __init__(self, workflows, selected, limit, parent=None, *, editLimit=True, entryWorkflowId=None):
        super().__init__(parent)
        self.entryWorkflowId = entryWorkflowId
        self.workflowNames = {}
        self.setWindowTitle("运行目标与并发")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        helpText = QLabel("“默认入口”是工程默认启动流程；勾选项是本次独立运行入口。\n"
                         "勾选多个入口，点击一次“开始”并行运行；取消默认入口的勾选后，本次不启动它。\n"
                         "只选择需要独立运行的入口；不要同时勾选它们的循环体或子流程。\n"
                         "此选择仅用于本次打开的工程，不添加工位或页面归属配置。")
        helpText.setWordWrap(True)
        layout.addWidget(helpText)
        self.targets = QListWidget()
        for key, workflow in workflows.items():
            name = workflow.name if hasattr(workflow, "name") else workflow.get("name", key)
            self.workflowNames[key] = name
            item = QListWidgetItem(f"{name}  ·  {key}")
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if key in selected else Qt.Unchecked)
            self.targets.addItem(item)
        self.targets.itemChanged.connect(self._refreshEntryMarkers)
        self._refreshEntryMarkers()
        layout.addWidget(self.targets, 1)
        form = QFormLayout()
        self.unlimited = QCheckBox("不限制 Job 数量（仍受机器和设备资源限制）")
        self.unlimited.setChecked(limit is None)
        self.limit = QSpinBox()
        self.limit.setRange(1, 2147483647)
        self.limit.setValue(limit or 2)
        self.limit.setEnabled(editLimit and limit is not None)
        self.unlimited.setEnabled(editLimit)
        self.unlimited.toggled.connect(lambda checked: self.limit.setEnabled(editLimit and not checked))
        form.addRow("最大并发 Job", self.limit)
        form.addRow(self.unlimited)
        layout.addLayout(form)
        self.notice = QLabel("并发上限保存到工程；关闭限制不等于无限算力。" if editLimit
                             else "并发上限来自已加载的工程；请在 Designer 中修改。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refreshEntryMarkers(self, *_args):
        selected = self.selectedWorkflowIds()
        previous = self.targets.blockSignals(True)
        try:
            for index in range(self.targets.count()):
                item = self.targets.item(index)
                key = item.data(Qt.UserRole)
                name = self.workflowNames[key]
                role = workflowEntryMarker(key, self.entryWorkflowId, selected)
                marker = role
                if key == self.entryWorkflowId:
                    marker += " · 已选" if key in selected else " · 未选"
                item.setText((f"【{marker}】 " if marker else "") + f"{name}  ·  {key}")
                item.setToolTip(workflowEntryToolTip(name, key, self.entryWorkflowId, selected))
                font = item.font()
                font.setBold(bool(role))
                item.setFont(font)
                color = "#dbeafe" if key == self.entryWorkflowId else "#d1fae5"
                item.setBackground(QBrush(QColor(color)) if role else QBrush())
        finally:
            self.targets.blockSignals(previous)

    def selectedWorkflowIds(self):
        return [self.targets.item(i).data(Qt.UserRole) for i in range(self.targets.count())
                if self.targets.item(i).checkState() == Qt.Checked]

    def maximum(self):
        return None if self.unlimited.isChecked() else self.limit.value()

    def accept(self):
        if not self.selectedWorkflowIds():
            self.notice.setText("请至少选择一个运行目标。")
            return
        limit = self.maximum()
        if limit is not None and len(self.selectedWorkflowIds()) > limit:
            self.notice.setText("所选流程数超过最大并发数，请调整后再开始。")
            return
        super().accept()
