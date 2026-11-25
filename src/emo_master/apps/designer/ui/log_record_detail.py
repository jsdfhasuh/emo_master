"""Read the selected log or debug record without retrieving any other execution."""
from collections.abc import Mapping
from datetime import datetime
from itertools import chain, islice
from pathlib import PureWindowsPath
import re

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.presenters.node_run_presenter import (
    describePort, executionStatusTitle, frameworkLogMessage,
)
from emo_master.core.contracts.run_inspection import normalizeInspection
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID as SQLITE_WRITER_ID, receiptSummary
from emo_master.core.plugin.result_schema import parameterSchemaAtPath
from emo_master.core.project.node_input_files import NODE_INPUT_FILES
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
_SEMANTICS_ROLE = Qt.UserRole + 11
_METRIC_GROUPS = {
    "duration": "算子内部耗时",
    "business": "业务统计",
    "result": "计算结果",
    "parameter": "实际处理参数",
}
_PARAMETER_SOURCES = {
    "prepared": "准备后的节点参数",
    "variable": "变量覆盖",
    "resource": "Runtime 资源解析",
    "input": "输入覆盖",
    "normalized": "算子内部归一化",
    "device": "设备解析",
    "automatic": "自动选择",
}


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _backgroundSqliteReceipt(event):
    payload = _mapping(event.get("payload"))
    if event.get("eventType") != "sqlite.write.finished" or payload.get("background") is not True:
        return None
    receipt = payload.get("receipt")
    identity = {key: event[key] for key in ("jobId", "workflowId", "workflowRunId", "nodeId", "nodeRunId")
                if key in event}
    summary = receiptSummary(receipt, identity)
    return receipt if summary is not None and summary["status"] != "QUEUED" else None


def _debugSqliteReceiptPair(record, definition):
    if definition.get("operatorId") != SQLITE_WRITER_ID:
        return False
    submitted = _mapping(record.get("outputs")).get("receipt")
    if _mapping(submitted).get("status") != "QUEUED" or not record.get("executionId"):
        return False
    queued = receiptSummary(submitted, {"nodeRunId": record["executionId"]})
    if queued is None:
        return False
    final = receiptSummary(_mapping(record.get("diagnostics")).get("sqliteReceipt"), submitted["execution"])
    return final is not None and final["writeId"] == queued["writeId"] and final["status"] != "QUEUED"


def _groupedFields(fields, values, groups, prefix=""):
    values = _mapping(values)
    for key, descriptor in _mapping(fields).items():
        if key not in values:
            continue
        value = values[key]
        path = prefix + key
        if descriptor.get("group") in groups:
            yield path, value, descriptor
        elif isinstance(value, Mapping) and descriptor.get("fields"):
            yield from _groupedFields(descriptor["fields"], value, groups, path + ".")


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
        fields = _mapping(parent.data(0, _SEMANTICS_ROLE))
        rawParent = parent.data(0, _VALUE_ROLE)
        children = _mapping(fields.get("fields") or fields.get("properties"))
        if rawParent and isinstance(rawParent[0], list):
            descriptor = fields.get("items")
        else:
            descriptor = children.get(name, _mapping(fields.get("xFieldDescriptions")).get(name))
            additional = fields.get("additionalProperties")
            if descriptor is None and isinstance(additional, Mapping):
                return self._namedValue(parent, name, value, additional, keepName=True)
        return self._namedValue(parent, name, value, _mapping(descriptor))

    def _namedValue(self, parent, name, value, descriptor, *, summarized=False, keepName=False):
        title = descriptor.get("title")
        label = str(name)
        if not keepName and isinstance(title, str) and title != name:
            label = f"{title}（{name}）"
        item = super().appendValue(parent, label, value)
        if descriptor and not summarized:
            item.setData(0, _SEMANTICS_ROLE, dict(descriptor))
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
        if descriptor:
            unit = descriptor.get("unit") or descriptor.get("xUnit")
            if isinstance(unit, str) and type(value) in (int, float):
                item.setText(1, f"{item.text(1)} {unit}")
            hint = "\n".join(text for text in (
                label, descriptor.get("description"), f"单位：{unit}" if unit else None
            ) if isinstance(text, str) and text)
            item.setToolTip(0, hint)
        return item

    def field(self, label, value, *, description=None, expand=False, parent=None,
              semantics=None, summarized=False, suffix=""):
        if parent is None:
            parent = self.invisibleRootItem()
        if value is _MISSING:
            return QTreeWidgetItem(parent, [label, description or "本记录未提供"])
        item = self._namedValue(parent, label, value, _mapping(semantics), summarized=summarized)
        if description is not None:
            item.setText(1, description)
        if suffix:
            item.setText(1, f"{item.text(1)} · {suffix}")
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

    def __init__(self, parent=None, *, operatorLookup=None):
        super().__init__(parent)
        self.setObjectName("logRecordDetail")
        self.operatorLookup = operatorLookup
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

    def setEvent(self, record, *, displayMessage=None, context=None, nodeElapsedMs=None):
        self._showEvent(record, record, displayMessage=displayMessage, context=context,
                        includeTime=True, nodeElapsedMs=nodeElapsedMs)

    def _showEvent(self, raw, event, *, displayMessage=None, context=None, includeTime=False, nodeElapsedMs=None):
        kind = event.get("eventType", "")
        title = _NODE_TITLES.get(kind) or _FRAMEWORK_TITLES.get(kind) or kind or "日志记录"
        finalReceipt = _backgroundSqliteReceipt(event)
        if finalReceipt is not None:
            title = "SQLite 后台写入结果"
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
        definition = self._definition(payload)
        if finalReceipt is not None:
            self._section("后台最终回执（receipt）", finalReceipt, {
                "writeId": {"title": "写入标识"}, "status": {"title": "数据库写入状态"},
                "rowsAffected": {"title": "影响行数"}, "elapsedMs": {"title": "写入耗时", "unit": "ms"},
            })
            payload = {key: value for key, value in payload.items() if key not in ("receipt", "background")}
        if isinstance(payload, dict):
            self._eventPayload(kind, payload, definition, nodeElapsedMs)
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
        info.update(self._identity(payload))
        if context:
            info.update(context)
        if info:
            self.readable.field("记录身份", info)
        self._finishRecord()

    def _definition(self, identity):
        if self.operatorLookup is None or not isinstance(identity, dict):
            return {}
        operatorId, version = identity.get("operatorId"), identity.get("operatorVersion")
        if not isinstance(operatorId, str) or not operatorId or not isinstance(version, str) or not version:
            return {}
        candidate = _mapping(self.operatorLookup(operatorId, version))
        return candidate if candidate.get("operatorId") == operatorId and candidate.get("version") == version else {}

    @staticmethod
    def _identity(record):
        record = _mapping(record)
        return {label: record[key] for key, label in (("operatorId", "算子 ID"), ("operatorVersion", "算子版本"))
                if key in record}

    def _eventPayload(self, kind, payload, definition, nodeElapsedMs):
        remaining = dict(payload)
        if payload.get("truncated") is True:
            self.notice.setText("该记录标记为已截断。详情与复制使用当前收到的数据。")
        nodeRecord = kind in {"node.started", "node.completed", "node.failed", "node.skipped"}
        if (nodeRecord or kind in _FRAMEWORK_TITLES) and "status" in remaining:
            value = remaining.pop("status")
            self.readable.field("状态", value, description=executionStatusTitle(value))
        if nodeRecord:
            resultSchema = _mapping(definition.get("resultSchema"))
            for key in ("operatorId", "operatorVersion"):
                remaining.pop(key, None)
            if nodeElapsedMs is not None:
                self.readable.field("节点总耗时", nodeElapsedMs, description=f"{nodeElapsedMs} ms · 节点起止事件")
            metrics = remaining.pop("metrics", _MISSING)
            metricFields = _mapping(resultSchema.get("metrics"))
            source = remaining.pop("ioSummary", None)
            summary = normalizeInspection(source)
            scalarOutputs = {
                row["port"]: row["value"]["value"] for row in _mapping(summary["outputs"]).get("items", ())
                if row["value"]["kind"] == "scalar" and not row["value"].get("truncated")
            }
            self._overview(resultSchema, scalarOutputs, metrics, remaining.get("diagnostics"))
            for key, label in (("message", "跳过原因" if kind == "node.skipped" else "原因"),
                               ("code", "记录代码"), ("reason", "原因"), ("missingInputs", "缺少输入"),
                               ("blockedByBranches", "未选中的上游分支")):
                if key in remaining:
                    self.readable.field(label, remaining.pop(key))
            diagnostics = remaining.get("diagnostics")
            diagnosticFields = _mapping(resultSchema.get("diagnostics"))
            if (kind == "node.completed" and isinstance(diagnostics, dict) and diagnosticFields.get("modelPath")
                    and NODE_INPUT_FILES.get(definition.get("operatorId")) == "modelPath"):
                remaining["diagnostics"] = self._modelDetails(diagnostics)
            reason = source.get("unavailableReason") if isinstance(source, dict) else None
            self._ioSide("输入摘要", summary["inputs"], reason, _mapping(resultSchema.get("inputs")))
            self._parameters(remaining.pop("parameterSummary", _MISSING), definition, metrics, diagnostics)
            self._ioSide("输出摘要", summary["outputs"], reason, _mapping(resultSchema.get("outputs")))
            self._metrics(metrics, metricFields)
            self._section("诊断", remaining.pop("diagnostics", _MISSING), diagnosticFields)
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

    def _overview(self, schema, outputs, metrics, diagnostics, *, title="运行概览"):
        # Prefer the reported business outcome. Timing has its own slot.
        groups = ("business", "result")
        candidates = chain(
            _groupedFields(schema.get("outputs"), outputs, (*groups, "parameter")),
            _groupedFields(schema.get("diagnostics"), diagnostics, groups),
            _groupedFields(schema.get("metrics"), metrics, groups),
        )
        visible = list(islice(candidates, 4))
        duration = _mapping(schema.get("metrics")).get("latencyMs", {})
        if "latencyMs" in _mapping(metrics) and duration.get("group") == "duration":
            visible.append(("latencyMs", metrics["latencyMs"], duration))
        if visible:
            parent = QTreeWidgetItem(self.readable, [title])
            for key, value, descriptor in visible:
                self.readable.field(key, value, semantics=descriptor, parent=parent)
            parent.setExpanded(True)

    def _section(self, label, value, fields=None, *, expand=True, summarized=False):
        if fields and summarized:
            fields = {key: {name: text for name, text in _mapping(description).items()
                            if name not in ("fields", "items")} for key, description in _mapping(fields).items()}
        return self.readable.field(label, value, semantics={"fields": _mapping(fields)} if fields else None,
                                   description="{} · 无附加" + label if value == {} else None, expand=expand)

    def _metrics(self, value, fields):
        if not isinstance(value, dict) or not value or not fields:
            self._section("指标", value, fields)
            return
        grouped = {}
        other = {}
        for key, metric in value.items():
            group = _mapping(fields.get(key)).get("group")
            if isinstance(group, str) and group in _METRIC_GROUPS:
                grouped.setdefault(group, {})[key] = metric
            else:
                other[key] = metric
        if not grouped:
            self._section("指标", value, fields)
            return
        parent = self.readable.field("指标", value)
        # Keep the complete metrics dictionary available to the copy action.
        parent.setData(0, Qt.UserRole, None)
        parent.takeChildren()
        for group, title in _METRIC_GROUPS.items():
            if group in grouped:
                self.readable.field(title, grouped[group], semantics={"fields": fields}, expand=True, parent=parent)
        for key, metric in other.items():
            self.readable.field(key, metric, semantics=fields.get(key), parent=parent)
        parent.setExpanded(True)

    def _parameters(self, value, definition, metrics, diagnostics):
        evidence = []
        schema = _mapping(definition.get("resultSchema"))
        for section, payload, source in (("metrics", metrics, "指标"), ("diagnostics", diagnostics, "诊断")):
            for key, fieldValue, descriptor in _groupedFields(schema.get(section), payload, ("parameter",)):
                evidence.append((key, fieldValue, descriptor, source))
        if value is not _MISSING and (not isinstance(value, list) or not value):
            self.readable.field("参数摘要", value)
        groups = {"call": [], "effective": []}
        other = []
        for row in value if isinstance(value, list) else ():
            path = row.get("path") if isinstance(row, dict) else None
            if (isinstance(path, list) and path and all(isinstance(part, str) for part in path)
                    and "value" in row and row.get("stage") in ("call", "effective")):
                groups[row["stage"]].append(row)
            else:
                other.append(row)
        schema = _mapping(definition.get("paramSchema"))
        for stage, label in (("call", "本次调用参数"), ("effective", "本次生效参数")):
            rows = groups[stage]
            recorded = evidence if stage == "effective" else []
            if not rows and not recorded:
                continue
            if rows:
                parent = self.readable.field(label, rows)
                parent.setData(0, Qt.UserRole, None)
                parent.takeChildren()
            else:
                parent = QTreeWidgetItem(self.readable, [label])
            for row in rows:
                path = row["path"]
                source = row.get("source")
                suffix = _PARAMETER_SOURCES.get(source, source) if isinstance(source, str) else ""
                self.readable.field(".".join(path), row["value"], semantics=parameterSchemaAtPath(schema, path),
                                    suffix=suffix, parent=parent)
            for key, fieldValue, descriptor, source in recorded:
                self.readable.field(key, fieldValue, semantics=descriptor, suffix=source, parent=parent)
            parent.setExpanded(True)
        if other:
            self.readable.field("参数摘要", other)

    def _modelDetails(self, diagnostics):
        loadedPath = diagnostics.get("modelPath")
        if not isinstance(loadedPath, str) or not loadedPath:
            return diagnostics
        remaining = dict(diagnostics)
        remaining.pop("modelPath")
        source = diagnostics.get("modelSource", {})
        source = source if isinstance(source, dict) else {}
        sourcePath = source.get("path")
        if not isinstance(sourcePath, str):
            sourcePath = None
        resourceId = source.get("resourceId")
        digest = source.get("sha256")
        if sourcePath:
            known = {"path", "configuredPath", "resourceId", "sha256"}
            unused = {key: value for key, value in source.items() if key not in known or not value}
            if unused:
                remaining["modelSource"] = unused
            else:
                remaining.pop("modelSource")
            name = PureWindowsPath(sourcePath).name
            if resourceId:
                summary = f"{name} · 工程资源"
            else:
                summary = f"{name} · 节点选定文件" if sourcePath != loadedPath else f"{name} · 直接文件"
        else:
            name = PureWindowsPath(loadedPath).name
            summary = f"{name} · 来源未记录"
            archived = re.search(r"(?:^|[/\\])configuration-resources[/\\][^/\\]+[/\\]([0-9a-f]{64})\.onnx\Z",
                                 loadedPath, re.IGNORECASE)
            if archived:
                digest = archived.group(1)
                summary = "来源未记录"
        model = QTreeWidgetItem(self.readable, ["使用的模型", summary])
        if sourcePath and sourcePath != loadedPath:
            self.readable.field("模型来源路径" if resourceId else "节点选择路径", sourcePath, parent=model)
        self.readable.field("实际加载路径", loadedPath, parent=model)
        if source.get("configuredPath"):
            self.readable.field("填写路径（未生效）", source["configuredPath"], parent=model)
        if resourceId:
            self.readable.field("资源 ID", resourceId, parent=model)
        if digest:
            self.readable.field("SHA-256", digest, parent=model)
        model.setExpanded(True)
        return remaining

    def _ioSide(self, label, batch, unavailableReason, fields):
        if batch is None:
            self.readable.field(label, _MISSING, description=unavailableReason if isinstance(unavailableReason, str) else None)
            return
        # The normalized descriptors are concise summaries of this record's
        # ports. They carry no image or collection contents to retrieve.
        count = batch["portCount"]
        parent = QTreeWidgetItem(self.readable, [label, f"{count} 个端口 · 仅记录摘要" if count else "无端口值"])
        for row in batch["items"]:
            value = row["value"]
            descriptor = _mapping(fields.get(row["port"]))
            if value["kind"] == "scalar":
                text = _scalarText(value["value"]) + (" · 已截断" if value.get("truncated") else "")
                value = value["value"]
                if type(value) in (int, float) and isinstance(descriptor.get("unit"), str):
                    text += " " + descriptor["unit"]
            else:
                text = describePort(value)
                if value.get("dtype"):
                    text += f" · dtype {value['dtype']}"
            self.readable.field(row["port"], value, description=text[:500], parent=parent,
                                semantics=descriptor, summarized=True)
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
        sourceIdentity = record.get("sourceIdentity")
        definition = self._definition(sourceIdentity)
        resultSchema = _mapping(definition.get("resultSchema"))
        remaining = dict(record)
        for key in ("message", "sessionId", "generation", "executionId"):
            remaining.pop(key, None)
        if "status" in remaining:
            value = remaining.pop("status")
            self.readable.field("状态", value, description=executionStatusTitle(value))
        if "code" in remaining:
            self.readable.field("结果代码", remaining.pop("code"))
        if "elapsedMs" in remaining:
            self.readable.field("调试执行耗时", remaining.pop("elapsedMs"),
                                semantics={"unit": "ms", "description": "调试 Worker 记录的执行耗时"})
        metrics = remaining.pop("metrics", _MISSING)
        diagnostics = remaining.pop("diagnostics", _MISSING)
        metricFields = _mapping(resultSchema.get("metrics"))
        pairedReceipts = _debugSqliteReceiptPair(record, definition)
        self._overview(resultSchema, remaining.get("outputs"), metrics, diagnostics,
                       title="后台最终回执" if pairedReceipts else "运行概览")
        if "ioSummary" in remaining:
            summary = normalizeInspection(remaining.pop("ioSummary"))
            self._ioSide("输入摘要", summary["inputs"], None, _mapping(resultSchema.get("inputs")))
        if "inputSources" in remaining:
            self._section("输入来源", remaining.pop("inputSources"), resultSchema.get("inputs"),
                          expand=False, summarized=True)
        self._parameters(remaining.pop("parameterSummary", _MISSING), definition, metrics, diagnostics)
        for key, label in (("outputs", "已返回输出"), ("outputAssets", "输出资产")):
            if key in remaining:
                if key == "outputs" and pairedReceipts:
                    label = "提交时输出（已入队）"
                self._section(label, remaining.pop(key), resultSchema.get("outputs"), expand=key == "outputs",
                              summarized=key == "outputAssets")
        if metrics is not _MISSING:
            self._metrics(metrics, metricFields)
        if diagnostics is not _MISSING:
            self._section("诊断", diagnostics, resultSchema.get("diagnostics"))
        for key, label in (("rawParams", "提交参数"), ("effectiveParams", "准备阶段参数"),
                           ("resolvedParams", "解析阶段参数"), ("bindingSources", "绑定来源")):
            if key in remaining:
                schema = _mapping(definition.get("paramSchema"))
                self._section(label, remaining.pop(key), schema.get("properties") if key != "bindingSources" else None,
                              expand=False)
        if remaining:
            self.readable.field("附加字段", remaining)
        identity = {"调试会话": sessionId, "代次": generation}
        identity.update(self._identity(sourceIdentity))
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
