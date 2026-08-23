from typing import Iterable, Protocol

from emo_master.apps.runtime.open_api.routes import listRoutes
from emo_master.apps.runtime.open_api.ws_hub import WsHub


class RuntimeApi(Protocol):
  def StartJob(self, request, context) -> object:
    ...

  def StopJob(self, request, context) -> object:
    ...

  def GetJobStatus(self, request, context) -> object:
    ...

  def StreamJobEvents(self, request, context):
    ...


def createOpenApiApp(runtimeService: RuntimeApi):
  try:
    from fastapi import FastAPI, WebSocket
  except Exception as err:  # pragma: no cover
    raise RuntimeError("fastapi is required for open api server") from err

  app = FastAPI(title="VisionX Runtime Open API")
  wsHub = WsHub()

  @app.get("/health")
  def getHealth() -> dict[str, str]:
    return {"status": "ok"}

  @app.post("/jobs/start")
  def postStartJob(projectId: str) -> dict[str, object]:
    request = type("StartJobRequest", (), {"project_id": projectId})()
    reply = runtimeService.StartJob(request, None)
    return {
      "ok": getattr(reply, "ok", False),
      "jobId": getattr(reply, "job_id", ""),
      "message": getattr(reply, "message", "")
    }

  @app.post("/jobs/stop")
  def postStopJob(jobId: str, mode: str = "graceful") -> dict[str, object]:
    request = type("StopJobRequest", (), {"job_id": jobId, "mode": mode})()
    reply = runtimeService.StopJob(request, None)
    return {
      "ok": getattr(reply, "ok", False),
      "status": getattr(reply, "status", "UNKNOWN"),
      "message": getattr(reply, "message", "")
    }

  @app.get("/jobs/{jobId}")
  def getJobStatus(jobId: str) -> dict[str, object]:
    request = type("GetJobStatusRequest", (), {"job_id": jobId})()
    reply = runtimeService.GetJobStatus(request, None)
    return {
      "ok": getattr(reply, "ok", False),
      "status": getattr(reply, "status", "UNKNOWN"),
      "message": getattr(reply, "message", "")
    }

  @app.websocket("/events")
  async def wsEvents(websocket: WebSocket) -> None:
    await websocket.accept()
    payload = await websocket.receive_json()
    jobId = payload.get("jobId", "") if isinstance(payload, dict) else ""
    if isinstance(jobId, str) and jobId != "":
      request = type("StreamJobEventsRequest", (), {"job_id": jobId})()
      stream = runtimeService.StreamJobEvents(request, None)
      events = stream if isinstance(stream, Iterable) else []
      for event in events:
        eventPayload: dict[str, object] = {
          "jobId": str(getattr(event, "job_id", "")),
          "eventType": str(getattr(event, "event_type", "")),
          "message": str(getattr(event, "message", "")),
          "level": str(getattr(event, "level", "INFO"))
        }
        wsHub.publish(str(eventPayload["eventType"]), eventPayload)
        await websocket.send_json(eventPayload)
    else:
      await websocket.send_json({"eventType": "runtime.heartbeat"})
    await websocket.close()

  _ = listRoutes
  return app
