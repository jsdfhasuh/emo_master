import pytest

from emo_master.apps.runtime.scheduler.state_machine import RuntimeState, transitionState


def testStateTransitionReadyToRunning() -> None:
  assert transitionState(RuntimeState.READY, "start") == RuntimeState.RUNNING


def testStateTransitionRunningToCompleted() -> None:
  assert transitionState(RuntimeState.RUNNING, "complete") == RuntimeState.COMPLETED


def testStateTransitionInvalidRaises() -> None:
  with pytest.raises(ValueError):
    transitionState(RuntimeState.IDLE, "complete")
