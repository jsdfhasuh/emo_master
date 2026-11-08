"""Preview failure diagnostics retain the owning Job after a project change."""
import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.models import ProjectDocument


@pytest.mark.parametrize("failure,code", [
    ("inspection", "E_INSPECTION_TAKEOVER"),
    ("promotion", "E_PREVIEW_SNAPSHOT_FAILED"),
])
def testPreviewFailureEventsRetainOwningJobIdentity(tmp_path, monkeypatch, failure, code):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        job = service.jobManager.createJob("original-project", 1, "producer")
        service.jobRepository.update(job.jobId, status="COMPLETED")
        payload = WorkflowStore().toPayload()
        payload["project"]["projectId"] = "later-loaded-project"
        service.loadedDocument = ProjectDocument.model_validate(payload)
        assert service.loadedDocument.schemaVersion == "2.9"
        assert service.loadedDocument.presentation.schemaVersion == "1.2"
        assert service.loadedDocument.project.projectId != job.projectId
        assert service.loadedDocument.entryWorkflowId != job.workflowId

        def fail(*_args, **_kwargs):
            raise OSError("controlled preview failure")

        if failure == "inspection":
            monkeypatch.setattr(service.runInspectionStore, "enqueue", fail)
            service._onInspectionSnapshot(job.jobId, object())
        else:
            monkeypatch.setattr(service.previewAssetStore, "promote", fail)
            service._jobPreviewKeys[job.jobId] = "original-preview-key"
            service._onJobTerminal(job.jobId, "COMPLETED")

        for events in (service.eventStore.read(job.jobId),
                       service.eventStore.readFromPersistence(job.jobId)):
            event, = [event for event in events if event.eventType == "preview.snapshot.failed"]
            assert (event.jobId, event.projectId, event.workflowId) == (
                job.jobId, "original-project", "producer")
            assert (event.level, event.code, event.message) == (
                "WARN", code, "controlled preview failure")
        assert service.jobRepository.get(job.jobId).status == "COMPLETED"
    finally:
        service.close()
