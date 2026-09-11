from copy import deepcopy

import pytest

from emo_master.apps.designer.controllers.workflow_controller import WorkflowController
from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.flow_scene import FlowScene


def _payload() -> dict[str, object]:
    return {
        "schemaVersion": "2.0",
        "project": {
            "projectId": "designer-project",
            "name": "Designer",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main", "body"],
        "workflows": {
            "main": {
                "name": "Main",
                "inputs": {},
                "outputs": {},
                "nodes": [
                    {
                        "nodeId": "subflow",
                        "kind": "subflow",
                        "targetWorkflowId": "body",
                        "inputPorts": {"value": "json"},
                        "outputPorts": {"result": "json"},
                    }
                ],
                "edges": [],
                "layout": {"nodePositions": {"subflow": {"x": 40, "y": 50}}},
            },
            "body": {
                "name": "Body",
                "inputs": {"value": "json"},
                "outputs": {"result": "json"},
                "nodes": [],
                "edges": [],
                "layout": {"nodePositions": {}},
            },
        },
        "runtime": {"maxConcurrentJobs": 2},
        "dependencies": {"operators": []},
        "devices": {"bindings": {}},
    }


def testWorkflowStoreRoundTripsMultipleWorkflowsAndEntry() -> None:
    store = WorkflowStore(_payload())
    store.setEntryWorkflow("body")
    bodyId = store.addWorkflow("Review")
    output = store.toPayload("Designer v2")

    assert output["entryWorkflowId"] == "body"
    assert output["workflowOrder"] == ["main", "body", bodyId]
    assert (
        output["workflows"]["main"]["layout"]["nodePositions"]["subflow"]["x"] == 40.0
    )


def testWorkflowStoreAdvancesRevisionOnlyAfterCommittedSave() -> None:
    store = WorkflowStore(_payload())

    first = store.toPayload()
    assert first["project"]["revision"] == 2
    store.commitSavedPayload(first)
    second = store.toPayload()
    assert second["project"]["revision"] == 3

    third = store.toPayload()
    assert third["project"]["revision"] == 3


def testWorkflowControllerSwitchesGraphsAndRejectsReferencedDelete() -> None:
    store = WorkflowStore(_payload())
    model = FlowGraphModel()
    scene = FlowScene()
    controller = WorkflowController(store, model, scene)
    controller.loadPayload(_payload())
    assert "subflow" in model.nodes
    controller.switchWorkflow("body")
    assert {node.kind for node in model.nodes.values()} == {
        "workflow_input",
        "workflow_output",
    }
    try:
        controller.deleteWorkflow("body")
    except ValueError as err:
        assert "main" in str(err)
    else:
        raise AssertionError("referenced workflow deletion should fail")


def testWorkflowControllerConfiguresSubflowPortsAndLoop() -> None:
    store = WorkflowStore(_payload())
    model = FlowGraphModel()
    scene = FlowScene()
    controller = WorkflowController(store, model, scene)
    controller.loadPayload(_payload())
    nodeId = model.addNode("", "Loop", {}, {})
    controller.configureSubflowNode(nodeId, "body")
    assert model.nodes[nodeId].inputPorts == {"value": "json"}
    assert model.nodes[nodeId].outputPorts == {"result": "json"}
    controller.configureLoopNode(
        nodeId,
        {"mode": "repeat", "repeatCount": 2, "maxIterations": 2, "timeoutMs": 1000},
    )
    assert model.nodes[nodeId].kind == "loop"
    assert model.nodes[nodeId].targetWorkflowId is None
    assert model.nodes[nodeId].loop["maxIterations"] == 2


def testWorkflowControllerRejectsLoopConfigWithoutPartiallyMutatingNode() -> None:
    store = WorkflowStore(_payload())
    model = FlowGraphModel()
    controller = WorkflowController(store, model, FlowScene())
    controller.loadPayload(_payload())
    sourceId = model.addNode("vision.test.source", "Source", {}, {"value": "json"})
    nodeId = model.addNode(
        "vision.test.original",
        "Original",
        {"value": "json"},
        {"result": "json"},
        paramSchema={"type": "object"},
    )
    model.nodes[nodeId].params = {"threshold": 3}
    model.connectNodes(sourceId, "value", nodeId, "value")
    originalNode = deepcopy(model.nodes[nodeId])
    originalEdges = list(model.edges)

    with pytest.raises(ValueError, match="index input does not exist"):
        controller.configureLoopNode(
            nodeId,
            {
                "contractVersion": 2,
                "mode": "foreach",
                "bodyWorkflowId": "body",
                "itemInputPort": "value",
                "indexInputPort": "missing",
                "maxIterations": 10,
                "timeoutMs": 0,
            },
        )

    assert model.nodes[nodeId] == originalNode
    assert model.edges == originalEdges


def testWorkflowInterfaceRefreshesAllSubflowPortsAndPrunesInvalidEdges() -> None:
    payload = _payload()
    payload["workflows"]["main"]["nodes"].append(
        {
            "nodeId": "subflow-two",
            "kind": "subflow",
            "targetWorkflowId": "body",
            "inputPorts": {"value": "json"},
            "outputPorts": {"result": "json"},
        }
    )
    payload["workflows"]["main"]["edges"] = [
        {
            "fromNode": "subflow",
            "fromPort": "result",
            "toNode": "subflow-two",
            "toPort": "value",
        }
    ]
    store = WorkflowStore(payload)
    model = FlowGraphModel()
    scene = FlowScene()
    controller = WorkflowController(store, model, scene)
    controller.loadPayload(payload)

    controller.setWorkflowInterface(
        "body",
        {"renamed": "string"},
        {"done": "string"},
    )

    for nodeId in ("subflow", "subflow-two"):
        assert model.nodes[nodeId].inputPorts == {"renamed": "string"}
        assert model.nodes[nodeId].outputPorts == {"done": "string"}
    assert model.edges == []
    assert store.get("main").edges == []


def testWorkflowInterfaceRefreshesRepeatAndForEachDerivedPorts() -> None:
    payload = _payload()
    payload["schemaVersion"] = "2.1"
    payload["workflows"]["main"]["nodes"].extend(
        [
            {
                "nodeId": "repeat",
                "kind": "loop",
                "inputPorts": {"value": "json"},
                "outputPorts": {"result": "json"},
                "loop": {
                    "contractVersion": 2,
                    "mode": "repeat",
                    "bodyWorkflowId": "body",
                    "repeatCount": 1,
                    "maxIterations": 1,
                    "timeoutMs": 0,
                },
            },
            {
                "nodeId": "foreach",
                "kind": "loop",
                "inputPorts": {"items": "list<json>"},
                "outputPorts": {"result": "list<json>"},
                "loop": {
                    "contractVersion": 2,
                    "mode": "foreach",
                    "bodyWorkflowId": "body",
                    "itemInputPort": "value",
                    "maxIterations": 10,
                    "timeoutMs": 0,
                },
            },
        ]
    )
    store = WorkflowStore(payload)
    model = FlowGraphModel()
    controller = WorkflowController(store, model, FlowScene())
    controller.loadPayload(payload)

    report = controller.setWorkflowInterface(
        "body",
        {"image": "image"},
        {"edges": "image"},
    )

    assert model.nodes["repeat"].inputPorts == {"image": "image"}
    assert model.nodes["repeat"].outputPorts == {"edges": "image"}
    assert model.nodes["foreach"].inputPorts == {"items": "list<image>"}
    assert model.nodes["foreach"].outputPorts == {"edges": "list<image>"}
    assert model.nodes["foreach"].loop["itemInputPort"] == "image"
    assert report == []


def testWorkflowStoreRejectsInconsistentWorkflowOrder() -> None:
    payload = _payload()
    payload["workflowOrder"] = ["main"]

    with pytest.raises(ValueError, match="every workflow exactly once"):
        WorkflowStore(payload)


def testWorkflowStoreRejectsMissingEntryWorkflow() -> None:
    payload = _payload()
    payload["entryWorkflowId"] = "missing"

    with pytest.raises(ValueError, match="entryWorkflowId"):
        WorkflowStore(payload)


def testWorkflowStoreRejectsDanglingSubflowReference() -> None:
    payload = _payload()
    mainWorkflow = payload["workflows"]["main"]
    mainWorkflow["nodes"][0]["targetWorkflowId"] = "missing"

    with pytest.raises(ValueError, match="targetWorkflowId"):
        WorkflowStore(payload)


def testWorkflowStoreRejectsDanglingLoopReference() -> None:
    payload = _payload()
    mainWorkflow = payload["workflows"]["main"]
    mainWorkflow["nodes"].append(
        {
            "nodeId": "repeat",
            "kind": "loop",
            "loop": {
                "mode": "repeat",
                "bodyWorkflowId": "missing",
                "repeatCount": 1,
                "maxIterations": 1,
                "timeoutMs": 1000,
            },
        }
    )

    with pytest.raises(ValueError, match="bodyWorkflowId"):
        WorkflowStore(payload)
