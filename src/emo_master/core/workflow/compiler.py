from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from copy import deepcopy
from typing import Callable, cast

from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument, WorkflowDefinition, WorkflowNode
from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError
from emo_master.core.workflow.models import (
    CompiledEdge,
    CompiledNode,
    CompiledProject,
    CompiledWorkflow,
    freezeMapping,
)
from emo_master.core.workflow.validation import validateProjectDocument


class WorkflowCompiler:
    def __init__(
        self,
        operatorRegistry: Mapping[str, object] | None = None,
        maxCallDepth: int = 32,
    ) -> None:
        self.operatorRegistry = operatorRegistry or {}
        self.maxCallDepth = maxCallDepth

    def compile(
        self,
        project: ProjectDocument | dict[str, object],
        pluginRootPaths: tuple[str, ...] = (),
    ) -> CompiledProject:
        document = (
            project
            if isinstance(project, ProjectDocument)
            else ProjectDocument.model_validate(migrateProjectPayload(project))
        )
        issues = validateProjectDocument(document, self.operatorRegistry)
        if issues:
            raise WorkflowCompileError(issues)

        compiledWorkflows: dict[str, CompiledWorkflow] = {}
        for workflowId in document.workflowOrder:
            compiledWorkflows[workflowId] = self._compileWorkflow(
                document, workflowId, document.workflows[workflowId]
            )
        callGraph = self._buildCallGraph(compiledWorkflows)
        recursionIssues = self._findRecursion(callGraph, document.project.projectId)
        if recursionIssues:
            raise WorkflowCompileError(recursionIssues)
        return CompiledProject(
            projectId=document.project.projectId,
            revision=document.project.revision,
            entryWorkflowId=document.entryWorkflowId,
            workflows=freezeMapping(compiledWorkflows),
            workflowCallGraph=freezeMapping(callGraph),
            runtime=freezeMapping(document.runtime.model_dump(mode="python")),
            pluginRootPaths=tuple(pluginRootPaths),
        )

    def _compileWorkflow(
        self,
        document: ProjectDocument,
        workflowId: str,
        workflow: WorkflowDefinition,
    ) -> CompiledWorkflow:
        nodes: list[CompiledNode] = []
        nodeById: dict[str, CompiledNode] = {}
        issues: list[ValidationIssue] = []
        for node in workflow.nodes:
            inputPorts, outputPorts = self._nodePorts(document, workflowId, node)
            if node.kind == "operator" and node.operatorId:
                issues.extend(
                    self._validateOperator(document, workflowId, node)
                )
            compiled = CompiledNode(
                nodeId=node.nodeId,
                kind=node.kind,
                operatorId=node.operatorId,
                inputPorts=freezeMapping(inputPorts),
                outputPorts=freezeMapping(outputPorts),
                params=freezeMapping(deepcopy(node.params)),
                targetWorkflowId=node.targetWorkflowId,
                loop=freezeMapping(deepcopy(node.loop)),
            )
            nodes.append(compiled)
            nodeById[node.nodeId] = compiled

        edges = tuple(
            CompiledEdge(
                edge.fromNode, edge.fromPort, edge.toNode, edge.toPort
            )
            for edge in workflow.edges
        )
        incoming: dict[str, list[CompiledEdge]] = defaultdict(list)
        outgoing: dict[str, list[CompiledEdge]] = defaultdict(list)
        for edge in edges:
            source = nodeById.get(edge.fromNode)
            target = nodeById.get(edge.toNode)
            if source is None or target is None:
                continue
            if edge.fromPort not in source.outputPorts:
                issues.append(
                    ValidationIssue(
                        "E_EDGE_SOURCE_PORT_UNKNOWN",
                        f"unknown source port: {edge.fromNode}.{edge.fromPort}",
                        projectId=document.project.projectId,
                        workflowId=workflowId,
                    )
                )
            if edge.toPort not in target.inputPorts:
                issues.append(
                    ValidationIssue(
                        "E_EDGE_TARGET_PORT_UNKNOWN",
                        f"unknown target port: {edge.toNode}.{edge.toPort}",
                        projectId=document.project.projectId,
                        workflowId=workflowId,
                    )
                )
            elif edge.fromPort in source.outputPorts:
                sourceType = source.outputPorts[edge.fromPort]
                targetType = target.inputPorts[edge.toPort]
                if not _portsCompatible(sourceType, targetType):
                    issues.append(
                        ValidationIssue(
                            "E_PORT_TYPE_MISMATCH",
                            f"port type mismatch: {sourceType} -> {targetType}",
                            projectId=document.project.projectId,
                            workflowId=workflowId,
                        )
                    )
            incoming[edge.toNode].append(edge)
            outgoing[edge.fromNode].append(edge)
        for nodeId, nodeEdges in incoming.items():
            ports = [edge.toPort for edge in nodeEdges]
            if len(ports) != len(set(ports)):
                issues.append(
                    ValidationIssue(
                        "E_INPUT_PORT_MULTIPLE_EDGES",
                        f"input port has more than one incoming edge: {nodeId}",
                        projectId=document.project.projectId,
                        workflowId=workflowId,
                        nodeId=nodeId,
                    )
                )
        topology = _topological_order(nodes, edges)
        if topology is None:
            issues.append(
                ValidationIssue(
                    "E_WORKFLOW_CYCLE",
                    "workflow contains a cycle",
                    projectId=document.project.projectId,
                    workflowId=workflowId,
                )
            )
        if issues:
            raise WorkflowCompileError(issues)
        return CompiledWorkflow(
            workflowId=workflowId,
            name=workflow.name,
            inputs=freezeMapping(workflow.inputs),
            outputs=freezeMapping(workflow.outputs),
            nodes=tuple(nodes),
            edges=edges,
            nodeById=freezeMapping(nodeById),
            incomingEdges=freezeMapping({key: tuple(value) for key, value in incoming.items()}),
            outgoingEdges=freezeMapping({key: tuple(value) for key, value in outgoing.items()}),
            topologicalOrder=tuple(topology or ()),
        )

    def _nodePorts(
        self, document: ProjectDocument, workflowId: str, node: WorkflowNode
    ) -> tuple[dict[str, str], dict[str, str]]:
        if node.kind == "workflow_input":
            return {}, _interfacePorts(document.workflows[workflowId].inputs)
        if node.kind == "workflow_output":
            return _interfacePorts(document.workflows[workflowId].outputs), {}
        if node.kind == "subflow" and node.targetWorkflowId:
            target = document.workflows.get(node.targetWorkflowId)
            if target is not None:
                return _interfacePorts(target.inputs), _interfacePorts(target.outputs)
        if node.kind == "operator" and node.operatorId:
            metadata = _operatorMetadata(self.operatorRegistry.get(node.operatorId))
            if metadata is not None:
                return metadata[0], metadata[1]
        return dict(node.inputPorts), dict(node.outputPorts)

    def _validateOperator(
        self, document: ProjectDocument, workflowId: str, node: WorkflowNode
    ) -> list[ValidationIssue]:
        if not node.operatorId:
            return []
        registered = self.operatorRegistry.get(node.operatorId)
        if registered is None:
            return [
                ValidationIssue(
                    "E_OPERATOR_UNKNOWN",
                    f"operator is not registered: {node.operatorId}",
                    projectId=document.project.projectId,
                    workflowId=workflowId,
                    nodeId=node.nodeId,
                )
            ]
        operatorClass = _operatorClass(registered)
        if operatorClass is None:
            return []
        validate = getattr(operatorClass, "validateParams", None)
        if not callable(validate):
            return []
        try:
            validator = cast(Callable[..., object], validate)
            result = validator(node.params)
        except TypeError:
            try:
                validator = cast(Callable[..., object], validate)
                operatorFactory = cast(Callable[[], object], operatorClass)
                result = validator(operatorFactory(), node.params)
            except Exception as err:
                return [
                    ValidationIssue(
                        "E_PARAM_INVALID",
                        f"parameter validation failed: {err}",
                        projectId=document.project.projectId,
                        workflowId=workflowId,
                        nodeId=node.nodeId,
                    )
                ]
        except Exception as err:
            return [
                ValidationIssue(
                    "E_PARAM_INVALID",
                    f"parameter validation failed: {err}",
                    projectId=document.project.projectId,
                    workflowId=workflowId,
                    nodeId=node.nodeId,
                )
            ]
        if isinstance(result, dict):
            return [
                ValidationIssue(
                    str(result.get("code", "E_PARAM_INVALID")),
                    str(result.get("message", "invalid parameters")),
                    projectId=document.project.projectId,
                    workflowId=workflowId,
                    nodeId=node.nodeId,
                )
            ]
        return []

    def _buildCallGraph(
        self, workflows: Mapping[str, CompiledWorkflow]
    ) -> dict[str, tuple[str, ...]]:
        graph: dict[str, tuple[str, ...]] = {}
        for workflowId, workflow in workflows.items():
            targets: list[str] = []
            for node in workflow.nodes:
                if node.kind == "subflow" and node.targetWorkflowId:
                    targets.append(node.targetWorkflowId)
                elif node.kind == "loop":
                    for field in ("bodyWorkflowId", "conditionWorkflowId"):
                        target = node.loop.get(field)
                        if isinstance(target, str) and target:
                            targets.append(target)
            graph[workflowId] = tuple(dict.fromkeys(targets))
        return graph

    def _findRecursion(
        self, graph: Mapping[str, tuple[str, ...]], projectId: str
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(workflowId: str, path: tuple[str, ...]) -> None:
            if workflowId in visiting:
                cycle = " -> ".join((*path, workflowId))
                issues.append(
                    ValidationIssue(
                        "E_WORKFLOW_RECURSIVE",
                        f"recursive workflow call graph: {cycle}",
                        projectId=projectId,
                        workflowId=workflowId,
                    )
                )
                return
            if workflowId in visited:
                return
            visiting.add(workflowId)
            for target in graph.get(workflowId, ()):
                visit(target, (*path, workflowId))
            visiting.remove(workflowId)
            visited.add(workflowId)

        for workflowId in graph:
            visit(workflowId, ())
        return issues


def _interfacePorts(interface: Mapping[str, object]) -> dict[str, str]:
    ports: dict[str, str] = {}
    for name, descriptor in interface.items():
        if isinstance(descriptor, str):
            ports[name] = descriptor
        elif isinstance(descriptor, dict):
            rawType = descriptor.get("type", "object")
            ports[name] = rawType if isinstance(rawType, str) else "object"
        else:
            ports[name] = "object"
    return ports


def _operatorMetadata(value: object | None) -> tuple[dict[str, str], dict[str, str]] | None:
    if value is None:
        return None
    manifest = getattr(value, "manifest", None)
    metadata = manifest if manifest is not None else value
    inputPorts = getattr(metadata, "inputPorts", None)
    outputPorts = getattr(metadata, "outputPorts", None)
    if inputPorts is None or outputPorts is None:
        metadata = getattr(value, "meta", value)
        inputPorts = getattr(metadata, "inputPorts", None)
        outputPorts = getattr(metadata, "outputPorts", None)
    if not isinstance(inputPorts, Mapping) or not isinstance(outputPorts, Mapping):
        return None
    return (
        {str(key): str(item) for key, item in inputPorts.items()},
        {str(key): str(item) for key, item in outputPorts.items()},
    )


def _operatorClass(value: object) -> object | None:
    operatorClass = getattr(value, "operatorClass", None)
    return operatorClass if operatorClass is not None else value


def _portsCompatible(source: str, target: str) -> bool:
    return source == target or source in {"object", "any"} or target in {"object", "any"}


def _topological_order(
    nodes: list[CompiledNode], edges: tuple[CompiledEdge, ...]
) -> list[str] | None:
    indegree = {node.nodeId: 0 for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.fromNode not in indegree or edge.toNode not in indegree:
            continue
        outgoing[edge.fromNode].append(edge.toNode)
        indegree[edge.toNode] += 1
    queue = deque(node.nodeId for node in nodes if indegree[node.nodeId] == 0)
    ordered: list[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for target in outgoing.get(current, []):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return ordered if len(ordered) == len(nodes) else None
