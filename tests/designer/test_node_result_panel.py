from types import SimpleNamespace

from tests.designer.test_run_inspector_ui import windowWithNode, deliver


def testUnifiedPanelUsesRealValuesAndPreviousRunWithoutChangingDraft():
    window, node = windowWithNode()
    history = window.nodeResultCoordinator.history
    panel = window.nodeResultCoordinator.panel
    before = window.flowModel.toProjectGraph()
    window._setCurrentJobId('job')
    window.nodeResultCoordinator.accepted('job')
    deliver(window, node, value=0)
    assert 'count = 0' in panel.values.toPlainText()
    assert panel.tabs.currentWidget() is panel.values
    assert not panel.tabs.isTabVisible(0)
    window._setCurrentJobId(None)  # Startup attempt, before a new acceptance.
    assert history.current.jobId == 'job' and history.previous is None
    window._setCurrentJobId('b')
    window.nodeResultCoordinator.accepted('b')
    raw = {'eventType': 'node.completed', 'jobId': 'b', 'workflowId': 'main', 'nodeId': node,
           'nodeRunId': 'b-node', 'sequence': 1, 'payload': {'outputs': {'count': False}}}
    window.runtimeController._onRuntimeEvent(SimpleNamespace(**raw))
    assert 'count = false' in panel.values.toPlainText()
    panel.previous.click()
    assert 'count = 0' in panel.values.toPlainText()
    assert '只读（历史）' in panel.origin.text()
    assert window.currentJobId == 'b'
    assert window.flowModel.toProjectGraph() == before


def testDeclaredButMissingImageKeepsImageTabAndMissingHistoryDoesNotUseDraft():
    window, node = windowWithNode()
    window.flowModel.nodes[node].outputPorts['image'] = 'image'
    window._setCurrentJobId('a')
    window.nodeResultCoordinator.accepted('a')
    panel = window.nodeResultCoordinator.panel
    assert panel.tabs.isTabVisible(0)
    assert panel.tabs.currentIndex() == 0
    assert panel.ports.currentData() == 'image'
    assert not panel.enlarge.isEnabled()
    window.addNodeFromOperatorPayload({'operatorId': 'new', 'displayName': '后来新增',
        'inputPorts': {}, 'outputPorts': {'value': 'number'}, 'paramSchema': {}})
    assert '所选任务没有此节点' in panel.values.toPlainText()


def testLongFrozenNameHasFullTooltipAndDoesNotExpandSidebar(designerApplication):
    window, node = windowWithNode()
    name = '真实执行时的长节点名称' * 24
    window.flowModel.nodes[node].displayName = name
    window._setCurrentJobId('a')
    window.nodeResultCoordinator.accepted('a')
    panel = window.nodeResultCoordinator.panel
    window.resize(1280, 720)
    window.show()
    designerApplication.processEvents()
    assert name in panel.title.toolTip()
    assert panel.title.minimumSizeHint().width() == 0
    assert panel.title.width() <= panel.width() <= 500
    assert not window.runResultTools.isVisible()
    window.flowModel.nodes[node].displayName = '后来编辑的草稿名称'
    window.nodeResultCoordinator.refresh()
    assert panel.title.text() == name and name in panel.title.toolTip()
    window.close()


def testShortImagePageScrollsWithoutMetadataOverlappingPixels(designerApplication):
    from PySide2.QtCore import QRect, Qt
    from PySide2.QtGui import QPixmap
    from emo_master.apps.designer.ui.node_result_panel import NodeResultPanel
    from emo_master.apps.designer.state.run_result_history import RunResultHistory
    history = RunResultHistory()
    history.accept('a', [{'workflowId': 'main', 'nodeId': 'load', 'displayName': '本地图片输入',
                         'operatorId': 'vision.io.image_loader', 'outputPorts': {'image': 'image'}}])
    panel = NodeResultPanel(history)
    try:
        panel.resize(260, 360)
        panel.render('main', 'load')
        panel.image.setPixmap(QPixmap(360, 240))
        panel.imageMeta.setText('真实端口 image · 360 × 240 · PNG\n2026-10-04 19:00:00.123 · 任务 abcdefgh · 执行 abcdefgh')
        panel.enlarge.setEnabled(True)
        panel.show()
        for _ in range(8):
            designerApplication.processEvents()
        assert panel.image.geometry().bottom() < panel.imageMeta.geometry().top()
        assert panel.imageMeta.geometry().bottom() < panel.enlarge.geometry().top()
        needed = panel.imageMeta.fontMetrics().boundingRect(QRect(0, 0, panel.imageMeta.width(), 10000),
            Qt.TextWordWrap, panel.imageMeta.text()).height()
        assert panel.imageMeta.height() >= needed
        panel.imagePage.ensureWidgetVisible(panel.enlarge)
        designerApplication.processEvents()
        assert not panel.enlarge.visibleRegion().isEmpty()
        assert panel.imagePage.verticalScrollBar().maximum() > 0
    finally:
        panel.close()


def testNarrowPanelReservesWrappedHeaderAndActionsInOuterScroll(designerApplication):
    from PySide2.QtWidgets import QWidget, QVBoxLayout, QSplitter
    from emo_master.apps.designer.ui.node_result_panel import NodeResultPanel
    from emo_master.apps.designer.ui.widgets import scrollContent
    from emo_master.apps.designer.state.run_result_history import RunResultHistory
    history = RunResultHistory()
    history.accept('12345678-1234-1234-1234-123456789012', [
        {'workflowId': 'main', 'nodeId': 'load', 'displayName': '图像输入',
         'operatorId': 'vision.io.image_loader', 'outputPorts': {'image': 'image'}}])
    panel = NodeResultPanel(history)
    host = QWidget()
    layout = QVBoxLayout(host)
    summary = QWidget()
    summary.setFixedHeight(120)
    layout.addWidget(summary)
    layout.addWidget(panel, 1)
    host.setFixedWidth(260)
    splitter = QSplitter()
    splitter.addWidget(QWidget())
    splitter.addWidget(host)
    scroll = scrollContent(splitter)
    scroll.resize(540, 400)
    try:
        panel.render('main', 'load')
        scroll.show()
        for _ in range(8):
            designerApplication.processEvents()
        assert panel.origin.height() >= panel.origin.heightForWidth(panel.origin.width())
        assert panel.tabs.geometry().bottom() < panel.configure.geometry().top()
        assert panel.height() >= panel.layout().minimumHeightForWidth(panel.width())
        scroll.ensureWidgetVisible(panel.configure)
        designerApplication.processEvents()
        assert not panel.configure.visibleRegion().isEmpty()
        assert scroll.verticalScrollBar().maximum() > 0
    finally:
        scroll.close()


def testNarrowNativeTabArrowsMakeInputsReachableByMouse(designerApplication):
    from PySide2.QtCore import Qt
    from PySide2.QtTest import QTest
    from PySide2.QtWidgets import QToolButton
    from emo_master.apps.designer.ui.node_result_panel import NodeResultPanel
    from emo_master.apps.designer.state.run_result_history import RunResultHistory
    history = RunResultHistory()
    history.accept('a', [{'workflowId': 'main', 'nodeId': 'load', 'displayName': '图像输入',
                         'operatorId': 'vision.io.image_loader', 'outputPorts': {'image': 'image'}}])
    panel = NodeResultPanel(history)
    try:
        panel.resize(260, 400)
        panel.render('main', 'load')
        panel.show()
        for _ in range(8):
            designerApplication.processEvents()
        bar = panel.tabs.tabBar()
        right = next(button for button in bar.findChildren(QToolButton) if button.arrowType() == Qt.RightArrow)
        assert right.isVisible() and right.width() >= 16
        for _ in range(4):
            if bar.tabRect(3).center().x() < right.geometry().left():
                break
            QTest.mouseClick(right, Qt.LeftButton)
            designerApplication.processEvents()
        point = bar.tabRect(3).center()
        assert 0 <= point.x() < right.geometry().left()
        QTest.mouseClick(bar, Qt.LeftButton, pos=point)
        assert panel.tabs.currentWidget() is panel.inputs
    finally:
        panel.close()
