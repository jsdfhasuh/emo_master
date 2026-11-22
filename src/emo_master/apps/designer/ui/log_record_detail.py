"""Read the selected log or debug record without retrieving any other execution."""
from datetime import datetime

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.presenters.node_run_presenter import (
    describePort, executionStatusTitle, frameworkLogMessage,
)
from emo_master.core.contracts.run_inspection import normalizeInspection
from .log_json_tree import LogJsonTree, _VALUE_ROLE, _copyText, _json
from .widgets import OptionalWrapLabel, WrapLabel


_MISSING = object()
_NODE_TITLES = {
    "node.started": "节点开始记录",
    "node.completed": "节点完成记录",
    "node.failed": "节点失败记录",
    "node.skipped": "节点跳过记录",
    "node.log": "算子主动日志",
    "node.preview.failed": "实时预览失败",
}
_FRAMEWORK_TITLES = {
    "job.accepted": "任务已受理", "job.started": "任务开始执行", "job.stopping": "任务正在停止",
    "job.completed": "任务已完成", "job.failed": "任务失败", "job.aborted": "任务已停止",
    "workflow.started": "工作流开始", "workflow.completed": "工作流完成", "workflow.failed": "工作流失败",
    "loop.started": "循环开始", "loop.completed": "循环结束", "loop.failed": "循环失败",
    "loop.timeout": "循环超时", "loop.limit_reached": "循环达到次数上限",
    "loop.iteration.started": "该轮开始", "loop.iteration.completed": "该轮结束",
}
_DEBUG_EVENT_TITLES = {
    "session.opened": "调试会话已建立",
    "session.ready": "调试会话就绪",
    "session.retired": "调试会话已退出",
    "session.closed": "调试会话已结束",
    "execution.started": "调试执行开始",
    "execution.finished": "调试执行结束",
    "logs.dropped": "调试日志有丢弃",
}
_MESSAGE_PREVIEW_CHARS = 220


def _timeText(value):
    if type(value) is int and value > 0:
        try:
            return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        except (ValueError, OSError, OverflowError):
            pass
    return "未记录"


def _scalarText(value):
    return value if isinstance(value, str) and value else _json(value)


def _tableColumns(value):
    if not isinstance(value, list) or not value or not isinstance(value[0], dict) or not value[0]:
        return ()
    columns = tuple(value[0])
    keys = value[0].keys()
    return columns if all(
        isinstance(row, dict) and row.keys() == keys
        and all(cell is None or isinstance(cell, (str, bool, int, float)) for cell in row.values())
        for row in value
    ) else ()


class _ReadableTree(LogJsonTree):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logReadableDetails")
        self.setAccessibleName("日志易读详情")
        self.setColumnHidden(2, True)
        self.setColumnWidth(0, 150)
        self.setToolTip("展开字段查看收到的数据。双击长文本查看内容；右键复制当前值。")

    def appendValue(self, parent, name, value):
        item = super().appendValue(parent, name, value)
        if isinstance(value, str):
            text = _scalarText(value)
            preview = text[:_MESSAGE_PREVIEW_CHARS]
            if len(preview) < len(text):
                preview += "…"
                item.setData(1, Qt.UserRole + 1, value)
            item.setText(1, preview)
            item.setToolTip(1, text[:2000])
        elif isinstance(value, (dict, list)) and not value:
            item.setText(1, _json(value))
        return item

    def field(self, label, value, *, description=None, expand=False, parent=None):
        if parent is None:
            parent = self.invisibleRootItem()
        if value is _MISSING:
            return QTreeWidgetItem(parent, [label, description or "本记录未提供"])
        item = self.appendValue(parent, label, value)
        if description is not None:
            item.setText(1, description)
        if expand:
            item.setExpanded(True)
        return item

    def selectedValue(self):
        item = self.currentItem()
        boxed = item.data(0, _VALUE_ROLE) if item is not None else None
        return boxed[0] if boxed is not None else _MISSING


def debugEventText(wrapper):
    """The wrapper type names transport notifications, the inner type names a log."""
    inner = wrapper.get("event")
    if isinstance(inner, dict):
        message = _eventMessage(inner)
        return " · ".join(str(value) for value in (inner.get("level"), inner.get("code"), message) if value) or str(inner.get("eventType", ""))
    kind = wrapper.get("type", "")
    title = _DEBUG_EVENT_TITLES.get(kind, kind)
    status = wrapper.get("status")
    if kind == "execution.finished":
        status = executionStatusTitle(status) or status
    return " · ".join(str(value) for value in (title, status, wrapper.get("code"), wrapper.get("message")) if value)


def _eventMessage(event):
    return frameworkLogMessage(event.get("eventType", ""), event.get("message", ""),
                               nodeId=event.get("nodeId", ""), workflowId=event.get("workflowId", ""),
                               iterationPath=event.get("iterationPath"), payload=event.get("payload"))


class LogRecordDetail(QWidget):
    """Shared Designer reader for a Runtime event, debug wrapper, or debug result."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logRecordDetail")
        self._copyData = _MISSING
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.title = WrapLabel()
        bold = self.title.font()
        bold.setBold(True)
        self.title.setFont(bold)
        root.addWidget(self.title)
        self.message = OptionalWrapLabel()
        root.addWidget(self.message)
        self.notice = OptionalWrapLabel()
        root.addWidget(self.notice)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.readable = _ReadableTree()
        self.rawTree = LogJsonTree()
        self.tabs.addTab(self.readable, "易读详情")
        self.tabs.addTab(self.rawTree, "原始 JSON")
        self.tabs.setTabToolTip(1, "查看当前收到的记录")
        root.addWidget(self.tabs, 1)

        actions = QGridLayout()
        self.copyValueButton = QPushButton("复制选中值")
        self.copyValueButton.clicked.connect(self.copySelectedValue)
        self.copyDataButton = QPushButton("复制日志数据")
        self.copyDataButton.clicked.connect(lambda: _copyText(_json(self._copyData)))
        self.copyRecordButton = QPushButton("复制当前记录")
        self.copyRecordButton.clicked.connect(self.rawTree.copyJson)
        self.arrayTableButton = QPushButton("表格查看")
        self.arrayTableButton.clicked.connect(self.showArrayTable)
        actions.addWidget(self.copyValueButton, 0, 0)
        actions.addWidget(self.copyRecordButton, 0, 1)
        actions.addWidget(self.arrayTableButton, 0, 2)
        actions.addWidget(self.copyDataButton, 1, 0)
        actions.setColumnStretch(3, 1)
        root.addLayout(actions)
        self.readable.currentItemChanged.connect(self._updateActions)
        self.rawTree.currentItemChanged.connect(self._updateActions)
        self.tabs.currentChanged.connect(self._updateActions)
        self.clear()

    def clear(self, title="选择记录查看详情"):
        self._copyData = _MISSING
        self.title.setText(title)
        self.message.setText("")
        self.notice.setText("")
        self.readable.clear()
        self.rawTree.clear()
        self.copyRecordButton.setEnabled(False)
        self._updateActions()

    def _startRecord(self, record, title, message="", *, originalMessage=None):
        self.clear(title)
        source = str(message)
        preview = "\n".join(source.splitlines()[:3])[:_MESSAGE_PREVIEW_CHARS]
        self.message.setText(preview + "…" if len(preview) < len(source) else preview)
        original = source if originalMessage is None else str(originalMessage)
        if len(preview) < len(source) or len(original) > _MESSAGE_PREVIEW_CHARS:
            self.readable.field("消息", original)
        self.rawTree.setPayload(record)
        self.copyRecordButton.setEnabled(True)

    def _finishRecord(self):
        if self.readable.topLevelItemCount():
            self.readable.setCurrentItem(self.readable.topLevelItem(0))
        self._updateActions()

    def setEvent(self, record, *, displayMessage=None, context=None):
        self._showEvent(record, record, displayMessage=displayMessage, context=context, includeTime=True)

    def _showEvent(self, raw, event, *, displayMessage=None, context=None, includeTime=False):
        kind = event.get("eventType", "")
        title = _NODE_TITLES.get(kind) or _FRAMEWORK_TITLES.get(kind) or kind or "日志记录"
        message = event.get("message", "")
        readable = _eventMessage(event) if displayMessage is None else displayMessage
        if kind in _FRAMEWORK_TITLES and readable == title and message != readable:
            readable = ""
        if event.get("level"):
            title = f"{event['level']} · {title}"
        self._startRecord(raw, title, readable, originalMessage=message)
        if includeTime:
            self.readable.field("发生时间", event.get("timestampMs", _MISSING), description=_timeText(event.get("timestampMs")))
        if event.get("code"):
            self.readable.field("日志代码", event["code"])
        payload = event.get("payload")
        if isinstance(payload, dict):
            self._eventPayload(kind, payload)
        elif "payload" in event:
            self.readable.field("数据", payload, expand=True)
        info = {label: event[key] for key, label in (
            ("projectId", "工程"), ("jobId", "Job"), ("workflowId", "工作流"),
            ("workflowRunId", "工作流调用"), ("parentWorkflowRunId", "父工作流调用"),
            ("nodeId", "节点"), ("nodeRunId", "节点执行"), ("deviceId", "设备"),
            ("runtimeInstanceId", "Runtime 实例"), ("eventId", "事件 ID"),
        ) if event.get(key) not in (None, "")}
        if "iterationPath" in event:
            info["迭代路径（索引）"] = event["iterationPath"]
        if context:
            info.update(context)
        if info:
            self.readable.field("记录身份", info)
        self._finishRecord()

    def _eventPayload(self, kind, payload):
        remaining = dict(payload)
        if payload.get("truncated") is True:
            self.notice.setText("该记录标记为已截断。详情与复制使用当前收到的数据。")
        nodeRecord = kind in {"node.started", "node.completed", "node.failed", "node.skipped"}
        if (nodeRecord or kind in _FRAMEWORK_TITLES) and "status" in remaining:
            value = remaining.pop("status")
            self.readable.field("状态", value, description=executionStatusTitle(value))
        if nodeRecord:
            for key, label in (("message", "跳过原因" if kind == "node.skipped" else "原因"),
                               ("code", "记录代码"), ("reason", "原因"), ("missingInputs", "缺少输入"),
                               ("blockedByBranches", "未选中的上游分支")):
                if key in remaining:
                    self.readable.field(label, remaining.pop(key))
            source = remaining.pop("ioSummary", None)
            summary = normalizeInspection(source)
            reason = source.get("unavailableReason") if isinstance(source, dict) else None
            self._ioSide("输入摘要", summary["inputs"], reason)
            self._ioSide("输出摘要", summary["outputs"], reason)
            for key, label in (("metrics", "指标"), ("diagnostics", "诊断")):
                value = remaining.pop(key, _MISSING)
                self.readable.field(label, value, description="{} · 无附加" + label if value == {} else None, expand=True)
            # Old streams may carry outputs; only the supplied IO description is
            # interpreted as an execution summary. The original stays in the raw tab.
            remaining.pop("outputs", None)
        elif kind == "node.log":
            if "phase" in remaining:
                phase = remaining.pop("phase")
                phaseTitle = {"execute": "执行", "lifecycle": "生命周期"}.get(phase) if isinstance(phase, str) else None
                self.readable.field("阶段", phase, description=phaseTitle)
            self._copyData = remaining.pop("data", _MISSING)
            self.readable.field("日志数据", self._copyData, expand=True)
        elif kind == "node.preview.failed" and "logs" in remaining:
            self.readable.field("预览附带日志", remaining.pop("logs"), expand=True)
        if remaining:
            label = "附加字段" if kind in _NODE_TITLES else "记录数据"
            self.readable.field(label, remaining, expand=kind not in _NODE_TITLES)

    def _ioSide(self, label, batch, unavailableReason):
        if batch is None:
            self.readable.field(label, _MISSING, description=unavailableReason if isinstance(unavailableReason, str) else None)
            return
        # The normalized descriptors are concise summaries of this record's
        # ports. They carry no image or collection contents to retrieve.
        count = batch["portCount"]
        parent = QTreeWidgetItem(self.readable, [label, f"{count} 个端口" if count else "无端口值"])
        for row in batch["items"]:
            value = row["value"]
            if value["kind"] == "scalar":
                text = _scalarText(value["value"]) + (" · 已截断" if value.get("truncated") else "")
                value = value["value"]
            else:
                text = describePort(value)
                if value.get("dtype"):
                    text += f" · dtype {value['dtype']}"
            self.readable.field(row["port"], value, description=text[:500], parent=parent)
        if batch["omitted"]:
            self.readable.field("省略端口", batch["omitted"], parent=parent)
        parent.setExpanded(True)

    def setDebugEvent(self, wrapper, sessionId, *, displayMessage=None):
        if isinstance(wrapper, str):
            self._startRecord(wrapper, "调试窗口反馈", wrapper)
            self._finishRecord()
            return
        identity = {"调试会话": sessionId}
        for key, label in (("generation", "代次"), ("executionId", "调试执行"), ("sequence", "序号")):
            if wrapper.get(key) not in (None, ""):
                identity[label] = wrapper[key]
        inner = wrapper.get("event")
        if isinstance(inner, dict):
            self._showEvent(wrapper, inner, displayMessage=displayMessage, context=identity)
            return
        kind = wrapper.get("type", "")
        self._startRecord(wrapper, _DEBUG_EVENT_TITLES.get(kind, kind or "会话通知"),
                          wrapper.get("message", "") if displayMessage is None else displayMessage)
        fields = {key: value for key, value in wrapper.items() if key not in {"type", "sequence", "generation", "executionId"}}
        if kind == "execution.finished" and "status" in fields:
            value = fields.pop("status")
            self.readable.field("状态", value, description=executionStatusTitle(value))
        if fields:
            self.readable.field("通知数据", fields, expand=True)
        self.readable.field("记录身份", identity)
        self._finishRecord()

    def setDebugResult(self, record, *, sessionId, generation):
        self._startRecord(record, "调试执行结果", record.get("message", ""))
        for key, label in (
            ("status", "状态"), ("code", "结果代码"), ("elapsedMs", "耗时（毫秒）"),
            ("outputs", "已返回输出"), ("outputAssets", "输出资产"), ("metrics", "指标"),
            ("diagnostics", "诊断"), ("rawParams", "提交参数"), ("effectiveParams", "生效参数"),
            ("resolvedParams", "解析参数"), ("inputSources", "输入来源"), ("bindingSources", "绑定来源"),
        ):
            if key in record:
                value = record[key]
                self.readable.field(label, value, description=executionStatusTitle(value) if key == "status" else None,
                                    expand=key in {"outputs", "metrics", "diagnostics"})
        identity = {"调试会话": sessionId, "代次": generation}
        if "executionId" in record:
            identity["调试执行"] = record["executionId"]
        self.readable.field("记录身份", identity)
        self._finishRecord()

    def _updateActions(self, *_):
        active = self.tabs.currentWidget()
        current = active.currentItem()
        self.copyValueButton.setEnabled(current is not None and current.data(0, _VALUE_ROLE) is not None)
        self.copyDataButton.setVisible(self._copyData is not _MISSING)
        self.arrayTableButton.setVisible(active is self.readable and bool(_tableColumns(self.readable.selectedValue())))

    def copySelectedValue(self):
        self.tabs.currentWidget().copySelectedValue()

    def showArrayTable(self):
        values = self.readable.selectedValue()
        columns = _tableColumns(values)
        if not columns:
            return
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle(self.readable.currentItem().text(0))
        dialog.resize(650, 400)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, len(columns))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setHorizontalHeaderLabels(columns)
        layout.addWidget(table)
        controls = QHBoxLayout()
        previous = QPushButton("上一页")
        nextPage = QPushButton("下一页")
        count = QLabel()
        copy = QPushButton("复制选中值")
        for widget in (previous, nextPage, count, copy):
            controls.addWidget(widget)
        layout.addLayout(controls)
        offset = 0
        pageSize = 50

        def showPage(delta):
            nonlocal offset
            offset += delta * pageSize
            page = values[offset:offset + pageSize]
            table.setRowCount(len(page))
            table.setVerticalHeaderLabels([str(index + 1) for index in range(offset, offset + len(page))])
            for index, row in enumerate(page):
                for column, key in enumerate(columns):
                    cell = QTableWidgetItem(_scalarText(row[key]))
                    cell.setToolTip(_scalarText(row[key]))
                    table.setItem(index, column, cell)
            previous.setEnabled(offset > 0)
            nextPage.setEnabled(offset + len(page) < len(values))
            count.setText(f"{offset + 1}–{offset + len(page)} / {len(values)}")

        def copyCell():
            row, column = table.currentRow(), table.currentColumn()
            if row >= 0 and column >= 0:
                _copyText(_json(values[offset + row][columns[column]]))

        previous.clicked.connect(lambda: showPage(-1))
        nextPage.clicked.connect(lambda: showPage(1))
        copy.clicked.connect(copyCell)
        showPage(0)
        dialog.open()
