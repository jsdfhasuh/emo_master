from __future__ import annotations

from collections.abc import Callable
from typing import cast

from emo_master.apps.designer.operator_editors.controller_protocol import (
    EditorContext,
    EditorKey,
    OperatorEditorController,
)
from emo_master.apps.designer.ui.param_form import SchemaParamForm


def _validationMessage(value: object) -> str:
    if value is None or value is True or value == [] or value == {}:
        return ""
    if value is False:
        return "参数校验失败"
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("message", value))
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    return str(value)


try:
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
    from emo_master.apps.designer.operator_editors.builtin_layout import prepareBuiltinLayout
    from emo_master.apps.designer.ui.widgets import WrapLabel, scrollContent
    from emo_master.apps.designer.ui.icon_map import icon

    class OperatorWorkspaceWindow(QDialog):
        def __init__(
            self,
            *,
            key: EditorKey,
            title: str,
            context: EditorContext,
            schema: dict[str, object],
            values: dict[str, object],
            customRoot: object | None = None,
            controller: OperatorEditorController | None = None,
            fallbackReason: str = "",
            parent: object | None = None,
            onClosed: Callable[[EditorKey, object], None] | None = None,
        ) -> None:
            super().__init__(cast(QWidget | None, parent))
            self.key = key
            self.context = context
            self._controller = controller
            self._onClosed = onClosed
            self._loadedParams = dict(values)
            self._dirtyHint = False
            self._forceClosing = False
            self._disposed = False
            self._schemaForm: SchemaParamForm | None = None
            self.setModal(False)
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self.setWindowTitle(title)
            self.resize(980 if customRoot is not None else 620, 720)

            layout = QVBoxLayout()
            self._statusLabel = WrapLabel("")
            self._statusLabel.setObjectName("operatorEditorStatus")
            layout.addWidget(self._statusLabel)
            if fallbackReason:
                self._statusLabel.setText(f"专用编辑器不可用，已回退通用表单：{fallbackReason}")
                self._statusLabel.setStyleSheet("color: #d18b32;")

            if customRoot is not None and controller is not None:
                prepareBuiltinLayout(cast(QWidget, customRoot), context.operatorId)
                layout.addWidget(cast(QWidget, customRoot))
                layout.setStretch(layout.count() - 1, 1)
                controller.bind(customRoot, context)
                controller.loadParams(dict(values))
            else:
                form = SchemaParamForm()
                form.setWorkflowOptions(context.workflowOptions)
                form.setSchema(schema, values)
                self._schemaForm = form
                layout.addWidget(scrollContent(form), 1)

            buttons = QHBoxLayout()
            buttons.addStretch(1)
            self._applyButton = QPushButton("应用")
            self._applyButton.setObjectName("primaryButton")
            self._applyButton.setIcon(icon("save", "#ffffff"))
            self._closeButton = QPushButton("关闭")
            buttons.addWidget(self._applyButton)
            buttons.addWidget(self._closeButton)
            layout.addLayout(buttons)
            self.setLayout(layout)
            self._applyButton.clicked.connect(self.applyChanges)
            self._closeButton.clicked.connect(self.close)
            context.bindWindowHooks(self.markDirty, self.setStatus, self.setError)
            try:
                self._loadedParams = self.collectParams()
            except Exception:
                self._loadedParams = dict(values)

        def openController(self) -> None:
            if self._controller is not None:
                self._controller.onOpen()

        def collectParams(self) -> dict[str, object]:
            if self._controller is not None:
                return dict(self._controller.collectParams())
            if self._schemaForm is not None:
                return dict(self._schemaForm.getValues())
            return {}

        def isDirty(self) -> bool:
            try:
                return self._dirtyHint or self.collectParams() != self._loadedParams
            except Exception:
                return True

        def markDirty(self) -> None:
            self._dirtyHint = True

        def setStatus(self, message: str) -> None:
            self._statusLabel.setStyleSheet("")
            self._statusLabel.setText(str(message))

        def setError(self, message: str) -> None:
            self._statusLabel.setStyleSheet("color: #d45b5b;")
            self._statusLabel.setText(str(message))

        def applyChanges(self) -> bool:
            try:
                if self._controller is not None:
                    validation = _validationMessage(self._controller.validate())
                    if validation:
                        self.setError(validation)
                        return False
                params = self.collectParams()
                if not self.context.applyParams(params):
                    self.setError("参数未应用，请查看 Designer 日志")
                    return False
            except Exception as err:
                self.setError(str(err))
                return False
            self._loadedParams = dict(params)
            self._dirtyHint = False
            self.setStatus("参数已应用")
            return True

        def simulateApply(self) -> None:
            self.applyChanges()

        def forceClose(self) -> None:
            self._forceClosing = True
            self.close()

        def closeEvent(self, event) -> None:  # type: ignore[override]
            if not self._forceClosing and self.isDirty():
                answer = QMessageBox.question(
                    self,
                    "未保存的参数",
                    "参数已经修改。是否应用后关闭？",
                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                    QMessageBox.Save,
                )
                if answer == QMessageBox.Cancel:
                    event.ignore()
                    return
                if answer == QMessageBox.Save and not self.applyChanges():
                    event.ignore()
                    return
            self._dispose()
            event.accept()
            try:
                super().closeEvent(event)
            except Exception:
                pass

        def _dispose(self) -> None:
            if self._disposed:
                return
            self._disposed = True
            try:
                if self._controller is not None:
                    self._controller.onClose()
            finally:
                try:
                    if self._controller is not None:
                        self._controller.dispose()
                finally:
                    if self._onClosed is not None:
                        self._onClosed(self.key, self)

except Exception:  # pragma: no cover

    class OperatorWorkspaceWindow:  # type: ignore[no-redef]
        def __init__(
            self,
            *,
            key: EditorKey,
            title: str,
            context: EditorContext,
            schema: dict[str, object],
            values: dict[str, object],
            customRoot: object | None = None,
            controller: OperatorEditorController | None = None,
            fallbackReason: str = "",
            parent: object | None = None,
            onClosed: Callable[[EditorKey, object], None] | None = None,
        ) -> None:
            _ = title, customRoot, controller, fallbackReason, parent
            self.key = key
            self.context = context
            self._form = SchemaParamForm()
            self._form.setWorkflowOptions(context.workflowOptions)
            self._form.setSchema(schema, values)
            self._loadedParams = self._form.getValues()
            self._visible = False
            self._onClosed = onClosed

        def openController(self) -> None:
            return

        def show(self) -> None:
            self._visible = True

        def raise_(self) -> None:
            self._visible = True

        def activateWindow(self) -> None:
            self._visible = True

        def isVisible(self) -> bool:
            return self._visible

        def isDirty(self) -> bool:
            return self._form.getValues() != self._loadedParams

        def applyChanges(self) -> bool:
            params = self._form.getValues()
            applied = self.context.applyParams(params)
            if applied:
                self._loadedParams = dict(params)
            return applied

        def simulateApply(self) -> None:
            self.applyChanges()

        def close(self) -> None:
            self.forceClose()

        def forceClose(self) -> None:
            if not self._visible and self._onClosed is None:
                return
            self._visible = False
            callback = self._onClosed
            self._onClosed = None
            if callback is not None:
                callback(self.key, self)
