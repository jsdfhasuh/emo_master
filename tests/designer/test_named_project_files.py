from pathlib import Path

from emo_master.apps.designer.state.project_store import createProjectSkeleton, loadProject
from emo_master.core.project.files import PROJECT_OPEN_FILTER, PROJECT_SAVE_FILTER
from tests.designer.test_main_window_project_save_load import RuntimeClientStub


def _window(owner):
    return owner(RuntimeClientStub())


def testOpenSaveAndRuntimeSyncKeepExactFileInSharedDirectory(tmp_path, ownedDesignerWindow, monkeypatch):
    legacy, named = tmp_path / "project.json", tmp_path / "\u56fe\u7247\u68c0\u6d4b.emoproj"
    createProjectSkeleton(legacy, "Legacy")
    createProjectSkeleton(named, "Named")
    legacyBytes = legacy.read_bytes()
    window = _window(ownedDesignerWindow)
    calls = []
    monkeypatch.setattr(window.runtimeClient, "loadProject", lambda path: (
        calls.append(path) or type("Reply", (), {"ok": True})()
    ))
    assert window.loadProjectSelection(str(named))
    assert window.projectController.currentProjectFile == named
    assert window.currentProjectDir == tmp_path
    assert window.loadedProjectPath == str(named)
    window.saveProjectAction()
    assert window._syncRuntimeProjectBeforeRun()
    assert calls == [str(named), str(named)]
    assert legacy.read_bytes() == legacyBytes
    assert loadProject(named)["project"]["name"] == "Named"
    assert window.getRecentProjects()[0] == {"projectName": named.stem, "projectPath": named.as_posix()}


def testSaveAsCreatesNamedFileAndLeavesOriginalUntouched(tmp_path, ownedDesignerWindow, monkeypatch):
    legacy = tmp_path / "project.json"
    createProjectSkeleton(legacy, "Legacy")
    window = _window(ownedDesignerWindow)
    assert window.loadProjectSelection(str(legacy))
    previous = legacy.read_bytes()
    named = tmp_path / "new location" / "inspection.emoproj"
    monkeypatch.setattr(window, "_chooseProjectFile", lambda title: str(named))
    window.saveProjectAsAction()
    assert named.is_file()
    assert window.projectController.currentProjectFile == named
    assert window.loadedProjectPath == str(named)
    window.saveProjectAction()
    assert legacy.read_bytes() == previous
    assert not (named.parent / "project.json").exists()
    assert "save_as" in window.designerActions.actions


def testFailedOpenAndCancelledSaveAsPreserveActiveFile(tmp_path, ownedDesignerWindow, monkeypatch):
    named = tmp_path / "good.emoproj"
    createProjectSkeleton(named, "Good")
    invalid = tmp_path / "bad.emoproj"
    invalid.write_text("{}")
    window = _window(ownedDesignerWindow)
    assert window.loadProjectSelection(str(named))
    assert not window.loadProjectSelection(str(invalid))
    assert not window.loadProjectSelection(str(tmp_path))
    monkeypatch.setattr(window, "_chooseProjectFile", lambda title: "")
    window.saveProjectAsAction()
    assert window.projectController.currentProjectFile == named
    assert window.loadedProjectPath == str(named)


def testFilePickersUseNamedProjectTypeAndCompleteSuffix(tmp_path, ownedDesignerWindow, monkeypatch):
    from PySide2.QtWidgets import QFileDialog

    window = _window(ownedDesignerWindow)
    seen = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (seen.append(args) or ("", "")))
    window.loadProject()
    assert seen[-1][1] == "\u6253\u5f00\u9879\u76ee"
    assert seen[-1][3] == PROJECT_OPEN_FILTER
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (
        seen.append(args) or (str(tmp_path / "chosen"), "")
    ))
    assert Path(window._chooseProjectFile("Save")) == tmp_path / "chosen.emoproj"
    assert seen[-1][3] == PROJECT_SAVE_FILTER


def testNamedSaveAsCopiesDeclaredResourcesAndPreservesOriginal(tmp_path, ownedDesignerWindow, monkeypatch):
    from tests.runtime.production_fixture import productionProject

    source = tmp_path / "source"
    productionProject(source, save=False)
    original = source / "project.json"
    before = original.read_bytes()
    window = _window(ownedDesignerWindow)
    assert window.loadProjectSelection(str(original))
    destination = tmp_path / "copy" / "copy.emoproj"
    monkeypatch.setattr(window, "_chooseProjectFile", lambda title: str(destination))
    window.saveProjectAsAction()
    assert destination.exists()
    assert (destination.parent / "input.png").read_bytes() == (source / "input.png").read_bytes()
    assert original.read_bytes() == before
    assert loadProject(destination)["presentation"] == loadProject(original)["presentation"]
    assert window.pageCoordinator.directory == destination.parent


def testCompletedSuffixDoesNotOverwriteWithoutConfirmation(tmp_path, ownedDesignerWindow, monkeypatch):
    from PySide2.QtWidgets import QFileDialog, QMessageBox

    existing = tmp_path / "existing.emoproj"
    createProjectSkeleton(existing, "Existing")
    original = existing.read_bytes()
    window = _window(ownedDesignerWindow)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(existing.with_suffix("")), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    window.saveProjectAsAction()
    assert existing.read_bytes() == original
    assert window.projectController.currentProjectFile is None


def testSaveAsIsBlockedWhileJobIsRunning(ownedDesignerWindow, monkeypatch):
    window = _window(ownedDesignerWindow)
    calls = []
    monkeypatch.setattr(window, "_chooseProjectFile", lambda title: calls.append(title))
    window._setIsJobRunning(True)
    try:
        window.saveProjectAsAction()
        assert not calls
        assert window.designerActions.blockedReason("save_as")
    finally:
        window._setIsJobRunning(False)
