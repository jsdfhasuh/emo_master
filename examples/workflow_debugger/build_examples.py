"""Build deterministic, device-free Designer debugger acceptance projects."""
from copy import deepcopy
import json
from pathlib import Path

import cv2
import numpy as np

from emo_master import __version__
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def edge(source, port, target, targetPort):
    return dict(fromNode=source, fromPort=port, toNode=target, toPort=targetPort)


def operator(nodeId, operatorId, **params):
    return dict(nodeId=nodeId, kind="operator", operatorId=operatorId, params=params)


def workflow(name, inputs, outputs, nodes, edges):
    nodes = [dict(nodeId="input", kind="workflow_input"), *nodes, dict(nodeId="output", kind="workflow_output")]
    return dict(name=name, inputs=inputs, outputs=outputs, nodes=nodes, edges=edges,
                layout={"nodePositions": {node["nodeId"]: dict(x=index * 260, y=100) for index, node in enumerate(nodes)}})


def project(name, workflows):
    return dict(schemaVersion="2.1", project=dict(projectId=name, name=name, revision=1,
        createdAt="2026-10-10T00:00:00Z", updatedAt="2026-10-10T00:00:00Z"),
        entryWorkflowId="main", workflowOrder=list(workflows), workflows=workflows)


def projects():
    nested = project("debug-01-nested-calls", {
        "main": workflow("Main - two calls, output 7", {}, {"value": "number"}, [
            operator("seed", "vision.value.number", value=1),
            dict(nodeId="first", kind="subflow", targetWorkflowId="child"),
            dict(nodeId="second", kind="subflow", targetWorkflowId="child")], [
            edge("seed", "value", "first", "after"), edge("first", "value", "second", "after"),
            edge("second", "value", "output", "value")]),
        "child": workflow("Child - trial 99, original stays 7", {"after": "number"}, {"value": "number"}, [
            operator("number", "vision.value.number", value=7)], [edge("number", "value", "output", "value")])})
    loopInputs, loopOutputs = {"items": "list<number>"}, {"result": "list<boolean>", "index": "list<integer>"}
    foreach = project("debug-02-foreach", {
        "main": workflow("ForEach - [1, 5, 9]", loopInputs, loopOutputs, [dict(nodeId="foreach", kind="loop",
            inputPorts=loopInputs, outputPorts=loopOutputs,
            loop=dict(contractVersion=2, mode="foreach", bodyWorkflowId="body", itemInputPort="item",
                      indexInputPort="index", maxIterations=10, timeoutMs=10000))], [
            edge("input", "items", "foreach", "items"), edge("foreach", "result", "output", "result"),
            edge("foreach", "index", "output", "index")]),
        "body": workflow("Body - compare item >= 5", {"item": "number", "index": "integer"},
            {"result": "boolean", "index": "integer"}, [operator("compare", "vision.compare.number", operator="gte", rightValue=5)], [
                edge("input", "item", "compare", "left"), edge("compare", "result", "output", "result"),
                edge("input", "index", "output", "index")])})
    image = project("debug-03-image", {
        "main": workflow("Image - upload sample.png", {"image": "image"}, {"mask": "image", "threshold": "number"}, [
            dict(nodeId="process", kind="subflow", targetWorkflowId="process")], [
                edge("input", "image", "process", "image"), edge("process", "mask", "output", "mask"),
                edge("process", "threshold", "output", "threshold")]),
        "process": workflow("Process - blur then threshold", {"image": "image"}, {"mask": "image", "threshold": "number"}, [
            operator("blur", "vision.preprocess.blur", mode="gaussian", kernelSize=3),
            operator("threshold", "vision.preprocess.threshold", mode="fixed", threshold=127)], [
                edge("input", "image", "blur", "image"), edge("blur", "image", "threshold", "image"),
                edge("blur", "frame", "threshold", "frame"), edge("threshold", "mask", "output", "mask"),
                edge("threshold", "threshold", "output", "threshold")])})
    return {"01-nested-calls.emoproj": nested, "02-foreach.emoproj": foreach, "03-image.emoproj": image}


def build():
    scan = PluginRegistry(coreVersion=__version__).scan(ROOT / "src/emo_master/plugins")
    if scan.rejectedOperators:
        raise RuntimeError(scan.rejectedOperators)
    result = projects()
    for filename, payload in result.items():
        for flow in payload["workflows"].values():
            for node in flow["nodes"]:
                kind = node["kind"]
                if kind == "operator":
                    manifest = scan.activeOperators[node["operatorId"]].manifest
                    node["paramSchema"] = deepcopy(manifest.paramSchema)
                    for key in ("inputPorts", "outputPorts"):
                        node[key] = {port: normalizePortType(spec) for port, spec in getattr(manifest, key).items()}
                    node["displayName"] = node["nodeId"]
                elif kind == "subflow":
                    child = payload["workflows"][node["targetWorkflowId"]]
                    node.update(inputPorts=deepcopy(child["inputs"]), outputPorts=deepcopy(child["outputs"]))
                elif kind == "workflow_input":
                    node["outputPorts"] = deepcopy(flow["inputs"])
                elif kind == "workflow_output":
                    node["inputPorts"] = deepcopy(flow["outputs"])
        WorkflowCompiler(scan.activeOperators).compile(ProjectDocument.model_validate(payload))
        (HERE / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pixels = np.full((240, 320), 30, np.uint8)
    cv2.rectangle(pixels, (35, 40), (130, 185), 220, -1)
    cv2.circle(pixels, (225, 120), 52, 180, -1)
    ok, png = cv2.imencode(".png", pixels)
    if not ok:
        raise RuntimeError("sample image encoding failed")
    (HERE / "sample.png").write_bytes(png.tobytes())
    (HERE / "items.json").write_text("[1, 5, 9]\n", encoding="utf-8")
    print("Built three validated device-free .emoproj files, sample.png and items.json")


if __name__ == "__main__":
    build()
