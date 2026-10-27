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
    assert getMenuBarGroups() == ["文件", "运行", "编辑", "视图", "Runtime", "设备管理"]


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
    assert labels["全部"] == "全部"
    assert labels["输出"] == "输出"

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
    assert sizes[0] == 0 if hasattr(window, 'floatingToolbox') else sizes[0] > 0
    assert sizes[1] > 0
    assert sizes[2] == 0 if hasattr(window, 'floatingRunPanel') else sizes[2] > 0

    saveSplitterSizes = getattr(window, "saveMainSplitterSizes", None)
    assert callable(saveSplitterSizes)
    saveSplitterSizes([260, 900, 360])
    assert settings.value("ui/main_splitter_sizes") == [260, 900, 360]

    restoredWindow = MainWindow(RuntimeClientStub(), settingsStore=settings)
    restoredSizes = restoredWindow.getMainSplitterSizes()
    assert restoredSizes[0] == (0 if hasattr(restoredWindow, 'floatingToolbox') else 36)
    assert restoredWindow.layoutController._expandedSplitterSizes == [260, 900, 360]


def testMainWindowKeepsRightPanelMinWidthIndependentFromDefaultWidth() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getPanelConstraints = getattr(window, "getPanelConstraints", None)
    assert callable(getPanelConstraints)
    constraints = getPanelConstraints()
    assert constraints["rightPanelMinWidth"] == 260
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
    assert sizes[0] == (0 if hasattr(window, 'floatingToolbox') else 36)
    assert sizes[2] <= 300


def testMainWindowDoesNotLimitCanvasMaxWidth() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getPanelConstraints = getattr(window, "getPanelConstraints", None)
    assert callable(getPanelConstraints)
    constraints = getPanelConstraints()
    assert constraints["canvasMaxWidth"] == -1


def testRuntimeLogDialogIsNonModalAndRestoresLocalSettings() -> None:
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QDialog, QDockWidget

    ensureQApp()
    settings = SettingsStoreStub()
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    dialog = window.logDialog
    assert isinstance(dialog, QDialog)
    assert dialog.parentWidget() is window
    assert dialog.isWindow()
    assert dialog.isModal() is False
    assert dialog.windowModality() == Qt.NonModal
    assert dialog.windowFlags() & Qt.WindowMinMaxButtonsHint
    assert dialog.isVisible() is False
    assert window.findChild(QDockWidget, "runtimeLogDock") is None

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
    dialog.resize(1120, 660)
    dialog.view.sourceFilter.setCurrentText("runtime")
    dialog.view.autoScrollCheck.setChecked(False)
    window._saveRuntimeLogSettings()
    assert settings.value("ui/runtime_log_dialog_visible") is True
    assert settings.value("ui/runtime_log_dialog_geometry") is not None
    assert settings.value("ui/runtime_log_dock_state") is None
    assert settings.value("ui/runtime_log_dock_floating") is None
    assert isinstance(settings.value("ui/runtime_log_view"), dict)

    restored = MainWindow(RuntimeClientStub(), settingsStore=settings)
    assert restored.logDialog.isVisible() is True
    assert restored.logDialog.size() == dialog.size()
    assert restored.logDialog.view.settings()["source"] == "runtime"
    assert restored.logDialog.view.settings()["autoScroll"] is False


def testRuntimeLogDialogClearAlsoReleasesMainWindowViewCache() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    assert window.logDialog is not None
    window.appendRuntimeLog("INFO", "cached message")
    assert window.logEntries
    assert window.logBuffer

    window.logDialog.view.clearView()

    assert window.logEntries == []
    assert window.logBuffer == []


def testRuntimeLogDialogReusesWindowWithoutResizingCanvasOrLosingLogs(
    designerApplication,
) -> None:
    from tests.designer.qt_wait import waitUntil

    window = MainWindow(RuntimeClientStub(), settingsStore=SettingsStoreStub())
    window.show()
    designerApplication.processEvents()
    # The asynchronous catalog contributes a real log before this test's history.
    waitUntil(lambda: window.operatorCatalogController.state == "ready")
    canvasSize = window.centralWidget().size()
    window.logDialog.view.clearView()
    window.appendRuntimeLog("INFO", "first message")

    window.openLogDialog()
    designerApplication.processEvents()
    dialog = window.logDialog
    assert window.centralWidget().size() == canvasSize
    dialog.view.sourceFilter.setCurrentText("designer")
    dialog.view.autoScrollCheck.setChecked(False)
    dialog.view.table.setCurrentCell(0, 0)
    assert "first message" in dialog.view.detailViewer.toPlainText()

    dialog.close()
    assert not dialog.isVisible()
    window.appendEditorLog("WARN", "hidden editor message")
    window.appendRuntimeLog("INFO", "message while closed")
    dialog.view._applyPendingEntries()
    window.openLogDialog()
    designerApplication.processEvents()

    assert window.logDialog is dialog
    assert dialog.isVisible()
    assert window.centralWidget().size() == canvasSize
    assert [entry.message for entry in dialog.view._entries] == [
        "first message", "hidden editor message", "message while closed",
    ]
    assert dialog.view.sourceFilter.currentText() == "designer"
    assert dialog.view.autoScrollCheck.isChecked() is False
    assert dialog.view.table.rowCount() == 2
    assert "first message" in dialog.view.detailViewer.toPlainText()


def testRuntimeLogDialogOpenRestoresMinimizedWindowButKeepsMaximized(
    designerApplication,
) -> None:
    window = MainWindow(RuntimeClientStub(), settingsStore=SettingsStoreStub())
    window.openLogDialog()
    dialog = window.logDialog
    dialog.showMinimized()
    designerApplication.processEvents()
    assert dialog.isMinimized()

    window.openLogDialog()
    assert dialog.isVisible()
    assert not dialog.isMinimized()

    dialog.showMaximized()
    window.openLogDialog()
    assert dialog.isMaximized()
    dialog.showMinimized()
    designerApplication.processEvents()
    assert dialog.isMinimized()
    window.openLogDialog()
    assert not dialog.isMinimized()
    assert dialog.isMaximized()


def testRuntimeLogDialogIgnoresOldDockLayoutButKeepsViewSettings(monkeypatch) -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    settings.values.update({
        "ui/runtime_log_dock_state": b"old main-window layout",
        "ui/runtime_log_dock_geometry": b"old dock geometry",
        "ui/runtime_log_dock_visible": True,
        "ui/runtime_log_dock_floating": False,
        "ui/runtime_log_view": {"level": "ERROR", "autoScroll": False},
    })
    restoredStates = []
    monkeypatch.setattr(MainWindow, "restoreState", restoredStates.append)

    window = MainWindow(RuntimeClientStub(), settingsStore=settings)

    assert not restoredStates
    assert not window.logDialog.isVisible()
    assert window.logDialog.view.settings()["level"] == "ERROR"
    assert window.logDialog.view.settings()["autoScroll"] is False
    window.openLogDialog()
    assert window.logDialog.isVisible()
    assert window.logDialog.isWindow()


def testClosingDesignerAlsoClosesLogDialog(designerApplication) -> None:
    settings = SettingsStoreStub()
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    window.show()
    window.openLogDialog()
    designerApplication.processEvents()
    assert window.logDialog.isVisible()

    assert window.close()

    assert not window.logDialog.isVisible()
    assert settings.value("ui/runtime_log_dialog_visible") is True
