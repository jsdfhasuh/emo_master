"""Native read-only result panel; no Runtime or execution ownership."""
from datetime import datetime

from PySide2.QtCore import QEvent, Qt, Signal
from PySide2.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QToolButton,
                              QTabWidget, QComboBox, QTextEdit, QLabel)

from emo_master.apps.designer.presenters.node_run_presenter import describePort, inspectionText
from emo_master.core.contracts.port_types import normalizePortType
from .widgets import WrapLabel, ElidedLabel, scrollContent
from .node_image_surface import NodeImageSurface


def portText(batch, definitions=None):
    if batch is None:
        return '本次执行未提供端口值'
    rows = [item['port'] + ' = ' + describePort(item['value']) +
            '  [' + str((definitions or {}).get(item['port'], item['value'].get('kind', '未知'))) + ']'
            for item in batch['items']]
    if not rows:
        rows.append('无端口值')
    if batch.get('omitted'):
        rows.append(f"另有 {batch['omitted']} 个端口因摘要额度未展开")
    return '\n'.join(rows)


class NodeResultPanel(QWidget):
    selectionChanged = Signal()
    portChanged = Signal()
    enlargeRequested = Signal()
    configureRequested = Signal()

    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self._nodeIdentity = None
        self._defaulted = None
        self.setObjectName('nodeResultPanel')
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 10)
        top = QHBoxLayout()
        top.addWidget(QLabel('节点结果'), 1)
        self.live = QToolButton()
        self.live.setText('本次运行')
        self.previous = QToolButton()
        self.previous.setText('上一次运行')
        for button, older in ((self.live, False), (self.previous, True)):
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, previous=older: self._selectRun(previous))
            top.addWidget(button)
        root.addLayout(top)
        self.title = ElidedLabel('未选中节点')
        self.title.setTextFormat(Qt.PlainText)
        self.origin = WrapLabel('明确运行后查看真实结果')
        self.origin.setTextFormat(Qt.PlainText)
        root.addWidget(self.title)
        self.operator = WrapLabel('')
        self.operator.setTextFormat(Qt.PlainText)
        root.addWidget(self.operator)
        root.addWidget(self.origin)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        # Generic Designer tool-button padding leaves the native 16px tab
        # arrows no drawable area on a narrow sidebar. Scope this exception.
        self.tabs.tabBar().setStyleSheet('QTabBar::scroller { width: 32px; } '
            'QToolButton { padding: 0; min-width: 16px; max-width: 16px; border: 0; background: #ffffff; } '
            'QToolButton::left-arrow, QToolButton::right-arrow { width: 12px; height: 12px; }')
        root.addWidget(self.tabs, 1)
        self.imageContent = QWidget()
        self.imagePage = scrollContent(self.imageContent, name='nodeImageScroll')
        self.imagePage.setMinimumHeight(self.imagePage.minimumSizeHint().height())
        imageLayout = QVBoxLayout(self.imageContent)
        imageLayout.setContentsMargins(0, 6, 0, 0)
        self.ports = QComboBox()
        self.ports.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.ports.setMinimumContentsLength(5)
        self.ports.currentIndexChanged.connect(lambda _index: self.portChanged.emit())
        imageLayout.addWidget(self.ports)
        self.image = NodeImageSurface('本次尚无节点图片')
        self.image.activated.connect(self.enlargeRequested)
        self.image.setMinimumHeight(150)
        imageLayout.addWidget(self.image, 1)
        self.imageMeta = WrapLabel('')
        self.imageMeta.setTextFormat(Qt.PlainText)
        imageLayout.addWidget(self.imageMeta)
        self.enlarge = QToolButton()
        self.enlarge.setText('放大查看')
        self.enlarge.setEnabled(False)
        self.enlarge.clicked.connect(self.enlargeRequested)
        imageLayout.addWidget(self.enlarge)
        self.tabs.addTab(self.imagePage, '图像输出')
        self.values = self._textPage('数值输出')
        self.execution = self._textPage('执行信息')
        self.inputs = self._textPage('输入数据')
        self.notice = WrapLabel('')
        self.notice.setTextFormat(Qt.PlainText)
        root.addWidget(self.notice)
        actions = QHBoxLayout()
        self.configure = QToolButton()
        self.configure.setText('当前草稿配置…')
        self.configure.setToolTip('编辑当前草稿，不会改变所查看任务的结果')
        self.configure.clicked.connect(self.configureRequested)
        self.logs = QToolButton()
        self.logs.setText('运行日志')
        actions.addWidget(self.configure)
        actions.addWidget(self.logs)
        actions.addStretch(1)
        root.addLayout(actions)
        for page in (self.imagePage, self.values, self.execution, self.inputs):
            page.ensurePolished()
            page.setMinimumHeight(page.minimumSizeHint().height())
        self.tabs.ensurePolished()
        self.tabs.setMinimumHeight(self.tabs.minimumSizeHint().height())

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest,
                            QEvent.FontChange, QEvent.StyleChange) and self.layout() is not None:
            # QSplitter uses minimumSizeHint, ignoring nested height-for-width.
            # Wrapped origin/identity lines must reserve their actual height;
            # the enclosing scroll surface can then expose the bottom actions.
            margins = self.layout().contentsMargins()
            width = max(1, self.width() - margins.left() - margins.right())
            for name in ('operator', 'origin', 'notice'):
                label = getattr(self, name, None)
                if label is not None and not label.isHidden():
                    required = label.heightForWidth(width)
                    if required >= 0 and required != label.minimumHeight():
                        label.setMinimumHeight(required)
            minimum = self.layout().minimumHeightForWidth(max(1, self.width()))
            if minimum >= 0 and minimum != self.minimumHeight():
                self.setMinimumHeight(minimum)
        return result

    def _textPage(self, title):
        text = QTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QTextEdit.WidgetWidth)
        self.tabs.addTab(text, title)
        return text

    def _selectRun(self, previous):
        self.history.selectPrevious(previous)
        self.selectionChanged.emit()

    def render(self, workflowId, nodeId, draft=None):
        self._nodeIdentity = (workflowId, nodeId)
        view = self.history.view(workflowId, nodeId)
        run, record, definition = view['job'], view['record'], view['definition']
        # Draft port declarations are useful before Run, never after acceptance.
        definition = definition or (draft if run is None else None) or {}
        self.previous.setEnabled(self.history.previous is not None)
        self.live.setChecked(not view['history'])
        self.previous.setChecked(view['history'])
        self.title.setText((definition.get('displayName') or definition.get('operatorId') or nodeId)
                           if nodeId else '未选中节点')
        self.title.setToolTip(self.title.text() +
            ('\n名称已因摘要额度截断' if definition.get('displayNameTruncated') else '') +
            '\n' + str(definition.get('operatorId', '')) + '\n' + str(nodeId))
        self.operator.setText('算子：' + definition.get('operatorId', '未提供'))
        status = {'RUNNING': '运行中', 'COMPLETED': '执行完成', 'FAILED': '执行失败', 'SKIPPED': '已跳过'}
        nodeStatus = status.get(record['status'], record['status']) if record else '尚无执行记录'
        if run:
            stamp = datetime.fromtimestamp(run.acceptedAtMs / 1000).strftime('%Y-%m-%d %H:%M:%S')
            self.origin.setText(f"{'只读（历史）' if view['history'] else '本次运行'} · {nodeStatus}\n"
                                f'任务 {run.jobId[:8]} · 修订 {run.revision} · {stamp}')
            self.origin.setToolTip('任务：' + run.jobId)
        else:
            self.origin.setText('尚未运行 · 明确启动后查看真实结果')
        unavailable = '所选任务没有此节点' if run and not definition else '本次尚未收到此节点的执行记录'
        self.values.setPlainText(portText(record['io']['outputs'], definition.get('outputPorts')) if record else unavailable)
        self.inputs.setPlainText(portText(record['io']['inputs'], definition.get('inputPorts')) if record else unavailable)
        execution = inspectionText(record, run.jobId if run else None, bool(nodeId))
        if view['history']:
            execution = execution.replace('本次运行', '上一次运行（只读）')
        if record:
            execution += '\n流程执行：' + record['workflowRunId'] + '\n节点执行完整标识：' + record['nodeRunId']
        self.execution.setPlainText(execution)
        self.configure.setEnabled(bool(nodeId))
        self.notice.setText(self.history.notice or (run.notice if run else ''))
        self.notice.setVisible(bool(self.notice.text()))
        ports = [port for port, kind in definition.get('outputPorts', {}).items()
                 if normalizePortType(kind) == 'image']
        previousPort = self.ports.currentData()
        wanted = [(port, port) for port in ports]
        saved = bool(run and run.artifact and run.artifact.get('nodeId') == nodeId
                     and run.artifact.get('workflowId') == workflowId)
        if saved:
            wanted.append(('保存结果图', '__saved_result__'))
        before = [(self.ports.itemText(i), self.ports.itemData(i)) for i in range(self.ports.count())]
        if before != wanted:
            self.ports.blockSignals(True)
            self.ports.clear()
            for title, key in wanted:
                self.ports.addItem(title, key)
            match = self.ports.findData(previousPort)
            self.ports.setCurrentIndex(max(0, match))
            self.ports.blockSignals(False)
        self.tabs.setTabVisible(0, bool(ports) or saved)
        defaultKey = (run.jobId if run else '', workflowId, nodeId)
        if defaultKey != self._defaulted:
            self.tabs.setCurrentIndex(0 if ports else 1 if definition.get('outputPorts') else 2)
            self._defaulted = defaultKey
        return view

    def clearImage(self, message):
        self.image.setText(message)
        self.imageMeta.setText('')
        self.enlarge.setEnabled(False)
