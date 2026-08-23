from emo_master.apps.runtime.events.event_bus import RuntimeEventBus


def testEventBusStoresAndReadsByJobId() -> None:
  bus = RuntimeEventBus()
  bus.publish("job-1", "job.started", "started")
  events = bus.read("job-1")
  assert len(events) == 1
  assert events[0].eventType == "job.started"
