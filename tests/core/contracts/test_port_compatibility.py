from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel, FlowScene
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.graph.validator import validateFlowGraph


def _ensureQApp() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide2.QtWidgets import QApplication

    if QApplication.instance() is None:
        _ = QApplication([])


def testPortCompatibilityUsesObjectAndAnyAsWildcards() -> None:
    assert arePortTypesCompatible("string", "string") is True
    assert arePortTypesCompatible("object", "string") is True
    assert arePortTypesCompatible("string", "object") is True
    assert arePortTypesCompatible("any", "json") is True
    assert arePortTypesCompatible("image", "mask") is False


def testGraphValidatorAcceptsWildcardPortTypes() -> None:
    result = validateFlowGraph(
        {
            "nodes": [
                {"nodeId": "source", "outputPorts": {"value": "object"}},
                {"nodeId": "target", "inputPorts": {"value": "string"}},
            ],
            "edges": [
                {
                    "fromNode": "source",
                    "fromPort": "value",
                    "toNode": "target",
                    "toPort": "value",
                }
            ],
        }
    )

    assert result["ok"] is True


def testFlowGraphModelAndFlowSceneUseTheSameWildcardRule() -> None:
    _ensureQApp()
    model = FlowGraphModel()
    source = model.addNode("source", "Source", {}, {"value": "string"})
    target = model.addNode("target", "Target", {"value": "object"}, {})
    model.connectNodes(source, "value", target, "value")

    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(source, "Source", 20.0, 20.0, {}, {"value": "string"})
    )
    scene.addFlowNode(
        FlowNodeViewModel(
            target,
            "Target",
            280.0,
            20.0,
            {"value": "object"},
            {},
        )
    )

    preview = scene.previewConnectionTarget(source, "value", target, "value")
    assert preview["valid"] is True
