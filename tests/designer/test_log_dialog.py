from emo_master.apps.designer.ui.log_dialog import (
  LogDialog,
  RuntimeLogDock,
  StructuredLogEntry,
)
import pytest


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


def testStructuredLogEntryPreservesRuntimeContextAndPayload() -> None:
  entry = StructuredLogEntry.fromRuntimeEvent({
    "timestampMs": 1234,
    "level": "WARNING",
    "message": "camera retry",
    "eventType": "node.log",
    "jobId": "job",
    "workflowId": "main",
    "workflowRunId": "workflow-run",
    "nodeId": "camera",
    "nodeRunId": "node-run",
    "iterationPath": [2, 4],
    "code": "W_RETRY",
    "payload": {"operatorId": "vision.io.huaray_camera", "attempt": 2},
  })

  assert entry.level == "WARN"
  assert entry.source == "runtime"
  assert entry.iterationPath == (2, 4)
  assert entry.payload["attempt"] == 2
  assert entry.detailPayload()["nodeRunId"] == "node-run"


def testRuntimeLogDockClearOnlyClearsCurrentView() -> None:
  ensureQApp()
  dock = RuntimeLogDock()
  original = [
    StructuredLogEntry(1, "INFO", "first", source="designer"),
    StructuredLogEntry(2, "ERROR", "second", source="runtime"),
  ]
  dock.setEntries(original)
  dock.view.clearView()
  dock.appendEntry(StructuredLogEntry(3, "INFO", "new"))

  entries = getattr(dock.view, "_entries", [])
  assert [entry.message for entry in entries] == ["new"]
  assert [entry.message for entry in original] == ["first", "second"]


def testStructuredLogViewRestoresFiltersBeforeHistoricalEntriesArrive() -> None:
  ensureQApp()
  dock = RuntimeLogDock()
  if not hasattr(dock.view, "levelFilter"):
    pytest.skip("PySide2 is not available")
  dock.view.restoreSettings({
    "level": "ERROR",
    "source": "runtime",
    "job": "historical-job",
    "node": "camera",
    "event": "node.log",
    "search": "timeout",
    "autoScroll": False,
    "columnWidths": [80] * 10,
  })
  dock.appendEntry(StructuredLogEntry(
    1,
    "INFO",
    "unrelated",
    source="runtime",
    eventType="node.completed",
    jobId="other-job",
    nodeId="other-node",
  ))

  settings = dock.view.settings()
  assert settings["level"] == "ERROR"
  assert settings["source"] == "runtime"
  assert settings["job"] == "historical-job"
  assert settings["node"] == "camera"
  assert settings["event"] == "node.log"
  assert settings["search"] == "timeout"
  assert settings["autoScroll"] is False


def testStructuredLogViewBatchesAndBoundsEntries() -> None:
  ensureQApp()
  dock = RuntimeLogDock(maximumEntries=3)
  for index in range(5):
    dock.appendEntry(StructuredLogEntry(index + 1, "INFO", f"entry-{index}"))

  applyPending = getattr(dock.view, "_applyPendingEntries", None)
  if callable(applyPending):
    applyPending()

  assert [entry.message for entry in dock.view._entries] == [
    "entry-2",
    "entry-3",
    "entry-4",
  ]
  table = getattr(dock.view, "table", None)
  if table is not None:
    assert table.rowCount() == 3


def testClearViewResetsDynamicFiltersAndClearsOwnerCache() -> None:
  ensureQApp()
  clearCalls = []
  dock = RuntimeLogDock(onClear=lambda: clearCalls.append(True))
  dock.setEntries([
    StructuredLogEntry(
      1,
      "INFO",
      "old",
      source="runtime",
      eventType="node.log",
      jobId="old-job",
      nodeId="old-node",
    )
  ])
  jobFilter = getattr(dock.view, "jobFilter", None)
  if jobFilter is not None:
    jobFilter.setCurrentText("old-job")

  dock.view.clearView()
  dock.appendEntry(StructuredLogEntry(
    2,
    "INFO",
    "new",
    source="runtime",
    eventType="job.started",
    jobId="new-job",
  ))
  applyPending = getattr(dock.view, "_applyPendingEntries", None)
  if callable(applyPending):
    applyPending()

  assert clearCalls == [True]
  if jobFilter is not None:
    assert jobFilter.currentText() == "全部"
    assert dock.view.table.rowCount() == 1
