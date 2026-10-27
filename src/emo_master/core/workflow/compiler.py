from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from copy import deepcopy
from typing import Callable, cast

from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument, WorkflowDefinition, WorkflowNode
from emo_master.core.project.resources import ParameterTarget, ResourceBinding, SiteBinding
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError, locatedValidationError
from emo_master.core.workflow.loop_contracts import (
    LoopContract,
    deriveLoopContract,
    loopWorkflowReferenceFields,
)
from emo_master.core.workflow.models import (
    CompiledEdge,
    CompiledNode,
    CompiledProject,
    CompiledWorkflow,
    freezeMapping,
)
from emo_master.core.workflow.validation import validateProjectDocument
from emo_master.core.workflow.parameter_bindings import compileBindings
from emo_master.core.project.global_variables import (
    VariableError, variablePorts, validateBindings, resolveParams, validateEffectiveParams,
)


class WorkflowCompiler:
    def __init__(
        self,
        operatorRegistry: Mapping[str, object] | None = None,
        maxCallDepth: int = 32,
        deviceTypes: Mapping[str, object] | None = None,
    ) -> None:
        self.operatorRegistry = operatorRegistry or {}
        self.maxCallDepth = maxCallDepth
        self.deviceTypes = deviceTypes or {}

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
        for workflowId, workflow in document.workflows.items():
            for node in workflow.nodes:
                descriptor = self.operatorRegistry.get(node.operatorId or "")
                manifest = getattr(descriptor, "manifest", descriptor)
                requirements = (manifest.get("deviceRequirements", {}) if isinstance(manifest, Mapping)
                                else getattr(manifest, "deviceRequirements", {}))
                if node.deviceBindings or requirements:
                    try:
                        self._devicePlans(document, node, requirements)
                    except ValueError as error:
                        issues.extend(locatedValidationError(error, 'E_DEVICE_BINDING',
                            projectId=document.project.projectId, workflowId=workflowId, nodeId=node.nodeId,
                            fieldPath='deviceBindings').issues)
        if issues:
            raise WorkflowCompileError(issues)

        for workflowId, workflow in document.workflows.items():
            for node in workflow.nodes:
                try:
                    from emo_master.core.project.tcp_dependencies import validateCommunicationNode
                    validateCommunicationNode(document, node)
                    resourceBindings: list[ResourceBinding | SiteBinding] = (
                        [*document.resources.parameterBindings, *document.resources.siteBindings] if document.resources else [])
                    occupied = [binding.target.parameterPath for binding in resourceBindings
                        if isinstance(binding.target, ParameterTarget)
                        and binding.target.workflowId == workflowId and binding.target.nodeId == node.nodeId]
                    schema = _operatorParamSchema(self.operatorRegistry.get(node.operatorId or ""))
                    if node.operatorId in {"vision.state.variable_read", "vision.state.variable_write"}:
                        occupied.append(["variableId"])
                    if node.operatorId == "vision.state.variable_write":
                        occupied.append(["operation"])
                    validateBindings(node.globalVariableBindings, document.globalVariables, schema, occupied=occupied)
                    if (node.operatorId == "vision.io.coordinate_reader" and node.params.get("readMode") == "session"
                            and node.globalVariableBindings):
                        raise VariableError("E_COORDINATE_SNAPSHOT_CONFIG", "session coordinate reader configuration must be static")
                    variablePorts(node.operatorId, node.params, document.globalVariables)
                except (VariableError, ValueError) as error:
                    issues.append(ValidationIssue(getattr(error, "code", "E_VARIABLE_BINDING"), str(error),
                                                  document.project.projectId, workflowId, node.nodeId))
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
        from emo_master.core.workflow.dispatch_contracts import groupSlots
        for workflowId in document.workflowOrder:
            try:
                groupSlots(document, workflowId)
            except ValueError as error:
                raise WorkflowCompileError([ValidationIssue("E_DISPATCH_CONTRACT", str(error),
                    projectId=document.project.projectId, workflowId=workflowId)]) from error
        return CompiledProject(
            projectId=document.project.projectId,
            revision=document.project.revision,
            entryWorkflowId=document.entryWorkflowId,
            workflows=freezeMapping(compiledWorkflows),
            workflowCallGraph=freezeMapping(callGraph),
            runtime=freezeMapping(document.runtime.model_dump(mode="python")),
            pluginRootPaths=tuple(pluginRootPaths),
            globalVariables=freezeMapping({key: value.model_dump() for key, value in document.globalVariables.items()}),
        )

    def _devicePlans(self, document, node, requirements=None):
        descriptor = self.operatorRegistry.get(node.operatorId or "")
        manifest = getattr(descriptor, "manifest", descriptor)
        if requirements is None:
            requirements = (manifest.get("deviceRequirements", {}) if isinstance(manifest, Mapping)
                            else getattr(manifest, "deviceRequirements", {}))
        if not node.deviceBindings and not requirements:
            return {}
        if set(requirements) != set(node.deviceBindings):
            raise WorkflowCompileError([ValidationIssue('E_DEVICE_BINDING', 'device slots do not match the operator declaration',
                fieldPath='deviceBindings.'+slot) for slot in sorted(set(requirements) ^ set(node.deviceBindings))])
        schema = _operatorParamSchema(descriptor)
        actionFields = schema.get("xDeviceActionFields")
        if actionFields is not None:
            boundFields = {binding.parameterPath[0] for binding in node.globalVariableBindings}
            if (set(node.params) | boundFields) - set(actionFields):
                raise WorkflowCompileError([ValidationIssue('E_DEVICE_PARAMETERS',
                    'managed node contains connection/retry parameters; explicitly migrate them to the device',
                    fieldPath='params.'+key) for key in sorted((set(node.params)|boundFields)-set(actionFields))])
        result = {}
        for slot, binding in node.deviceBindings.items():
            definition = document.devices.definitions.get(binding.deviceId)
            if definition is None:
                raise WorkflowCompileError([ValidationIssue('E_DEVICE_REFERENCE', f'device missing: {binding.deviceId}',
                    fieldPath=f'deviceBindings.{slot}.deviceId')])
            declaration = self.deviceTypes.get(definition.deviceTypeId)
            if declaration is None or not declaration.driverEntry:
                raise WorkflowCompileError([ValidationIssue('E_DEVICE_DRIVER_UNAVAILABLE',
                    f'Device-bound execution requires the managed device Runtime: {definition.deviceTypeId} @ {definition.driverVersion}',
                    fieldPath=f'deviceBindings.{slot}.deviceId')])
            try:
                declaration.validateDefinition(definition)
            except ValueError as error:
                raise locatedValidationError(error, 'E_DEVICE_DEFINITION', prefix=f'devices.definitions.{binding.deviceId}') from error
            requirement = requirements[slot]
            from emo_master.core.plugin.devices import validateDeviceBinding
            try:
                selectors = validateDeviceBinding(requirement, binding, definition, declaration)
            except ValueError as error:
                raise locatedValidationError(error, 'E_DEVICE_BINDING', prefix=f'deviceBindings.{slot}') from error
            overrides = node.deviceOverrides.get(slot, {})
            bindings = node.deviceValueBindings.get(slot, {})
            try:
                declaration.validateNodeSources(definition, overrides, bindings, slot=slot)
            except ValueError as error:
                raise locatedValidationError(error, 'E_DEVICE_PARAMETERS', fieldPath=f'deviceOverrides.{slot}') from error
            from emo_master.core.project.global_variables import compatible
            for path, source in bindings.items():
                expected = declaration.parameterSpec(path)["type"]
                if source.source == "variable":
                    variable = document.globalVariables.get(source.variableId)
                    if variable is None or not compatible(variable.type, expected):
                        raise WorkflowCompileError([ValidationIssue('E_DEVICE_PARAMETERS', 'invalid dynamic device variable',
                            fieldPath=f'deviceValueBindings.{slot}.{path}.variableId')])
                elif not compatible(normalizePortType(node.inputPorts.get(source.inputName)), expected):
                    raise WorkflowCompileError([ValidationIssue('E_DEVICE_PARAMETERS', 'invalid dynamic device input',
                        fieldPath=f'deviceValueBindings.{slot}.{path}.inputName')])
            result[slot] = dict(deviceId=binding.deviceId, selectors=deepcopy(selectors), definition=definition.model_dump(),
                                declaration=declaration.model_dump(), overrides=deepcopy(overrides),
                                bindings={path: source.model_dump() for path, source in bindings.items()})
        return result

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
            inputPorts: dict[str, object]
            outputPorts: dict[str, object]
            loopConfig = deepcopy(node.loop)
            if node.kind == "loop":
                loopContract = self._loopContract(document, node)
                inputPorts = dict(loopContract.inputPorts)
                outputPorts = dict(loopContract.outputPorts)
                loopConfig = deepcopy(loopContract.normalizedConfig)
            else:
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
                loop=freezeMapping(loopConfig),
                globalVariableBindings=tuple(freezeMapping(binding.model_dump()) for binding in node.globalVariableBindings),
                paramSchema=freezeMapping(_operatorParamSchema(self.operatorRegistry.get(node.operatorId or ""))),
                devicePlans=freezeMapping(self._devicePlans(document, node)),
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
                if not arePortTypesCompatible(sourceType, targetType):
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
        for compiledNode in nodes:
            if compiledNode.kind == "join":
                tokenEdges = [e for e in incoming.get(compiledNode.nodeId, ()) if e.toPort == "token"]
                if len(tokenEdges) != 1 or (tokenEdges[0].fromNode, tokenEdges[0].fromPort) != (
                        compiledNode.params["dispatchNodeId"], "token"):
                    issues.append(ValidationIssue("E_DISPATCH_JOIN_EDGE", "Join token must come from its declared Dispatch",
                        projectId=document.project.projectId, workflowId=workflowId, nodeId=compiledNode.nodeId))
            if (compiledNode.operatorId == "vision.state.variable_write"
                    and compiledNode.params.get("operation", "set") != "set"
                    and not any(edge.toPort == "after" for edge in incoming.get(compiledNode.nodeId, ()))):
                issues.append(ValidationIssue(
                    "E_VARIABLE_DEPENDENCY_REQUIRED", "increment/reset requires an incoming after dependency",
                    projectId=document.project.projectId, workflowId=workflowId, nodeId=compiledNode.nodeId))
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
        bindings, outgoingBindings, bindingIssues = compileBindings(
            nodeById, document.project.projectId, workflowId)
        issues.extend(bindingIssues)
        # Dependencies affect scheduling, never the public input/output ports.
        dependencyEdges = tuple(CompiledEdge(b.fromNode, b.fromPort, b.toNode, "") for b in bindings)
        topology = _topological_order(nodes, edges + dependencyEdges)
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
            parameterBindings=bindings,
            outgoingBindings=freezeMapping(outgoingBindings),
        )

    def _nodePorts(
        self, document: ProjectDocument, workflowId: str, node: WorkflowNode
    ) -> tuple[dict[str, object], dict[str, object]]:
        if node.kind in {"dispatch", "join"}:
            from emo_master.core.workflow.dispatch_contracts import dispatchPorts
            try:
                return dispatchPorts(document, workflowId, node)
            except ValueError as error:
                raise WorkflowCompileError([ValidationIssue("E_DISPATCH_CONTRACT", str(error),
                    projectId=document.project.projectId, workflowId=workflowId, nodeId=node.nodeId)]) from error
        if node.kind == "workflow_input":
            return {}, _interfacePorts(document.workflows[workflowId].inputs)
        if node.kind == "workflow_output":
            return _interfacePorts(document.workflows[workflowId].outputs), {}
        if node.kind == "subflow" and node.targetWorkflowId:
            target = document.workflows.get(node.targetWorkflowId)
            if target is not None:
                return _interfacePorts(target.inputs), _interfacePorts(target.outputs)
        if node.kind == "operator" and node.operatorId:
            dynamicPorts = variablePorts(node.operatorId, node.params, document.globalVariables)
            if dynamicPorts is not None:
                return dynamicPorts
            metadata = _operatorMetadata(self.operatorRegistry.get(node.operatorId))
            if metadata is not None:
                from emo_master.plugins.builtins._operator_modes import requiredChoiceInput
                inputs, outputs = metadata
                required = requiredChoiceInput(node.operatorId, node.params,
                    {edge.toPort for edge in document.workflows[workflowId].edges if edge.toNode == node.nodeId},
                    {binding.parameterPath[0] for binding in node.globalVariableBindings})
                if required in inputs:
                    inputs[required] = {**cast(Mapping[str, object], inputs[required]), "required": True}
                return inputs, outputs
        return dict(node.inputPorts), dict(node.outputPorts)

    def nodePortDefinitions(self, document: ProjectDocument, workflowId: str, node: WorkflowNode):
        """Read formal ports without compiling an unfinished Designer draft."""
        if node.kind == "loop":
            contract = self._loopContract(document, node)
            return deepcopy(dict(contract.inputPorts)), deepcopy(dict(contract.outputPorts))
        inputs, outputs = self._nodePorts(document, workflowId, node)
        return deepcopy(inputs), deepcopy(outputs)

    def _loopContract(
        self, document: ProjectDocument, node: WorkflowNode
    ) -> LoopContract:
        bodyWorkflowId = node.loop.get("bodyWorkflowId")
        conditionWorkflowId = (node.loop.get("conditionWorkflowId")
                               if "conditionWorkflowId" in loopWorkflowReferenceFields(node.loop) else None)
        body = (
            document.workflows.get(bodyWorkflowId)
            if isinstance(bodyWorkflowId, str)
            else None
        )
        condition = (
            document.workflows.get(conditionWorkflowId)
            if isinstance(conditionWorkflowId, str)
            else None
        )
        return deriveLoopContract(
            node.loop,
            body.inputs if body is not None else {},
            body.outputs if body is not None else {},
            condition.inputs if condition is not None else {},
            condition.outputs if condition is not None else {},
            document.globalVariables,
        )

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
        # Port-aware validators defer only values supplied by graph connections.
        validationArgs: tuple = ()
        connectedValidator = getattr(operatorClass, "validateNodeParams", None)
        if callable(connectedValidator):
            validate = connectedValidator
            validationArgs = ({edge.toPort for edge in document.workflows[workflowId].edges
                               if edge.toNode == node.nodeId},)
        if node.deviceBindings:
            validate = getattr(operatorClass, "validateDeviceParams", validate)
        try:
            params = resolveParams(node.params, node.globalVariableBindings,
                                   {key: value.initialValue for key, value in document.globalVariables.items()})
            if node.globalVariableBindings:
                params = validateEffectiveParams(params, node.globalVariableBindings, _operatorParamSchema(registered))
            if not callable(validate):
                return []
            validator = cast(Callable[..., object], validate)
            result = validator(params, *validationArgs)
        except TypeError:
            try:
                validator = cast(Callable[..., object], validate)
                operatorFactory = cast(Callable[[], object], operatorClass)
                result = validator(operatorFactory(), params, *validationArgs)
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
                if node.kind == "dispatch":
                    declarations = cast(list[dict[str, str]], node.params["targets"])
                    targets.extend(target["workflowId"] for target in declarations)
                if node.kind == "subflow" and node.targetWorkflowId:
                    targets.append(node.targetWorkflowId)
                elif node.kind == "loop":
                    for field in loopWorkflowReferenceFields(node.loop):
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


def _interfacePorts(interface: Mapping[str, object]) -> dict[str, object]:
    return {str(name): deepcopy(spec) for name, spec in interface.items()}


def _operatorMetadata(
    value: object | None,
) -> tuple[dict[str, object], dict[str, object]] | None:
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
        {str(key): deepcopy(item) for key, item in inputPorts.items()},
        {str(key): deepcopy(item) for key, item in outputPorts.items()},
    )


def _operatorClass(value: object) -> object | None:
    operatorClass = getattr(value, "operatorClass", None)
    return operatorClass if operatorClass is not None else value


def _operatorParamSchema(value):
    metadata = getattr(value, "manifest", None) or getattr(value, "meta", value)
    schema = getattr(metadata, "paramSchema", {})
    return deepcopy(dict(schema)) if isinstance(schema, Mapping) else {}


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
