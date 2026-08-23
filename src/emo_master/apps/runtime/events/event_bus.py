from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeEvent:
    jobId: str
    eventType: str
    message: str
    level: str = "INFO"
    nodeId: str = ""
    payloadJson: str = "{}"


class RuntimeEventBus:
    def __init__(self) -> None:
        self._events: dict[str, list[RuntimeEvent]] = {}

    def publish(
        self,
        jobId: str,
        eventType: str,
        message: str,
        level: str = "INFO",
        nodeId: str = "",
        payloadJson: str = "{}",
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            jobId=jobId,
            eventType=eventType,
            message=message,
            level=level,
            nodeId=nodeId,
            payloadJson=payloadJson,
        )
        bucket = self._events.get(jobId)
        if bucket is None:
            bucket = []
            self._events[jobId] = bucket
        bucket.append(event)
        return event

    def read(self, jobId: str) -> list[RuntimeEvent]:
        return list(self._events.get(jobId, []))
