"""Malformed project JSON is a located public input failure, not HTTP 500."""
import asyncio

import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.authoring.errors import AuthoringError
from emo_master.authoring.service import AuthoringService


def test_public_import_preserves_formal_model_location_and_creates_no_draft(tmp_path):
    service = AuthoringService(tmp_path)
    payload = WorkflowStore().toPayload()
    payload['project']['name'] = {'unexpected': 'object'}
    with pytest.raises(AuthoringError) as caught:
        asyncio.run(service.invoke('import_project_draft', {'project': payload}))
    error = caught.value
    assert error.code == 'E_PROJECT_INVALID' and error.status == 422
    assert error.details['fieldPath'] == 'project.name'
    assert error.details['diagnostics'][0]['fieldPath'] == 'project.name'
    assert service.drafts.list() == []
    assert list((tmp_path / 'project-drafts').iterdir()) == []
