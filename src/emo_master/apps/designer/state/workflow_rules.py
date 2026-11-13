"""Qt-free workflow interface rules shared by Designer and authoring sessions."""
from __future__ import annotations
from copy import deepcopy
from typing import cast
from emo_master.apps.designer.state.workflow_store import portTypes
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.workflow.loop_contracts import LoopContract, deriveLoopContract, loopWorkflowReferenceFields

def refreshWorkflowReferences(
    store, targetWorkflowId: str | None = None
) -> list[str]:
    report: list[str] = []
    for workflow in store.workflows.values():
        for node in workflow.nodes:
            kind = node.get("kind")
            if kind == "operator":
                from emo_master.core.project.global_variables import definitions, variablePorts, VariableError
                try:
                    ports = variablePorts(node.get("operatorId"), node.get("params", {}),
                                          definitions(cast(dict, store.projectExtensions.get("globalVariables", {}))))
                    if ports is not None:
                        node["inputPorts"], node["outputPorts"] = portTypes(ports[0]), portTypes(ports[1])
                except VariableError:
                    pass
            if kind == "subflow":
                referencedId = node.get("targetWorkflowId")
                if not isinstance(referencedId, str):
                    continue
                if targetWorkflowId is not None and referencedId != targetWorkflowId:
                    continue
                target = store.workflows.get(referencedId)
                if target is None:
                    continue
                node["inputPorts"] = portTypes(target.inputs)
                node["outputPorts"] = portTypes(target.outputs)
                continue
            if kind != "loop":
                continue
            loop = node.get("loop")
            if not isinstance(loop, dict):
                continue
            referencedIds = {
                value
                for value in (loop.get(field) for field in loopWorkflowReferenceFields(loop))
                if isinstance(value, str)
            }
            if targetWorkflowId is not None and targetWorkflowId not in referencedIds:
                continue
            try:
                contract = loopContract(store, loop)
            except (KeyError, ValueError):
                continue
            nodeId = node.get("nodeId")
            if contract.issues:
                if isinstance(nodeId, str):
                    report.extend(
                        f"{workflow.workflowId}.{nodeId}: {issue.message}"
                        for issue in contract.issues
                    )
                # Keep the last configured contract and its connections until
                # the user explicitly repairs an invalid body mapping.
                continue
            node["loop"] = deepcopy(contract.normalizedConfig)
            node["inputPorts"] = dict(contract.inputPorts)
            node["outputPorts"] = dict(contract.outputPorts)
        report.extend(pruneWorkflowEdges(workflow))
    return report


def loopContract(store, config: dict[str, object]) -> LoopContract:
    bodyWorkflowId = config.get("bodyWorkflowId")
    if not isinstance(bodyWorkflowId, str):
        raise ValueError("bodyWorkflowId must reference an existing workflow")
    body = store.get(bodyWorkflowId)
    conditionWorkflowId = (config.get("conditionWorkflowId")
                           if "conditionWorkflowId" in loopWorkflowReferenceFields(config) else None)
    condition = (
        store.get(conditionWorkflowId)
        if isinstance(conditionWorkflowId, str)
        else None
    )
    return deriveLoopContract(
        config,
        body.inputs,
        body.outputs,
        condition.inputs if condition is not None else {},
        condition.outputs if condition is not None else {},
        cast(dict, store.projectExtensions.get("globalVariables", {})),
    )


def pruneWorkflowEdges(workflow) -> list[str]:
    nodesById = {
        node.get("nodeId"): node
        for node in workflow.nodes
        if isinstance(node, dict) and isinstance(node.get("nodeId"), str)
    }
    kept: list[dict[str, object]] = []
    removed: list[str] = []
    occupiedInputs: set[tuple[str, str]] = set()
    for edge in workflow.edges:
        if not isinstance(edge, dict):
            continue
        fromNode = edge.get("fromNode")
        fromPort = edge.get("fromPort")
        toNode = edge.get("toNode")
        toPort = edge.get("toPort")
        if (
            not isinstance(fromNode, str)
            or not isinstance(fromPort, str)
            or not isinstance(toNode, str)
            or not isinstance(toPort, str)
        ):
            continue
        source = nodesById.get(fromNode)
        target = nodesById.get(toNode)
        sourcePorts = source.get("outputPorts", {}) if source is not None else {}
        targetPorts = target.get("inputPorts", {}) if target is not None else {}
        sourceType = (
            sourcePorts.get(fromPort) if isinstance(sourcePorts, dict) else None
        )
        targetType = (
            targetPorts.get(toPort) if isinstance(targetPorts, dict) else None
        )
        inputKey = (toNode, toPort)
        valid = (
            isinstance(sourceType, str)
            and isinstance(targetType, str)
            and arePortTypesCompatible(sourceType, targetType)
            and inputKey not in occupiedInputs
        )
        if valid:
            kept.append(dict(edge))
            occupiedInputs.add(inputKey)
        else:
            removed.append(
                f"{workflow.workflowId}: {fromNode}.{fromPort} -> {toNode}.{toPort}"
            )
    workflow.edges = kept
    return removed
