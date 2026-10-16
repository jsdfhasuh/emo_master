"""Native Qt views for Runtime-owned communication; these widgets do no I/O."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re
from uuid import uuid4

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from emo_master.core.project.communication import CommunicationSettings


def summary(state):
    if state is None:
        return "TCP 状态未知"
    if not state.get("projectId"):
        return "TCP · 请先打开项目"
    if not state.get("enabled"):
        return "TCP 未启用"
    current = state.get("state", "UNKNOWN")
    if current == "LISTENING":
        channels = state.get("channels", [])
        count = sum(bool(c["connected"]) for c in channels)
        label = "全部连接" if count and count == len(channels) else "部分连接" if count else "等待客户端"
        return f"TCP 监听中 {count}/{len(channels)} · {label}"
    return {"STOPPED": "TCP 未启动", "FAILED": "TCP 启动/运行失败", "STARTING": "TCP 启动中",
            "STOPPING": "TCP 停止中"}.get(current, "TCP 状态未知")


def label(text=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    return widget


def pendingChanges(state):
    """Compare saved and effective settings without treating either as live I/O."""
    before = state["effective"]["tcpServer"]
    after = state["desired"]["tcpServer"]
    changes = []
    for key, title, unit in (("name", "服务名称", ""), ("bindAddress", "监听地址", ""),
                             ("port", "端口", ""), ("sendTimeoutMs", "发送超时", " 毫秒"),
                             ("maxMessageBytes", "报文上限", " 字节")):
        if before[key] != after[key]:
            changes.append(f"{title} {before[key]}{unit} → {after[key]}{unit}")
    old = {item["channelId"]: item for item in before["channels"]}
    new = {item["channelId"]: item for item in after["channels"]}
    changed = [key for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]
    if changed:
        channels = []
        for key in changed[:4]:
            item = new.get(key) or old[key]
            action = "新增" if key not in old else "移除" if key not in new else "修改"
            channels.append(f"{action} {item['name']} ({key})")
        changes.append(f"通道变更 {len(changed)} 项：" + "、".join(channels) + ("…" if len(changed) > 4 else ""))
    return "；".join(changes)


class CommunicationSettingsDialog(QDialog):
    def __init__(self, projectName, config, save, parent=None):
        super().__init__(parent)
        self.setWindowTitle("项目 TCP 通信设置")
        self.resize(780, 650)
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.resize(min(780, max(480, int(area.width() * .9))), min(650, max(360, int(area.height() * .9))))
        self.save = save
        self.original = CommunicationSettings.model_validate(config).model_dump()
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"项目：{projectName}\n配置随此项目保存，不影响其他项目。仅打开项目不启动监听。"))
        self.enabled = QCheckBox("本项目启用 TCP 通信")
        self.enabled.setObjectName("tcpProjectEnabled")
        self.enabled.setChecked(self.original["enabled"])
        layout.addWidget(self.enabled)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.fields = QWidget()
        formLayout = QVBoxLayout(self.fields)
        form = QFormLayout()
        self.policy = QComboBox()
        self.policy.addItem("随项目开始运行", "on_project_run")
        self.policy.addItem("手动启动", "manual")
        self.policy.setCurrentIndex(self.policy.findData(self.original["startupPolicy"]))
        form.addRow("启动方式", self.policy)
        server = self.original["tcpServer"]
        self.serviceName = QLineEdit(server["name"])
        self.serviceName.setMaxLength(80)
        self.address = QLineEdit(server["bindAddress"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(server["port"])
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 60000)
        self.timeout.setSuffix(" 毫秒")
        self.timeout.setValue(server["sendTimeoutMs"])
        self.byteLimit = QSpinBox()
        self.byteLimit.setRange(1, 16777216)
        self.byteLimit.setSuffix(" 字节")
        self.byteLimit.setValue(server["maxMessageBytes"])
        for title, widget in (("服务名称", self.serviceName), ("Runtime 监听 IPv4", self.address),
                              ("监听端口", self.port), ("发送超时", self.timeout), ("单条报文上限", self.byteLimit)):
            form.addRow(title, widget)
        formLayout.addLayout(form)
        formLayout.addWidget(label("监听地址属于 Runtime 机器，不是 Designer。0.0.0.0 会暴露所有 IPv4 网卡。\nIP 白名单只用于接入路由，不等于设备身份认证。"))
        self.channels = QTableWidget(0, 3)
        self.channels.setObjectName("tcpChannelMappings")
        self.channels.setHorizontalHeaderLabels(["通道 ID（稳定引用）", "显示名称", "允许客户端 IPv4（逗号分隔）"])
        self.channels.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.channels.setMinimumHeight(160)
        for channel in server["channels"]:
            self.addChannel(channel)
        formLayout.addWidget(self.channels)
        row = QHBoxLayout()
        add = QPushButton("添加通道")
        remove = QPushButton("移除选中通道")
        add.clicked.connect(lambda: self.addChannel())
        remove.clicked.connect(self.removeChannel)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        formLayout.addLayout(row)
        scroll.setWidget(self.fields)
        layout.addWidget(scroll, 1)
        self.fields.setEnabled(self.enabled.isChecked())
        self.enabled.toggled.connect(self.fields.setEnabled)
        layout.addWidget(label("保存不启动、不重启服务。运行中修改连接参数将在下次启动生效。\n关闭项目开关前必须先停止服务；停止流程并不停止通信。"))
        self.error = label()
        self.error.setObjectName("tcpSettingsError")
        self.error.setStyleSheet("color: #c23b3b")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存配置")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.saveDraft)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def addChannel(self, channel=None):
        if self.channels.rowCount() >= 64:
            return
        channel = channel or dict(channelId="ch-" + uuid4().hex[:10], name=f"通道 {self.channels.rowCount()+1}", allowedIps=[])
        index = self.channels.rowCount()
        self.channels.insertRow(index)
        for col, value in enumerate((channel["channelId"], channel["name"], ", ".join(channel["allowedIps"]))):
            item = QTableWidgetItem(value)
            if col == 0:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip("通道 ID 不随名称更改；被流程引用的通道不能直接删除。")
            self.channels.setItem(index, col, item)

    def removeChannel(self):
        row = self.channels.currentRow()
        if row >= 0:
            self.channels.removeRow(row)

    def draft(self):
        value = deepcopy(self.original)
        value.update(enabled=self.enabled.isChecked(), startupPolicy=self.policy.currentData())
        value["tcpServer"].update(name=self.serviceName.text().strip(), bindAddress=self.address.text().strip(),
            port=self.port.value(), sendTimeoutMs=self.timeout.value(), maxMessageBytes=self.byteLimit.value(),
            channels=[dict(channelId=self.channels.item(row, 0).text(), name=self.channels.item(row, 1).text().strip(),
                allowedIps=[ip for ip in re.split(r"[,，;；\s]+", self.channels.item(row, 2).text().strip()) if ip])
                for row in range(self.channels.rowCount())])
        return CommunicationSettings.model_validate(value).model_dump()

    def saveDraft(self):
        try:
            result = self.save(self.draft())
            if result:
                raise ValueError(result)
        except Exception as error:
            self.error.setText(str(error))
            return
        self.accept()


class CommunicationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.stateLabel = label("TCP · 请先打开项目")
        row.addWidget(self.stateLabel, 1)
        self.startButton, self.stopButton, self.settingsButton, self.syncButton = [QPushButton(t) for t in ("启动服务", "停止服务", "项目设置…", "同步已保存配置")]
        for button in (self.startButton, self.stopButton, self.syncButton, self.settingsButton):
            row.addWidget(button)
        layout.addLayout(row)
        self.identityLabel = label()
        self.notice = label()
        layout.addWidget(self.identityLabel)
        layout.addWidget(self.notice)
        splitter = QSplitter(Qt.Vertical)
        self.channels = QTableWidget(0, 6)
        self.channels.setHorizontalHeaderLabels(["通道", "允许地址", "实际对端", "连接状态", "连接代次", "最近发送"])
        self.channels.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.channels.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        splitter.addWidget(self.channels)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        filters = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem("所有通道", "")
        self.format = QComboBox()
        self.format.addItems(["HEX", "UTF-8 文本"])
        self.navigateButton = QPushButton("定位来源节点")
        filters.addWidget(self.filter)
        filters.addWidget(self.format)
        filters.addWidget(label("RX 为原始字节块；TX 回执仅表示传输，不表示设备动作完成。"), 1)
        filters.addWidget(self.navigateButton)
        bl.addLayout(filters)
        self.records = QTreeWidget()
        self.records.setHeaderLabels(["时间", "类型", "通道", "结果 / 事件", "字节"])
        self.records.setRootIsDecorated(False)
        self.records.setMinimumHeight(90)
        for column in (0, 1, 4):
            self.records.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.records.header().setSectionResizeMode(2, QHeaderView.Interactive)
        self.records.setColumnWidth(2, 140)
        self.records.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.records.header().setStretchLastSection(False)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(160)
        bl.addWidget(self.records, 1)
        bl.addWidget(self.details)
        splitter.addWidget(bottom)
        layout.addWidget(splitter, 1)
        self.history = []
        self.filter.currentIndexChanged.connect(self.renderRecords)
        self.format.currentIndexChanged.connect(self.renderDetails)
        self.records.itemSelectionChanged.connect(self.renderDetails)
        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(False)

    def setState(self, state, history, *, busy=False, message=""):
        self.history = history
        self.stateLabel.setText(summary(state))
        notice = message or (state.get("error", "") if state else
                            "Runtime 状态未知；下方仅为历史记录，不代表服务已停止。")
        if state and state.get("pending"):
            notice += " 已保存参数待下次启动生效（当前 → 已保存）：" + pendingChanges(state)
        if state and state.get("gap"):
            notice += " 记录存在断档；当前通道状态已按完整快照同步。"
        savePending = bool(state and (state.get("savePending") or state.get("disableSavePending")))
        if savePending:
            notice += " 通信配置保存保护尚未结束，禁止启动或切换工程。请在发起保存的窗口完成同步；若该窗口已丢失，请重启 Runtime 后重新加载已保存工程。"
        if state and not state.get("enabled") and not notice:
            notice = "此项目不使用 TCP 通信；已保存参数保留，不监听、不等待客户端。"
        self.notice.setText(notice)
        self.startButton.setEnabled(bool(state and state.get("enabled") and not savePending and state.get("state") in {"STOPPED", "FAILED"} and not busy))
        self.stopButton.setEnabled(bool(state and state.get("state") in {"LISTENING", "FAILED"} and not busy))
        self.settingsButton.setEnabled(not busy)
        self.syncButton.setEnabled(not busy)
        self.channels.setRowCount(0)
        if state:
            server = state["effective"]["tcpServer"]
            if state.get("localOnly"):
                self.identityLabel.setText(f"{server['name']} · 本地已保存配置，尚无此项目的 Runtime 状态确认")
            else:
                self.identityLabel.setText(f"{server['name']} · 生效端点 {server['bindAddress']}:{server['port']}\nRuntime {state['runtimeInstanceId']} · 服务代次 {state['serviceGeneration']} · 丢弃历史记录 {state.get('droppedRecords', 0)}")
            selected = self.filter.currentData()
            options = [("所有通道", "")] + [(c["name"], c["channelId"]) for c in state["channels"]]
            if [(self.filter.itemText(i), self.filter.itemData(i)) for i in range(self.filter.count())] != options:
                self.filter.blockSignals(True)
                self.filter.clear()
                for title, key in options:
                    self.filter.addItem(title, key)
                self.filter.setCurrentIndex(max(0, self.filter.findData(selected)))
                self.filter.blockSignals(False)
            for c in state["channels"]:
                row = self.channels.rowCount()
                self.channels.insertRow(row)
                peer = c.get("peerAddress")
                values = [f"{c['name']} ({c['channelId']})", ", ".join(c["allowedIps"]),
                          f"{peer[0]}:{peer[1]}" if peer else "—", "已连接" if c["connected"] else "未连接",
                          str(c["connectionGeneration"]), (c.get("lastSend") or {}).get("outcome", "—")]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.channels.setItem(row, col, item)
        else:
            self.identityLabel.setText("实时连接信息不可用")
        self.renderRecords()

    def renderRecords(self):
        selected = self.currentRecord().get("sequence")
        self.records.blockSignals(True)
        self.records.clear()
        for record in self.history:
            if self.filter.currentData() and record.get("channelId") != self.filter.currentData():
                continue
            item = QTreeWidgetItem([datetime.fromtimestamp(record["timestampMs"]/1000).strftime("%H:%M:%S.%f")[:-3],
                record["kind"].upper(), record.get("channelId", ""), record.get("outcome") or record["message"], str(record.get("bytes", ""))])
            for column in range(5):
                item.setToolTip(column, item.text(column))
            item.setData(0, Qt.UserRole, record)
            self.records.addTopLevelItem(item)
            if record.get("sequence") == selected:
                self.records.setCurrentItem(item)
        self.records.blockSignals(False)
        self.renderDetails()

    def currentRecord(self):
        item = self.records.currentItem()
        return item.data(0, Qt.UserRole) if item else {}

    def renderDetails(self):
        record = self.currentRecord()
        self.navigateButton.setEnabled(bool(record.get("source", {}).get("nodeId")))
        if not record:
            self.details.clear()
            return
        data = record.get("dataHex", "")
        if self.format.currentIndex() == 1 and data:
            data = repr(bytes.fromhex(data).decode("utf-8", errors="replace"))
        self.details.setPlainText(data + ("\n[原始数据已截断]" if record.get("truncated") else "") + "\n" +
                                 json.dumps({k: v for k, v in record.items() if k != "dataHex"}, ensure_ascii=False, indent=2))
