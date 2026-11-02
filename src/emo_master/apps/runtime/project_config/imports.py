"""Remote first publication: declared bytes and the existing configuration writer."""
from copy import deepcopy
import hashlib

from emo_master.core.project.configuration import digest
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.resources import Resource
from .store import ConfigurationError


def prepareImport(runtime, request):
    document = ProjectDocument.model_validate_json(request.project_json)
    if document.schemaVersion not in {'2.6', '2.7', '2.8', '2.9'}:
        raise ConfigurationError('E_CONFIG_INVALID', 'remote import requires a supported online project format')
    payload = document.model_dump(mode='json')
    source = runtime.workspaceRoot.parent / 'online-projects' / hashlib.sha256(
        document.project.projectId.encode('utf-8')).hexdigest() / 'project.emoproj'
    with runtime._projectStateLock:
        runtime._checkInitialConfigurationAdmission()
        return runtime.projectConfiguration.stageInitial(dict(operation='initialize', requestId=request.request_id,
            expectedRuntimeInstanceId=request.expected_runtime_instance_id, expectedRevision=0,
            localPayload=payload, sourcePath=str(source.resolve())))


def initializeImport(runtime, request):
    if request.resource_root or request.project_path:
        raise ConfigurationError('E_CONFIG_IMPORT_PATH', 'remote import uses the Runtime-managed project directory')
    if request.request_id != request.import_id:
        raise ConfigurationError('E_CONFIG_REQUEST_MISMATCH', 'commit and lookup must use the prepared import request ID')
    with runtime._projectStateLock:
        entry = runtime.projectConfiguration.initialImport(request.import_id, cleanup=not request.validate_only)
        staged = deepcopy(entry['initialRequest'])
        if request.expected_runtime_instance_id != runtime.runtimeInstanceId:
            raise ConfigurationError('E_CONFIG_INSTANCE', 'Runtime instance changed')
        if request.local_payload_json:
            payload = ProjectDocument.model_validate_json(request.local_payload_json).model_dump(mode='json')
            if digest(payload) != digest(staged['localPayload']):
                raise ConfigurationError('E_CONFIG_REQUEST_MISMATCH', 'candidate changed; prepare a new import explicitly')
    if not request.validate_only:
        # Same request ledger, durable receipt, cancellation and publish path.
        from pathlib import Path
        Path(staged['sourcePath']).parent.mkdir(parents=True, exist_ok=True)
        return runtime.projectConfiguration.start(staged)
    runtime._validateInitialProjectConfiguration(staged['localPayload'], staged['sourcePath'])
    with runtime._projectStateLock:
        entry = runtime.projectConfiguration.initialImport(request.import_id)
        entry['status'].update(state='VALIDATED', message='远程候选校验通过；尚未写入在线工程')
        return runtime.projectConfiguration.status(request.import_id)


def transferImportResource(runtime, request):
    """Called with the existing project lock held, including the bounded transfer."""
    cleanup = request.action in {'STATUS', 'CANCEL'}
    entry = runtime.projectConfiguration.initialImport(request.initial_import_id, cleanup=cleanup)
    payload = entry['initialRequest']['localPayload']
    if request.project_id != payload['project']['projectId']:
        raise ConfigurationError('E_RESOURCE_PROJECT', 'resource belongs to another import candidate')
    if request.replacement_of:
        raise ConfigurationError('E_RESOURCE_REPAIR_CONFIRM', 'initial import cannot replace an existing resource authority')
    resources = ProjectDocument.model_validate(payload).resources
    declared = list(resources.items.values()) if resources else []
    if request.action in {'LOOKUP', 'BEGIN'}:
        item = Resource.model_validate_json(request.resource_json)
        if item not in declared:
            raise ConfigurationError('E_RESOURCE_DECLARATION', 'resource is not declared by this import candidate')
        if request.action == 'BEGIN':
            if any(other is not entry and request.transfer_id in other.get('transferIds', ())
                   for other in runtime.projectConfiguration.requests.values()):
                raise ConfigurationError('E_RESOURCE_PROJECT', 'transfer belongs to another import candidate')
            entry['transferIds'].add(request.transfer_id)
    elif request.transfer_id not in entry['transferIds']:
        raise ConfigurationError('E_RESOURCE_PROJECT', 'transfer is not owned by this import candidate')
    return runtime.projectResourceTransfers.execute(request)
