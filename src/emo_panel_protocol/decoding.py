"""Wire decoding without image/Qt dependencies."""
import json
from .results import ClosedResult


def decodeResult(wire):
    identity = wire.identity
    sources = []
    for source in wire.sources:
        value = {"sourceId": source.source_id, "state": source.state}
        if source.HasField("value_json"):
            value["valueJson"] = source.value_json
        if source.reason_code:
            value.update(reasonCode=source.reason_code, reason=source.reason or source.reason_code)
        if source.HasField("image"):
            image = source.image
            value["image"] = {"resourceId": image.resource_id, "ownerResultKey": image.owner_result_key,
                "byteSize": image.byte_size, "sha256": image.sha256, "mimeType": image.mime_type,
                "provenance": {"frameIdentity": image.frame_identity, "coordinateSpaceId": image.coordinate_space_id,
                               "trust": image.trust, "adapterVersion": image.adapter_version or None,
                               "parentFrameIdentity": image.parent_frame_identity or None}}
        sources.append(value)
    return ClosedResult.model_validate_json(json.dumps({"identity": {
        "runtimeInstanceId": identity.runtime_instance_id, "jobId": identity.job_id,
        "resultScopeId": identity.result_scope_id, "invocationId": identity.invocation_id,
        "resultKey": identity.result_key, "resultOrdinal": identity.result_ordinal,
        "executionRevision": identity.execution_revision, "capturePlanRevision": identity.capture_plan_revision,
        "mode": identity.mode}, "expectedSourceIds": list(wire.expected_source_ids), "sources": sources,
        "status": wire.status, "executionTerminal": wire.execution_terminal,
        "timing": {"captureStartedNs": wire.capture_started_ns, "scopeEndedNs": wire.scope_ended_ns,
                   "closedNs": wire.closed_ns} if wire.capture_started_ns else None}))
