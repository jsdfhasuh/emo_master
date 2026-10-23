from copy import deepcopy

from emo_master.apps.designer.state.flow_graph_model import FlowEdge
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from tests.designer.test_operator_modes import nodeFor, view


def testDynamicIndexIsVisibleInIndexModeAndRetainedWhenConnectedButInactive():
    node = nodeFor(CollectionSelectOperator.meta, {"collectionType": "detections", "mode": "first"})
    contract = deepcopy(node.inputPorts)
    assert set(view(node).inputPorts) == {"detections"}
    node.params["mode"] = "index"
    assert set(view(node).inputPorts) == {"detections", "index"}
    edge = FlowEdge("input", "targetIndex", node.nodeId, "index")
    presentation = view(node, [edge])
    assert "index" not in presentation.inputPortLabels
    assert any("index 输入优先" in text for text, _ in presentation.summaryLines)
    node.params["mode"] = "first"
    assert "index" in view(node, [edge]).inputPortLabels
    assert "index" not in view(node).inputPorts
    assert "index" in view(node, dynamic={"mode"}).inputPorts
    assert node.inputPorts == contract
