"""Bounded, pinned Runtime connection for an isolated operator debug window."""
from __future__ import annotations

from copy import copy
import hashlib
import threading
import time
from uuid import uuid4

from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.operator_debug.contracts import encode, parse, MAX_REQUEST_BYTES
from emo_master.apps.runtime.operator_debug.assets import CHUNK_BYTES, VALUE_BYTES


class DebugConnection:
    def __init__(self, client):
        self.origin = client
        self.client = copy(client)
        self.client.deadlineMs = 5000
        self.service = client.runtimeService
        self.identity = {}
        self.openId = uuid4().hex
        self.stop = threading.Event()
        self.renewer = None
        self.leaseError = ""
        self.uncertain = False
        self.executionPhase = "idle"

    def attached(self):
        return self.origin.runtimeService is self.service

    def call(self, method, **fields):
        request = pb.OperatorDebugRequest(**dict(self.identity, **fields))
        reply = self.client.operatorDebugCall(method, request)
        if not reply.ok:
            raise RuntimeClientError(reply.code, reply.message)
        value = parse(reply.snapshot_json, MAX_REQUEST_BYTES)
        if reply.content:
            value["content"] = bytes(reply.content)
        return value

    def mutation(self, method, **fields):
        return self.call(method, request_id=uuid4().hex, **fields)

    def open(self, payload, key, operatorId):
        capability = self.call("GetOperatorDebugCapabilities")
        supported = next((row for row in capability["operators"] if row["operatorId"] == operatorId), None)
        if not supported or not supported["supported"]:
            raise RuntimeClientError("E_DEBUG_UNSUPPORTED", (supported or {}).get("reason", "Operator unavailable"))
        self.identity = dict(runtime_instance_id=capability["runtimeInstanceId"])
        try:
            state = self.call("OpenOperatorDebugSession", open_request_id=self.openId,
                project_json=encode(payload, MAX_REQUEST_BYTES), project_id=key.projectId,
                workflow_id=key.workflowId, node_id=key.nodeId, operator_id=operatorId)
        except Exception as error:
            if isinstance(error, RuntimeClientError) and error.code.startswith("E_"):
                self.identity = {}
                raise
            # A response loss must never generate a second open request.
            state = self.call("GetOperatorDebugSession", open_request_id=self.openId)
        self.identity.update(session_id=state["sessionId"], generation=state["generation"])
        self.renewer = threading.Thread(target=self._renew, name="designer-debug-lease", daemon=True)
        self.renewer.start()
        return dict(capability=supported, session=state)

    def _renew(self):
        while not self.stop.wait(20):
            try:
                self.call("RenewOperatorDebugSession")
                self.leaseError = ""
            except Exception as error:
                self.leaseError = str(error)

    def snapshot(self, executionId="", sequence=0):
        state = self.call("GetOperatorDebugSession")
        if state["generation"] != self.identity["generation"]:
            self.identity = dict(self.identity, generation=state["generation"])
        result = self.call("GetOperatorDebugExecution", execution_id=executionId) if executionId else None
        events = self.call("ReadOperatorDebugEvents", after_sequence=sequence)
        return dict(session=state, result=result, events=events, leaseError=self.leaseError)

    def upload(self, raw, mime, provenance):
        if not 0 < len(raw) <= VALUE_BYTES:
            raise ValueError("Input file exceeds 64 MiB or is empty")
        assetId = ""
        for offset in range(0, len(raw), CHUNK_BYTES):
            result = self.mutation("WriteOperatorDebugAsset", asset_id=assetId, offset=offset,
                total_bytes=len(raw), content=raw[offset:offset+CHUNK_BYTES], mime_type=mime,
                sha256=hashlib.sha256(raw).hexdigest(), provenance_json=encode(provenance))
            assetId = result["assetId"]
        return {"assetRef": assetId}

    def execute(self, params, inputs):
        if self.uncertain:
            raise RuntimeClientError("E_DEBUG_UNCERTAIN", "End this session before another execution")
        self.executionPhase = "preparing"
        wire = {}
        for port, value in inputs.items():
            if "inline" in value:
                wire[port] = pb.OperatorDebugValue(inline_json=encode(value["inline"]))
            elif "assetRef" in value:
                wire[port] = pb.OperatorDebugValue(asset_ref=value["assetRef"])
            else:
                wire[port] = pb.OperatorDebugValue(output_ref=pb.OperatorDebugOutputRef(
                    execution_id=value["executionId"], port=value["port"]))
        raw = encode(params)
        prepared = self.mutation("PrepareOperatorDebugInputs", params_json=raw, inputs=wire)
        inputSetId = prepared["inputSetId"]
        requestId = uuid4().hex
        self.executionPhase = "submitted"
        try:
            result = self.call("ExecuteOperatorDebugNode", request_id=requestId,
                input_set_id=inputSetId, params_json=raw)
            if not result.get("executionId"):
                raise ValueError("Execution acknowledgement has no identity")
        except Exception:
            # Even an E_* reply can follow Worker dispatch if Runtime fails while
            # recording acceptance. Reconcile once by the original request ID;
            # neither a missing ledger entry nor a transport failure permits replay.
            try:
                result = self.call("GetOperatorDebugExecution", request_id=requestId)
                if not result.get("executionId") or result.get("requestId") != requestId:
                    raise ValueError("Execution lookup identity does not match the submitted request")
            except Exception as lookup:
                self.uncertain = True
                self.executionPhase = "unknown"
                raise RuntimeClientError("E_DEBUG_UNCERTAIN", "Execution acknowledgement lost; request " + requestId) from lookup
        self.executionPhase = "accepted"
        return result

    def download(self, assetId):
        chunks, offset, metadata = [], 0, None
        while True:
            part = self.call("ReadOperatorDebugAsset", asset_id=assetId, offset=offset)
            metadata = metadata or part["asset"]
            if part["asset"] != metadata or metadata["bytes"] > VALUE_BYTES:
                raise ValueError("Asset identity or size changed")
            chunk = part.get("content", b"")
            chunks.append(chunk)
            offset += len(chunk)
            if offset == metadata["bytes"]:
                break
            if not chunk or offset > metadata["bytes"]:
                raise ValueError("Incomplete asset")
        raw = b"".join(chunks)
        if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise ValueError("Asset digest mismatch")
        return dict(content=raw, asset=metadata)

    def close(self):
        self.stop.set()
        try:
            if "session_id" not in self.identity:
                if not self.identity:
                    return
                try:
                    state = self.call("GetOperatorDebugSession", open_request_id=self.openId)
                except RuntimeClientError as error:
                    if error.code == "E_DEBUG_SESSION_EXPIRED":
                        return
                    raise
                self.identity.update(session_id=state["sessionId"], generation=state["generation"])
            self.mutation("CloseOperatorDebugSession")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = self.call("GetOperatorDebugSession")
                if not state["resourcesHeld"]:
                    return state
                time.sleep(.05)
            raise RuntimeClientError("E_RESOURCE_CLEANUP_FAILED", "Runtime still holds debug resources")
        finally:
            if self.renewer is not None:
                self.renewer.join(6)
