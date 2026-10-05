"""Native field-mapping editor. It never executes a workflow or caches results."""
from copy import deepcopy
import json

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from shiboken2 import isValid

from emo_master.apps.designer.ui.widgets import WrapLabel, scrollContent
from emo_master.apps.designer.operator_editors.sqlite_requests import requests
from emo_master.apps.designer.operator_editors.sqlite_sources import validateDraftMappings
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.contracts.sqlite_writer import (
    CONTEXT_TYPES, MAX_FIELDS, STORAGE_TYPES, SqliteWriterError, acceptsSource, jsonValue,
)


def _combo(items, value=None):
    combo = QComboBox()
    for label, key in items:
        combo.addItem(label, key)
    if value is not None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
    return combo


class SourceDialog(QDialog):
    def __init__(self, parent, sources, source, storage):
        super().__init__(parent)
        self.setWindowTitle('选择数据来源 · 同一流程的完整输出')
        self.resize(580, 450)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['节点 / 正式输出端口', '类型与说明'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 170)
        for node in sources:
            group = QTreeWidgetItem([node['name'], node.get('error', '')])
            group.setToolTip(0, node['nodeId'])
            self.tree.addTopLevelItem(group)
            for port, spec in node['ports'].items():
                compatible = acceptsSource(spec, storage) and (storage != 'FILE_REFERENCE' or node['operatorId'] == 'vision.io.image_saver')
                optional = isinstance(spec, dict) and not spec.get('required', True)
                detail = normalizePortType(spec) + (' · 可选，可能缺失' if optional else '')
                if not compatible:
                    detail += ' · 类型不兼容'
                item = QTreeWidgetItem(group, [port, detail])
                item.setData(0, Qt.UserRole, {'kind': 'node_output', 'nodeId': node['nodeId'], 'port': port})
                item.setToolTip(0, f"{node['nodeId']}.{port}")
                if not compatible:
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                if source == item.data(0, Qt.UserRole):
                    self.tree.setCurrentItem(item)
            group.setExpanded(True)
        self.tabs.addTab(self.tree, '节点 → 输出端口')
        self.constant = QPlainTextEdit()
        self.constant.setPlaceholderText('完整 JSON 常量，如 0、false、"文本"、null、[1,2]；不是表达式')
        self.constant.setPlainText(json.dumps(source.get('value', ''), ensure_ascii=False))
        self.tabs.addTab(self.constant, '常量')
        self.context = _combo([(key, key) for key in CONTEXT_TYPES], source.get('key'))
        container = QWidget()
        form = QFormLayout(container)
        form.addRow('执行上下文', self.context)
        self.tabs.addTab(container, '执行上下文')
        self.tabs.setCurrentIndex({'node_output': 0, 'constant': 1, 'context': 2}.get(source.get('kind'), 0))
        layout.addWidget(self.tabs)
        self.error = QLabel('只选择完整端口；不支持表达式、JSONPath 或数组索引。')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tree.itemDoubleClicked.connect(lambda *_args: self.accept())
        self.source = deepcopy(source)

    def accept(self):
        try:
            if self.tabs.currentIndex() == 0:
                item = self.tree.currentItem()
                value = item.data(0, Qt.UserRole) if item is not None else None
                if not value or not item.flags() & Qt.ItemIsEnabled:
                    raise ValueError('请选择兼容的正式输出端口')
                self.source = deepcopy(value)
            elif self.tabs.currentIndex() == 1:
                self.source = {'kind': 'constant', 'value': jsonValue(json.loads(self.constant.toPlainText()))}
            else:
                self.source = {'kind': 'context', 'key': self.context.currentData()}
        except (ValueError, TypeError) as error:
            self.error.setText(str(error))
            return
        super().accept()


class InitializationDialog(QDialog):
    """Explicit, scrollable confirmation without the platform message-box icon."""
    def __init__(self, parent, message):
        super().__init__(parent)
        self.setWindowTitle('确认初始化 SQLite')
        self.resize(780, 420)
        layout = QVBoxLayout(self)
        self.message = QPlainTextEdit(message)
        self.message.setReadOnly(True)
        layout.addWidget(self.message, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        self.yes = buttons.button(QDialogButtonBox.Yes)
        self.no = buttons.button(QDialogButtonBox.No)
        self.yes.setText('明确创建')
        self.no.setText('取消')
        self.no.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SqliteWriterEditorController:
    def __init__(self):
        self._loading = False
        self._closed = False
        self._generation = 0
        self._pending = None
        self._preview = None
        self._schema = None
        self._dialogs = []

    def bind(self, rootWidget, context):
        self.root, self.context = rootWidget, context
        content = QWidget()
        self.scroll = scrollContent(content, name='sqliteEditorScroll')
        self.root.layout().addWidget(self.scroll)
        layout = QVBoxLayout(content)
        target = QGroupBox('数据库目标 · 文件位于 Runtime 主机')
        form = QFormLayout(target)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.path = QLineEdit()
        self.path.setObjectName('sqliteDatabasePath')
        self.path.setPlaceholderText('已有或待明确创建的本地文件；相对原工程目录解析')
        pathRow = QWidget()
        pathLayout = QHBoxLayout(pathRow)
        pathLayout.setContentsMargins(0, 0, 0, 0)
        pathLayout.addWidget(self.path, 1)
        self.browse = QPushButton('选择文件')
        self.browse.setObjectName('sqliteBrowse')
        pathLayout.addWidget(self.browse)
        form.addRow('SQLite 文件', pathRow)
        self.table = QComboBox()
        self.table.setObjectName('sqliteTable')
        self.table.setEditable(True)
        self.table.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        form.addRow('目标表', self.table)
        self.testPath = QLineEdit()
        self.testPath.setPlaceholderText('复杂表结构需要专门测试库；草稿调试绝不写正式库')
        form.addRow('专用测试库（可选）', self.testPath)
        self.location = WrapLabel()
        self.location.setObjectName('sqliteLocation')
        self.location.setWordWrap(True)
        form.addRow('主机 / 最终路径', self.location)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.inspectButton = QPushButton('测试连接 / 刷新结构')
        self.inspectButton.setObjectName('sqliteInspect')
        self.previewButton = QPushButton('预览建表方案')
        self.previewButton.setObjectName('sqlitePreviewPlan')
        self.createButton = QPushButton('明确创建数据库 / 表')
        self.createButton.setObjectName('sqliteInitialize')
        self.createButton.setEnabled(False)
        for button in (self.inspectButton, self.previewButton, self.createButton):
            row.addWidget(button)
        form.addRow(actions)
        self.structure = WrapLabel('尚未检查。普通运行不自动建库、建表或迁移。')
        self.structure.setObjectName('sqliteStructure')
        self.structure.setWordWrap(True)
        self.structure.setTextFormat(Qt.PlainText)
        form.addRow(self.structure)
        layout.addWidget(target)
        fields = QGroupBox('字段映射 · 无需为每个字段增加连线')
        fieldsLayout = QVBoxLayout(fields)
        self.fields = QTableWidget(0, 6)
        self.fields.setMinimumHeight(180)
        self.fields.setObjectName('sqliteMappings')
        self.fields.setHorizontalHeaderLabels(['数据库列', '数据来源', '存储类型', '缺失处理', '新列允许 NULL', '新列默认值 JSON'])
        self.fields.verticalHeader().setVisible(False)
        self.fields.setSelectionBehavior(QTableWidget.SelectRows)
        self.fields.setSelectionMode(QTableWidget.SingleSelection)
        self.fields.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for col, width in ((0, 130), (2, 110), (3, 115), (4, 105), (5, 130)):
            self.fields.setColumnWidth(col, width)
        fieldsLayout.addWidget(self.fields, 1)
        fieldActions = QHBoxLayout()
        self.addButton = QPushButton('添加字段')
        self.deleteButton = QPushButton('删除字段')
        self.upButton = QPushButton('上移')
        self.downButton = QPushButton('下移')
        for button in (self.addButton, self.deleteButton, self.upButton, self.downButton):
            fieldActions.addWidget(button)
        fieldActions.addStretch(1)
        fieldsLayout.addLayout(fieldActions)
        note = WrapLabel('“使用默认值”省略该列，与写入 NULL 不同。新列设置仅用于明确建表，不进入算子映射。')
        note.setWordWrap(True)
        fieldsLayout.addWidget(note)
        layout.addWidget(fields, 1)
        rules = QGroupBox('写入规则')
        rulesLayout = QFormLayout(rules)
        self.failure = _combo([('写入失败停止任务（默认）', 'stop'), ('继续任务，保留失败回执和 ERROR 日志', 'continue')])
        rulesLayout.addRow('失败策略', self.failure)
        rule = WrapLabel('每次调用写一条；集合存 JSON。enabled 未连接默认启用。只有 commit 成功才返回 COMMITTED。取消不会被“继续”吞掉。')
        rule.setWordWrap(True)
        rulesLayout.addRow(rule)
        layout.addWidget(rules)
        self.error = WrapLabel('')
        self.error.setObjectName('sqliteValidationError')
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #c0392b')
        self.error.setTextFormat(Qt.PlainText)
        layout.addWidget(self.error)
        self.path.textChanged.connect(self._changed)
        self.table.currentTextChanged.connect(self._changed)
        self.testPath.textChanged.connect(self._changed)
        self.failure.currentIndexChanged.connect(self._changed)
        self.fields.itemChanged.connect(self._changed)
        self.browse.clicked.connect(self._browse)
        self.addButton.clicked.connect(lambda: self.addMapping())
        self.deleteButton.clicked.connect(self.deleteMapping)
        self.upButton.clicked.connect(lambda: self.moveMapping(-1))
        self.downButton.clicked.connect(lambda: self.moveMapping(1))
        self.inspectButton.clicked.connect(lambda: self._request('inspect'))
        self.previewButton.clicked.connect(lambda: self._request('preview'))
        self.createButton.clicked.connect(self._initialize)
        self.timer = QTimer(self.root)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._poll)
        address, local = context.sqliteLocation()
        self.browse.setEnabled(local)
        self.browse.setToolTip('本机文件选择器' if local else '远程 Runtime：请填写服务端本地路径')
        self.location.setText(address + '\n最终路径：等待 Runtime 检查')

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self.root, '选择已有文件或待创建的 SQLite 文件（此操作不创建）',
                                            self.path.text(), 'SQLite (*.sqlite3 *.db);;所有文件 (*)')
        if path and self._alive():
            self.path.setText(path)

    def _alive(self):
        return not self._closed and isValid(self.root)

    def _openDialog(self, dialog, completed=None):
        """No nested event loop holding a Qt stack frame across project close."""
        self._dialogs.append(dialog)
        def finished(result):
            try:
                if self._alive() and completed is not None:
                    completed(result)
            except Exception as error:
                if self._alive():
                    self.error.setText(str(error))
            finally:
                if dialog in self._dialogs:
                    self._dialogs.remove(dialog)
                if isValid(dialog):
                    dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    def _changed(self, *_args):
        if self._loading or self._closed:
            return
        self._generation += 1
        self._preview = None
        self._schema = None
        self.createButton.setEnabled(False)
        address, _ = self.context.sqliteLocation()
        self.location.setText(address + '\n最终路径：配置已变更，需重新检查')
        self.context.markDirty()

    def _sourceLabel(self, source):
        if source.get('kind') == 'constant':
            value = json.dumps(source.get('value'), ensure_ascii=False)
            return '常量 ' + value[:64], value
        if source.get('kind') == 'context':
            return '上下文 · ' + source.get('key', ''), source.get('key', '')
        try:
            sources = self.context.sqliteDraft()['sources']
        except Exception:
            sources = []
        node = next((n for n in sources if n['nodeId'] == source.get('nodeId')), None)
        name = node['name'] if node else '来源已删除 / 缺失'
        return name[:40] + ' → ' + source.get('port', ''), source.get('nodeId', '') + '.' + source.get('port', '')

    def addMapping(self, value=None, plan=None):
        if self.fields.rowCount() >= MAX_FIELDS:
            self.error.setText('每个算子最多 64 个映射字段')
            return
        value = deepcopy(value or {'column': 'field_' + str(self.fields.rowCount() + 1),
            'storageType': 'TEXT', 'missing': 'error', 'source': {'kind': 'constant', 'value': ''}})
        row = self.fields.rowCount()
        self.fields.insertRow(row)
        self.fields.setItem(row, 0, QTableWidgetItem(value['column']))
        source = QToolButton()
        source.setProperty('source', value['source'])
        source.setToolButtonStyle(Qt.ToolButtonTextOnly)
        text, tooltip = self._sourceLabel(value['source'])
        source.setText(text)
        source.setToolTip(tooltip)
        source.setMaximumWidth(380)
        self.fields.setCellWidget(row, 1, source)
        source.clicked.connect(lambda: self._chooseSource(source))
        storage = _combo([(kind, kind) for kind in STORAGE_TYPES], value['storageType'])
        missing = _combo([('报错', 'error'), ('写入 NULL', 'null'), ('使用数据库默认值', 'default')], value.get('missing', 'error'))
        self.fields.setCellWidget(row, 2, storage)
        self.fields.setCellWidget(row, 3, missing)
        nullable = QCheckBox()
        nullable.setChecked((plan or {}).get('nullable', value.get('missing') == 'null'))
        self.fields.setCellWidget(row, 4, nullable)
        self.fields.setItem(row, 5, QTableWidgetItem(json.dumps(plan['default'], ensure_ascii=False) if plan and 'default' in plan else ''))
        for widget, signal in ((storage, 'currentIndexChanged'), (missing, 'currentIndexChanged'), (nullable, 'toggled')):
            getattr(widget, signal).connect(self._changed)
        self.fields.setRowHeight(row, 36)
        self.fields.selectRow(row)
        self._changed()

    def _chooseSource(self, button):
        if not self._alive():
            return
        generation = self._generation
        row = next((i for i in range(self.fields.rowCount()) if self.fields.cellWidget(i, 1) is button), None)
        if row is None:
            return
        try:
            draft = self.context.sqliteDraft()
            dialog = SourceDialog(self.root, [n for n in draft['sources'] if n['nodeId'] != self.context.key.nodeId],
                                  button.property('source'), self.fields.cellWidget(row, 2).currentData())
            def chosen(result):
                if result == QDialog.Accepted and generation == self._generation and isValid(button):
                    button.setProperty('source', dialog.source)
                    text, tooltip = self._sourceLabel(dialog.source)
                    button.setText(text)
                    button.setToolTip(tooltip)
                    self._changed()
            self._openDialog(dialog, chosen)
        except Exception as error:
            if self._alive():
                self.error.setText(str(error))

    def _rows(self):
        return [{'column': self.fields.item(row, 0).text(),
                 'source': deepcopy(self.fields.cellWidget(row, 1).property('source')),
                 'storageType': self.fields.cellWidget(row, 2).currentData(),
                 'missing': self.fields.cellWidget(row, 3).currentData()} for row in range(self.fields.rowCount())]

    def _plan(self):
        plan = []
        for index, row in enumerate(self._rows()):
            item = {'name': row['column'], 'storageType': row['storageType'],
                    'nullable': self.fields.cellWidget(index, 4).isChecked()}
            default = self.fields.item(index, 5).text().strip()
            if default:
                try:
                    item['default'] = jsonValue(json.loads(default))
                except ValueError as error:
                    raise SqliteWriterError('E_SQLITE_CONFIG', '新列默认值必须为完整 JSON 常量', index) from error
            plan.append(item)
        return plan

    def deleteMapping(self):
        row = self.fields.currentRow()
        if row >= 0:
            self.fields.removeRow(row)
            self._changed()

    def moveMapping(self, delta):
        row = self.fields.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.fields.rowCount():
            return
        try:
            rows, plan = self._rows(), self._plan()
        except SqliteWriterError as error:
            self.error.setText(str(error))
            if error.row is not None:
                self.fields.selectRow(error.row)
            return
        rows[row], rows[target] = rows[target], rows[row]
        plan[row], plan[target] = plan[target], plan[row]
        self._loadRows(rows, plan)
        self.fields.selectRow(target)
        self._changed()

    def _loadRows(self, rows, plans=None):
        old = self._loading
        self._loading = True
        try:
            self.fields.setRowCount(0)
            for index, row in enumerate(rows):
                self.addMapping(row, plans[index] if plans else None)
        finally:
            self._loading = old

    def loadParams(self, params):
        self._generation += 1
        self._preview = None
        self._schema = None
        self.createButton.setEnabled(False)
        self._loading = True
        try:
            self.path.setText(params.get('databasePath', ''))
            self.testPath.setText(params.get('debugDatabasePath', ''))
            self.table.setCurrentText(params.get('table', 'records'))
            self.failure.setCurrentIndex(max(0, self.failure.findData(params.get('failurePolicy', 'stop'))))
            self._loadRows(params.get('mappings', []))
        finally:
            self._loading = False

    def collectParams(self):
        params = {'configVersion': 1, 'databasePath': self.path.text().strip(), 'table': self.table.currentText().strip(),
                  'mappings': self._rows(), 'failurePolicy': self.failure.currentData()}
        if self.testPath.text().strip():
            params['debugDatabasePath'] = self.testPath.text().strip()
        return params

    def validate(self):
        try:
            draft = self.context.sqliteDraft()
            config = validateDraftMappings(self.collectParams(), draft['sources'], draft['workflow'], self.context.key.nodeId)
            if self._schema is not None:
                from emo_master.apps.runtime.business_sqlite.backend import validateMappings
                validateMappings(config, self._schema)
        except Exception as error:
            self.error.setText(str(error))
            row = getattr(error, 'row', None)
            if row is not None and row < self.fields.rowCount():
                self.fields.selectRow(row)
                self.fields.scrollToItem(self.fields.item(row, 0))
            return str(error)
        self.error.clear()
        return None

    def _setBusy(self, busy):
        for widget in (self.inspectButton, self.previewButton, self.fields, self.path, self.table,
                       self.testPath, self.browse, self.addButton, self.deleteButton, self.upButton, self.downButton):
            widget.setEnabled(not busy)
        if not busy:
            self.browse.setEnabled(self.context.sqliteLocation()[1])
        self.createButton.setEnabled(not busy and self._preview is not None)

    def _request(self, action, preview=None):
        if not self._alive() or self._pending is not None:
            return
        try:
            draft = self.context.sqliteDraft()
            path, table, directory = self.path.text().strip(), self.table.currentText().strip(), draft['directory']
            columns = self._plan() if action in {'preview', 'initialize'} else None
            if not path or not table:
                raise ValueError('请填写 SQLite 文件和目标表')
            if action == 'initialize' and (preview is None or preview != self._preview):
                raise ValueError('建表方案已变化，请重新预览')
            if action == 'initialize':
                path = preview[1]
            context = self.context
            def operation(token):
                if action == 'initialize':
                    return context.sqliteTarget('initialize', path, directory, table, columns, confirmed=True, cancellationToken=token)
                return context.sqliteTarget('inspect', path, directory, table, columns, cancellationToken=token)
            future, token = requests.submit(operation)
            self._pending = (future, token, self._generation, action)
            self._setBusy(True)
            self.context.setStatus('正在 Runtime 后台处理；等待实际操作退出后归还额度')
            self.timer.start()
        except Exception as error:
            self.error.setText(str(error))

    def _poll(self):
        if not self._alive() or self._pending is None:
            return
        future, _token, generation, action = self._pending
        if not future.done():
            return
        self._pending = None
        self.timer.stop()
        self._setBusy(False)
        if generation != self._generation:
            self.context.setStatus('配置已变化，已拒绝迟到的检查结果；请重新检查')
            return
        try:
            reply = future.result()
            self.location.setText(f'Runtime 主机：{reply.runtime_host}\n最终路径：{reply.resolved_path}')
            self.location.setToolTip(self.location.text())
            self._loading = True
            selected = self.table.currentText()
            self.table.clear()
            self.table.addItems(list(reply.tables))
            self.table.setCurrentText(selected)
            self._loading = False
            self._schema = json.loads(reply.structure_json) or None
            if self._schema:
                text = '真实结构：' + '；'.join(c['name'] + ' ' + c['declaredType'] + (' NULL' if c['nullable'] else ' 必填') + (' / 数据库默认值' if c['defaultSql'] is not None else '') for c in self._schema['columns'])
                self.structure.setText(text[:900] + ('…完整结构见提示' if len(text) > 900 else ''))
                self.structure.setToolTip(text)
            else:
                self.structure.setText('数据库或目标表不存在；检查没有创建文件。请预览后明确创建。')
            if action == 'preview':
                self._preview = (self._generation, reply.resolved_path, reply.runtime_host, reply.preview_sql, deepcopy(self._plan()))
                self.createButton.setEnabled(True)
                dialog = QDialog(self.root)
                dialog.setWindowTitle('建表预览 · 尚未执行')
                dialog.resize(780, 420)
                layout = QVBoxLayout(dialog)
                label = QLabel(self.location.text() + '\n目标表：' + self.table.currentText() + '\n列、空值及默认值由下方 SQL 明确展示。')
                label.setWordWrap(True)
                layout.addWidget(label)
                text = QPlainTextEdit(reply.preview_sql)
                text.setReadOnly(True)
                layout.addWidget(text)
                buttons = QDialogButtonBox(QDialogButtonBox.Close)
                buttons.rejected.connect(dialog.reject)
                layout.addWidget(buttons)
                self._openDialog(dialog)
            self.context.setStatus('初始化完成；数据库外部操作不随项目撤销' if action == 'initialize' else '只读检查完成；未启动检测')
            self.error.clear()
        except Exception as error:
            if not self._alive():
                return
            reply = getattr(error, 'sqliteReply', None)
            if reply is not None:
                self.location.setText(f'Runtime 主机：{reply.runtime_host}\n最终路径：{reply.resolved_path or "路径无效"}')
            self.error.setText(str(error))
            self.context.setError(str(error))

    def _initialize(self):
        preview = self._preview
        if not self._alive() or preview is None:
            return
        message = (f'Runtime 主机：{preview[2]}\n文件：{preview[1]}\n目标表：{self.table.currentText()}\n\n'
                   + preview[3] + '\n\n此操作创建外部数据库/表，不能通过项目撤销删除；不迁移已有表。')
        dialog = InitializationDialog(self.root, message)
        def confirmed(result):
            if result == QDialog.Accepted:
                self._request('initialize', preview)
        self._openDialog(dialog, confirmed)

    def onOpen(self):
        try:
            draft = self.context.sqliteDraft()
            self.structure.setText('相对路径基准：' + (draft['directory'] or '尚未保存工程，请使用 Runtime 绝对路径') + '\n普通运行写实际目标；专门草稿调试使用隔离测试库。')
        except Exception as error:
            self.error.setText(str(error))

    def onClose(self):
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        if isValid(self.timer):
            self.timer.stop()
        for dialog in tuple(self._dialogs):
            if isValid(dialog):
                dialog.reject()
        if self._pending:
            _future, token, _generation, _action = self._pending
            token.cancel()
            # Work owns admission until it really exits. It captures no widgets.
            self._pending = None

    def dispose(self):
        self.onClose()
