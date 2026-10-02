from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolatedRuntimeData(tmp_path, monkeypatch):
    # Tests using RuntimeService() must not acquire the live application's lock.
    monkeypatch.setenv("EMO_RUNTIME_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.delenv("EMO_RUNTIME_DB_PATH", raising=False)


@pytest.fixture(scope="session")
def retainedQtApplication():
    """Keep the application wrapper alive even in a core-only Qt test run."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import emo_master  # noqa: F401 - preload Windows dependencies before Qt
    try:
        from PySide2.QtWidgets import QApplication
    except ImportError:
        return None
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ownedFlowScene(retainedQtApplication):
    """Standalone scenes need a GUI teardown; they are not top-level widgets."""
    from tests.qt_scene_owner import OwnedFlowScenes
    owner = OwnedFlowScenes(retainedQtApplication)
    try:
        yield owner
    finally:
        owner.retire()


@pytest.fixture
def ownedDesignerWindow(retainedQtApplication, monkeypatch):
    """Explicit native ownership for e2e tests which create Designer windows."""
    from PySide2.QtWidgets import QMessageBox
    from tests.qt_widget_owner import OwnedDesignerWindows
    with OwnedDesignerWindows(retainedQtApplication) as owner:
        originalQuestion = QMessageBox.question

        def question(parent, title, *args, **kwargs):
            if owner.owns(parent) and title == '项目有未保存修改':
                return QMessageBox.Discard
            return originalQuestion(parent, title, *args, **kwargs)

        monkeypatch.setattr(QMessageBox, 'question', question)
        yield owner
