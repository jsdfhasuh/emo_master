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


def testMainWindowBuildsMenuBarGroups() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getMenuBarGroups = getattr(window, "getMenuBarGroups", None)
    assert callable(getMenuBarGroups)
    groups = getMenuBarGroups()
    assert groups == ["文件", "运行", "编辑", "视图"]


def testRecentProjectsMenuEntriesExist() -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    settings.setValue(
        "ui/recent_projects",
        [{"projectName": "demo", "projectPath": "C:/demo/project.json"}],
    )
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    getRecentProjectsMenuEntries = getattr(window, "getRecentProjectsMenuEntries", None)
    assert callable(getRecentProjectsMenuEntries)
    entries = getRecentProjectsMenuEntries()
    assert entries == [{"projectName": "demo", "projectPath": "C:/demo/project.json"}]


def testRecentProjectsMenuCanOpenProject(tmp_path: Path) -> None:
    ensureQApp()
    settings = SettingsStoreStub()
    projectDir = tmp_path / "demo_project"
    projectDir.mkdir(parents=True, exist_ok=True)
    projectFile = projectDir / "project.json"
    projectFile.write_text(
        '{"version":"1.0","meta":{"name":"demo"},"runtime":{"sourceImagePath":""},"designer":{"nodes":[],"edges":[]}}',
        encoding="utf-8",
    )
    settings.setValue(
        "ui/recent_projects",
        [{"projectName": "demo", "projectPath": projectFile.as_posix()}],
    )
    window = MainWindow(RuntimeClientStub(), settingsStore=settings)
    openRecentProject = getattr(window, "openRecentProject", None)
    assert callable(openRecentProject)
    assert openRecentProject(projectFile.as_posix()) is True


def testMenuBarFontSizeAdaptsToWindowWidth() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getMenuBarFontSize = getattr(window, "getMenuBarFontSize", None)
    assert callable(getMenuBarFontSize)
    smallSize = getMenuBarFontSize()

    resizeMethod = getattr(window, "resize", None)
    assert callable(resizeMethod)
    resizeMethod(1800, 900)
    window.applyResponsiveLayout()

    largeSize = getMenuBarFontSize()
    assert largeSize > smallSize
    assert largeSize >= 14
