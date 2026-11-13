"""Local draft checks reuse formal rules and never query or execute a Runtime."""
from dataclasses import asdict
from pathlib import Path

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.state.node_configuration import validateConfiguration
from emo_master.core.contracts.port_types import isPortRequired
from emo_master.core.graph.validator import validateFlowGraph
from emo_master.core.plugin.devices import DeviceRequirement
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.device_resources import deviceTarget
from emo_master.core.project.resources import DeviceParameterTarget
from emo_master.core.project.runtime_directory import nodeFileSchema
from emo_master.core.project.snapshots import _parameters, parameterValidationApplies, verifyResources
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError, locatedValidationError
from emo_master.core.workflow.validation import validateProjectDocument


def localDiagnostics(draft, document, manifests):
    issues = [asdict(issue) for issue in validateProjectDocument(document)]
    missing = []
    resources = document.resources
    plan = resources.model_dump() if resources else {}
    root = draft.directory.resolve()

    def report(error, code, *, workflowId='', nodeId='', fieldPath='', resourceId='', incomplete=False):
        for issue in locatedValidationError(error, getattr(error, 'code', code),
                projectId=document.project.projectId, workflowId=workflowId, nodeId=nodeId, fieldPath=fieldPath).issues:
            value = asdict(issue)
            if resourceId:
                value['resourceId'] = resourceId
            if resources:
                for resource_id in resources.items:
                    if issue.fieldPath.startswith('resources.items.' + resource_id + '.'):
                        value['resourceId'] = resource_id
                        break
            (missing if incomplete or 'required parameter missing' in issue.message else issues).append(value)

    resource_targets = {}
    device_resource_locations = set()
    if resources:
        try:
            verifyResources(document, root)
        except WorkflowCompileError as error:
            for issue in error.issues:
                report(WorkflowCompileError([issue]), issue.code, incomplete=issue.code == 'E_RESOURCE_MISSING')
        for binding in (*resources.parameterBindings, *resources.siteBindings):
            target = binding.target
            resource_id = getattr(binding, 'resourceId', None)
            purpose = resources.items[resource_id].purpose if resource_id in resources.items else getattr(binding, 'purpose', None)
            if resource_id and resource_id not in resources.items:
                missing.append({'code': 'E_RESOURCE_MISSING', 'message': 'Binding references a missing resource',
                                'resourceId': resource_id, 'fieldPath': 'resources.parameterBindings', **target.model_dump()})
            try:
                if isinstance(target, DeviceParameterTarget):
                    if purpose:
                        params = deviceTarget(document, target, draft.device_types, purpose)
                        if resource_id:
                            device_resource_locations.add(f'devices.definitions.{target.deviceId}.{target.section}.' + '.'.join(target.parameterPath))
                        elif binding.required:
                            value = params
                            for part in target.parameterPath:
                                value = value.get(part) if isinstance(value, dict) else None
                            if not isinstance(value, str) or not value:
                                missing.append({'code': 'E_SITE_VALUE_MISSING', 'message': 'Required device site value is not configured',
                                    'fieldPath': f'devices.definitions.{target.deviceId}.{target.section}.' + '.'.join(target.parameterPath)})
                    continue
                node = next(node for node in document.workflows[target.workflowId].nodes if node.nodeId == target.nodeId)
                metadata = manifests.get(node.operatorId)
                if metadata is None:
                    continue  # The node's missing declaration is reported below.
                schema = metadata.paramSchema
                for part in target.parameterPath:
                    schema = schema.get('properties', {}).get(part, {})
                if node.kind != 'operator' or not schema:
                    raise ValueError('Binding target is not a declared operator parameter')
                if resource_id and (schema.get('xWidget') != 'file' or schema.get('xFileMode') != 'open'):
                    raise ValueError('Resource binding must target a declared input file')
                resource_targets[(target.workflowId, target.nodeId, tuple(target.parameterPath))] = resource_id
                if getattr(binding, 'required', False):
                    value = node.params
                    for part in target.parameterPath:
                        value = value.get(part) if isinstance(value, dict) else None
                    if not isinstance(value, str) or not value:
                        missing.append({'code': 'E_SITE_VALUE_MISSING', 'message': 'Required site value is not configured',
                                        'workflowId': target.workflowId, 'nodeId': target.nodeId,
                                        'fieldPath': 'params.' + '.'.join(target.parameterPath)})
            except (ValueError, KeyError, StopIteration) as error:
                report(error, 'E_RESOURCE_TARGET', workflowId=getattr(target, 'workflowId', ''),
                       nodeId=getattr(target, 'nodeId', ''), fieldPath='params.' + '.'.join(target.parameterPath),
                       resourceId=resource_id or '',
                       incomplete=isinstance(error, WorkflowCompileError) and any(
                           issue.code == 'E_DEVICE_DEPENDENCY' for issue in error.issues))

    def files(value, schema, *, workflowId='', nodeId='', path=(), location='params', required=False):
        if schema.get('xWidget') == 'file' and schema.get('xFileMode') in {'open', 'directory'}:
            if (workflowId, nodeId, path) in resource_targets or location in device_resource_locations:
                return
            value = value if value is not None else schema.get('default')
            if not value and not required:
                return
            available = False
            if isinstance(value, str) and value:
                candidate = (root / Path(value)).resolve()
                # Local paths are not file transfer; only inspect this draft's bytes.
                if candidate.is_relative_to(root):
                    available = candidate.is_dir() if schema.get('xFileMode') == 'directory' else candidate.is_file()
            if not available:
                missing.append({'code': 'E_RESOURCE_INPUT_MISSING', 'message': 'Input bytes must be uploaded and bound in this draft',
                                'workflowId': workflowId, 'nodeId': nodeId, 'fieldPath': location})
        elif schema.get('type') == 'object' or 'properties' in schema:
            params = value if isinstance(value, dict) else {}
            for name, child in schema.get('properties', {}).items():
                if parameterValidationApplies(params, child):
                    files(params.get(name, child.get('default')), child, workflowId=workflowId, nodeId=nodeId,
                          path=(*path, name), location=location + '.' + name, required=name in schema.get('required', []))
        elif schema.get('type') == 'array' and isinstance(value, list):
            for index, child in enumerate(value):
                files(child, schema.get('items', {}), workflowId=workflowId, nodeId=nodeId,
                      path=(*path, str(index)), location=location + '.' + str(index))

    requirements = {key: {slot: DeviceRequirement.model_validate(value) for slot, value in getattr(manifest, 'deviceRequirements', {}).items()}
                    for key, manifest in manifests.items()}
    devices = DeviceCatalogEditor(draft.session, draft.device_types, requirements)
    available_devices = set()
    for device_id, definition in document.devices.definitions.items():
        declaration = draft.device_types.get(definition.deviceTypeId)
        if declaration is None or declaration.version != definition.driverVersion:
            missing.append({'code': 'E_DEVICE_DEPENDENCY', 'message': 'Matching device declaration is not available locally',
                            'fieldPath': f'devices.definitions.{device_id}.driverVersion'})
            continue
        available_devices.add(device_id)
        try:
            checked = declaration.validateDefinition(definition.model_copy(deep=True))
            for section in ('connection', 'defaults'):
                files(getattr(checked, section), getattr(declaration, section + 'Schema'),
                      location=f'devices.definitions.{device_id}.{section}')
        except ValueError as error:
            report(error, 'E_DEVICE_DEFINITION', fieldPath=f'devices.definitions.{device_id}')

    compiler = WorkflowCompiler(manifests, deviceTypes=draft.device_types)
    for workflow_id, workflow in document.workflows.items():
        raw_workflow = draft.session.workflows.get(workflow_id)
        for message in validateFlowGraph(raw_workflow.graph())['errors']:
            issues.append({'code': 'E_GRAPH_INVALID', 'message': message, 'workflowId': workflow_id, 'fieldPath': 'edges'})
        for node in workflow.nodes:
            if node.kind != 'operator':
                continue
            metadata = manifests.get(node.operatorId)
            if metadata is None:
                missing.append({'code': 'DECLARATION_UNAVAILABLE', 'message': 'Matching Runtime declaration is not available locally',
                                'workflowId': workflow_id, 'nodeId': node.nodeId, 'fieldPath': 'operatorId'})
            schema = metadata.paramSchema if metadata is not None else node.paramSchema
            try:
                validateConfiguration(raw_workflow, node.model_dump(), node.params,
                    [binding.model_dump() for binding in node.globalVariableBindings], schema,
                    document.globalVariables, plan, validateParams=True)
            except ValueError as error:
                report(error, 'E_PARAM_INVALID', workflowId=workflow_id, nodeId=node.nodeId, fieldPath='params')
            # Defaults are used only in this isolated validation copy.
            try:
                normalized = _parameters(node.params, schema, 'params')
            except WorkflowCompileError:
                normalized = node.params
            if metadata is not None:
                files(normalized, nodeFileSchema(node, workflow, schema), workflowId=workflow_id, nodeId=node.nodeId)
                for slot in set(getattr(metadata, 'deviceRequirements', {})) - set(node.deviceBindings):
                    missing.append({'code': 'DEVICE_BINDING_MISSING', 'message': 'Required device slot is unbound',
                                    'workflowId': workflow_id, 'nodeId': node.nodeId, 'fieldPath': 'deviceBindings.' + slot})
                if all(binding.deviceId in available_devices for binding in node.deviceBindings.values()):
                    try:
                        devices._validateNode(node.model_copy(deep=True), allowIncomplete=True)
                    except ValueError as error:
                        report(error, 'E_DEVICE_BINDING', workflowId=workflow_id, nodeId=node.nodeId, fieldPath='deviceBindings')
                try:
                    ports, _ = compiler.nodePortDefinitions(document, workflow_id, node)
                    connected = {edge.toPort for edge in workflow.edges if edge.toNode == node.nodeId}
                    for name, definition in ports.items():
                        if isPortRequired(definition) and name not in connected:
                            missing.append({'code': 'E_INPUT_REQUIRED', 'message': 'Required input is not connected',
                                            'workflowId': workflow_id, 'nodeId': node.nodeId, 'fieldPath': 'inputPorts.' + name})
                except ValueError as error:
                    report(error, 'E_PORT_INVALID', workflowId=workflow_id, nodeId=node.nodeId, fieldPath='inputPorts')
    for issue in validateBindings(document, manifests):
        value = {'code': issue.code, 'message': issue.message, 'fieldPath': issue.path}
        (missing if 'trusted plugin manifest unavailable' in issue.message else issues).append(value)
    return issues, missing
