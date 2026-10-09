from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
import queue
import re
from threading import Event, Lock, Thread
from typing import Any, cast
from uuid import uuid4

from PySide2.QtCore import QDateTime, QTimer, Qt
from PySide2.QtGui import QValidator
from PySide2.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.param_form import SchemaParamForm, applyParameterLabel
from emo_master.apps.designer.ui.widgets import WrapLabel, scrollContent
from emo_master.core.contracts.communication import plcDeviceAddressSpan, plcWordsPerValue
from emo_master.plugins.builtins._communication_operators import (
    PLC_READ_PARAM_SCHEMA,
    PlcSlmpReadOperator,
    PlcSlmpWriteOperator,
)
from emo_master.plugins.builtins._editor_support import requiredChild


_WORD_TYPES = ("uint16", "int16", "uint32", "int32", "float32")
_BOUNDS = {
    "uint16": (0, 65535),
    "int16": (-32768, 32767),
    "uint32": (0, 4294967295),
    "int32": (-2147483648, 2147483647),
    "float32": (-3.4028234663852886e38, 3.4028234663852886e38),
}
_ENDPOINT_FIELDS = (
    "host", "port", "connectTimeoutMs", "responseTimeoutMs",
    "networkNo", "pcNo", "moduleIoNo", "moduleStationNo",
    "monitoringTimer",
)
_ENDPOINT_SPECS = cast(dict[str, dict[str, Any]], PLC_READ_PARAM_SCHEMA["properties"])
_STATES = {"connecting": "连接中", "connected": "已连接", "verified": "通信已验证",
           "closed": "已断开", "error": "错误"}
_COMMANDS = {"open": "连接", "read": "读取", "write": "写入", "set_write_enabled": "写入解锁"}


class _Cancellation(Event):
    def cancel(self) -> None:
        self.set()

    def is_active(self) -> bool:
        return not self.is_set()


class _Cancelled(RuntimeError):
    pass


def _requireOk(reply: Any) -> None:
    if not bool(getattr(reply, "ok", False)):
        code = str(getattr(reply, "code", "")) or "E_PLC_DEBUG"
        message = str(getattr(reply, "message", "")) or "Runtime 请求失败"
        raise RuntimeError(f"{code}: {message}")


def _number(text: str, dataType: str) -> int | float:
    if dataType == "float32":
        value = float(text)
    else:
        if not re.fullmatch(r"[+-]?[0-9]+", text):
            raise ValueError(f"{dataType} 的值必须为整数")
        value = int(text)
    low, high = _BOUNDS[dataType]
    if not low <= value <= high or not math.isfinite(value):
        raise ValueError(f"{dataType} 的值必须介于 {low} 和 {high} 之间")
    return value


class _NumberValidator(QValidator):
    def __init__(self, dataType: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.dataType = dataType

    def validate(self, text: str, position: int):
        try:
            _number(text, self.dataType)
            state = QValidator.Acceptable
        except (ValueError, OverflowError):
            partial = r"[+-]?(?:[0-9]*(?:\.[0-9]*)?)(?:[eE][+-]?[0-9]*)?"
            if text in ("", "+", "-") or (
                self.dataType == "float32" and re.fullmatch(partial, text)
                and (text.endswith(("e", "E", "+", "-")) or text in (".", "+.", "-."))
            ):
                state = QValidator.Intermediate
            else:
                state = QValidator.Invalid
        return state, text, position


@dataclass
class _Operation:
    generation: int
    name: str
    params: dict
    sessionId: str = ""
    runtimeId: str = ""
    cancellation: _Cancellation = field(default_factory=_Cancellation)
    requestId: str = field(default_factory=lambda: uuid4().hex)
    ownershipLock: Any = field(default_factory=Lock)
    openReply: Any = None
    claimed: bool = False
    releaseStarted: bool = False


@dataclass
class _Result:
    operation: _Operation
    reply: Any = None
    error: Exception | None = None
    cleaned: bool = False
    fenceReply: Any = None


@dataclass
class _Cleanup:
    generation: int
    kind: str
    sessionId: str
    runtimeId: str
    writesEnabledAtStart: bool = False
    permissionRevision: int = 0
    identity: str = field(default_factory=lambda: uuid4().hex)
    cancellation: _Cancellation = field(default_factory=_Cancellation)


@dataclass
class _CleanupResult:
    cleanup: _Cleanup
    reply: Any = None
    error: Exception | None = None


def _lock(context: Any, sessionId: str, runtimeId: str, cancellation=None) -> Any:
    reply = context.executePlcDebugCommand(
        sessionId, runtimeId, "set_write_enabled", {"enabled": False},
        uuid4().hex, cancellation=cancellation or _Cancellation(),
    )
    _requireOk(reply)
    if bool(getattr(reply, "write_enabled", False)):
        raise RuntimeError("Runtime 未锁定 PLC 写入")
    return reply


def _close(context: Any, sessionId: str, runtimeId: str, cancellation=None) -> Any:
    reply = context.closePlcDebugSession(
        sessionId, runtimeId, cancellation=cancellation or _Cancellation(),
    )
    _requireOk(reply)
    return reply


def _runOperation(context: Any, operation: _Operation, results: queue.Queue) -> None:
    result = _Result(operation)
    try:
        if operation.cancellation.is_set():
            raise _Cancelled("请求已在发送前取消")
        if operation.name == "open":
            result.reply = context.openPlcDebugSession(
                operation.params, operation.requestId, cancellation=operation.cancellation,
            )
            reply = result.reply
            unusable = (
                not bool(getattr(reply, "ok", False))
                or str(getattr(reply, "state", "")) not in ("connected", "verified")
                or not str(getattr(reply, "runtime_instance_id", ""))
                or bool(getattr(reply, "write_enabled", False))
            )
            # An open can finish after cancellation without ever reaching the UI.
            with operation.ownershipLock:
                operation.openReply = reply
                release = operation.cancellation.is_set() or unusable
                operation.releaseStarted = release
            if release:
                sessionId = str(getattr(reply, "session_id", ""))
                if sessionId:
                    try:
                        _close(context, sessionId, str(getattr(reply, "runtime_instance_id", "")))
                        result.cleaned = True
                    except Exception:
                        with operation.ownershipLock:
                            operation.releaseStarted = False
                        raise
        else:
            result.reply = context.executePlcDebugCommand(
                operation.sessionId, operation.runtimeId, operation.name,
                operation.params, operation.requestId, cancellation=operation.cancellation,
            )
            # A slow unlock must not undo the independent page-leave fence.
            if (operation.name == "set_write_enabled" and operation.params["enabled"]
                    and operation.cancellation.is_set()):
                result.fenceReply = _lock(context, operation.sessionId, operation.runtimeId)
                result.cleaned = True
    except Exception as error:
        result.error = error
    results.put(result)


def _runCleanup(context: Any, cleanup: _Cleanup, results: queue.Queue) -> None:
    result = _CleanupResult(cleanup)
    try:
        if cleanup.kind == "renew":
            result.reply = context.executePlcDebugCommand(
                cleanup.sessionId, cleanup.runtimeId, "renew", {}, cleanup.identity,
                cancellation=cleanup.cancellation,
            )
            _requireOk(result.reply)
        else:
            method = _close if cleanup.kind == "close" else _lock
            result.reply = method(context, cleanup.sessionId, cleanup.runtimeId, cleanup.cancellation)
    except Exception as error:
        result.error = error
    results.put(result)


class PlcEditorController:
    def __init__(self) -> None:
        self.context: Any = None
        self.root: Any = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="designer-plc-debug")
        self._results: queue.Queue = queue.Queue()
        self._operation: _Operation | None = None
        self._pendingWrite: _Operation | None = None
        self._cleanups: dict[str, _Cleanup] = {}
        self._generation = 0
        self._sessionId = ""
        self._runtimeId = ""
        self._state = "closed"
        self._writeEnabled = False
        self._writeLockGeneration = 0
        self._writeLockFresh = False
        self._permissionRevision = 0
        self._debugInitialized = False
        self._loadingDebug = False
        self._pendingDraft: tuple[int, dict] | None = None
        self._disposed = False
        self._valueType = "uint16"
        self._endpointControls: dict[str, Any] = {}

    def bind(self, rootWidget: object, context: Any) -> None:
        self.root = rootWidget
        self.context = context
        self.tabs = requiredChild(rootWidget, QTabWidget, "plcTabs")
        self.paramsPage = requiredChild(rootWidget, QWidget, "paramsPage")
        self.runtimePage = requiredChild(rootWidget, QWidget, "runtimePage")
        self.root.layout().setContentsMargins(0, 0, 0, 0)
        self.root.layout().setSpacing(0)
        self.paramsPage.layout().setContentsMargins(0, 0, 0, 0)
        self.paramForm = SchemaParamForm()
        self.paramForm.setWorkflowOptions(getattr(context, "workflowOptions", []))
        self.paramsPage.layout().addWidget(scrollContent(self.paramForm))
        self._buildRuntimePage()
        self._resultTimer = QTimer(self.root)
        self._resultTimer.setInterval(20)
        self._resultTimer.timeout.connect(self._drainResults)
        self._resultTimer.start()
        self._pollTimer = QTimer(self.root)
        self._pollTimer.setSingleShot(True)
        self._pollTimer.timeout.connect(self.read)
        self._heartbeatTimer = QTimer(self.root)
        self._heartbeatTimer.setInterval(10000)
        self._heartbeatTimer.timeout.connect(self._renew)
        self._leaseTimer = QTimer(self.root)
        self._leaseTimer.setSingleShot(True)
        self._leaseTimer.timeout.connect(lambda: self._fail("PLC 调试租约已过期"))
        self.tabs.currentChanged.connect(self._tabChanged)
        bindInvalidation = getattr(context, "bindPreviewInvalidation", None)
        if callable(bindInvalidation):
            bindInvalidation(self.invalidatePreviewSources)
        self._refreshLocation()
        self._refreshControls()

    def _tool(self, name: str, tooltip: str, iconName: str, callback) -> QToolButton:
        button = QToolButton()
        button.setObjectName(name)
        button.setIcon(icon(iconName))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(34, 28)
        button.clicked.connect(callback)
        return button

    def _spin(self, name: str, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setObjectName(name)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setMinimumWidth(80)
        spin.setFixedHeight(28)
        return spin

    def _parameterLabel(self, name: str, control: QWidget) -> QLabel:
        label = WrapLabel()
        # Debug controls include read parameters even in the write editor.
        applyParameterLabel(label, control, name, PLC_READ_PARAM_SCHEMA)
        return label

    def _buildRuntimePage(self) -> None:
        runtimeLayout = self.runtimePage.layout()
        runtimeLayout.setContentsMargins(6, 4, 6, 4)
        runtimeLayout.setSpacing(4)
        self.runtimePage.setMinimumWidth(520)
        self.runtimePage.setStyleSheet(
            "QLineEdit, QSpinBox, QComboBox { padding: 2px 4px; }"
            "QSpinBox QLineEdit { padding: 0; border: none; }"
            "QComboBox { padding-right: 22px; }"
            "QToolButton { padding: 2px 4px; }"
        )
        controls = QWidget()
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.locationLabel = WrapLabel()
        self.locationLabel.setObjectName("plcRuntimeLocation")
        self.locationLabel.setWordWrap(True)
        top.addWidget(self.locationLabel, 1)
        self.reloadButton = self._tool("reloadParamsButton", "从当前参数重新加载", "refresh-cw", self.reloadParameters)
        self.connectButton = self._tool("connectButton", "连接 PLC", "play", self.connect)
        self.disconnectButton = self._tool("disconnectButton", "断开连接", "square", self.disconnect)
        for button in (self.reloadButton, self.connectButton, self.disconnectButton):
            top.addWidget(button)
        runtimeLayout.addLayout(top)
        self.statusLabel = WrapLabel("已断开 | 写入已锁定")
        self.statusLabel.setObjectName("plcDebugStatus")
        self.statusLabel.setWordWrap(True)
        runtimeLayout.addWidget(self.statusLabel)
        self.metadataLabel = WrapLabel("")
        self.metadataLabel.setObjectName("plcReplyMetadata")
        self.metadataLabel.setWordWrap(True)
        runtimeLayout.addWidget(self.metadataLabel)
        endpoint = QHBoxLayout()
        self.hostEdit = QLineEdit()
        self.hostEdit.setObjectName("debugHost")
        self.hostEdit.setMinimumWidth(180)
        self.hostEdit.setFixedHeight(28)
        self.portSpin = self._spin("debugPort", 1, 65535, 10001)
        self._endpointControls.update(host=self.hostEdit, port=self.portSpin)
        endpoint.addWidget(self._parameterLabel("host", self.hostEdit))
        endpoint.addWidget(self.hostEdit, 1)
        endpoint.addWidget(self._parameterLabel("port", self.portSpin))
        endpoint.addWidget(self.portSpin)
        layout.addLayout(endpoint)

        tools = QGridLayout()
        tools.setVerticalSpacing(2)
        tools.setHorizontalSpacing(4)
        self.deviceCombo = QComboBox()
        self.deviceCombo.setObjectName("debugDevice")
        self.deviceCombo.addItems(["D", "M"])
        self.deviceCombo.setFixedHeight(28)
        self.addressSpin = self._spin("debugStartAddress", 0, 0xFFFFFF, 0)
        self.addressSpin.setMinimumWidth(120)
        self.dataTypeCombo = QComboBox()
        self.dataTypeCombo.setObjectName("debugDataType")
        self.dataTypeCombo.addItems(_WORD_TYPES)
        self.dataTypeCombo.setMinimumWidth(105)
        self.dataTypeCombo.setFixedHeight(28)
        self.countSpin = self._spin("debugCount", 1, 960, 1)
        for column, (name, control) in enumerate((
            ("device", self.deviceCombo), ("startAddress", self.addressSpin),
            ("dataType", self.dataTypeCombo), ("count", self.countSpin),
        )):
            tools.addWidget(self._parameterLabel(name, control), 0, column * 2)
            tools.addWidget(control, 0, column * 2 + 1)
        tools.setColumnStretch(3, 1)
        layout.addLayout(tools)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.readButton = self._tool("readButton", "读取一次", "scan-line", self.read)
        self.writeButton = self._tool("writeButton", "写入当前值", "upload", self.write)
        self.writeEnabledCheck = QCheckBox("允许写入")
        self.writeEnabledCheck.setObjectName("writeEnabledCheck")
        self.writeEnabledCheck.setToolTip("Runtime 写入锁")
        self.pollCheck = QCheckBox("轮询")
        self.pollCheck.setObjectName("pollCheck")
        self.pollIntervalSpin = self._spin("pollIntervalMs", 100, 60000, 500)
        self.pollIntervalSpin.setSuffix(" 毫秒")
        self.pollIntervalSpin.setMinimumWidth(130)
        actions.addWidget(self.readButton)
        actions.addWidget(self.pollCheck)
        actions.addWidget(self.pollIntervalSpin)
        actions.addStretch(1)
        actions.addWidget(self.writeEnabledCheck)
        actions.addWidget(self.writeButton)
        self.controlsScroll = scrollContent(controls, name="plcControlsScroll")
        self.controlsScroll.setMinimumHeight(24)
        self.controlsScroll.setMaximumHeight(96)
        runtimeLayout.addWidget(self.controlsScroll)
        runtimeLayout.addLayout(actions)
        layout = runtimeLayout

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.resultTable = self._table("readResultTable", ["地址", "类型", "值", "原始字", "更新时间"])
        self.resultTable.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.writeTable = self._table("writeValuesTable", ["地址", "值"])
        self.receiptLabel = WrapLabel("待写入值")
        self.receiptLabel.setObjectName("writeReceipt")
        for title, table in ((WrapLabel("读取 / 回读"), self.resultTable), (self.receiptLabel, self.writeTable)):
            panel = QWidget()
            panelLayout = QVBoxLayout(panel)
            panelLayout.setContentsMargins(0, 0, 0, 0)
            panelLayout.setSpacing(2)
            panelLayout.addWidget(title)
            panelLayout.addWidget(table, 1)
            splitter.addWidget(panel)
        splitter.setSizes([400, 300])
        layout.addWidget(splitter, 1)
        footer = QHBoxLayout()
        self.advancedButton = QToolButton()
        self.advancedButton.setText("高级连接设置")
        self.advancedButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advancedButton.setArrowType(Qt.RightArrow)
        self.advancedButton.setCheckable(True)
        self.advancedButton.setObjectName("advancedEndpointButton")
        self.advancedButton.setFixedHeight(28)
        footer.addWidget(self.advancedButton, 0, Qt.AlignTop)
        footer.addStretch(1)
        layout.addLayout(footer)
        advanced = QWidget()
        advancedForm = QFormLayout(advanced)
        advancedForm.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for name in _ENDPOINT_FIELDS[2:]:
            spec = _ENDPOINT_SPECS[name]
            spin = self._spin("debug_" + name, spec["minimum"], spec["maximum"], spec["default"])
            self._endpointControls[name] = spin
            advancedForm.addRow(self._parameterLabel(name, spin), spin)
        self.advancedScroll = QScrollArea()
        self.advancedScroll.setObjectName("advancedEndpointScroll")
        self.advancedScroll.setWidgetResizable(True)
        self.advancedScroll.setFrameShape(QScrollArea.NoFrame)
        self.advancedScroll.setWidget(advanced)
        self.advancedScroll.setMaximumHeight(110)
        self.advancedScroll.setMinimumHeight(0)
        self.advancedScroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.advancedScroll.hide()
        # Share spare height with the result tables while allowing short windows.
        layout.addWidget(self.advancedScroll, 1)
        self.advancedButton.toggled.connect(self._toggleAdvanced)
        self.deviceCombo.currentTextChanged.connect(self._deviceChanged)
        self.dataTypeCombo.currentTextChanged.connect(self._shapeChanged)
        self.addressSpin.valueChanged.connect(self._shapeChanged)
        self.countSpin.valueChanged.connect(self._shapeChanged)
        self.pollCheck.toggled.connect(self._pollChanged)
        self.writeEnabledCheck.toggled.connect(self._writeLockChanged)

    def _table(self, name: str, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setObjectName(name)
        table.setHorizontalHeaderLabels(headers)
        table.setMinimumSize(240, 100)
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setMinimumSectionSize(70)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table

    def _toggleAdvanced(self, enabled: bool) -> None:
        self.advancedScroll.setVisible(enabled)
        self.advancedButton.setArrowType(Qt.DownArrow if enabled else Qt.RightArrow)

    def loadParams(self, params: dict[str, object]) -> None:
        self.paramForm.setSchema(self.context.paramSchema, deepcopy(params))
        if self._debugInitialized:
            self.reloadParameters()

    def collectParams(self) -> dict[str, object]:
        return deepcopy(self.paramForm.getValues())

    def validate(self) -> object:
        # SchemaParamForm intentionally tolerates malformed array JSON; Apply must not.
        for fieldName, control in self.paramForm._controls.items():
            if isinstance(control, QTextEdit):
                try:
                    json.loads(control.toPlainText() or "[]")
                except json.JSONDecodeError as error:
                    return {"message": f"{fieldName}: {error}"}
        operator = (PlcSlmpWriteOperator() if self.context.operatorId.endswith("slmp_write")
                    else PlcSlmpReadOperator())
        return operator.validateParams(self.collectParams())

    def onOpen(self) -> None:
        self._refreshLocation()

    def onClose(self) -> None:
        if hasattr(self, "_leaseTimer") and not self._disposed:
            self._resetSession("已断开连接")

    def dispose(self) -> None:
        if self._disposed:
            return
        self.onClose()
        self._disposed = True
        if self.root is not None:
            for name in ("_resultTimer", "_pollTimer", "_heartbeatTimer", "_leaseTimer"):
                timer = getattr(self, name, None)
                if timer is not None:
                    timer.stop()
            bindInvalidation = getattr(self.context, "bindPreviewInvalidation", None)
            if callable(bindInvalidation):
                bindInvalidation(lambda: None)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _active(self) -> bool:
        return not self._disposed and self.tabs.currentWidget() is self.runtimePage

    @staticmethod
    def _checkQuietly(check: QCheckBox, enabled: bool) -> None:
        blocked = check.blockSignals(True)
        check.setChecked(enabled)
        check.blockSignals(blocked)

    def _tabChanged(self, _index: int) -> None:
        if self._disposed:
            return
        if self._active():
            self._refreshLocation()
            if not self._debugInitialized:
                self._applyDraft(self.collectParams())
            self._refreshControls()
            return
        self._pausePolling()
        self._cancelOperation(preserveData=True)
        if self._operation is None or self._operation.name not in ("read", "write"):
            self._generation += 1
        self._writeEnabled = False
        if not self._sessionId:
            self._state = "closed"
        if self._sessionId:
            self._startCleanup("lock", self._sessionId, self._runtimeId)
        self._setStatus("运行调试已暂停")
        self._refreshControls()

    def _refreshLocation(self) -> None:
        method = getattr(self.context, "plcDebugLocation", None)
        try:
            location = str(method()) if callable(method) else "不可用"
        except Exception as error:
            location = str(error)
        self.locationLabel.setText("运行目标：" + location)

    def reloadParameters(self) -> None:
        if self._disposed:
            return
        draft = self.collectParams()
        self._resetSession("正在重新加载参数")
        self._pendingDraft = self._generation, draft
        self._maybeReload()

    def _maybeReload(self) -> None:
        pending = self._pendingDraft
        if pending is not None and not self._operation and not self._safetyPending():
            self._pendingDraft = None
            if pending[0] == self._generation and not self._disposed:
                self._applyDraft(pending[1])
                self._setStatus("参数已重新加载，连接已断开")

    def _applyDraft(self, draft: dict) -> None:
        self._loadingDebug = True
        try:
            for name, control in self._endpointControls.items():
                default = _ENDPOINT_SPECS[name]["default"]
                value = draft.get(name, default)
                if isinstance(control, QLineEdit):
                    control.setText(str(value))
                else:
                    control.setValue(int(value))
            self.deviceCombo.setCurrentText(str(draft.get("device", "D")).upper())
            self._deviceChanged()
            self.dataTypeCombo.setCurrentText(str(draft.get("dataType", "uint16")))
            self.addressSpin.setValue(int(draft.get("startAddress", 0)))
            self.countSpin.setMaximum(960 // plcWordsPerValue(self.dataTypeCombo.currentText()))
            values = draft.get("values", [])
            values = values if isinstance(values, list) else []
            self.countSpin.setValue(int(draft.get("count", len(values) or 1)))
            self._rebuildWriteTable(values)
            self.resultTable.setRowCount(0)
            self.receiptLabel.setText("待写入值")
            self.receiptLabel.setStyleSheet("")
            self.receiptLabel.setToolTip("")
            self.metadataLabel.clear()
            self._debugInitialized = True
        finally:
            self._loadingDebug = False
        self._refreshControls()

    def _deviceChanged(self, *_args) -> None:
        previous = self.dataTypeCombo.currentText()
        types = _WORD_TYPES if self.deviceCombo.currentText() == "D" else ("uint16", "int16", "bit")
        blocked = self.dataTypeCombo.blockSignals(True)
        self.dataTypeCombo.clear()
        self.dataTypeCombo.addItems(types)
        self.dataTypeCombo.setCurrentText(previous if previous in types else types[0])
        self.dataTypeCombo.blockSignals(blocked)
        self._shapeChanged()

    def _shapeChanged(self, *_args) -> None:
        if self._loadingDebug:
            return
        self.countSpin.setMaximum(960 // plcWordsPerValue(self.dataTypeCombo.currentText()))
        self._rebuildWriteTable()

    def _rebuildWriteTable(self, values: list | None = None) -> None:
        dataType = self.dataTypeCombo.currentText()
        if values is None:
            values = []
            if self._valueType == dataType:
                for row in range(self.writeTable.rowCount()):
                    control = self.writeTable.cellWidget(row, 1)
                    if isinstance(control, QLineEdit):
                        values.append(control.text())
                    else:
                        values.append(self._cellValue(row))
        self._valueType = dataType
        count = self.countSpin.value()
        self.writeTable.setRowCount(0)
        self.writeTable.setRowCount(count)
        device = self.deviceCombo.currentText()
        stride = 1 if dataType == "bit" else plcDeviceAddressSpan(device, plcWordsPerValue(dataType))
        for row in range(count):
            address = f"{device}{self.addressSpin.value() + row * stride}"
            self.writeTable.setItem(row, 0, QTableWidgetItem(address))
            value = values[row] if row < len(values) else 0
            if dataType != "float32" and isinstance(value, float) and value.is_integer():
                value = int(value)
            if dataType == "bit":
                control = QCheckBox()
                control.setChecked(bool(value))
            elif dataType in ("uint16", "int16"):
                low, high = _BOUNDS[dataType]
                control = self._spin(f"writeValue{row}", int(low), int(high), 0)
                try:
                    control.setValue(int(_number(str(value), dataType)))
                except (ValueError, OverflowError):
                    pass
            else:
                control = QLineEdit()
                control.setValidator(_NumberValidator(dataType, control))
                control.setText(str(value))
                control.setMinimumWidth(110)
            control.setObjectName(f"writeValue{row}")
            control.setAccessibleName(address)
            self.writeTable.setCellWidget(row, 1, control)
        self._refreshControls()

    def _cellValue(self, row: int) -> int | float | bool:
        control = self.writeTable.cellWidget(row, 1)
        if isinstance(control, QCheckBox):
            return control.isChecked()
        if isinstance(control, QSpinBox):
            return control.value()
        if isinstance(control, QLineEdit):
            return _number(control.text(), self._valueType)
        raise ValueError("缺少待写入值")

    def _readParams(self) -> dict:
        device = self.deviceCombo.currentText()
        startAddress = self.addressSpin.value()
        dataType = self.dataTypeCombo.currentText()
        count = self.countSpin.value()
        span = count if dataType == "bit" else plcDeviceAddressSpan(device, count * plcWordsPerValue(dataType))
        if startAddress + span - 1 > 0xFFFFFF:
            raise ValueError("请求的地址范围超出了 24 位设备地址空间")
        return {"device": device, "startAddress": startAddress, "dataType": dataType, "count": count}

    def connect(self) -> None:
        if not self._active() or self._sessionId:
            return
        params = {}
        for name, control in self._endpointControls.items():
            params[name] = control.text().strip() if isinstance(control, QLineEdit) else control.value()
        if not params["host"]:
            self._setStatus("PLC 主机不能为空", error=True)
            return
        self._pausePolling()
        self._writeEnabled = False
        self._submit("open", params)

    def disconnect(self) -> None:
        if not self._disposed:
            self._resetSession("已断开连接")

    def read(self) -> None:
        if not self._canCommand():
            return
        try:
            self._submit("read", self._readParams())
        except ValueError as error:
            self._fail(str(error))

    def write(self) -> None:
        if not self._canWrite():
            return
        self._pollTimer.stop()
        try:
            params = self._readParams()
            del params["count"]
            params["values"] = [self._cellValue(row) for row in range(self.writeTable.rowCount())]
            params["lockGeneration"] = self._writeLockGeneration
            if self._operation is not None:
                self._pendingWrite = _Operation(self._generation, "write", deepcopy(params), self._sessionId, self._runtimeId)
                self._setStatus("写入等待当前读取完成")
                self._refreshControls()
            else:
                self._submit("write", params)
        except ValueError as error:
            self._fail(str(error))

    def _writeLockChanged(self, enabled: bool) -> None:
        if self._disposed:
            return
        if enabled:
            self._checkQuietly(self.writeEnabledCheck, self._writeEnabled)
            if self._canCommand():
                if self._writeLockFresh:
                    self._submit("set_write_enabled", {
                        "enabled": True, "lockGeneration": self._writeLockGeneration,
                    })
                else:
                    self._setStatus("正在同步 Runtime 写入锁")
                    self._renew()
        else:
            self._pausePolling()
            self._cancelOperation(preserveData=True)
            if self._operation is None or self._operation.name not in ("read", "write"):
                self._generation += 1
            self._writeEnabled = False
            if self._sessionId:
                self._startCleanup("lock", self._sessionId, self._runtimeId)
        self._refreshControls()

    def _pollChanged(self, enabled: bool) -> None:
        if enabled:
            self._schedulePoll()
        else:
            self._pollTimer.stop()

    def _pausePolling(self) -> None:
        self._pollTimer.stop()
        self._checkQuietly(self.pollCheck, False)

    def _schedulePoll(self) -> None:
        if self.pollCheck.isChecked() and self._canCommand():
            self._pollTimer.start(self.pollIntervalSpin.value())

    def _renew(self) -> None:
        if (self._sessionId and not self._disposed and not self._safetyPending()
                and not any(cleanup.kind == "renew" for cleanup in self._cleanups.values())):
            self._startCleanup("renew", self._sessionId, self._runtimeId)

    def _safetyPending(self) -> bool:
        return any(cleanup.kind != "renew" for cleanup in self._cleanups.values())

    def _canCommand(self) -> bool:
        return bool(self._active() and self._sessionId and not self._operation
                    and not self._pendingWrite and not self._safetyPending())

    def _canWrite(self) -> bool:
        operation = self._operation
        return bool(self._active() and self._sessionId and self._writeEnabled
                    and not self._pendingWrite and not self._safetyPending()
                    and (operation is None or (operation.name == "read"
                         and operation.generation == self._generation and not operation.cancellation.is_set())))

    def _dispatchPendingWrite(self) -> bool:
        pending, self._pendingWrite = self._pendingWrite, None
        if pending is None:
            return False
        if (pending.generation == self._generation and self._canWrite()
                and (pending.sessionId, pending.runtimeId) == (self._sessionId, self._runtimeId)):
            self._startOperation(pending)
            return True
        pending.cancellation.cancel()
        return False

    def _submit(self, name: str, params: dict) -> None:
        if self._disposed or self._operation or self._safetyPending() or not self._active():
            return
        self._startOperation(_Operation(self._generation, name, deepcopy(params), self._sessionId, self._runtimeId))

    def _startOperation(self, operation: _Operation) -> None:
        self._pollTimer.stop()
        self._operation = operation
        if operation.name == "open":
            self._state = "connecting"
        self._setStatus("正在" + _COMMANDS[operation.name])
        self._refreshControls()
        self._executor.submit(_runOperation, self.context, operation, self._results)

    def _cancelOperation(self, *, preserveData: bool = False) -> None:
        if self._pendingWrite is not None:
            self._pendingWrite.cancellation.cancel()
            self._pendingWrite = None
        operation = self._operation
        if operation is None or (preserveData and operation.name in ("read", "write")):
            return
        if operation.name == "write" and not operation.cancellation.is_set():
            self._unknownWrite("写入请求已在通信过程中取消。")
        with operation.ownershipLock:
            operation.cancellation.cancel()
            reply = operation.openReply
            release = reply is not None and not operation.claimed and not operation.releaseStarted
            if release:
                operation.releaseStarted = True
        if release and str(getattr(reply, "session_id", "")):
            self._startCleanup("close", str(reply.session_id), str(getattr(reply, "runtime_instance_id", "")))

    def _resetSession(self, message: str, *, error: bool = False) -> None:
        self._pausePolling()
        self._heartbeatTimer.stop()
        self._leaseTimer.stop()
        self._cancelOperation()
        for cleanup in self._cleanups.values():
            if cleanup.kind == "renew":
                cleanup.cancellation.cancel()
        self._generation += 1
        self._pendingDraft = None
        self._writeEnabled = False
        self._writeLockFresh = False
        self._writeLockGeneration = 0
        self._permissionRevision += 1
        sessionId, runtimeId = self._sessionId, self._runtimeId
        self._sessionId = self._runtimeId = ""
        self._state = "error" if error else "closed"
        if sessionId:
            self._startCleanup("close", sessionId, runtimeId)
        self._setStatus(message, error=error)
        self._refreshControls()

    def _fail(self, message: str) -> None:
        self._resetSession(message, error=True)

    def invalidatePreviewSources(self) -> None:
        if self._disposed:
            return
        self._resetSession("作业或 Runtime 已变更")
        self.resultTable.setRowCount(0)
        self.receiptLabel.setText("待写入值")
        self.receiptLabel.setStyleSheet("")
        self.receiptLabel.setToolTip("")
        self.metadataLabel.clear()
        self._refreshLocation()

    def _startCleanup(self, kind: str, sessionId: str, runtimeId: str) -> None:
        if kind == "lock":
            self._permissionRevision += 1
            self._writeLockFresh = False
        cleanup = _Cleanup(self._generation, kind, sessionId, runtimeId,
                           self._writeEnabled, self._permissionRevision)
        self._cleanups[cleanup.identity] = cleanup
        # Safety/cleanup RPCs must not wait behind a blocked serial data RPC.
        Thread(target=_runCleanup, args=(self.context, cleanup, self._results),
               name="designer-plc-" + kind, daemon=True).start()

    def _drainResults(self) -> None:
        if self._disposed:
            return
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                break
            if isinstance(result, _CleanupResult):
                self._finishCleanup(result)
            else:
                self._finishOperation(result)
        self._maybeReload()
        self._refreshControls()

    def _finishCleanup(self, result: _CleanupResult) -> None:
        cleanup = result.cleanup
        self._cleanups.pop(cleanup.identity, None)
        if result.error is None:
            try:
                if self._cacheWriteLockVersion(result.reply):
                    if cleanup.kind == "lock" or (
                        cleanup.kind == "renew" and cleanup.permissionRevision == self._permissionRevision
                        and not cleanup.cancellation.is_set()
                    ):
                        self._writeLockFresh = True
            except Exception as error:
                self._fail(str(error))
                return
        if result.error is not None:
            label = {"close": "关闭会话", "lock": "锁定写入", "renew": "续租"}[cleanup.kind]
            self.context.log("WARN", f"PLC 调试{label}失败：{result.error}")
            if cleanup.kind == "close":
                self._pendingDraft = None
                self._setStatus(f"关闭会话失败：{result.error}", error=True)
            elif (cleanup.sessionId, cleanup.runtimeId) == (self._sessionId, self._runtimeId):
                self._fail(f"{label}失败：{result.error}")
        elif (cleanup.generation == self._generation and cleanup.kind in ("lock", "renew")
                and not cleanup.cancellation.is_set()):
            try:
                self._acceptSessionReply(result.reply, updateWriteLock=cleanup.kind == "lock" or cleanup.writesEnabledAtStart)
                if cleanup.kind == "lock":
                    self._setStatus("写入已锁定")
            except Exception as error:
                self._fail(str(error))

    def _finishOperation(self, result: _Result) -> None:
        operation = result.operation
        if self._operation is operation:
            self._operation = None
        for reply in (result.reply, result.fenceReply):
            if reply is not None and bool(getattr(reply, "ok", False)):
                try:
                    if self._cacheWriteLockVersion(reply) and reply is result.fenceReply:
                        self._writeLockFresh = True
                except Exception as error:
                    self._fail(str(error))
                    return
        if operation.generation != self._generation or operation.cancellation.is_set():
            if not result.cleaned:
                if operation.name == "open" and result.reply is not None:
                    sessionId = str(getattr(result.reply, "session_id", ""))
                    if sessionId and not operation.releaseStarted:
                        operation.releaseStarted = True
                        self._startCleanup("close", sessionId, str(getattr(result.reply, "runtime_instance_id", "")))
                elif operation.name == "set_write_enabled" and operation.params["enabled"]:
                    self._startCleanup("lock", operation.sessionId, operation.runtimeId)
            return
        try:
            if result.error is not None:
                if operation.name == "write":
                    self._unknownWrite(str(result.error))
                elif operation.name == "open" and operation.openReply is not None and not operation.releaseStarted:
                    operation.releaseStarted = True
                    reply = operation.openReply
                    if str(getattr(reply, "session_id", "")):
                        self._startCleanup("close", str(reply.session_id), str(getattr(reply, "runtime_instance_id", "")))
                raise result.error
            reply = result.reply
            successfulWrite = True
            if operation.name == "write":
                successfulWrite = self._renderWrite(reply)
            _requireOk(reply)
            if operation.name == "open":
                sessionId = str(getattr(reply, "session_id", ""))
                runtimeId = str(getattr(reply, "runtime_instance_id", ""))
                if result.cleaned or not sessionId or not runtimeId or bool(getattr(reply, "write_enabled", False)):
                    raise RuntimeError("Runtime 未返回已锁定写入的可用 PLC 会话")
                self._sessionId, self._runtimeId = sessionId, runtimeId
                operation.claimed = True
                self._heartbeatTimer.start()
            self._acceptSessionReply(reply, updateCommunication=operation.name in ("open", "read", "write"))
            if operation.name == "open":
                self._writeLockFresh = True
            if operation.name == "set_write_enabled":
                if bool(getattr(reply, "write_enabled", False)) != operation.params["enabled"]:
                    raise RuntimeError("Runtime 写入锁确认与请求不一致")
                self._writeEnabled = bool(reply.write_enabled)
            if operation.name == "read":
                self._renderRead(self._resultJson(reply), int(getattr(reply, "timestamp_ms", 0)))
            if operation.name == "write" and not successfulWrite:
                raise RuntimeError(self.receiptLabel.text())
            if operation.name == "read" and self._dispatchPendingWrite():
                return
            message = str(getattr(reply, "message", ""))
            self._setStatus(message if message and message != "ok" else _COMMANDS[operation.name] + "完成")
            if operation.name != "open":
                self._schedulePoll()
        except Exception as error:
            self._fail(str(error))

    def _acceptSessionReply(self, reply: Any, *, updateWriteLock: bool = True,
                            updateCommunication: bool = False) -> None:
        _requireOk(reply)
        if (str(getattr(reply, "session_id", "")), str(getattr(reply, "runtime_instance_id", ""))) != (self._sessionId, self._runtimeId):
            raise RuntimeError("Runtime 返回了不同的 PLC 会话标识")
        state = str(getattr(reply, "state", ""))
        if state not in ("connected", "verified"):
            raise RuntimeError(str(getattr(reply, "message", "")) or "PLC 会话状态：" + _STATES.get(state, state))
        version = int(getattr(reply, "write_lock_generation", 0))
        olderPermission = version < self._writeLockGeneration
        self._cacheWriteLockVersion(reply)
        if self._state != "verified" or state == "verified":
            self._state = state
        if updateWriteLock and not olderPermission and not bool(getattr(reply, "write_enabled", False)):
            self._writeEnabled = False
        ttl = int(getattr(reply, "ttl_ms", 0))
        if ttl > 0:
            self._leaseTimer.start(ttl)
        if updateCommunication:
            timestamp = int(getattr(reply, "timestamp_ms", 0))
            stamp = QDateTime.fromMSecsSinceEpoch(timestamp).toString("yyyy-MM-dd HH:mm:ss.zzz") if timestamp else "-"
            elapsed = float(getattr(reply, "elapsed_ms", 0))
            label = "最近 PLC 读写" if state == "verified" else "TCP 连接"
            self.metadataLabel.setText(f"{label}：{stamp} | 耗时：{elapsed:.1f} 毫秒")

    def _cacheWriteLockVersion(self, reply: Any) -> bool:
        identity = str(getattr(reply, "session_id", "")), str(getattr(reply, "runtime_instance_id", ""))
        if not self._sessionId or identity != (self._sessionId, self._runtimeId):
            return False
        version = getattr(reply, "write_lock_generation", 0)
        if type(version) is not int or version < 0:
            raise ValueError("Runtime 返回了无效的 PLC 写入锁版本")
        if version > self._writeLockGeneration:
            self._writeLockGeneration = version
            self._writeEnabled = False
        return True

    @staticmethod
    def _resultJson(reply: Any) -> dict:
        payload = json.loads(str(getattr(reply, "result_json", "")) or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Runtime PLC 结果必须为 JSON 对象")
        return payload

    def _renderRead(self, payload: dict, timestampMs: int) -> None:
        values, addresses, words = payload.get("values"), payload.get("addresses"), payload.get("rawWords")
        count = payload.get("count")
        dataType = payload.get("dataType")
        if (dataType not in (*_WORD_TYPES, "bit") or type(count) is not int
                or not 1 <= count <= 960 // plcWordsPerValue(dataType)
                or not isinstance(values, list) or len(values) != count
                or not isinstance(addresses, list) or len(addresses) != count
                or any(not isinstance(address, str) for address in addresses)
                or not isinstance(words, list) or len(words) > 960):
            raise ValueError("Runtime 返回了无效的 PLC 读取结果")
        self.resultTable.setRowCount(count)
        self.resultTable.horizontalHeaderItem(3).setText("原始位" if dataType == "bit" else "原始字")
        stride = plcWordsPerValue(dataType)
        stamp = QDateTime.fromMSecsSinceEpoch(timestampMs).toString("yyyy-MM-dd HH:mm:ss.zzz") if timestampMs else "-"
        for row, value in enumerate(values):
            self.resultTable.setItem(row, 0, QTableWidgetItem(addresses[row]))
            self.resultTable.setItem(row, 1, QTableWidgetItem(dataType))
            self.resultTable.setItem(row, 2, QTableWidgetItem(str(value)))
            raw = ("1" if value else "0") if dataType == "bit" else ", ".join(
                str(word) for word in words[row * stride:(row + 1) * stride]
            )
            self.resultTable.setItem(row, 3, QTableWidgetItem(raw))
            self.resultTable.setItem(row, 4, QTableWidgetItem(stamp))
            for column in range(self.resultTable.columnCount()):
                item = self.resultTable.item(row, column)
                item.setToolTip(item.text())

    def _unknownWrite(self, message: str) -> None:
        self.receiptLabel.setText("结果未知 (unknown)：写入结果尚未确认。" + message)
        self.receiptLabel.setStyleSheet("color: #a05a00;")

    def _renderWrite(self, reply: Any) -> bool:
        try:
            payload = self._resultJson(reply)
            receipt = payload.get("receipt", {})
            outcome = receipt.get("outcome") if isinstance(receipt, dict) else None
            if outcome not in ("confirmed", "unknown", "rejected"):
                raise ValueError("Runtime 未返回可识别的写入回执")
        except (ValueError, TypeError, AttributeError) as error:
            self._unknownWrite(str(error))
            raise
        try:
            label = {"confirmed": "已确认", "unknown": "结果未知", "rejected": "已拒绝"}[outcome]
            message = f"{label} ({outcome})：{receipt.get('valueCount', '?')} 个值"
            if outcome == "unknown":
                message += "，写入结果尚未确认"
            readbackError = str(payload.get("readbackError", ""))
            if readbackError:
                message += "\n回读失败：" + readbackError
            self.receiptLabel.setText(message)
            self.receiptLabel.setToolTip(json.dumps(receipt, ensure_ascii=True))
            color = {"confirmed": "#25703d", "unknown": "#a05a00", "rejected": "#b42318"}[outcome]
            self.receiptLabel.setStyleSheet("color: " + color + ";")
            if payload.get("readback") is not None:
                self._renderRead(payload["readback"], int(getattr(reply, "timestamp_ms", 0)))
            return outcome == "confirmed" and not readbackError
        except (ValueError, TypeError, AttributeError) as error:
            self.receiptLabel.setText(message + "\n回读失败：" + str(error))
            return False

    def _setStatus(self, message: str, *, error: bool = False) -> None:
        lock = "允许写入" if self._writeEnabled else "写入已锁定"
        self.statusLabel.setText(f"{_STATES[self._state]} | {lock} | {message}")
        self.statusLabel.setToolTip(message)
        self.statusLabel.setStyleSheet("color: #b42318;" if error else "")
        method = getattr(self.context, "setError" if error else "setStatus", None)
        if callable(method):
            summary = message.splitlines()[0][:32]
            method(summary + ("..." if summary != message else ""))
        if error:
            self.context.log("WARN", "PLC 调试：" + message)

    def _refreshControls(self) -> None:
        active = self._active()
        busy = self._operation is not None or self._safetyPending()
        connected = bool(self._sessionId)
        self.connectButton.setEnabled(active and not connected and not busy)
        self.disconnectButton.setEnabled(connected or self._operation is not None)
        self.reloadButton.setEnabled(active and not self._safetyPending())
        for control in self._endpointControls.values():
            control.setEnabled(active and not connected and not busy)
        self.readButton.setEnabled(active and connected and not busy)
        self.writeButton.setEnabled(self._canWrite())
        self.writeEnabledCheck.setEnabled(active and connected and (not busy or self._writeEnabled))
        self._checkQuietly(self.writeEnabledCheck, self._writeEnabled)
        self.pollCheck.setEnabled(active and connected)
