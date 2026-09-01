from __future__ import annotations

from typing import Callable


class LayoutController:
    def __init__(
        self,
        mainSplitter,
        sidebarContainer,
        rightPanelContainer,
        canvasPanel,
        dependencyTreeWidget,
        nodeListWidget,
        runtimeStatusOutput,
        previewImageLabel,
        sidebarToggleButton,
        categoryPanel,
        dependencyTreeContainer,
        nodeListContainer,
        mainMenuBar,
        settingsStore,
        widthGetter: Callable[[], int],
        isSidebarCollapsedGetter: Callable[[], bool],
    ) -> None:
        self.mainSplitter = mainSplitter
        self.sidebarContainer = sidebarContainer
        self.rightPanelContainer = rightPanelContainer
        self.canvasPanel = canvasPanel
        self.dependencyTreeWidget = dependencyTreeWidget
        self.nodeListWidget = nodeListWidget
        self.runtimeStatusOutput = runtimeStatusOutput
        self.previewImageLabel = previewImageLabel
        self.sidebarToggleButton = sidebarToggleButton
        self.categoryPanel = categoryPanel
        self.dependencyTreeContainer = dependencyTreeContainer
        self.nodeListContainer = nodeListContainer
        self.mainMenuBar = mainMenuBar
        self.settingsStore = settingsStore
        self.widthGetter = widthGetter
        self.isSidebarCollapsedGetter = isSidebarCollapsedGetter

        self._splitterSizesInitialized = False
        self._currentSplitterSizes = [228, 900, 340]
        self._expandedSplitterSizes = [228, 900, 340]
        self._sidebarExpandedWidth = 228
        self._sidebarCollapsedWidth = 36
        self._sidebarMinWidth = 180
        self._canvasMinWidth = 520
        self._canvasMaxWidth = -1
        self._rightPanelMinWidth = 300
        self._rightPanelWidth = 340
        self._layoutMode = "normal"
        self._menuBarFontSize = 13

    def getMainSplitterSizes(self) -> list[int]:
        return [int(size) for size in self._currentSplitterSizes]

    def saveMainSplitterSizes(self, sizes: list[int]) -> None:
        validSizes = [int(size) for size in sizes[:3]]
        if len(validSizes) != 3:
            return
        self._currentSplitterSizes = list(validSizes)
        self._expandedSplitterSizes = list(validSizes)
        setValue = getattr(self.settingsStore, "setValue", None)
        if callable(setValue):
            setValue("ui/main_splitter_sizes", validSizes)
        setSizes = getattr(self.mainSplitter, "setSizes", None)
        if callable(setSizes):
            setSizes(validSizes)

    def restoreMainSplitterSizes(self) -> None:
        valueMethod = getattr(self.settingsStore, "value", None)
        if callable(valueMethod):
            stored = valueMethod("ui/main_splitter_sizes", None)
            if isinstance(stored, list) and len(stored) == 3:
                parsed: list[int] = []
                for item in stored:
                    if isinstance(item, (int, float)):
                        parsed.append(int(item))
                if len(parsed) == 3:
                    self._currentSplitterSizes = list(parsed)
                    self._expandedSplitterSizes = list(parsed)
                    setSizes = getattr(self.mainSplitter, "setSizes", None)
                    if callable(setSizes):
                        setSizes(parsed)
                    self._splitterSizesInitialized = True
                    return
        defaultSizes = [self._sidebarExpandedWidth, 900, self._rightPanelWidth]
        self._currentSplitterSizes = list(defaultSizes)
        self._expandedSplitterSizes = list(defaultSizes)
        setSizes = getattr(self.mainSplitter, "setSizes", None)
        if callable(setSizes):
            setSizes(defaultSizes)
        self._splitterSizesInitialized = True

    def onMainSplitterMoved(self, pos: int, index: int) -> None:
        _ = pos
        _ = index
        sizesMethod = getattr(self.mainSplitter, "sizes", None)
        if callable(sizesMethod):
            rawSizes = sizesMethod()
            if isinstance(rawSizes, list):
                parsed: list[int] = []
                for item in rawSizes:
                    if isinstance(item, (int, float)):
                        parsed.append(int(item))
                if len(parsed) == 3:
                    self.saveMainSplitterSizes(parsed)

    def applyResponsiveLayout(self) -> None:
        windowWidth = int(self.widthGetter())
        isLarge = windowWidth >= 1600
        self._layoutMode = "large" if isLarge else "normal"
        self._sidebarExpandedWidth = 248 if isLarge else 228
        self._rightPanelWidth = 320 if isLarge else 340
        self._menuBarFontSize = 15 if isLarge else 13
        dependencyTreeHeight = 180 if isLarge else 150
        nodeListHeight = 130 if isLarge else 110
        statusHeight = 100 if isLarge else 120
        previewHeight = 260 if isLarge else 220

        setSidebarMinWidth = getattr(self.sidebarContainer, "setMinimumWidth", None)
        if callable(setSidebarMinWidth):
            setSidebarMinWidth(self._sidebarMinWidth)

        setRightMinWidth = getattr(self.rightPanelContainer, "setMinimumWidth", None)
        if callable(setRightMinWidth):
            setRightMinWidth(self._rightPanelMinWidth)

        setCanvasMinWidth = getattr(self.canvasPanel, "setMinimumWidth", None)
        if callable(setCanvasMinWidth):
            setCanvasMinWidth(self._canvasMinWidth)

        setDependencyTreeHeight = getattr(
            self.dependencyTreeWidget, "setFixedHeight", None
        )
        if callable(setDependencyTreeHeight):
            setDependencyTreeHeight(dependencyTreeHeight)

        setNodeListHeight = getattr(self.nodeListWidget, "setFixedHeight", None)
        if callable(setNodeListHeight):
            setNodeListHeight(nodeListHeight)

        setStatusHeight = getattr(self.runtimeStatusOutput, "setFixedHeight", None)
        if callable(setStatusHeight):
            setStatusHeight(statusHeight)

        setPreviewMinHeight = getattr(self.previewImageLabel, "setMinimumHeight", None)
        if callable(setPreviewMinHeight):
            setPreviewMinHeight(previewHeight)

        setCanvasMaxWidth = getattr(self.canvasPanel, "setMaximumWidth", None)
        if callable(setCanvasMaxWidth):
            setCanvasMaxWidth(16777215)

        menuBarStyle = f"QMenuBar {{ font-size: {self._menuBarFontSize}px; }} QMenu {{ font-size: {self._menuBarFontSize}px; }}"
        setMenuBarStyle = getattr(self.mainMenuBar, "setStyleSheet", None)
        if callable(setMenuBarStyle):
            setMenuBarStyle(menuBarStyle)

        if not self._splitterSizesInitialized:
            self.restoreMainSplitterSizes()

    def applySidebarState(self) -> None:
        if self.isSidebarCollapsedGetter():
            self.categoryPanel.setVisible(False)
            self.dependencyTreeContainer.setVisible(False)
            self.nodeListContainer.setVisible(False)
            sizes = list(self._expandedSplitterSizes)
            if len(sizes) == 3:
                sizes[0] = self._sidebarCollapsedWidth
                self._currentSplitterSizes = list(sizes)
                self.mainSplitter.setSizes(sizes)
            setSidebarMinWidth = getattr(self.sidebarContainer, "setMinimumWidth", None)
            if callable(setSidebarMinWidth):
                setSidebarMinWidth(self._sidebarCollapsedWidth)
            setSidebarMaxWidth = getattr(self.sidebarContainer, "setMaximumWidth", None)
            if callable(setSidebarMaxWidth):
                setSidebarMaxWidth(self._sidebarCollapsedWidth)
            self.sidebarToggleButton.setText(">")
            return

        self.categoryPanel.setVisible(True)
        self.dependencyTreeContainer.setVisible(True)
        self.nodeListContainer.setVisible(True)
        setSidebarMinWidth = getattr(self.sidebarContainer, "setMinimumWidth", None)
        if callable(setSidebarMinWidth):
            setSidebarMinWidth(self._sidebarMinWidth)
        setSidebarMaxWidth = getattr(self.sidebarContainer, "setMaximumWidth", None)
        if callable(setSidebarMaxWidth):
            setSidebarMaxWidth(16777215)
        sizes = list(self._expandedSplitterSizes)
        if len(sizes) == 3:
            sizes[0] = max(sizes[0], self._sidebarExpandedWidth)
            self._currentSplitterSizes = list(sizes)
            self.mainSplitter.setSizes(sizes)
        self.sidebarToggleButton.setText("<")

    def getLayoutMode(self) -> str:
        return self._layoutMode

    def getPanelWidths(self) -> dict[str, int]:
        return {
            "sidebar": int(self._sidebarExpandedWidth),
            "rightPanel": int(self._rightPanelWidth),
        }

    def getPanelConstraints(self) -> dict[str, int]:
        return {
            "sidebarMinWidth": int(self._sidebarMinWidth),
            "canvasMinWidth": int(self._canvasMinWidth),
            "canvasMaxWidth": int(self._canvasMaxWidth),
            "rightPanelMinWidth": int(self._rightPanelMinWidth),
            "rightPanelDefaultWidth": int(self._rightPanelWidth),
        }

    def getMenuBarFontSize(self) -> int:
        return int(self._menuBarFontSize)
