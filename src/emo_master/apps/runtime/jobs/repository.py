from __future__ import annotations

from datetime import datetime
import threading

from emo_master.apps.runtime.jobs.models import JobRecord, JobStatus


class JobRepository:
    def __init__(self, persistence: object | None = None) -> None:
        self.persistence = persistence
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def create(self, record: JobRecord) -> JobRecord:
        with self._lock:
            if record.jobId in self._jobs:
                raise ValueError(f"duplicate job id: {record.jobId}")
            self._jobs[record.jobId] = record
            if self.persistence is not None:
                insert = getattr(self.persistence, "insertJob", None)
                if callable(insert):
                    insert(
                        jobId=record.jobId,
                        projectId=record.projectId,
                        status=record.status,
                        projectRevision=record.projectRevision,
                        workflowId=record.workflowId,
                        acceptedAtMs=record.acceptedAtMs,
                    )
            return record

    def get(self, jobId: str) -> JobRecord | None:
        with self._lock:
            record = self._jobs.get(jobId)
            if record is not None:
                return record
        if self.persistence is None:
            return None
        loaded = getattr(self.persistence, "getJob", lambda _jobId: None)(jobId)
        if not isinstance(loaded, dict):
            return None
        return JobRecord(
            jobId=str(loaded.get("jobId", jobId)),
            projectId=str(loaded.get("projectId", "")),
            projectRevision=int(loaded.get("projectRevision", 1) or 1),
            workflowId=str(loaded.get("workflowId", "")),
            status=str(loaded.get("status", JobStatus.FAILED.value)),
            pid=int(loaded["pid"]) if isinstance(loaded.get("pid"), int) else None,
            acceptedAtMs=_toMs(loaded.get("acceptedAt")),
            startedAtMs=_toMs(loaded.get("startedAt")),
            endedAtMs=_toMs(loaded.get("endAt")),
            errorCode=str(loaded.get("errorCode", "") or ""),
            message=str(loaded.get("errorMessage", "") or ""),
            stopMode=str(loaded.get("stopMode", "") or ""),
        )

    def update(self, jobId: str, **changes: object) -> JobRecord | None:
        with self._lock:
            record = self._jobs.get(jobId)
            if record is None:
                return None
            for key, value in changes.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            if self.persistence is not None:
                update = getattr(self.persistence, "updateJobStatus", None)
                if callable(update) and "status" in changes:
                    update(
                        jobId=jobId,
                        status=record.status,
                        errorCode=record.errorCode or None,
                        errorMessage=record.message or None,
                        endedAtMs=record.endedAtMs or None,
                        startedAtMs=record.startedAtMs or None,
                        pid=record.pid,
                        stopMode=record.stopMode or None,
                    )
            return record

    def markOrphanedJobsFailed(self) -> int:
        count = 0
        if self.persistence is not None:
            marker = getattr(self.persistence, "markOrphanedJobsFailed", None)
            if callable(marker):
                count = int(marker())
        with self._lock:
            for record in self._jobs.values():
                if record.status in {
                    JobStatus.ACCEPTED.value,
                    JobStatus.STARTING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.STOPPING.value,
                }:
                    record.status = JobStatus.FAILED.value
                    record.errorCode = "E_RUNTIME_RESTARTED"
                    record.message = "runtime restarted while job was active"
                    count += 1
        return count

    def all(self) -> list[JobRecord]:
        with self._lock:
            return list(self._jobs.values())


def _toMs(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str) or value == "":
        return 0
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000.0)
    except (TypeError, ValueError, OSError):
        return 0
