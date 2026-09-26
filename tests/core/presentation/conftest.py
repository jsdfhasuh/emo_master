from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from emo_master.core.plugin.models import PluginManifest
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


@pytest.fixture
def manifests():
    root = Path(__file__).resolve().parents[3] / "src/emo_master/plugins/builtins"
    result = {}
    for folder in ["blob_analysis", "collection_count", "image_loader"]:
        raw = json.loads((root / folder / "manifest.json").read_text(encoding="utf-8"))
        values = {f.name: raw[f.name] for f in fields(PluginManifest)
                  if f.name in raw and f.name != "editor"}
        manifest = PluginManifest(**values)
        result[manifest.operatorId] = manifest
    return result


@pytest.fixture
def project(manifests):
    count = next(m.operatorId for m in manifests.values() if "count" in m.outputPorts)
    raw = {"schemaVersion": "2.1", "project": {"projectId": "p1-test", "name": "Test",
           "createdAt": "2026-09-26T00:00:00Z", "updatedAt": "2026-09-26T00:00:00Z"},
           "entryWorkflowId": "main", "workflowOrder": ["main", "child"], "workflows": {
               "main": {"name": "Main", "outputs": {"count": "integer"}, "nodes": [
                   {"nodeId": "a", "operatorId": count, "outputPorts": {"count": "int"}},
                   {"nodeId": "b", "operatorId": count},
                   {"nodeId": "blob", "operatorId": "vision.analysis.blob"},
                   {"nodeId": "callA", "kind": "subflow", "targetWorkflowId": "child"},
                   {"nodeId": "callB", "kind": "subflow", "targetWorkflowId": "child"},
                   {"nodeId": "loop", "kind": "loop", "loop": {"bodyWorkflowId": "child",
                    "contractVersion": 1, "mode": "repeat", "repeatCount": 1, "maxIterations": 1}},
               ]},
               "child": {"name": "Child", "nodes": [{"nodeId": "c", "operatorId": count}]},
           }}
    for workflow in raw["workflows"].values():
        workflow["nodes"].extend([
            {"nodeId": "input", "kind": "workflow_input"},
            {"nodeId": "output", "kind": "workflow_output", "inputPorts": workflow.get("outputs", {})},
        ])
    payload = migrateProjectPayload(raw, enablePresentation=True)
    payload["presentation"] = {
        "defaultPageId": "overview", "pageOrder": ["overview", "detail"],
        "resultScopes": {"root": {"entryWorkflowId": "main", "scopeWorkflowId": "main"}},
        "dataSources": {"count": {"kind": "node_output", "workflowId": "main", "nodeId": "a",
                                  "port": "count", "resultScopeId": "root", "expectedType": "integer"}},
        "pages": {"overview": {"name": "Overview", "components": [
            {"componentId": "number", "type": "number", "bindings": {"value": "count"}},
            {"componentId": "go", "type": "navigation_button", "layout": {"row": 1},
             "actions": {"clicked": {"type": "navigate", "pageId": "detail",
                                     "context": "displayed_result", "resultScopeId": "root"}}}]},
            "detail": {"name": "Detail", "resultScopeIds": ["root"]}}}
    return ProjectDocument.model_validate(payload)
