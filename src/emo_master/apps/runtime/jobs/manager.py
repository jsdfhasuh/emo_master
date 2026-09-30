from __future__ import annotations

from uuid import uuid4

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.jobs.models import JobProcessSpec, JobRecord
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
from emo_master.core.contracts.legacy_snapshots import normalizeLegacySnapshotPolicy


class JobManager:
    def __init__(
        self,
        jobRepository: JobRepository,
        eventStore: EventStore,
        supervisor: JobSupervisor,
    ) -> None:
        self.jobRepository = jobRepository
        self.eventStore = eventStore
        self.supervisor = supervisor

    def createJob(
        self,
        projectId: str,
        projectRevision: int,
        workflowId: str,
        jobId: str | None = None,
        *,
        legacySnapshotPolicy: str = "ALL",
        previewProjectKey: str = "",
    ) -> JobRecord:
        policy = normalizeLegacySnapshotPolicy(legacySnapshotPolicy)
        record = JobRecord(
            jobId=jobId or str(uuid4()),
            projectId=projectId,
            projectRevision=projectRevision,
            workflowId=workflowId,
            legacySnapshotPolicy=policy,
            previewProjectKey=previewProjectKey,
        )
        self.jobRepository.create(record)
        self.eventStore.append(
            record.jobId,
            "job.accepted",
            "job accepted",
            projectId=projectId,
            workflowId=workflowId,
            payload={"legacySnapshotPolicy": policy, "previewProjectKey": previewProjectKey},
        )
        return record

    def start(self, record: JobRecord, spec: JobProcessSpec) -> JobRecord:
        self.supervisor.startJob(spec)
        return record

    def startJob(self, record: JobRecord, spec: JobProcessSpec) -> JobRecord:
        return self.start(record, spec)

    def stop(self, jobId: str, mode: str = "graceful") -> str:
        return self.supervisor.stopJob(jobId, mode)

    def getJob(self, jobId: str) -> JobRecord | None:
        return self.jobRepository.get(jobId)
