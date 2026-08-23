from __future__ import annotations


try:
    from PySide2.QtWidgets import (
        QDialog,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    class ProjectEntryDialog(QDialog):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setModal(True)
            self.setWindowTitle("项目入口")
            self.resize(720, 520)
            self._action = "cancel"
            self._selectedPath = ""
            self._recentProjects: list[dict[str, str]] = []
            self._removedRecentProjectPath = ""
            self._clearRecentProjects = False
            self._layoutMode = "homepage"
            self._recentCardMode = "card-list"
            self._actionSectionMode = "card-actions"
            self._recentRemoveMode = "inline-button"
            self._recentEmptyStateText = (
                "暂无最近项目\n点击“打开项目”或“新建空白”开始使用"
            )
            self._visualSectionNames = ["hero", "actions", "recent-projects"]

            rootLayout = QVBoxLayout()
            self._heroTitle = QLabel("开始使用 EmoMaster")
            self._heroSubtitle = QLabel("打开项目，或从空白项目开始构建视觉流程")
            self._heroSection = QWidget()
            setHeroSectionName = getattr(self._heroSection, "setObjectName", None)
            if callable(setHeroSectionName):
                setHeroSectionName("startupHeroSection")
            heroLayout = QVBoxLayout()
            setHeroTitleName = getattr(self._heroTitle, "setObjectName", None)
            if callable(setHeroTitleName):
                setHeroTitleName("startupHeroTitle")
            setHeroSubtitleName = getattr(self._heroSubtitle, "setObjectName", None)
            if callable(setHeroSubtitleName):
                setHeroSubtitleName("startupHeroSubtitle")
            heroLayout.addWidget(self._heroTitle)
            heroLayout.addWidget(self._heroSubtitle)
            self._heroSection.setLayout(heroLayout)
            rootLayout.addWidget(self._heroSection)

            self._actionSection = QWidget()
            setActionSectionName = getattr(self._actionSection, "setObjectName", None)
            if callable(setActionSectionName):
                setActionSectionName("startupActionSection")
            buttonRow = QHBoxLayout()
            self._openProjectButton = QPushButton("打开项目")
            self._newBlankButton = QPushButton("新建空白")
            self._cancelButton = QPushButton("取消")
            setOpenName = getattr(self._openProjectButton, "setObjectName", None)
            if callable(setOpenName):
                setOpenName("startupPrimaryAction")
            setBlankName = getattr(self._newBlankButton, "setObjectName", None)
            if callable(setBlankName):
                setBlankName("startupSecondaryAction")
            setCancelName = getattr(self._cancelButton, "setObjectName", None)
            if callable(setCancelName):
                setCancelName("startupGhostAction")

            buttonRow.addWidget(self._openProjectButton)
            buttonRow.addWidget(self._newBlankButton)
            buttonRow.addWidget(self._cancelButton)
            self._actionSection.setLayout(buttonRow)
            rootLayout.addWidget(self._actionSection)

            self._recentSection = QWidget()
            setRecentSectionName = getattr(self._recentSection, "setObjectName", None)
            if callable(setRecentSectionName):
                setRecentSectionName("startupRecentSection")
            recentSectionLayout = QVBoxLayout()
            self._recentTitle = QLabel("最近项目")
            recentSectionLayout.addWidget(self._recentTitle)
            self._recentList = QListWidget()
            setObjectName = getattr(self._recentList, "setObjectName", None)
            if callable(setObjectName):
                setObjectName("recentProjectsList")
            recentSectionLayout.addWidget(self._recentList)
            self._recentEmptyStateLabel = QLabel(self._recentEmptyStateText)
            recentSectionLayout.addWidget(self._recentEmptyStateLabel)
            recentActionRow = QHBoxLayout()
            self._removeRecentButton = QPushButton("移除选中")
            self._clearRecentButton = QPushButton("清空历史")
            recentActionRow.addWidget(self._removeRecentButton)
            recentActionRow.addWidget(self._clearRecentButton)
            recentSectionLayout.addLayout(recentActionRow)
            self._recentSection.setLayout(recentSectionLayout)
            rootLayout.addWidget(self._recentSection)

            host = QWidget()
            host.setLayout(rootLayout)
            containerLayout = QVBoxLayout()
            containerLayout.addWidget(host)
            self.setLayout(containerLayout)

            self._openProjectButton.clicked.connect(self._onOpenProject)
            self._newBlankButton.clicked.connect(self._onNewBlank)
            self._cancelButton.clicked.connect(self._onCancel)
            self._recentList.itemDoubleClicked.connect(self._onRecentProjectActivated)
            self._removeRecentButton.clicked.connect(self._onRemoveRecentProject)
            self._clearRecentButton.clicked.connect(self._onClearRecentProjects)

        def execSelection(self) -> tuple[str, str]:
            _ = self.exec_()
            return self._action, self._selectedPath

        def setRecentProjects(self, projects: list[dict[str, str]]) -> None:
            self._recentProjects = list(projects)
            self._recentList.clear()
            for item in projects:
                projectName = str(item.get("projectName", "项目"))
                projectPath = str(item.get("projectPath", ""))
                listItem = QListWidgetItem(f"{projectName}\n{projectPath}")
                setData = getattr(listItem, "setData", None)
                if callable(setData):
                    setData(32, projectPath)
                self._recentList.addItem(listItem)
            setVisible = getattr(self._recentEmptyStateLabel, "setVisible", None)
            if callable(setVisible):
                setVisible(len(projects) == 0)

        def _setRemovedRecentProjectPath(self, projectPath: str) -> None:
            self._removedRecentProjectPath = projectPath

        def getRemovedRecentProjectPath(self) -> str:
            return self._removedRecentProjectPath

        def shouldClearRecentProjects(self) -> bool:
            return self._clearRecentProjects

        def getLayoutMode(self) -> str:
            return self._layoutMode

        def getRecentProjectDisplayTexts(self) -> list[str]:
            texts: list[str] = []
            countMethod = getattr(self._recentList, "count", None)
            itemMethod = getattr(self._recentList, "item", None)
            if not callable(countMethod) or not callable(itemMethod):
                return texts
            countRaw = countMethod()
            if not isinstance(countRaw, int):
                return texts
            countValue = int(countRaw)
            for index in range(countValue):
                item = itemMethod(index)
                textMethod = getattr(item, "text", None)
                if callable(textMethod):
                    texts.append(str(textMethod()))
            return texts

        def getRecentCardMode(self) -> str:
            return self._recentCardMode

        def getActionSectionMode(self) -> str:
            return self._actionSectionMode

        def getVisualSectionNames(self) -> list[str]:
            return list(self._visualSectionNames)

        def getRecentRemoveMode(self) -> str:
            return self._recentRemoveMode

        def getRecentEmptyStateText(self) -> str:
            return self._recentEmptyStateText

        def _onOpenProject(self) -> None:
            selectedPath, _ = QFileDialog.getOpenFileName(
                self,
                "选择项目文件(project.json)",
                "",
                "项目文件 (project.json)",
            )
            if selectedPath == "":
                return
            self._action = "open_project"
            self._selectedPath = selectedPath
            self.accept()

        def _onNewBlank(self) -> None:
            self._action = "new_blank"
            self._selectedPath = ""
            self.accept()

        def _onCancel(self) -> None:
            self._action = "cancel"
            self._selectedPath = ""
            self.reject()

        def _onRecentProjectActivated(self, item) -> None:
            dataMethod = getattr(item, "data", None)
            if callable(dataMethod):
                selectedPath = str(dataMethod(32))
            else:
                textMethod = getattr(item, "text", None)
                if not callable(textMethod):
                    return
                selectedPath = str(textMethod()).splitlines()[-1]
            if selectedPath == "":
                return
            self._action = "open_project"
            self._selectedPath = selectedPath
            self.accept()

        def _onRemoveRecentProject(self) -> None:
            currentItem = getattr(self._recentList, "currentItem", None)
            if not callable(currentItem):
                return
            item = currentItem()
            if item is None:
                return
            dataMethod = getattr(item, "data", None)
            if callable(dataMethod):
                self._setRemovedRecentProjectPath(str(dataMethod(32)))

        def _onClearRecentProjects(self) -> None:
            self._clearRecentProjects = True

except Exception:  # pragma: no cover

    class ProjectEntryDialog:  # type: ignore[no-redef]
        def __init__(self, parent=None) -> None:
            _ = parent
            self._action = "cancel"
            self._selectedPath = ""
            self._recentProjects: list[dict[str, str]] = []
            self._removedRecentProjectPath = ""
            self._clearRecentProjects = False
            self._layoutMode = "homepage"
            self._recentCardMode = "card-list"
            self._actionSectionMode = "card-actions"
            self._recentRemoveMode = "inline-button"
            self._recentEmptyStateText = (
                "暂无最近项目\n点击“打开项目”或“新建空白”开始使用"
            )
            self._visualSectionNames = ["hero", "actions", "recent-projects"]

        def execSelection(self) -> tuple[str, str]:
            return self._action, self._selectedPath

        def setRecentProjects(self, projects: list[dict[str, str]]) -> None:
            self._recentProjects = list(projects)

        def getRemovedRecentProjectPath(self) -> str:
            return self._removedRecentProjectPath

        def shouldClearRecentProjects(self) -> bool:
            return self._clearRecentProjects

        def getLayoutMode(self) -> str:
            return self._layoutMode

        def getRecentProjectDisplayTexts(self) -> list[str]:
            return [
                f"{item.get('projectName', '项目')}\n{item.get('projectPath', '')}"
                for item in self._recentProjects
            ]

        def getRecentCardMode(self) -> str:
            return self._recentCardMode

        def getActionSectionMode(self) -> str:
            return self._actionSectionMode

        def getVisualSectionNames(self) -> list[str]:
            return list(self._visualSectionNames)

        def getRecentRemoveMode(self) -> str:
            return self._recentRemoveMode

        def getRecentEmptyStateText(self) -> str:
            return self._recentEmptyStateText
