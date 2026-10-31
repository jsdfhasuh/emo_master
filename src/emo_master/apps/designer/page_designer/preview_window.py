"""A single independent preview; closing this window never owns execution."""
from PySide2.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QToolButton, QMenu, QComboBox, QInputDialog

from .workspace import ObserverPages


class PreviewWindow(ObserverPages):
    allowsDesignExamples = True

    def reload(self, presentation):
        from emo_master.core.presentation.assets import pageAssets
        self.assets = pageAssets(self.controller.coordinator.session.document(), self.controller.coordinator.directory)
        self.setProjectRoot(self.controller.coordinator.directory)
        coordinator = self.controller.coordinator
        self.historyClient = self.controller.historyClient(coordinator.session.document())
        super().reload(presentation)

    def __init__(self, controller, presentation):
        self.controller = controller
        self.retiring = False
        self.pendingPresentation = None
        self.localPreviewOnly = True
        from emo_master.core.presentation.assets import pageAssets
        super().__init__(presentation, parent=controller.coordinator.window, label='页面预览',
                         assets=pageAssets(controller.coordinator.session.document(), controller.coordinator.directory),
                         projectRoot=controller.coordinator.directory,
                         historyClient=controller.historyClient(controller.coordinator.session.document()))
        self.setStyleSheet(self.styleSheet().replace("font-family: 'Microsoft YaHei'; font-size: 13px; ", '')
                          .replace('padding:9px 14px;', 'padding:6px 12px;'))
        self.setObjectName('pagePreview')
        self.bar = QWidget()
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.source = QComboBox()
        self.source.addItems(['示例数据', '运行结果', '历史数据'])
        self.source.currentIndexChanged.connect(self.chooseSource)
        row.addWidget(self.source)
        self.simulation = QComboBox()
        for title, state in [('默认示例', None), ('模拟合格', 'OK'), ('模拟不合格', 'NG'),
                             ('模拟等待', 'WAITING'), ('模拟错误', 'ERROR')]:
            self.simulation.addItem(title, state)
        self.simulation.currentIndexChanged.connect(self.simulate)
        row.addWidget(self.simulation)
        self.results = QToolButton()
        self.results.setText('查看运行结果')
        self.results.clicked.connect(lambda: self.run(controller.watchCurrent))
        row.addWidget(self.results)
        self.stop = QToolButton()
        self.stop.setText('停止查看')
        self.stop.clicked.connect(lambda: self.run(controller.stopViewing))
        row.addWidget(self.stop)
        row.addWidget(self.fitButton)
        row.addWidget(self.pageNavigation)
        row.addStretch()
        self.more = QToolButton()
        self.more.setText('更多')
        self.more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.more)
        menu.addAction('高级连接…', self.connectJob)
        menu.addAction('切换到流程设计', controller.coordinator.showFlow)
        self.more.setMenu(menu)
        row.addWidget(self.more)
        self.layout().insertWidget(0, self.bar)
        self.details = QWidget()
        detailsLayout = QVBoxLayout(self.details)
        for widget in (self.jobStatus, self.identity):
            detailsLayout.addWidget(widget)
        self.layout().addWidget(self.details)
        self.details.hide()
        detailsAction = menu.addAction('运行信息')
        detailsAction.setCheckable(True)
        detailsAction.toggled.connect(self.details.setVisible)
        self.resumeButton.setText('继续更新')
        self.fitButton.setText('适应窗口')
        self.status.setText('预览页面外观和按钮跳转，不会启动流程')
        self.setDesignExamples(True)
        self.setSource(False)

    def fitToAvailableScreen(self, *, resize=True, screen=None):
        super().fitToAvailableScreen(resize=resize, screen=screen)
        self.setMinimumSize(min(460, self.maximumWidth()), min(240, self.maximumHeight()))
        if resize and hasattr(self, 'status'):
            self.status.setText('已适应当前屏幕')

    def run(self, command):
        try:
            command()
        except (ValueError, KeyError) as error:
            self.controller.message(str(error))

    def displayMessage(self, message):
        for prefix, text in [('NOT_CAPTURED', '本次运行未采集所需数据'),
                             ('SOURCE_NOT_CAPTURED', '本次运行未采集所需数据'),
                             ('OFFLINE', '尚未连接运行结果'), ('CONNECTING', '正在连接结果服务'),
                             ('DISCONNECTED', '连接已断开'), ('CONNECTED', '结果服务已连接'),
                             ('RESOURCE_EXPIRED', '此结果已过期，请查看新的运行结果'),
                             ('PIN_EXPIRED', '固定的结果已过期，请继续更新'),
                             ('未绑定', '没有选择数据来源')]:
            if message.startswith(prefix):
                return text
        return super().displayMessage(message)

    def submit(self, view):
        super().submit(view)
        if self.frozen:
            self.modeLabel.setText('已固定当前结果 · 流程继续运行')
        elif self.hub:
            self.modeLabel.setText('自动更新结果')
        if self.simulationState:
            self.banner.setText('模拟状态 · 非检测结果')
            self.status.setText('仅演示显示效果，不会运行流程')

    def setSource(self, real):
        index = 1 if real else (2 if self.source.currentIndex() == 2 else 0)
        self.source.blockSignals(True)
        self.source.setCurrentIndex(index)
        self.source.blockSignals(False)
        self.localPreviewOnly = index == 0
        self._syncLocalPreviews()
        self.simulation.setVisible(index == 0)
        self.stop.setEnabled(real)
        self.stop.setVisible(real)
        self.resumeButton.setVisible(real)
        self.modeLabel.setVisible(real)

    def chooseSource(self, index):
        if index == 1:
            self.run(self.controller.watchCurrent)
        else:
            # Keep the previous samples/connection teardown from triggering a
            # local query before the selected source mode has been applied.
            self.localPreviewOnly = True
            self._syncLocalPreviews()
            callback = self.showLocalData if index == 2 else self.showSamples
            self.run(lambda: self.controller.stopViewing(callback))

    def showSamples(self):
        self.source.blockSignals(True)
        self.source.setCurrentIndex(0)
        self.source.blockSignals(False)
        self.reload(self.controller.coordinator.session.presentation.snapshot())
        self.setSource(False)
        self.simulate()
        self.status.setText('预览页面外观和按钮跳转，不会启动流程')

    def showLocalData(self):
        # Job observation may retain only its workflow's pages. Restore the
        # full draft while database components are still in preview mode.
        self.localPreviewOnly = True
        self._syncLocalPreviews()
        self.reload(self.controller.coordinator.session.presentation.snapshot())
        self.setDesignExamples(False)
        self.setSimulationState(None)
        self.source.blockSignals(True)
        self.source.setCurrentIndex(2)
        self.source.blockSignals(False)
        self.setSource(False)
        self.banner.setText('历史数据查阅')
        self.status.setText('按组件配置读取本机或 Runtime 数据 · 点击组件刷新读取')

    def simulate(self, *_args):
        if self.hub is not None or self.captureCoverage is not None:
            return
        state = self.simulation.currentData()
        if state is None:
            self.setDesignExamples(True)
        else:
            self.setSimulationState(state)

    def connectJob(self):
        address, ok = QInputDialog.getText(self, '高级连接', 'Runtime 主机:端口，例如 192.168.1.20:50051')
        if not ok:
            return
        job, ok = QInputDialog.getText(self, '高级连接', '运行编号')
        if ok:
            self.setDesignExamples(False)
            self.setSimulationState(None)
            self.setSource(True)
            self.run(lambda: self.controller.connect(address, job))

    def resumeLive(self, _checked=False, *, submit=True):
        super().resumeLive(_checked, submit=submit)
        self.modeLabel.setText('自动更新结果')
        pending = self.pendingPresentation
        self.pendingPresentation = None
        if pending is not None and self.config != pending:
            self.reload(pending)
        elif submit:
            from emo_master.core.presentation.assets import pageAssets
            self.setAssets(pageAssets(self.controller.coordinator.session.document(), self.controller.coordinator.directory))

    def closeEvent(self, event):
        super().closeEvent(event)
        if not self.retiring:
            self.controller.previewClosed(self)
