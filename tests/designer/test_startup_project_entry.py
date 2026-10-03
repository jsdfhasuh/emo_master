import json
from pathlib import Path

from emo_master.apps.designer.state.workflow_store import WorkflowStore
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

    def loadProject(self, projectPath: str):
        _ = projectPath
        return type("Reply", (), {"ok": True, "message": "ok"})()


class EntryDialogStub:
    def __init__(self, action: str, path: str) -> None:
        self._action = action
        self._path = path
        self.recentProjects: list[dict[str, str]] = []
        self.removedProjectPath = ""
        self.cleared = False
        self.layoutMode = ""

    def execSelection(self) -> tuple[str, str]:
        return self._action, self._path

    def setRecentProjects(self, projects: list[dict[str, str]]) -> None:
        self.recentProjects = list(projects)

    def getRemovedRecentProjectPath(self) -> str:
        return self.removedProjectPath

    def shouldClearRecentProjects(self) -> bool:
        return self.cleared

    def getLayoutMode(self) -> str:
        return self.layoutMode


def testStartupCancelReturnsFalse() -> None:
    ensureQApp()
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: EntryDialogStub("cancel", ""),
    )
    assert window.showStartupProjectEntry() is False


def testStartupDialogReceivesRecentProjects() -> None:
    ensureQApp()
    settings = type(
        "SettingsStoreStub",
        (),
        {
            "__init__": lambda self: setattr(
                self, "values", {"ui/recent_projects": ["C:/demo/project.json"]}
            ),
            "value": lambda self, key, default=None: self.values.get(key, default),
            "setValue": lambda self, key, value: self.values.__setitem__(key, value),
        },
    )()
    dialog = EntryDialogStub("cancel", "")
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: dialog,
        settingsStore=settings,
    )
    _ = window.showStartupProjectEntry()
    assert dialog.recentProjects == [
        {"projectName": "demo", "projectPath": "C:/demo/project.json"}
    ]


def testStartupDialogRemoveRecentProjectUpdatesHistory() -> None:
    ensureQApp()
    settings = type(
        "SettingsStoreStub",
        (),
        {
            "__init__": lambda self: setattr(
                self,
                "values",
                {
                    "ui/recent_projects": [
                        {"projectName": "demo", "projectPath": "C:/demo/project.json"},
                        {
                            "projectName": "demo2",
                            "projectPath": "C:/demo2/project.json",
                        },
                    ]
                },
            ),
            "value": lambda self, key, default=None: self.values.get(key, default),
            "setValue": lambda self, key, value: self.values.__setitem__(key, value),
        },
    )()
    dialog = EntryDialogStub("cancel", "")
    dialog.removedProjectPath = "C:/demo/project.json"
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: dialog,
        settingsStore=settings,
    )
    _ = window.showStartupProjectEntry()
    assert settings.value("ui/recent_projects") == [
        {"projectName": "demo2", "projectPath": "C:/demo2/project.json"}
    ]


def testStartupDialogClearRecentProjectsUpdatesHistory() -> None:
    ensureQApp()
    settings = type(
        "SettingsStoreStub",
        (),
        {
            "__init__": lambda self: setattr(
                self,
                "values",
                {
                    "ui/recent_projects": [
                        {"projectName": "demo", "projectPath": "C:/demo/project.json"}
                    ]
                },
            ),
            "value": lambda self, key, default=None: self.values.get(key, default),
            "setValue": lambda self, key, value: self.values.__setitem__(key, value),
        },
    )()
    dialog = EntryDialogStub("cancel", "")
    dialog.cleared = True
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: dialog,
        settingsStore=settings,
    )
    _ = window.showStartupProjectEntry()
    assert settings.value("ui/recent_projects") == []


def testStartupOpenProjectLoadsPathAndReturnsTrue(tmp_path: Path) -> None:
    ensureQApp()
    projectDir = tmp_path / "demo_project"
    projectDir.mkdir(parents=True, exist_ok=True)
    projectFile = projectDir / "project.json"
    projectFile.write_text(
        json.dumps(
            {
                "version": "1.0",
                "meta": {"name": "demo"},
                "runtime": {"sourceImagePath": ""},
                "designer": {"nodes": [], "edges": []},
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: EntryDialogStub(
            "open_project", str(projectFile)
        ),
    )
    assert window.showStartupProjectEntry() is True


def testStartupOpenProjectRefreshesWorkflowTabs(tmp_path: Path) -> None:
    ensureQApp()
    projectDir = tmp_path / "multi-workflow-project"
    projectDir.mkdir(parents=True, exist_ok=True)
    workflowStore = WorkflowStore()
    bodyWorkflowId = workflowStore.createWorkflow("Body")
    (projectDir / "project.json").write_text(
        json.dumps(
            workflowStore.toPayload("multi-workflow-project"),
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: EntryDialogStub(
            "open_project", str(projectDir / "project.json")
        ),
    )

    assert window.showStartupProjectEntry() is True
    assert window.workflowTabs.count() == 3
    assert window.workflowTabs.tabBar().tabData(0) == "main"
    assert window.workflowTabs.tabBar().tabData(1) == bodyWorkflowId
    assert window.workflowTabs.tabBar().tabData(2) is None
    assert window.workflowTabs.tabText(1) == "Body"


def testStartupNewBlankPromptsDirectoryAndSaves(tmp_path: Path) -> None:
    ensureQApp()
    projectDir = tmp_path / "blank_project"
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: EntryDialogStub("new_blank", ""),
    )
    window._chooseProjectDirectory = lambda title: str(projectDir)  # type: ignore[method-assign]
    assert window.showStartupProjectEntry() is True
    assert (projectDir / "project.json").exists()
    assert window.currentProjectDir == projectDir


def testStartupNewBlankCancelDirectoryReturnsFalse() -> None:
    ensureQApp()
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: EntryDialogStub("new_blank", ""),
    )
    window._chooseProjectDirectory = lambda title: ""  # type: ignore[method-assign]
    assert window.showStartupProjectEntry() is False


def testStartupNewBlankStopsWhenSaveFails(tmp_path: Path) -> None:
    class CountingRuntimeClientStub(RuntimeClientStub):
        def __init__(self) -> None:
            self.loadCalls = 0

        def loadProject(self, projectPath: str):
            self.loadCalls += 1
            return super().loadProject(projectPath)

    runtimeClient = CountingRuntimeClientStub()
    projectDir = tmp_path / "failed-blank-project"
    window = MainWindow(
        runtimeClient,
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: EntryDialogStub("new_blank", ""),
    )
    window._chooseProjectDirectory = lambda title: str(projectDir)  # type: ignore[method-assign]
    window.projectController.saveProjectToDirectory = (  # type: ignore[method-assign]
        lambda *args, **kwargs: (False, None)
    )

    assert window.showStartupProjectEntry() is False
    assert runtimeClient.loadCalls == 0
    assert window.currentProjectDir is None


def testStartupDialogUsesHomepageLayoutMode() -> None:
    ensureQApp()
    dialog = EntryDialogStub("cancel", "")
    dialog.layoutMode = "homepage"
    window = MainWindow(
        RuntimeClientStub(),
        showStartupEntry=True,
        projectEntryDialogFactory=lambda: dialog,
    )
    _ = window.showStartupProjectEntry()
    assert dialog.getLayoutMode() == "homepage"


def testStartupDialogUsesHomepageActionCardMode() -> None:
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog

    ensureQApp()
    dialog = ProjectEntryDialog()
    getActionSectionMode = getattr(dialog, "getActionSectionMode", None)
    assert callable(getActionSectionMode)
    assert getActionSectionMode() == "card-actions"


def testStartupDialogUsesVisualHierarchySections() -> None:
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog

    ensureQApp()
    dialog = ProjectEntryDialog()
    getVisualSectionNames = getattr(dialog, "getVisualSectionNames", None)
    assert callable(getVisualSectionNames)
    assert getVisualSectionNames() == ["hero", "actions", "recent-projects"]
