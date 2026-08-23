from emo_master.apps.runtime.open_api.routes import listRoutes
from emo_master.apps.runtime.open_api.ws_hub import WsHub
from emo_master.apps.runtime.events.event_bus import RuntimeEventBus


def testOpenApiServerDefinesHealthRoute() -> None:
  routes = listRoutes()
  assert "/health" in routes


def testWsHubCanBridgeRuntimeEvents() -> None:
  eventBus = RuntimeEventBus()
  eventBus.publish("j1", "job.completed", "done")
  hub = WsHub(eventBus=eventBus)
  events = hub.readJobEvents("j1")
  assert len(events) == 1
  assert events[0]["eventType"] == "job.completed"
