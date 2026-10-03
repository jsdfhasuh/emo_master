from __future__ import annotations

from emo_master.apps.runtime.events.event_store import EventStore


class RuntimeEventBus(EventStore):
    """Compatibility facade for the original in-memory event bus."""

    def publish(
        self,
        jobId: str,
        eventType: str,
        message: str,
        level: str = "INFO",
        nodeId: str = "",
        payloadJson: str = "{}",
    ):
        import json

        try:
            payload = json.loads(payloadJson) if payloadJson else {}
        except json.JSONDecodeError:
            payload = {}
        return self.append(
            jobId=jobId,
            eventType=eventType,
            message=message,
            level=level,
            nodeId=nodeId,
            payload=payload if isinstance(payload, dict) else {},
        )
