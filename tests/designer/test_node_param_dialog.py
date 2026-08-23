from emo_master.apps.designer.ui.node_param_dialog import NodeParamDialog


def ensureQApp() -> None:
  try:
    from PySide2.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
      _ = QApplication([])
  except Exception:
    pass


def testNodeParamDialogApplyCallbackReceivesNodeId() -> None:
  ensureQApp()
  dialog = NodeParamDialog()
  captured: dict[str, object] = {}

  def applyHandler(nodeId: str, values: dict[str, object]) -> None:
    captured["nodeId"] = nodeId
    captured["values"] = values

  dialog.setApplyHandler(applyHandler)
  dialog.setNodeContext(
    nodeId="node-1",
    operatorId="vision.edge.canny",
    schema={"type": "object", "properties": {"thresholdLow": {"type": "integer", "default": 50}}},
    values={}
  )

  simulateApply = getattr(dialog, "simulateApply", None)
  if callable(simulateApply):
    simulateApply()
  else:
    trigger = getattr(dialog, "_onApplyClicked", None)
    if callable(trigger):
      trigger()

  assert captured["nodeId"] == "node-1"
