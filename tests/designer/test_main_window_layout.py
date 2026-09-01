from emo_master.apps.designer.ui.main_window import MainWindow


def ensureQApp() -> None:
    try:
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            _ = QApplication([])
    except Exception:
        pass


class RuntimeClientStub:
    def listOperators(self):
        return []


class SettingsStoreStub:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default=None):
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value


def testMainWindowUsesExpectedToolbarGroups() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getToolbarGroups = getattr(window, "getToolbarGroups", None)
    assert callable(getToolbarGroups)
    groups = getToolbarGroups()
    assert groups == [
        ["加载项目", "保存项目", "校验项目"],
        ["开始运行", "停止运行"],
        ["自动布局", "校验流程图"],
        ["刷新算子", "打开日志"],
    ]
    getToolbarGroupNames = getattr(window, "getToolbarGroupNames", None)
    assert callable(getToolbarGroupNames)
    groupNames = getToolbarGroupNames()
    assert groupNames == ["项目", "运行", "编辑", "辅助"]

    getMenuBarGroups = getattr(window, "getMenuBarGroups", None)
    assert callable(getMenuBarGroups)
    assert getMenuBarGroups() == ["文件", "运行", "编辑", "视图"]


def testMainWindowUsesBalancedPanelWidths() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getPanelWidths = getattr(window, "getPanelWidths", None)
    assert callable(getPanelWidths)
    widths = getPanelWidths()
    assert isinstance(widths, dict)
    assert widths["sidebar"] >= 200
    assert widths["rightPanel"] >= 320
    getLayoutMode = getattr(window, "getLayoutMode", None)
    assert callable(getLayoutMode)
    assert getLayoutMode() in ["normal", "large"]


def testMainWindowDelegatesLayoutStateToController() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    layoutController = getattr(window, "layoutController", None)
    assert layoutController is not None
    getSplitterSizes = getattr(layoutController, "getMainSplitterSizes", None)
    assert callable(getSplitterSizes)
    sizes = getSplitterSizes()
    assert isinstance(sizes, list)
    assert len(sizes) == 3


def testMainWindowDelegatesOperatorCatalogToController() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    operatorCatalogController = getattr(window, "operatorCatalogController", None)
    assert operatorCatalogController is not None
    getOperatorsByCategory = getattr(
        operatorCatalogController, "getOperatorsByCategory", None
    )
    assert callable(getOperatorsByCategory)


def testMainWindowDoesNotKeepLegacyLayoutStateFields() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    assert not hasattr(window, "_sidebarExpandedWidth")
    assert not hasattr(window, "_rightPanelWidth")
    assert not hasattr(window, "_canvasMaxWidth")


def testMainWindowUsesCompactCategoryLabelsAndRightSections() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getCategoryButtonLabels = getattr(window, "getCategoryButtonLabels", None)
    assert callable(getCategoryButtonLabels)
    labels = getCategoryButtonLabels()
    assert isinstance(labels, dict)
    assert "全部" in labels
    assert "控制流" in labels
    assert labels["全部"].startswith("◌")
    assert labels["输出"].startswith("⬒")

    getRightPanelSections = getattr(window, "getRightPanelSections", None)
    assert callable(getRightPanelSections)
    sections = getRightPanelSections()
    assert sections == ["运行摘要", "当前节点", "结果预览"]


def testMainWindowClassifiesFlowOperatorsAsControlFlow() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    classifyOperator = getattr(window, "_classifyOperator", None)
    assert callable(classifyOperator)
    assert classifyOperator("vision.flow.if") == "控制流"
    assert classifyOperator("vision.flow.switch") == "控制流"


def testMainWindowUsesHorizontalSplitterAndPersistsSizes() -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    getSplitterSizes = getattr(window, "getMainSplitterSizes", None)
    assert callable(getSplitterSizes)
    sizes = getSplitterSizes()
    assert isinstance(sizes, list)
    assert len(sizes) == 3
    assert sizes[0] > 0
    assert sizes[1] > 0
    assert sizes[2] > 0

    saveSplitterSizes = getattr(window, "saveMainSplitterSizes", None)
    assert callable(saveSplitterSizes)
    saveSplitterSizes([260, 900, 360])
    assert settings.value("ui/main_splitter_sizes") == [260, 900, 360]

    restoredWindow = MainWindow(RuntimeClientStub(), settingsStore=settings)
    restoredSizes = restoredWindow.getMainSplitterSizes()
    assert restoredSizes == [260, 900, 360]


def testMainWindowKeepsRightPanelMinWidthIndependentFromDefaultWidth() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getPanelConstraints = getattr(window, "getPanelConstraints", None)
    assert callable(getPanelConstraints)
    constraints = getPanelConstraints()
    assert constraints["rightPanelMinWidth"] == 300
    assert constraints["rightPanelDefaultWidth"] in [320, 340]


def testCollapsedSidebarUsesLiveSplitterSizes() -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    settings.setValue("ui/main_splitter_sizes", [260, 900, 360])
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)

    setSizes = getattr(window.mainSplitter, "setSizes", None)
    assert callable(setSizes)
    setSizes([36, 980, 300])
    onMainSplitterMoved = getattr(window, "onMainSplitterMoved", None)
    assert callable(onMainSplitterMoved)
    onMainSplitterMoved(0, 0)

    window.collapseSidebar()
    sizes = window.getMainSplitterSizes()
    assert sizes[0] == 36
    assert sizes[2] <= 300


def testMainWindowDoesNotLimitCanvasMaxWidth() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getPanelConstraints = getattr(window, "getPanelConstraints", None)
    assert callable(getPanelConstraints)
    constraints = getPanelConstraints()
    assert constraints["canvasMaxWidth"] == -1


def testRuntimeLogDockIsBottomDockedAndRestoresLocalSettings() -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    assert window.logDock is not None
    assert window.logDock.isVisible() is False

    window.appendRuntimeLog("INFO", "designer message")
    window.appendEditorLog("WARN", "editor message")
    window.appendRuntimeEvent({
        "timestampMs": 123,
        "level": "ERROR",
        "message": "runtime message",
        "eventType": "node.log",
        "jobId": "job",
        "workflowId": "main",
        "nodeId": "node",
        "payload": {"operatorId": "test.operator"},
    })
    assert [entry.source for entry in window.logEntries[-3:]] == [
        "designer", "editor", "runtime"
    ]

    window.openLogDialog()
    window.logDock.setFloating(True)
    window._saveRuntimeLogSettings()
    assert settings.value("ui/runtime_log_dock_visible") is True
    assert settings.value("ui/runtime_log_dock_floating") is True
    assert isinstance(settings.value("ui/runtime_log_view"), dict)

    restored = MainWindow(RuntimeClientStub(), settingsStore=settings)
    assert restored.logDock is not None
    assert restored.logDock.isVisible() is True
    assert restored.logDock.isFloating() is True


def testRuntimeLogDockClearAlsoReleasesMainWindowViewCache() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    assert window.logDock is not None
    window.appendRuntimeLog("INFO", "cached message")
    assert window.logEntries
    assert window.logBuffer

    window.logDock.view.clearView()

    assert window.logEntries == []
    assert window.logBuffer == []
