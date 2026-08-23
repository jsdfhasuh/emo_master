from __future__ import annotations


try:
  from PySide2.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout

  class LogDialog(QDialog):
    def __init__(self) -> None:
      super().__init__()
      self.setModal(False)
      self.setWindowTitle("运行日志")
      self.resize(760, 420)

      rootLayout = QVBoxLayout()
      self._logViewer = QTextEdit()
      self._logViewer.setReadOnly(True)
      rootLayout.addWidget(self._logViewer)

      actionRow = QHBoxLayout()
      self._clearButton = QPushButton("清空")
      self._closeButton = QPushButton("关闭")
      actionRow.addWidget(self._clearButton)
      actionRow.addWidget(self._closeButton)
      rootLayout.addLayout(actionRow)

      self.setLayout(rootLayout)

      self._clearButton.clicked.connect(self._clearLogs)
      self._closeButton.clicked.connect(self.close)

    def appendLog(self, line: str) -> None:
      self._logViewer.append(line)

    def setLogs(self, lines: list[str]) -> None:
      self._logViewer.setPlainText("\n".join(lines))

    def _clearLogs(self) -> None:
      self._logViewer.setPlainText("")

except Exception:  # pragma: no cover
  class LogDialog:  # type: ignore[no-redef]
    def __init__(self) -> None:
      self._lines: list[str] = []
      self._visible = False

    def appendLog(self, line: str) -> None:
      self._lines.append(line)

    def setLogs(self, lines: list[str]) -> None:
      self._lines = list(lines)

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
