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
from emo_master.apps.designer.state.workflow_store import (
    WorkflowStore,
    defaultNodePosition,
)
from emo_master.apps.designer.state.workflow_package import (
    WORKFLOW_PACKAGE_EXTENSION,
)
from emo_master.apps.designer.state.system_node_catalog import SYSTEM_NODE_CATALOG
from emo_master.apps.designer.presenters import NodeDetailsPresenter
from emo_master.apps.designer.operator_editors import EditorKey, OperatorEditorManager
from emo_master.apps.designer.ui.flow_scene import (
    FlowEdgeViewModel,
    FlowNodeViewModel,
    FlowScene,
)
from emo_master.apps.designer.ui.designer_graphics_view import DesignerGraphicsView
from emo_master.apps.designer.ui.log_dialog import (
    DEFAULT_MAX_LOG_ENTRIES,
    RuntimeLogDock,
    StructuredLogEntry,
)
from emo_master.apps.designer.ui.node_param_dialog import NodeParamDialog
from emo_master.apps.designer.ui.global_counters_dialog import GlobalCountersDialog
from emo_master.apps.designer.ui.icon_map import getOperatorGlyph
from emo_master.apps.designer.ui.operator_bubble import OperatorBubble
from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState
from emo_master.apps.designer.ui.workflow_package_preview_dialog import (
    WorkflowPackagePreviewDialog,
)

try:
    from PySide2.QtCore import QPointF, QSettings, QSize, QTimer, Qt
    from PySide2.QtGui import QKeySequence, QPixmap
    from PySide2.QtWidgets import (
        QAction,
        QFileDialog,
        QGraphicsView,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QMenu,
        QMenuBar,
        QPushButton,
        QShortcut,
        QSplitter,
        QTabWidget,
        QTextEdit,
        QToolBar,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    from emo_master.apps.designer.ui.icon_map import icon
    from emo_master.apps.designer.ui.widgets import ElidedLabel, PreviewLabel, WorkflowTabs, WrapLabel, scrollContent
    from emo_master.apps.designer.ui.theme import fitWindowToScreen

    _nativeQt = True
    _userRole = int(Qt.UserRole)
except Exception:  # pragma: no cover
    _nativeQt = False

    class Qt:  # type: ignore[no-redef]
        AlignCenter = 0
        UserRole = 0
        KeepAspectRatio = 0
        SmoothTransformation = 0
        Horizontal = 0
        CustomContextMenu = 0
        BottomDockWidgetArea = 0

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

        def addLayout(self, layout) -> None:
            _ = layout

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
            self._dockWidgets: list[tuple[object, object]] = []

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

        def addDockWidget(self, area, dockWidget) -> None:
            self._dockWidgets.append((area, dockWidget))

        def saveState(self):
            return b""

        def restoreState(self, state) -> bool:
            _ = state
            return True

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

    class QTreeWidgetItem:  # type: ignore[no-redef]
        def __init__(self, texts) -> None:
            self._texts = [texts] if isinstance(texts, str) else list(texts)
            self._data: dict[tuple[int, int], object] = {}
            self._children: list[object] = []
            self._tooltips: dict[int, str] = {}
            self._expanded = False

        def setData(self, column: int, role: int, value: object) -> None:
            self._data[(column, role)] = value

        def data(self, column: int, role: int) -> object:
            return self._data.get((column, role))

        def text(self, column: int = 0) -> str:
            return self._texts[column] if column < len(self._texts) else ""

        def addChild(self, item) -> None:
            self._children.append(item)

        def childCount(self) -> int:
            return len(self._children)

        def child(self, index: int):
            return self._children[index]

        def setToolTip(self, column: int, text: str) -> None:
            self._tooltips[column] = text

        def toolTip(self, column: int) -> str:
            return self._tooltips.get(column, "")

        def setExpanded(self, expanded: bool) -> None:
            self._expanded = expanded

    class QTreeWidget(QWidget):  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.itemClicked = _SignalStub()
            self.itemDoubleClicked = _SignalStub()
            self._items: list[QTreeWidgetItem] = []
            self._headerHidden = False

        def clear(self) -> None:
            self._items = []

        def addTopLevelItem(self, item) -> None:
            self._items.append(item)

        def topLevelItemCount(self) -> int:
            return len(self._items)

        def topLevelItem(self, index: int):
            return self._items[index]

        def setHeaderHidden(self, hidden: bool) -> None:
            self._headerHidden = hidden

        def isHeaderHidden(self) -> bool:
            return self._headerHidden

        def setFixedHeight(self, height: int) -> None:
            _ = height

        def expandAll(self) -> None:
            for item in self._items:
                item.setExpanded(True)

    class _QTabBarStub:
        def __init__(self, owner) -> None:
            self._owner = owner
            self.customContextMenuRequested = _SignalStub()
            self._contextMenuPolicy = None

        def setTabData(self, index: int, value: object) -> None:
            self._owner._tabData[index] = value

        def tabData(self, index: int):
            return self._owner._tabData[index]

        def setContextMenuPolicy(self, policy) -> None:
            self._contextMenuPolicy = policy

        def contextMenuPolicy(self):
            return self._contextMenuPolicy

        def tabAt(self, position) -> int:
            return position if isinstance(position, int) else -1

        def mapToGlobal(self, position):
            return position

    class QTabWidget(QWidget):  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.currentChanged = _SignalStub()
            self.tabBarClicked = _SignalStub()
            self.tabBarDoubleClicked = _SignalStub()
            self._tabs: list[tuple[object, str]] = []
            self._tabData: list[object] = []
            self._currentIndex = -1
            self._tabBar = _QTabBarStub(self)

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

        def tabBar(self):
            return self._tabBar

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
            self._enabled = True

        def text(self) -> str:
            return self._text

        def trigger(self) -> None:
            if self._enabled:
                self.triggered.emit(False)

        def setEnabled(self, enabled: bool) -> None:
            self._enabled = enabled

        def isEnabled(self) -> bool:
            return self._enabled

        def setFontPointSize(self, size: int) -> None:
            self._fontPointSize = size

        def fontPointSize(self) -> int:
            return self._fontPointSize

    class QMenu:  # type: ignore[no-redef]
        def __init__(self, title: str, parent=None) -> None:
            _ = parent
            self._title = title
            self._actions: list[object] = []
            self._styleSheet = ""
            self._executedAt = None

        def addAction(self, action) -> None:
            self._actions.append(action)

        def addMenu(self, menu) -> None:
            self._actions.append(menu)

        def addSeparator(self) -> None:
            return

        def actions(self) -> list[object]:
            return list(self._actions)

        def title(self) -> str:
            return self._title

        def clear(self) -> None:
            self._actions = []

        def setStyleSheet(self, style: str) -> None:
            self._styleSheet = style

        def exec_(self, position) -> None:
            self._executedAt = position

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

        @staticmethod
        def getSaveFileName(parent, title: str, directory: str, filterText: str):
            _ = parent
            _ = title
            _ = directory
            _ = filterText
            return "", ""

    class QInputDialog:  # type: ignore[no-redef]
        @staticmethod
        def getText(parent, title: str, label: str, echo=0, text: str = ""):
            _ = parent
            _ = title
            _ = label
            _ = echo
            return text, False

    class QMessageBox:  # type: ignore[no-redef]
        @staticmethod
        def warning(parent, title: str, message: str):
            _ = parent
            _ = title
            _ = message
            return None

    class QLineEdit:  # type: ignore[no-redef]
        Normal = 0

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


def _runtimeStateBelongsToJob(value: dict[str, object], jobId: str | None) -> bool:
    valueJobId = value.get("jobId", "")
    if not isinstance(valueJobId, str):
        valueJobId = ""
    if jobId is None:
        return valueJobId == ""
    return valueJobId in {"", jobId}


def _settingBool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


class MainWindow(QMainWindow):  # type: ignore[valid-type,misc]
    def __init__(
        self,
        runtimeClient,
        showStartupEntry: bool = False,
        projectEntryDialogFactory: Callable[[], object] | None = None,
        settingsStore: object | None = None,
        workflowPackagePreviewDialogFactory: Callable[[object], object]
        | None = None,
        globalCountersDialogFactory: Callable[[], object] | None = None,
    ) -> None:
        super().__init__()
        self.runtimeClient = runtimeClient
        self._showStartupEntry = showStartupEntry
        self._projectEntryDialogFactory = projectEntryDialogFactory
        self._workflowPackagePreviewDialogFactory = (
            workflowPackagePreviewDialogFactory
        )
        self._globalCountersDialogFactory = globalCountersDialogFactory
        self._globalCountersDialog: object | None = None
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
        self.logDock: RuntimeLogDock | None = None
        self.logDialog: RuntimeLogDock | None = None
        self.logBuffer: list[str] = []
        self.logEntries: list[StructuredLogEntry] = []
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
        self._allowRuntimeEventsWithoutActiveJob = True
        self.isJobRunning = False
        self._lastRunBlockedReason = ""
        self._addWorkflowTabIndex = -1
        self._menuActions: dict[str, QAction] = {}
        self._toolbarActions: dict[str, QAction] = {}
        self._focusedWorkflowId: tuple[str, str] | None = None
        self._workflowViewStates: dict[tuple[str, str], tuple[float, QPointF]] = {}
        self.setWindowTitle("视觉流程设计器")
        self.resize(1200, 760)

        self.mainToolbar = QToolBar("主工具栏")
        if _nativeQt:
            self.mainToolbar.setIconSize(QSize(18, 18))
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
        if _nativeQt:
            self.sidebarToggleButton.setFixedHeight(34)
            self.sidebarToggleButton.setIconSize(QSize(18, 18))
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
        categoryNames = [
            "全部",
            "预处理",
            "检测",
            "测量",
            "输出",
            "控制流",
            "其他",
        ]
        for rowStart in range(0, len(categoryNames), 2):
            categoryRow = QHBoxLayout()
            setCategoryRowSpacing = getattr(categoryRow, "setSpacing", None)
            if callable(setCategoryRowSpacing):
                setCategoryRowSpacing(6)
            for categoryName in categoryNames[rowStart : rowStart + 2]:
                categoryButton = QPushButton(categoryName)
                if _nativeQt:
                    categoryButton.setIcon(icon({"全部": "layout-grid", "预处理": "sliders-horizontal",
                                                 "检测": "scan-line", "测量": "ruler", "输出": "upload",
                                                 "控制流": "git-branch"}.get(categoryName, "layout-grid")))
                setCategoryName = getattr(categoryButton, "setObjectName", None)
                if callable(setCategoryName):
                    setCategoryName("sideCategoryButton")
                setCheckable = getattr(categoryButton, "setCheckable", None)
                if callable(setCheckable):
                    setCheckable(True)
                categoryButton.clicked.connect(
                    self._buildCategoryClickHandler(categoryName)
                )
                categoryRow.addWidget(categoryButton)
                self.categoryButtons[categoryName] = categoryButton
            if len(categoryNames[rowStart : rowStart + 2]) == 1:
                categoryRow.addStretch(1)
            categoryLayout.addLayout(categoryRow)
        self.categoryPanel.setLayout(categoryLayout)
        sideBarLayout.addWidget(self.categoryPanel)

        self.dependencyTreeContainer = QWidget()
        setDependencyContainerName = getattr(
            self.dependencyTreeContainer, "setObjectName", None
        )
        if callable(setDependencyContainerName):
            setDependencyContainerName("sidebarDependencySection")
        dependencyLayout = QVBoxLayout()
        setDependencyMargins = getattr(dependencyLayout, "setContentsMargins", None)
        if callable(setDependencyMargins):
            setDependencyMargins(0, 0, 0, 0)
        setDependencySpacing = getattr(dependencyLayout, "setSpacing", None)
        if callable(setDependencySpacing):
            setDependencySpacing(6)
        self.dependencyTreeTitle = QLabel("工作流依赖")
        setDependencyTitleName = getattr(
            self.dependencyTreeTitle, "setObjectName", None
        )
        if callable(setDependencyTitleName):
            setDependencyTitleName("panelTitle")
        dependencyLayout.addWidget(self.dependencyTreeTitle)
        self.workflowDependencyTree = QTreeWidget()
        setDependencyTreeName = getattr(
            self.workflowDependencyTree, "setObjectName", None
        )
        if callable(setDependencyTreeName):
            setDependencyTreeName("workflowDependencyTree")
        setHeaderHidden = getattr(self.workflowDependencyTree, "setHeaderHidden", None)
        if callable(setHeaderHidden):
            setHeaderHidden(True)
        setDependencyHeight = getattr(
            self.workflowDependencyTree, "setFixedHeight", None
        )
        if callable(setDependencyHeight):
            setDependencyHeight(150)
        self.workflowDependencyTree.itemClicked.connect(
            self.navigateToWorkflowDependencyItem
        )
        self.workflowDependencyTree.itemDoubleClicked.connect(
            self.navigateToWorkflowDependencyItem
        )
        dependencyLayout.addWidget(self.workflowDependencyTree)
        self.dependencyTreeContainer.setLayout(dependencyLayout)
        sideBarLayout.addWidget(self.dependencyTreeContainer)

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
            setFixedHeight(110)
        self.nodeListWidget.itemClicked.connect(self.selectNodeFromSidebar)
        self.nodeListWidget.itemDoubleClicked.connect(self.navigateToSidebarItem)
        nodeListLayout.addWidget(self.nodeListWidget)
        self.nodeListContainer.setLayout(nodeListLayout)
        sideBarLayout.addWidget(self.nodeListContainer)
        addSidebarStretch = getattr(sideBarLayout, "addStretch", None)
        if callable(addSidebarStretch):
            addSidebarStretch(1)
        if _nativeQt:
            sideBarLayout.removeWidget(self.sidebarToggleButton)
            sidebarBody = QWidget()
            sidebarBody.setLayout(sideBarLayout)
            sidebarOuter = QVBoxLayout(self.sidebarContainer)
            sidebarOuter.setContentsMargins(0, 0, 0, 0)
            sidebarOuter.addWidget(self.sidebarToggleButton)
            self.sidebarScroll = scrollContent(sidebarBody)
            sidebarOuter.addWidget(self.sidebarScroll, 1)
        else:
            self.sidebarContainer.setLayout(sideBarLayout)

        self.canvasPanel = QWidget()
        leftPanel = QVBoxLayout()
        setCanvasMargins = getattr(leftPanel, "setContentsMargins", None)
        if callable(setCanvasMargins):
            setCanvasMargins(0, 0, 0, 0)
        setCanvasSpacing = getattr(leftPanel, "setSpacing", None)
        if callable(setCanvasSpacing):
            setCanvasSpacing(8)
        self.workflowTabs = WorkflowTabs() if _nativeQt else QTabWidget()
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
        if _nativeQt:
            leftPanel.setStretch(leftPanel.indexOf(self.flowView), 1)
            canvasTools = QHBoxLayout()
            canvasTools.setContentsMargins(0, 0, 0, 0)
            canvasTools.addStretch(1)
            self.zoomOutButton = QToolButton()
            self.zoomOutButton.setIcon(icon("minus"))
            self.zoomOutButton.setToolTip("缩小")
            self.zoomOutButton.clicked.connect(lambda: self.flowView.zoomByDelta(-1))
            self.zoomResetButton = QToolButton()
            self.zoomResetButton.setText("100%")
            self.zoomResetButton.setFixedWidth(64)
            self.zoomResetButton.setToolTip("恢复 100%")
            self.zoomResetButton.clicked.connect(lambda: self.flowView.setZoomFactor(1.0))
            self.zoomInButton = QToolButton()
            self.zoomInButton.setIcon(icon("plus"))
            self.zoomInButton.setToolTip("放大")
            self.zoomInButton.clicked.connect(lambda: self.flowView.zoomByDelta(1))
            self.fitCanvasButton = QToolButton()
            self.fitCanvasButton.setIcon(icon("maximize"))
            self.fitCanvasButton.setToolTip("适应画布")
            self.fitCanvasButton.clicked.connect(self.focusGraphContent)
            for tool in (self.zoomOutButton, self.zoomResetButton, self.zoomInButton, self.fitCanvasButton):
                canvasTools.addWidget(tool)
            self.flowView.zoomChanged.connect(lambda value: self.zoomResetButton.setText(f"{value:.0%}"))
            leftPanel.addLayout(canvasTools)
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
            setSummaryMargins(12, 4, 12, 10)
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

        self.jobMessageCard = ElidedLabel("消息：-") if _nativeQt else QLabel("消息：-")
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
            setNodeStatusMargins(12, 4, 12, 10)
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
        detailLabel = WrapLabel if _nativeQt else QLabel
        self.nodeDetailTitleCard = detailLabel("未选中节点")
        self.nodeDetailMetaCard = detailLabel("")
        self.nodeDetailPortsCard = detailLabel("输入: 无\n输出: 无")
        self.nodeDetailParamsCard = detailLabel("参数:\n- 无")
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
        if _nativeQt:
            nodeStatusLayout.addStretch(1)
            self.nodeDetailsScroll = scrollContent(self.nodeStatusSection, name="nodeDetailsScroll")
            self.nodeDetailsScroll.setMinimumHeight(72)
            rightPanel.addWidget(self.nodeDetailsScroll, 1)
        else:
            rightPanel.addWidget(self.nodeStatusSection)

        self.previewSection = QWidget()
        setPreviewSectionName = getattr(self.previewSection, "setObjectName", None)
        if callable(setPreviewSectionName):
            setPreviewSectionName("rightPanelSection")
        previewLayout = QVBoxLayout()
        setPreviewMargins = getattr(previewLayout, "setContentsMargins", None)
        if callable(setPreviewMargins):
            setPreviewMargins(12, 4, 12, 10)
        setPreviewSpacing = getattr(previewLayout, "setSpacing", None)
        if callable(setPreviewSpacing):
            setPreviewSpacing(6)
        previewTitle = QLabel("结果预览")
        setPreviewTitleName = getattr(previewTitle, "setObjectName", None)
        if callable(setPreviewTitleName):
            setPreviewTitleName("panelTitle")
        previewLayout.addWidget(previewTitle)

        self.previewImageLabel = PreviewLabel("暂无图片") if _nativeQt else QLabel("暂无图片")
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
        if _nativeQt:
            rightPanel.setStretch(rightPanel.indexOf(self.previewSection), 1)
        addRightStretch = getattr(rightPanel, "addStretch", None)
        if callable(addRightStretch) and not _nativeQt:
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
        self.logDock = RuntimeLogDock(
            self,
            onClear=self._clearRuntimeLogCache,
            maximumEntries=DEFAULT_MAX_LOG_ENTRIES,
        )
        self.logDialog = self.logDock
        addDockWidget = getattr(self, "addDockWidget", None)
        if callable(addDockWidget):
            addDockWidget(Qt.BottomDockWidgetArea, self.logDock)
        self.logDock.hide()

        self.workflowController = WorkflowController(
            workflowStore=self.workflowStore,
            flowModel=self.flowModel,
            flowScene=self.flowScene,
            refreshSidebarNodeList=self.refreshSidebarNodeList,
            focusGraphContent=lambda: self.focusGraphContent(automatic=True),
            updateToolbarState=self.updateToolbarState,
        )
        self.projectController = ProjectController(
            runtimeClient=self.runtimeClient,
            flowModel=self.flowModel,
            flowScene=self.flowScene,
            settingsStore=self.settingsStore,
            appendLog=self.appendRuntimeLog,
            refreshSidebarNodeList=self.refreshSidebarNodeList,
            focusGraphContent=lambda: self.focusGraphContent(automatic=True),
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
            appendEvent=self.appendRuntimeEvent,
        )
        self.operatorCatalogController = OperatorCatalogController(
            runtimeClient=self.runtimeClient,
            appendLog=self.appendRuntimeLog,
        )
        self.operatorEditorManager = OperatorEditorManager(
            runtimeClient=self.runtimeClient,
            settingsStore=self.settingsStore,
            applyParams=self._applyEditorParams,
            appendLog=self.appendEditorLog,
        )
        self.layoutController = LayoutController(
            mainSplitter=self.mainSplitter,
            sidebarContainer=self.sidebarContainer,
            rightPanelContainer=self.rightPanelContainer,
            canvasPanel=self.canvasPanel,
            dependencyTreeWidget=self.workflowDependencyTree,
            nodeListWidget=self.nodeListWidget,
            runtimeStatusOutput=self.runtimeStatusOutput,
            previewImageLabel=self.previewImageLabel,
            sidebarToggleButton=self.sidebarToggleButton,
            categoryPanel=self.categoryPanel,
            dependencyTreeContainer=self.dependencyTreeContainer,
            nodeListContainer=self.nodeListContainer,
            mainMenuBar=self.mainMenuBar,
            settingsStore=self.settingsStore,
            widthGetter=self.width,
            isSidebarCollapsedGetter=lambda: self.isSidebarCollapsed,
        )

        workflowChanged = getattr(self.workflowTabs, "currentChanged", None)
        if workflowChanged is not None and hasattr(workflowChanged, "connect"):
            workflowChanged.connect(self._onWorkflowTabChanged)
        workflowTabClicked = getattr(self.workflowTabs, "tabBarClicked", None)
        if workflowTabClicked is not None and hasattr(workflowTabClicked, "connect"):
            workflowTabClicked.connect(self._onWorkflowTabClicked)
        workflowTabBar = self.workflowTabs.tabBar()
        setContextMenuPolicy = getattr(workflowTabBar, "setContextMenuPolicy", None)
        if callable(setContextMenuPolicy):
            setContextMenuPolicy(Qt.CustomContextMenu)
        workflowTabContextMenu = getattr(
            workflowTabBar, "customContextMenuRequested", None
        )
        if workflowTabContextMenu is not None and hasattr(
            workflowTabContextMenu, "connect"
        ):
            workflowTabContextMenu.connect(self._onWorkflowTabContextMenuRequested)
        self.deleteShortcut = QShortcut(QKeySequence.Delete, self)
        self.deleteShortcut.activated.connect(self.handleDeleteShortcut)
        self.backspaceDeleteShortcut = QShortcut(QKeySequence("Backspace"), self)
        self.backspaceDeleteShortcut.activated.connect(self.handleDeleteShortcut)
        self._buildMainMenuBar()
        self.refreshOperators()
        self._layoutMode = "normal"
        self.workflowController.refreshActiveWorkflow()
        self.updateToolbarState()
        self._refreshRuntimePanelView()
        self.setActiveCategory("全部")
        self.applySidebarState()
        self.operatorBubble = OperatorBubble(self)
        self.operatorBubble.setCreateHandler(self.addNodeFromOperatorPayload)
        self.refreshSidebarNodeList()
        self._refreshWorkflowTabs()
        self.applyResponsiveLayout()
        self.restoreMainSplitterSizes()
        self._restoreRuntimeLogSettings()

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
        self._focusedWorkflowId = None
        self._workflowViewStates.clear()
        ok, loadedProjectPath, currentProjectDir = (
            self.projectController.loadProjectDirectory(projectDirPath)
        )
        if ok:
            self._applyLoadedProjectState(loadedProjectPath, currentProjectDir)
        else:
            self._refreshWorkflowTabs()
            self.updateToolbarState()
        return bool(ok)

    def _applyLoadedProjectState(
        self, loadedProjectPath: str | None, currentProjectDir: Path | None
    ) -> None:
        self.operatorEditorManager.closeAll()
        self.nodeParamDialog = None
        self.activeParamNodeId = None
        self._resetRuntimeVisualState(clearHistory=True)
        self.currentJobId = None
        self.runtimePanelState.setActiveJob(None)
        self.loadedProjectPath = loadedProjectPath
        self.currentProjectDir = currentProjectDir
        counterDialog = self._globalCountersDialog
        if counterDialog is not None:
            bindProject = getattr(counterDialog, "bindProject", None)
            if callable(bindProject):
                bindProject(loadedProjectPath or "")
        self.activeWorkflowId = self.workflowController.activeWorkflowId
        self._restoreActiveWorkflowRuntimeState()
        self._refreshWorkflowTabs()

    def refreshSidebarNodeList(self) -> None:
        clearMethod = getattr(self.nodeListWidget, "clear", None)
        if callable(clearMethod):
            clearMethod()
        for node in self.flowModel.nodes.values():
            if node.kind in {"workflow_input", "workflow_output"}:
                continue
            shortId = node.nodeId[:8]
            item = QListWidgetItem(f"{node.displayName}\n{shortId}")
            setData = getattr(item, "setData", None)
            if callable(setData):
                setData(USER_ROLE, node.nodeId)
            addItem = getattr(self.nodeListWidget, "addItem", None)
            if callable(addItem):
                addItem(item)
        self.refreshWorkflowDependencyTree()

    def getSidebarNodeEntries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for node in self.flowModel.nodes.values():
            if node.kind in {"workflow_input", "workflow_output"}:
                continue
            entries.append(
                {
                    "nodeId": node.nodeId,
                    "displayName": node.displayName,
                    "shortNodeId": node.nodeId[:8],
                }
            )
        return entries

    def getWorkflowDependencyEntries(self) -> list[dict[str, object]]:
        controller = getattr(self, "workflowController", None)
        captureActiveWorkflow = getattr(controller, "captureActiveWorkflow", None)
        if callable(captureActiveWorkflow):
            captureActiveWorkflow()
        return self.workflowStore.getWorkflowDependencyTree()

    def refreshWorkflowDependencyTree(self) -> None:
        treeWidget = getattr(self, "workflowDependencyTree", None)
        if treeWidget is None:
            return
        clearMethod = getattr(treeWidget, "clear", None)
        if callable(clearMethod):
            clearMethod()
        for entry in self.getWorkflowDependencyEntries():
            item = self._buildWorkflowDependencyTreeItem(entry)
            addTopLevelItem = getattr(treeWidget, "addTopLevelItem", None)
            if callable(addTopLevelItem):
                addTopLevelItem(item)
        expandAll = getattr(treeWidget, "expandAll", None)
        if callable(expandAll):
            expandAll()

    def _buildWorkflowDependencyTreeItem(
        self, entry: dict[str, object]
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([self._workflowDependencyItemText(entry)])
        itemType = entry.get("itemType")
        workflowId = entry.get("workflowId")
        status = entry.get("status")
        setData = getattr(item, "setData", None)
        if callable(setData):
            setData(
                0,
                USER_ROLE,
                {
                    "itemType": itemType,
                    "workflowId": workflowId,
                    "status": status,
                },
            )
        setToolTip = getattr(item, "setToolTip", None)
        if callable(setToolTip):
            setToolTip(0, self._workflowDependencyItemToolTip(entry))
        rawChildren = entry.get("children", [])
        children = rawChildren if isinstance(rawChildren, list) else []
        addChild = getattr(item, "addChild", None)
        if callable(addChild):
            for child in children:
                if isinstance(child, dict):
                    addChild(self._buildWorkflowDependencyTreeItem(child))
        return item

    def _workflowDependencyItemText(self, entry: dict[str, object]) -> str:
        if entry.get("itemType") == "group":
            count = entry.get("count", 0)
            return f"{entry.get('label', '未使用工作流')} ({count})"

        name = str(entry.get("name", "未设置"))
        relation = str(entry.get("relation", ""))
        relationLabel = str(entry.get("relationLabel", ""))
        text = (
            f"{relationLabel} → {name}"
            if relation
            not in {
                "entry",
                "unreachable-root",
            }
            else name
        )
        if relation == "entry":
            text = f"★ {text}"

        badges: list[str] = []
        workflowId = entry.get("workflowId")
        if bool(entry.get("isEntry", False)):
            badges.append("入口")
        if workflowId == self.activeWorkflowId and bool(entry.get("exists", False)):
            badges.append("当前")
        status = entry.get("status")
        if status == "cycle":
            badges.append("循环引用")
        elif status == "missing":
            badges.append("目标缺失")
        elif relation == "unreachable-root":
            badges.append(
                "未被引用" if bool(entry.get("isUnreferenced", False)) else "入口不可达"
            )
        return f"{text} [{' · '.join(badges)}]" if badges else text

    def _workflowDependencyItemToolTip(self, entry: dict[str, object]) -> str:
        if entry.get("itemType") == "group":
            return "这些工作流无法从入口工作流到达。"
        workflowId = entry.get("workflowId")
        lines = [f"工作流：{workflowId or '未设置'}"]
        relationLabel = entry.get("relationLabel")
        if relationLabel not in {None, "", "入口", "入口不可达"}:
            lines.append(f"引用类型：{relationLabel}")
        sourceNodeId = entry.get("sourceNodeId")
        if isinstance(sourceNodeId, str) and sourceNodeId:
            lines.append(f"来源节点：{sourceNodeId}")
        status = entry.get("status")
        if status == "cycle":
            lines.append("此引用返回当前依赖路径中的上级工作流。")
        elif status == "missing":
            lines.append("引用目标不存在或尚未配置。")
        return "\n".join(lines)

    def getToolbarGroups(self) -> list[list[str]]:
        return [
            ["加载项目", "保存项目", "校验项目"],
            ["开始运行", "停止运行"],
            ["自动布局", "校验流程图"],
            ["刷新算子", "打开日志"],
        ]

    def _addToolbarGroup(self, title: str, buttons: list[QPushButton]) -> None:
        commands = {
            "加载项目": ("loadButton", "folder-open", self.loadProject),
            "保存项目": ("saveProjectButton", "save", self.saveProjectAction),
            "校验项目": ("validateButton", "shield-check", self.validateProject),
            "开始运行": ("startButton", "play", self.startJob),
            "停止运行": ("stopButton", "square", self.stopJob),
            "自动布局": ("autoLayoutButton", "layout-grid", self.autoLayoutNodes),
            "校验流程图": ("validateGraphButton", "shield-check", self.validateGraph),
            "刷新算子": ("refreshButton", "refresh-cw", self.refreshOperators),
            "打开日志": ("openLogsButton", "logs", self.openLogDialog),
        }
        for button in buttons:
            label = button.text()
            attribute, iconName, callback = commands[label]
            if not _nativeQt:
                self.mainToolbar.addWidget(button)
                button.clicked.connect(callback)
                continue
            action = QAction(icon(iconName, "#ffffff" if label == "开始运行" else "#475569"), label, self)
            action.setToolTip(label)
            action.triggered.connect(lambda checked=False, command=callback: command())
            self.mainToolbar.addAction(action)
            tool = self.mainToolbar.widgetForAction(action)
            tool.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if label in {
                "加载项目", "保存项目", "开始运行", "停止运行"} else Qt.ToolButtonIconOnly)
            tool.setObjectName("primaryButton" if label == "开始运行" else "dangerButton" if label == "停止运行" else "")
            self._toolbarActions[label] = action
            setattr(self, attribute, tool)
            button.deleteLater()

    def _buildMainMenuBar(self) -> None:
        self._menuBarGroups = ["文件", "运行", "编辑", "视图"]
        addMenu = getattr(self.mainMenuBar, "addMenu", None)
        if not callable(addMenu):
            return

        fileMenu = addMenu("文件")
        self._addMenuAction(fileMenu, "打开项目", self.loadProject)
        self._addMenuAction(fileMenu, "保存项目", self.saveProjectAction)
        addSubMenu = getattr(fileMenu, "addMenu", None)
        recentMenu = (
            addSubMenu("最近项目") if callable(addSubMenu) else QMenu("最近项目")
        )
        self._recentProjectsMenu = recentMenu
        self.refreshRecentProjectsMenu()

        runMenu = addMenu("运行")
        self._addMenuAction(runMenu, "开始运行", self.startJob)
        self._addMenuAction(runMenu, "停止运行", self.stopJob)
        self._addMenuAction(runMenu, "全局计数器…", self.openGlobalCountersDialog)
        self._addMenuAction(runMenu, "打开日志", self.openLogDialog)

        editMenu = addMenu("编辑")
        self._addMenuAction(editMenu, "自动布局", self.autoLayoutNodes)
        self._addMenuAction(editMenu, "校验流程图", self.validateGraph)

        viewMenu = addMenu("视图")
        self._addMenuAction(viewMenu, "切换侧边栏", self.toggleSidebar)
        self._addMenuAction(viewMenu, "聚焦画布内容", self.focusGraphContent)

    def _addMenuAction(self, menu, title: str, callback) -> None:
        sharedTitle = "加载项目" if title == "打开项目" else title
        shared = self._toolbarActions.get(sharedTitle)
        action = shared if shared is not None else QAction(title, self)
        triggered = getattr(action, "triggered", None)
        if shared is None and triggered is not None and hasattr(triggered, "connect"):

            def onTriggered(checked: bool = False) -> None:
                _ = checked
                callback()

            triggered.connect(onTriggered)
        addAction = getattr(menu, "addAction", None)
        if callable(addAction):
            addAction(action)
        self._menuActions[title] = action

    def getMenuBarFontSize(self) -> int:
        return self.layoutController.getMenuBarFontSize()

    def openGlobalCountersDialog(self) -> object | None:
        if not self.loadedProjectPath:
            QMessageBox.warning(self, "全局计数器", "请先加载项目")
            return None
        dialog = self._globalCountersDialog
        if dialog is None:
            dialog = (
                self._globalCountersDialogFactory()
                if self._globalCountersDialogFactory is not None
                else GlobalCountersDialog(self.runtimeClient, self)
            )
            self._globalCountersDialog = dialog
        showForProject = getattr(dialog, "showForProject", None)
        if callable(showForProject):
            showForProject(self.loadedProjectPath)
        else:
            bindProject = getattr(dialog, "bindProject", None)
            if callable(bindProject):
                bindProject(self.loadedProjectPath)
            show = getattr(dialog, "show", None)
            if callable(show):
                show()
        return dialog

    def _setCurrentJobId(self, jobId: str | None) -> None:
        if self.currentJobId == jobId:
            return
        hadActiveJob = self.currentJobId is not None
        self.currentJobId = jobId
        self._allowRuntimeEventsWithoutActiveJob = not (jobId is None and hadActiveJob)
        self._resetRuntimeVisualState()
        setActiveJob = getattr(self.runtimePanelState, "setActiveJob", None)
        if callable(setActiveJob):
            setActiveJob(jobId)
        self._restoreActiveWorkflowRuntimeState()
        self._refreshRuntimePanelView()

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

    def getSystemNodeCatalog(self) -> list[dict[str, object]]:
        return [
            {
                "kind": definition.kind,
                "displayName": definition.displayName,
                "category": definition.category,
                "canvasVisible": definition.canvasVisible,
            }
            for definition in SYSTEM_NODE_CATALOG
        ]

    def _getSystemNodeLibraryPayloads(self) -> list[dict[str, object]]:
        operatorIds = {
            "subflow": "system.subflow",
            "loop:repeat": "system.repeat",
            "loop:foreach": "system.foreach",
            "loop:while": "system.while",
        }
        return [
            {
                "operatorId": operatorIds[definition.kind],
                "displayName": definition.displayName,
                "version": "",
                "category": definition.category,
                "iconKey": "flow",
                "summary": "",
                "inputPorts": {},
                "outputPorts": {},
                "paramSchema": {},
                "systemNodeKind": definition.kind,
            }
            for definition in SYSTEM_NODE_CATALOG
            if definition.canvasVisible and definition.kind in operatorIds
        ]

    def getRecentProjectsMenuEntries(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.getRecentProjects()]

    def _refreshWorkflowTabs(self) -> None:
        labels = self.workflowController.getWorkflowTabLabels()
        self._workflowTabsUpdating = True
        self._addWorkflowTabIndex = -1
        blockSignals = getattr(self.workflowTabs, "blockSignals", None)
        previousBlock = blockSignals(True) if callable(blockSignals) else None
        tabBarGetter = getattr(self.workflowTabs, "tabBar", None)
        tabBar = tabBarGetter() if callable(tabBarGetter) else None
        setTabData = getattr(tabBar, "setTabData", None)
        try:
            self.workflowTabs.clear()
            for label in labels:
                title = str(label["name"])
                if bool(label.get("isEntry", False)):
                    title = f"{title} [入口]"
                index = self.workflowTabs.addTab(QWidget(), title)
                if callable(setTabData):
                    setTabData(index, str(label["workflowId"]))
            addTab = getattr(self.workflowTabs, "addTab", None)
            if callable(addTab):
                plusIndex = addTab(QWidget(), "+")
                self._addWorkflowTabIndex = plusIndex
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
        self.refreshWorkflowDependencyTree()

    def _onWorkflowTabChanged(self, index: int) -> None:
        if getattr(self, "_workflowTabsUpdating", False):
            return
        workflowId = self._workflowIdForTab(index)
        if not isinstance(workflowId, str) or workflowId == "":
            return
        if workflowId == self.activeWorkflowId:
            return
        self.workflowController.switchWorkflow(workflowId)
        self.activeWorkflowId = workflowId
        self._restoreActiveWorkflowRuntimeState()
        self._refreshWorkflowTabs()

    def _onWorkflowTabClicked(self, index: int) -> None:
        if index != self._addWorkflowTabIndex:
            return
        self.promptCreateWorkflow()

    def _onWorkflowTabContextMenuRequested(self, position) -> None:
        tabBar = self.workflowTabs.tabBar()
        tabAt = getattr(tabBar, "tabAt", None)
        if not callable(tabAt):
            return
        index = tabAt(position)
        if not isinstance(index, int):
            return
        menu = self._buildWorkflowTabContextMenu(index)
        if menu is None:
            return
        mapToGlobal = getattr(tabBar, "mapToGlobal", None)
        globalPosition = mapToGlobal(position) if callable(mapToGlobal) else position
        execMenu = getattr(menu, "exec_", None)
        if callable(execMenu):
            execMenu(globalPosition)

    def _buildWorkflowTabContextMenu(self, index: int):
        workflowId = self._workflowIdForTab(index)
        if workflowId is None:
            return None
        isActive = workflowId == self.activeWorkflowId
        isEntry = workflowId == self.workflowStore.entryWorkflowId
        menu = QMenu("工作流", self)
        self._addContextMenuAction(
            menu,
            "当前已打开" if isActive else "打开工作流",
            lambda: self.activateWorkflow(workflowId),
            enabled=not isActive,
        )
        self._addContextMenuAction(
            menu,
            "当前入口工作流" if isEntry else "设为入口工作流",
            lambda: self.setEntryWorkflow(workflowId),
            enabled=not isEntry,
        )
        menu.addSeparator()
        self._addContextMenuAction(
            menu,
            "重命名工作流",
            lambda: self.promptRenameWorkflow(workflowId),
        )
        self._addContextMenuAction(
            menu,
            "设置工作流接口",
            lambda: self.editWorkflowInterfaceFor(workflowId),
        )
        menu.addSeparator()
        self._addContextMenuAction(
            menu,
            "导出工作流包",
            lambda: self.exportWorkflowPackageFor(workflowId),
        )
        self._addContextMenuAction(
            menu,
            "导入为子工作流",
            lambda: self.importWorkflowPackageAction(workflowId),
        )
        menu.addSeparator()
        self._addContextMenuAction(
            menu,
            "删除工作流",
            lambda: self.deleteWorkflow(workflowId),
        )
        return menu

    def _addContextMenuAction(
        self,
        menu,
        title: str,
        callback: Callable[[], object],
        enabled: bool = True,
    ) -> QAction:
        action = QAction(title, self)
        setEnabled = getattr(action, "setEnabled", None)
        if callable(setEnabled):
            setEnabled(enabled)
        triggered = getattr(action, "triggered", None)
        if triggered is not None and hasattr(triggered, "connect"):

            def onTriggered(checked: bool = False) -> None:
                _ = checked
                callback()

            triggered.connect(onTriggered)
        menu.addAction(action)
        return action

    def _workflowIdForTab(self, index: int) -> str | None:
        if index < 0 or index >= self.workflowTabs.count():
            return None
        tabBarGetter = getattr(self.workflowTabs, "tabBar", None)
        tabBar = tabBarGetter() if callable(tabBarGetter) else None
        tabData = getattr(tabBar, "tabData", None)
        workflowId = tabData(index) if callable(tabData) else None
        return workflowId if isinstance(workflowId, str) and workflowId else None

    def getWorkflowTabs(self) -> list[dict[str, object]]:
        return self.workflowController.getWorkflowTabLabels()

    def exportWorkflowPackageFor(self, workflowId: str) -> str | None:
        try:
            workflow = self.workflowStore.get(workflowId)
        except KeyError:
            self.appendRuntimeLog("ERROR", f"导出工作流失败：{workflowId} 不存在")
            return None
        suggestedName = f"{workflowId}{WORKFLOW_PACKAGE_EXTENSION}"
        defaultPath = (
            str(self.currentProjectDir / suggestedName)
            if self.currentProjectDir is not None
            else suggestedName
        )
        selectedPath, _ = QFileDialog.getSaveFileName(
            self,
            "导出工作流包",
            defaultPath,
            "EmoMaster 工作流包 (*.emowf.json)",
        )
        if selectedPath == "":
            self.appendRuntimeLog("INFO", "已取消导出工作流")
            return None
        try:
            result = self.workflowController.exportWorkflowPackage(
                workflowId, selectedPath
            )
        except (KeyError, OSError, TypeError, ValueError) as err:
            message = f"导出工作流失败：{err}"
            self.appendRuntimeLog("ERROR", message)
            QMessageBox.warning(self, "导出工作流失败", message)
            return None
        self.appendRuntimeLog(
            "INFO",
            f"工作流已导出：{result.packagePath} | 根工作流：{workflow.name} | "
            f"包含 {len(result.workflowIds)} 个工作流",
        )
        return str(result.packagePath)

    def importWorkflowPackageAction(
        self, parentWorkflowId: str | None = None
    ) -> str | None:
        selectedPath, _ = QFileDialog.getOpenFileName(
            self,
            "导入工作流包",
            str(self.currentProjectDir) if self.currentProjectDir is not None else "",
            "EmoMaster 工作流包 (*.emowf.json);;JSON 文件 (*.json)",
        )
        if selectedPath == "":
            self.appendRuntimeLog("INFO", "已取消导入工作流")
            return None
        selectedParentWorkflowId = parentWorkflowId or self.activeWorkflowId
        availableOperatorIds = [
            str(item.get("operatorId"))
            for item in self.operatorCatalog
            if isinstance(item.get("operatorId"), str)
            and str(item.get("operatorId")) != ""
        ]
        try:
            preview = self.workflowController.previewWorkflowPackageImport(
                selectedPath,
                availableOperatorIds=availableOperatorIds,
            )
        except (OSError, TypeError, ValueError) as err:
            message = f"导入工作流失败：{err}"
            self.appendRuntimeLog("ERROR", message)
            QMessageBox.warning(self, "导入工作流失败", message)
            return None
        dialog = self._createWorkflowPackagePreviewDialog(preview)
        accepted, insertSubflow = dialog.execSelection()
        if not accepted:
            self.appendRuntimeLog("INFO", "已在预览中取消导入工作流")
            return None
        try:
            result = self.workflowController.importWorkflowPackage(
                selectedPath,
                insertIntoWorkflowId=(
                    selectedParentWorkflowId if insertSubflow else None
                ),
            )
        except (KeyError, OSError, TypeError, ValueError) as err:
            message = f"导入工作流失败：{err}"
            self.appendRuntimeLog("ERROR", message)
            QMessageBox.warning(self, "导入工作流失败", message)
            return None
        self.activeWorkflowId = (
            result.parentWorkflowId or result.rootWorkflowId
        )
        self._restoreActiveWorkflowRuntimeState()
        self._refreshWorkflowTabs()
        if result.insertedSubflowNodeId is not None:
            self.flowModel.selectNode(result.insertedSubflowNodeId)
            self.flowScene.setNodeSelected(result.insertedSubflowNodeId)
            self.onNodeSelectionChanged()
        self.appendRuntimeLog(
            "INFO",
            f"工作流已导入：{selectedPath} | 包含 {len(result.workflowIds)} 个工作流"
            + (
                f" | 已在 {selectedParentWorkflowId} 插入 Subflow："
                f"{result.insertedSubflowNodeId}"
                if result.insertedSubflowNodeId is not None
                else ""
            )
            + " | 请保存项目以持久化更改",
        )
        if preview.missingOperators:
            self.appendRuntimeLog(
                "WARN",
                "导入的工作流包含未加载算子："
                + ", ".join(preview.missingOperators),
            )
        pathWarnings = [
            item for item in preview.externalPaths if item.status != "available"
        ]
        if pathWarnings:
            self.appendRuntimeLog(
                "WARN",
                f"导入的工作流有 {len(pathWarnings)} 个外部路径需要核对",
            )
        return result.rootWorkflowId

    def _createWorkflowPackagePreviewDialog(self, preview):
        if self._workflowPackagePreviewDialogFactory is not None:
            return self._workflowPackagePreviewDialogFactory(preview)
        return WorkflowPackagePreviewDialog(preview, self)

    def getActiveWorkflowId(self) -> str:
        return self.activeWorkflowId

    def activateWorkflow(self, workflowId: str) -> None:
        if workflowId == self.activeWorkflowId:
            return
        try:
            self.workflowController.switchWorkflow(workflowId)
        except KeyError:
            self.appendRuntimeLog("ERROR", f"工作流不存在：{workflowId}")
            return
        self.activeWorkflowId = workflowId
        self._restoreActiveWorkflowRuntimeState()
        self._refreshWorkflowTabs()

    def createWorkflow(self, name: str = "New Workflow") -> str:
        workflowId = self.workflowController.createWorkflow(name)
        self.activeWorkflowId = workflowId
        self._refreshWorkflowTabs()
        return workflowId

    def promptCreateWorkflow(self) -> str | None:
        name = self._promptWorkflowName("新建工作流", "New Workflow")
        if name is None:
            self._refreshWorkflowTabs()
            return None
        return self.createWorkflow(name)

    def renameWorkflow(self, workflowId: str, name: str) -> None:
        self.workflowController.renameWorkflow(workflowId, name)
        self._refreshWorkflowTabs()

    def renameActiveWorkflow(self, name: str) -> None:
        self.renameWorkflow(self.activeWorkflowId, name)

    def promptRenameActiveWorkflow(self) -> bool:
        return self.promptRenameWorkflow(self.activeWorkflowId)

    def promptRenameWorkflow(self, workflowId: str) -> bool:
        try:
            currentName = self.workflowStore.get(workflowId).name
        except KeyError:
            self.appendRuntimeLog("ERROR", f"重命名工作流失败：{workflowId} 不存在")
            return False
        name = self._promptWorkflowName("重命名工作流", currentName)
        if name is None:
            return False
        try:
            self.renameWorkflow(workflowId, name)
        except (KeyError, ValueError) as err:
            self.appendRuntimeLog("ERROR", f"重命名工作流失败：{err}")
            return False
        return True

    def _promptWorkflowName(self, title: str, currentName: str) -> str | None:
        text, accepted = QInputDialog.getText(
            self,
            title,
            "工作流名称",
            QLineEdit.Normal,
            currentName,
        )
        if not accepted:
            return None
        name = str(text).strip()
        if name == "":
            self.appendRuntimeLog("WARN", "工作流名称不能为空")
            return None
        return name

    def deleteActiveWorkflow(self) -> None:
        self.deleteWorkflow(self.activeWorkflowId)

    def deleteWorkflow(self, workflowId: str) -> None:
        try:
            self.workflowController.deleteWorkflow(workflowId)
        except (KeyError, ValueError) as err:
            message = self._formatWorkflowDeleteFailure(workflowId, err)
            self.appendRuntimeLog("ERROR", message)
            QMessageBox.warning(self, "无法删除工作流", message)
            return
        self.operatorEditorManager.closeWorkflow(
            self._currentProjectId(), workflowId
        )
        self.activeWorkflowId = self.workflowController.activeWorkflowId
        self._refreshWorkflowTabs()

    def _formatWorkflowDeleteFailure(
        self, workflowId: str, error: KeyError | ValueError
    ) -> str:
        references = self.workflowStore.referencesTo(workflowId)
        if references:
            workflowName = self.workflowStore.get(workflowId).name
            referenceNames = [
                self.workflowStore.get(referenceId).name for referenceId in references
            ]
            return (
                f"无法删除工作流“{workflowName}”：它正被以下工作流引用："
                f"{', '.join(referenceNames)}。请先删除或重新配置相关引用节点。"
            )
        if isinstance(error, ValueError) and "at least one workflow" in str(error):
            return "无法删除工作流：项目必须至少保留一个工作流。"
        if isinstance(error, KeyError):
            return "无法删除工作流：工作流不存在。"
        return f"无法删除工作流：{error}"

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
        self.editWorkflowInterfaceFor(self.activeWorkflowId, inputs, outputs)

    def editWorkflowInterfaceFor(
        self,
        workflowId: str,
        inputs: dict[str, object] | None = None,
        outputs: dict[str, object] | None = None,
    ) -> None:
        try:
            workflow = self.workflowStore.get(workflowId)
        except KeyError:
            self.appendRuntimeLog("ERROR", f"工作流接口设置失败：{workflowId} 不存在")
            return
        if inputs is None:
            inputs = self._promptInterfaceMap("输入接口", workflow.inputs)
        if inputs is None:
            return
        if outputs is None:
            outputs = self._promptInterfaceMap("输出接口", workflow.outputs)
        if outputs is None:
            return
        try:
            refreshReport = self.workflowController.setWorkflowInterface(
                workflowId, inputs, outputs
            )
        except (TypeError, ValueError) as err:
            self.appendRuntimeLog("ERROR", f"工作流接口设置失败：{err}")
            return
        self.appendRuntimeLog("INFO", f"工作流接口已更新：{workflowId}")
        if refreshReport:
            message = "接口同步时发现以下变化：\n" + "\n".join(refreshReport)
            self.appendRuntimeLog("WARN", message.replace("\n", " | "))
            QMessageBox.warning(self, "工作流引用已更新", message)
        self._refreshWorkflowTabs()

    def _promptInterfaceMap(
        self, label: str, current: dict[str, object]
    ) -> dict[str, object] | None:
        text, accepted = QInputDialog.getText(
            self,
            "设置工作流接口",
            f"{label} JSON",
            QLineEdit.Normal,
            json.dumps(current, ensure_ascii=True),
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

    def addSubflowNode(
        self,
        targetWorkflowId: str | None = None,
        sceneX: float | None = None,
        sceneY: float | None = None,
    ) -> str | None:
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
        self._addControlNodeToScene(nodeId, sceneX=sceneX, sceneY=sceneY)
        return nodeId

    def addRepeatNode(
        self,
        config: dict[str, object] | None = None,
        sceneX: float | None = None,
        sceneY: float | None = None,
    ) -> str | None:
        target = self._firstOtherWorkflow()
        if target is None:
            self.appendRuntimeLog("WARN", "请先创建 Repeat 的 body 工作流")
            return None
        loopConfig = config or {
            "contractVersion": 2,
            "mode": "repeat",
            "bodyWorkflowId": target,
            "repeatCount": 1,
            "maxIterations": 1,
            "timeoutMs": 0,
        }
        return self._addLoopNode(loopConfig, "Repeat", sceneX=sceneX, sceneY=sceneY)

    def addForEachNode(
        self,
        config: dict[str, object] | None = None,
        sceneX: float | None = None,
        sceneY: float | None = None,
    ) -> str | None:
        target = self._firstOtherWorkflow()
        if target is None:
            self.appendRuntimeLog("WARN", "请先创建 ForEach 的 body 工作流")
            return None
        loopConfig = config or {
            "contractVersion": 2,
            "mode": "foreach",
            "bodyWorkflowId": target,
            "maxIterations": 100,
            "timeoutMs": 0,
        }
        return self._addLoopNode(loopConfig, "ForEach", sceneX=sceneX, sceneY=sceneY)

    def addWhileNode(
        self,
        config: dict[str, object] | None = None,
        sceneX: float | None = None,
        sceneY: float | None = None,
    ) -> str | None:
        targets = self._otherWorkflowIds()
        if config is None:
            if len(targets) < 2:
                self.appendRuntimeLog(
                    "WARN", "While 需要独立的 condition 和 body 工作流"
                )
                return None
            config = self._findCompatibleWhileConfig(targets)
            if config is None:
                self.appendRuntimeLog(
                    "WARN",
                    "没有兼容的 While 工作流：Body 输入/输出必须同名同类型，"
                    "Condition 必须输出 continue:boolean",
                )
                return None
        return self._addLoopNode(config, "While", sceneX=sceneX, sceneY=sceneY)

    def _findCompatibleWhileConfig(
        self, workflowIds: list[str]
    ) -> dict[str, object] | None:
        for bodyWorkflowId in workflowIds:
            for conditionWorkflowId in workflowIds:
                if bodyWorkflowId == conditionWorkflowId:
                    continue
                candidate: dict[str, object] = {
                    "contractVersion": 2,
                    "mode": "while",
                    "conditionWorkflowId": conditionWorkflowId,
                    "bodyWorkflowId": bodyWorkflowId,
                    "maxIterations": 100,
                    "timeoutMs": 30000,
                }
                try:
                    contract = self.workflowController.previewLoopContract(candidate)
                except (KeyError, ValueError):
                    continue
                if not contract.issues:
                    return candidate
        return None

    def _addLoopNode(
        self,
        config: dict[str, object],
        title: str,
        sceneX: float | None = None,
        sceneY: float | None = None,
    ) -> str | None:
        nodeId = self.flowModel.addNode("", title, {}, {}, kind="loop")
        try:
            self.workflowController.configureLoopNode(nodeId, config)
        except (KeyError, ValueError) as err:
            self.flowModel.removeNode(nodeId)
            self.appendRuntimeLog("ERROR", f"{title} 节点创建失败：{err}")
            return None
        self._addControlNodeToScene(nodeId, sceneX=sceneX, sceneY=sceneY)
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

    def _addControlNodeToScene(
        self,
        nodeId: str,
        sceneX: float | None = None,
        sceneY: float | None = None,
    ) -> None:
        node = self.flowModel.nodes[nodeId]
        defaultX, defaultY = self.flowScene.nextNodePosition()
        x = defaultX if sceneX is None else float(sceneX)
        y = defaultY if sceneY is None else float(sceneY)
        self.flowScene.addFlowNode(
            FlowNodeViewModel(
                nodeId=nodeId,
                title=node.displayName,
                x=x,
                y=y,
                inputPorts=node.inputPorts,
                outputPorts=node.outputPorts,
                operatorId=node.operatorId,
                kind=node.kind,
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
        if hasattr(self, "layoutController"):
            self.applyResponsiveLayout()
        try:
            super().resizeEvent(event)
        except Exception:
            _ = event

    def showEvent(self, event) -> None:
        if _nativeQt:
            super().showEvent(event)
            QTimer.singleShot(0, lambda: fitWindowToScreen(self))
            if not getattr(self, "_screenSizingConnected", False) and self.windowHandle():
                self.windowHandle().screenChanged.connect(lambda _screen: QTimer.singleShot(0, lambda: fitWindowToScreen(self)))
                self._screenSizingConnected = True

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._saveRuntimeLogSettings()
        self.operatorEditorManager.closeAll()
        counterDialog = self._globalCountersDialog
        if counterDialog is not None:
            shutdown = getattr(counterDialog, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self.runtimeController.close()
        try:
            super().closeEvent(event)
        except Exception:
            accept = getattr(event, "accept", None)
            if callable(accept):
                accept()

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
        isCurrentJobEvent = (
            self.currentJobId is None and self._allowRuntimeEventsWithoutActiveJob
        ) or (self.currentJobId is not None and jobId == self.currentJobId)
        if not isCurrentJobEvent:
            return
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
            self._nodeRuntimeStateByWorkflowRun[
                (workflowId, workflowRunId, nodeIdRaw)
            ] = dict(info)
        if workflowId == self.activeWorkflowId or (
            workflowId == "" and (jobId == "" or jobId == self.currentJobId)
        ):
            self._nodeRuntimeState[nodeIdRaw] = info
            setNodeRuntimeState = getattr(self.flowScene, "setNodeRuntimeState", None)
            if callable(setNodeRuntimeState):
                setNodeRuntimeState(nodeIdRaw, status)
            self._refreshRuntimePanelView()

    def _restoreActiveWorkflowRuntimeState(self) -> None:
        expectedJobId = self.currentJobId
        resetRuntimeStates = getattr(self.flowScene, "resetRuntimeStates", None)
        if callable(resetRuntimeStates):
            resetRuntimeStates()
        self._nodeRuntimeState.clear()
        for nodeId in self.flowModel.nodes:
            candidates = [
                value
                for (
                    workflowId,
                    _runId,
                    candidateNodeId,
                ), value in self._nodeRuntimeStateByWorkflowRun.items()
                if workflowId == self.activeWorkflowId
                and candidateNodeId == nodeId
                and _runtimeStateBelongsToJob(value, expectedJobId)
            ]
            if not candidates:
                continue
            latest = max(candidates, key=_runtimeSequence)
            self._nodeRuntimeState[nodeId] = dict(latest)
            setNodeRuntimeState = getattr(self.flowScene, "setNodeRuntimeState", None)
            if callable(setNodeRuntimeState):
                setNodeRuntimeState(nodeId, str(latest.get("status", "IDLE")))

    def _resetRuntimeVisualState(self, clearHistory: bool = False) -> None:
        self._nodeRuntimeState.clear()
        if clearHistory:
            self._nodeRuntimeStateByRun.clear()
            self._nodeRuntimeStateByWorkflowRun.clear()
        resetRuntimeStates = getattr(self.flowScene, "resetRuntimeStates", None)
        if callable(resetRuntimeStates):
            resetRuntimeStates()

    def getToolbarGroupNames(self) -> list[str]:
        return ["项目", "运行", "编辑", "辅助"]

    def navigateToWorkflowDependencyItem(self, item, column: int = 0) -> None:
        getData = getattr(item, "data", None)
        if not callable(getData):
            return
        payload = getData(column, USER_ROLE)
        if not isinstance(payload, dict) or payload.get("itemType") != "workflow":
            return
        workflowId = payload.get("workflowId")
        if (
            not isinstance(workflowId, str)
            or workflowId not in self.workflowStore.workflows
        ):
            return
        self.activateWorkflow(workflowId)

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
            defaultX, defaultY = defaultNodePosition(node.kind)
            xValue = defaultX
            yValue = defaultY
            for rawNode in graphPayload.get("nodes", []):
                if not isinstance(rawNode, dict):
                    continue
                rawNodeId = rawNode.get("nodeId")
                if not isinstance(rawNodeId, str) or rawNodeId != node.nodeId:
                    continue
                xRaw = rawNode.get("x", defaultX)
                yRaw = rawNode.get("y", defaultY)
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
                    kind=node.kind,
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
            self._applyLoadedProjectState(loadedProjectPath, currentProjectDir)
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

        systemNodeKind = payload.get("systemNodeKind")
        if isinstance(systemNodeKind, str) and systemNodeKind != "":
            nodeId = self._addSystemNodeFromLibrary(
                systemNodeKind, sceneX=sceneX, sceneY=sceneY
            )
            if nodeId is not None:
                operatorId = str(payload.get("operatorId", systemNodeKind))
                self.appendRuntimeLog("INFO", f"已添加节点：{nodeId}")
                self._recordRecentOperator(operatorId)
            self.collapseOperatorBubble()
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
        defaultX, defaultY = self.flowScene.nextNodePosition()
        x = defaultX if sceneX is None else float(sceneX)
        y = defaultY if sceneY is None else float(sceneY)
        self.flowScene.addFlowNode(
            FlowNodeViewModel(
                nodeId=nodeId,
                title=displayName,
                x=x,
                y=y,
                inputPorts=inputPorts,
                outputPorts=outputPorts,
                operatorId=operatorId,
                kind="operator",
            )
        )
        self.appendRuntimeLog("INFO", f"已添加节点：{nodeId}")
        self._recordRecentOperator(operatorId)
        self.flowModel.selectNode(nodeId)
        self.flowScene.setNodeSelected(nodeId)
        self.onNodeSelectionChanged()
        self.refreshSidebarNodeList()
        self.updateToolbarState()
        self.collapseOperatorBubble()

    def _addSystemNodeFromLibrary(
        self,
        systemNodeKind: str,
        sceneX: float | None = None,
        sceneY: float | None = None,
    ) -> str | None:
        if systemNodeKind == "subflow":
            return self.addSubflowNode(sceneX=sceneX, sceneY=sceneY)
        if systemNodeKind == "loop:repeat":
            return self.addRepeatNode(sceneX=sceneX, sceneY=sceneY)
        if systemNodeKind == "loop:foreach":
            return self.addForEachNode(sceneX=sceneX, sceneY=sceneY)
        if systemNodeKind == "loop:while":
            return self.addWhileNode(sceneX=sceneX, sceneY=sceneY)
        self.appendRuntimeLog("ERROR", f"未知系统节点类型：{systemNodeKind}")
        return None

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
            if self.flowModel.isBoundaryNode(nodeId):
                self.appendRuntimeLog("WARN", "Workflow Input/Output 节点不能删除")
                continue
            self.flowModel.removeNode(nodeId)
            self.flowScene.removeFlowNode(nodeId)
            self.operatorEditorManager.closeNode(
                self._currentProjectId(), self.activeWorkflowId, nodeId
            )
            if self.activeParamNodeId == nodeId and self.nodeParamDialog is not None:
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

    def focusGraphContent(self, checked: bool = False, *, automatic: bool = False) -> None:
        workflowId = (str(self.workflowStore.project.get("projectId", "")), self.workflowStore.activeWorkflowId)
        if automatic and self._focusedWorkflowId == workflowId:
            return
        if automatic and _nativeQt:
            if self._focusedWorkflowId is not None:
                center = self.flowView.mapToScene(self.flowView.viewport().rect().center())
                self._workflowViewStates[self._focusedWorkflowId] = (self.flowView.getZoomFactor(), center)
            self._focusedWorkflowId = workflowId
            state = self._workflowViewStates.get(workflowId)
            if state is not None:
                self.flowView.setZoomFactor(state[0])
                self.flowView.centerOn(state[1])
                return
        getContentBounds = getattr(self.flowScene, "getContentBounds", None)
        if not callable(getContentBounds):
            return
        bounds = getContentBounds()
        if not isinstance(bounds, tuple) or len(bounds) != 4:
            return
        self.flowView.fitContent(bounds, minimumZoom=0.85 if automatic else 0.02)

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
            self.nodeDetailTitleCard.setText("未选中节点")
            self.nodeDetailMetaCard.setText("")
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

        self.previewImageLabel.setPixmap(pixmap)

    def openLogDialog(self) -> None:
        if self.logDock is None:
            self.logDock = RuntimeLogDock(
                self,
                onClear=self._clearRuntimeLogCache,
                maximumEntries=DEFAULT_MAX_LOG_ENTRIES,
            )
            self.logDialog = self.logDock
            addDockWidget = getattr(self, "addDockWidget", None)
            if callable(addDockWidget):
                addDockWidget(Qt.BottomDockWidgetArea, self.logDock)
            self.logDock.setEntries(self.logEntries)
        self.logDock.show()
        raiseWindow = getattr(self.logDock, "raise_", None)
        if callable(raiseWindow):
            raiseWindow()
        activateWindow = getattr(self.logDock, "activateWindow", None)
        if callable(activateWindow):
            activateWindow()

    def appendRuntimeLog(self, level: str, message: str) -> None:
        self._appendLocalLog("designer", level, message)

    def appendEditorLog(self, level: str, message: str) -> None:
        self._appendLocalLog("editor", level, message)

    def _appendLocalLog(self, source: str, level: str, message: str) -> None:
        now = datetime.now()
        normalizedLevel = str(level).strip().upper()
        if normalizedLevel == "WARNING":
            normalizedLevel = "WARN"
        if normalizedLevel not in {"DEBUG", "INFO", "WARN", "ERROR"}:
            normalizedLevel = "INFO"
        entry = StructuredLogEntry(
            timestampMs=int(now.timestamp() * 1000.0),
            level=normalizedLevel,
            message=str(message),
            source=source,
        )
        logLine = f"[{now.strftime('%H:%M:%S')}][{normalizedLevel}] {message}"
        self._storeRuntimeLogEntry(entry, logLine)
        if self.logDock is not None:
            self.logDock.appendEntry(entry)

    def appendRuntimeEvent(self, event: dict[str, object]) -> None:
        entry = StructuredLogEntry.fromRuntimeEvent(event)
        self._storeRuntimeLogEntry(entry, entry.displayLine())
        if self.logDock is not None:
            self.logDock.appendEntry(entry)

    def _storeRuntimeLogEntry(
        self,
        entry: StructuredLogEntry,
        line: str,
    ) -> None:
        self.logEntries.append(entry)
        self.logBuffer.append(line)
        overflow = len(self.logEntries) - DEFAULT_MAX_LOG_ENTRIES
        if overflow > 0:
            del self.logEntries[:overflow]
            del self.logBuffer[:overflow]

    def _clearRuntimeLogCache(self) -> None:
        self.logEntries.clear()
        self.logBuffer.clear()

    def _saveRuntimeLogSettings(self) -> None:
        dock = self.logDock
        setValue = getattr(self.settingsStore, "setValue", None)
        if dock is None or not callable(setValue):
            return
        saveState = getattr(self, "saveState", None)
        if callable(saveState):
            setValue("ui/runtime_log_dock_state", saveState())
        saveGeometry = getattr(dock, "saveGeometry", None)
        if callable(saveGeometry):
            setValue("ui/runtime_log_dock_geometry", saveGeometry())
        setValue("ui/runtime_log_dock_visible", bool(dock.isVisible()))
        setValue("ui/runtime_log_dock_floating", bool(dock.isFloating()))
        setValue("ui/runtime_log_view", dock.view.settings())
        sync = getattr(self.settingsStore, "sync", None)
        if callable(sync):
            sync()

    def _restoreRuntimeLogSettings(self) -> None:
        dock = self.logDock
        value = getattr(self.settingsStore, "value", None)
        if dock is None or not callable(value):
            return
        state = value("ui/runtime_log_dock_state", None)
        restoreState = getattr(self, "restoreState", None)
        if state is not None and callable(restoreState):
            restoreState(state)
        geometry = value("ui/runtime_log_dock_geometry", None)
        restoreGeometry = getattr(dock, "restoreGeometry", None)
        if geometry is not None and callable(restoreGeometry):
            restoreGeometry(geometry)
        dock.setFloating(
            _settingBool(value("ui/runtime_log_dock_floating", False), False)
        )
        viewSettings = value("ui/runtime_log_view", {})
        if isinstance(viewSettings, dict):
            dock.view.restoreSettings(viewSettings)
        if _settingBool(value("ui/runtime_log_dock_visible", False), False):
            dock.show()
        else:
            dock.hide()

    def updateToolbarState(self) -> None:
        canRun = self.loadedProjectPath is not None and not self.isJobRunning
        canStop = self.isJobRunning and self.currentJobId is not None

        self.startButton.setEnabled(canRun)
        self.stopButton.setEnabled(canStop)
        self.validateButton.setEnabled(self.loadedProjectPath is not None)
        for name, enabled in (("开始运行", canRun), ("停止运行", canStop),
                              ("校验项目", self.loadedProjectPath is not None)):
            if name in self._toolbarActions:
                self._toolbarActions[name].setEnabled(enabled)
        self._updateRunBlockedHint(canRun)

    def _updateRunBlockedHint(self, canRun: bool) -> None:
        blockedReason = ""
        if self.loadedProjectPath is None:
            blockedReason = "无法运行：未加载项目，请先打开或新建项目"
        elif self.isJobRunning:
            blockedReason = "无法运行：当前作业仍在运行，请先停止作业"

        setToolTip = getattr(self.startButton, "setToolTip", None)
        if callable(setToolTip):
            setToolTip("开始运行" if canRun else blockedReason)
        if "开始运行" in self._toolbarActions:
            self._toolbarActions["开始运行"].setToolTip("开始运行" if canRun else blockedReason)

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
        operators = self.operatorCatalogController.getOperatorsByCategory(
            self.activeOperatorCategory
        )
        if self.activeOperatorCategory not in {"全部", "控制流"}:
            return operators
        return operators + self._getSystemNodeLibraryPayloads()

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

        self.activeParamNodeId = nodeId
        schema = self._nodeEditorSchema(node)
        values = dict(node.params)
        if node.kind == "subflow":
            values["targetWorkflowId"] = node.targetWorkflowId or ""
        elif node.kind == "loop":
            values.update(node.loop)
        window = self.operatorEditorManager.open(
            projectId=self._currentProjectId(),
            workflowId=self.activeWorkflowId,
            nodeId=nodeId,
            operatorId=node.operatorId or node.displayName,
            displayName=node.displayName,
            schema=schema,
            values=values,
            operatorDefinition=self._operatorDefinition(node.operatorId),
            workflowOptions=self._otherWorkflowIds(),
            parent=self,
        )
        # Kept as an alias for integrations that still inspect the last opened
        # parameter window. Ownership and uniqueness now live in the manager.
        self.nodeParamDialog = cast(NodeParamDialog, window)

    def applyNodeParams(self, nodeId: str, params: dict[str, object]) -> bool:
        if nodeId not in self.flowModel.nodes:
            self.appendRuntimeLog("ERROR", "参数应用失败：节点不存在")
            return False
        node = self.flowModel.nodes[nodeId]
        try:
            if node.kind == "subflow":
                targetWorkflowId = params.get("targetWorkflowId")
                if not isinstance(targetWorkflowId, str) or targetWorkflowId == "":
                    raise ValueError("请选择 Subflow 目标工作流")
                self.workflowController.configureSubflowNode(nodeId, targetWorkflowId)
            elif node.kind == "loop":
                loopParams = dict(params)
                loopParams["mode"] = node.loop.get("mode")
                loopParams["contractVersion"] = 2
                self.workflowController.configureLoopNode(nodeId, loopParams)
            else:
                self.flowModel.setNodeParams(nodeId, params)
        except (KeyError, TypeError, ValueError) as err:
            self.appendRuntimeLog("ERROR", f"节点配置失败：{err}")
            return False
        if node.kind in {"subflow", "loop"}:
            refresh = getattr(self.workflowController, "refreshActiveWorkflow", None)
            if callable(refresh):
                refresh()
            self.flowModel.selectNode(nodeId)
            self.flowScene.setNodeSelected(nodeId)
            self.refreshSidebarNodeList()
            self.onNodeSelectionChanged()
        self.appendRuntimeLog("INFO", f"参数已更新：{nodeId}")
        self.updateToolbarState()
        self._refreshRuntimePanelView()
        return True

    def updateNodeParams(self, nodeId: str, params: dict[str, object]) -> None:
        self.applyNodeParams(nodeId, params)

    def _applyEditorParams(
        self, key: EditorKey, params: dict[str, object]
    ) -> bool:
        if key.projectId != self._currentProjectId():
            self.appendRuntimeLog("ERROR", "参数应用失败：编辑器所属项目已关闭")
            return False
        if key.workflowId == self.activeWorkflowId:
            return self.applyNodeParams(key.nodeId, params)
        workflow = self.workflowStore.workflows.get(key.workflowId)
        if workflow is None:
            self.appendRuntimeLog("ERROR", "参数应用失败：编辑器所属工作流已关闭")
            return False
        for rawNode in workflow.nodes:
            if rawNode.get("nodeId") != key.nodeId:
                continue
            if rawNode.get("kind", "operator") != "operator":
                self.appendRuntimeLog(
                    "ERROR", "参数应用失败：请切回对应工作流配置控制节点"
                )
                return False
            rawNode["params"] = dict(params)
            self.appendRuntimeLog("INFO", f"参数已更新：{key.nodeId}")
            self.updateToolbarState()
            return True
        self.appendRuntimeLog("ERROR", "参数应用失败：节点不存在")
        return False

    def _currentProjectId(self) -> str:
        projectId = self.workflowStore.project.get("projectId", "")
        return str(projectId) if projectId is not None else ""

    def _operatorDefinition(self, operatorId: str) -> dict[str, object]:
        for definition in self.operatorCatalogController.getCatalog():
            if definition.get("operatorId") == operatorId:
                return dict(definition)
        return {}

    def _nodeEditorSchema(self, node) -> dict[str, object]:
        options = self._otherWorkflowIds()
        workflowSelect = {
            "type": "string",
            "xWidget": "workflow-select",
            "xOptions": options,
        }
        if node.kind == "subflow":
            return {
                "type": "object",
                "properties": {"targetWorkflowId": workflowSelect},
                "required": ["targetWorkflowId"],
            }
        if node.kind != "loop":
            return node.paramSchema
        mode = node.loop.get("mode")
        modeValue = mode if isinstance(mode, str) else "repeat"
        properties: dict[str, object] = {
            "mode": {"type": "string", "enum": [modeValue]},
            "bodyWorkflowId": workflowSelect,
            "repeatCount": {"type": "integer", "minimum": 0},
            "maxIterations": {
                "type": "integer",
                "minimum": 1 if modeValue == "while" else 0,
            },
            "timeoutMs": {"type": "integer", "minimum": 0},
            "conditionWorkflowId": workflowSelect,
        }
        required = ["mode", "bodyWorkflowId", "maxIterations", "timeoutMs"]
        if modeValue == "repeat":
            required.append("repeatCount")
        elif modeValue == "while":
            required.append("conditionWorkflowId")
        elif modeValue == "foreach":
            bodyWorkflowId = node.loop.get("bodyWorkflowId")
            body = (
                self.workflowStore.workflows.get(bodyWorkflowId)
                if isinstance(bodyWorkflowId, str)
                else None
            )
            inputNames = list(body.inputs) if body is not None else []
            integerInputs = [
                name
                for name, portType in (body.inputs.items() if body is not None else [])
                if portType == "integer"
                or (isinstance(portType, dict) and portType.get("type") == "integer")
            ]
            properties["itemInputPort"] = {
                "type": "string",
                "enum": inputNames,
            }
            properties["indexInputPort"] = {
                "type": "string",
                "enum": ["", *integerInputs],
                "default": "",
            }
            if inputNames:
                required.append("itemInputPort")
        return {"type": "object", "properties": properties, "required": required}
