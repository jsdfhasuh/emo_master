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
    assert output["workflows"]["main"]["layout"]["nodePositions"]["subflow"]["x"] == 40.0


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
    assert model.nodes == {}
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
    assert model.nodes[nodeId].loop["maxIterations"] == 2
