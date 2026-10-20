"""Project startup defaults and explicitly temporary launch selection."""
from PySide2.QtCore import Qt
from PySide2.QtGui import QBrush, QColor
from PySide2.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout)
from emo_master.ui.workflow_labels import workflowEntryMarker, workflowEntryToolTip


class RunTargetsDialog(QDialog):
    def __init__(self, workflows, selected, limit, parent=None, *, editLimit=True, entryWorkflowId=None,
                 allowPersist=False, temporary=False, projectTargets=None):
        super().__init__(parent)
        self.entryWorkflowId = entryWorkflowId
        self.allowPersist = allowPersist
        self.editLimit = editLimit
        self.projectTargets = list(projectTargets) if projectTargets is not None else None
        self.originalLimit = limit
        self.workflowNames = {}
        self.setWindowTitle("运行目标与并发")
        self.resize(600, 560)
        layout = QVBoxLayout(self)
        helpText = QLabel("勾选的入口会作为独立 Job 并行启动；不必勾选默认入口。\n"
                         "不要同时勾选这些入口内部的循环体或子流程。")
        helpText.setWordWrap(True)
        layout.addWidget(helpText)
        self.targets = QListWidget()
        self.targets.setMinimumHeight(110)
        self.targets.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.targets.setTextElideMode(Qt.ElideRight)
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
        self.temporary = QCheckBox("仅本次临时使用（不保存）")
        self.temporary.setToolTip("不修改工程启动列表或并发上限；重新打开工程时恢复工程配置。")
        self.temporary.setChecked(temporary or not allowPersist)
        self.temporary.setEnabled(allowPersist)
        selectionMode = QHBoxLayout()
        selectionMode.addWidget(self.temporary, 1)
        self.restoreButton = QPushButton("恢复工程启动列表")
        self.restoreButton.setEnabled(self.projectTargets is not None)
        self.restoreButton.clicked.connect(self.restoreProjectTargets)
        selectionMode.addWidget(self.restoreButton)
        layout.addLayout(selectionMode)
        form = QFormLayout()
        self.unlimited = QCheckBox("不限制 Job 数量（仍受机器和设备资源限制）")
        self.unlimited.setChecked(limit is None)
        self.limit = QSpinBox()
        self.limit.setRange(1, 2147483647)
        self.limit.setValue(limit or 2)
        self.limit.setEnabled(editLimit and limit is not None)
        self.unlimited.setEnabled(editLimit)
        self.unlimited.toggled.connect(self._refreshMode)
        form.addRow("最大并发 Job", self.limit)
        form.addRow(self.unlimited)
        layout.addLayout(form)
        self.notice = QLabel("并发上限保存到工程；关闭限制不等于无限算力。" if editLimit
                             else "并发上限来自已加载的工程；请在 Designer 中修改。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.temporary.toggled.connect(self._refreshMode)
        self._refreshMode()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def saveSelection(self):
        return self.allowPersist and not self.temporary.isChecked()

    def _refreshMode(self, *_args):
        editable = self.editLimit and (not self.allowPersist or self.saveSelection())
        self.unlimited.setEnabled(editable)
        self.limit.setEnabled(editable and not self.unlimited.isChecked())
        self.notice.setText("确定后修改启动配置；保存工程后保留，支持撤销。"
                            if self.saveSelection() else
                            "临时选择不写入工程；重开工程或恢复列表即可还原。")

    def restoreProjectTargets(self):
        if self.projectTargets is None:
            return
        for index in range(self.targets.count()):
            item = self.targets.item(index)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in self.projectTargets else Qt.Unchecked)
        self.temporary.setChecked(not self.allowPersist)
        self.unlimited.setChecked(self.originalLimit is None)
        self.limit.setValue(self.originalLimit or 2)
        self._refreshMode()

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
        if self.allowPersist and not self.saveSelection():
            return self.originalLimit
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
