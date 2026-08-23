from emo_master.apps.runtime.events.event_bus import RuntimeEventBus


class WsHub:
  def __init__(self, eventBus: RuntimeEventBus | None = None) -> None:
    self.eventBus = eventBus
    self.messages: list[dict[str, object]] = []

  def publish(self, eventType: str, payload: dict[str, object]) -> None:
    self.messages.append({"eventType": eventType, "payload": payload})

  def readJobEvents(self, jobId: str) -> list[dict[str, object]]:
    if self.eventBus is None:
      return []
    events = self.eventBus.read(jobId)
    return [
      {
        "jobId": event.jobId,
        "eventType": event.eventType,
        "message": event.message,
        "level": event.level
      }
      for event in events
    ]
