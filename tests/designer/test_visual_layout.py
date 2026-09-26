from __future__ import annotations

from itertools import combinations
from types import SimpleNamespace

import pytest

import emo_master  # noqa: F401 - preload the Windows dependency DLLs before Qt

pytest.importorskip("PySide2")
from PySide2.QtCore import QRectF, QResource, QTimer, Qt
from PySide2.QtGui import QImage, QPainter, QPixmap
from PySide2.QtWidgets import QGraphicsSimpleTextItem, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel, FlowScene
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.widgets import ElidedLabel, PreviewLabel, WorkflowTabs
from emo_master.apps.designer.ui.icon_map import icon


@pytest.fixture
def styledApp(designerApplication):
    return designerApplication


def makeWindow():
    values = {}
    settings = SimpleNamespace(value=lambda key, default=None: values.get(key, default),
                               setValue=lambda key, value: values.__setitem__(key, value))
    window = MainWindow(SimpleNamespace(listOperators=lambda: []), settingsStore=settings)
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    return window


@pytest.mark.parametrize("inputs,outputs", [(0, 0), (0, 5), (5, 0), (6, 8), (16, 16)])
def testNodeContainsAllTextAndPorts(styledApp, inputs, outputs):
    scene = FlowScene()
    model = FlowNodeViewModel("test", "相机采集 Camera - 192.168.125.28", 0, 0,
                              {f"输入图像_{i}": "image" for i in range(inputs)},
                              {f"actualExposureUs_{i}": "number" for i in range(outputs)})
    scene.addFlowNode(model)
    node = scene._nodeItems["test"]
    assert 260 <= node.rect().width() <= 420
    assert node.rect().contains(node.childrenBoundingRect())
    labels = [child for child in node.childItems() if isinstance(child, QGraphicsSimpleTextItem)]
    for first, second in combinations(labels, 2):
        assert not first.mapRectToParent(first.boundingRect()).intersects(second.mapRectToParent(second.boundingRect()))
    assert all(label.font().pointSizeF() == 10.5 for label in labels)


def testLongNodeNamesAreElidedWithFullTooltips(styledApp):
    scene = FlowScene()
    title = "非常长的节点名称-" * 30
    port = "unbroken_output_identifier_" * 20
    scene.addFlowNode(FlowNodeViewModel("test", title, 0, 0, {}, {port: "image"}))
    node = scene._nodeItems["test"]
    assert node.rect().width() == 420
    assert node.rect().contains(node.childrenBoundingRect())
    tooltips = [item.toolTip() for item in node.childItems()]
    assert title in tooltips
    assert f"{port}: image" in tooltips


def testAutoLayoutUsesVariableNodeHeights(styledApp):
    scene = FlowScene()
    for index, count in enumerate((2, 14, 6, 8, 20, 1)):
        scene.addFlowNode(FlowNodeViewModel(str(index), "Camera", 0, 0, {},
                                          {f"port_{i}": "number" for i in range(count)}))
    scene.layoutNodesGrid(columns=2)
    for first, second in combinations(scene._nodeItems.values(), 2):
        assert not first.sceneBoundingRect().intersects(second.sceneBoundingRect())
    nextX, nextY = scene.nextNodePosition()
    assert nextX == 20
    assert nextY > max(item.sceneBoundingRect().bottom() for item in scene._nodeItems.values())


def testSidebarStaysCollapsedAcrossShowResizeAndRestore(styledApp):
    window = makeWindow()
    try:
        window.show()
        for width, height in ((1280, 720), (960, 540), (1800, 900)):
            window.resize(width, height)
            window.restoreMainSplitterSizes()
            styledApp.processEvents()
            assert window.sidebarContainer.width() == 36
            assert window.sidebarContainer.minimumWidth() == 36
            assert window.sidebarContainer.maximumWidth() == 36
        window.expandSidebar()
        window.saveMainSplitterSizes([260, 850, 320])
        stored = window.getMainSplitterSizes()
        window.collapseSidebar()
        window.resize(1200, 680)
        styledApp.processEvents()
        window.expandSidebar()
        assert window.sidebarContainer.width() >= 180
        assert window.layoutController._expandedSplitterSizes == stored
    finally:
        window.close()


def testTabsDoNotReserveEmptyPagesOrMoveOnSelection(styledApp):
    tabs = WorkflowTabs()
    tabs.setObjectName("workflowTabs")
    tabs.setAttribute(Qt.WA_DontShowOnScreen, True)
    for index in range(20):
        tabs.addTab(QWidget(), f"Camera Capture 中文流程 {index} [入口]")
    tabs.addTab(QWidget(), "+")
    tabs.resize(520, 40)
    tabs.show()
    styledApp.processEvents()
    try:
        assert tabs.findChild(QStackedWidget).isHidden()
        assert tabs.height() < 60
        size = tabs.tabBar().tabSizeHint(1)
        tabs.setCurrentIndex(1)
        styledApp.processEvents()
        assert tabs.tabBar().tabSizeHint(1) == size
        assert tabs._addButton.isVisible()
        assert tabs.rect().contains(tabs._addButton.geometry())
        clicked = []
        tabs.tabBarClicked.connect(clicked.append)
        tabs._addButton.click()
        assert clicked[-1] == 20
    finally:
        tabs.close()


def testLongDetailsDoNotForceWindowBeyondViewport(styledApp):
    window = makeWindow()
    try:
        window.jobMessageCard.setText("C:/" + "very-long-directory/" * 60 + "project.json")
        window.nodeDetailParamsCard.setText("\n".join(f"parameter_{i}: " + "x" * 120 for i in range(80)))
        window.resize(1000, 600)
        window.show()
        styledApp.processEvents()
        assert window.minimumSizeHint().height() < 500
        assert window.minimumSizeHint().width() < 800
        assert window.nodeDetailsScroll.verticalScrollBar().maximum() > 0
        assert window.jobMessageCard.toolTip().endswith("project.json")
    finally:
        window.close()


def testManualZoomSurvivesNodeCreationAndResize(styledApp):
    window = makeWindow()
    try:
        window.show()
        styledApp.processEvents()
        window.flowView.setZoomFactor(1.5)
        window.addNodeFromOperatorPayload({"operatorId": "vision.test.echo", "displayName": "Echo",
                                           "inputPorts": {}, "outputPorts": {}, "paramSchema": {}})
        window.resize(1100, 650)
        styledApp.processEvents()
        assert window.flowView.getZoomFactor() == pytest.approx(1.5)
        assert window.zoomResetButton.text() == "150%"
        window.flowView.zoomByDelta(1)
        assert window.flowView.getZoomFactor() == pytest.approx(1.5 * 1.15)
    finally:
        window.close()


def testToolbarAndMenuShareEnabledActions(styledApp):
    window = makeWindow()
    try:
        action = window._menuActions["开始运行"]
        assert action is window.startButton.defaultAction()
        assert not action.isEnabled()
        window.loadedProjectPath = "fixture/project.json"
        window.updateToolbarState()
        assert action.isEnabled()
        window.isJobRunning = True
        window.currentJobId = "fixture-job"
        window.updateToolbarState()
        assert not action.isEnabled()
        assert window._menuActions["停止运行"].isEnabled()
    finally:
        window.isJobRunning = False
        window.currentJobId = None
        window.close()


def testPreviewRetainsOriginalImageAndAspectRatio(styledApp):
    label = PreviewLabel()
    source = QPixmap(800, 400)
    source.fill(Qt.red)
    label.resize(300, 200)
    label.setPixmap(source)
    label.setAttribute(Qt.WA_DontShowOnScreen, True)
    label.show()
    styledApp.processEvents()
    try:
        for width in (200, 500, 300):
            label.resize(width, 220)
            styledApp.processEvents()
            image = label.pixmap()
            assert image.width() / image.height() == pytest.approx(2, abs=0.02)
            assert image.width() <= label.width()
        assert label._sourcePixmap.width() == 800
    finally:
        label.close()


def testOfflineIconsAndElidedLabels(styledApp):
    for name in ("play", "folder-open", "panel-left-close"):
        assert not icon(name).isNull()
        assert not icon(name).pixmap(32, 32).isNull()
    assert QResource(":/designer/chevron-down.svg").isValid()
    label = ElidedLabel("X" * 1000)
    label.resize(200, 32)
    assert label.minimumSizeHint().width() == 0
    assert label.text() == "X" * 1000
    assert label.toolTip() == label.text()


def testFirstFitWaitsForFinalViewportSize(styledApp):
    window = makeWindow()
    try:
        window.flowScene.addFlowNode(FlowNodeViewModel("camera", "Camera", 0, 0, {}, {}))
        bounds = window.flowScene.getContentBounds()
        window.resize(1600, 900)
        window.flowView.fitContent(bounds, minimumZoom=0.85)
        window.show()
        QTimer.singleShot(0, lambda: window.resize(800, 500))
        for _ in range(8):
            styledApp.processEvents()
        viewport = window.flowView.viewport()
        expected = max(0.85, min((viewport.width() - 24) / bounds[2],
                                 (viewport.height() - 24) / bounds[3], 1.0))
        assert window.flowView.getZoomFactor() == pytest.approx(expected)
        assert window.flowView._pendingFit is None
        window.flowView.setZoomFactor(1.2)
        window.resize(1300, 800)
        styledApp.processEvents()
        assert window.flowView.getZoomFactor() == pytest.approx(1.2)
    finally:
        window.close()


def testNarrowToolbarKeepsRunActionAndHasOverflow(styledApp):
    window = makeWindow()
    try:
        window.resize(640, 500)
        window.show()
        for _ in range(4):
            styledApp.processEvents()
        extension = window.mainToolbar.findChild(QToolButton, "qt_toolbar_ext_button")
        assert extension is not None and extension.isVisible()
        assert window.startButton.isVisible()
        assert not window.openLogsButton.isVisible()
        assert window._toolbarActions["打开日志"] in window.mainToolbar.actions()
    finally:
        window.close()


def testLogMessageAppearsInInitialViewport(styledApp):
    from emo_master.apps.designer.ui.log_dialog import StructuredLogView

    view = StructuredLogView()
    view.setAttribute(Qt.WA_DontShowOnScreen, True)
    try:
        view.resize(900, 520)
        view.show()
        styledApp.processEvents()
        header = view.table.horizontalHeader()
        assert header.visualIndex(9) == 3
        assert header.sectionViewportPosition(9) < view.table.viewport().width() - 200
    finally:
        view.close()


@pytest.mark.parametrize("name,operatorId", [
    ("HuarayCameraEditor", "vendor.camera"),
    ("VendorCamera", "vision.io.huaray_camera"),
])
def testThirdPartyEditorLayoutIsNotReplaced(styledApp, name, operatorId):
    from emo_master.apps.designer.operator_editors.builtin_layout import prepareBuiltinLayout

    root = QWidget()
    root.setObjectName(name)
    layout = QVBoxLayout(root)
    content = QWidget(root)
    layout.addWidget(content)
    prepareBuiltinLayout(root, operatorId)
    assert root.layout() is layout
    assert layout.count() == 1
    assert layout.itemAt(0).widget() is content


def testSelectedNodePaintsAnUnclippedHighlight(styledApp):
    scene = FlowScene()
    scene.addFlowNode(FlowNodeViewModel("camera", "相机 Camera", 0, 0,
                                      {"输入图像": "image"}, {"image": "image"}))
    node = scene._nodeItems["camera"]
    bounds = node.sceneBoundingRect().adjusted(-4, -4, 4, 4)

    def render(selected):
        node.setSelected(selected)
        image = QImage(round(bounds.width() * 2), round(bounds.height() * 2), QImage.Format_ARGB32)
        image.fill(Qt.white)
        painter = QPainter(image)
        scene.render(painter, QRectF(image.rect()), bounds)
        painter.end()
        return image

    idle, selected = render(False), render(True)
    assert idle != selected
    bluePixels = sum(selected.pixelColor(x, y).name() == "#2563eb"
                     for x in range(selected.width()) for y in range(4, 14))
    assert bluePixels > 100
    assert all(selected.pixelColor(x, 0) == idle.pixelColor(x, 0) for x in range(selected.width()))
    assert node.rect().contains(node.childrenBoundingRect())
