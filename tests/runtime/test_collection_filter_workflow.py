from copy import deepcopy

import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.context.global_variables import ReadOnlyVariables
from emo_master.core.contracts.geometry2d import BBox2D
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.global_variables import VariableBinding, VariableDefinition, validateEffectiveParams
from emo_master.core.project.snapshots import _parameters
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.collection_filter.operator import CollectionFilterOperator
from tests.plugins.test_collection_operators import _detections, _space


def filterProject(params, *, spatial=False):
    meta = CollectionFilterOperator.meta
    payload = WorkflowStore().toPayload("Collection Filter Regression")
    assert payload["schemaVersion"] == "2.7"
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = {name: deepcopy(meta.inputPorts[name])
                          for name in (["detections", "roi"] if spatial else ["detections"])}
    workflow["outputs"] = {name: deepcopy(meta.outputPorts[name])
                           for name in ("keptDetections", "keptCount", "rejectedCount")}
    entry, exitNode = workflow["nodes"]
    entry["outputPorts"] = {name: spec["type"] for name, spec in workflow["inputs"].items()}
    exitNode["inputPorts"] = {name: spec["type"] for name, spec in workflow["outputs"].items()}
    workflow["nodes"].insert(1, dict(nodeId="filter", kind="operator", operatorId=meta.operatorId,
        displayName="Filter", params=deepcopy(params),
        inputPorts={name: spec["type"] for name, spec in meta.inputPorts.items()},
        outputPorts={name: spec["type"] for name, spec in meta.outputPorts.items()},
        paramSchema=deepcopy(meta.paramSchema)))
    workflow["edges"] = [dict(fromNode=entry["nodeId"], fromPort=name, toNode="filter", toPort=name)
                          for name in workflow["inputs"]]
    workflow["edges"].extend(dict(fromNode="filter", fromPort=name, toNode=exitNode["nodeId"], toPort=name)
                              for name in workflow["outputs"])
    return ProjectDocument.model_validate(payload)


@pytest.mark.parametrize("params,spatial,expected", [
    ({"classIds": [1], "minConfidence": .6}, False, 1),
    ({"collectionType": "detections", "classIdsEnabled": True, "classIds": [1],
      "confidenceEnabled": True, "minConfidence": .6,
      "spatialEnabled": True, "spatialMode": "centerInside"}, True, 1),
    ({"collectionType": "detections", "classIdsEnabled": True, "classIds": [0],
      "confidenceEnabled": True, "minConfidence": .6}, False, 0),
    ({"collectionType": "detections", "classIdsEnabled": False, "classIds": "unfinished draft",
      "confidenceEnabled": False, "minConfidence": "unfinished number",
      "areaEnabled": True, "minArea": -5, "spatialEnabled": False, "spatialMode": "centerInside"}, False, 2),
])
def testSavedFilterProjectCompilesAndRunsWithoutChangingParameters(tmp_path, params, spatial, expected):
    document = filterProject(params, spatial=spatial)
    path = tmp_path / "filter.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    reloaded = loadProjectDocument(path)
    assert reloaded == document
    assert reloaded.devices.definitions == {}
    assert reloaded.communication is None
    registry = {CollectionFilterOperator.meta.operatorId: CollectionFilterOperator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(reloaded)
    inputs = {"detections": _detections().toPayload()}
    if spatial:
        inputs["roi"] = BBox2D(40, 40, 30, 30, _space()).toPayload()
    result = WorkflowRunner(compiled, registry).run(
        "main", inputs, RunContext.root("filter-regression", "main", str(tmp_path)), CancellationToken())
    assert result.outputs["keptCount"] == expected
    assert result.outputs["rejectedCount"] == 2 - expected
    assert reloaded.workflows["main"].nodes[1].params == params


@pytest.mark.parametrize("spatial", [False, True])
def testSchemaNormalizationPreservesLegacySpatialAndFilterBehavior(spatial):
    params = {"classIds": [1], "minConfidence": .6}
    inputs = {"detections": _detections().toPayload()}
    if spatial:
        params["spatialMode"] = "centerInside"
        inputs["roi"] = BBox2D(0, 0, 30, 30, _space()).toPayload()
    normalized = _parameters(params, CollectionFilterOperator.meta.paramSchema, "params")
    operator = CollectionFilterOperator()
    assert operator.validateParams(normalized) is None
    assert operator.executeNode(inputs, normalized, {})["outputs"] == operator.executeNode(inputs, params, {})["outputs"]


def testBoundValuesAndSchemaValidationIgnoreOnlyInactiveGroups():
    params = {"collectionType": "detections", "confidenceEnabled": False, "minConfidence": 2.,
              "classIdsEnabled": False, "classIds": "unfinished list"}
    bindings = [{"parameterPath": ["minConfidence"], "variableId": "confidence"}]
    result = validateEffectiveParams(params, bindings, CollectionFilterOperator.meta.paramSchema)
    assert result["minConfidence"] == 2. and result["classIds"] == "unfinished list"
    with pytest.raises(ValueError):
        validateEffectiveParams({**params, "confidenceEnabled": True}, bindings, CollectionFilterOperator.meta.paramSchema)


def testBoundFilterWorkflowRetainsInactiveDraftAndUsesResolvedThreshold(tmp_path):
    params = {"collectionType": "detections", "confidenceEnabled": True, "minConfidence": 0.,
              "classIdsEnabled": False, "classIds": "unfinished list"}
    project = filterProject(params)
    project.globalVariables["confidence"] = VariableDefinition.model_validate(
        dict(name="Confidence", type="number", initialValue=.6, kind="variable"))
    project.workflows["main"].nodes[1].globalVariableBindings = [
        VariableBinding(parameterPath=["minConfidence"], variableId="confidence")]
    registry = {CollectionFilterOperator.meta.operatorId: CollectionFilterOperator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(project)
    result = WorkflowRunner(compiled, registry, globalVariables=ReadOnlyVariables({"confidence": .6})).run(
        "main", {"detections": _detections().toPayload()},
        RunContext.root("bound-filter", "main", str(tmp_path)), CancellationToken())
    assert result.outputs["keptCount"] == 1
    assert project.workflows["main"].nodes[1].params == params
