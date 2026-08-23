from enum import Enum


class RuntimeState(str, Enum):
  IDLE = "IDLE"
  READY = "READY"
  RUNNING = "RUNNING"
  PAUSED = "PAUSED"
  STOPPING = "STOPPING"
  COMPLETED = "COMPLETED"
  FAILED = "FAILED"
  ABORTED = "ABORTED"


stateTransitions: dict[RuntimeState, dict[str, RuntimeState]] = {
  RuntimeState.IDLE: {
    "load": RuntimeState.READY
  },
  RuntimeState.READY: {
    "start": RuntimeState.RUNNING,
    "reset": RuntimeState.IDLE
  },
  RuntimeState.RUNNING: {
    "pause": RuntimeState.PAUSED,
    "stop": RuntimeState.STOPPING,
    "complete": RuntimeState.COMPLETED,
    "fail": RuntimeState.FAILED
  },
  RuntimeState.PAUSED: {
    "resume": RuntimeState.RUNNING,
    "stop": RuntimeState.STOPPING,
    "fail": RuntimeState.FAILED
  },
  RuntimeState.STOPPING: {
    "abort": RuntimeState.ABORTED,
    "fail": RuntimeState.FAILED
  },
  RuntimeState.COMPLETED: {
    "reset": RuntimeState.IDLE
  },
  RuntimeState.FAILED: {
    "reset": RuntimeState.IDLE
  },
  RuntimeState.ABORTED: {
    "reset": RuntimeState.IDLE
  }
}


def transitionState(currentState: RuntimeState, eventName: str) -> RuntimeState:
  allowedEvents = stateTransitions.get(currentState, {})
  if eventName not in allowedEvents:
    raise ValueError(f"invalid transition: {currentState} + {eventName}")
  return allowedEvents[eventName]
