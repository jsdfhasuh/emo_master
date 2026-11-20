"""Local appearance drafts using the native page and button renderers."""
from PySide2.QtCore import Qt
from PySide2.QtGui import QFontDatabase, QTextCursor
from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from emo_master.ui.presentation.renderer import RuntimePages


def disclosure(title, content, *, expanded=False):
    button = QToolButton()
    button.setText(title)
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setCheckable(True)
    button.setChecked(expanded)
    button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def show(opened):
        button.setArrowType(Qt.DownArrow if opened else Qt.RightArrow)
        content.setVisible(opened)

    button.toggled.connect(show)
    show(expanded)
    return button


class _CodeResizeHandle(QFrame):
    def __init__(self, code):
        super().__init__()
        self.code = code
        self.start = (0, 0)
        self.setFrameShape(QFrame.HLine)
        self.setFixedHeight(10)
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip('拖动调整代码区高度')

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start = event.globalY(), self.code.height()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            y, height = self.start
            self.code.setFixedHeight(max(116, min(520, height + event.globalY() - y)))


class CustomQssFields(QWidget):
    def __init__(self, value, *, expanded=False):
        super().__init__()
        self.saved = value or {}
        branch = self.saved.get('qtQss', {})
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        self.section = disclosure('自定义样式（QSS）', body, expanded=expanded or branch.get('enabled', False))
        layout.addWidget(self.section)
        layout.addWidget(body)
        fields = QVBoxLayout(body)
        fields.setContentsMargins(4, 4, 4, 4)
        self.enabled = QCheckBox('启用 QSS')
        self.enabled.setObjectName('advancedQssEnabled')
        self.enabled.setChecked(branch.get('enabled', False))
        fields.addWidget(self.enabled)
        hint = QLabel('选择当前控件可用的部位，再插入一条规则。')
        hint.setWordWrap(True)
        fields.addWidget(hint)
        actions = QHBoxLayout()
        self.selectors = QComboBox()
        self.selectors.setObjectName('advancedQssSelectors')
        self.selectors.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.selectors.setMinimumContentsLength(18)
        actions.addWidget(self.selectors, 1)
        insert = QPushButton('插入规则')
        insert.clicked.connect(self.insertRule)
        actions.addWidget(insert)
        self.clearButton = QPushButton('清空 QSS')
        self.clearButton.setObjectName('advancedQssClear')
        actions.addWidget(self.clearButton)
        fields.addLayout(actions)
        self.code = QPlainTextEdit(branch.get('code', ''))
        self.code.setObjectName('advancedQssCode')
        self.code.setPlaceholderText('例如 *[emoPart="root"] { border-color: #2459D1; }')
        self.code.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.code.setMinimumHeight(116)
        fields.addWidget(self.code, 1)
        fields.addWidget(_CodeResizeHandle(self.code))
        self.clearButton.clicked.connect(self.code.clear)
        self.feedback = QLabel()
        self.feedback.setObjectName('advancedQssFeedback')
        self.feedback.setWordWrap(True)
        fields.addWidget(self.feedback)

    def setSelectors(self, selectors):
        if selectors == [self.selectors.itemText(i) for i in range(self.selectors.count())]:
            return
        active = self.selectors.currentText()
        self.selectors.blockSignals(True)
        self.selectors.clear()
        self.selectors.addItems(selectors)
        self.selectors.setCurrentIndex(max(0, self.selectors.findText(active)))
        self.selectors.blockSignals(False)

    def insertRule(self):
        selector = self.selectors.currentText()
        if selector:
            cursor = self.code.textCursor()
            cursor.movePosition(QTextCursor.End)
            prefix = '\n\n' if self.code.toPlainText() else ''
            cursor.insertText(prefix + selector + ' {\n    \n}')
            cursor.movePosition(QTextCursor.PreviousBlock)
            cursor.movePosition(QTextCursor.EndOfBlock)
            self.code.setTextCursor(cursor)
            self.code.setFocus()

    def result(self, *, preview=False):
        from emo_master.ui.presentation.native_style import validateQss

        branch = {'enabled': self.enabled.isChecked(), 'code': self.code.toPlainText()}
        error = validateQss(branch['code']) if branch['enabled'] and branch['code'].strip() else ''
        if error:
            self.feedback.setText('当前代码未应用。' + error)
            if not preview:
                raise ValueError('请修正 QSS，或关闭“启用 QSS”后保存。' + error)
            branch['enabled'] = False
        else:
            self.feedback.setText('当前使用通用外观。' if not branch['enabled'] or not branch['code'].strip() else '')
        if 'qtQss' in self.saved or branch['enabled'] or branch['code'] or error:
            return {**self.saved, 'qtQss': branch}
        return self.saved or None


class ComponentAppearancePreview(RuntimePages):
    allowsDesignExamples = True

    def __init__(self, presentation, pageId, component, *, assets, projectRoot, view=None):
        config = presentation.model_copy(update={'defaultPageId': pageId})
        super().__init__(config, appearancePreview=component, target=None, assets=assets, projectRoot=projectRoot)
        for widget in (self.header, self.hostMessage, self.banner, self.pageNavigation, self.modeLabel,
                       self.resumeButton, self.diagnosticToggle, self.diagnostics):
            widget.hide()
        self.setMinimumSize(0, 0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        if view and view.scopes:
            self.submit(view)
        else:
            self.setDesignExamples(True)
        from emo_master.ui.presentation.sqlite_widgets import SqliteTableView, SqliteValueView
        for _, widget in self.widgets[self.currentPageId].values():
            if isinstance(widget, SqliteTableView):
                widget.model.replace([('示例 1',) * len(widget.model.columns),
                                      ('示例 2',) * len(widget.model.columns)])
                widget.table.selectRow(0)
                widget.message.setText('示例数据')
            elif isinstance(widget, SqliteValueView):
                widget._accept(10.0)
                widget.message.setText('示例数据')
        self.banner.hide()
        self.setObjectName('componentAppearancePreview')
        if component.type in {'container', 'image', 'table', 'sqlite_table'}:
            self.setMaximumHeight(350)
        else:
            self.setMaximumHeight(max(108, self.componentCard.sizeHint().height() + 24))

    @property
    def componentCard(self):
        return self.cards[self.currentPageId][self.appearancePreview.componentId]

    def fitToAvailableScreen(self, *, resize=True, screen=None):
        # The dialog layout owns the size of this embedded surface.
        self.setMinimumSize(0, 0)
