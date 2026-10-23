from types import SimpleNamespace

from tests.designer.test_run_inspector_ui import windowWithNode, deliver
from tests.designer.test_node_run_inspection import event


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


def testDisabledImageMessageFollowsInvocationAndPortNotCurrentDraft(monkeypatch):
    window, node = windowWithNode()
    coordinator = window.nodeResultCoordinator
    panel = coordinator.panel
    selections = []
    monkeypatch.setattr(coordinator.images, 'select', selections.append)
    overlayMessage = '此次执行未启用“绘制叠加图”，未生成 overlay。'
    cropMessage = '此次执行未启用“输出裁剪图”，未生成 croppedImage。'
    try:
        definition = window.flowModel.nodes[node]
        definition.operatorId = 'vision.inference.yolo'
        definition.outputPorts = {'overlay': 'image', 'croppedImage': 'image'}
        definition.params['drawOverlay'] = False
        window._setCurrentJobId('job')
        coordinator.accepted('job')
        raw = event(node=node)
        raw['payload']['diagnostics'] = {'imageOutputMessages': {
            'overlay': overlayMessage, 'croppedImage': cropMessage}}
        window.runtimeController._onRuntimeEvent(SimpleNamespace(**raw))
        assert panel.image.text() == overlayMessage
        assert selections[-1] is None and not panel.enlarge.isEnabled()
        panel.ports.setCurrentIndex(panel.ports.findData('croppedImage'))
        assert panel.image.text() == cropMessage
        assert selections[-1] is None
        panel.ports.setCurrentIndex(panel.ports.findData('overlay'))
        definition.params['drawOverlay'] = True
        coordinator.refresh()
        assert panel.image.text() == overlayMessage

        deliver(window, node, 'node.started', 3, 'enabled')
        assert '未启用' not in panel.image.text()
        deliver(window, node, sequence=4, execution='enabled')
        assert selections[-1].nodeRunId == 'enabled' and selections[-1].port == 'overlay'
        assert '未启用' not in panel.image.text()
        panel.previous.click()
        assert panel.image.text() == overlayMessage and selections[-1] is None
        assert coordinator.history.view('main', node)['record']['nodeRunId'] == 'invocation'
        panel.live.click()
        assert selections[-1].nodeRunId == 'enabled'
        assert definition.params['drawOverlay'] is True

        deliver(window, node, 'node.started', 5, 'failed')
        failed = event('node.failed', 6, node=node, execution='failed')
        failed['payload']['diagnostics'] = raw['payload']['diagnostics']
        window.runtimeController._onRuntimeEvent(SimpleNamespace(**failed))
        assert panel.image.text() == '本次执行未产生有效图片'
        assert selections[-1] is None
    finally:
        window.close()


def testRunTargetsHaveSeparateHistoryAndProjectSwitchReleasesBoth():
    window, node = windowWithNode()
    coordinator = window.nodeResultCoordinator
    try:
        for key, job, value in [('target-a', 'a', 2), ('target-b', 'b', 3)]:
            coordinator.forWorkflow('onInspectionAccepted', key, SimpleNamespace(job_id=job, project_revision=7))
            coordinator.forWorkflow('onInspectionEvent', key, event(job=job, node=node, value=value))
        for key, expected in [('target-a', 2), ('target-b', 3), ('target-a', 2)]:
            coordinator.selectWorkflow(key)
            assert f'count = {expected}' in coordinator.panel.values.toPlainText()
            assert not coordinator.panel.previous.isEnabled()
        contexts = list(coordinator.workflowContexts.values())
        coordinator.reset()
        assert all(context['history'].retainedBytes == 0 for context in contexts)
        assert coordinator.workflowContexts == {} and coordinator.history.current is None
    finally:
        window.close()


def testExpiredContextClearsHistoryAndImageInsteadOfKeepingOldResults():
    window, node = windowWithNode()
    coordinator = window.nodeResultCoordinator
    try:
        window._setCurrentJobId('job')
        coordinator.accepted('job')
        deliver(window, node, value=2)
        assert 'count = 2' in coordinator.panel.values.toPlainText()
        coordinator.images._mailbox = {'contextExpired': True, 'message': 'E_INSPECTION_SESSION_EXPIRED'}
        coordinator._imageReady(coordinator.images.generation)
        assert coordinator.history.retainedBytes == 0 and coordinator.history.current is None
        assert 'count = 2' not in coordinator.panel.values.toPlainText()
        assert coordinator.pixmap.isNull() and coordinator.imagePacket is None
    finally:
        window.close()


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
