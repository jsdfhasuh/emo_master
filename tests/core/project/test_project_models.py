import pytest
from pydantic import ValidationError

from emo_master.core.project.models import ProjectDocument


def testProjectDocumentRejectsDuplicateNodeIdsWithinWorkflow() -> None:
    payload = {
        "schemaVersion": "2.1",
        "project": {
            "projectId": "duplicate-nodes",
            "name": "Duplicate nodes",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main"],
        "workflows": {
            "main": {
                "name": "Main",
                "nodes": [
                    {"nodeId": "duplicate", "kind": "operator"},
                    {"nodeId": "duplicate", "kind": "operator"},
                ],
                "edges": [],
            }
        },
    }

    with pytest.raises(ValidationError, match="duplicate nodeId values: duplicate"):
        ProjectDocument.model_validate(payload)
