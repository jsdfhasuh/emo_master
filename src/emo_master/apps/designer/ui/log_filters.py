"""Date ranges and known identity choices in the Designer log window."""
from PySide2.QtCore import QDateTime, QTimeZone, Qt, Signal
from PySide2.QtGui import QStandardItem, QStandardItemModel
from PySide2.QtWidgets import QCheckBox, QCompleter, QDateTimeEdit, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


_DATE_FORMAT = "yyyy-MM-dd HH:mm:ss.zzz"
_IDENTITY_FIELDS = ("workflowId", "nodeId", "deviceId")


def describeDateBounds(bounds):
    return "，".join(
        f"{label} {QDateTime.fromMSecsSinceEpoch(value, Qt.LocalTime).toString(_DATE_FORMAT)}"
        for label, value in zip(("起点（含）", "终点（不含）"), bounds) if value is not None
    )


def includesTimestamp(timestamp, bounds):
    start, end = bounds
    if start is None and end is None:
        return True
    return (type(timestamp) is int and timestamp > 0
            and (start is None or timestamp >= start)
            and (end is None or timestamp < end))


class OptionalDateTimeRange(QWidget):
    changed = Signal()

    def __init__(self, title="事件时间范围", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.startControl, self.startEdit = self._addBound(row, "开始（含）", title)
        self.endControl, self.endEdit = self._addBound(row, "结束（不含）", title)
        row.addStretch()
        layout.addLayout(row)
        note = QHBoxLayout()
        zone = bytes(QTimeZone.systemTimeZoneId()).decode("utf-8", errors="replace")
        note.addWidget(QLabel(f"{title} · 本机时区 {zone} · 未勾选为不限"))
        self.error = QLabel("开始时间不能晚于结束时间")
        self.error.setWordWrap(True)
        self.error.hide()
        note.addWidget(self.error, 1)
        layout.addLayout(note)
        self.setBounds(None, None)

    def _addBound(self, row, label, title):
        enabled = QCheckBox(label)
        enabled.setAccessibleName(f"{title} {label}")
        edit = QDateTimeEdit()
        edit.setAccessibleName(f"{title} {label}日期时间")
        edit.setDisplayFormat(_DATE_FORMAT)
        edit.setTimeSpec(Qt.LocalTime)
        edit.setMinimumDateTime(QDateTime.fromMSecsSinceEpoch(0, Qt.LocalTime))
        edit.setCalendarPopup(True)
        edit.setKeyboardTracking(False)
        edit.dateTimeChanged.connect(self._changed)
        enabled.toggled.connect(self._changed)
        row.addWidget(enabled)
        row.addWidget(edit)
        return enabled, edit

    def bounds(self):
        return (
            self.startEdit.dateTime().toMSecsSinceEpoch() if self.startControl.isChecked() else None,
            self.endEdit.dateTime().toMSecsSinceEpoch() if self.endControl.isChecked() else None,
        )

    def isValidRange(self):
        start, end = self.bounds()
        return start is None or end is None or start <= end

    def options(self, startKey="startTimestampMs", endKey="endTimestampMs"):
        return {name: value for name, value in zip((startKey, endKey), self.bounds()) if value is not None}

    def setBounds(self, start, end):
        previous = self.blockSignals(True)
        for control, edit, value in ((self.startControl, self.startEdit, start), (self.endControl, self.endEdit, end)):
            edit.setDateTime(QDateTime.currentDateTime() if value is None
                             else QDateTime.fromMSecsSinceEpoch(int(value), Qt.LocalTime))
            control.setChecked(value is not None)
        self.blockSignals(previous)
        self._changed()

    def _changed(self, *_):
        self.startEdit.setEnabled(self.startControl.isChecked())
        self.endEdit.setEnabled(self.endControl.isChecked())
        self.error.setVisible(not self.isValidRange())
        self.changed.emit()


def deviceChoices(entry):
    payload = entry.payload
    direct = payload.get("deviceId")
    if isinstance(direct, str) and direct:
        return [(payload.get("slot", "") or direct, direct, payload.get("scope") or payload)]
    bindings = payload.get("deviceBindings")
    if not isinstance(bindings, dict):
        return []
    diagnostics = payload.get("diagnostics")
    slot = diagnostics.get("deviceSlot") if isinstance(diagnostics, dict) else None
    return [(key, binding["deviceId"], binding.get("scope") or {}) for key, binding in bindings.items()
            if (not slot or key == slot) and isinstance(binding, dict) and binding.get("deviceId")]


class _IdentityCompleter(QCompleter):
    def __init__(self, edit, inputs):
        self.rows = ()
        self.entries = QStandardItemModel(edit)
        super().__init__(self.entries, edit)
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterMode(Qt.MatchContains)
        self.setCompletionRole(Qt.DisplayRole)
        self.activated["QModelIndex"].connect(lambda index: self._selectScope(inputs, index))
        edit.setCompleter(self)

    @staticmethod
    def _selectScope(inputs, index):
        _, project, workflow = index.data(Qt.UserRole)
        for field, value in (("projectId", project), ("workflowId", workflow)):
            if value and field in inputs and not inputs[field].isReadOnly():
                inputs[field].setText(value)

    def pathFromIndex(self, index):
        return str(index.data(Qt.UserRole)[0])

    def setRows(self, rows):
        ordered = tuple(sorted(rows.items()))
        if ordered == self.rows:
            return
        self.rows = ordered
        self.entries.clear()
        for identity, label in ordered:
            item = QStandardItem(label)
            item.setData(identity, Qt.UserRole)
            self.entries.appendRow(item)


def updateIdentityChoices(inputs, entries):
    choices = {key: {} for key in _IDENTITY_FIELDS}
    for entry in entries:
        if entry.workflowId:
            choices["workflowId"][(entry.workflowId, entry.projectId, "")] = (
                f"{entry.displayWorkflow()} · {entry.workflowId} · 工程 {entry.projectId}")
        if entry.nodeId:
            choices["nodeId"][(entry.nodeId, entry.projectId, entry.workflowId)] = (
                f"{entry.displayNode()} · {entry.nodeId} · 工作流 {entry.displayWorkflow()}（{entry.workflowId}）"
                f" · 工程 {entry.projectId}")
        for label, deviceId, _ in deviceChoices(entry):
            choices["deviceId"][(deviceId, entry.projectId, "")] = f"{label} · {deviceId} · 工程 {entry.projectId}"
    for key, rows in choices.items():
        edit = inputs[key]
        completer = edit.completer()
        if completer is None:
            completer = _IdentityCompleter(edit, inputs)
        completer.setRows(rows)
