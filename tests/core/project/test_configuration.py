from copy import deepcopy

import pytest

from emo_master.core.project.configuration import (
    projectChanges, projectConfigurationChanges, selectedCandidate, executionDigest, requiredDeviceChanges, changeCategory)
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_runtime import simulatedProject


def testDispatchDependencyClosureIncludesTransitiveWorkflowAndOrderOnly():
    from emo_master.core.project.configuration import requiredProjectChanges
    from tests.runtime.test_frame_dispatch import dispatchProject
    online = simulatedProject()
    local = dispatchProject()
    local["workflows"]["main"]["nodes"].append(dict(nodeId="unfinished", operatorId="missing.plugin"))
    selected = ["/workflows/main/nodes/dispatch"]
    required = requiredProjectChanges(online, local, selected)
    assert set(required) == {"/workflows/consumer", "/workflowOrder"}
    result = selectedCandidate(online, local, selected + list(required))
    assert not any(n["nodeId"] == "unfinished" for n in result["workflows"]["main"]["nodes"])
    assert not requiredProjectChanges(online, local, selected + list(required))


def testResourceDependencyClosureAndSelectedDeletion():
    from emo_master.core.project.configuration import requiredProjectChanges
    online = simulatedProject()
    local = deepcopy(online)
    local["resources"]["items"]["model"] = dict(path="model.onnx", purpose="model", size=1, sha256="a" * 64)
    local["resources"]["parameterBindings"] = [dict(resourceId="model", target=dict(
        deviceId="camera", section="defaults", parameterPath=["model"]))]
    selected = ["/resources/parameterBindings"]
    assert set(requiredProjectChanges(online, local, selected)) == {"/resources/items/model"}
    online = deepcopy(local)
    del local["resources"]["items"]["model"]
    with pytest.raises(ValueError, match="missing project dependency"):
        requiredProjectChanges(online, local, ["/resources/items/model"])


def testPageDependencyClosureIncludesSourcesScopesAndNewWorkflow():
    from emo_master.core.project.configuration import requiredProjectChanges
    online = simulatedProject()
    online["presentation"] = dict(pages={}, dataSources={}, resultScopes={}, pageOrder=[], defaultPageId=None)
    local = deepcopy(online)
    local["workflows"]["inspection"] = dict(name="Inspection", nodes=[dict(nodeId="result")], edges=[])
    local["workflowOrder"].append("inspection")
    local["presentation"] = dict(pages={"new": dict(components=[dict(bindings={"value": "source"})])},
        dataSources={"source": dict(resultScopeId="scope", workflowId="inspection", nodeId="result")},
        resultScopes={"scope": dict(entryWorkflowId="inspection", scopeWorkflowId="inspection")},
        pageOrder=["new"], defaultPageId="new")
    selected = ["/presentation/pages/new", "/presentation/defaultPageId"]
    required = requiredProjectChanges(online, local, selected)
    assert set(required) == {"/presentation/dataSources/source", "/presentation/resultScopes/scope",
        "/presentation/pageOrder", "/workflows/inspection", "/workflowOrder"}


def testSelectedExposureDoesNotIncludeInvalidUnselectedDraft():
    online = ProjectDocument.model_validate(simulatedProject()).model_dump(mode="json")
    local = deepcopy(online)
    local["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    local["devices"]["definitions"]["camera"]["connection"]["serial"] = "UNSELECTED"
    local["workflows"]["main"]["nodes"].append(dict(nodeId="unfinished", operatorId="missing.plugin"))
    candidate = selectedCandidate(online, local, ["/devices/definitions/camera/defaults/exposureUs"])
    assert ProjectDocument.model_validate(candidate)
    expected = deepcopy(online)
    expected["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    assert candidate == expected


def testMissingDeviceDependencyIncludesOnlyExactNewDefinition():
    online = simulatedProject()
    local = deepcopy(online)
    local["devices"]["definitions"]["new-camera"] = deepcopy(local["devices"]["definitions"]["camera"])
    local["devices"]["definitions"]["new-camera"]["connection"]["serial"] = "NEW"
    local["devices"]["definitions"]["camera"]["connection"]["serial"] = "UNSELECTED"
    local["workflows"]["main"]["nodes"][1]["deviceBindings"]["camera"]["deviceId"] = "new-camera"
    local["workflows"]["main"]["nodes"].append(dict(nodeId="unfinished", operatorId="missing.plugin"))
    path = "/workflows/main/nodes/capture/deviceBindings/camera/deviceId"
    required = requiredDeviceChanges(online, local, [path])
    assert required == {"/devices/definitions/new-camera": ["main/capture"]}
    candidate = selectedCandidate(online, local, [path, *required])
    assert candidate["devices"]["definitions"]["camera"] == online["devices"]["definitions"]["camera"]
    assert len(candidate["workflows"]["main"]["nodes"]) == 3
    assert not requiredDeviceChanges(online, local, [path, *required])


def testDependencySelectionDoesNotUndoDeletionOrInventMissingDefinition():
    online = simulatedProject()
    local = deepcopy(online)
    del local["devices"]["definitions"]["camera"]
    with pytest.raises(ValueError, match="missing device"):
        requiredDeviceChanges(online, local, ["/devices/definitions/camera"])


@pytest.mark.parametrize("parts,category", [
    (("devices", "definitions", "camera", "defaults", "gain"), "device_parameters"),
    (("devices", "definitions", "camera", "connection", "serial"), "device_connection"),
    (("workflows", "main", "layout"), "layout"),
    (("workflows", "main", "nodes", "node", "deviceOverrides"), "node_parameters"),
    (("workflows", "main", "nodes", "node", "deviceBindings"), "device_binding"),
    (("resources", "items", "model"), "resources"),
    (("presentation", "pages"), "presentation"),
])
def testComparisonCategoryPreservesBindingAndParameterKinds(parts, category):
    assert changeCategory(parts) == category


def testNodeIdSelectionSurvivesReorderAndEscapesPointers():
    online = {"workflows": {"main": {"nodes": [dict(nodeId="a/b~c", params={"value": 1}), dict(nodeId="other")]}}}
    local = deepcopy(online)
    local["workflows"]["main"]["nodes"].reverse()
    local["workflows"]["main"]["nodes"][1]["params"]["value"] = 2
    changes = projectChanges(online, local)
    assert [x["path"] for x in changes] == ["/workflows/main/nodes/a~1b~0c/params/value"]
    candidate = selectedCandidate(online, local, [changes[0]["path"]])
    assert candidate["workflows"]["main"]["nodes"][0]["params"]["value"] == 2


def testMissingNullFalseZeroAndEmptyAreNotConflated():
    online = {"field": None, "other": False, "zero": 0, "text": ""}
    local = {"other": 0, "zero": False, "text": None, "new": None}
    changes = projectChanges(online, local)
    assert len(changes) == 5
    assert selectedCandidate(online, local, [x["path"] for x in changes]) == local


def testSelectionRejectsStaleDuplicateAndEmptyPaths():
    for paths in ([], ["/missing"], ["/a", "/a"]):
        with pytest.raises(ValueError):
            selectedCandidate({"a": 1}, {"a": 2}, paths)


def testLayoutAndLabelsDoNotChangeExecutionRevision():
    online = simulatedProject()
    local = deepcopy(online)
    local["workflows"]["main"]["layout"] = {"nodePositions": {"capture": {"x": 5, "y": 8}}}
    local["workflows"]["main"]["name"] = "renamed"
    local["project"]["revision"] += 1
    local["devices"]["definitions"]["camera"].update(name="Renamed camera", description="Display only")
    assert executionDigest(online) == executionDigest(local)
    local["devices"]["definitions"]["camera"]["defaults"]["gain"] = 1
    assert executionDigest(online) != executionDigest(local)


@pytest.mark.parametrize('layout', [None, {}, {'nodePositions': {}},
    {'nodePositions': {'capture': {'x': 5, 'y': 8}}}])
def testConfigurationComparisonIgnoresPositionAddMoveAndRemove(layout):
    online = simulatedProject()
    workflow = online['workflows']['main']
    if layout is None:
        workflow.pop('layout', None)
    else:
        workflow['layout'] = deepcopy(layout)
    local = deepcopy(online)
    local['workflows']['main']['layout'] = {'nodePositions': {'capture': {'x': 20, 'y': 40}}}
    before, after = deepcopy(online), deepcopy(local)
    assert projectChanges(online, local)
    assert projectConfigurationChanges(online, local) == []
    assert projectConfigurationChanges(local, online) == []
    assert online == before and local == after


def testConfigurationComparisonKeepsNamesParametersBindingsAndStructure():
    online = simulatedProject()
    local = deepcopy(online)
    workflow = local['workflows']['main']
    workflow['layout'] = {'nodePositions': {'capture': {'x': 20, 'y': 40}}}
    workflow['name'] = 'Renamed workflow'
    node = next(node for node in workflow['nodes'] if node['nodeId'] == 'capture')
    node.update(displayName='Renamed capture', params={'gain': 1})
    node['deviceBindings']['camera']['deviceId'] = 'other-camera'
    workflow['edges'] = []
    workflow['nodes'].append(dict(nodeId='new-node', operatorId='new-operator'))
    changes = projectConfigurationChanges(online, local)
    paths = {change['path'] for change in changes}
    assert paths == {'/workflows/main/name', '/workflows/main/nodes/capture/displayName',
        '/workflows/main/nodes/capture/params', '/workflows/main/nodes/capture/deviceBindings/camera/deviceId',
        '/workflows/main/edges', '/workflows/main/nodes/new-node'}
    candidate = selectedCandidate(online, local, list(paths))
    assert candidate['workflows']['main']['layout'] == online['workflows']['main']['layout']
    assert not projectConfigurationChanges(candidate, local)


def testConfigurationComparisonPreservesWholeWorkflowAndProjectReplacement():
    online = simulatedProject()
    local = deepcopy(online)
    local['workflows']['other'] = deepcopy(local['workflows']['main'])
    local['workflows']['other']['layout'] = {'nodePositions': {'capture': {'x': 20, 'y': 40}}}
    assert projectConfigurationChanges(online, local) == projectChanges(online, local)
    assert selectedCandidate(online, local, ['/workflows/other']) == local
    assert projectConfigurationChanges(local, online) == projectChanges(local, online)
    local['project']['projectId'] = 'replacement-project'
    local['workflows']['main']['layout'] = deepcopy(local['workflows']['other']['layout'])
    assert projectConfigurationChanges(online, local) == projectChanges(online, local)
    paths = [change['path'] for change in projectConfigurationChanges(online, local)]
    assert selectedCandidate(online, local, paths) == local


def testStaticPageSelectionRequiresOnlyItsMissingResource():
    from emo_master.core.project.configuration import requiredProjectChanges
    online = simulatedProject()
    online['presentation'] = dict(defaultPageId='page', pageOrder=['page'], pages={
        'page': dict(name='Page', components=[])})
    local = deepcopy(online)
    local['presentation']['pages']['page']['components'] = [dict(componentId='image', type='image',
        props=dict(imageResourceId='page-image'))]
    for key in ('page-image', 'unselected'):
        local['resources']['items'][key] = dict(path=key + '.png', purpose='page_image', size=4, sha256='a' * 64)
    required = requiredProjectChanges(online, local, ['/presentation/pages/page/components'])
    assert set(required) == {'/resources/items/page-image'}
    assert executionDigest(online) == executionDigest(local)
    local['presentation']['pages']['page']['components'][0]['bindings'] = {'image': 'result'}
    assert executionDigest(online) != executionDigest(local)


def testCycleParametersKeepMaterializedPathsAndDoNotMutateOnlineSnapshot():
    from types import SimpleNamespace
    from emo_master.core.project.configuration import applyCycleParameters
    materialized = {"workflows": {"main": {"nodes": [dict(nodeId="node", operatorId="test.operator",
        params=dict(threshold=12, model="/prepared/model.onnx", state=7))]}}}
    online = deepcopy(materialized)
    online["workflows"]["main"]["nodes"][0]["params"].update(threshold=34, model="model.onnx", state=0)
    untouched = deepcopy(online)
    registry = {"test.operator": SimpleNamespace(manifest=SimpleNamespace(nextCycleParameterPaths=("threshold",)))}
    applyCycleParameters(materialized, online, registry)
    assert materialized["workflows"]["main"]["nodes"][0]["params"] == dict(threshold=34, model="/prepared/model.onnx", state=7)
    assert online == untouched
    online["workflows"]["main"]["nodes"][0]["params"].pop("threshold")
    applyCycleParameters(materialized, online, registry)
    assert "threshold" not in materialized["workflows"]["main"]["nodes"][0]["params"]
