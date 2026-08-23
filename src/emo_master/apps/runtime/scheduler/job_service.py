from dataclasses import dataclass

from emo_master.apps.runtime.scheduler.state_machine import RuntimeState, transitionState


@dataclass
class JobState:
  jobId: str
  state: RuntimeState
  errorCode: str | None = None


class JobService:
  def __init__(self) -> None:
    self.jobs: dict[str, JobState] = {}

  def createJob(self, jobId: str) -> JobState:
    jobState = JobState(jobId=jobId, state=RuntimeState.IDLE)
    self.jobs[jobId] = jobState
    return jobState

  def applyEvent(self, jobId: str, eventName: str) -> JobState:
    if jobId not in self.jobs:
      self.createJob(jobId)
    current = self.jobs[jobId]
    nextState = transitionState(current.state, eventName)
    current.state = nextState
    return current

  def getJob(self, jobId: str) -> JobState | None:
    return self.jobs.get(jobId)
