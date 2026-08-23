import json
from dataclasses import dataclass
from collections.abc import Iterable as IterableABC
from typing import Iterable, Protocol, cast


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


class RuntimeServiceProtocol(Protocol):
    def ListOperators(self, request, context): ...

    def ListRejectedOperators(self, request, context): ...

    def LoadProject(self, request, context): ...

    def ValidateProject(self, request, context): ...

    def StartJob(self, request, context): ...

    def StopJob(self, request, context): ...

    def GetJobStatus(self, request, context): ...

    def StreamJobEvents(self, request, context) -> Iterable[object]: ...


class RuntimeClient:
    def __init__(self, runtimeService: RuntimeServiceProtocol) -> None:
        self.runtimeService = runtimeService

    def listOperators(self) -> list[OperatorDefinition]:
        request = type("ListOperatorsRequest", (), {})()
        reply = self.runtimeService.ListOperators(request, None)
        operators = getattr(reply, "operators", [])
        parsed: list[OperatorDefinition] = []
        for operatorInfo in operators:
            rawCategory = str(getattr(operatorInfo, "category", "其他")).strip()
            category = rawCategory if rawCategory != "" else "其他"
            parsed.append(
                OperatorDefinition(
                    operatorId=str(getattr(operatorInfo, "operator_id", "")),
                    displayName=str(getattr(operatorInfo, "display_name", "")),
                    version=str(getattr(operatorInfo, "version", "")),
                    category=category,
                    iconKey=str(getattr(operatorInfo, "icon_key", "default")),
                    summary=str(getattr(operatorInfo, "summary", "")),
                    inputPorts=self._toStrMap(getattr(operatorInfo, "input_ports", {})),
                    outputPorts=self._toStrMap(
                        getattr(operatorInfo, "output_ports", {})
                    ),
                    paramSchema=self._parseSchema(
                        getattr(operatorInfo, "param_schema_json", "{}")
                    ),
                )
            )
        return parsed

    def listRejectedOperators(self) -> list[object]:
        request = type("ListRejectedOperatorsRequest", (), {})()
        reply = self.runtimeService.ListRejectedOperators(request, None)
        rejected = getattr(reply, "rejected", [])
        return list(rejected)

    def loadProject(self, projectPath: str) -> object:
        request = type("LoadProjectRequest", (), {"project_path": projectPath})()
        return self.runtimeService.LoadProject(request, None)

    def validateProject(self, projectId: str) -> object:
        request = type("ValidateProjectRequest", (), {"project_id": projectId})()
        return self.runtimeService.ValidateProject(request, None)

    def startJob(self, projectId: str) -> object:
        request = type("StartJobRequest", (), {"project_id": projectId})()
        return self.runtimeService.StartJob(request, None)

    def stopJob(self, jobId: str, mode: str = "graceful") -> object:
        request = type("StopJobRequest", (), {"job_id": jobId, "mode": mode})()
        return self.runtimeService.StopJob(request, None)

    def getJobStatus(self, jobId: str) -> object:
        request = type("GetJobStatusRequest", (), {"job_id": jobId})()
        return self.runtimeService.GetJobStatus(request, None)

    def streamJobEvents(self, jobId: str) -> list[object]:
        request = type("StreamJobEventsRequest", (), {"job_id": jobId})()
        events = self.runtimeService.StreamJobEvents(request, None)
        if not isinstance(events, Iterable):
            return []
        parsedEvents: list[object] = []
        for event in events:
            payloadJson = getattr(event, "payload_json", "{}")
            payload: dict[str, object] = {}
            if isinstance(payloadJson, str) and payloadJson.strip() != "":
                try:
                    parsedPayload = json.loads(payloadJson)
                    if isinstance(parsedPayload, dict):
                        payload = parsedPayload
                except json.JSONDecodeError:
                    payload = {}
            setattr(event, "payload", payload)
            parsedEvents.append(event)
        return parsedEvents

    def _toStrMap(self, rawValue: object) -> dict[str, str]:
        mapping = self._toDict(rawValue)
        if mapping is None:
            return {}
        parsed: dict[str, str] = {}
        for key, value in mapping.items():
            if isinstance(key, str) and isinstance(value, str):
                parsed[key] = value
        return parsed

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
        if not isinstance(parsed, dict):
            return {}
        return parsed
