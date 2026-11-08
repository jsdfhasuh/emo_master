"""A collapsible view of the editor's live draft, without a second parameter store."""
from copy import deepcopy

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QHBoxLayout, QScrollArea, QToolButton, QVBoxLayout, QWidget
from shiboken2 import isValid

from .debug_values import ValueTree, tool
from .param_form import parameterTitle
from .widgets import OptionalWrapLabel, WrapLabel


class DebugParameterSection(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.form = getattr(editor, "_schemaForm", None)
        self.sourceScroll = None
        self.placeholder = None
        self.snapshot = None
        self.lastValues = None
        self.lastBindings = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.toggle = QToolButton(self)
        self.toggle.setToolTip("展开或收起算子参数")
        self.toggle.setAccessibleName("算子参数")
        self.toggle.setArrowType(Qt.DownArrow)
        self.toggle.setText("算子参数")
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        header.addWidget(self.toggle)
        header.addStretch()
        layout.addLayout(header)
        self.body = QWidget(self)
        bodyLayout = QVBoxLayout(self.body)
        bodyLayout.setContentsMargins(0, 0, 0, 0)
        self.error = OptionalWrapLabel("")
        bodyLayout.addWidget(self.error)
        if self.form is not None:
            parentWidget = self.form.parentWidget()
            while parentWidget is not None and not isinstance(parentWidget, QScrollArea):
                parentWidget = parentWidget.parentWidget()
            if parentWidget is not None and parentWidget.widget() is self.form:
                self.sourceScroll = parentWidget
                self.sourceScroll.takeWidget()
                self.placeholder = WrapLabel("参数正在算子调试窗口中编辑")
                self.sourceScroll.setWidget(self.placeholder)
                bodyLayout.addWidget(self.form)
        if self.sourceScroll is None:
            self.form = None
            self.snapshot = ValueTree(self.body)
            self.snapshot.setMinimumHeight(180)
            bodyLayout.addWidget(self.snapshot)
            header.addWidget(tool(self, "sliders-horizontal", "在专用编辑器中编辑参数", self.edit))
            self.refresh()
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self.setExpanded)

    def setExpanded(self, expanded):
        self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        if expanded:
            self.refresh()

    def edit(self):
        self.editor.show()
        self.editor.raise_()
        self.editor.activateWindow()

    def refresh(self):
        if self.snapshot is None:
            return
        try:
            values = self.editor.collectParams()
            bindings = self.editor.context.collectVariableBindings()
            if values != self.lastValues or bindings != self.lastBindings:
                properties = self.editor.context.paramSchema.get("properties", {})
                labeled = {}
                for name, value in values.items():
                    title = parameterTitle(name, properties.get(name, {}))
                    labeled[f"{title} ({name})" if title != name else name] = value
                if bindings:
                    labeled["参数来源"] = bindings
                self.snapshot.setValue(labeled)
                self.lastValues = deepcopy(values)
                self.lastBindings = deepcopy(bindings)
            self.error.setText("")
        except Exception as error:
            self.snapshot.clear()
            self.lastValues = self.lastBindings = None
            self.error.setText(str(error))

    def restore(self):
        # Return the original QObject before the debug dialog owns its deletion.
        if self.sourceScroll is None:
            return
        source, self.sourceScroll = self.sourceScroll, None
        if isValid(source) and self.form is not None and isValid(self.form):
            self.body.layout().removeWidget(self.form)
            placeholder = source.takeWidget()
            source.setWidget(self.form)
            self.form.show()
            if placeholder is not None:
                placeholder.deleteLater()
