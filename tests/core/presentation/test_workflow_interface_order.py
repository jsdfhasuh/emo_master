from copy import deepcopy

import pytest

from emo_master.apps.designer.state.project_edit_session import ProjectEditSession


@pytest.mark.parametrize("side", ["inputs", "outputs"])
@pytest.mark.parametrize("workflowId", ["main", "child"])
def testOrderOnlyTransactionIsDirtyUndoableAndPersisted(project, tmp_path, side, workflowId):
    session = ProjectEditSession(project.model_dump())
    setattr(session.workflows.get(workflowId), side, {"first": "integer", "last": "integer"})
    session.workflows.ensureBoundaryNodes(workflowId)
    session.markSaved()
    with session.transaction():
        setattr(session.workflows.get(workflowId), side, {"last": "integer", "first": "integer"})
    assert session.dirty
    assert session.undo()
    assert list(getattr(session.workflows.get(workflowId), side)) == ["first", "last"]
    assert not session.dirty
    assert session.redo()
    assert list(getattr(session.workflows.get(workflowId), side)) == ["last", "first"]
    session.save(tmp_path)
    reloaded = ProjectEditSession.load(tmp_path)
    assert list(getattr(reloaded.workflows.get(workflowId), side)) == ["last", "first"]
    assert not reloaded.dirty
    assert "interfaceOrder" not in session.payload()


def testDescriptorFieldOrderAndSaveMetadataAreNotEditingCommands(project):
    session = ProjectEditSession(project.model_dump())
    session.workflows.get("main").inputs = {"value": {"type": "int", "required": False, "nullable": False}}
    session.workflows.ensureBoundaryNodes("main")
    session.markSaved()
    signature = session._signature()
    payload = deepcopy(session.payload())
    with session.transaction():
        session.workflows.get("main").inputs["value"] = {"nullable": False, "required": False, "type": "int"}
    session.checkpoint()
    assert not session.dirty
    assert session._signature() == signature
    assert not session.undo()
    session.workflows.project["revision"] = 2
    session.workflows.project["updatedAt"] = "2026-10-08T00:00:00Z"
    assert not session.dirty
    # Signature computation must never strip metadata from the actual draft.
    assert session.payload()["project"]["revision"] == 2
    assert payload["project"]["revision"] == 1
