from copy import deepcopy

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.collection_sort.operator import CollectionSortOperator
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from emo_master.plugins.builtins.collection_count.operator import CollectionCountOperator
from tests.plugins.test_collection_operators import _detections


@pytest.mark.parametrize("legacy", [False, True])
def testSavedDetectionSortSelectCountWorkflow(tmp_path, legacy):
    payload = WorkflowStore().toPayload("Operator Modes Regression")
    assert payload["schemaVersion"] == "2.7"
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = {"detections": deepcopy(CollectionSortOperator.meta.inputPorts["detections"])}
    workflow["inputs"]["detections"]["required"] = True
    workflow["outputs"] = {"count": {"type": "integer", "required": True, "nullable": False}}
    entry, exitNode = workflow["nodes"]
    entry["outputPorts"] = {"detections": "detectionCollection"}
    exitNode["inputPorts"] = {"count": "integer"}
    classes = [CollectionSortOperator, CollectionSelectOperator, CollectionCountOperator]
    nodes = []
    for index, cls in enumerate(classes):
        meta = cls.meta
        params = {} if legacy else {"collectionType": "detections"}
        if index == 0 and legacy:
            params["key"] = "confidence"
        if index == 1:
            params.update(mode="first", index="unfinished index", count=0)
        schema = deepcopy(meta.paramSchema)
        if legacy:
            schema.pop("xModeOperator")
            schema["properties"].pop("collectionType")
            for field in schema["properties"].values():
                field.pop("xVisibleWhen", None)
                field.pop("xValidateWhen", None)
        nodes.append(dict(nodeId=f"op{index}", kind="operator", operatorId=meta.operatorId,
                          displayName=meta.displayName, params=params, paramSchema=schema,
                          inputPorts={key: spec["type"] for key, spec in meta.inputPorts.items()},
                          outputPorts={key: spec["type"] for key, spec in meta.outputPorts.items()}))
    workflow["nodes"] = [entry, *nodes, exitNode]
    workflow["edges"] = [
        dict(fromNode=entry["nodeId"], fromPort="detections", toNode="op0", toPort="detections"),
        dict(fromNode="op0", fromPort="sortedDetections", toNode="op1", toPort="detections"),
        dict(fromNode="op1", fromPort="selectedDetections", toNode="op2", toPort="detections"),
        dict(fromNode="op2", fromPort="count", toNode=exitNode["nodeId"], toPort="count"),
    ]
    document = ProjectDocument.model_validate(payload)
    target = tmp_path / "operator-modes.emoproj"
    saveProject(target, document.model_dump(mode="json"))
    reloaded = loadProjectDocument(target)
    assert reloaded == document
    assert reloaded.devices.definitions == {} and reloaded.communication is None
    registry = {cls.meta.operatorId: cls for cls in classes}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(reloaded)
    result = WorkflowRunner(compiled, registry).run(
        "main", {"detections": _detections().toPayload()},
        RunContext.root("operator-modes", "main", str(tmp_path)), CancellationToken())
    assert result.outputs["count"] == 1
    assert reloaded.workflows["main"].nodes[2].params["index"] == "unfinished index"


def testSavedBoundBlurIgnoresLiteralDraftAndValidatesEffectiveValue(tmp_path):
    from emo_master.plugins.builtins.blur.operator import BlurOperator
    from emo_master.core.workflow.errors import WorkflowCompileError
    from tests.runtime.test_global_variables import service, variable

    meta = BlurOperator.meta
    payload = WorkflowStore().toPayload("Bound Mode Draft")
    assert payload["schemaVersion"] == "2.7"
    payload["globalVariables"] = {"sigma": variable(45.0, "number")}
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = {"image": deepcopy(meta.inputPorts["image"])}
    workflow["outputs"] = {"image": deepcopy(meta.outputPorts["image"])}
    entry, exitNode = workflow["nodes"]
    entry["outputPorts"] = {"image": "image"}
    exitNode["inputPorts"] = {"image": "image"}
    workflow["nodes"].insert(1, dict(
        nodeId="blur", kind="operator", operatorId=meta.operatorId, displayName=meta.displayName,
        params={"mode": "bilateral", "sigmaColor": "unfinished", "sigmaX": "inactive draft"},
        paramSchema=deepcopy(meta.paramSchema),
        inputPorts={key: spec["type"] for key, spec in meta.inputPorts.items()},
        outputPorts={key: spec["type"] for key, spec in meta.outputPorts.items()},
        globalVariableBindings=[{"parameterPath": ["sigmaColor"], "variableId": "sigma"}]))
    workflow["edges"] = [
        dict(fromNode=entry["nodeId"], fromPort="image", toNode="blur", toPort="image"),
        dict(fromNode="blur", fromPort="image", toNode=exitNode["nodeId"], toPort="image"),
    ]
    document = ProjectDocument.model_validate(payload)
    target = tmp_path / "bound-mode.emoproj"
    saveProject(target, document.model_dump(mode="json"))
    reloaded = loadProjectDocument(target)
    assert reloaded == document
    registry = {meta.operatorId: BlurOperator}
    compiled = WorkflowCompiler(registry).compile(reloaded)
    state = service(tmp_path, payload["globalVariables"])
    runner = WorkflowRunner(compiled, registry, globalVariables=state)
    image = np.arange(144, dtype=np.uint8).reshape(12, 12)
    for sigma in (45.0, 10.0):
        state.set("sigma", sigma)
        result = runner.run("main", {"image": image}, RunContext.root("bound-mode", "main"), CancellationToken())
        np.testing.assert_array_equal(result.outputs["image"], cv2.bilateralFilter(image, 9, sigma, 75.0))
        assert result.outputs["image"].shape == image.shape
    assert reloaded.workflows["main"].nodes[1].params["sigmaColor"] == "unfinished"
    payload["globalVariables"]["sigma"]["initialValue"] = 0.0
    with pytest.raises(WorkflowCompileError):
        WorkflowCompiler(registry).compile(payload)
