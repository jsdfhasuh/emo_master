"""Read current Runtime-owned runs without acquiring execution or device ownership."""
from __future__ import annotations

import json

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb


def runState(runtime, jobId):
    record = runtime.jobRepository.getCurrentSnapshot(jobId)
    if record is None:
        return None
    presentation = getattr(runtime, '_presentationOwner', None)
    config = {}
    if presentation is not None:
        with presentation.lock:
            source = presentation.jobs.get(jobId, {})
            config = dict(capture=bool(source.get('capture')), dispatch=dict(source.get('dispatch', {}))) if source else {}
    supervisor = runtime.jobSupervisor
    with supervisor._lock:
        group = runtime.deviceManager.dispatchGroups.get(jobId)
        dispatch = config.get('dispatch') or dict(runGroupId=record.runGroupId,
            sourceJobId=record.sourceJobId, targetSlotId=record.targetSlotId)
        owned = supervisor.ownsJobResources(jobId)
        groupActive = False
        if group is not None:
            slot = next((key for key, value in group.slots.items() if value.jobId == jobId), '')
            dispatch = dict(runGroupId=group.groupId, sourceJobId=group.source, targetSlotId=slot)
            if group.source == jobId:
                # A completed producer can still have managed consumers draining.
                owned = any(supervisor.ownsJobResources(member) for member in group.members)
                groupActive = any((memberRecord is not None and not memberRecord.isTerminal)
                    for member in group.members
                    for memberRecord in [runtime.jobRepository.getCurrentSnapshot(member)])
    document = runtime.loadedDocument
    workflow = document.workflows.get(record.workflowId) if document and document.project.projectId == record.projectId else None
    active = not record.isTerminal or owned or groupActive
    capture = bool(config.get('capture'))
    actions = (['stop'] if active else []) + (['view'] if capture else [])
    return pb.RunState(runtime_instance_id=runtime.runtimeInstanceId, project_id=record.projectId,
        workflow_id=record.workflowId, job_id=record.jobId, status=record.status,
        run_group_id=dispatch.get('runGroupId', ''), source_job_id=dispatch.get('sourceJobId', ''),
        target_slot_id=dispatch.get('targetSlotId', ''), active=active, owns_resources=owned,
        capture_enabled=capture, display_resources_released=record.isTerminal and not config and not owned,
        workflow_name=workflow.name if workflow else record.workflowId, accepted_at_ms=record.acceptedAtMs,
        started_at_ms=record.startedAtMs, ended_at_ms=record.endedAtMs, error_code=record.errorCode,
        message=record.message, start_request_id=runtime._jobStartRequestIds.get(jobId, ''), allowed_actions=actions)


def currentRuns(runtime, projectId='', workflowId='', *, includeTerminal=False):
    rows = []
    for record in runtime.jobRepository.all():
        if (projectId and record.projectId != projectId) or (workflowId and record.workflowId != workflowId):
            continue
        row = runState(runtime, record.jobId)
        if row is not None and (includeTerminal or row.active):
            rows.append(row)
    return sorted(rows, key=lambda row: (row.accepted_at_ms, row.job_id))


def startAvailability(runtime, workflowId):
    """Cheap admission information; full preflight remains solely in StartJob."""
    if runtime.loadedDocument is None:
        return False, 'E_PROJECT_NOT_LOADED'
    if runtime._closing or runtime._closed:
        return False, 'E_RUNTIME_CLOSING'
    configuration = runtime.projectConfiguration
    if (runtime.onlineRecoveryError or configuration.active or configuration.closed or configuration.publicationError):
        return False, 'E_CONFIG_NOT_READY'
    from emo_master.core.workflow.dispatch_contracts import groupSlots
    required = 1 + len(groupSlots(runtime.loadedDocument, workflowId))
    with runtime.jobSupervisor._lock:
        limit = runtime.jobSupervisor.maxConcurrentJobs
        if limit is not None and len(runtime.jobSupervisor._ownedJobs()) + required > limit:
            return False, 'E_MAX_CONCURRENT_JOBS'
    return True, ''


class RunReadRpcMixin:
    def GetRuntimeInfo(self, request, context):
        with self._projectStateLock:
            document = self.loadedDocument
            current = self.projectConfiguration.current or {}
            production = document.production if document else None
            from emo_master.core.presentation.history import historyRevision
            from emo_master.core.presentation.models import Presentation
            presentation = current.get('payload', {}).get('presentation')
            return pb.RuntimeInfoReply(ok=True, runtime_instance_id=self.runtimeInstanceId,
                protocol_version='1.0', capabilities=['runtime_info_v1', 'run_catalog_v1', 'stop_identity_v1',
                    'remote_project_import_v1', 'history_query_v1', 'history_image_v1', 'project_resource_read_v1'],
                production_mode=self.productionMode, project_id=document.project.projectId if document else '',
                document_revision=current.get('documentRevision', 0),
                production_inputs_json=json.dumps(production.inputs if production else {}, ensure_ascii=False),
                production_execution_mode=production.mode if production else 'single',
                startup_workflow_ids=document.startupWorkflows() if document else [],
                history_definition_revision=historyRevision(Presentation.model_validate(presentation)) if presentation else '')

    def ListRuns(self, request, context):
        if request.runtime_instance_id and request.runtime_instance_id != self.runtimeInstanceId:
            return pb.ListRunsReply(ok=False, code='RESET_REQUIRED', message='Runtime instance changed',
                                   runtime_instance_id=self.runtimeInstanceId)
        return pb.ListRunsReply(ok=True, runtime_instance_id=self.runtimeInstanceId,
            runs=currentRuns(self, request.project_id, request.workflow_id, includeTerminal=request.include_terminal))

    def GetRunState(self, request, context):
        if not all((request.runtime_instance_id, request.project_id, request.job_id)):
            return pb.GetRunStateReply(ok=False, code='JOB_IDENTITY_REQUIRED', message='Runtime, project and Job identity required')
        if request.runtime_instance_id != self.runtimeInstanceId:
            return pb.GetRunStateReply(ok=False, code='RESET_REQUIRED', message='Runtime instance changed')
        row = runState(self, request.job_id)
        if row is None:
            return pb.GetRunStateReply(ok=False, code='JOB_NOT_FOUND', message='Job is not owned by this Runtime instance')
        if row.project_id != request.project_id:
            return pb.GetRunStateReply(ok=False, code='PROJECT_MISMATCH', message='Job belongs to another project')
        return pb.GetRunStateReply(ok=True, run=row)


def stopIdentityError(runtime, request):
    fields = ('expected_runtime_instance_id', 'expected_project_id', 'expected_run_group_id')
    hasField = getattr(request, 'HasField', None)
    supplied = {field: getattr(request, field, '') for field in fields
                if (hasField(field) if callable(hasField) else bool(getattr(request, field, '')))}
    if not supplied:
        return ''  # Existing clients retain their original StopJob contract.
    if ('expected_runtime_instance_id' in supplied
            and supplied['expected_runtime_instance_id'] != runtime.runtimeInstanceId):
        return 'RESET_REQUIRED: Runtime instance changed'
    row = runState(runtime, request.job_id)
    if row is None:
        return 'JOB_NOT_FOUND: Job is not owned by this Runtime instance'
    if 'expected_project_id' in supplied and supplied['expected_project_id'] != row.project_id:
        return 'PROJECT_MISMATCH: Job belongs to another project'
    if 'expected_run_group_id' in supplied and supplied['expected_run_group_id'] != row.run_group_id:
        return 'RUN_GROUP_MISMATCH: Job belongs to another run group'
    return ''
