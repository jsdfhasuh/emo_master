"""Project startup defaults and explicitly temporary launch selection."""
from PySide2.QtCore import Qt
from PySide2.QtGui import QBrush, QColor
from PySide2.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout)
from emo_master.core.workflow.dispatch_contracts import groupSlots
from emo_master.ui.workflow_labels import workflowEntryMarker, workflowEntryToolTip


class RunTargetsDialog(QDialog):
    def __init__(self, workflows, selected, limit, parent=None, *, editLimit=True, entryWorkflowId=None,
                 allowPersist=False, temporary=False, projectTargets=None, document=None):
        super().__init__(parent)
        self.document = document
        self.slotsByEntry = {}
        self.slotErrors = {}
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
                         "循环体、子流程和 Dispatch 自动消费者不必重复勾选。")
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
        layout.addWidget(self.targets, 1)
        self.queueHint = QLabel()
        self.queueHint.setWordWrap(True)
        layout.addWidget(self.queueHint)
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
        self.capacityHint = QLabel()
        self.capacityHint.setWordWrap(True)
        layout.addWidget(self.capacityHint)
        self.notice = QLabel("并发上限保存到工程；关闭限制不等于无限算力。" if editLimit
                             else "并发上限来自已加载的工程；请在 Designer 中修改。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.temporary.toggled.connect(self._refreshMode)
        self.limit.valueChanged.connect(self._refreshMode)
        self.targets.itemChanged.connect(self._refreshEntryMarkers)
        self._refreshEntryMarkers()
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
        self._refreshCapacity()

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
        self.slotsByEntry, self.slotErrors = {}, {}
        consumers = {}
        for key in selected:
            if self.document is None:
                continue
            try:
                # Keep slot identity and traversal identical to Runtime admission.
                self.slotsByEntry[key] = groupSlots(self.document, key)
            except (ValueError, KeyError) as error:
                self.slotErrors[key] = str(error)
                continue
            for slot, (target, _capacity) in self.slotsByEntry[key].items():
                consumers.setdefault(target, []).append(f"{self.workflowNames[key]} · {key} / 槽位 {slot}")
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
                if key in consumers:
                    marker = (marker + " · " if marker else "") + "自动消费者"
                item.setText((f"【{marker}】 " if marker else "") + f"{name}  ·  {key}")
                tip = workflowEntryToolTip(name, key, self.entryWorkflowId, selected)
                if key in consumers:
                    tip += ("\nDispatch 自动消费者：随所选入口启动，无需勾选。\n来源：\n"
                            + "\n".join(consumers[key])
                            + "\n手动勾选会额外启动独立 Job，不会接入原队列。")
                if key in self.slotErrors:
                    tip += "\n队列依赖无法计算：" + self.slotErrors[key]
                item.setToolTip(tip)
                font = item.font()
                font.setBold(bool(role))
                item.setFont(font)
                color = "#dbeafe" if key == self.entryWorkflowId else "#d1fae5"
                item.setBackground(QBrush(QColor(color)) if role else QBrush())
        finally:
            self.targets.blockSignals(previous)
        duplicateCount = len(set(selected) & consumers.keys())
        self.queueHint.setText(
            f"已额外勾选 {duplicateCount} 个自动消费者：将另起独立 Job，不会接入原队列。"
            if duplicateCount else
            "Dispatch 消费者随入口自动启动，无需勾选；每个槽位占用 1 个 Job。")
        self.queueHint.setVisible(bool(consumers))
        self.queueHint.setStyleSheet("color: #b45309;" if duplicateCount else "")
        self._refreshMode()

    def minimumJobCount(self):
        return len(self.selectedWorkflowIds()) + sum(len(slots) for slots in self.slotsByEntry.values())

    def _refreshCapacity(self, *_args):
        selected = self.selectedWorkflowIds()
        required = self.minimumJobCount()
        limit = self.maximum()
        details = []
        for key in selected:
            name = f"{self.workflowNames[key]} · {key}"
            if key in self.slotErrors:
                details.append(f"{name}：队列依赖无法计算\n{self.slotErrors[key]}")
            elif self.document is None:
                details.append(f"{name}：仅统计手动入口，未提供队列依赖。")
            else:
                slots = self.slotsByEntry.get(key, {})
                details.append(f"{name}：1 个入口 Job + {len(slots)} 个自动消费者 Job")
                details.extend(f"  槽位 {slot} → {self.workflowNames.get(target, target)} · {target}"
                               for slot, (target, _capacity) in slots.items())
        details.append("每个入口单独建立运行组；相同消费者在不同槽位或运行组中分别占用 Job。\n"
                       "队列容量是待处理数据条数，不是 Job 数；当前估算未计入 Runtime 已占用容量。")
        self.capacityHint.setToolTip("\n".join(details))
        if not selected:
            text = "尚未选择运行入口。"
        elif self.slotErrors:
            text = f"并发需求暂无法完整计算：{len(self.slotErrors)} 个入口的队列配置待修正（详情见悬停）。"
        elif self.document is None:
            text = f"已选 {len(selected)} 个入口 Job；未提供队列依赖，自动消费者数量未核算。"
        else:
            text = (f"本次启动：{len(selected)} 个入口 Job + {required - len(selected)} 个自动消费者 Job"
                    f" = {required} 个 Job。")
        insufficient = limit is not None and required > limit
        if insufficient:
            text += f"\n并发不足：至少需要 {required} 个 Job，当前上限 {limit}。"
            if not self.editLimit:
                text += "请在 Designer 中修改工程并发上限，或减少运行目标。"
            elif self.allowPersist and not self.saveSelection():
                text += "请取消“仅本次临时使用”后提高上限，或减少运行目标。"
            else:
                text += "请提高上限或减少运行目标。"
        elif selected:
            if self.slotErrors or self.document is None:
                text += "\n已知需求至少 " + str(required) + " 个 Job，完整需求以 Runtime 校验为准。"
            else:
                text += "\n未限制 Job 数量" if limit is None else f"\n并发上限 {limit}，满足本次需求"
                text += "；尚未计入 Runtime 已占用容量。"
        self.capacityHint.setText(text)
        self.capacityHint.setStyleSheet("color: #b45309;" if insufficient or self.slotErrors else "")

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
        if limit is not None and self.minimumJobCount() > limit:
            self.notice.setText(f"本次启动至少需要 {self.minimumJobCount()} 个 Job（含自动消费者），"
                                f"超过并发上限 {limit}，请调整后再确定。")
            return
        super().accept()
