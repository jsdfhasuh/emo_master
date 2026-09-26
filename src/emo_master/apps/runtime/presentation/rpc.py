"""Thin typed RPC adapter; execution ownership stays in PresentationService."""
import time

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.core.project.models import ProjectDocument


def wireResult(result):
    identity = result.identity
    wire = pb.DisplayResult(identity=pb.DisplayIdentity(
        runtime_instance_id=identity.runtimeInstanceId, job_id=identity.jobId,
        result_scope_id=identity.resultScopeId, invocation_id=identity.invocationId,
        result_key=identity.resultKey, result_ordinal=identity.resultOrdinal,
        execution_revision=identity.executionRevision, capture_plan_revision=identity.capturePlanRevision,
        mode=identity.mode), expected_source_ids=result.expectedSourceIds,
        status=result.status, execution_terminal=result.executionTerminal)
    for source in result.sources:
        item = wire.sources.add(source_id=source.sourceId, state=source.state,
                                reason_code=source.reasonCode or "", reason=source.reason or "")
        if source.valueJson is not None:
            item.value_json = source.valueJson
        if source.image:
            image = source.image
            item.image.CopyFrom(pb.DisplayImage(resource_id=image.resourceId, owner_result_key=image.ownerResultKey,
                byte_size=image.byteSize, sha256=image.sha256, mime_type=image.mimeType,
                frame_identity=image.provenance.frameIdentity, coordinate_space_id=image.provenance.coordinateSpaceId,
                trust=image.provenance.trust, adapter_version=image.provenance.adapterVersion or ""))
    return wire


class DisplayRpc(rpc.DisplayServiceServicer):
    def __init__(self, service):
        self.service = service

    def Capabilities(self, request, context):
        return pb.DisplayCapabilities(runtime_instance_id=self.service.runtimeInstanceId,
                                      protocol_version="1.0", capabilities=["snapshot", "subscribe", "explicit_start"])

    def Prepare(self, request, context):
        from pathlib import Path
        try:
            record = self.service.prepare(ProjectDocument.model_validate_json(request.project_json),
                Path(request.resource_root), siteValues=dict(request.site_values))
        except (ValueError, OSError) as error:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        return pb.DisplayPrepared(prepared_id=record.snapshot.snapshotId,
            execution_revision=record.snapshot.executionRevision, capture_plan_revision=record.snapshot.capturePlanRevision)

    def Start(self, request, context):
        try:
            return pb.DisplayJob(job_id=self.service.start(request.prepared_id))
        except (KeyError, ValueError, RuntimeError) as error:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(error))

    def ListJobs(self, request, context):
        return pb.DisplayJobs(jobs=[pb.DisplayJob(job_id=key, status=self.service.runtime.jobRepository.get(key).status)
                                   for key in self.service.jobs])

    def Snapshot(self, request, context):
        if request.job_id not in self.service.jobs:
            context.abort(grpc.StatusCode.NOT_FOUND, "unknown display Job")
        snapshot = self.service.store.snapshot(request.job_id, request.after_cursor)
        return pb.DisplaySnapshot(runtime_instance_id=self.service.runtimeInstanceId, job_id=request.job_id,
            cursor=snapshot["cursor"], reset_required=snapshot["reset"] or request.runtime_instance_id != self.service.runtimeInstanceId,
            results=[wireResult(result) for result in snapshot["results"]], latest_started_ordinals=snapshot["high"])

    def Subscribe(self, request, context):
        cursor = request.after_cursor
        while context.is_active():
            request.after_cursor = cursor
            result = self.Snapshot(request, context)
            if result.cursor != cursor or result.reset_required:
                yield result
                cursor = result.cursor
                request.runtime_instance_id = result.runtime_instance_id
            time.sleep(0.02)
