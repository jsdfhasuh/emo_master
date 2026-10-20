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
    from PySide2.QtCore import Qt, QTimer
    from PySide2.QtWidgets import (
        QDialog,
        QDialogButtonBox,
        QHeaderView,
        QAbstractItemView,
        QTableWidget,
        QTableWidgetItem,
        QHBoxLayout,
        QMessageBox,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
    from emo_master.apps.designer.operator_editors.builtin_layout import prepareBuiltinLayout
    from emo_master.apps.designer.ui.widgets import OptionalWrapLabel, scrollContent
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
            self._scrollablePlcLayout = customRoot is not None and context.operatorId in {
                'communication.plc.slmp_read', 'communication.plc.slmp_write'}
            self._controller = controller
            self._onClosed = onClosed
            self._loadedParams = dict(values)
            self._dirtyHint = False
            self._forceClosing = False
            self._disposed = False
            self._waitingForDebug = False
            self._debugCloseConnected = None
            self._disabledForDebugClose: list[tuple[QWidget, bool]] = []
            self._debugWindow = None
            self._openDebug = None
            self._schemaForm: SchemaParamForm | None = None
            self._bindingPanel = None
            self._bindingScroll = None
            self._loadedBindings = list(getattr(context, "variableBindings", []))
            self.setModal(False)
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self.setWindowTitle(title)
            self.resize(980 if customRoot is not None else 620, 720)

            layout = QVBoxLayout()
            self._statusLabel = OptionalWrapLabel("")
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

            if getattr(context, "variableDefinitions", None) is not None:
                from emo_master.apps.designer.operator_editors.variable_bindings import VariableBindingsPanel
                self._bindingPanel = VariableBindingsPanel(context)
                context.collectVariableBindings = self._bindingPanel.bindings
                bindingScroll = scrollContent(self._bindingPanel)
                self._bindingScroll = bindingScroll
                if self._scrollablePlcLayout:
                    # Keep parameter bindings accessible without taking half
                    # the height of the dedicated PLC runtime page.
                    tabs = cast(QTabWidget, cast(QWidget, customRoot).findChild(QTabWidget, "plcTabs"))
                    tabs.addTab(bindingScroll, "参数来源")
                else:
                    bindingScroll.setMaximumHeight(230)
                    layout.addWidget(bindingScroll)
                if self._schemaForm is not None and self._bindingPanel.installInline(self._schemaForm):
                    bindingScroll.hide()
            buttons = QHBoxLayout()
            self._debugButton = QPushButton("算子调试")
            self._debugButton.setIcon(icon("play"))
            self._debugButton.setToolTip("调试当前未应用的参数")
            if context.operatorId in {"communication.plc.slmp_read", "communication.plc.slmp_write"}:
                self._debugButton.setText("调试当前算子")
                action = "读取" if context.operatorId.endswith("slmp_read") else "写入"
                self._debugButton.setToolTip(f"按参数页配置执行当前{action}算子；不运行整个工作流。通信测试页的手动写入锁不控制此入口。")
            self._debugButton.hide()
            self._debugButton.clicked.connect(self.openDebug)
            buttons.addWidget(self._debugButton)
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

        def hasHeightForWidth(self) -> bool:
            # The native Windows geometry handler treats the *preferred* HFW
            # of nested PLC tables as a minimum, growing 430 px to 528 px after
            # a reply. Child layouts still wrap text; the scrollable PLC form
            # and table minimums, not its preferred height, constrain resizing.
            if getattr(self, '_scrollablePlcLayout', False):
                return False
            return super().hasHeightForWidth()

        def heightForWidth(self, width: int) -> int:
            if getattr(self, '_scrollablePlcLayout', False):
                return -1
            return super().heightForWidth(width)

        def bindDebug(self, callback) -> None:
            self._openDebug = callback
            self._debugButton.show()

        def openDebug(self) -> None:
            if self._openDebug is not None and not self._disposed:
                self._openDebug()

        def closeDebug(self) -> None:
            from shiboken2 import isValid
            if self._debugWindow is not None and isValid(self._debugWindow):
                self._debugWindow.detach()
                self._debugWindow.close()
            self._debugWindow = None

        def refreshSchema(self, schema) -> None:
            bindingsChanged = self._bindingPanel is not None and self._bindingPanel.needsRefresh(schema)
            schemaChanged = self.context.paramSchema != schema
            if not schemaChanged and not bindingsChanged:
                return
            self.context.paramSchema = schema
            if self._schemaForm is not None:
                values = self.collectParams()
                self._schemaForm.setSchema(schema, values)
            if self._bindingPanel is not None and self._bindingScroll is not None:
                self._bindingPanel.refresh()
                if self._schemaForm is not None:
                    self._bindingScroll.setVisible(not self._bindingPanel.installInline(self._schemaForm))

        def collectParams(self) -> dict[str, object]:
            if self._controller is not None:
                return dict(self._controller.collectParams())
            if self._schemaForm is not None:
                return dict(self._schemaForm.getValues())
            return {}

        def isDirty(self) -> bool:
            try:
                return (self._dirtyHint or self.collectParams() != self._loadedParams
                        or (self._bindingPanel is not None and self._bindingPanel.bindings() != self._loadedBindings))
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
                bindings = self._bindingPanel.bindings() if self._bindingPanel else []
                if self._schemaForm is not None:
                    validation = self._schemaForm.validationMessage()
                    if validation:
                        self.setError(validation)
                        return False
                if self._controller is not None and not bindings:
                    validation = _validationMessage(self._controller.validate())
                    if validation:
                        self.setError(validation)
                        return False
                collector = getattr(self._controller, "collectParamsForSave", None)
                params = collector() if callable(collector) else self.collectParams()
                if params is None:
                    return False
                applied = (self.context.applyConfiguration(self.key, params, bindings)
                           if getattr(self.context, "applyConfiguration", None) is not None else self.context.applyParams(params))
                if not applied:
                    self.setError("参数未应用，请查看 Designer 日志")
                    return False
            except Exception as err:
                self.setError(str(err))
                return False
            self._loadedParams = dict(params)
            saved = getattr(self._controller, "onParamsSaved", None)
            if callable(saved):
                saved(params)
            self._loadedBindings = bindings
            self.context.variableBindings = bindings
            self._dirtyHint = False
            self.setStatus("参数已应用")
            return True

        def simulateApply(self) -> None:
            self.applyChanges()

        def forceClose(self) -> bool:
            self._forceClosing = True
            try:
                return self.close()
            finally:
                if not self._waitingForDebug:
                    self._forceClosing = False

        def done(self, result: int) -> None:
            # Esc and QDialog completion must respect unapplied edits and
            # unregister/dispose this editor through the same close path.
            if not self._disposed:
                self.close()
                return
            super().done(result)

        def reject(self) -> None:
            self.done(QDialog.Rejected)

        def accept(self) -> None:
            self.done(QDialog.Accepted)

        def _restoreAfterDebugClose(self) -> None:
            from shiboken2 import isValid
            for widget, enabled in self._disabledForDebugClose:
                if isValid(widget):
                    widget.setEnabled(enabled)
            self._disabledForDebugClose.clear()

        def _debugCloseFailed(self) -> None:
            self._waitingForDebug = False
            self._forceClosing = False
            self._restoreAfterDebugClose()
            self.setError("调试资源释放失败，窗口已保留；请在调试窗口查看错误并明确重试关闭")

        def _finishDebugClose(self) -> None:
            from shiboken2 import isValid
            if isValid(self) and self._waitingForDebug:
                self._restoreAfterDebugClose()
                self.close()

        def _waitForDebugClose(self) -> bool:
            from shiboken2 import isValid
            debug = self._debugWindow
            if debug is None or not isValid(debug) or debug.disposed:
                return False
            if not self._waitingForDebug:
                self._waitingForDebug = True
                # Disable only editor controls, never the child debug window:
                # it must remain usable if cleanup fails and needs an explicit
                # user action. Preserve each original enabled state.
                def disable(layout):
                    for index in range(layout.count()):
                        item = layout.itemAt(index)
                        widget = item.widget()
                        if widget is not None and widget is not self._statusLabel:
                            self._disabledForDebugClose.append((widget, widget.isEnabled()))
                            widget.setEnabled(False)
                        elif item.layout() is not None:
                            disable(item.layout())
                disable(self.layout())
                self.setStatus("关闭中，等待调试资源实际释放…")
            if self._debugCloseConnected is not debug:
                self._debugCloseConnected = debug
                debug.cleanupFailed.connect(self._debugCloseFailed)
                debug.destroyed.connect(lambda: QTimer.singleShot(0, self._finishDebugClose))
            debug.close()
            return True

        def _confirmUnsavedParams(self):
            describe = getattr(self._controller, "describeUnsavedChanges", None)
            if not callable(describe):
                return QMessageBox.question(
                    self,
                    "未保存的参数",
                    "参数已经修改。是否应用后关闭？",
                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                    QMessageBox.Save,
                )
            rows = describe(self._loadedParams, self.collectParams())
            dialog = QDialog(self)
            dialog.setWindowTitle("未保存的参数")
            dialog.resize(760, 400)
            layout = QVBoxLayout(dialog)
            layout.addWidget(OptionalWrapLabel("节点参数尚未保存。相机运行参数的保留或恢复将在后续单独确认。"))
            table = QTableWidget(len(rows), 4)
            table.setHorizontalHeaderLabels(["参数", "修改前", "修改后", "值的来源"])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(str(value)))
            layout.addWidget(table)
            buttons = QDialogButtonBox()
            answer = [QMessageBox.Cancel]

            def choose(value):
                answer[0] = value
                dialog.accept()

            buttons.addButton("应用节点参数", QDialogButtonBox.AcceptRole).clicked.connect(lambda: choose(QMessageBox.Save))
            buttons.addButton("放弃节点更改", QDialogButtonBox.DestructiveRole).clicked.connect(lambda: choose(QMessageBox.Discard))
            back = buttons.addButton("继续编辑", QDialogButtonBox.RejectRole)
            back.clicked.connect(dialog.reject)
            back.setDefault(True)
            layout.addWidget(buttons)
            dialog.exec_()
            dialog.deleteLater()
            return answer[0]

        def closeEvent(self, event) -> None:  # type: ignore[override]
            if self._disposed:
                event.accept()
                return
            if not self._forceClosing and not self._waitingForDebug and self.isDirty():
                answer = self._confirmUnsavedParams()
                if answer == QMessageBox.Cancel:
                    event.ignore()
                    return
                if answer == QMessageBox.Save and not self.applyChanges():
                    event.ignore()
                    return
            requestClose = getattr(self._controller, "requestClose", None)
            if callable(requestClose):
                try:
                    if not requestClose():
                        event.ignore()
                        return
                except Exception as err:
                    self.setError(str(err))
                    event.ignore()
                    return
            if self._waitForDebugClose():
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
            self.closeDebug()
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
