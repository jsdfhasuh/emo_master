import hashlib

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.core.project.models import ProjectDocument


def sampleProject(root, image=True, overlay=True):
    pixels = np.zeros((120, 160, 3), np.uint8)
    pixels[20:40, 20:40] = 255
    pixels[60:90, 80:100] = 255
    cv2.imwrite(str(root / "input.png"), pixels)
    data = (root / "input.png").read_bytes()
    sources = {"count": {"kind": "node_output", "resultScopeId": "root", "workflowId": "main",
                          "nodeId": "count", "port": "count", "expectedType": "integer"}}
    components = [{"componentId": "count", "type": "number", "bindings": {"value": "count"}}]
    if image:
        sources["image"] = dict(sources["count"], nodeId="blob", port="overlay", expectedType="image")
        components.append({"componentId": "image", "type": "image", "bindings": {"image": "image"}, "layout": {"row": 1}})
    nodes = [{"nodeId": "input", "kind": "workflow_input"},
             {"nodeId": "load", "operatorId": "vision.io.image_loader"},
             {"nodeId": "blob", "operatorId": "vision.analysis.blob", "params": {"drawOverlay": overlay, "includeContour": False}},
             {"nodeId": "count", "operatorId": "vision.collection.count"},
             {"nodeId": "output", "kind": "workflow_output"}]
    return ProjectDocument.model_validate({"schemaVersion": "2.2", "project": {"projectId": "p2-fixture", "name": "P2",
        "createdAt": "2026-09-26T00:00:00Z", "updatedAt": "2026-09-26T00:00:00Z"}, "entryWorkflowId": "main",
        "workflowOrder": ["main"], "workflows": {"main": {"name": "Main", "nodes": nodes, "edges": [
            {"fromNode": "load", "fromPort": "image", "toNode": "blob", "toPort": "image"},
            {"fromNode": "load", "fromPort": "frame", "toNode": "blob", "toPort": "frame"},
            {"fromNode": "blob", "fromPort": "blobs", "toNode": "count", "toPort": "blobs"}]}},
        "presentation": {"defaultPageId": "main", "pageOrder": ["main"], "pages": {"main": {"name": "Main", "components": components}},
                         "dataSources": sources, "resultScopes": {"root": {"entryWorkflowId": "main", "scopeWorkflowId": "main"}}},
        "resources": {"items": {"input": {"path": "input.png", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "purpose": "input_image"}},
                      "parameterBindings": [{"target": {"workflowId": "main", "nodeId": "load", "parameterPath": ["imagePath"]}, "resourceId": "input"}]}})


@pytest.fixture
def channel(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / "runtime.sqlite3", workspaceRoot=tmp_path / "legacy-jobs")
    service = PresentationService(runtime, tmp_path / "display")
    try:
        yield service
    finally:
        runtime.close()
        service.close()


@pytest.fixture
def sample():
    return sampleProject
