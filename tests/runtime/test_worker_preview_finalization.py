"""A Job terminal cannot overtake its asynchronous legacy snapshot owner."""
from dataclasses import replace
import json
import queue
import threading
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.jobs import worker_main as module
from emo_master.apps.runtime.preview.async_writer import AsyncPreviewSnapshotWriter
from tests.runtime.test_worker_heartbeat_finalization import _spec, _stubComputation


@pytest.mark.parametrize("outcome", ["completed", "failed", "aborted"])
def testTerminalWaitsForSnapshotRetirementAndHeartbeatsContinue(monkeypatch, tmp_path, outcome):
    _stubComputation(monkeypatch, lambda: None)
    baseRunner = module.WorkflowRunner
    entered, release, returned, closing = (threading.Event() for _ in range(4))
    cancel, heartbeat = threading.Event(), threading.Event()
    writers, failures = [], []
    primary = RuntimeError("original business failure")

    class HeldWriter(AsyncPreviewSnapshotWriter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            writers.append(self)
            persist = self._writer.captureLegacy

            def hold(*args, **kwargs):
                entered.set()
                assert release.wait(5), "test did not release snapshot I/O"
                return persist(*args, **kwargs)

            self._writer.captureLegacy = hold

        def close(self, *, drain=True):
            closing.set()
            return super().close(drain=drain)

    class Runner(baseRunner):
        def __init__(self, **kwargs):
            self.writer = kwargs["previewSnapshotStore"]

        def run(self, workflowId, inputs, context, token):
            node = SimpleNamespace(nodeId="source", outputPorts={"value": "integer"})
            self.writer.capture(node, {"value": 1}, context.forNode("source"))
            assert entered.wait(1)
            self.writer.capture(node, {"value": 2}, context.forNode("source"))
            if outcome == "failed":
                raise primary

    class Events(queue.Queue):
        def put(self, event, *args, **kwargs):
            if event["eventType"] == "process.heartbeat" and closing.is_set():
                heartbeat.set()
            if event["eventType"] in {"job.completed", "job.failed", "job.aborted"}:
                assert not writers[0]._thread.is_alive()
                assert (writers[0]._writer.root / "index.json").exists()
            super().put(event, *args, **kwargs)

    monkeypatch.setattr(module, "WorkflowRunner", Runner)
    monkeypatch.setattr(module, "AsyncPreviewSnapshotWriter", HeldWriter)
    events = Events()
    spec = replace(_spec(tmp_path, None), legacySnapshotPolicy="ALL", jobWorkspacePath=str(tmp_path / "job"))

    def execute():
        try:
            module.runJobProcess(spec, cancel, events)
        except Exception as error:
            failures.append(error)
        finally:
            returned.set()

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert closing.wait(2)
        if outcome == "aborted":
            cancel.set()  # Stop after computation, while successful close drains.
        assert heartbeat.wait(1)
        assert not returned.is_set()
        assert not any(event["eventType"] in {"job.completed", "job.failed", "job.aborted"}
                       for event in list(events.queue))
    finally:
        release.set()
        worker.join(3)
    assert returned.is_set() and not worker.is_alive()
    assert failures == ([primary] if outcome == "failed" else [])
    terminal = list(events.queue)[-1]
    assert terminal["eventType"] == f"job.{outcome}"
    stats = terminal["payload"]["previewSnapshots"]
    assert stats["written"] == (2 if outcome == "completed" else 1)
    assert stats["discarded"] == (0 if outcome == "completed" else 1)
    row = json.loads((writers[0]._writer.root / "index.json").read_text(encoding="utf-8"))["entries"][0]
    assert json.loads((writers[0]._writer.root / row["relativePath"]).read_text()) == (
        2 if outcome == "completed" else 1)
    assert not any(thread.name in {"runtime-heartbeat-job-stop", "preview-writer-job-stop"}
                   for thread in threading.enumerate())


def testDisabledLegacySnapshotsDoNotCreateBackgroundWriter(monkeypatch, tmp_path):
    _stubComputation(monkeypatch, lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("NONE policy must not create a snapshot owner")

    monkeypatch.setattr(module, "AsyncPreviewSnapshotWriter", forbidden)
    events = queue.Queue()
    module.runJobProcess(_spec(tmp_path, None), threading.Event(), events)
    terminal = list(events.queue)[-1]
    assert terminal["eventType"] == "job.completed"
    assert "previewSnapshots" not in terminal.get("payload", {})


def testSpawnedVideoJobPromotesFinalSnapshotWithTerminalStatistics(tmp_path):
    import cv2
    import numpy as np
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.runtime_test_utils import jobFailureDetails, waitForTerminal
    from tests.runtime.test_video_files_foreach import prepare

    prepare(tmp_path)
    runtime = RuntimeService(dbPath=tmp_path / "runtime/state.db", workspaceRoot=tmp_path / "runtime/jobs")
    try:
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(tmp_path / "files.emoproj")), None)
        assert loaded.ok, loaded.message
        started = runtime.StartJob(pb.StartJobRequest(project_id=runtime.loadedProjectId,
            workflow_id="main", legacy_snapshot_policy="ALL"), None)
        assert started.ok, started.message
        status = waitForTerminal(runtime, started.job_id, timeoutSeconds=30)
        assert status.status == "COMPLETED", jobFailureDetails(runtime, started.job_id, status)
        rows = runtime.eventStore.readMerged(started.job_id)
        assert not [event for event in rows if event.eventType == "preview.snapshot.failed"]
        terminal = next(event for event in rows if event.eventType == "job.completed")
        stats = json.loads(terminal.payloadJson)["previewSnapshots"]
        assert stats["submitted"] >= stats["written"] > 0
        assert stats["failed"] == stats["discarded"] == 0
        assert stats["submitted"] == stats["written"] + stats["coalesced"]
        final = next(event for event in reversed(rows) if event.nodeId == "load" and event.eventType == "node.completed")
        sources = runtime.previewAssetStore.listSources(runtime._loadedProjectPreviewKey, "frame", "load", [])
        source = next(source for source in sources if source.port == "image")
        assert source.nodeRunId == final.nodeRunId and source.workflowRunId == final.workflowRunId
        assert source.originJobId == started.job_id
        content, mime = runtime.previewAssetStore.readBytes(source.assetId)
        assert mime == "image/png"
        image = cv2.imdecode(np.frombuffer(content, np.uint8), -1)
        assert np.mean(image) == pytest.approx(50, abs=5)
    finally:
        runtime.close()
