from __future__ import annotations

import json
from dataclasses import dataclass, field
from collections.abc import Iterable as IterableABC
import inspect
import threading
from typing import Any, Iterable, Protocol, cast

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _runtime_pb2
from emo_master.apps.runtime.context.global_counters import MAX_GLOBAL_COUNTER_VALUE
from emo_master.core.contracts.execution import RuntimeEventDTO
from emo_master.core.contracts.port_types import (
    PortSpecValidationError,
    validatePortSpec,
)

runtime_pb2: Any = _runtime_pb2

try:
    import grpc
except Exception:  # pragma: no cover
    grpc = None  # type: ignore[assignment]


@dataclass(frozen=True)
class OperatorDefinition:
    operatorId: str
    displayName: str
    version: str
    category: str
    iconKey: str
    summary: str
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    inputPortSpecs: dict[str, object]
    outputPortSpecs: dict[str, object]
    paramSchema: dict[str, object]
    editorSpec: dict[str, object] = field(default_factory=dict)
    editorIssues: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class PreviewSource:
    sourceId: str
    label: str
    sourceKind: str
    workflowId: str
    nodeId: str
    port: str
    width: int
    height: int
    mimeType: str
    iterationPath: tuple[int, ...] = ()


@dataclass(frozen=True)
class WorkflowInfo:
    workflowId: str
    name: str
    isEntry: bool
    inputs: dict[str, object]
    outputs: dict[str, object]


@dataclass(frozen=True)
class GlobalCounterInfo:
    name: str
    value: int
    updatedAtMs: int


class RuntimeClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RuntimeServiceProtocol(Protocol):
    def ListOperators(self, request, context): ...

    def ListRejectedOperators(self, request, context): ...

    def GetOperatorEditorAsset(self, request, context): ...

    def ListNodePreviewSources(self, request, context): ...

    def UploadPreviewImage(self, request, context): ...

    def StreamPreviewAsset(self, request, context): ...

    def RunOperatorPreview(self, request, context): ...

    def CancelOperatorPreview(self, request, context): ...

    def OpenOperatorPreviewSession(self, request, context): ...

    def StreamOperatorPreviewFrames(self, request, context): ...

    def CloseOperatorPreviewSession(self, request, context): ...

    def ListWorkflows(self, request, context): ...

    def ListGlobalCounters(self, request, context): ...

    def GetGlobalCounter(self, request, context): ...

    def SetGlobalCounter(self, request, context): ...

    def ResetGlobalCounter(self, request, context): ...

    def LoadProject(self, request, context): ...

    def ValidateProject(self, request, context): ...

    def StartJob(self, request, context): ...

    def StopJob(self, request, context): ...

    def GetJobStatus(self, request, context): ...

    def StreamJobEvents(self, request, context) -> Iterable[object]: ...


class RuntimeEventStream:
    """Lazy DTO iterator over a unary-stream RPC call.

    The gRPC call is intentionally kept alive so a consumer can cancel a
    long-lived follow subscription without waiting for the server to finish.
    """

    def __init__(self, call: object, converter, onClose) -> None:
        self._call = call
        self._iterator = iter(call) if isinstance(call, IterableABC) else iter(())
        self._converter = converter
        self._onClose = onClose
        self._closed = False

    def __iter__(self):
        return self

    def __next__(self) -> RuntimeEventDTO:
        if self._closed:
            raise StopIteration
        try:
            event = next(self._iterator)
        except StopIteration:
            self.close()
            raise
        except BaseException:
            self.close()
            raise
        return self._converter(event)

    def cancel(self) -> None:
        cancel = getattr(self._call, "cancel", None)
        if callable(cancel):
            cancel()
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._call, "close", None)
        if callable(close):
            close()
        self._onClose(self)


class RuntimeClient:
    def __init__(
        self,
        runtimeService: RuntimeServiceProtocol,
        deadlineMs: int = 10000,
        ownedRuntimeService: object | None = None,
        ownedChannel: object | None = None,
    ) -> None:
        self.runtimeService = runtimeService
        self.deadlineMs = deadlineMs
        self._ownedRuntimeService = ownedRuntimeService
        self._ownedChannel = ownedChannel
        self._streamLock = threading.RLock()
        self._activeStreams: dict[str, RuntimeEventStream] = {}
        self._closed = False

    def listOperators(self) -> list[OperatorDefinition]:
        reply = self._call("ListOperators", runtime_pb2.ListOperatorsRequest())
        parsed: list[OperatorDefinition] = []
        for operatorInfo in getattr(reply, "operators", []):
            rawCategory = str(getattr(operatorInfo, "category", "Other")).strip()
            inputPorts = self._toStrMap(
                getattr(operatorInfo, "input_ports", {})
            )
            outputPorts = self._toStrMap(
                getattr(operatorInfo, "output_ports", {})
            )
            parsed.append(
                OperatorDefinition(
                    operatorId=str(getattr(operatorInfo, "operator_id", "")),
                    displayName=str(getattr(operatorInfo, "display_name", "")),
                    version=str(getattr(operatorInfo, "version", "")),
                    category=rawCategory or "Other",
                    iconKey=str(getattr(operatorInfo, "icon_key", "default")),
                    summary=str(getattr(operatorInfo, "summary", "")),
                    inputPorts=inputPorts,
                    outputPorts=outputPorts,
                    inputPortSpecs=self._parsePortSpecs(
                        getattr(operatorInfo, "input_port_specs_json", ""),
                        inputPorts,
                    ),
                    outputPortSpecs=self._parsePortSpecs(
                        getattr(operatorInfo, "output_port_specs_json", ""),
                        outputPorts,
                    ),
                    paramSchema=self._parseSchema(
                        getattr(operatorInfo, "param_schema_json", "{}")
                    ),
                    editorSpec=self._parseSchema(
                        getattr(operatorInfo, "editor_spec_json", "{}")
                    ),
                    editorIssues=tuple(
                        item
                        for item in self._parseJsonList(
                            getattr(operatorInfo, "editor_issues_json", "[]")
                        )
                        if isinstance(item, dict)
                    ),
                )
            )
        return parsed

    def getOperatorEditorAsset(self, operatorId: str, version: str = "") -> object:
        return self._call(
            "GetOperatorEditorAsset",
            runtime_pb2.GetOperatorEditorAssetRequest(
                operator_id=operatorId, version=version
            ),
        )

    def listNodePreviewSources(
        self,
        projectId: str,
        workflowId: str,
        nodeId: str,
    ) -> list[PreviewSource]:
        reply = self._call(
            "ListNodePreviewSources",
            runtime_pb2.ListNodePreviewSourcesRequest(
                project_id=projectId,
                workflow_id=workflowId,
                node_id=nodeId,
            ),
        )
        return [
            PreviewSource(
                sourceId=str(getattr(source, "source_id", "")),
                label=str(getattr(source, "label", "")),
                sourceKind=str(getattr(source, "source_kind", "")),
                workflowId=str(getattr(source, "workflow_id", "")),
                nodeId=str(getattr(source, "node_id", "")),
                port=str(getattr(source, "port", "")),
                width=int(getattr(source, "width", 0)),
                height=int(getattr(source, "height", 0)),
                mimeType=str(getattr(source, "mime_type", "")),
                iterationPath=self._parseIterationPath(
                    getattr(source, "iteration_path_json", "[]")
                ),
            )
            for source in getattr(reply, "sources", [])
        ]

    def uploadPreviewImage(
        self,
        data: bytes,
        filename: str = "",
        projectId: str = "",
    ) -> object:
        uploadId = f"upload-{threading.get_ident()}"

        def chunks():
            for offset in range(0, len(data), 256 * 1024):
                yield runtime_pb2.PreviewUploadChunk(
                    upload_id=uploadId,
                    filename=filename if offset == 0 else "",
                    content=data[offset : offset + 256 * 1024],
                    project_id=projectId,
                )

        return self._call("UploadPreviewImage", chunks())

    def downloadPreviewAsset(
        self,
        assetId: str,
        projectId: str = "",
    ) -> tuple[bytes, str]:
        chunks = self._call(
            "StreamPreviewAsset",
            runtime_pb2.GetPreviewAssetRequest(
                asset_id=assetId,
                project_id=projectId,
            ),
            useDeadline=False,
        )
        content: list[bytes] = []
        mimeType = ""
        for chunk in chunks if isinstance(chunks, IterableABC) else ():
            content.append(bytes(getattr(chunk, "content", b"")))
            if not mimeType:
                mimeType = str(getattr(chunk, "mime_type", ""))
        return b"".join(content), mimeType

    def runOperatorPreview(
        self,
        projectId: str,
        workflowId: str,
        nodeId: str,
        operatorId: str,
        params: dict[str, object],
        imageAssetId: str,
        requestId: str = "",
    ) -> object:
        return self._call(
            "RunOperatorPreview",
            runtime_pb2.RunOperatorPreviewRequest(
                project_id=projectId,
                workflow_id=workflowId,
                node_id=nodeId,
                operator_id=operatorId,
                params_json=json.dumps(params, ensure_ascii=True),
                image_asset_id=imageAssetId,
                request_id=requestId,
            ),
        )

    def cancelOperatorPreview(self, requestId: str) -> object:
        return self._call(
            "CancelOperatorPreview",
            runtime_pb2.CancelOperatorPreviewRequest(request_id=requestId),
        )

    def openOperatorPreviewSession(
        self,
        projectId: str,
        workflowId: str,
        nodeId: str,
        operatorId: str,
        params: dict[str, object],
    ) -> object:
        return self._call(
            "OpenOperatorPreviewSession",
            runtime_pb2.OpenOperatorPreviewSessionRequest(
                project_id=projectId,
                workflow_id=workflowId,
                node_id=nodeId,
                operator_id=operatorId,
                params_json=json.dumps(params, ensure_ascii=True),
            ),
        )

    def streamOperatorPreviewFrames(self, sessionId: str) -> Iterable[object]:
        stream = self._call(
            "StreamOperatorPreviewFrames",
            runtime_pb2.StreamOperatorPreviewFramesRequest(session_id=sessionId),
            useDeadline=False,
        )
        return stream if isinstance(stream, IterableABC) else ()

    def closeOperatorPreviewSession(self, sessionId: str) -> object:
        return self._call(
            "CloseOperatorPreviewSession",
            runtime_pb2.CloseOperatorPreviewSessionRequest(session_id=sessionId),
        )

    def listRejectedOperators(self) -> list[object]:
        reply = self._call("ListRejectedOperators", runtime_pb2.ListRejectedOperatorsRequest())
        return list(getattr(reply, "rejected", []))

    def listWorkflows(self, projectId: str = "") -> list[WorkflowInfo]:
        reply = self._call(
            "ListWorkflows", runtime_pb2.ListWorkflowsRequest(project_id=projectId)
        )
        result: list[WorkflowInfo] = []
        for workflow in getattr(reply, "workflows", []):
            result.append(
                WorkflowInfo(
                    workflowId=str(getattr(workflow, "workflow_id", "")),
                    name=str(getattr(workflow, "name", "")),
                    isEntry=bool(getattr(workflow, "is_entry", False)),
                    inputs=self._parseSchema(getattr(workflow, "inputs_json", "{}")),
                    outputs=self._parseSchema(getattr(workflow, "outputs_json", "{}")),
                )
            )
        return result

    def listGlobalCounters(self, projectId: str) -> list[GlobalCounterInfo]:
        reply = self._call(
            "ListGlobalCounters",
            runtime_pb2.ListGlobalCountersRequest(project_id=projectId),
        )
        self._raiseGlobalCounterReply(reply)
        return [
            self._toGlobalCounterInfo(counter)
            for counter in getattr(reply, "counters", [])
        ]

    def getGlobalCounter(self, projectId: str, name: str) -> GlobalCounterInfo:
        reply = self._call(
            "GetGlobalCounter",
            runtime_pb2.GetGlobalCounterRequest(project_id=projectId, name=name),
        )
        self._raiseGlobalCounterReply(reply)
        return self._toGlobalCounterInfo(getattr(reply, "counter", None))

    def setGlobalCounter(
        self,
        projectId: str,
        name: str,
        value: int,
    ) -> GlobalCounterInfo:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > MAX_GLOBAL_COUNTER_VALUE
        ):
            raise RuntimeClientError(
                "E_COUNTER_VALUE_RANGE",
                f"counter value must be an integer between 0 and {MAX_GLOBAL_COUNTER_VALUE}",
            )
        reply = self._call(
            "SetGlobalCounter",
            runtime_pb2.SetGlobalCounterRequest(
                project_id=projectId,
                name=name,
                value=value,
            ),
        )
        self._raiseGlobalCounterReply(reply)
        return self._toGlobalCounterInfo(getattr(reply, "counter", None))

    def resetGlobalCounter(self, projectId: str, name: str) -> GlobalCounterInfo:
        reply = self._call(
            "ResetGlobalCounter",
            runtime_pb2.ResetGlobalCounterRequest(project_id=projectId, name=name),
        )
        self._raiseGlobalCounterReply(reply)
        return self._toGlobalCounterInfo(getattr(reply, "counter", None))

    def loadProject(self, projectPath: str) -> object:
        return self._call(
            "LoadProject", runtime_pb2.LoadProjectRequest(project_path=projectPath)
        )

    def validateProject(self, projectId: str) -> object:
        return self._call(
            "ValidateProject", runtime_pb2.ValidateProjectRequest(project_id=projectId)
        )

    def startJob(
        self,
        projectId: str,
        workflowId: str = "",
        inputs: dict[str, object] | str | None = None,
    ) -> object:
        if isinstance(inputs, str):
            inputsJson = inputs
        else:
            inputsJson = json.dumps(inputs or {}, ensure_ascii=True)
        request = runtime_pb2.StartJobRequest(
            project_id=projectId, workflow_id=workflowId, inputs_json=inputsJson
        )
        return self._call("StartJob", request)

    def stopJob(self, jobId: str, mode: str = "graceful") -> object:
        request = runtime_pb2.StopJobRequest(job_id=jobId, mode=mode)
        return self._call("StopJob", request)

    def getJobStatus(self, jobId: str) -> object:
        return self._call("GetJobStatus", runtime_pb2.GetJobStatusRequest(job_id=jobId))

    def streamJobEvents(
        self, jobId: str, afterSequence: int = 0, follow: bool = False
    ) -> Iterable[RuntimeEventDTO]:
        stream = self.iterJobEvents(jobId, afterSequence, follow)
        return stream if follow else list(stream)

    def iterJobEvents(
        self, jobId: str, afterSequence: int = 0, follow: bool = False
    ) -> Iterable[RuntimeEventDTO]:
        """Yield event DTOs without materializing a live subscription."""
        request = runtime_pb2.StreamJobEventsRequest(
            job_id=jobId, after_sequence=afterSequence, follow=follow
        )
        events = self._call("StreamJobEvents", request, useDeadline=False)
        if not isinstance(events, IterableABC):
            return iter(())
        if not follow:
            return (self._toEventDTO(event) for event in events)
        with self._streamLock:
            previous = self._activeStreams.pop(jobId, None)
            if previous is not None:
                previous.cancel()
            stream = RuntimeEventStream(
                events,
                self._toEventDTO,
                lambda value: self._removeActiveStream(jobId, value),
            )
            self._activeStreams[jobId] = stream
        return stream

    def cancelEventStream(self, jobId: str) -> bool:
        with self._streamLock:
            stream = self._activeStreams.pop(jobId, None)
        if stream is None:
            return False
        stream.cancel()
        return True

    def close(self) -> None:
        with self._streamLock:
            if self._closed:
                return
            self._closed = True
            streams = list(self._activeStreams.values())
            self._activeStreams.clear()
        for stream in streams:
            stream.cancel()
        self._closeOwned(self._ownedRuntimeService)
        self._closeOwned(self._ownedChannel)

    def _closeOwned(self, resource: object | None) -> None:
        close = getattr(resource, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:
            return

    def _removeActiveStream(self, jobId: str, stream: RuntimeEventStream) -> None:
        with self._streamLock:
            if self._activeStreams.get(jobId) is stream:
                self._activeStreams.pop(jobId, None)

    def _call(self, methodName: str, request: object, useDeadline: bool = True):
        method = getattr(self.runtimeService, methodName)
        try:
            if useDeadline and self.deadlineMs > 0:
                parameters = _signatureParameters(method)
                if not parameters or "timeout" in parameters or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters.values()
                ):
                    return method(request, timeout=self.deadlineMs / 1000.0)
            return method(request, None)
        except Exception as err:
            if grpc is not None and isinstance(err, grpc.RpcError):
                code = err.code()
                detail = err.details() or "runtime RPC failed"
                raise RuntimeClientError(str(code), detail) from err
            raise

    def _toEventDTO(self, event: object) -> RuntimeEventDTO:
        payloadJson = getattr(event, "payload_json", "{}")
        payload: dict[str, object] = {}
        if isinstance(payloadJson, str) and payloadJson.strip():
            try:
                parsedPayload = json.loads(payloadJson)
                if isinstance(parsedPayload, dict):
                    payload = parsedPayload
            except json.JSONDecodeError:
                payload = {}
        iterationPath = self._parseIterationPath(
            getattr(event, "iteration_path_json", "[]")
        )
        return RuntimeEventDTO(
            jobId=str(getattr(event, "job_id", "")),
            eventType=str(getattr(event, "event_type", "")),
            message=str(getattr(event, "message", "")),
            level=str(getattr(event, "level", "INFO")),
            nodeId=str(getattr(event, "node_id", "")),
            code=str(getattr(event, "code", "")),
            payload=payload,
            sequence=int(getattr(event, "sequence", 0)),
            timestampMs=int(getattr(event, "timestamp_ms", 0)),
            projectId=str(getattr(event, "project_id", "")),
            workflowId=str(getattr(event, "workflow_id", "")),
            workflowRunId=str(getattr(event, "workflow_run_id", "")),
            parentWorkflowRunId=str(getattr(event, "parent_workflow_run_id", "")),
            nodeRunId=str(getattr(event, "node_run_id", "")),
            iterationPath=iterationPath,
        )

    def _raiseGlobalCounterReply(self, reply: object) -> None:
        if bool(getattr(reply, "ok", False)):
            return
        raise RuntimeClientError(
            str(getattr(reply, "code", "E_RUNTIME_STATE_UNAVAILABLE")),
            str(getattr(reply, "message", "global counter request failed")),
        )

    def _toGlobalCounterInfo(self, counter: object) -> GlobalCounterInfo:
        return GlobalCounterInfo(
            name=str(getattr(counter, "name", "")),
            value=int(getattr(counter, "value", 0)),
            updatedAtMs=int(getattr(counter, "updated_at_ms", 0)),
        )

    def _toStrMap(self, rawValue: object) -> dict[str, str]:
        mapping = self._toDict(rawValue)
        if mapping is None:
            return {}
        return {
            key: value
            for key, value in mapping.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def _toDict(self, rawValue: object) -> dict[object, object] | None:
        if isinstance(rawValue, dict):
            return rawValue
        itemsMethod = getattr(rawValue, "items", None)
        if not callable(itemsMethod):
            return None
        try:
            items = itemsMethod()
            if not isinstance(items, IterableABC):
                return None
            converted: dict[object, object] = {}
            for item in cast(Iterable[object], items):
                if not isinstance(item, tuple) or len(item) != 2:
                    return None
                converted[item[0]] = item[1]
            return converted
        except Exception:
            return None

    def _parseSchema(self, rawValue: object) -> dict[str, object]:
        if not isinstance(rawValue, str) or rawValue.strip() == "":
            return {}
        try:
            parsed = json.loads(rawValue)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _parseJsonList(self, rawValue: object) -> list[object]:
        if not isinstance(rawValue, str) or not rawValue.strip():
            return []
        try:
            parsed = json.loads(rawValue)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def _parsePortSpecs(
        self,
        rawValue: object,
        fallback: dict[str, str],
    ) -> dict[str, object]:
        parsed = self._parseSchema(rawValue)
        if not parsed:
            return dict(fallback)
        result: dict[str, object] = {}
        for name, spec in parsed.items():
            try:
                result[name] = validatePortSpec(spec, f"ports.{name}")
            except PortSpecValidationError:
                return dict(fallback)
        return result

    def _parseIterationPath(self, rawValue: object) -> tuple[int, ...]:
        if not isinstance(rawValue, str) or not rawValue.strip():
            return ()
        try:
            parsed = json.loads(rawValue)
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed, list):
            return ()
        return tuple(item for item in parsed if isinstance(item, int) and not isinstance(item, bool))


def _signatureParameters(method) -> dict[str, inspect.Parameter]:
    try:
        return dict(inspect.signature(method).parameters)
    except (TypeError, ValueError):
        return {}
