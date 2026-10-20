from copy import deepcopy

import pytest
from pydantic import ValidationError

from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


def payload():
    store = WorkflowStore()
    store.createWorkflow('Second', 'second')
    return store.toPayload()


def testMissingStartupListPreservesLegacySchemaAndCleanDraft():
    data = payload()
    session = ProjectEditSession(data, enablePresentation=False)
    assert not session.dirty
    assert session.document().startupWorkflows() == ['main']
    assert 'startupWorkflowIds' not in session.payload()['runtime']
    assert session.payload()['schemaVersion'] == '2.1'
    with session.transaction():
        session.workflows.setEntryWorkflow('second')
    assert session.document().startupWorkflows() == ['second']
    assert 'startupWorkflowIds' not in session.payload()['runtime']


@pytest.mark.parametrize('targets', [['main', 'second'], ['second'], ['second', 'main']])
def testStartupListRoundTripsAndIsNotInferredFromNamesOrDefaultEntry(targets):
    data = payload()
    data['runtime']['startupWorkflowIds'] = targets
    original = deepcopy(data)
    document = ProjectDocument.model_validate(migrateProjectPayload(data))
    assert ProjectDocument.model_validate_json(document.model_dump_json()).startupWorkflows() == targets
    store = WorkflowStore(data)
    store.renameWorkflow('second', 'Renamed')
    store.setEntryWorkflow('second')
    assert store.startupWorkflows() == targets
    assert store.toPayload()['runtime']['startupWorkflowIds'] == targets
    assert data == original


@pytest.mark.parametrize('targets,limit', [([], 2), (['missing'], 2), ([''], 2), ([1], 2),
    (['main', 'main'], 2), (['main', 'second'], 1), ('main', 2)])
def testInvalidStartupListsAreRejectedNotSilentlyReplaced(targets, limit):
    data = payload()
    data['runtime'].update(startupWorkflowIds=targets, maxConcurrentJobs=limit)
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(data)


def testDeleteStartupRootIsUndoableAndLastDeletionFallsBackToEntry():
    data = payload()
    data['runtime']['startupWorkflowIds'] = ['main', 'second']
    session = ProjectEditSession(data, enablePresentation=False)
    with session.transaction():
        session.workflows.deleteWorkflow('second')
    assert session.document().startupWorkflows() == ['main']
    session.undo()
    assert session.document().startupWorkflows() == ['main', 'second']
    assert not session.dirty
    with session.transaction():
        session.workflows.runtime['startupWorkflowIds'] = ['second']
    with session.transaction():
        session.workflows.deleteWorkflow('second')
    assert session.document().startupWorkflows() == ['main']
    assert 'startupWorkflowIds' not in session.payload()['runtime']


def testInvalidConcurrencyEditRollsBackBothListAndLimit():
    session = ProjectEditSession(payload(), enablePresentation=False)
    before = session.payload()
    with pytest.raises(ValidationError):
        with session.transaction():
            session.workflows.runtime.update(startupWorkflowIds=['main', 'second'], maxConcurrentJobs=1)
    assert session.payload() == before and not session.dirty
