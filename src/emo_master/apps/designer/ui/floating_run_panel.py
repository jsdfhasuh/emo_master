"""One floating surface for the existing run summary and node-result owners."""
from PySide2.QtCore import QSize
from PySide2.QtWidgets import QMenu, QToolButton

from emo_master.ui.workflow_labels import WORKFLOW_RUN_STYLES, aggregateWorkflowStatus
from .canvas_overlay import FloatingPanel
from .icon_map import icon
from .widgets import ElidedLabel


class FloatingRunPanel(FloatingPanel):
    def __init__(self, window):
        self.owner = window
        super().__init__(window.flowView, window.flowView.viewport(), window.settingsStore,
                         'ui/run_panel', '运行面板', window.rightPanelContainer,
                         right=True, symbol='chart-no-axes-column', expandedSize=QSize(400, 660),
                         collapsedSize=QSize(328, 46))
        self.status = ElidedLabel()
        self.status.setFixedWidth(76)
        self.header.insertWidget(2, self.status)
        self.startButton = QToolButton()
        self.startButton.setDefaultAction(window._toolbarActions['开始运行'])
        self.stopButton = QToolButton()
        self.stopButton.setDefaultAction(window._toolbarActions['停止运行'])
        self.stopMenuButton = QToolButton()
        self.stopMenu = QMenu(self.stopMenuButton)
        self.stopMenu.addAction(window._menuActions['停止全部'])
        self.stopMenuButton.setIcon(icon('ellipsis'))
        self.stopMenuButton.setToolTip('更多运行操作')
        self.stopMenuButton.setAccessibleName('更多运行操作')
        self.stopMenuButton.setMenu(self.stopMenu)
        self.stopMenuButton.setPopupMode(QToolButton.InstantPopup)
        for index, button in enumerate((self.startButton, self.stopButton, self.stopMenuButton), 3):
            button.setIconSize(QSize(16, 16))
            button.setFixedSize(30, 30)
            button.setStyleSheet('QToolButton { padding: 0; } '
                                'QToolButton::menu-indicator { image: none; width: 0; height: 0; }')
            self.header.insertWidget(index, button)
        self.refreshStatus()

    def refreshStatus(self):
        window = self.owner
        runtime = window.runtimeController
        statuses = [run.state.jobStatus for run in runtime.runs.values() if runtime.busy(run)]
        count = len(statuses)
        state = window.runtimePanelState.jobStatus
        fallback = {'IDLE': '空闲', 'READY': '就绪', 'UNKNOWN': '未知'}.get(state, state)
        text, color, _ = WORKFLOW_RUN_STYLES.get(state, (fallback, '#626b78', ''))
        if count:
            active = aggregateWorkflowStatus(statuses)
            text, color, _ = WORKFLOW_RUN_STYLES.get(active, ('运行中', '#b45309', ''))
            text = f'{"运行中" if active == "RUNNING" else text} {count}'
        self.status.setText(text)
        self.status.setStyleSheet(f'color: {color}; background: transparent; font-weight: 600;')
        self.status.setToolTip(f'活动任务：{count}\n当前所选：{window.jobStatusCard.text()}\n'
                               + window.jobMessageCard.text())
        self.stopButton.setToolTip('停止当前所选任务')
        self.stopMenuButton.setEnabled(window.isJobRunning)
