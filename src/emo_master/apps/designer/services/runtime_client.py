from __future__ import annotations

import json
from dataclasses import dataclass
from collections.abc import Iterable as IterableABC
import inspect
import threading
from typing import Any, Iterable, Protocol, cast

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _runtime_pb2
from emo_master.core.contracts.execution import RuntimeEventDTO

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
    paramSchema: dict[str, object]


@dataclass(frozen=True)
class WorkflowInfo:
    workflowId: str
    name: str
    isEntry: bool
    inputs: dict[str, object]
    outputs: dict[str, object]


class RuntimeClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RuntimeServiceProtocol(Protocol):
    def ListOperators(self, request, context): ...

    def ListRejectedOperators(self, request, context): ...

    def ListWorkflows(self, request, context): ...

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
            parsed.append(
                OperatorDefinition(
                    operatorId=str(getattr(operatorInfo, "operator_id", "")),
                    displayName=str(getattr(operatorInfo, "display_name", "")),
                    version=str(getattr(operatorInfo, "version", "")),
                    category=rawCategory or "Other",
                    iconKey=str(getattr(operatorInfo, "icon_key", "default")),
                    summary=str(getattr(operatorInfo, "summary", "")),
                    inputPorts=self._toStrMap(getattr(operatorInfo, "input_ports", {})),
                    outputPorts=self._toStrMap(getattr(operatorInfo, "output_ports", {})),
                    paramSchema=self._parseSchema(
                        getattr(operatorInfo, "param_schema_json", "{}")
                    ),
                )
            )
        return parsed

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
