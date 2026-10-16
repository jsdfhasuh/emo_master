"""Explicit snapshot policy regressions; synthetic inputs, no devices/performance claims."""
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.manager import JobManager
from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.presentation.rpc import DisplayRpc
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.core.contracts.legacy_snapshots import normalizeLegacySnapshotPolicy
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_operator_editor_preview import _writePreviewProject


def previewRuntime(root):
    directory = root / "project"
    directory.mkdir()
    image = directory / "input.png"
    assert cv2.imwrite(str(image), np.full((16, 18, 3), 80, np.uint8))
    _writePreviewProject(directory, image)
    runtime = RuntimeService(dbPath=root / "runtime.db", workspaceRoot=root / "jobs")
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(directory)), None).ok
    # Persist the migrated document's identity, as Designer save does. Reopening
    # an unsaved v1 migration otherwise intentionally creates another UUID.
    (directory / "project.json").write_text(runtime.loadedDocument.model_dump_json(), encoding="utf-8")
    return runtime, directory, image


def request(runtime, policy="", token=None):
    return pb.StartJobRequest(project_id=runtime.loadedProjectId,
        legacy_snapshot_policy=policy, start_request_id=token or uuid4().hex,
        expected_runtime_instance_id=runtime.runtimeInstanceId)


def sources(runtime, job=""):
    return runtime.ListNodePreviewSources(pb.ListNodePreviewSourcesRequest(
        project_id=runtime.loadedProjectId, workflow_id="main", node_id="roi", job_id=job), None)


@pytest.mark.parametrize(("raw", "expected"), [("", "ALL"), ("ALL", "ALL"), ("NONE", "NONE")])
def testCanonicalPolicy(raw, expected):
    assert normalizeLegacySnapshotPolicy(raw) == expected
    assert JobProcessSpec("job", "project", "main").legacySnapshotPolicy == "ALL"


@pytest.mark.parametrize("raw", [None, False, 1, "all", "none", " ALL", "SELECTED", []])
def testUnknownPolicyIsNotSilentlyCoerced(raw):
    with pytest.raises(ValueError):
        normalizeLegacySnapshotPolicy(raw)


def testCanonicalFingerprintAndFrozenSpecRejectPolicyChange(tmp_path, monkeypatch):
    runtime, _, _ = previewRuntime(tmp_path)
    captured = []
    monkeypatch.setattr(runtime.jobManager, "start", lambda _record, spec: captured.append(spec))
    try:
        original = request(runtime)
        first = runtime.StartJob(original, None)
        assert first.ok and first.legacy_snapshot_policy == "ALL"
        explicit = request(runtime, "ALL", original.start_request_id)
        assert runtime.StartJob(explicit, None).job_id == first.job_id
        conflict = request(runtime, "NONE", original.start_request_id)
        assert runtime.StartJob(conflict, None).status == "REJECTED"
        assert len(captured) == 1 and captured[0].legacySnapshotPolicy == "ALL"
        nextRun = runtime.StartJob(request(runtime, "NONE"), None)
        assert nextRun.ok and captured[1].legacySnapshotPolicy == "NONE"
        assert runtime.GetJobStatus(pb.GetJobStatusRequest(job_id=nextRun.job_id), None).legacy_snapshot_policy == "NONE"
        lookup = runtime.GetStartRequest(pb.StartRequestLookup(start_request_id=original.start_request_id,
            runtime_instance_id=runtime.runtimeInstanceId), None)
        assert lookup.job_id == first.job_id and lookup.legacy_snapshot_policy == "ALL"
        snapshot = json.loads(Path(captured[1].projectSnapshotPath).read_text(encoding="utf-8"))
        assert "legacySnapshotPolicy" not in snapshot["runtime"]
    finally:
        runtime.close()


def testInvalidPolicyIsConclusiveWithoutCreatingJob(tmp_path):
    runtime, _, _ = previewRuntime(tmp_path)
    try:
        invalid = request(runtime, "SELECTED")
        reply = runtime.StartJob(invalid, None)
        assert not reply.ok and reply.status == "REJECTED"
        assert not runtime.jobRepository.all()
        lookup = runtime.GetStartRequest(pb.StartRequestLookup(start_request_id=invalid.start_request_id,
            runtime_instance_id=runtime.runtimeInstanceId), None)
        assert lookup.status == "REJECTED" and not lookup.job_id
    finally:
        runtime.close()


def testAllThenNoneKeepsHistoryWithoutNewSnapshotsAndRestoresPolicy(tmp_path):
    runtime, directory, image = previewRuntime(tmp_path)
    try:
        first = runtime.StartJob(request(runtime), None)
        assert waitForTerminal(runtime, first.job_id).status == "COMPLETED"
        current = sources(runtime, first.job_id)
        assert current.capture_state == "CURRENT_AVAILABLE"
        assert current.sources and all(source.snapshot_state == "CURRENT" for source in current.sources)
        assert all(source.origin_job_id == first.job_id and source.capture_id for source in current.sources)
        previousIds = {source.source_id for source in current.sources}
        previousBytes = {source.source_id: runtime.previewAssetStore.readBytes(source.source_id)
                         for source in current.sources}
        assert sources(runtime).capture_state == "NO_JOB_SELECTED"
        assert all(source.snapshot_state == "PREVIOUS" for source in sources(runtime).sources)
        assert cv2.imwrite(str(image), np.full((16, 18, 3), 220, np.uint8))
        second = runtime.StartJob(request(runtime, "NONE"), None)
        assert waitForTerminal(runtime, second.job_id).status == "COMPLETED"
        assert not (runtime.workspaceRoot / second.job_id / "preview_staging").exists()
        disabled = sources(runtime, second.job_id)
        assert disabled.legacy_snapshot_policy == "NONE" and disabled.capture_state == "DISABLED_THIS_RUN"
        assert {source.source_id for source in disabled.sources} == previousIds
        assert all(source.snapshot_state == "PREVIOUS" and source.origin_job_id == first.job_id
                   for source in disabled.sources)
        assert {source.source_id: runtime.previewAssetStore.readBytes(source.source_id)
                for source in disabled.sources} == previousBytes
        assert not getattr(runtime, "_presentationOwner", None)
        secondJob = second.job_id
        firstJob = first.job_id
    finally:
        runtime.close()
    reopened = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert reopened.LoadProject(pb.LoadProjectRequest(project_path=str(directory)), None).ok
        restored = sources(reopened, secondJob)
        assert restored.capture_state == "DISABLED_THIS_RUN" and restored.legacy_snapshot_policy == "NONE"
        assert all(source.origin_job_id == firstJob and source.snapshot_state == "PREVIOUS" for source in restored.sources)
    finally:
        reopened.close()


def testNewAllCaptureExpiresListedAssetIdRatherThanRetargetingIt(tmp_path):
    runtime, _, image = previewRuntime(tmp_path)
    try:
        first = runtime.StartJob(request(runtime), None)
        assert waitForTerminal(runtime, first.job_id).status == "COMPLETED"
        old = sources(runtime, first.job_id).sources[0]
        assert cv2.imwrite(str(image), np.full((16, 18, 3), 210, np.uint8))
        second = runtime.StartJob(request(runtime, "ALL"), None)
        assert waitForTerminal(runtime, second.job_id).status == "COMPLETED"
        assert old.source_id not in {item.source_id for item in sources(runtime, second.job_id).sources}
        assert list(runtime.StreamPreviewAsset(pb.GetPreviewAssetRequest(
            asset_id=old.source_id, project_id=runtime.loadedProjectId), None)) == []
        preview = runtime.RunOperatorPreview(pb.RunOperatorPreviewRequest(
            project_id=runtime.loadedProjectId, workflow_id="main", node_id="roi",
            operator_id="vision.preprocess.roi", image_asset_id=old.source_id,
            params_json='{"roiType":"bbox","x":0,"y":0,"width":1,"height":1}'), None)
        assert not preview.ok and preview.code == "E_PREVIEW_SOURCE_NOT_FOUND"
    finally:
        runtime.close()


def testUnknownOrDifferentProjectCopyJobCannotBecomeCurrent(tmp_path, monkeypatch):
    runtime, _, _ = previewRuntime(tmp_path)
    monkeypatch.setattr(runtime.jobManager, "start", lambda _record, _spec: None)
    try:
        job = runtime.StartJob(request(runtime, "NONE"), None).job_id
        assert sources(runtime, "unknown-job").capture_state == "INVALID_JOB"
        record = runtime.jobRepository.get(job)
        record.previewProjectKey = "different-path-same-project-uuid"
        assert sources(runtime, job).capture_state == "INVALID_JOB"
        record.previewProjectKey = ""
        assert sources(runtime, job).capture_state == "UNKNOWN"
    finally:
        runtime.close()


def testNoneLeavesPageCaptureAndProductionCounterSemanticsIntact(tmp_path):
    from tests.runtime.presentation.test_normal_capture import load, release
    runtime = RuntimeService(dbPath=tmp_path / "state.db", workspaceRoot=tmp_path / "jobs")
    display = PresentationService(runtime, tmp_path / "display")
    try:
        _, document = load(runtime, tmp_path)
        runtime.sqliteStore.setGlobalCounter(document.project.projectId, "jobs", 20)
        expected = None
        for policy in ("ALL", "NONE"):
            start = request(runtime, policy)
            start.capture_presentation = True
            start.inputs_json = '{"increment":false}'
            reply = runtime.StartJob(start, None)
            assert reply.ok, reply.message
            assert waitForTerminal(runtime, reply.job_id).status == "COMPLETED"
            deadline = time.monotonic() + 5
            while not display.store.snapshot(reply.job_id)["results"] and time.monotonic() < deadline:
                time.sleep(.01)
            result = display.store.snapshot(reply.job_id)["results"][0]
            values = {source.sourceId: source.valueJson for source in result.sources}
            assert values == (expected or values)
            expected = values
            assert result.identity.mode == "runtime"
            assert runtime.sqliteStore.getGlobalCounter(document.project.projectId, "jobs").value == 20
            assert (runtime.workspaceRoot / reply.job_id / "preview_staging").exists() == (policy == "ALL")
            info = DisplayRpc(display).ListJobs(pb.DisplayEmpty(project_id=document.project.projectId), None)
            assert next(job for job in info.jobs if job.job_id == reply.job_id).legacy_snapshot_policy == policy
            release(display, reply.job_id)
        assert len(runtime.jobRepository.all()) == 2
        capabilities = DisplayRpc(display).Capabilities(pb.DisplayEmpty(), None)
        assert {"legacy_snapshot_policy_v1", "preview_snapshot_origin_v1"} <= set(capabilities.capabilities)
    finally:
        runtime.close()


def testPrunedAcceptedEvidenceIsUnknownAndOldAcceptedEvidenceIsAll(tmp_path):
    store = SqliteStore(tmp_path / "state.db")
    store.initialize()
    repository = JobRepository(store)
    manager = JobManager(repository, EventStore(store), None)
    record = manager.createJob("project", 3, "main", legacySnapshotPolicy="NONE", previewProjectKey="copy-key")
    restored = JobRepository(store).get(record.jobId)
    assert restored.legacySnapshotPolicy == "NONE" and restored.previewProjectKey == "copy-key"
    repository.update(record.jobId, status="COMPLETED", endedAtMs=1)
    assert store.pruneTerminalJobEvents(retentionDays=1, minimumJobsPerProject=0,
        currentTimestampMs=int(time.time() * 1000) + 2 * 86400000) > 0
    assert JobRepository(store).get(record.jobId).legacySnapshotPolicy == "UNKNOWN"
    legacy = manager.createJob("project", 1, "main")
    with sqlite3.connect(store.dbPath) as connection:
        connection.execute("UPDATE jobEvents SET payloadJson = '{}' WHERE jobId = ?", (legacy.jobId,))
    assert JobRepository(store).get(legacy.jobId).legacySnapshotPolicy == "ALL"
    assert not JobRepository(store).get(legacy.jobId).previewProjectKey
    with sqlite3.connect(store.dbPath) as connection:
        connection.execute("UPDATE jobEvents SET payloadJson = ? WHERE jobId = ?",
                           ('{"legacySnapshotPolicy":[]}', legacy.jobId))
    assert JobRepository(store).get(legacy.jobId).legacySnapshotPolicy == "UNKNOWN"


def testNonePolicyAndListingEnvelopeOverRealAioRpc(tmp_path):
    import grpc
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc

    runtime, _, _ = previewRuntime(tmp_path)
    display = PresentationService(runtime, tmp_path / "display")
    server = AioRuntimeServer(runtime, display)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{server.port}") as connection:
            execution = rpc.RuntimeServiceStub(connection)
            capability = rpc.DisplayServiceStub(connection).Capabilities(pb.DisplayEmpty(), timeout=3)
            assert {"legacy_snapshot_policy_v1", "preview_snapshot_origin_v1"} <= set(capability.capabilities)
            start = request(runtime, "NONE")
            reply = execution.StartJob(start, timeout=3)
            assert reply.ok and reply.legacy_snapshot_policy == "NONE"
            assert waitForTerminal(runtime, reply.job_id).status == "COMPLETED"
            lookup = execution.GetStartRequest(pb.StartRequestLookup(start_request_id=start.start_request_id,
                runtime_instance_id=capability.runtime_instance_id), timeout=3)
            assert lookup.job_id == reply.job_id and lookup.legacy_snapshot_policy == "NONE"
            listing = execution.ListNodePreviewSources(pb.ListNodePreviewSourcesRequest(
                project_id=runtime.loadedProjectId, workflow_id="main", node_id="roi",
                job_id=reply.job_id), timeout=3)
            assert listing.job_id == reply.job_id and listing.capture_state == "DISABLED_THIS_RUN"
            assert listing.legacy_snapshot_policy == "NONE" and not listing.sources
            assert display.exporter is None and not display.jobs
    finally:
        server.close()
        runtime.close()


def testAllRunMissingImageCannotRelabelEarlierNodeSnapshot(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter

    runtime, _, _ = previewRuntime(tmp_path)
    try:
        first = runtime.StartJob(request(runtime), None)
        assert waitForTerminal(runtime, first.job_id).status == "COMPLETED"
        old = sources(runtime, first.job_id).sources
        monkeypatch.setattr(runtime.jobManager, "start", lambda _record, _spec: None)
        nextRun = runtime.StartJob(request(runtime, "ALL"), None)
        writer = PreviewSnapshotWriter(runtime.workspaceRoot / nextRun.job_id,
            jobId=nextRun.job_id, projectRevision=runtime.loadedDocument.project.revision)
        writer.capture(SimpleNamespace(nodeId="roi", outputPorts={"frame": "bbox2d"}),
            {"frame": {"only": "non-image-output"}}, SimpleNamespace(workflowId="main", iterationPath=()))
        runtime.previewAssetStore.promote(writer.root, runtime._loadedProjectPreviewKey)
        listing = sources(runtime, nextRun.job_id)
        assert listing.capture_state == "NO_CURRENT_SNAPSHOT"
        assert {source.source_id for source in listing.sources} == {source.source_id for source in old}
        assert all(source.snapshot_state == "PREVIOUS" for source in listing.sources)
    finally:
        runtime.close()


def testNoneCannotWidenRestrictedTestReleaseHost(tmp_path):
    import grpc
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
    from emo_master.apps.runtime.release_host import ReleaseHost
    from tests.runtime.test_release_host import installed

    store, _ = installed(tmp_path)
    host = ReleaseHost(store.root, tmp_path / "host-data")
    try:
        with grpc.insecure_channel(host.address) as connection:
            runtime, display = rpc.RuntimeServiceStub(connection), rpc.DisplayServiceStub(connection)
            capabilities = display.Capabilities(pb.DisplayEmpty(), timeout=3)
            assert "legacy_snapshot_policy_v1" not in capabilities.capabilities
            assert not host.runtime.supportsLegacySnapshotPolicy
            assert runtime.LoadProject(pb.LoadProjectRequest(
                project_path=str(host.prepared.projectPath.parent)), timeout=3).ok
            start = request(host.runtime, "NONE")
            rejected = runtime.StartJob(start, timeout=3)
            assert not rejected.ok and rejected.status == "REJECTED"
            assert not host.runtime.jobRepository.all()
            lookup = runtime.GetStartRequest(pb.StartRequestLookup(start_request_id=start.start_request_id,
                runtime_instance_id=host.runtime.runtimeInstanceId), timeout=3)
            assert lookup.status == "REJECTED" and not lookup.job_id
            job = display.Start(pb.DisplayStartRequest(prepared_id=host.prepared.snapshot.snapshotId), timeout=20).job_id
            assert waitForTerminal(host.runtime, job).status == "COMPLETED"
            assert host.runtime.jobRepository.get(job).legacySnapshotPolicy == "ALL"
            assert runtime.StartJob(start, timeout=3).status == "REJECTED"
            assert len(host.runtime.jobRepository.all()) == 1
    finally:
        host.close()
