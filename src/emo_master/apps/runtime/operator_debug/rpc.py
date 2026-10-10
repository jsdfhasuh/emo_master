from __future__ import annotations

import base64
from typing import Any

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _pb
from emo_master.apps.runtime.operator_debug.contracts import DebugError, MAX_REQUEST_BYTES, encode, fail, trustedEntries

pb: Any = _pb


class OperatorDebugRpcMixin:
    """Keep wire conversion separate from process and request ownership."""

    def _operatorDebugRpc(self, action, request, context, debugKind="operator"):
        runtime: Any = self
        manager = runtime.operatorDebugManager
        try:
            runtime.jobSupervisor.assertMutationAllowed()
            if request.ByteSize() > MAX_REQUEST_BYTES:
                fail("E_DEBUG_LIMIT", "debug request exceeds 768 KiB")
            if action == "capabilities":
                if request.runtime_instance_id and request.runtime_instance_id != runtime.runtimeInstanceId:
                    fail("E_DEBUG_STALE_SESSION", "Runtime changed")
                with manager.lock:
                    manager.admitted = trustedEntries(runtime.pluginScanResult.activeOperators)
                    payload = manager.capabilities(runtime.pluginScanResult.activeOperators)
                    if debugKind == "workflow":
                        payload.update(debugKind="workflow", commands=["continue", "pause", "into", "over", "out", "runTo", "breakpoints", "trial"],
                                       maxBreakpoints=256, maxSnapshots=32, fixedStartupDraft=True)
            else:
                values = dict(debugKind=debugKind, controlJson=request.control_json or "{}",
                    runtimeInstanceId=request.runtime_instance_id, sessionId=request.session_id,
                    generation=request.generation, requestId=request.request_id, openRequestId=request.open_request_id,
                    projectJson=request.project_json, projectId=request.project_id, workflowId=request.workflow_id,
                    nodeId=request.node_id, operatorId=request.operator_id, resourceRoot=request.resource_root,
                    inputSetId=request.input_set_id, paramsJson=request.params_json or "{}", timeoutMs=request.timeout_ms,
                    executionId=request.execution_id, afterSequence=request.after_sequence, limit=request.limit or 100,
                    assetId=request.asset_id, offset=request.offset, totalBytes=request.total_bytes,
                    content=base64.b64encode(request.content).decode("ascii"), mimeType=request.mime_type,
                    sha256=request.sha256, provenanceJson=request.provenance_json or "{}",
                    inputs={port: ({"inlineJson": value.inline_json} if value.WhichOneof("value") == "inline_json"
                                   else {"outputRef": {"executionId": value.output_ref.execution_id, "port": value.output_ref.port}}
                                   if value.WhichOneof("value") == "output_ref" else {"assetRef": value.asset_ref})
                            for port, value in request.inputs.items()})
                if action == "open":
                    # Same atomic admission boundary as Jobs, camera/PLC and page runs.
                    with runtime._projectStateLock, runtime._previewJobLock:
                        if runtime._closing:
                            fail("E_RUNTIME_CLOSING", "Runtime is closing")
                        if not getattr(context, "is_active", lambda: True)():
                            fail("E_CANCELLED", "request cancelled before acceptance")
                        with manager.lock:
                            manager.admitted = trustedEntries(runtime.pluginScanResult.activeOperators)
                        payload = manager.call(action, values)
                else:
                    payload = manager.call(action, values)
            payload = dict(payload)
            content = payload.pop("content", b"")
            return pb.OperatorDebugReply(ok=True, runtime_instance_id=runtime.runtimeInstanceId, content=content,
                session_id=payload.get("sessionId", request.session_id), generation=payload.get("generation", request.generation),
                state=payload.get("state", ""), execution_id=payload.get("executionId", ""),
                input_set_id=payload.get("inputSetId", ""), snapshot_json=encode(payload, MAX_REQUEST_BYTES))
        except (DebugError, ValueError, TypeError, KeyError, OSError) as error:
            return pb.OperatorDebugReply(ok=False, code=getattr(error, "code", "E_DEBUG_CONTEXT_INVALID"),
                message=str(error)[:2048], runtime_instance_id=runtime.runtimeInstanceId)

    def GetOperatorDebugCapabilities(self, request, context):
        return self._operatorDebugRpc("capabilities", request, context)

    def OpenOperatorDebugSession(self, request, context):
        return self._operatorDebugRpc("open", request, context)

    def GetOperatorDebugSession(self, request, context):
        return self._operatorDebugRpc("get", request, context)

    def PrepareOperatorDebugInputs(self, request, context):
        return self._operatorDebugRpc("prepare", request, context)

    def ExecuteOperatorDebugNode(self, request, context):
        return self._operatorDebugRpc("execute", request, context)

    def GetOperatorDebugExecution(self, request, context):
        return self._operatorDebugRpc("execution", request, context)

    def ReadOperatorDebugEvents(self, request, context):
        return self._operatorDebugRpc("events", request, context)

    def CancelOperatorDebugExecution(self, request, context):
        return self._operatorDebugRpc("cancel", request, context)

    def ResetOperatorDebugSession(self, request, context):
        return self._operatorDebugRpc("reset", request, context)

    def RenewOperatorDebugSession(self, request, context):
        return self._operatorDebugRpc("renew", request, context)

    def CloseOperatorDebugSession(self, request, context):
        return self._operatorDebugRpc("close", request, context)

    def WriteOperatorDebugAsset(self, request, context):
        return self._operatorDebugRpc("upload", request, context)

    def ReadOperatorDebugAsset(self, request, context):
        return self._operatorDebugRpc("asset", request, context)

    def ListOperatorDebugSources(self, request, context):
        return self._operatorDebugRpc("sources", request, context)

    def ImportOperatorDebugSource(self, request, context):
        return self._operatorDebugRpc("import", request, context)

    def CopyOperatorDebugVariables(self, request, context):
        return self._operatorDebugRpc("copyVariables", request, context)

    def GetWorkflowDebugCapabilities(self, request, context):
        return self._operatorDebugRpc("capabilities", request, context, "workflow")

    def OpenWorkflowDebugSession(self, request, context):
        return self._operatorDebugRpc("open", request, context, "workflow")

    def GetWorkflowDebugSession(self, request, context):
        return self._operatorDebugRpc("get", request, context, "workflow")

    def PrepareWorkflowDebugInputs(self, request, context):
        return self._operatorDebugRpc("prepare", request, context, "workflow")

    def StartWorkflowDebug(self, request, context):
        return self._operatorDebugRpc("flow_start", request, context, "workflow")

    def ControlWorkflowDebug(self, request, context):
        return self._operatorDebugRpc("flow_control", request, context, "workflow")

    def GetWorkflowDebugCommand(self, request, context):
        return self._operatorDebugRpc("flow_command", request, context, "workflow")

    def GetWorkflowDebugSnapshot(self, request, context):
        return self._operatorDebugRpc("flow_snapshot", request, context, "workflow")

    def ReadWorkflowDebugEvents(self, request, context):
        return self._operatorDebugRpc("events", request, context, "workflow")

    def WriteWorkflowDebugAsset(self, request, context):
        return self._operatorDebugRpc("upload", request, context, "workflow")

    def ReadWorkflowDebugAsset(self, request, context):
        return self._operatorDebugRpc("asset", request, context, "workflow")

    def RenewWorkflowDebugSession(self, request, context):
        return self._operatorDebugRpc("renew", request, context, "workflow")

    def CloseWorkflowDebugSession(self, request, context):
        return self._operatorDebugRpc("close", request, context, "workflow")

    def _operatorDebugSources(self, spec, request, importing):
        from .assets import VALUE_BYTES, decode
        from .data import companionPort
        runtime: Any = self
        if runtime.loadedProjectId != spec["projectId"] or not runtime._loadedProjectPreviewKey:
            if importing:
                fail("E_DEBUG_RESULT_EXPIRED", "no complete historical data for this draft project")
            return dict(sources=[], nextOffset=0)
        store = runtime.previewAssetStore
        def identity(asset):
            siblings = {row.port: row.assetId for row in store._assets.values()
                        if row.captureId and row.captureId == asset.captureId and row.projectKey == asset.projectKey}
            frame = companionPort(asset.port, siblings)
            return dict(kind="history", projectId=spec["projectId"], sourceId=asset.assetId,
                workflowId=asset.workflowId, nodeId=asset.nodeId, port=asset.port, portType=asset.portType,
                jobId=asset.originJobId, workflowRunId=asset.workflowRunId, nodeRunId=asset.nodeRunId,
                iterationPath=list(asset.iterationPath), projectRevision=asset.originProjectRevision,
                captureId=asset.captureId, createdAtMs=asset.createdAtMs, mime=asset.mimeType,
                companionId=siblings.get(frame, ""))
        with store._lock:
            if importing:
                asset = store.resolve(request.get("assetId", ""))
                if asset is None or asset.projectKey != runtime._loadedProjectPreviewKey:
                    fail("E_DEBUG_RESULT_EXPIRED", "historical asset expired or belongs to another project")
                if asset.path.stat().st_size > VALUE_BYTES:
                    fail("E_DEBUG_LIMIT", "historical asset exceeds transport capacity")
                raw, mime = store.readBytes(asset.assetId, projectKey=runtime._loadedProjectPreviewKey)
                value, _ = decode(raw, mime)
                return value, identity(asset)
            candidates = sorted((asset for asset in store._assets.values()
                if asset.projectKey == runtime._loadedProjectPreviewKey and asset.path.is_file()),
                key=lambda asset: (asset.createdAtMs, asset.assetId), reverse=True)
            offset = request.get("offset", 0)
            selected = candidates[offset:offset + min(request.get("limit", 100), 100)]
            return dict(sources=[identity(asset) for asset in selected], nextOffset=offset+len(selected))

    def _operatorDebugVariables(self, spec):
        from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
        runtime: Any = self
        if runtime.loadedProjectId != spec["projectId"]:
            fail("E_DEBUG_CONTEXT_INVALID", "production project differs from the selected draft")
        declarations = {key: value for key, value in spec.get("variableDefinitions", {}).items()
                        if value.get("lifetime") == "persistent" and value.get("kind") == "variable"}
        return ProjectGlobalVariables(runtime.sqliteStore, spec["projectId"], declarations).readMany(declarations)
