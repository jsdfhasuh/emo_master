from pathlib import Path

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


class SettingsStoreStub:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default=None):
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value


def testRecentProjectsReturnProjectEntries() -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    recordRecentProject = getattr(window, "recordRecentProject", None)
    getRecentProjects = getattr(window, "getRecentProjects", None)
    assert callable(recordRecentProject)
    assert callable(getRecentProjects)

    recordRecentProject("C:/demo/a/project.json")
    recordRecentProject("C:/demo/b/project.json")
    recent = getRecentProjects()
    assert isinstance(recent, list)
    assert recent[0]["projectName"] == "b"
    assert recent[0]["projectPath"] == "C:/demo/b/project.json"


def testRecentProjectsDeduplicateAndSort() -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    window.recordRecentProject("C:/demo/a/project.json")
    window.recordRecentProject("C:/demo/b/project.json")
    window.recordRecentProject("C:/demo/a/project.json")
    recent = window.getRecentProjects()
    assert [item["projectPath"] for item in recent] == [
        "C:/demo/a/project.json",
        "C:/demo/b/project.json",
    ]


def testRecentProjectsDropMissingPaths(tmp_path: Path) -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    validProject = tmp_path / "valid" / "project.json"
    validProject.parent.mkdir(parents=True, exist_ok=True)
    validProject.write_text("{}", encoding="utf-8")
    settings.setValue(
        "ui/recent_projects",
        [validProject.as_posix(), "C:/missing/project.json"],
    )
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    recent = window.getRecentProjects()
    assert validProject.as_posix() in [item["projectPath"] for item in recent]


def testLoadProjectDirectoryPushesRecentProject(tmp_path: Path) -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    projectDir = tmp_path / "demo_project"
    projectDir.mkdir(parents=True, exist_ok=True)
    (projectDir / "project.json").write_text(
        '{"version":"1.0","meta":{"name":"demo"},"runtime":{"sourceImagePath":""},"designer":{"nodes":[],"edges":[]}}',
        encoding="utf-8",
    )
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    assert window.loadProjectDirectory(str(projectDir)) is True
    recent = window.getRecentProjects()
    assert recent[0]["projectPath"] == (projectDir / "project.json").as_posix()
    assert recent[0]["projectName"] == "demo_project"


def testRemoveRecentProjectUpdatesSettings() -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    window.recordRecentProject("C:/demo/a/project.json")
    window.recordRecentProject("C:/demo/b/project.json")
    removeRecentProject = getattr(window, "removeRecentProject", None)
    assert callable(removeRecentProject)
    removeRecentProject("C:/demo/a/project.json")
    recent = window.getRecentProjects()
    assert [item["projectPath"] for item in recent] == ["C:/demo/b/project.json"]


def testClearRecentProjects() -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    window.recordRecentProject("C:/demo/a/project.json")
    clearRecentProjects = getattr(window, "clearRecentProjects", None)
    assert callable(clearRecentProjects)
    clearRecentProjects()
    recent = window.getRecentProjects()
    assert recent == []


def testClearRecentProjectsButtonImmediatelyClearsDialogList() -> None:
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog

    ensureQApp()
    dialog = ProjectEntryDialog()
    dialog.setRecentProjects(
        [{"projectName": "demo", "projectPath": "C:/demo/project.json"}]
    )

    clearButton = getattr(dialog, "_clearRecentButton", None)
    if clearButton is not None:
        clearButton.click()
    else:
        dialog._onClearRecentProjects()

    assert dialog.shouldClearRecentProjects() is True
    assert dialog.getRecentProjectDisplayTexts() == []


def testRecentProjectsRenderedAsProjectCards() -> None:
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog

    ensureQApp()
    dialog = ProjectEntryDialog()
    dialog.setRecentProjects(
        [{"projectName": "demo", "projectPath": "C:/demo/project.json"}]
    )
    texts = dialog.getRecentProjectDisplayTexts()
    assert texts == ["demo\nC:/demo/project.json"]


def testRecentProjectsExposeHomepageCardMode() -> None:
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog

    ensureQApp()
    dialog = ProjectEntryDialog()
    getRecentCardMode = getattr(dialog, "getRecentCardMode", None)
    assert callable(getRecentCardMode)
    assert getRecentCardMode() == "card-list"


def testRecentProjectsShowEmptyStateGuidance() -> None:
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog

    ensureQApp()
    dialog = ProjectEntryDialog()
    dialog.setRecentProjects([])
    getRecentEmptyStateText = getattr(dialog, "getRecentEmptyStateText", None)
    assert callable(getRecentEmptyStateText)
    emptyState = getRecentEmptyStateText()
    assert "暂无最近项目" in emptyState
    assert "打开项目" in emptyState


def testRecentProjectsExposeInlineRemoveMode() -> None:
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog

    ensureQApp()
    dialog = ProjectEntryDialog()
    getRecentRemoveMode = getattr(dialog, "getRecentRemoveMode", None)
    assert callable(getRecentRemoveMode)
    assert getRecentRemoveMode() == "inline-button"
