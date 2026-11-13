"""Read actual target declarations through the protocol imported by this service."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock

import grpc

from emo_panel_protocol.connection import ConnectionProfile, createChannel
from emo_panel_protocol.generated import runtime_pb2 as pb, runtime_pb2_grpc
from .errors import AuthoringError


# Protobuf's builder installs these classes dynamically (no generated stubs).
# Resolve the actual imported protocol classes without suppressing type checks.
CandidateProjectChunk = getattr(pb, "CandidateProjectChunk")
CandidateProjectMetadata = getattr(pb, "CandidateProjectMetadata")


async def _file_work(function, *args):
    """A cancelled read/hash still owns its input until the worker has exited."""
    pending = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        try:
            await pending
        except Exception:
            pass
        raise


def _package_digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(256 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _json_field(raw, field, unavailable, *, expected=None):
    if raw == "":
        unavailable.append(field)
        return None
    try:
        value = json.loads(raw)
        if expected is not None and not isinstance(value, expected):
            raise ValueError("unexpected declaration type")
        return value
    except (ValueError, TypeError) as error:
        raise AuthoringError("E_CATALOG_METADATA", "Target returned invalid declaration metadata",
                             status=502, diagnostics=[{"code": "E_CATALOG_METADATA",
                                                      "message": "Invalid declaration metadata",
                                                      "fieldPath": field}]) from error


def operator_dto(info, *, target_id, runtime_instance_id, observed_at):
    """JSON schema extensions and full port objects are intentionally not filtered."""
    unavailable = []
    result = {"operatorId": info.operator_id, "displayName": info.display_name,
              "version": info.version, "category": info.category, "summary": info.summary,
              "targetId": target_id, "runtimeInstanceId": runtime_instance_id or None,
              "observedAt": observed_at, "source": "live"}
    for destination, source, expected in (
        ("inputPorts", "input_port_specs_json", dict),
        ("outputPorts", "output_port_specs_json", dict),
        ("paramSchema", "param_schema_json", dict),
        ("deviceRequirements", "device_requirements_json", dict),
        ("editorSpec", "editor_spec_json", dict),
        ("editorIssues", "editor_issues_json", list),
        ("nextCycleParameterPaths", "next_cycle_parameter_paths_json", list),
    ):
        result[destination] = _json_field(getattr(info, source, ""), destination,
                                          unavailable, expected=expected)
    paths = result["nextCycleParameterPaths"]
    if paths is not None and not all(isinstance(path, str) for path in paths):
        raise AuthoringError("E_CATALOG_METADATA", "Target returned invalid parameter paths",
                             status=502, fieldPath="nextCycleParameterPaths")
    for destination, source in (("minCoreVersion", "min_core_version"),
                                ("maxCoreVersion", "max_core_version")):
        result[destination] = getattr(info, source, "") or None
        if result[destination] is None:
            unavailable.append(destination)
    if not runtime_instance_id:
        unavailable.append("runtimeInstanceId")
    result["unavailableFields"] = unavailable
    return result


class RuntimeCatalog:
    def __init__(self, targets: dict[str, str], *, timeout=5.0):
        self.targets = {key: ConnectionProfile(address, timeout) for key, address in targets.items()}
        self._cache: dict[str, dict[str, dict[str, object]]] = {}
        self._cache_lock = RLock()

    def _profile(self, target_id):
        try:
            return self.targets[target_id]
        except KeyError as error:
            raise AuthoringError("E_TARGET", "Unknown Runtime target", status=404,
                                 targetId=target_id) from error

    def _call(self, target_id, method, request):
        profile = self._profile(target_id)
        try:
            with createChannel(profile) as channel:
                stub = runtime_pb2_grpc.RuntimeServiceStub(channel)
                return getattr(stub, method)(request, timeout=profile.timeoutSeconds)
        except grpc.RpcError as error:
            raise AuthoringError("E_TARGET_UNAVAILABLE", "Runtime catalog request failed",
                                 status=502, targetId=target_id) from error

    def _operators(self, target_id):
        reply = self._call(target_id, "ListOperators", pb.ListOperatorsRequest())
        observed = datetime.now(timezone.utc).isoformat()
        instance = getattr(reply, "runtime_instance_id", "")
        entries = [operator_dto(info, target_id=target_id, runtime_instance_id=instance,
                                observed_at=observed) for info in reply.operators]
        with self._cache_lock:
            # Replace only this target's complete observation, never borrow another target.
            self._cache[target_id] = {item["operatorId"]: deepcopy(item) for item in entries}
        return entries, instance, observed

    def list_operators(self, target_id, query=None, category=None):
        entries, instance, observed = self._operators(target_id)
        if category:
            entries = [entry for entry in entries if entry["category"] == category]
        if query:
            needle = query.casefold()
            entries = [entry for entry in entries if needle in " ".join(
                str(entry[field]) for field in ("operatorId", "displayName", "summary", "category")
            ).casefold()]
        fields = ("operatorId", "displayName", "version", "category", "summary")
        return {"targetId": target_id, "runtimeInstanceId": instance or None,
                "source": "live", "observedAt": observed,
                "operators": [{key: item[key] for key in fields} for item in entries]}

    def get_operator(self, target_id, operator_id):
        entries, _, _ = self._operators(target_id)
        for entry in entries:
            if entry["operatorId"] == operator_id:
                return entry
        raise AuthoringError("E_OPERATOR_NOT_LISTED", "Target does not list this operator",
                             status=404, targetId=target_id, operatorId=operator_id)

    def get_cached_operator(self, target_id, operator_id):
        with self._cache_lock:
            value = deepcopy(self._cache.get(target_id, {}).get(operator_id))
        if value is not None:
            value["source"] = "cache"
        return value

    def list_device_types(self, target_id):
        reply = self._call(target_id, "ListDeviceTypes", pb.ListDeviceTypesRequest())
        unavailable = []
        declarations = _json_field(reply.types_json, "deviceTypes", unavailable, expected=dict)
        if declarations is None:
            raise AuthoringError("E_CATALOG_METADATA", "Target did not provide device declarations", status=502)
        return {"targetId": target_id, "source": "live",
                "observedAt": datetime.now(timezone.utc).isoformat(),
                "deviceTypes": [{"typeId": key, "declaration": value}
                                for key, value in declarations.items()],
                "requirements": _json_field(reply.requirements_json, "requirements", unavailable, expected=dict),
                "rejected": _json_field(reply.rejected_json, "rejected", unavailable, expected=dict),
                "unavailableFields": unavailable}

    def get_device_type(self, target_id, type_id):
        result = self.list_device_types(target_id)
        for entry in result["deviceTypes"]:
            if entry["typeId"] == type_id:
                return dict(entry, targetId=target_id, source=result["source"], observedAt=result["observedAt"])
        raise AuthoringError("E_DEVICE_TYPE_NOT_LISTED", "Target does not list this device type",
                             status=404, targetId=target_id, typeId=type_id)

    async def precheck(self, target_id, zip_path: Path, revision, *, expected_instance=None,
                       size_bytes=None, sha256=None):
        profile = self._profile(target_id)
        if expected_instance is None:
            observation = await asyncio.to_thread(self.list_operators, target_id)
            expected_instance = observation["runtimeInstanceId"]
        if not expected_instance:
            raise AuthoringError("E_RUNTIME_IDENTITY", "Target did not provide its Runtime instance identity",
                                 status=502, targetId=target_id)
        if size_bytes is None or sha256 is None:
            size_bytes, sha256 = await _file_work(_package_digest, zip_path)

        channel = createChannel(profile, asynchronous=True)
        call = None
        try:
            stub = runtime_pb2_grpc.RuntimeServiceStub(channel)
            # Explicit writes keep a single owner for the file reader rather
            # than leaving a separate gRPC iterator task reading after cancel.
            call = stub.ValidateCandidateProject(timeout=profile.timeoutSeconds)
            await call.write(CandidateProjectChunk(metadata=CandidateProjectMetadata(
                draft_revision=revision, expected_runtime_instance_id=expected_instance,
                size_bytes=size_bytes, sha256=sha256)))
            with zip_path.open("rb") as reader:
                while chunk := await _file_work(reader.read, 256 * 1024):
                    await call.write(CandidateProjectChunk(data=chunk))
            await call.done_writing()
            reply = await call
            if not reply.ok:
                raise AuthoringError(reply.code or "E_PRECHECK", reply.message or "Candidate request failed", status=502)
            try:
                report = json.loads(reply.report_json)
                if report["draftRevision"] != revision or report["scope"] != "target":
                    raise ValueError("Report does not match the submitted snapshot")
                if expected_instance and report["runtimeInstanceId"] != expected_instance:
                    raise ValueError("Report belongs to another Runtime instance")
                return report
            except (ValueError, KeyError, TypeError) as error:
                raise AuthoringError("E_PRECHECK_REPORT", "Invalid target precheck report", status=502) from error
        except asyncio.CancelledError:
            if call is not None:
                call.cancel()
            raise
        except grpc.RpcError as error:
            raise AuthoringError("E_TARGET_UNAVAILABLE", "Runtime candidate request failed", status=502,
                                 targetId=target_id) from error
        finally:
            await channel.close()
