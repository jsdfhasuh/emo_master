"""Read-only, work-owned candidate ZIP checks using the formal Runtime compiler.

No online configuration coordinator, resource publisher or device manager is
called here. Every byte the compiler may read belongs to this request's copy.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import stat
import tempfile
import zipfile

from pydantic import ValidationError

from emo_panel_protocol.generated import runtime_pb2 as pb
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.dependencies import validateOperatorDependencies
from emo_master.core.project.device_resources import deviceTarget
from emo_master.core.project.files import isProjectFile
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.resources import DeviceParameterTarget, ParameterTarget, safeRelativePath
from emo_master.core.project.runtime_directory import nodeFileSchema, prepareRuntimeProject
from emo_master.core.project.snapshots import _parameters, _setPath, verifyResources
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.core.workflow.validation import validateProjectDocument


MAX_CANDIDATE_BYTES = 128 * 1024 * 1024
MAX_CANDIDATE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_CANDIDATE_CHUNK_BYTES = 256 * 1024


class CandidateTransferError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _active(context):
    if context is not None and not context.is_active():
        raise CandidateTransferError('E_CANDIDATE_CANCELLED', 'candidate check cancelled')


def _diagnostic(code, message, *, document=None, workflowId='', nodeId='', fieldPath='', resourceId=''):
    result = dict(code=code, message=message, projectId=document.project.projectId if document else '',
                  workflowId=workflowId, nodeId=nodeId, fieldPath=fieldPath)
    if resourceId:
        result['resourceId'] = resourceId
    return result


def _issues(error, document):
    if isinstance(error, WorkflowCompileError):
        result = [asdict(issue) for issue in error.issues]
    elif isinstance(error, ValidationError):
        result = [_diagnostic('E_PROJECT_INVALID', issue['msg'], document=document,
                              fieldPath='.'.join(map(str, issue['loc']))) for issue in error.errors()]
    else:
        result = [_diagnostic(getattr(error, 'code', 'E_PROJECT_INVALID'), str(error), document=document)]
    for issue in result:
        if document and not issue.get('projectId'):
            issue['projectId'] = document.project.projectId
        if document and document.resources:
            location = issue.get('fieldPath', '')
            # IDs can contain dots: use the actual declaration, not path splitting.
            for resourceId in sorted(document.resources.items, key=len, reverse=True):
                prefix = 'resources.items.' + resourceId
                if location == prefix or location.startswith(prefix + '.'):
                    issue['resourceId'] = resourceId
                    break
            if 'resourceId' not in issue:
                for binding in document.resources.parameterBindings:
                    target = binding.target
                    if (isinstance(target, ParameterTarget) and issue.get('workflowId') == target.workflowId
                            and issue.get('nodeId') == target.nodeId
                            and location == 'params.' + '.'.join(target.parameterPath)):
                        issue['resourceId'] = binding.resourceId
    return result


def _extract(package, root, context):
    try:
        with zipfile.ZipFile(package) as archive:
            entries = archive.infolist()
            names = set()
            expanded = 0
            projects = []
            for entry in entries:
                _active(context)
                # ZipInfo.filename normalizes '\\' to '/' on Windows. Validate
                # the actual wire name before using that normalized path.
                name = entry.orig_filename.rstrip('/')
                if not name:
                    continue
                safeRelativePath(name)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('candidate ZIP must not contain symbolic links')
                if name.casefold() in names:
                    raise ValueError('candidate ZIP has duplicate paths')
                names.add(name.casefold())
                expanded += entry.file_size
                if expanded > MAX_CANDIDATE_EXPANDED_BYTES:
                    raise ValueError('candidate ZIP exceeds expanded size limit')
                target = root / name
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open('wb') as output:
                    while block := source.read(1024 * 1024):
                        _active(context)
                        output.write(block)
                if isProjectFile(target):
                    projects.append(target)
            if len(projects) != 1:
                raise ValueError('candidate ZIP must contain exactly one project document')
            return projects[0]
    except CandidateTransferError:
        raise
    except (ValueError, zipfile.BadZipFile, OSError, RuntimeError) as error:
        raise CandidateTransferError('E_CANDIDATE_PACKAGE', str(error)) from error


def _candidatePaths(document, root, registry, deviceTypes, protectedPaths):
    """Resolve only declared uploaded inputs; map outputs only on the local copy."""
    prepared = document.model_copy(deep=True)
    errors, missing = [], []
    resources = document.resources
    if resources:
        try:
            verifyResources(document, root)
        except WorkflowCompileError as error:
            for issue in _issues(error, document):
                (missing if issue['code'] == 'E_RESOURCE_MISSING' else errors).append(issue)
        for binding in resources.parameterBindings:
            target = binding.target
            resource = resources.items.get(binding.resourceId)
            if resource is None:
                errors.append(_diagnostic('E_RESOURCE_TARGET', 'resource reference missing', document=document,
                    fieldPath='resources.parameterBindings', resourceId=binding.resourceId))
                continue
            value = str(root / resource.path)
            if isinstance(target, ParameterTarget):
                workflow = prepared.workflows.get(target.workflowId)
                node = next((n for n in workflow.nodes if n.nodeId == target.nodeId), None) if workflow else None
                descriptor = registry.get(node.operatorId or '') if node else None
                schema = descriptor.manifest.paramSchema if descriptor else {}
                for part in target.parameterPath:
                    schema = schema.get('properties', {}).get(part, {})
                if node is None or schema.get('xWidget') != 'file' or schema.get('xFileMode') != 'open':
                    errors.append(_diagnostic('E_RESOURCE_TARGET', 'resource binding must target an input file parameter',
                        document=document, workflowId=target.workflowId, nodeId=target.nodeId,
                        fieldPath='params.' + '.'.join(target.parameterPath), resourceId=binding.resourceId))
                else:
                    _setPath(node.params, target.parameterPath, value)
            elif isinstance(target, DeviceParameterTarget):
                try:
                    params = deviceTarget(prepared, target, deviceTypes, resource.purpose)
                    _setPath(params, target.parameterPath, value)
                except ValueError as error:
                    errors.extend(_issues(error, document))
        for binding in resources.siteBindings:
            target = binding.target
            if isinstance(target, ParameterTarget):
                workflow = prepared.workflows.get(target.workflowId)
                node = next((n for n in workflow.nodes if n.nodeId == target.nodeId), None) if workflow else None
                value = node.params if node else None
                location = dict(workflowId=target.workflowId, nodeId=target.nodeId,
                                fieldPath='params.' + '.'.join(target.parameterPath))
                if node is None:
                    errors.append(_diagnostic('E_SITE_TARGET', 'site parameter target missing',
                        document=document, **location))
                    continue
            else:
                location = dict(fieldPath='devices.definitions.' + target.deviceId + '.' + target.section + '.'
                                + '.'.join(target.parameterPath))
                try:
                    value = deviceTarget(prepared, target, deviceTypes, binding.purpose)
                except ValueError as error:
                    errors.extend(_issues(error, document))
                    continue
            for part in target.parameterPath:
                value = value.get(part) if isinstance(value, dict) else None
            if binding.required and (not isinstance(value, str) or not value):
                missing.append(_diagnostic('E_CANDIDATE_SITE_MISSING', 'required site parameter missing: ' + binding.field,
                    document=document, **location))

    allowed = {(root / resource.path).resolve() for resource in resources.items.values()} if resources else set()
    inputPaths = {root / 'project.json', *[p for p in root.iterdir() if isProjectFile(p)], *allowed}
    protected = [Path(path).resolve() for path in (*inputPaths, *protectedPaths)]

    def visit(value, schema, location, *, workflowId='', nodeId='', required=False):
        if schema.get('xWidget') == 'file':
            mode = schema.get('xFileMode')
            if not isinstance(value, str) or not value:
                if required and mode in {'open', 'directory'}:
                    missing.append(_diagnostic('E_CANDIDATE_INPUT_MISSING', 'required input has not been uploaded',
                        document=document, workflowId=workflowId, nodeId=nodeId, fieldPath=location))
                return value
            windows = PureWindowsPath(value)
            path = Path(value)
            resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
            if mode in {'open', 'directory'}:
                contained = resolved.is_relative_to(root.resolve())
                uploaded = (resolved in allowed if mode == 'open' else
                    any(item.is_relative_to(resolved) for item in allowed))
                if (windows.drive and not path.is_absolute()) or not contained or not uploaded:
                    missing.append(_diagnostic('E_CANDIDATE_INPUT_NOT_UPLOADED',
                        'input must be supplied as a declared candidate resource', document=document,
                        workflowId=workflowId, nodeId=nodeId, fieldPath=location))
                elif mode == 'directory' and not resolved.is_dir():
                    missing.append(_diagnostic('E_CANDIDATE_INPUT_MISSING', 'input directory has not been uploaded',
                        document=document, workflowId=workflowId, nodeId=nodeId, fieldPath=location))
                return str(resolved)
            if mode == 'save':
                if any(resolved == item or resolved.is_relative_to(item) or item.is_relative_to(resolved)
                       for item in protected):
                    errors.append(_diagnostic('E_RESOURCE_OUTPUT', 'output overlaps project input or Runtime data',
                        document=document, workflowId=workflowId, nodeId=nodeId, fieldPath=location))
                return str(root / '_candidate-outputs' / (hashlib.sha256(str(resolved).encode()).hexdigest() + path.suffix))
            return value
        if isinstance(value, dict):
            requiredFields = schema.get('required', [])
            for key, child in schema.get('properties', {}).items():
                if key in value or 'default' in child or key in requiredFields:
                    value[key] = visit(value.get(key, child.get('default')), child, location + '.' + key,
                        workflowId=workflowId, nodeId=nodeId, required=key in requiredFields)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = visit(item, schema.get('items', {}), location + '.' + str(index),
                                     workflowId=workflowId, nodeId=nodeId)
        return value

    for wid, workflow in prepared.workflows.items():
        for node in workflow.nodes:
            descriptor = registry.get(node.operatorId or '')
            if descriptor:
                schema = nodeFileSchema(node, workflow, descriptor.manifest.paramSchema)
                node.params = visit(node.params, schema, 'params', workflowId=wid, nodeId=node.nodeId)
            for slot, binding in node.deviceBindings.items():
                definition = prepared.devices.definitions.get(binding.deviceId)
                declaration = deviceTypes.get(definition.deviceTypeId) if definition else None
                if declaration and slot in node.deviceOverrides:
                    visit(node.deviceOverrides[slot], declaration.defaultsSchema, 'deviceOverrides.' + slot,
                          workflowId=wid, nodeId=node.nodeId)
    for did, definition in prepared.devices.definitions.items():
        declaration = deviceTypes.get(definition.deviceTypeId)
        if declaration:
            for section, schema in [('connection', declaration.connectionSchema), ('defaults', declaration.defaultsSchema)]:
                setattr(definition, section, visit(getattr(definition, section), schema,
                                                   'devices.definitions.' + did + '.' + section))
    return prepared, errors, missing


def checkCandidate(runtime, projectFile, revision, context):
    document = None
    report = dict(scope='target', draftRevision=revision, runtimeInstanceId=runtime.runtimeInstanceId,
                  status='FAILED', compileStatus='not_run', executionStatus='not_run',
                  errors=[], warnings=[], missingInputs=[])
    try:
        _active(context)
        raw = json.loads(projectFile.read_text(encoding='utf-8-sig'))
        document = ProjectDocument.model_validate(migrateProjectPayload(raw))
        report['projectId'] = document.project.projectId
        registry, deviceTypes = runtime.pluginScanResult.activeOperators, runtime.pluginScanResult.deviceTypes
        report['errors'].extend(asdict(issue) for issue in validateProjectDocument(document, registry))
        try:
            validateOperatorDependencies(document, registry)
        except ValueError as error:
            report['errors'].extend(_issues(error, document))
        manifests = {key: descriptor.manifest for key, descriptor in registry.items()}
        for issue in validateBindings(document, manifests, publish=True):
            report['errors'].append(_diagnostic(issue.code, issue.message, document=document, fieldPath=issue.path))
        prepared, errors, missing = _candidatePaths(document, projectFile.parent, registry, deviceTypes,
            (*runtime.sqliteProtectedPaths(), runtime.operationalLogWriter.directory))
        report['errors'].extend(errors)
        report['missingInputs'].extend(missing)
        # Check real parameter structure independently of missing input files.
        for wid, workflow in prepared.workflows.items():
            for node in workflow.nodes:
                descriptor = registry.get(node.operatorId or '')
                if descriptor:
                    try:
                        _parameters(node.params, descriptor.manifest.paramSchema, 'params')
                    except ValueError as error:
                        issues = _issues(error, document)
                        for issue in issues:
                            issue['workflowId'], issue['nodeId'] = wid, node.nodeId
                        report['errors'].extend(issues)
                elif node.kind == 'operator':
                    report['errors'].append(_diagnostic('E_OPERATOR_UNKNOWN', 'operator is not registered: ' + str(node.operatorId),
                        document=document, workflowId=wid, nodeId=node.nodeId, fieldPath='operatorId'))
        _active(context)
        if not report['errors'] and not report['missingInputs']:
            prepared = prepareRuntimeProject(prepared, projectFile.parent, registry, deviceTypes=deviceTypes,
                protectedPaths=runtime.sqliteProtectedPaths())
            _active(context)
            try:
                WorkflowCompiler(operatorRegistry=registry, deviceTypes=deviceTypes).compile(
                    prepared, pluginRootPaths=runtime.pluginRootPaths)
            except ValueError:
                report['compileStatus'] = 'failed'
                raise
            _active(context)
            report['compileStatus'] = 'passed'
    except CandidateTransferError:
        raise
    except (ValueError, OSError) as error:
        report['errors'].extend(_issues(error, document))
    report['status'] = ('FAILED' if report['errors'] else 'INCOMPLETE' if report['missingInputs'] else 'PASSED')
    return report


def validateCandidateStream(runtime, requests, context):
    """The executing worker owns all files until checking has actually exited."""
    try:
        _active(context)
        iterator = iter(requests)
        first = next(iterator, None)
        if first is None or first.WhichOneof('payload') != 'metadata':
            raise CandidateTransferError('E_CANDIDATE_TRANSFER', 'first candidate chunk must contain metadata')
        metadata = first.metadata
        if not metadata.draft_revision or not metadata.expected_runtime_instance_id:
            raise CandidateTransferError('E_CANDIDATE_TRANSFER', 'revision and expected Runtime instance are required')
        if metadata.expected_runtime_instance_id != runtime.runtimeInstanceId:
            raise CandidateTransferError('E_CANDIDATE_INSTANCE', 'Runtime instance changed')
        if not 0 < metadata.size_bytes <= MAX_CANDIDATE_BYTES or re.fullmatch('[0-9a-f]{64}', metadata.sha256) is None:
            raise CandidateTransferError('E_CANDIDATE_TRANSFER', 'invalid candidate size or SHA-256')
        with tempfile.TemporaryDirectory(prefix='emo-candidate-') as directory:
            folder = Path(directory)
            package = folder / 'candidate.zip'
            received, digest = 0, hashlib.sha256()
            with package.open('wb') as output:
                for chunk in iterator:
                    _active(context)
                    if chunk.WhichOneof('payload') != 'data' or not 0 < len(chunk.data) <= MAX_CANDIDATE_CHUNK_BYTES:
                        raise CandidateTransferError('E_CANDIDATE_TRANSFER', 'candidate data chunk is empty, oversized or has repeated metadata')
                    received += len(chunk.data)
                    if received > metadata.size_bytes:
                        raise CandidateTransferError('E_CANDIDATE_TRANSFER', 'candidate exceeds declared size')
                    digest.update(chunk.data)
                    output.write(chunk.data)
            _active(context)
            if received != metadata.size_bytes or digest.hexdigest() != metadata.sha256:
                raise CandidateTransferError('E_CANDIDATE_TRANSFER', 'candidate length or digest mismatch')
            extracted = folder / 'project'
            extracted.mkdir()
            projectFile = _extract(package, extracted, context)
            report = checkCandidate(runtime, projectFile, metadata.draft_revision, context)
            _active(context)
            return pb.CandidateProjectReply(ok=True, code='OK', report_json=json.dumps(report, ensure_ascii=True))
    except CandidateTransferError as error:
        return pb.CandidateProjectReply(ok=False, code=error.code, message=str(error))
