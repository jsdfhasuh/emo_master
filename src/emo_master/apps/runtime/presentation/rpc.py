"""Thin typed RPC adapter; execution ownership stays in PresentationService."""
import json
import time

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.core.project.models import ProjectDocument
from emo_master.core.presentation.capture_limits import normalCaptureProfile


def wireResult(result):
    identity = result.identity
    wire = pb.DisplayResult(identity=pb.DisplayIdentity(
        runtime_instance_id=identity.runtimeInstanceId, job_id=identity.jobId,
        result_scope_id=identity.resultScopeId, invocation_id=identity.invocationId,
        result_key=identity.resultKey, result_ordinal=identity.resultOrdinal,
        execution_revision=identity.executionRevision, capture_plan_revision=identity.capturePlanRevision,
        mode=identity.mode), expected_source_ids=result.expectedSourceIds,
        status=result.status, execution_terminal=result.executionTerminal)
    if result.timing:
        wire.capture_started_ns = result.timing.captureStartedNs
        wire.scope_ended_ns = result.timing.scopeEndedNs
        wire.closed_ns = result.timing.closedNs
        wire.owner_age_ms = max(0, (time.perf_counter_ns() - result.timing.scopeEndedNs) / 1e6)
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
                trust=image.provenance.trust, adapter_version=image.provenance.adapterVersion or "",
                parent_frame_identity=image.provenance.parentFrameIdentity or ""))
    return wire


class DisplayRpc(rpc.DisplayServiceServicer):
    def __init__(self, service):
        self.service = service

    def Capabilities(self, request, context):
        capabilities = ["snapshot", "subscribe", "explicit_start", "asset_id", "finite_lease",
                        "bounded_replay", "project_jobs", "source_coverage", "start_request_lookup", "scope_retention",
                        "job_status_v1"]
        if self.service.supportsNormalCapture:
            capabilities.extend(["normal_start_capture", "normal_multi_scope", "normal_two_image_lanes"])
            if getattr(self.service.runtime, "supportsLegacySnapshotPolicy", False):
                capabilities.extend(["legacy_snapshot_policy_v1", "preview_snapshot_origin_v1"])
        return pb.DisplayCapabilities(runtime_instance_id=self.service.runtimeInstanceId,
                                      protocol_version="1.0", capabilities=capabilities,
            normal_capture_limits_json=json.dumps(normalCaptureProfile()) if self.service.supportsNormalCapture else "")

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
        with self.service.lock:
            projectId = getattr(request, "project_id", "")
            jobs = self.service.runtime.jobRepository.all()
            # Empty input keeps the legacy captured-jobs-only contract.
            jobs = [job for job in jobs if (job.projectId == projectId if projectId
                                            else job.jobId in self.service.jobs)]
            result = []
            for job in jobs:
                config = self.service.jobs.get(job.jobId, {})
                identity = config.get("identity", {})
                plan = json.loads(config.get("plan", "{}"))
                result.append(pb.DisplayJob(job_id=job.jobId, status=job.status,
                    project_id=job.projectId, workflow_id=job.workflowId,
                    mode=identity.get("mode", job.executionMode), capture_enabled=bool(config.get("capture")),
                    source_ids=list(plan.get("sources", {})),
                    sources_json=json.dumps(plan.get("sources", {})),
                    capture_definition_json=config.get("captureDefinitionJson", ""),
                    capture_limits_json=config.get("limitsJson", ""),
                    capture_plan_revision=identity.get("capturePlanRevision", ""),
                    execution_revision=identity.get("executionRevision", ""),
                    runtime_instance_id=self.service.runtimeInstanceId,
                    start_request_id=getattr(self.service.runtime, "_jobStartRequestIds", {}).get(job.jobId, ""),
                    accepted_at_ms=job.acceptedAtMs,
                    legacy_snapshot_policy=job.legacySnapshotPolicy,
                    resources_released=job.isTerminal and not config
                    and not self.service.runtime.jobSupervisor.ownsJobResources(job.jobId)))
            return pb.DisplayJobs(jobs=result)

    def Snapshot(self, request, context):
        return self._snapshot(request, context, incremental=request.replay)

    def GetJob(self, request, context):
        """Bounded status lookup; observation never acquires execution ownership."""
        if not all((request.runtime_instance_id, request.project_id, request.job_id)):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "JOB_IDENTITY_REQUIRED")
        if request.runtime_instance_id != self.service.runtimeInstanceId:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "RESET_REQUIRED")
        with self.service.lock:
            job = self.service.runtime.jobRepository.getCurrentSnapshot(request.job_id)
            if job is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "JOB_NOT_FOUND")
            if job.projectId != request.project_id:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, "PROJECT_MISMATCH")
            config = self.service.jobs.get(job.jobId, {})
            return pb.DisplayJob(runtime_instance_id=self.service.runtimeInstanceId,
                project_id=job.projectId, job_id=job.jobId, status=job.status,
                workflow_id=job.workflowId, mode=config.get("identity", {}).get("mode", job.executionMode),
                capture_enabled=bool(config.get("capture")),
                resources_released=job.isTerminal and not config
                and not self.service.runtime.jobSupervisor.ownsJobResources(job.jobId))

    def _snapshot(self, request, context, incremental=False):
        if request.job_id not in self.service.jobs:
            context.abort(grpc.StatusCode.NOT_FOUND, "unknown display Job")
        snapshot = self.service.store.snapshot(request.job_id, request.after_cursor, incremental)
        return pb.DisplaySnapshot(runtime_instance_id=self.service.runtimeInstanceId, job_id=request.job_id,
            cursor=snapshot["cursor"], reset_required=snapshot["reset"] or request.runtime_instance_id != self.service.runtimeInstanceId,
            results=[wireResult(result) for result in snapshot["results"]], latest_started_ordinals=snapshot["high"],
            expired_scope_ordinals=snapshot["expired"])

    def Subscribe(self, request, context):
        cursor = request.after_cursor
        while context.is_active():
            request.after_cursor = cursor
            result = self._snapshot(request, context, incremental=True)
            if result.cursor != cursor or result.reset_required:
                yield result
                cursor = result.cursor
                request.runtime_instance_id = result.runtime_instance_id
                time.sleep(0.02)
            else:
                self.service.store.waitForChange(result.cursor)

    def _asset(self, request, context, action):
        if request.runtime_instance_id != self.service.runtimeInstanceId:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "RESET_REQUIRED")
        try:
            return action()
        except KeyError:
            context.abort(grpc.StatusCode.NOT_FOUND, "RESOURCE_EXPIRED")
        except ValueError as error:
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(error))
        except TimeoutError as error:
            context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, str(error))

    def ReadAsset(self, request, context):
        def read():
            data, sha = self.service.assets.read(request.job_id, request.resource_id)
            return pb.DisplayAsset(content=data, sha256=sha)
        return self._asset(request, context, read)

    def AcquireLease(self, request, context):
        def acquire():
            leaseId = self.service.assets.lease(request.job_id, request.resource_id, request.ttl_ms)
            return pb.DisplayLease(lease_id=leaseId, resource_id=request.resource_id, ttl_ms=request.ttl_ms)
        return self._asset(request, context, acquire)

    def ReleaseLease(self, request, context):
        self.service.assets.release(request.lease_id)
        return pb.DisplayEmpty()

    def ReleaseJob(self, request, context):
        if request.runtime_instance_id != self.service.runtimeInstanceId:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "RESET_REQUIRED")
        try:
            self.service.release(request.job_id)
        except ValueError as error:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(error))
        return pb.DisplayEmpty()

    def DiscardPrepared(self, request, context):
        try:
            self.service.discardPrepared(request.prepared_id)
        except (ValueError, KeyError) as error:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(error))
        return pb.DisplayEmpty()
