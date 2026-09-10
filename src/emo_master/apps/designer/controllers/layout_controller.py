from __future__ import annotations

from typing import Callable


class LayoutController:
    def __init__(self, mainSplitter, sidebarContainer, rightPanelContainer, canvasPanel,
                 dependencyTreeWidget, nodeListWidget, runtimeStatusOutput, previewImageLabel,
                 sidebarToggleButton, categoryPanel, dependencyTreeContainer, nodeListContainer,
                 mainMenuBar, settingsStore, widthGetter: Callable[[], int],
                 isSidebarCollapsedGetter: Callable[[], bool]) -> None:
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
        self._expandedSplitterSizes = [240, 900, 340]
        self._currentSplitterSizes = [36, 900, 340]
        self._sidebarExpandedWidth = 240
        self._sidebarCollapsedWidth = 36
        self._sidebarMinWidth = 180
        self._canvasMinWidth = 280
        self._canvasMaxWidth = -1
        self._rightPanelMinWidth = 260
        self._rightPanelWidth = 340
        self._layoutMode = "normal"
        self._menuBarFontSize = 14
        self._applying = False

    def getMainSplitterSizes(self) -> list[int]:
        sizes = getattr(self.mainSplitter, "sizes", lambda: self._currentSplitterSizes)()
        return [int(size) for size in sizes]

    def saveMainSplitterSizes(self, sizes: list[int]) -> None:
        if len(sizes) != 3 or any(size < 0 for size in sizes):
            return
        expanded = [int(size) for size in sizes]
        if self.isSidebarCollapsedGetter() and expanded[0] < self._sidebarMinWidth:
            expanded[0] = self._expandedSplitterSizes[0]
        self._expandedSplitterSizes = expanded
        self.settingsStore.setValue("ui/main_splitter_sizes", expanded)
        self._applySizes()

    def restoreMainSplitterSizes(self) -> None:
        stored = self.settingsStore.value("ui/main_splitter_sizes", None)
        if isinstance(stored, list) and len(stored) == 3:
            try:
                parsed = [int(value) for value in stored]
                if all(value > 0 for value in parsed):
                    parsed[0] = max(self._sidebarMinWidth, parsed[0])
                    self._expandedSplitterSizes = parsed
            except (ValueError, TypeError):
                pass
        self._splitterSizesInitialized = True
        self._applySizes()

    def _applySizes(self) -> None:
        self._applying = True
        try:
            collapsed = self.isSidebarCollapsedGetter()
            width = self._sidebarCollapsedWidth if collapsed else self._sidebarMinWidth
            self.sidebarContainer.setMinimumWidth(width)
            self.sidebarContainer.setMaximumWidth(self._sidebarCollapsedWidth if collapsed else 16777215)
            sizes = list(self._expandedSplitterSizes)
            if collapsed:
                sizes[0] = self._sidebarCollapsedWidth
            self._currentSplitterSizes = sizes
            self.mainSplitter.setSizes(sizes)
        finally:
            self._applying = False

    def onMainSplitterMoved(self, pos: int, index: int) -> None:
        if not self._applying:
            self.saveMainSplitterSizes(self.getMainSplitterSizes())

    def applyResponsiveLayout(self) -> None:
        self._layoutMode = "large" if self.widthGetter() >= 1600 else "normal"
        self.rightPanelContainer.setMinimumWidth(self._rightPanelMinWidth)
        self.canvasPanel.setMinimumWidth(self._canvasMinWidth)
        self.canvasPanel.setMaximumWidth(16777215)
        self.previewImageLabel.setMinimumHeight(90)
        collapsed = self.isSidebarCollapsedGetter()
        self.sidebarContainer.setMinimumWidth(self._sidebarCollapsedWidth if collapsed else self._sidebarMinWidth)
        self.sidebarContainer.setMaximumWidth(self._sidebarCollapsedWidth if collapsed else 16777215)
        if not self._splitterSizesInitialized:
            self.restoreMainSplitterSizes()

    def applySidebarState(self) -> None:
        collapsed = self.isSidebarCollapsedGetter()
        for widget in (self.categoryPanel, self.dependencyTreeContainer, self.nodeListContainer):
            widget.setVisible(not collapsed)
        self._applySizes()
        setIcon = getattr(self.sidebarToggleButton, "setIcon", None)
        if callable(setIcon):
            from emo_master.apps.designer.ui.icon_map import icon
            setIcon(icon("panel-left-open" if collapsed else "panel-left-close"))
            self.sidebarToggleButton.setText("")
            self.sidebarToggleButton.setToolTip("展开工具区" if collapsed else "折叠工具区")
        else:
            self.sidebarToggleButton.setText(">" if collapsed else "<")

    def getLayoutMode(self) -> str:
        return self._layoutMode

    def getPanelWidths(self) -> dict[str, int]:
        return {"sidebar": self._sidebarExpandedWidth, "rightPanel": self._rightPanelWidth}

    def getPanelConstraints(self) -> dict[str, int]:
        return {"sidebarMinWidth": self._sidebarMinWidth, "canvasMinWidth": self._canvasMinWidth,
                "canvasMaxWidth": self._canvasMaxWidth, "rightPanelMinWidth": self._rightPanelMinWidth,
                "rightPanelDefaultWidth": self._rightPanelWidth}

    def getMenuBarFontSize(self) -> int:
        return self._menuBarFontSize
