from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel


def testFlowGraphModelCanExportProjectGraph() -> None:
    model = FlowGraphModel()
    sourceId = model.addNode(
        operatorId="vision.io.image_loader",
        displayName="Image Loader",
        inputPorts={},
        outputPorts={"image": "image"},
        paramSchema={"type": "object", "properties": {"imagePath": {"type": "string"}}},
    )
    targetId = model.addNode(
        operatorId="vision.edge.canny",
        displayName="Canny",
        inputPorts={"image": "image"},
        outputPorts={"edges": "image"},
        paramSchema={},
    )
    model.setNodeParams(sourceId, {"imagePath": "C:/tmp/a.png"})
    _ = model.connectNodes(sourceId, "image", targetId, "image")

    exportMethod = getattr(model, "toProjectGraph", None)
    assert callable(exportMethod)
    payload = exportMethod()
    assert isinstance(payload, dict)
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    assert len(nodes) == 2
    assert len(edges) == 1


def testFlowGraphModelCanLoadProjectGraph() -> None:
    model = FlowGraphModel()
    loadMethod = getattr(model, "loadProjectGraph", None)
    assert callable(loadMethod)

    payload = {
        "nodes": [
            {
                "nodeId": "node-a",
                "operatorId": "vision.io.image_loader",
                "displayName": "Image Loader",
                "inputPorts": {},
                "outputPorts": {"image": "image"},
                "paramSchema": {},
                "params": {"imagePath": "C:/tmp/a.png"},
            },
            {
                "nodeId": "node-b",
                "operatorId": "vision.edge.canny",
                "displayName": "Canny",
                "inputPorts": {"image": "image"},
                "outputPorts": {"edges": "image"},
                "paramSchema": {},
                "params": {},
            },
        ],
        "edges": [
            {
                "fromNode": "node-a",
                "fromPort": "image",
                "toNode": "node-b",
                "toPort": "image",
            }
        ],
    }
    loadMethod(payload)
    assert len(model.nodes) == 2
    assert len(model.edges) == 1
    assert model.nodes["node-a"].params["imagePath"] == "C:/tmp/a.png"
