from copy import deepcopy
import json

import pytest

from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.presentation.models import Action, DataSource, walkComponents
from emo_master.core.presentation.validation import validateBindings


def testPageCopyRebindUndoAndDeleteAreIsolated(project, manifests):
    session = ProjectEditSession(project.model_dump())
    store = session.presentation
    original = store.snapshot().pages["overview"].model_dump()
    copyId = store.copyPage("overview")
    copied = store.snapshot().pages[copyId]
    assert {c.componentId for c in walkComponents(copied.components)}.isdisjoint(
        c.componentId for c in walkComponents(store.snapshot().pages["overview"].components))
    source = store.snapshot().dataSources["count"]
    source.nodeId = "b"
    store.rebind(copyId, copied.components[0].componentId, "value", source)
    source.nodeId = "unknown"  # caller-owned source cannot mutate stored binding
    assert store.snapshot().pages["overview"].model_dump() == original
    assert store.snapshot().dataSources["count"].nodeId == "a"
    assert validateBindings(session.document(), manifests, publish=True) == []
    assert session.undo()
    assert store.snapshot().pages[copyId].components[0].bindings["value"] == "count"
    assert session.redo()
    store.deletePage(copyId)
    assert store.snapshot().pages["overview"].model_dump() == original
    assert session.undo() and copyId in store.snapshot().pages


def testCopyRemapsInternalNavigationAndNestedComponentIds(project):
    page = project.presentation.pages["overview"]
    page.components[1].actions["clicked"].pageId = "overview"
    session = ProjectEditSession(project.model_dump())
    copiedId = session.presentation.copyPage("overview")
    copied = session.presentation.snapshot().pages[copiedId]
    assert copied.components[1].actions["clicked"].pageId == copiedId
    assert session.presentation.snapshot().pages["overview"].components[1].actions["clicked"].pageId == "overview"


def testDeleteAndReferenceRepairAreOneTransaction(project):
    session = ProjectEditSession(project.model_dump())
    before = session.payload()
    with pytest.raises(ValueError, match="explicit repair"):
        session.presentation.deletePage("detail")
    assert session.payload() == before
    session.presentation.deletePage("detail", repairTo="overview")
    assert session.presentation.snapshot().pages["overview"].components[1].actions["clicked"].pageId == "overview"
    assert session.undo() and session.payload() == before


def testNavigationCapturesDisplayedNotLatestAndDoesNotDirty(project):
    session = ProjectEditSession(project.model_dump())
    store = session.presentation
    store.recordDisplayed("overview", "root", "displayed-7")
    store.recordDisplayed("detail", "root", "background-9")
    action = store.snapshot().pages["overview"].components[1].actions["clicked"]
    store.navigate(action)
    assert store.activePageId == "detail" and store.frozenResults["detail"]["root"] == "displayed-7"
    assert not session.dirty and not session.undo()
    store.navigate(Action(type="resume_live"))
    assert store.frozenResults == {} and not session.dirty
    raw = json.dumps(session.payload())
    assert "displayed-7" not in raw and "activePageId" not in raw


def testCrudOrderingDefaultAndFailedEditsRollback(project):
    session = ProjectEditSession(project.model_dump())
    store = session.presentation
    pageId = store.createPage("New")
    store.renamePage(pageId, "Renamed")
    store.reorderPages([pageId, "overview", "detail"])
    store.setDefaultPage(pageId)
    before = session.payload()
    for edit in [lambda: store.renamePage(pageId, ""),
                 lambda: store.reorderPages([pageId]),
                 lambda: store.setDefaultPage("missing")]:
        with pytest.raises(ValueError):
            edit()
        assert session.payload() == before
    assert store.activePageId == pageId


def testSourcesDeduplicateByCompleteIdentity(project):
    session = ProjectEditSession(project.model_dump())
    copiedId = session.presentation.copyPage("overview")
    componentId = session.presentation.snapshot().pages[copiedId].components[0].componentId
    source = session.presentation.snapshot().dataSources["count"]
    session.presentation.rebind(copiedId, componentId, "value", source)
    assert len(session.presentation.snapshot().dataSources) == 1
    source = DataSource.model_validate({**source.model_dump(), "nodeId": "b"})
    session.presentation.rebind(copiedId, componentId, "value", source)
    assert len(session.presentation.snapshot().dataSources) == 2


def testUnifiedSaveLoadWorkflowEditsPreservePagesAndBackup(project, tmp_path):
    session = ProjectEditSession(project.model_dump())
    session.save(tmp_path)
    diskBefore = (tmp_path / "project.json").read_bytes()
    with session.transaction():
        session.workflows.renameWorkflow("main", "Changed workflow")
    session.presentation.renamePage("overview", "Changed page")
    assert session.dirty
    session.save(tmp_path)
    assert not session.dirty
    assert (tmp_path / "project.json.bak").read_bytes() == diskBefore
    loaded = ProjectEditSession.load(tmp_path)
    assert loaded.payload() == session.payload()
    assert loaded.workflows.get("main").name == "Changed workflow"
    assert loaded.presentation.snapshot().pages["overview"].name == "Changed page"
    # The existing canvas saving only through WorkflowStore still retains all P1 data.
    legacyStore = WorkflowStore(loaded.payload())
    assert legacyStore.toPayload()["presentation"] == session.payload()["presentation"]
    assert legacyStore.toPayload()["resources"] == session.payload()["resources"]
    savedRevision = session.workflows.project["revision"]
    assert session.undo() and session.dirty
    assert session.workflows.project["revision"] == savedRevision
    assert session.redo() and not session.dirty


def testFailedSaveRetainsDraftRevisionAndOldFile(project, tmp_path, monkeypatch):
    from emo_master.apps.designer.state import project_store
    session = ProjectEditSession(project.model_dump())
    session.save(tmp_path)
    disk = (tmp_path / "project.json").read_bytes()
    session.presentation.renamePage("overview", "unsaved")
    draft = deepcopy(session.payload())

    def deny(*args):
        raise PermissionError("injected atomic replacement failure")

    monkeypatch.setattr(project_store.os, "replace", deny)
    with pytest.raises(PermissionError):
        session.save(tmp_path)
    assert session.dirty and session.payload() == draft
    assert (tmp_path / "project.json").read_bytes() == disk
    assert not list(tmp_path.glob("*.tmp"))


def testWorkflowTransactionRollsBackAndHistoryBounded(project):
    session = ProjectEditSession(project.model_dump(), historyLimit=2)
    before = session.payload()
    with pytest.raises(ValueError):
        with session.transaction():
            session.workflows.entryWorkflowId = "missing"
    assert session.payload() == before
    for name in ["one", "two", "three"]:
        session.presentation.renamePage("overview", name)
    assert session.undo() and session.undo() and not session.undo()
    assert session.presentation.snapshot().pages["overview"].name == "one"
