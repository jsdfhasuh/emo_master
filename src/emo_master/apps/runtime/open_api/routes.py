def listRoutes() -> list[str]:
  return [
    "/health",
    "/jobs/start",
    "/jobs/stop",
    "/jobs/{jobId}",
    "/events"
  ]
