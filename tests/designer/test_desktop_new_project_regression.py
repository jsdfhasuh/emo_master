"""Actual menu/startup constructors must open local drafts, not online Runtime."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.state.project_store import loadProject


@pytest.mark.parametrize('entry', ['menu', 'startup'])
def testActualNewEntryPreservesOnlineAndReopens(ownedDesignerWindow, tmp_path, monkeypatch, entry):
    online = dict(projectId='online-A', revision=7, connectionIntent={'camera': True})
    before = deepcopy(online)
    def forbidden(*args, **kwargs):
        raise AssertionError('new/open draft must not touch online A')
    runtime = SimpleNamespace(listOperators=lambda: [], loadProject=forbidden,
                              releaseCommunicationForReplacement=forbidden)
    w = ownedDesignerWindow(runtime, settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    a = tmp_path / 'A.emoproj'
    monkeypatch.setattr(w, '_chooseProjectFile', lambda title: str(a))
    w.newProjectAction()
    aId = w.workflowStore.project['projectId']
    b = tmp_path / 'B.emoproj'
    monkeypatch.setattr(w, '_chooseProjectFile', lambda title: str(b))
    if entry == 'menu':
        w.newProjectAction()
    else:
        from tests.designer.test_startup_project_entry import EntryDialogStub
        monkeypatch.setattr(w, '_projectEntryDialogFactory', lambda: EntryDialogStub('new_blank', ''))
        assert w.showStartupProjectEntry()
    raw = loadProject(b)
    assert raw['schemaVersion'] == '2.7'
    assert all(key in raw for key in ('resources', 'production', 'presentation', 'globalVariables'))
    assert raw['devices']['schemaVersion'] == '1.0'
    assert raw['project']['projectId'] != aId
    assert w.projectController.currentProjectFile == b
    assert w.pageCoordinator.session.payload()['project']['projectId'] == raw['project']['projectId']
    assert w.loadedProjectPath == str(b)
    assert w.saveProjectToDirectory(str(b))
    assert w.loadProjectDirectory(str(b), confirmed=True)
    assert online == before
    assert set(w.flowModel.nodes) == {'__workflow_input__', '__workflow_output__'}


def testCreatedFileOpenFailureIsVisibleAndKeepsOld(ownedDesignerWindow, tmp_path, monkeypatch):
    w = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: []))
    old = w.pageCoordinator.session.payload()
    target = tmp_path / 'B.emoproj'
    monkeypatch.setattr(w, '_chooseProjectFile', lambda title: str(target))
    def reject(*args, **kwargs):
        w.projectController.lastLoadError = 'test load error'
        return False
    monkeypatch.setattr(w, 'loadProjectDirectory', reject)
    messages = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: messages.append(args[2]))
    w.newProjectAction()
    assert target.exists() and w.pageCoordinator.session.payload() == old
    assert 'test load error' in messages[0] and str(target) in messages[0]
