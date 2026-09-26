"""Fixed local image project for the explicitly launched P2 demo. No devices."""
import hashlib

import cv2
import numpy as np

from emo_master.core.project.models import ProjectDocument
import json
from pathlib import Path


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


def pacedProject(root, count=30, large=False):
    project = sampleProject(root)
    if large:
        pixels = np.random.default_rng(20260926).integers(0, 256, (1080, 1920, 3), np.uint8)
        cv2.imwrite(str(root / "input.png"), pixels)
        data = (root / "input.png").read_bytes()
        project.resources.items["input"].sha256 = hashlib.sha256(data).hexdigest()
        project.resources.items["input"].size = len(data)
        # A fixed threshold limits geometry work; both baseline and channel use it.
        project.workflows["main"].nodes[2].params.update(threshold=250, minArea=4)
    raw = project.model_dump()
    raw["workflows"]["detect"] = raw["workflows"].pop("main")
    raw["workflows"]["detect"]["inputs"] = {"tick": "boolean"}
    raw["workflows"]["detect"]["nodes"][0]["outputPorts"] = {"tick": "boolean"}
    raw["workflows"]["tick"] = {"name": "5Hz test scheduler", "nodes": [
        {"nodeId": "input", "kind": "workflow_input"}, {"nodeId": "pace", "operatorId": "test.p2.pace"},
        {"nodeId": "detect", "kind": "subflow", "targetWorkflowId": "detect", "inputPorts": {"tick": "boolean"}},
        {"nodeId": "output", "kind": "workflow_output"}],
        "edges": [{"fromNode": "pace", "fromPort": "tick", "toNode": "detect", "toPort": "tick"}]}
    raw["workflows"]["main"] = {"name": "Paced root", "nodes": [
        {"nodeId": "input", "kind": "workflow_input"},
        {"nodeId": "loop", "kind": "loop", "loop": {"contractVersion": 1, "mode": "repeat",
            "bodyWorkflowId": "tick", "repeatCount": count, "maxIterations": count}},
        {"nodeId": "output", "kind": "workflow_output"}]}
    raw["workflowOrder"] = ["main", "tick", "detect"]
    raw["resources"]["parameterBindings"][0]["target"]["workflowId"] = "detect"
    path = [{"nodeId": "loop", "relation": "loop_body"}, {"nodeId": "detect", "relation": "subflow"}]
    raw["presentation"]["resultScopes"]["root"].update(scopeWorkflowId="detect", callPath=path)
    for source in raw["presentation"]["dataSources"].values():
        source.update(workflowId="detect", callPath=path)
    return ProjectDocument.model_validate(raw)


def pluginRoots(root):
    directory = root / "plugins" / "pace"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"operatorId": "test.p2.pace", "displayName": "P2 Test Pace",
        "version": "1.0.0", "entry": "examples.p2_pace:Pace", "category": "test", "iconKey": "test", "summary": "Controlled test scheduler",
        "inputPorts": {}, "outputPorts": {"tick": "boolean"}, "paramSchema": {"type": "object", "properties": {}},
        "minCoreVersion": "0.1.0", "maxCoreVersion": "1.x"}), encoding="utf-8")
    builtins = Path(__file__).resolve().parents[1] / "src/emo_master/plugins/builtins"
    return (str(builtins), str(root / "plugins"))
