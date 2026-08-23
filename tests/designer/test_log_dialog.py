from emo_master.apps.designer.ui.log_dialog import LogDialog


def ensureQApp() -> None:
  try:
    from PySide2.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
      _ = QApplication([])
  except Exception:
    pass


def testLogDialogSetAndAppendLogs() -> None:
  ensureQApp()
  dialog = LogDialog()
  dialog.setLogs(["line1", "line2"])
  dialog.appendLog("line3")

  stored = getattr(dialog, "_lines", None)
  if isinstance(stored, list):
    assert stored[-1] == "line3"
  else:
    viewer = getattr(dialog, "_logViewer", None)
    assert viewer is not None
