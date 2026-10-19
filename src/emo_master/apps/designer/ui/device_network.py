"""Discovery is explicit; adopting a selector and mutating hardware are separate."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QLineEdit, QLabel, QPushButton, QMessageBox)

from .icon_map import icon


class DeviceNetworkDialog(QDialog):
    def __init__(self, client, projectId, runtimeId, deviceId, state, parent=None, *, discoveryOnly=False):
        super().__init__(parent)
        self.client, self.projectId, self.runtimeId, self.deviceId = client, projectId, runtimeId, deviceId
        self.state = dict(state)
        self.discoveryOnly = discoveryOnly
        self.discovery = None
        self.selectedCameraKey = None
        self.pending = None
        self.kind = ''
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='device-network-ui')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.finish)
        self.setWindowTitle('Runtime 设备网络')
        self.resize(980, 600)
        self.setMinimumSize(640, 420)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.discoverButton = QPushButton('发现 Runtime 主机设备')
        self.discoverButton.setIcon(icon('search'))
        self.discoverButton.clicked.connect(lambda: self.start('discover'))
        bar.addWidget(self.discoverButton)
        self.adoptButton = QPushButton('采用所选标识到草稿')
        self.adoptButton.setIcon(icon('shield-check'))
        self.adoptButton.clicked.connect(self.adopt)
        bar.addWidget(self.adoptButton)
        bar.addStretch()
        layout.addLayout(bar)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['设备标识', '型号', '序列号', 'MAC', '设备 IPv4', '子网掩码', 'Runtime 网卡 IPv4'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.currentCellChanged.connect(self.select)
        layout.addWidget(self.table, 1)
        form = QFormLayout()
        self.fields = {}
        for key, label in [('ipAddress', '设备 IPv4'), ('subnetMask', '子网掩码'), ('defaultGateWay', '默认网关')]:
            field = QLineEdit()
            field.setMaxLength(15)
            form.addRow(label, field)
            self.fields[key] = field
        self.forceButton = QPushButton('修改实物设备 IP')
        self.forceButton.setIcon(icon('sliders-horizontal'))
        self.forceButton.clicked.connect(self.forceIp)
        form.addRow(self.forceButton)
        self.receiptId = QLineEdit()
        self.receiptId.setMaxLength(160)
        form.addRow('回执 ID', self.receiptId)
        self.receiptButton = QPushButton('查询回执')
        self.receiptButton.setIcon(icon('refresh-cw'))
        self.receiptButton.clicked.connect(lambda: self.start('status', requestId=self.receiptId.text().strip()))
        form.addRow(self.receiptButton)
        layout.addLayout(form)
        if discoveryOnly:
            for index in range(form.count()):
                widget = form.itemAt(index).widget()
                if widget is not None:
                    widget.hide()
        self.status = QLabel('尚未发现设备')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.controls()

    def row(self):
        index = self.table.currentRow()
        return self.discovery['devices'][index] if self.discovery and 0 <= index < len(self.discovery['devices']) else None

    def controls(self):
        available = self.pending is None
        self.discoverButton.setEnabled(available)
        self.receiptButton.setEnabled(available)
        self.adoptButton.setEnabled(available and self.row() is not None)
        self.forceButton.setEnabled(available and self.row() is not None and self.row().get('cameraType') == 0)

    def select(self, *_args):
        row = self.row() or {}
        for key, field in self.fields.items():
            field.setText(row.get(key, ''))
        self.controls()

    def start(self, action, **kwargs):
        if self.pending is not None:
            return
        self.kind = action
        self.pending = self.pool.submit(self.client.deviceNetworkCommand, self.projectId, self.runtimeId, action,
                                        deviceId=self.deviceId, **kwargs)
        self.status.setText('等待 Runtime 回执')
        self.controls()
        self.timer.start(40)

    def finish(self):
        if self.pending is None or not self.pending.done():
            return
        future, self.pending = self.pending, None
        self.timer.stop()
        try:
            result = future.result()
            if self.kind == 'discover':
                self.discovery = result
                self.table.setRowCount(0)
                for row in result['devices']:
                    index = self.table.rowCount()
                    self.table.insertRow(index)
                    for column, key in enumerate(('cameraKey', 'modelName', 'serialNumber', 'macAddress', 'ipAddress', 'subnetMask', 'interfaceIp')):
                        self.table.setItem(index, column, QTableWidgetItem(str(row.get(key, ''))))
                self.status.setText(f"已发现 {len(result['devices'])} 台设备 · SDK {result.get('sdkVersion', '')}")
            else:
                self.status.setText(result.get('state', '') + ' · ' + result.get('message', '') + '\n回执: ' + result.get('requestId', ''))
                if self.kind == 'force_ip':
                    self.discovery = None
                    self.table.setRowCount(0)
                    if result.get('state') == 'COMPLETED':
                        for key, field in self.fields.items():
                            field.setText(result.get('after', {}).get(key, ''))
                        self.status.setText('实物 IP 回读已确认；设备仍暂停连接。工程草稿未修改。\n回执: ' + result['requestId'])
        except Exception as error:
            self.status.setText(('结果尚未确认，请查询回执；不得直接重发。\n' if self.kind == 'force_ip' else '') + str(error))
            self.discovery = None
            self.table.setRowCount(0)
        self.controls()

    def adopt(self):
        row = self.row()
        if row is not None and self.pending is None:
            self.selectedCameraKey = row['cameraKey']
            self.accept()

    def forceIp(self):
        if self.discoveryOnly:
            return
        row = self.row()
        if row is None or self.pending is not None:
            return
        values = {key: field.text().strip() for key, field in self.fields.items()}
        message = (f"设备: {row['cameraKey']}\nMAC: {row.get('macAddress', '')}\n"
                   f"IPv4: {row.get('ipAddress', '')} → {values['ipAddress']}\n"
                   f"掩码: {values['subnetMask']}\n网关: {values['defaultGateWay']}\n\n"
                   '这会修改实物设备，不是工程连接参数。相关任务须先停止，设备须明确断开并暂停。'
                   '\n使用 SDK ForceIP，不承诺断电后保留；不会自动重连或重发。')
        if QMessageBox.question(self, '确认修改实物 IP', message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        requestId = uuid4().hex
        self.receiptId.setText(requestId)
        self.start('force_ip', requestId=requestId, payload=dict(values, discoveryId=self.discovery['discoveryId'],
            cameraKey=row['cameraKey'], expectedGeneration=self.state.get('generation', ''),
            expectedDefinitionRevision=self.state.get('definitionRevision', ''), confirmed=True))

    def done(self, result):
        if self.pending is not None:
            self.status.setText('操作尚未返回，等待回执或超时后再关闭。')
            return
        self.timer.stop()
        self.pool.shutdown(wait=False)
        super().done(result)

    def closeEvent(self, event):
        if self.pending is not None:
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        self.done(QDialog.Rejected)
