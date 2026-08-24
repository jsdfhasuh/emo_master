from datetime import datetime
import json
from pathlib import Path
from typing import Callable, cast

from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.controllers import (
    LayoutController,
    OperatorCatalogController,
    ProjectController,
    RuntimeController,
    WorkflowController,
)
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.presenters import NodeDetailsPresenter
from emo_master.apps.designer.ui.flow_scene import (
    FlowEdgeViewModel,
    FlowNodeViewModel,
    FlowScene,
)
from emo_master.apps.designer.ui.designer_graphics_view import DesignerGraphicsView
from emo_master.apps.designer.ui.log_dialog import LogDialog
from emo_master.apps.designer.ui.node_param_dialog import NodeParamDialog
from emo_master.apps.designer.ui.icon_map import getOperatorGlyph
from emo_master.apps.designer.ui.operator_bubble import OperatorBubble
from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState

try:
    from PySide2.QtCore import QSettings, Qt
    from PySide2.QtGui import QKeySequence, QPixmap
    from PySide2.QtWidgets import (
        QAction,
        QFileDialog,
        QGraphicsView,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMenuBar,
        QPushButton,
        QShortcut,
        QSplitter,
        QTabWidget,
        QTextEdit,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )

    _userRole = int(Qt.UserRole)
except Exception:  # pragma: no cover

    class Qt:  # type: ignore[no-redef]
        AlignCenter = 0
        UserRole = 0
        KeepAspectRatio = 0
        SmoothTransformation = 0
        Horizontal = 0

    class QWidget:  # type: ignore[no-redef]
        def setLayout(self, layout) -> None:
            _ = layout

        def setVisible(self, visible: bool) -> None:
            _ = visible

        def setFixedWidth(self, width: int) -> None:
            _ = width

        def setMinimumWidth(self, width: int) -> None:
            _ = width

        def setMaximumWidth(self, width: int) -> None:
            _ = width

        def setMinimumHeight(self, height: int) -> None:
            _ = height

    class QHBoxLayout:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._stretch: dict[int, int] = {}

        def addLayout(self, layout, stretch=0) -> None:
            _ = layout
            _ = stretch

        def addWidget(self, widget, stretch=0) -> None:
            _ = widget
            _ = stretch

        def addStretch(self, stretch: int = 0) -> None:
            _ = stretch

        def setStretch(self, index: int, stretch: int) -> None:
            self._stretch[index] = stretch

        def setContentsMargins(
            self, left: int, top: int, right: int, bottom: int
        ) -> None:
            _ = left
            _ = top
            _ = right
            _ = bottom

        def setSpacing(self, spacing: int) -> None:
            _ = spacing

    class QVBoxLayout:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._items: list[object] = []

        def addWidget(self, widget) -> None:
            _ = widget

        def addStretch(self, stretch: int = 0) -> None:
            _ = stretch

        def setContentsMargins(
            self, left: int, top: int, right: int, bottom: int
        ) -> None:
            _ = left
            _ = top
            _ = right
            _ = bottom

        def setSpacing(self, spacing: int) -> None:
            _ = spacing

    class QLabel:  # type: ignore[no-redef]
        def __init__(self, text: str) -> None:
            self._text = text
            self._styleSheet = ""

        def setText(self, text: str) -> None:
            self._text = text

        def text(self) -> str:
            return self._text

        def setMinimumHeight(self, height: int) -> None:
            _ = height

        def setAlignment(self, alignment: int) -> None:
            _ = alignment

        def setObjectName(self, name: str) -> None:
            _ = name

        def setPixmap(self, pixmap) -> None:
            _ = pixmap

        def setStyleSheet(self, style: str) -> None:
            self._styleSheet = style

        def styleSheet(self) -> str:
            return self._styleSheet

    class _SignalStub:
        def __init__(self) -> None:
            self._callbacks: list[Callable[..., object]] = []

        def connect(self, callback: Callable[..., object]) -> None:
            self._callbacks.append(callback)

        def emit(self, *args) -> None:
            for callback in list(self._callbacks):
                callback(*args)

    class QSettings:  # type: ignore[no-redef]
        _store: dict[str, object] = {}

        def __init__(self, organization: str, application: str) -> None:
            _ = organization
            _ = application

        def value(self, key: str, default=None):
            return self._store.get(key, default)

        def setValue(self, key: str, value: object) -> None:
            self._store[key] = value

    class QPushButton:  # type: ignore[no-redef]
        def __init__(self, text: str) -> None:
            self._text = text
            self.clicked = _SignalStub()

        def setEnabled(self, enabled: bool) -> None:
            _ = enabled

        def setObjectName(self, name: str) -> None:
            _ = name

        def setCheckable(self, value: bool) -> None:
            _ = value

        def setChecked(self, value: bool) -> None:
            _ = value

        def setText(self, text: str) -> None:
            self._text = text

        def text(self) -> str:
            return self._text

    class QShortcut:  # type: ignore[no-redef]
        def __init__(self, sequence, parent) -> None:
            _ = sequence
            _ = parent
            self.activated = _SignalStub()

    class QKeySequence:  # type: ignore[no-redef]
        Delete = "Delete"

        def __init__(self, text: str) -> None:
            _ = text

    class QTextEdit:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._text = ""

        def setReadOnly(self, value: bool) -> None:
            _ = value

        def append(self, text: str) -> None:
            _ = text

        def setPlainText(self, text: str) -> None:
            self._text = text

        def toPlainText(self) -> str:
            return self._text

        def setFixedHeight(self, height: int) -> None:
            _ = height

    class QMainWindow:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._width = 1200
            self._menuBar = None

        def setWindowTitle(self, text: str) -> None:
            _ = text

        def resize(self, width: int, height: int) -> None:
            self._width = width
            _ = height

        def width(self) -> int:
            return self._width

        def setCentralWidget(self, widget) -> None:
            _ = widget

        def addToolBar(self, toolbar) -> None:
            _ = toolbar

        def setMenuBar(self, menuBar) -> None:
            self._menuBar = menuBar

        def menuBar(self):
            return self._menuBar

    class QGraphicsView:  # type: ignore[no-redef]
        def __init__(self, scene) -> None:
            _ = scene
            self._center = (0.0, 0.0)

        def setAcceptDrops(self, value: bool) -> None:
            _ = value

        def centerOn(self, x: float, y: float) -> None:
            self._center = (x, y)

        def fitInView(
            self, x: float, y: float, width: float, height: float, mode
        ) -> None:
            _ = mode
            self._center = (x + width / 2.0, y + height / 2.0)

        def resetTransform(self) -> None:
            return

        def scale(self, sx: float, sy: float) -> None:
            _ = sx
            _ = sy

    class QListWidgetItem:  # type: ignore[no-redef]
        def __init__(self, text: str) -> None:
            self._text = text
            self._data: dict[int, object] = {}

        def setData(self, role: int, value: object) -> None:
            self._data[role] = value

        def data(self, role: int) -> object:
            return self._data.get(role)

        def text(self) -> str:
            return self._text

    class QListWidget(QWidget):  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.itemClicked = _SignalStub()
            self.itemDoubleClicked = _SignalStub()
            self._items: list[QListWidgetItem] = []

        def clear(self) -> None:
            self._items = []

        def addItem(self, item) -> None:
            self._items.append(item)

        def setFixedHeight(self, height: int) -> None:
            _ = height

        def count(self) -> int:
            return len(self._items)

        def item(self, index: int):
            return self._items[index]

    class QTabWidget(QWidget):  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.currentChanged = _SignalStub()
            self.tabBarClicked = _SignalStub()
            self._tabs: list[tuple[object, str]] = []
            self._tabData: list[object] = []
            self._currentIndex = -1

        def addTab(self, widget, title: str) -> int:
            self._tabs.append((widget, title))
            self._tabData.append(None)
            if self._currentIndex < 0:
                self._currentIndex = 0
            return len(self._tabs) - 1

        def clear(self) -> None:
            self._tabs = []
            self._tabData = []
            self._currentIndex = -1

        def setCurrentIndex(self, index: int) -> None:
            if 0 <= index < len(self._tabs):
                self._currentIndex = index
                self.currentChanged.emit(index)

        def currentIndex(self) -> int:
            return self._currentIndex

        def count(self) -> int:
            return len(self._tabs)

        def tabText(self, index: int) -> str:
            return self._tabs[index][1]

        def setTabText(self, index: int, title: str) -> None:
            widget, _ = self._tabs[index]
            self._tabs[index] = (widget, title)

        def setTabData(self, index: int, value: object) -> None:
            self._tabData[index] = value

        def tabData(self, index: int):
            return self._tabData[index]

        def setTabsClosable(self, closable: bool) -> None:
            _ = closable

    class QSplitter(QWidget):  # type: ignore[no-redef]
        def __init__(self, orientation) -> None:
            _ = orientation
            self._widgets: list[object] = []
            self._sizes: list[int] = []
            self.splitterMoved = _SignalStub()

        def addWidget(self, widget) -> None:
            self._widgets.append(widget)

        def setChildrenCollapsible(self, collapsible: bool) -> None:
            _ = collapsible

        def setHandleWidth(self, width: int) -> None:
            _ = width

        def setSizes(self, sizes: list[int]) -> None:
            self._sizes = list(sizes)

        def sizes(self) -> list[int]:
            return list(self._sizes)

    class QPixmap:  # type: ignore[no-redef]
        def __init__(self, path: str) -> None:
            _ = path

        def isNull(self) -> bool:
            return True

        def scaled(self, width: int, height: int, aspectMode: int, transformMode: int):
            _ = width
            _ = height
            _ = aspectMode
            _ = transformMode
            return self

    class QToolBar:  # type: ignore[no-redef]
        def __init__(self, title: str) -> None:
            _ = title

        def setMovable(self, movable: bool) -> None:
            _ = movable

        def addWidget(self, widget) -> None:
            _ = widget

        def addSeparator(self) -> None:
            return

    class QAction:  # type: ignore[no-redef]
        def __init__(self, text: str, parent=None) -> None:
            _ = parent
            self._text = text
            self.triggered = _SignalStub()
            self._fontPointSize = 12

        def text(self) -> str:
            return self._text

        def setFontPointSize(self, size: int) -> None:
            self._fontPointSize = size

        def fontPointSize(self) -> int:
            return self._fontPointSize

    class QMenu:  # type: ignore[no-redef]
        def __init__(self, title: str) -> None:
            self._title = title
            self._actions: list[object] = []
            self._styleSheet = ""

        def addAction(self, action) -> None:
            self._actions.append(action)

        def addMenu(self, menu) -> None:
            self._actions.append(menu)

        def addSeparator(self) -> None:
            return

        def title(self) -> str:
            return self._title

        def clear(self) -> None:
            self._actions = []

        def setStyleSheet(self, style: str) -> None:
            self._styleSheet = style

    class QMenuBar(QWidget):  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._menus: list[QMenu] = []
            self._styleSheet = ""

        def addMenu(self, title: str):
            menu = QMenu(title)
            self._menus.append(menu)
            return menu

        def setStyleSheet(self, style: str) -> None:
            self._styleSheet = style

    class QFileDialog:  # type: ignore[no-redef]
        @staticmethod
        def getOpenFileName(parent, title: str, directory: str, filterText: str):
            _ = parent
            _ = title
            _ = directory
            _ = filterText
            return "", ""

        @staticmethod
        def getExistingDirectory(parent, title: str, directory: str):
            _ = parent
            _ = title
            _ = directory
            return ""

    class QInputDialog:  # type: ignore[no-redef]
        @staticmethod
        def getText(parent, title: str, label: str, text: str = ""):
            _ = parent
            _ = title
            _ = label
            return text, False

    _userRole = 0

USER_ROLE: int = _userRole


def _runtimeSequence(value: dict[str, object]) -> int:
    rawSequence = value.get("sequence", 0)
    if isinstance(rawSequence, bool):
        return 0
    if isinstance(rawSequence, int):
        return rawSequence
    if isinstance(rawSequence, float):
        return int(rawSequence)
    if isinstance(rawSequence, str):
        try:
            return int(rawSequence)
        except ValueError:
            return 0
    return 0


class MainWindow(QMainWindow):  # type: ignore[valid-type,misc]
    def __init__(
        self,
        runtimeClient,
        showStartupEntry: bool = False,
        projectEntryDialogFactory: Callable[[], object] | None = None,
        settingsStore: object | None = None,
    ) -> None:
        super().__init__()
        self.runtimeClient = runtimeClient
        self._showStartupEntry = showStartupEntry
        self._projectEntryDialogFactory = projectEntryDialogFactory
        self._startupEntryHandled = False
        self.settingsStore = (
            settingsStore
            if settingsStore is not None
            else QSettings("emo_master", "designer")
        )
        self._nodeRuntimeState: dict[str, dict[str, object]] = {}
        self._nodeRuntimeStateByRun: dict[tuple[str, str], dict[str, object]] = {}
        self._nodeRuntimeStateByWorkflowRun: dict[
            tuple[str, str, str], dict[str, object]
        ] = {}
        self.flowModel = FlowGraphModel()
        self.workflowStore = WorkflowStore()
        self.activeWorkflowId = self.workflowStore.activeWorkflowId
        self.nodeDetailsPresenter = NodeDetailsPresenter(
            flowModel=self.flowModel,
            nodeRuntimeState=self._nodeRuntimeState,
            getActiveWorkflowId=lambda: self.activeWorkflowId,
        )
        self.runtimePanelState = RuntimePanelState()
        self.nodeParamDialog: NodeParamDialog | None = None
        self.logDialog: LogDialog | None = None
        self.logBuffer: list[str] = []
        self.operatorCatalog: list[dict[str, object]] = []
        self.recentOperatorIds: list[str] = []
        self.maxRecentOperators = 6
        self.activeOperatorCategory = "全部"
        self.categoryButtons: dict[str, QPushButton] = {}
        self.isSidebarCollapsed = True
        self.operatorBubble: OperatorBubble | None = None
        self.activeParamNodeId: str | None = None
        self.loadedProjectPath: str | None = None
        self.currentProjectDir: Path | None = None
        self.currentJobId: str | None = None
        self.isJobRunning = False
        self._lastRunBlockedReason = ""
        self.setWindowTitle("视觉流程设计器")
        self.resize(1200, 760)

        self.mainToolbar = QToolBar("主工具栏")
        setMovable = getattr(self.mainToolbar, "setMovable", None)
        if callable(setMovable):
            setMovable(False)
        self.addToolBar(self.mainToolbar)

        self.mainMenuBar = QMenuBar()
        setMenuBar = getattr(self, "setMenuBar", None)
        if callable(setMenuBar):
            setMenuBar(self.mainMenuBar)

        rootWidget = QWidget()
        rootLayout = QVBoxLayout()
        setRootMargins = getattr(rootLayout, "setContentsMargins", None)
        if callable(setRootMargins):
            setRootMargins(12, 12, 12, 12)
        setRootSpacing = getattr(rootLayout, "setSpacing", None)
        if callable(setRootSpacing):
            setRootSpacing(0)

        self.sidebarContainer = QWidget()
        sideBarLayout = QVBoxLayout()
        setSidebarMargins = getattr(sideBarLayout, "setContentsMargins", None)
        if callable(setSidebarMargins):
            setSidebarMargins(0, 0, 0, 0)
        setSidebarSpacing = getattr(sideBarLayout, "setSpacing", None)
        if callable(setSidebarSpacing):
            setSidebarSpacing(8)
        self.sidebarToggleButton = QPushButton(">" if self.isSidebarCollapsed else "<")
        setToggleName = getattr(self.sidebarToggleButton, "setObjectName", None)
        if callable(setToggleName):
            setToggleName("sidebarToggleButton")
        self.sidebarToggleButton.clicked.connect(self.toggleSidebar)
        sideBarLayout.addWidget(self.sidebarToggleButton)

        self.categoryPanel = QWidget()
        setCategoryPanelName = getattr(self.categoryPanel, "setObjectName", None)
        if callable(setCategoryPanelName):
            setCategoryPanelName("sidebarCategorySection")
        categoryLayout = QVBoxLayout()
        setCategoryMargins = getattr(categoryLayout, "setContentsMargins", None)
        if callable(setCategoryMargins):
            setCategoryMargins(0, 0, 0, 0)
        setCategorySpacing = getattr(categoryLayout, "setSpacing", None)
        if callable(setCategorySpacing):
            setCategorySpacing(6)
        for categoryName in ["全部", "预处理", "检测", "测量", "输出", "控制流", "其他"]:
            categoryButton = QPushButton(
                f"{self._getCategoryGlyph(categoryName)} {categoryName}"
            )
            setCategoryName = getattr(categoryButton, "setObjectName", None)
            if callable(setCategoryName):
                setCategoryName("sideCategoryButton")
            setCheckable = getattr(categoryButton, "setCheckable", None)
            if callable(setCheckable):
                setCheckable(True)
            categoryButton.clicked.connect(
                self._buildCategoryClickHandler(categoryName)
            )
            categoryLayout.addWidget(categoryButton)
            self.categoryButtons[categoryName] = categoryButton
        self.categoryPanel.setLayout(categoryLayout)
        sideBarLayout.addWidget(self.categoryPanel)

        self.nodeListContainer = QWidget()
        setNodeListContainerName = getattr(
            self.nodeListContainer, "setObjectName", None
        )
        if callable(setNodeListContainerName):
            setNodeListContainerName("sidebarNodeSection")
        nodeListLayout = QVBoxLayout()
        setNodeListMargins = getattr(nodeListLayout, "setContentsMargins", None)
        if callable(setNodeListMargins):
            setNodeListMargins(0, 0, 0, 0)
        setNodeListSpacing = getattr(nodeListLayout, "setSpacing", None)
        if callable(setNodeListSpacing):
            setNodeListSpacing(6)
        self.nodeListTitle = QLabel("当前节点")
        setNodeListTitleName = getattr(self.nodeListTitle, "setObjectName", None)
        if callable(setNodeListTitleName):
            setNodeListTitleName("panelTitle")
        nodeListLayout.addWidget(self.nodeListTitle)
        self.nodeListWidget = QListWidget()
        setNodeListName = getattr(self.nodeListWidget, "setObjectName", None)
        if callable(setNodeListName):
            setNodeListName("sidebarNodeList")
        setFixedHeight = getattr(self.nodeListWidget, "setFixedHeight", None)
        if callable(setFixedHeight):
            setFixedHeight(220)
        self.nodeListWidget.itemClicked.connect(self.selectNodeFromSidebar)
        self.nodeListWidget.itemDoubleClicked.connect(self.navigateToSidebarItem)
        nodeListLayout.addWidget(self.nodeListWidget)
        self.nodeListContainer.setLayout(nodeListLayout)
        sideBarLayout.addWidget(self.nodeListContainer)
        addSidebarStretch = getattr(sideBarLayout, "addStretch", None)
        if callable(addSidebarStretch):
            addSidebarStretch(1)
        self.sidebarContainer.setLayout(sideBarLayout)

        self.canvasPanel = QWidget()
        leftPanel = QVBoxLayout()
        setCanvasMargins = getattr(leftPanel, "setContentsMargins", None)
        if callable(setCanvasMargins):
            setCanvasMargins(0, 0, 0, 0)
        setCanvasSpacing = getattr(leftPanel, "setSpacing", None)
        if callable(setCanvasSpacing):
            setCanvasSpacing(8)
        leftPanel.addWidget(QLabel("流程画布"))
        self.workflowTabs = QTabWidget()
        setWorkflowTabsName = getattr(self.workflowTabs, "setObjectName", None)
        if callable(setWorkflowTabsName):
            setWorkflowTabsName("workflowTabs")
        leftPanel.addWidget(self.workflowTabs)
        self.flowScene = FlowScene()
        setSceneRect = getattr(self.flowScene, "setSceneRect", None)
        if callable(setSceneRect):
            setSceneRect(-2000.0, -2000.0, 4000.0, 4000.0)
        selectionChanged = getattr(self.flowScene, "selectionChanged", None)
        if selectionChanged is not None and hasattr(selectionChanged, "connect"):
            selectionChanged.connect(self.onNodeSelectionChanged)
        self.flowScene.setConnectionHandler(self.connectPorts)
        setConnectionErrorHandler = getattr(
            self.flowScene, "setConnectionErrorHandler", None
        )
        if callable(setConnectionErrorHandler):
            setConnectionErrorHandler(self.onConnectionError)
        self.flowScene.setNodeDoubleClickHandler(self.openNodeParamDialog)
        self.flowScene.setCanvasClickHandler(self.collapseOperatorBubble)
        self.flowScene.setOperatorDropHandler(self.addNodeFromOperatorDrop)
        self.flowView = DesignerGraphicsView(self.flowScene)
        setAcceptDrops = getattr(self.flowView, "setAcceptDrops", None)
        if callable(setAcceptDrops):
            setAcceptDrops(True)
        leftPanel.addWidget(self.flowView)
        self.canvasPanel.setLayout(leftPanel)

        self.autoLayoutButton = QPushButton("自动布局")
        self.validateGraphButton = QPushButton("校验流程图")

        self.loadButton = QPushButton("加载项目")
        self.saveProjectButton = QPushButton("保存项目")
        self.validateButton = QPushButton("校验项目")
        self.startButton = QPushButton("开始运行")
        self.stopButton = QPushButton("停止运行")
        self.refreshButton = QPushButton("刷新算子")
        self.openLogsButton = QPushButton("打开日志")
        setPrimary = getattr(self.loadButton, "setObjectName", None)
        if callable(setPrimary):
            setPrimary("primaryButton")
        setStartName = getattr(self.startButton, "setObjectName", None)
        if callable(setStartName):
            setStartName("primaryButton")
        setLogsName = getattr(self.openLogsButton, "setObjectName", None)
        if callable(setLogsName):
            setLogsName("secondaryButton")
        setStopObjectName = getattr(self.stopButton, "setObjectName", None)
        if callable(setStopObjectName):
            setStopObjectName("dangerButton")
        self._addToolbarGroup(
            "项目", [self.loadButton, self.saveProjectButton, self.validateButton]
        )
        self.mainToolbar.addSeparator()
        self._addToolbarGroup("运行", [self.startButton, self.stopButton])
        self.mainToolbar.addSeparator()
        self._addToolbarGroup("编辑", [self.autoLayoutButton, self.validateGraphButton])
        self.mainToolbar.addSeparator()
        self._addToolbarGroup("辅助", [self.refreshButton, self.openLogsButton])

        self.rightPanelContainer = QWidget()
        rightPanel = QVBoxLayout()
        setRightMargins = getattr(rightPanel, "setContentsMargins", None)
        if callable(setRightMargins):
            setRightMargins(0, 0, 0, 0)
        setRightSpacing = getattr(rightPanel, "setSpacing", None)
        if callable(setRightSpacing):
            setRightSpacing(8)

        self.summarySection = QWidget()
        setSummaryName = getattr(self.summarySection, "setObjectName", None)
        if callable(setSummaryName):
            setSummaryName("rightPanelSection")
        summaryLayout = QVBoxLayout()
        setSummaryMargins = getattr(summaryLayout, "setContentsMargins", None)
        if callable(setSummaryMargins):
            setSummaryMargins(0, 0, 0, 0)
        setSummarySpacing = getattr(summaryLayout, "setSpacing", None)
        if callable(setSummarySpacing):
            setSummarySpacing(6)
        statusTitle = QLabel("运行摘要")
        setTitleName = getattr(statusTitle, "setObjectName", None)
        if callable(setTitleName):
            setTitleName("panelTitle")
        summaryLayout.addWidget(statusTitle)

        self.jobStatusCard = QLabel("作业状态：空闲")
        setStatusCardName = getattr(self.jobStatusCard, "setObjectName", None)
        if callable(setStatusCardName):
            setStatusCardName("statusCard")
        summaryLayout.addWidget(self.jobStatusCard)

        self.jobMessageCard = QLabel("消息：-")
        setMessageCardName = getattr(self.jobMessageCard, "setObjectName", None)
        if callable(setMessageCardName):
            setMessageCardName("statusCard")
        summaryLayout.addWidget(self.jobMessageCard)
        self.summarySection.setLayout(summaryLayout)
        rightPanel.addWidget(self.summarySection)

        self.nodeStatusSection = QWidget()
        setNodeSectionName = getattr(self.nodeStatusSection, "setObjectName", None)
        if callable(setNodeSectionName):
            setNodeSectionName("rightPanelSection")
        nodeStatusLayout = QVBoxLayout()
        setNodeStatusMargins = getattr(nodeStatusLayout, "setContentsMargins", None)
        if callable(setNodeStatusMargins):
            setNodeStatusMargins(0, 0, 0, 0)
        setNodeStatusSpacing = getattr(nodeStatusLayout, "setSpacing", None)
        if callable(setNodeStatusSpacing):
            setNodeStatusSpacing(6)
        self.runtimeStatusOutput = QTextEdit()
        self.runtimeStatusOutput.setReadOnly(True)
        nodeStatusTitle = QLabel("当前节点")
        setNodeTitleName = getattr(nodeStatusTitle, "setObjectName", None)
        if callable(setNodeTitleName):
            setNodeTitleName("panelTitle")
        nodeStatusLayout.addWidget(nodeStatusTitle)
        self.nodeDetailTitleCard = QLabel("未选中节点")
        self.nodeDetailMetaCard = QLabel("点击画布节点或左侧当前节点列表查看详情")
        self.nodeDetailPortsCard = QLabel("输入: 无\n输出: 无")
        self.nodeDetailParamsCard = QLabel("参数:\n- 无")
        for detailWidget in [
            self.nodeDetailTitleCard,
            self.nodeDetailMetaCard,
            self.nodeDetailPortsCard,
            self.nodeDetailParamsCard,
        ]:
            setDetailName = getattr(detailWidget, "setObjectName", None)
            if callable(setDetailName):
                setDetailName("statusCard")
            nodeStatusLayout.addWidget(detailWidget)
        self.nodeStatusSection.setLayout(nodeStatusLayout)
        rightPanel.addWidget(self.nodeStatusSection)

        self.previewSection = QWidget()
        setPreviewSectionName = getattr(self.previewSection, "setObjectName", None)
        if callable(setPreviewSectionName):
            setPreviewSectionName("rightPanelSection")
        previewLayout = QVBoxLayout()
        setPreviewMargins = getattr(previewLayout, "setContentsMargins", None)
        if callable(setPreviewMargins):
            setPreviewMargins(0, 0, 0, 0)
        setPreviewSpacing = getattr(previewLayout, "setSpacing", None)
        if callable(setPreviewSpacing):
            setPreviewSpacing(6)
        previewTitle = QLabel("结果预览")
        setPreviewTitleName = getattr(previewTitle, "setObjectName", None)
        if callable(setPreviewTitleName):
            setPreviewTitleName("panelTitle")
        previewLayout.addWidget(previewTitle)

        self.previewImageLabel = QLabel("暂无图片")
        setPreviewLabelName = getattr(self.previewImageLabel, "setObjectName", None)
        if callable(setPreviewLabelName):
            setPreviewLabelName("previewCard")
        setMinHeight = getattr(self.previewImageLabel, "setMinimumHeight", None)
        if callable(setMinHeight):
            setMinHeight(220)
        setAlign = getattr(self.previewImageLabel, "setAlignment", None)
        if callable(setAlign):
            setAlign(Qt.AlignCenter)
        previewLayout.addWidget(self.previewImageLabel)
        self.previewSection.setLayout(previewLayout)
        rightPanel.addWidget(self.previewSection)
        addRightStretch = getattr(rightPanel, "addStretch", None)
        if callable(addRightStretch):
            addRightStretch(1)
        self.rightPanelContainer.setLayout(rightPanel)
        self.mainSplitter = QSplitter(Qt.Horizontal)
        setChildrenCollapsible = getattr(
            self.mainSplitter, "setChildrenCollapsible", None
        )
        if callable(setChildrenCollapsible):
            setChildrenCollapsible(False)
        setHandleWidth = getattr(self.mainSplitter, "setHandleWidth", None)
        if callable(setHandleWidth):
            setHandleWidth(8)
        self.mainSplitter.addWidget(self.sidebarContainer)
        self.mainSplitter.addWidget(self.canvasPanel)
        self.mainSplitter.addWidget(self.rightPanelContainer)
        connectSplitterMoved = getattr(self.mainSplitter, "splitterMoved", None)
        if connectSplitterMoved is not None and hasattr(
            connectSplitterMoved, "connect"
        ):
            connectSplitterMoved.connect(self.onMainSplitterMoved)
        rootLayout.addWidget(self.mainSplitter)
        rootWidget.setLayout(rootLayout)
        self.setCentralWidget(rootWidget)

        self.workflowController = WorkflowController(
            workflowStore=self.workflowStore,
            flowModel=self.flowModel,
            flowScene=self.flowScene,
            refreshSidebarNodeList=self.refreshSidebarNodeList,
            focusGraphContent=self.focusGraphContent,
            updateToolbarState=self.updateToolbarState,
        )
        self.projectController = ProjectController(
            runtimeClient=self.runtimeClient,
            flowModel=self.flowModel,
            flowScene=self.flowScene,
            settingsStore=self.settingsStore,
            appendLog=self.appendRuntimeLog,
            refreshSidebarNodeList=self.refreshSidebarNodeList,
            focusGraphContent=self.focusGraphContent,
            refreshRuntimePanelView=self._refreshRuntimePanelView,
            updateToolbarState=self.updateToolbarState,
            updateRuntimeJobState=lambda status, message: (
                self.runtimePanelState.updateJob(status, message)
            ),
            workflowController=self.workflowController,
        )
        self.runtimeController = RuntimeController(
            runtimeClient=self.runtimeClient,
            runtimePanelState=self.runtimePanelState,
            appendLog=self.appendRuntimeLog,
            refreshRuntimePanelView=self._refreshRuntimePanelView,
            updateToolbarState=self.updateToolbarState,
            syncRuntimeProjectBeforeRun=self._syncRuntimeProjectBeforeRun,
            applyRuntimeEventToNode=self.applyRuntimeEventToNode,
            setCurrentJobId=self._setCurrentJobId,
            setIsJobRunning=self._setIsJobRunning,
            getLoadedProjectPath=lambda: self.loadedProjectPath,
            getCurrentJobId=lambda: self.currentJobId,
            getActiveWorkflowId=lambda: self.activeWorkflowId,
            getEntryWorkflowId=lambda: self.workflowStore.entryWorkflowId,
        )
        self.operatorCatalogController = OperatorCatalogController(
            runtimeClient=self.runtimeClient,
            appendLog=self.appendRuntimeLog,
        )
        self.layoutController = LayoutController(
            mainSplitter=self.mainSplitter,
            sidebarContainer=self.sidebarContainer,
            rightPanelContainer=self.rightPanelContainer,
            canvasPanel=self.canvasPanel,
            nodeListWidget=self.nodeListWidget,
            runtimeStatusOutput=self.runtimeStatusOutput,
            previewImageLabel=self.previewImageLabel,
            sidebarToggleButton=self.sidebarToggleButton,
            categoryPanel=self.categoryPanel,
            nodeListContainer=self.nodeListContainer,
            mainMenuBar=self.mainMenuBar,
            settingsStore=self.settingsStore,
            widthGetter=self.width,
            isSidebarCollapsedGetter=lambda: self.isSidebarCollapsed,
        )

        self.refreshButton.clicked.connect(self.refreshOperators)
        workflowChanged = getattr(self.workflowTabs, "currentChanged", None)
        if workflowChanged is not None and hasattr(workflowChanged, "connect"):
            workflowChanged.connect(self._onWorkflowTabChanged)
        workflowTabClicked = getattr(self.workflowTabs, "tabBarClicked", None)
        if workflowTabClicked is not None and hasattr(workflowTabClicked, "connect"):
            workflowTabClicked.connect(self._onWorkflowTabClicked)
        self.loadButton.clicked.connect(self.loadProject)
        self.saveProjectButton.clicked.connect(self.saveProjectAction)
        self.validateButton.clicked.connect(self.validateProject)
        self.startButton.clicked.connect(self.startJob)
        self.stopButton.clicked.connect(self.stopJob)
        self.openLogsButton.clicked.connect(self.openLogDialog)
        self.autoLayoutButton.clicked.connect(self.autoLayoutNodes)
        self.validateGraphButton.clicked.connect(self.validateGraph)
        self.deleteShortcut = QShortcut(QKeySequence.Delete, self)
        self.deleteShortcut.activated.connect(self.handleDeleteShortcut)
        self.backspaceDeleteShortcut = QShortcut(QKeySequence("Backspace"), self)
        self.backspaceDeleteShortcut.activated.connect(self.handleDeleteShortcut)
        self._buildMainMenuBar()
        self.refreshOperators()
        self.updateToolbarState()
        self._refreshRuntimePanelView()
        self.setActiveCategory("全部")
        self.applySidebarState()
        self.operatorBubble = OperatorBubble()
        self.operatorBubble.setCreateHandler(self.addNodeFromOperatorPayload)
        self.refreshSidebarNodeList()
        self._refreshWorkflowTabs()
        self._layoutMode = "normal"
        self.applyResponsiveLayout()
        self.restoreMainSplitterSizes()

    def triggerStartupProjectEntry(self) -> None:
        if not self._showStartupEntry:
            return
        if self._startupEntryHandled:
            return
        self._startupEntryHandled = True
        self.showStartupProjectEntry()

    def loadProject(self) -> None:
        selectedPath, _ = QFileDialog.getOpenFileName(
            self, "选择项目文件(project.json)", "", "项目文件 (project.json)"
        )
        if selectedPath == "":
            self.appendRuntimeLog("INFO", "已取消加载项目")
            return
        if not self.loadProjectSelection(selectedPath):
            self.appendRuntimeLog("ERROR", "请选择有效的 project.json")

    def saveProjectAction(self) -> None:
        if (
            self.currentProjectDir is not None
            and (self.currentProjectDir / "project.json").exists()
        ):
            ok, projectDir = self.projectController.saveProjectToDirectory(
                str(self.currentProjectDir),
                self.currentProjectDir.name or "project",
                self.loadedProjectPath,
            )
            if ok:
                self.currentProjectDir = cast(Path | None, projectDir)
            return
        selectedDir = self._chooseProjectDirectory("选择项目文件夹")
        if selectedDir == "":
            self.appendRuntimeLog("INFO", "已取消保存项目")
            return
        ok, projectDir = self.projectController.saveProjectToDirectory(
            selectedDir,
            Path(selectedDir).name or "project",
            self.loadedProjectPath,
        )
        if ok:
            self.currentProjectDir = cast(Path | None, projectDir)

    def _chooseProjectDirectory(self, title: str) -> str:
        defaultDir = (
            str(self.currentProjectDir) if self.currentProjectDir is not None else ""
        )
        return str(QFileDialog.getExistingDirectory(self, title, defaultDir))

    def loadProjectSelection(self, selectedPath: str) -> bool:
        projectDir = cast(
            Path | None, self.projectController.resolveProjectDirectory(selectedPath)
        )
        if projectDir is None:
            return False
        return self.loadProjectDirectory(str(projectDir))

    def getRecentProjects(self) -> list[dict[str, str]]:
        return cast(list[dict[str, str]], self.projectController.getRecentProjects())

    def recordRecentProject(self, projectJsonPath: str) -> None:
        self.projectController.recordRecentProject(projectJsonPath)
        self.refreshRecentProjectsMenu()

    def removeRecentProject(self, projectJsonPath: str) -> None:
        self.projectController.removeRecentProject(projectJsonPath)
        self.refreshRecentProjectsMenu()

    def clearRecentProjects(self) -> None:
        self.projectController.clearRecentProjects()
        self.refreshRecentProjectsMenu()

    def saveProjectToDirectory(self, projectDirPath: str) -> bool:
        projectName = (
            Path(projectDirPath).name if Path(projectDirPath).name != "" else "project"
        )
        ok, projectDir = self.projectController.saveProjectToDirectory(
            projectDirPath, projectName, self.loadedProjectPath
        )
        if ok:
            self.currentProjectDir = projectDir
        return bool(ok)

    def loadProjectDirectory(self, projectDirPath: str) -> bool:
        ok, loadedProjectPath, currentProjectDir = (
            self.projectController.loadProjectDirectory(projectDirPath)
        )
        if ok:
            self.loadedProjectPath = loadedProjectPath
            self.currentProjectDir = currentProjectDir
            self.activeWorkflowId = self.workflowController.activeWorkflowId
            self._restoreActiveWorkflowRuntimeState()
            self._refreshWorkflowTabs()
        return bool(ok)

    def refreshSidebarNodeList(self) -> None:
        clearMethod = getattr(self.nodeListWidget, "clear", None)
        if callable(clearMethod):
            clearMethod()
        for node in self.flowModel.nodes.values():
            shortId = node.nodeId[:8]
            item = QListWidgetItem(f"{node.displayName}\n{shortId}")
            setData = getattr(item, "setData", None)
            if callable(setData):
                setData(USER_ROLE, node.nodeId)
            addItem = getattr(self.nodeListWidget, "addItem", None)
            if callable(addItem):
                addItem(item)

    def getSidebarNodeEntries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for node in self.flowModel.nodes.values():
            entries.append(
                {
                    "nodeId": node.nodeId,
                    "displayName": node.displayName,
                    "shortNodeId": node.nodeId[:8],
                }
            )
        return entries

    def getToolbarGroups(self) -> list[list[str]]:
        return [
            ["加载项目", "保存项目", "校验项目"],
            ["开始运行", "停止运行"],
            ["自动布局", "校验流程图"],
            ["刷新算子", "打开日志"],
        ]

    def _addToolbarGroup(self, title: str, buttons: list[QPushButton]) -> None:
        titleLabel = QLabel(title)
        setTitleName = getattr(titleLabel, "setObjectName", None)
        if callable(setTitleName):
            setTitleName("toolbarGroupLabel")
        self.mainToolbar.addWidget(titleLabel)
        for button in buttons:
            self.mainToolbar.addWidget(button)

    def _buildMainMenuBar(self) -> None:
        self._menuBarGroups = ["文件", "运行", "编辑", "视图"]
        addMenu = getattr(self.mainMenuBar, "addMenu", None)
        if not callable(addMenu):
            return

        fileMenu = addMenu("文件")
        self._addMenuAction(fileMenu, "打开项目", self.loadProject)
        self._addMenuAction(fileMenu, "保存项目", self.saveProjectAction)
        self._addMenuAction(fileMenu, "新建工作流", self.createWorkflow)
        self._addMenuAction(fileMenu, "删除当前工作流", self.deleteActiveWorkflow)
        self._addMenuAction(fileMenu, "设置当前为入口", self.setEntryWorkflow)
        self._addMenuAction(fileMenu, "设置工作流接口", self.editWorkflowInterface)
        self._addMenuAction(fileMenu, "添加 Subflow 节点", self.addSubflowNode)
        self._addMenuAction(fileMenu, "添加 Repeat 节点", self.addRepeatNode)
        self._addMenuAction(fileMenu, "添加 ForEach 节点", self.addForEachNode)
        self._addMenuAction(fileMenu, "添加 While 节点", self.addWhileNode)
        addSubMenu = getattr(fileMenu, "addMenu", None)
        recentMenu = (
            addSubMenu("最近项目") if callable(addSubMenu) else QMenu("最近项目")
        )
        self._recentProjectsMenu = recentMenu
        self.refreshRecentProjectsMenu()

        runMenu = addMenu("运行")
        self._addMenuAction(runMenu, "开始运行", self.startJob)
        self._addMenuAction(runMenu, "停止运行", self.stopJob)
        self._addMenuAction(runMenu, "打开日志", self.openLogDialog)

        editMenu = addMenu("编辑")
        self._addMenuAction(editMenu, "自动布局", self.autoLayoutNodes)
        self._addMenuAction(editMenu, "校验流程图", self.validateGraph)

        viewMenu = addMenu("视图")
        self._addMenuAction(viewMenu, "切换侧边栏", self.toggleSidebar)
        self._addMenuAction(viewMenu, "聚焦画布内容", self.focusGraphContent)

    def _addMenuAction(self, menu, title: str, callback) -> None:
        action = QAction(title, self)
        triggered = getattr(action, "triggered", None)
        if triggered is not None and hasattr(triggered, "connect"):
            triggered.connect(callback)
        addAction = getattr(menu, "addAction", None)
        if callable(addAction):
            addAction(action)

    def getMenuBarFontSize(self) -> int:
        return self.layoutController.getMenuBarFontSize()

    def _setCurrentJobId(self, jobId: str | None) -> None:
        self.currentJobId = jobId

    def _setIsJobRunning(self, isRunning: bool) -> None:
        self.isJobRunning = isRunning

    def refreshRecentProjectsMenu(self) -> None:
        recentMenu = getattr(self, "_recentProjectsMenu", None)
        if recentMenu is None:
            return
        clearMethod = getattr(recentMenu, "clear", None)
        if callable(clearMethod):
            clearMethod()
        recentProjects = self.getRecentProjects()
        for item in recentProjects:
            projectName = str(item.get("projectName", "项目"))
            projectPath = str(item.get("projectPath", ""))
            action = QAction(f"{projectName} ({projectPath})", self)
            triggered = getattr(action, "triggered", None)
            if triggered is not None and hasattr(triggered, "connect"):
                triggered.connect(
                    lambda checked=False, path=projectPath: self.openRecentProject(path)
                )
            addAction = getattr(recentMenu, "addAction", None)
            if callable(addAction):
                addAction(action)

    def getMenuBarGroups(self) -> list[str]:
        return list(getattr(self, "_menuBarGroups", ["文件", "运行", "编辑", "视图"]))

    def getRecentProjectsMenuEntries(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.getRecentProjects()]

    def _refreshWorkflowTabs(self) -> None:
        labels = self.workflowController.getWorkflowTabLabels()
        self._workflowTabsUpdating = True
        blockSignals = getattr(self.workflowTabs, "blockSignals", None)
        previousBlock = blockSignals(True) if callable(blockSignals) else None
        try:
            self.workflowTabs.clear()
            for label in labels:
                title = str(label["name"])
                if bool(label.get("isEntry", False)):
                    title = f"{title} [入口]"
                index = self.workflowTabs.addTab(QWidget(), title)
                setTabData = getattr(self.workflowTabs, "setTabData", None)
                if callable(setTabData):
                    setTabData(index, str(label["workflowId"]))
            addTab = getattr(self.workflowTabs, "addTab", None)
            if callable(addTab):
                plusIndex = addTab(QWidget(), "+")
                setTabData = getattr(self.workflowTabs, "setTabData", None)
                if callable(setTabData):
                    setTabData(plusIndex, None)
            activeIndex = 0
            for index, label in enumerate(labels):
                if bool(label.get("isActive", False)):
                    activeIndex = index
                    break
            if labels:
                self.workflowTabs.setCurrentIndex(activeIndex)
        finally:
            if callable(blockSignals) and previousBlock is not None:
                blockSignals(previousBlock)
            self._workflowTabsUpdating = False

    def _onWorkflowTabChanged(self, index: int) -> None:
        if getattr(self, "_workflowTabsUpdating", False):
            return
        tabData = getattr(self.workflowTabs, "tabData", None)
        workflowId = tabData(index) if callable(tabData) and index >= 0 else None
        if not isinstance(workflowId, str) or workflowId == "":
            return
        if workflowId == self.activeWorkflowId:
            return
        self.workflowController.switchWorkflow(workflowId)
        self.activeWorkflowId = workflowId
        self._restoreActiveWorkflowRuntimeState()
        self._refreshWorkflowTabs()

    def _onWorkflowTabClicked(self, index: int) -> None:
        tabData = getattr(self.workflowTabs, "tabData", None)
        workflowId = tabData(index) if callable(tabData) and index >= 0 else None
        if workflowId is None:
            self.createWorkflow()

    def getWorkflowTabs(self) -> list[dict[str, object]]:
        return self.workflowController.getWorkflowTabLabels()

    def getActiveWorkflowId(self) -> str:
        return self.activeWorkflowId

    def createWorkflow(self, name: str = "New Workflow") -> str:
        workflowId = self.workflowController.createWorkflow(name)
        self.activeWorkflowId = workflowId
        self._refreshWorkflowTabs()
        return workflowId

    def renameWorkflow(self, workflowId: str, name: str) -> None:
        self.workflowController.renameWorkflow(workflowId, name)
        self._refreshWorkflowTabs()

    def renameActiveWorkflow(self, name: str) -> None:
        self.renameWorkflow(self.activeWorkflowId, name)

    def deleteActiveWorkflow(self) -> None:
        try:
            self.workflowController.deleteWorkflow(self.activeWorkflowId)
        except (KeyError, ValueError) as err:
            self.appendRuntimeLog("ERROR", f"删除工作流失败：{err}")
            return
        self.activeWorkflowId = self.workflowController.activeWorkflowId
        self._refreshWorkflowTabs()

    def setEntryWorkflow(self, workflowId: str | None = None) -> None:
        selected = workflowId or self.activeWorkflowId
        try:
            self.workflowController.setEntryWorkflow(selected)
        except KeyError as err:
            self.appendRuntimeLog("ERROR", f"入口工作流不存在：{err}")
            return
        self._refreshWorkflowTabs()

    def editWorkflowInterface(
        self,
        inputs: dict[str, object] | None = None,
        outputs: dict[str, object] | None = None,
    ) -> None:
        if inputs is None:
            inputs = self._promptInterfaceMap("输入接口", self.workflowStore.get().inputs)
        if inputs is None:
            return
        if outputs is None:
            outputs = self._promptInterfaceMap("输出接口", self.workflowStore.get().outputs)
        if outputs is None:
            return
        try:
            self.workflowController.setWorkflowInterface(
                self.activeWorkflowId, inputs, outputs
            )
        except (TypeError, ValueError) as err:
            self.appendRuntimeLog("ERROR", f"工作流接口设置失败：{err}")
            return
        self.appendRuntimeLog("INFO", f"工作流接口已更新：{self.activeWorkflowId}")
        self._refreshWorkflowTabs()

    def _promptInterfaceMap(
        self, label: str, current: dict[str, object]
    ) -> dict[str, object] | None:
        text, accepted = QInputDialog.getText(
            self,
            "设置工作流接口",
            f"{label} JSON",
            text=json.dumps(current, ensure_ascii=True),
        )
        if not accepted:
            return None
        try:
            parsed = json.loads(str(text))
        except json.JSONDecodeError as err:
            self.appendRuntimeLog("ERROR", f"{label} JSON 无效：{err}")
            return None
        if not isinstance(parsed, dict):
            self.appendRuntimeLog("ERROR", f"{label} 必须是 JSON 对象")
            return None
        return parsed

    def addSubflowNode(self, targetWorkflowId: str | None = None) -> str | None:
        target = targetWorkflowId or self._firstOtherWorkflow()
        if target is None:
            self.appendRuntimeLog("WARN", "请先创建一个可调用的工作流")
            return None
        nodeId = self.flowModel.addNode("", "Subflow", {}, {}, kind="subflow")
        try:
            self.workflowController.configureSubflowNode(nodeId, target)
        except (KeyError, ValueError) as err:
            self.flowModel.removeNode(nodeId)
            self.appendRuntimeLog("ERROR", f"Subflow 节点创建失败：{err}")
            return None
        self._addControlNodeToScene(nodeId)
        return nodeId

    def addRepeatNode(self, config: dict[str, object] | None = None) -> str | None:
        target = self._firstOtherWorkflow()
        if target is None:
            self.appendRuntimeLog("WARN", "请先创建 Repeat 的 body 工作流")
            return None
        loopConfig = config or {
            "mode": "repeat",
            "bodyWorkflowId": target,
            "repeatCount": 1,
            "maxIterations": 1,
            "timeoutMs": 0,
        }
        return self._addLoopNode(loopConfig, "Repeat")

    def addForEachNode(self, config: dict[str, object] | None = None) -> str | None:
        target = self._firstOtherWorkflow()
        if target is None:
            self.appendRuntimeLog("WARN", "请先创建 ForEach 的 body 工作流")
            return None
        loopConfig = config or {
            "mode": "foreach",
            "bodyWorkflowId": target,
            "maxIterations": 100,
            "timeoutMs": 0,
        }
        return self._addLoopNode(loopConfig, "ForEach")

    def addWhileNode(self, config: dict[str, object] | None = None) -> str | None:
        targets = self._otherWorkflowIds()
        if config is None:
            if len(targets) < 2:
                self.appendRuntimeLog("WARN", "While 需要独立的 condition 和 body 工作流")
                return None
            config = {
                "mode": "while",
                "conditionWorkflowId": targets[0],
                "bodyWorkflowId": targets[1],
                "maxIterations": 100,
                "timeoutMs": 30000,
            }
        return self._addLoopNode(config, "While")

    def _addLoopNode(self, config: dict[str, object], title: str) -> str | None:
        nodeId = self.flowModel.addNode("", title, {}, {}, kind="loop")
        try:
            self.workflowController.configureLoopNode(nodeId, config)
        except (KeyError, ValueError) as err:
            self.flowModel.removeNode(nodeId)
            self.appendRuntimeLog("ERROR", f"{title} 节点创建失败：{err}")
            return None
        self._addControlNodeToScene(nodeId)
        return nodeId

    def _otherWorkflowIds(self) -> list[str]:
        return [
            workflow.workflowId
            for workflow in self.workflowStore.listWorkflows()
            if workflow.workflowId != self.activeWorkflowId
        ]

    def _firstOtherWorkflow(self) -> str | None:
        values = self._otherWorkflowIds()
        return values[0] if values else None

    def _addControlNodeToScene(self, nodeId: str) -> None:
        node = self.flowModel.nodes[nodeId]
        nodeIndex = len(self.flowModel.nodes) - 1
        self.flowScene.addFlowNode(
            FlowNodeViewModel(
                nodeId=nodeId,
                title=node.displayName,
                x=float(20 + (nodeIndex % 4) * 220),
                y=float(20 + (nodeIndex // 4) * 120),
                inputPorts=node.inputPorts,
                outputPorts=node.outputPorts,
                operatorId=node.operatorId,
            )
        )
        self.flowModel.selectNode(nodeId)
        self.flowScene.setNodeSelected(nodeId)
        self.refreshSidebarNodeList()
        self.onNodeSelectionChanged()
        self.updateToolbarState()

    def openRecentProject(self, projectPath: str) -> bool:
        return self.loadProjectSelection(projectPath)

    def getPanelWidths(self) -> dict[str, int]:
        return self.layoutController.getPanelWidths()

    def getPanelConstraints(self) -> dict[str, int]:
        return self.layoutController.getPanelConstraints()

    def getMainSplitterSizes(self) -> list[int]:
        return self.layoutController.getMainSplitterSizes()

    def saveMainSplitterSizes(self, sizes: list[int]) -> None:
        self.layoutController.saveMainSplitterSizes(sizes)

    def restoreMainSplitterSizes(self) -> None:
        self.layoutController.restoreMainSplitterSizes()

    def onMainSplitterMoved(self, pos: int, index: int) -> None:
        self.layoutController.onMainSplitterMoved(pos, index)

    def getLayoutMode(self) -> str:
        return self.layoutController.getLayoutMode()

    def applyResponsiveLayout(self) -> None:
        self.layoutController.applyResponsiveLayout()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self.applyResponsiveLayout()
        try:
            super().resizeEvent(event)
        except Exception:
            _ = event

    def getCategoryButtonLabels(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        for categoryName, button in self.categoryButtons.items():
            textMethod = getattr(button, "text", None)
            if callable(textMethod):
                labels[categoryName] = str(textMethod())
                continue
            labels[categoryName] = (
                f"{self._getCategoryGlyph(categoryName)} {categoryName}"
            )
        return labels

    def getRightPanelSections(self) -> list[str]:
        return ["运行摘要", "当前节点", "结果预览"]

    def getCurrentNodeSummary(self) -> str:
        return self.nodeDetailsPresenter.buildSummary()

    def getCurrentNodeDetailViewModel(self) -> dict[str, object]:
        return self.nodeDetailsPresenter.buildModel()

    def setCurrentNodeRuntimeState(self, nodeId: str, status: str, branch: str) -> None:
        self._nodeRuntimeState[nodeId] = {
            "status": status,
            "branch": branch,
            "workflowId": self.activeWorkflowId,
        }
        setNodeRuntimeState = getattr(self.flowScene, "setNodeRuntimeState", None)
        if callable(setNodeRuntimeState):
            setNodeRuntimeState(nodeId, status)
        self._refreshRuntimePanelView()

    def applyRuntimeEventToNode(self, event: dict[str, object]) -> None:
        nodeIdRaw = event.get("nodeId", "")
        payloadRaw = event.get("payload", {})
        if not isinstance(nodeIdRaw, str) or nodeIdRaw == "":
            return
        if not isinstance(payloadRaw, dict):
            return
        statusRaw = payloadRaw.get("status", "")
        branchRaw = payloadRaw.get("branch", "")
        eventType = event.get("eventType")
        if not isinstance(statusRaw, str) or statusRaw == "":
            statusRaw = {
                "node.started": "RUNNING",
                "node.completed": "COMPLETED",
                "node.failed": "FAILED",
                "node.skipped": "SKIPPED",
            }.get(eventType if isinstance(eventType, str) else "", "")
        status = statusRaw if isinstance(statusRaw, str) else ""
        branch = branchRaw if isinstance(branchRaw, str) else ""
        if status == "":
            return
        workflowRunIdRaw = event.get("workflowRunId", "")
        workflowRunId = workflowRunIdRaw if isinstance(workflowRunIdRaw, str) else ""
        workflowIdRaw = event.get("workflowId", "")
        workflowId = workflowIdRaw if isinstance(workflowIdRaw, str) else ""
        jobIdRaw = event.get("jobId", "")
        jobId = jobIdRaw if isinstance(jobIdRaw, str) else ""
        info: dict[str, object] = {
            "status": status,
            "branch": branch,
            "jobId": jobId,
            "workflowId": workflowId,
            "workflowRunId": workflowRunId,
            "parentWorkflowRunId": event.get("parentWorkflowRunId", ""),
            "nodeRunId": event.get("nodeRunId", ""),
            "iterationPath": event.get("iterationPath", ()),
            "sequence": event.get("sequence", 0),
        }
        if workflowRunId:
            self._nodeRuntimeStateByRun[(workflowRunId, nodeIdRaw)] = dict(info)
            self._nodeRuntimeStateByWorkflowRun[(workflowId, workflowRunId, nodeIdRaw)] = dict(info)
        if workflowId == self.activeWorkflowId or (
            workflowId == ""
            and (jobId == "" or jobId == self.currentJobId)
        ):
            self._nodeRuntimeState[nodeIdRaw] = info
            setNodeRuntimeState = getattr(self.flowScene, "setNodeRuntimeState", None)
            if callable(setNodeRuntimeState):
                setNodeRuntimeState(nodeIdRaw, status)
            self._refreshRuntimePanelView()

    def _restoreActiveWorkflowRuntimeState(self) -> None:
        for nodeId in self.flowModel.nodes:
            candidates = [
                value
                for (workflowId, _runId, candidateNodeId), value in self._nodeRuntimeStateByWorkflowRun.items()
                if workflowId == self.activeWorkflowId and candidateNodeId == nodeId
                and (
                    self.currentJobId is None
                    or value.get("jobId", "") in ("", self.currentJobId)
                )
            ]
            if not candidates:
                self._nodeRuntimeState.pop(nodeId, None)
                continue
            latest = max(candidates, key=_runtimeSequence)
            self._nodeRuntimeState[nodeId] = dict(latest)
            setNodeRuntimeState = getattr(self.flowScene, "setNodeRuntimeState", None)
            if callable(setNodeRuntimeState):
                setNodeRuntimeState(nodeId, str(latest.get("status", "IDLE")))

    def getToolbarGroupNames(self) -> list[str]:
        return ["项目", "运行", "编辑", "辅助"]

    def selectNodeFromSidebar(self, item) -> None:
        getData = getattr(item, "data", None)
        if not callable(getData):
            return
        nodeIdRaw = getData(USER_ROLE)
        if not isinstance(nodeIdRaw, str):
            return
        self.flowModel.selectNode(nodeIdRaw)
        self.flowScene.setNodeSelected(nodeIdRaw)
        self.onNodeSelectionChanged()

    def navigateToSidebarItem(self, item) -> None:
        getData = getattr(item, "data", None)
        if not callable(getData):
            return
        nodeIdRaw = getData(USER_ROLE)
        if not isinstance(nodeIdRaw, str):
            return
        self.navigateToNodeFromSidebar(nodeIdRaw)

    def navigateToNodeFromSidebar(self, nodeId: str) -> None:
        self.flowModel.selectNode(nodeId)
        self.flowScene.setNodeSelected(nodeId)
        nodeCenter = self.flowScene.getNodeCenter(nodeId)
        if nodeCenter is not None:
            centerOn = getattr(self.flowView, "centerOn", None)
            if callable(centerOn):
                centerOn(nodeCenter[0], nodeCenter[1])
        self.onNodeSelectionChanged()

    def _buildProjectPayload(self, projectName: str) -> dict[str, object]:
        if hasattr(self, "workflowController"):
            return self.workflowController.buildPayload(projectName)
        graphPayload = self.flowModel.toProjectGraph()
        nodePositions = self.flowScene.getNodePositions()

        rawNodes = graphPayload.get("nodes", [])
        nodes = rawNodes if isinstance(rawNodes, list) else []
        patchedNodes: list[dict[str, object]] = []
        for rawNode in nodes:
            if not isinstance(rawNode, dict):
                continue
            node = dict(rawNode)
            nodeIdRaw = node.get("nodeId", "")
            nodeId = nodeIdRaw if isinstance(nodeIdRaw, str) else ""
            if nodeId in nodePositions:
                posX, posY = nodePositions[nodeId]
                node["x"] = float(posX)
                node["y"] = float(posY)
            else:
                node["x"] = float(node.get("x", 20.0))
                node["y"] = float(node.get("y", 20.0))
            patchedNodes.append(node)

        edgesRaw = graphPayload.get("edges", [])
        edges = edgesRaw if isinstance(edgesRaw, list) else []

        return {
            "version": "1.0",
            "meta": {
                "name": projectName,
            },
            "runtime": {
                "sourceImagePath": ""
                if self.loadedProjectPath is None
                else self.loadedProjectPath,
            },
            "designer": {
                "nodes": patchedNodes,
                "edges": edges,
            },
        }

    def _restoreProjectPayload(self, payload: dict[str, object]) -> None:
        if hasattr(self, "workflowController"):
            self.workflowController.loadPayload(payload)
            self.activeWorkflowId = self.workflowController.activeWorkflowId
            self._restoreActiveWorkflowRuntimeState()
            self._refreshWorkflowTabs()
            return
        designerRaw = payload.get("designer", {})
        designer = designerRaw if isinstance(designerRaw, dict) else {}
        graphPayload = {
            "nodes": designer.get("nodes", []),
            "edges": designer.get("edges", []),
        }
        self.flowModel.loadProjectGraph(graphPayload)
        self.flowScene.clearGraph()

        for node in self.flowModel.nodes.values():
            xValue = 20.0
            yValue = 20.0
            for rawNode in graphPayload.get("nodes", []):
                if not isinstance(rawNode, dict):
                    continue
                rawNodeId = rawNode.get("nodeId")
                if not isinstance(rawNodeId, str) or rawNodeId != node.nodeId:
                    continue
                xRaw = rawNode.get("x", 20.0)
                yRaw = rawNode.get("y", 20.0)
                if isinstance(xRaw, (int, float)):
                    xValue = float(xRaw)
                if isinstance(yRaw, (int, float)):
                    yValue = float(yRaw)
                break

            self.flowScene.addFlowNode(
                FlowNodeViewModel(
                    nodeId=node.nodeId,
                    title=node.displayName,
                    x=xValue,
                    y=yValue,
                    inputPorts=node.inputPorts,
                    outputPorts=node.outputPorts,
                    operatorId=node.operatorId,
                )
            )

        for edge in self.flowModel.edges:
            self.flowScene.renderEdge(
                FlowEdgeViewModel(
                    fromNodeId=edge.fromNode,
                    fromPort=edge.fromPort,
                    toNodeId=edge.toNode,
                    toPort=edge.toPort,
                )
            )
        self.refreshSidebarNodeList()
        self.focusGraphContent()
        self.updateToolbarState()

    def _resolveProjectDirectory(self, selectedPath: str) -> Path | None:
        pathObj = Path(selectedPath)
        if pathObj.is_dir() and (pathObj / "project.json").exists():
            return pathObj
        if pathObj.is_file() and pathObj.name.lower() == "project.json":
            return pathObj.parent
        return None

    def loadProjectFromPath(
        self,
        projectPath: str,
        successMessagePrefix: str,
        failedMessagePrefix: str,
    ) -> bool:
        reply = self.runtimeClient.loadProject(projectPath)
        if getattr(reply, "ok", False):
            self.loadedProjectPath = projectPath
            self.appendRuntimeLog("INFO", f"{successMessagePrefix}：{projectPath}")
            self.runtimePanelState.updateJob("READY", f"当前项目：{projectPath}")
            self._refreshRuntimePanelView()
            self.updateToolbarState()
            return True
        self.appendRuntimeLog(
            "ERROR", f"{failedMessagePrefix}：{getattr(reply, 'message', '未知错误')}"
        )
        return False

    def showStartupProjectEntry(self) -> bool:
        ok, loadedProjectPath, currentProjectDir = (
            self.projectController.handleStartupProjectEntry(
                self,
                self._chooseProjectDirectory,
                self._projectEntryDialogFactory,
            )
        )
        if ok:
            self.loadedProjectPath = loadedProjectPath
            self.currentProjectDir = currentProjectDir
        return bool(ok)

    def validateProject(self) -> None:
        if self.loadedProjectPath is None:
            self.appendRuntimeLog("WARN", "尚未加载项目")
            return
        reply = self.runtimeClient.validateProject(self.loadedProjectPath)
        if getattr(reply, "ok", False):
            self.appendRuntimeLog("INFO", "项目校验通过")
            self.updateToolbarState()
            return
        errors = getattr(reply, "errors", [])
        self.appendRuntimeLog("ERROR", f"项目校验失败：{list(errors)}")

    def startJob(self) -> None:
        self.runtimeController.startJob()

    def stopJob(self) -> None:
        self.runtimeController.stopJob()

    def _syncRuntimeProjectBeforeRun(self) -> bool:
        if self.currentProjectDir is None:
            return True
        if not self.saveProjectToDirectory(str(self.currentProjectDir)):
            return False
        return self.loadProjectFromPath(
            str(self.currentProjectDir),
            successMessagePrefix="项目已同步",
            failedMessagePrefix="项目同步失败",
        )

    def refreshOperators(self) -> None:
        self.operatorCatalog = self.operatorCatalogController.refreshOperators(
            self._classifyOperator
        )
        self._refreshBubbleOperators()
        self.updateToolbarState()

    def addNodeFromOperatorPayload(
        self,
        payload: dict[str, object],
        sceneX: float | None = None,
        sceneY: float | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            self.appendRuntimeLog("ERROR", "算子数据无效")
            return

        operatorId = str(payload.get("operatorId", "unknown"))
        displayName = str(payload.get("displayName", operatorId))
        inputPortsRaw = payload.get("inputPorts", {})
        outputPortsRaw = payload.get("outputPorts", {})
        paramSchemaRaw = payload.get("paramSchema", {})
        inputPorts = inputPortsRaw if isinstance(inputPortsRaw, dict) else {}
        outputPorts = outputPortsRaw if isinstance(outputPortsRaw, dict) else {}
        paramSchema = paramSchemaRaw if isinstance(paramSchemaRaw, dict) else {}
        nodeId = self.flowModel.addNode(
            operatorId=operatorId,
            displayName=displayName,
            inputPorts=inputPorts,
            outputPorts=outputPorts,
            paramSchema=paramSchema,
        )
        nodeIndex = len(self.flowModel.nodes) - 1
        x = float(20 + (nodeIndex % 4) * 220) if sceneX is None else float(sceneX)
        y = float(20 + (nodeIndex // 4) * 120) if sceneY is None else float(sceneY)
        self.flowScene.addFlowNode(
            FlowNodeViewModel(
                nodeId=nodeId,
                title=displayName,
                x=x,
                y=y,
                inputPorts=inputPorts,
                outputPorts=outputPorts,
                operatorId=operatorId,
            )
        )
        self.appendRuntimeLog("INFO", f"已添加节点：{nodeId}")
        self._recordRecentOperator(operatorId)
        self.flowModel.selectNode(nodeId)
        self.flowScene.setNodeSelected(nodeId)
        self.onNodeSelectionChanged()
        self.refreshSidebarNodeList()
        self.focusGraphContent()
        self.updateToolbarState()
        self.collapseOperatorBubble()

    def addNodeFromOperatorDrop(
        self, payload: dict[str, object], sceneX: float, sceneY: float
    ) -> None:
        self.addNodeFromOperatorPayload(payload, sceneX=sceneX, sceneY=sceneY)

    def addNodeFromOperator(self, item) -> None:
        payload = item.data(USER_ROLE)
        if isinstance(payload, dict):
            self.addNodeFromOperatorPayload(payload)

    def onNodeSelectionChanged(self) -> None:
        try:
            nodeId = self.flowScene.getSelectedNodeId()
        except RuntimeError:
            return
        if nodeId is None:
            self.flowModel.selectNode(None)
            self._refreshRuntimePanelView()
            self.updateToolbarState()
            return
        self.flowModel.selectNode(nodeId)
        self._appendNodeHints(nodeId)
        self._refreshRuntimePanelView()
        self.updateToolbarState()

    def connectSelectedNodes(self) -> None:
        selectedNodeIds = self.flowScene.getSelectedNodeIds()
        if len(selectedNodeIds) != 2:
            self.appendRuntimeLog("WARN", "请恰好选择 2 个节点")
            return

        sourceNodeId = selectedNodeIds[0]
        targetNodeId = selectedNodeIds[1]
        try:
            edge = self.flowModel.connectNodesByDefaultPorts(sourceNodeId, targetNodeId)
        except ValueError as err:
            self.appendRuntimeLog("ERROR", str(err))
            return
        edgeViewModel = FlowEdgeViewModel(
            fromNodeId=edge.fromNode,
            fromPort=edge.fromPort,
            toNodeId=edge.toNode,
            toPort=edge.toPort,
        )
        self.flowScene.renderEdge(edgeViewModel)
        self.appendRuntimeLog(
            "INFO",
            f"连线成功：{edge.fromNode}.{edge.fromPort} -> {edge.toNode}.{edge.toPort}",
        )

    def connectPorts(
        self, fromNodeId: str, fromPort: str, toNodeId: str, toPort: str
    ) -> FlowEdgeViewModel | None:
        try:
            edge = self.flowModel.connectNodes(
                fromNode=fromNodeId,
                fromPort=fromPort,
                toNode=toNodeId,
                toPort=toPort,
                replaceInputPort=True,
            )
        except ValueError as err:
            self.appendRuntimeLog("ERROR", self._formatConnectionError(str(err)))
            return None

        edgeViewModel = FlowEdgeViewModel(
            fromNodeId=edge.fromNode,
            fromPort=edge.fromPort,
            toNodeId=edge.toNode,
            toPort=edge.toPort,
        )
        self.appendRuntimeLog(
            "INFO",
            f"连线成功：{edge.fromNode}.{edge.fromPort} -> {edge.toNode}.{edge.toPort}",
        )
        return edgeViewModel

    def validateGraph(self) -> None:
        result = self.flowModel.validateGraph()
        if result.get("ok") is True:
            self.appendRuntimeLog("INFO", "流程图校验通过")
            return
        rawErrors = result.get("errors", [])
        errors = rawErrors if isinstance(rawErrors, list) else []
        self.appendRuntimeLog("ERROR", f"流程图校验失败（{len(errors)} 个错误）")
        for errorText in errors:
            self.appendRuntimeLog("ERROR", f"  - {errorText}")

    def deleteSelectedElements(self) -> None:
        selectedEdgeKeys = self.flowScene.getSelectedEdgeKeys()
        for fromNode, fromPort, toNode, toPort in selectedEdgeKeys:
            self.flowModel.removeEdge(fromNode, fromPort, toNode, toPort)
            self.flowScene.removeFlowEdge(fromNode, fromPort, toNode, toPort)

        selectedNodeIds = self.flowScene.getSelectedNodeIds()
        for nodeId in selectedNodeIds:
            self.flowModel.removeNode(nodeId)
            self.flowScene.removeFlowNode(nodeId)
            if self.activeParamNodeId == nodeId and self.nodeParamDialog is not None:
                self.nodeParamDialog.close()
                self.nodeParamDialog = None
                self.activeParamNodeId = None

        deletedCount = len(selectedEdgeKeys) + len(selectedNodeIds)
        if deletedCount > 0:
            self.appendRuntimeLog("INFO", f"已删除选中元素：{deletedCount}")
            self.refreshSidebarNodeList()
            self._refreshRuntimePanelView()
        self.updateToolbarState()

    def handleDeleteShortcut(self) -> None:
        self.deleteSelectedElements()

    def autoLayoutNodes(self) -> None:
        self.flowScene.layoutNodesGrid(columns=4)
        self.focusGraphContent()
        self.appendRuntimeLog("INFO", "已应用自动布局")

    def focusGraphContent(self) -> None:
        getContentBounds = getattr(self.flowScene, "getContentBounds", None)
        if not callable(getContentBounds):
            return
        bounds = getContentBounds()
        if not isinstance(bounds, tuple) or len(bounds) != 4:
            return
        fitInView = getattr(self.flowView, "fitInView", None)
        if callable(fitInView):
            resetTransform = getattr(self.flowView, "resetTransform", None)
            if callable(resetTransform):
                resetTransform()
            fitInView(bounds[0], bounds[1], bounds[2], bounds[3], Qt.KeepAspectRatio)
            if self._layoutMode == "large":
                scaleMethod = getattr(self.flowView, "scale", None)
                if callable(scaleMethod):
                    scaleMethod(1.12, 1.12)

    def _appendNodeHints(self, nodeId: str) -> None:
        node = self.flowModel.nodes.get(nodeId)
        if node is None:
            return

        inputSummary = ", ".join(
            [f"{name}:{portType}" for name, portType in node.inputPorts.items()]
        )
        outputSummary = ", ".join(
            [f"{name}:{portType}" for name, portType in node.outputPorts.items()]
        )
        requiredRaw = (
            node.paramSchema.get("required", [])
            if isinstance(node.paramSchema, dict)
            else []
        )
        requiredParams = (
            [name for name in requiredRaw if isinstance(name, str)]
            if isinstance(requiredRaw, list)
            else []
        )
        requiredSummary = ", ".join(requiredParams)

        self.appendRuntimeLog(
            "INFO",
            f"节点提示 {nodeId}：输入[{inputSummary}] 输出[{outputSummary}] 必填[{requiredSummary}]",
        )

    def _refreshRuntimePanelView(self) -> None:
        statusMap = {
            "IDLE": "空闲",
            "READY": "就绪",
            "RUNNING": "运行中",
            "STARTING": "启动中",
            "STOPPING": "停止中",
            "COMPLETED": "已完成",
            "FAILED": "失败",
            "ABORTED": "已中止",
            "UNKNOWN": "未知",
        }
        statusText = statusMap.get(
            self.runtimePanelState.jobStatus, self.runtimePanelState.jobStatus
        )
        self.jobStatusCard.setText(f"作业状态：{statusText}")
        messageText = (
            self.runtimePanelState.lastMessage
            if self.runtimePanelState.lastMessage != ""
            else "-"
        )
        self.jobMessageCard.setText(f"消息：{messageText}")

        self._refreshNodeDetailsView()
        self._refreshPreviewImage()

    def _refreshNodeDetailsView(self) -> None:
        detailModel = self.getCurrentNodeDetailViewModel()
        self.runtimeStatusOutput.setPlainText("")
        state = str(detailModel.get("state", "empty"))
        if state == "empty":
            message = str(detailModel.get("message", "未选中节点"))
            self.nodeDetailTitleCard.setText("未选中节点")
            self.nodeDetailMetaCard.setText(message)
            self.nodeDetailPortsCard.setText("输入: 无\n输出: 无")
            self.nodeDetailParamsCard.setText("参数:\n- 无")
            return

        title = str(detailModel.get("title", ""))
        nodeId = str(detailModel.get("nodeId", ""))
        operatorId = str(detailModel.get("operatorId", ""))
        inputs = str(detailModel.get("inputs", "无"))
        outputs = str(detailModel.get("outputs", "无"))
        required = str(detailModel.get("required", "无"))
        missing = str(detailModel.get("missing", "无"))
        runtimeStatus = str(detailModel.get("runtimeStatus", "-"))
        branch = str(detailModel.get("branch", "-"))
        paramsRaw = detailModel.get("params", [])
        params = paramsRaw if isinstance(paramsRaw, list) else []

        self.nodeDetailTitleCard.setText(title)
        self.nodeDetailMetaCard.setText(
            f"ID: {nodeId}\n算子: {operatorId}\n运行状态: {runtimeStatus}\n分支命中: {branch}"
        )
        self.nodeDetailPortsCard.setText(
            f"输入: {inputs}\n输出: {outputs}\n必填: {required}\n未填: {missing}"
        )
        self.nodeDetailParamsCard.setText(
            "参数:\n" + "\n".join([str(item) for item in params])
        )

    def _refreshPreviewImage(self) -> None:
        imagePath = self.runtimePanelState.latestImagePath
        if imagePath is None:
            self.previewImageLabel.setText("暂无图片")
            return
        if not Path(imagePath).exists():
            self.previewImageLabel.setText(f"图片不存在\n{imagePath}")
            return

        pixmap = QPixmap(imagePath)
        if getattr(pixmap, "isNull", lambda: True)():
            self.previewImageLabel.setText(f"预览加载失败\n{imagePath}")
            return

        scaledPixmap = pixmap.scaled(
            360, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.previewImageLabel.setPixmap(scaledPixmap)

    def openLogDialog(self) -> None:
        if self.logDialog is None:
            self.logDialog = LogDialog()
            self.logDialog.setLogs(self.logBuffer)
        self.logDialog.show()
        raiseWindow = getattr(self.logDialog, "raise_", None)
        if callable(raiseWindow):
            raiseWindow()
        activateWindow = getattr(self.logDialog, "activateWindow", None)
        if callable(activateWindow):
            activateWindow()

    def appendRuntimeLog(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        logLine = f"[{timestamp}][{level}] {message}"
        self.logBuffer.append(logLine)
        if self.logDialog is not None:
            self.logDialog.appendLog(logLine)

    def updateToolbarState(self) -> None:
        canRun = self.loadedProjectPath is not None and not self.isJobRunning
        canStop = self.isJobRunning and self.currentJobId is not None

        self.startButton.setEnabled(canRun)
        self.stopButton.setEnabled(canStop)
        self.validateButton.setEnabled(self.loadedProjectPath is not None)
        self._updateRunBlockedHint(canRun)

    def _updateRunBlockedHint(self, canRun: bool) -> None:
        blockedReason = ""
        if self.loadedProjectPath is None:
            blockedReason = "无法运行：未加载项目，请先打开或新建项目"
        elif self.isJobRunning:
            blockedReason = "无法运行：当前作业仍在运行，请先停止作业"

        setToolTip = getattr(self.startButton, "setToolTip", None)
        if callable(setToolTip):
            setToolTip("" if canRun else blockedReason)

        if blockedReason != "" and blockedReason != self._lastRunBlockedReason:
            self.appendRuntimeLog("WARN", blockedReason)
        if canRun:
            self._lastRunBlockedReason = ""
            return
        self._lastRunBlockedReason = blockedReason

    def setActiveCategory(self, categoryName: str) -> None:
        self.activeOperatorCategory = categoryName
        for currentName, button in self.categoryButtons.items():
            setChecked = getattr(button, "setChecked", None)
            if callable(setChecked):
                setChecked(currentName == categoryName)
            setObjectName = getattr(button, "setObjectName", None)
            if callable(setObjectName):
                setObjectName(
                    "sideCategoryButtonActive"
                    if currentName == categoryName
                    else "sideCategoryButton"
                )
        self._refreshBubbleOperators()

    def toggleCategoryDrawer(self, categoryName: str) -> None:
        if self.isSidebarCollapsed:
            self.expandSidebar()
        if (
            self.activeOperatorCategory == categoryName
            and self.isOperatorBubbleVisible()
        ):
            self.collapseOperatorBubble()
            return
        keepPosition = self.isOperatorBubbleVisible()
        self.setActiveCategory(categoryName)
        self.expandOperatorBubble(categoryName, keepPosition=keepPosition)

    def _getOperatorsByCategory(self) -> list[dict[str, object]]:
        return self.operatorCatalogController.getOperatorsByCategory(
            self.activeOperatorCategory
        )

    def _buildCategoryClickHandler(self, categoryName: str):
        def handler() -> None:
            self.toggleCategoryDrawer(categoryName)

        return handler

    def expandOperatorBubble(
        self, categoryName: str, keepPosition: bool = False
    ) -> None:
        if self.operatorBubble is None:
            return
        button = self.categoryButtons.get(categoryName)
        if button is None:
            return
        self.operatorBubble.setOperators(self._getOperatorsByCategory())
        setRecentOperatorIds = getattr(
            self.operatorBubble, "setRecentOperatorIds", None
        )
        if callable(setRecentOperatorIds):
            setRecentOperatorIds(self.recentOperatorIds)
        self.operatorBubble.showAt(button, keepPosition=keepPosition)

    def collapseOperatorBubble(self) -> None:
        if self.operatorBubble is None:
            return
        self.operatorBubble.close()

    def toggleSidebar(self) -> None:
        if self.isSidebarCollapsed:
            self.expandSidebar()
            return
        self.collapseSidebar()

    def expandSidebar(self) -> None:
        self.isSidebarCollapsed = False
        self.applySidebarState()

    def collapseSidebar(self) -> None:
        self.isSidebarCollapsed = True
        self.collapseOperatorBubble()
        currentSizes = self.layoutController.getMainSplitterSizes()
        if len(currentSizes) != 3:
            currentSizes = self.getMainSplitterSizes()
        sidebarCollapsedWidth = 36
        if len(currentSizes) == 3 and currentSizes[0] > sidebarCollapsedWidth:
            self.layoutController._expandedSplitterSizes = list(currentSizes)
        self.applySidebarState()

    def applySidebarState(self) -> None:
        self.layoutController.applySidebarState()

    def _getSidebarExpandedWidth(self) -> int:
        return self.layoutController.getPanelWidths()["sidebar"]

    def isOperatorBubbleVisible(self) -> bool:
        if self.operatorBubble is None:
            return False
        isVisible = getattr(self.operatorBubble, "isVisible", None)
        if callable(isVisible):
            return bool(isVisible())
        return False

    def _refreshBubbleOperators(self) -> None:
        if self.operatorBubble is None:
            return
        if not self.isOperatorBubbleVisible():
            return
        setRecentOperatorIds = getattr(
            self.operatorBubble, "setRecentOperatorIds", None
        )
        if callable(setRecentOperatorIds):
            setRecentOperatorIds(self.recentOperatorIds)
        self.operatorBubble.setOperators(self._getOperatorsByCategory())

    def onConnectionError(self, reason: str) -> None:
        if reason == "":
            return
        self.appendRuntimeLog("WARN", self._formatConnectionError(reason))

    def _formatConnectionError(self, rawReason: str) -> str:
        if rawReason.startswith("port type mismatch:"):
            return rawReason.replace("port type mismatch:", "端口类型不匹配：")
        if rawReason.startswith("unknown source port:"):
            return rawReason.replace("unknown source port:", "无效输出端口：")
        if rawReason.startswith("unknown target port:"):
            return rawReason.replace("unknown target port:", "无效输入端口：")
        if rawReason.startswith("connectNodes references unknown node"):
            return "连线失败：节点不存在"
        return rawReason

    def _recordRecentOperator(self, operatorId: str) -> None:
        if operatorId == "":
            return
        updated = [
            current for current in self.recentOperatorIds if current != operatorId
        ]
        updated.insert(0, operatorId)
        self.recentOperatorIds = updated[: self.maxRecentOperators]

    def _classifyOperator(self, operatorId: str) -> str:
        if operatorId.startswith("vision.edge."):
            return "检测"
        if operatorId.startswith("vision.pre.") or operatorId.startswith(
            "vision.input."
        ):
            return "预处理"
        if operatorId.startswith("vision.measure."):
            return "测量"
        if operatorId.startswith("vision.output."):
            return "输出"
        if operatorId.startswith("vision.flow."):
            return "控制流"
        if operatorId.startswith("vision.demo."):
            return "预处理"
        return "其他"

    def _getCategoryGlyph(self, categoryName: str) -> str:
        if categoryName == "全部":
            return getOperatorGlyph("default")
        if categoryName == "预处理":
            return getOperatorGlyph("source")
        if categoryName == "检测":
            return getOperatorGlyph("edge")
        if categoryName == "测量":
            return getOperatorGlyph("measure")
        if categoryName == "输出":
            return getOperatorGlyph("output")
        if categoryName == "控制流":
            return getOperatorGlyph("flow")
        return getOperatorGlyph("default")

    def openNodeParamDialog(self, nodeId: str) -> None:
        node = self.flowModel.nodes.get(nodeId)
        if node is None:
            self.appendRuntimeLog("ERROR", "未找到所选节点")
            return

        if self.nodeParamDialog is None:
            self.nodeParamDialog = NodeParamDialog()
            self.nodeParamDialog.setApplyHandler(self.applyNodeParams)

        self.activeParamNodeId = nodeId
        setWorkflowOptions = getattr(self.nodeParamDialog, "setWorkflowOptions", None)
        if callable(setWorkflowOptions):
            setWorkflowOptions(list(self.workflowStore.workflowOrder))
        self.nodeParamDialog.setNodeContext(
            nodeId=nodeId,
            operatorId=node.operatorId,
            schema=node.paramSchema,
            values=node.params,
        )
        self.nodeParamDialog.show()
        raiseWindow = getattr(self.nodeParamDialog, "raise_", None)
        if callable(raiseWindow):
            raiseWindow()
        activateWindow = getattr(self.nodeParamDialog, "activateWindow", None)
        if callable(activateWindow):
            activateWindow()

    def applyNodeParams(self, nodeId: str, params: dict[str, object]) -> None:
        if nodeId not in self.flowModel.nodes:
            self.appendRuntimeLog("ERROR", "参数应用失败：节点不存在")
            return
        self.flowModel.setNodeParams(nodeId, params)
        self.appendRuntimeLog("INFO", f"参数已更新：{nodeId}")
        self._refreshRuntimePanelView()

    def updateNodeParams(self, nodeId: str, params: dict[str, object]) -> None:
        self.applyNodeParams(nodeId, params)
