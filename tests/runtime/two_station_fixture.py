"""Spawned Job orchestration fixture, not field-algorithm equivalence."""
from copy import deepcopy

from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from tests.runtime.production_fixture import productionProject, saveDocument


def twoStationProject(root, *, autoStart=False):
    original = productionProject(root, save=False, autoStart=autoStart)
    payload = migrateProjectPayload(original.model_dump(), enableGlobalVariables=True)
    payload["workflows"]["second"] = deepcopy(payload["workflows"]["main"])
    payload["workflowOrder"].append("second")
    pages = payload["presentation"]
    pages["resultScopes"]["second-root"] = dict(entryWorkflowId="second", scopeWorkflowId="second")
    pages["pages"]["second"] = deepcopy(pages["pages"]["main"])
    pages["pages"]["second"]["name"] = "工位二检测"
    pages["pageOrder"].append("second")
    for key, source in list(pages["dataSources"].items()):
        pages["dataSources"][key + "-second"] = dict(source, resultScopeId="second-root", workflowId="second")
    for component in pages["pages"]["second"]["components"]:
        component["componentId"] += "-second"
        component["bindings"] = {key: value + "-second" for key, value in component["bindings"].items()}
    binding = deepcopy(payload["resources"]["parameterBindings"][0])
    binding["target"]["workflowId"] = "second"
    payload["resources"]["parameterBindings"].append(binding)
    for index, wid in enumerate(("main", "second"), 1):
        variableId = f"s{index}-count"
        payload["globalVariables"][variableId] = dict(name=f"工位{index}测试接收数", type="integer", initialValue=0, lifetime="job")
        workflow = payload["workflows"][wid]
        workflow["nodes"].append(dict(nodeId="received", operatorId="vision.state.variable_write",
            params=dict(variableId=variableId, operation="increment")))
        workflow["edges"].append(dict(fromNode="count", fromPort="count", toNode="received", toPort="after"))
    document = ProjectDocument.model_validate(payload)
    saveDocument(root, document)
    return document
