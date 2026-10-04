"""Read-only shortcuts and explicit provenance for the existing result image."""
from pathlib import Path

from PySide2.QtCore import QUrl
from PySide2.QtGui import QDesktopServices
from PySide2.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from .widgets import WrapLabel


class RunResultTools(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        self.path: str | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.origin = WrapLabel('运行后显示本次保存的结果图')
        root.addWidget(self.origin)
        actions = QHBoxLayout()
        self.logs = QToolButton()
        self.logs.setText('运行日志')
        self.logs.clicked.connect(window.openLogDialog)
        actions.addWidget(self.logs)
        self.openFile = QToolButton()
        self.openFile.setText('打开结果图')
        self.openFile.setEnabled(False)
        self.openFile.clicked.connect(self._openFile)
        actions.addWidget(self.openFile)
        actions.addStretch(1)
        root.addLayout(actions)

    def refresh(self):
        state = self.owner.runtimePanelState
        self.path = state.latestImagePath
        job = self.owner.currentJobId
        node = state.latestArtifact.get('nodeId', '')
        self.origin.setText(f'本次保存图 · 任务 {job[:8]} · 来源节点 {node or "未提供"}' if job and self.path
                            else '本次尚无保存的结果图')
        self.openFile.setEnabled(bool(job and self.path))
        self.openFile.setToolTip(self.path or '由图像保存节点产生结果文件后可打开')
        self._updateMinimumHeight()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._updateMinimumHeight()

    def _updateMinimumHeight(self):
        layout = self.layout()
        if layout is None:
            return
        # QWidget's minimumSizeHint doesn't include its wrapped child's height
        # at the current width. Expose it so a short parent scrolls instead of
        # putting this footer over the image's minimum-height rectangle.
        height = max(layout.minimumSize().height(), layout.heightForWidth(self.width()))
        if height != self.minimumHeight():
            self.setMinimumHeight(height)

    def _openFile(self):
        path = self.path
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
