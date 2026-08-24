from __future__ import annotations

from typing import Callable

from emo_master.apps.designer.ui.param_form import SchemaParamForm


ApplyHandler = Callable[[str, dict[str, object]], None]


try:
  from PySide2.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

  class NodeParamDialog(QDialog):
    def __init__(self) -> None:
      super().__init__()
      self.setModal(False)
      self.setWindowTitle("节点参数")
      self._currentNodeId: str | None = None
      self._applyHandler: ApplyHandler | None = None

      rootLayout = QVBoxLayout()
      self._metaLabel = QLabel("")
      rootLayout.addWidget(self._metaLabel)

      self._paramForm = SchemaParamForm()
      rootLayout.addWidget(self._paramForm)

      buttonRow = QHBoxLayout()
      self._applyButton = QPushButton("应用")
      self._closeButton = QPushButton("关闭")
      buttonRow.addWidget(self._applyButton)
      buttonRow.addWidget(self._closeButton)
      rootLayout.addLayout(buttonRow)

      container = QWidget()
      container.setLayout(rootLayout)
      hostLayout = QVBoxLayout()
      hostLayout.addWidget(container)
      self.setLayout(hostLayout)

      self._applyButton.clicked.connect(self._onApplyClicked)
      self._closeButton.clicked.connect(self.close)

    def setApplyHandler(self, handler: ApplyHandler | None) -> None:
      self._applyHandler = handler

    def setNodeContext(
      self,
      nodeId: str,
      operatorId: str,
      schema: dict[str, object],
      values: dict[str, object]
    ) -> None:
      self._currentNodeId = nodeId
      self._metaLabel.setText(f"节点：{nodeId} | 算子：{operatorId}")
      self._paramForm.setSchema(schema, values)

    def setWorkflowOptions(self, options: list[str]) -> None:
      setOptions = getattr(self._paramForm, "setWorkflowOptions", None)
      if callable(setOptions):
        setOptions(options)

    def _onApplyClicked(self) -> None:
      if self._currentNodeId is None:
        return
      if self._applyHandler is None:
        return
      self._applyHandler(self._currentNodeId, self._paramForm.getValues())

except Exception:  # pragma: no cover
  class NodeParamDialog:  # type: ignore[no-redef]
    def __init__(self) -> None:
      self._currentNodeId: str | None = None
      self._operatorId = ""
      self._schema: dict[str, object] = {}
      self._values: dict[str, object] = {}
      self._applyHandler: ApplyHandler | None = None
      self._visible = False

    def setApplyHandler(self, handler: ApplyHandler | None) -> None:
      self._applyHandler = handler

    def setNodeContext(
      self,
      nodeId: str,
      operatorId: str,
      schema: dict[str, object],
      values: dict[str, object]
    ) -> None:
      self._currentNodeId = nodeId
      self._operatorId = operatorId
      self._schema = dict(schema)
      self._values = dict(values)

    def setWorkflowOptions(self, options: list[str]) -> None:
      _ = options

    def show(self) -> None:
      self._visible = True

    def raise_(self) -> None:
      self._visible = True

    def activateWindow(self) -> None:
      self._visible = True

    def close(self) -> None:
      self._visible = False

    def isVisible(self) -> bool:
      return self._visible

    def simulateApply(self) -> None:
      if self._applyHandler is None or self._currentNodeId is None:
        return
      self._applyHandler(self._currentNodeId, dict(self._values))
