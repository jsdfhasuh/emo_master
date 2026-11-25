from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3
from threading import Event, get_ident
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from emo_master import __version__
from emo_master.apps.runtime.artifacts.store import ArtifactStore
from emo_master.apps.runtime.business_sqlite.backend import freezeTargets, initialize
from emo_master.apps.runtime.execution.background_tasks import BackgroundTaskService
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from tests.sqlite_writer.test_relative_file_references import referenceDocument


@pytest.fixture
def setupFiles(tmp_path):
    database = tmp_path / "results.sqlite3"
    initialize(database, "records", [{"name": "picture", "storageType": "FILE_REFERENCE"}])
    plugins = Path("src/emo_master/plugins/builtins")
    scanned = PluginRegistry(__version__).scanRoots([plugins / "image_saver", plugins / "sqlite_writer"])
    assert not scanned.rejectedOperators
    document = referenceDocument(tmp_path)
    saver = next(n for n in document.workflows["main"].nodes if n.nodeId == "save")
    saver.params.update(saveMode="background", namingMode="unique")
    return database, document, scanned.activeOperators


def testBackgroundFileOverlapsDownstreamAndReachesArtifactPreviewSqliteAndReader(
    tmp_path, monkeypatch, setupFiles,
):
    database, document, registry = setupFiles
    image = np.arange(120, dtype=np.uint8).reshape(10, 12)
    original = image.copy()
    order = []
    startedEncoding, sqliteWaiting = Event(), Event()
    runtimeThread = get_ident()
    encode = cv2.imencode

    def gatedEncode(extension, pixels):
        assert get_ident() != runtimeThread
        startedEncoding.set()
        assert sqliteWaiting.wait(5), "SQLite must wait before capturing this file binding"
        return encode(extension, pixels)

    monkeypatch.setattr(cv2, "imencode", gatedEncode)

    class MutateImage:
        def executeNode(self, inputs, params, context):
            receipt = inputs["submitted"]
            assert startedEncoding.wait(5)
            assert receipt["state"] == "SUBMITTED" and receipt["saved"] is False
            assert not Path(receipt["path"]).exists() and "artifact.created" not in order
            image.fill(0)
            order.append("overlap")
            return {"status": "ok", "outputs": {"enabled": True}}

    class Reader:
        def executeNode(self, inputs, params, context):
            receipt = inputs["submitted"]
            saved = context["backgroundTasks"].wait(receipt["taskId"])
            pixels = cv2.imdecode(np.fromfile(saved["path"], dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            np.testing.assert_array_equal(pixels, original)
            order.append("reader")
            return {"status": "ok", "outputs": {}}

    class TracedService(BackgroundTaskService):
        def wait(self, taskId):
            sqliteWaiting.set()
            return super().wait(taskId)

    def capture(node, artifact, context):
        assert get_ident() == runtimeThread and node.nodeId == context.callerNodeId == "save"
        assert artifact["checksum"] == sha256(Path(artifact["path"]).read_bytes()).hexdigest()
        order.append("captureArtifact")

    def publish(**event):
        if event["eventType"] == "node.started" and event["context"].callerNodeId == "writer":
            order.append("writer.started")
        elif event["eventType"] in ("artifact.created", "sqlite.write.started", "sqlite.write.finished"):
            assert get_ident() == runtimeThread
            order.append(event["eventType"])

    # The read-only consumer is a separate declared operator. Its descriptor uses the
    # same manifest capability as the registered ImageSaver.
    saverDescriptor = registry[ImageSaverOperator.meta.operatorId]
    readerId = "test.file_reader"
    readerManifest = replace(saverDescriptor.manifest, operatorId=readerId, paramSchema={},
                             inputPorts={"submitted": "json", "written": "json"}, outputPorts={})
    registry[readerId] = replace(saverDescriptor, manifest=readerManifest, operatorClass=Reader)
    registry["test.mutate_image"] = MutateImage
    payload = document.model_dump(mode="json", by_alias=True)
    workflow = payload["workflows"]["main"]
    workflow["nodes"].extend([
        {"nodeId": "overlap", "operatorId": "test.mutate_image",
         "inputPorts": {"submitted": "json"}, "outputPorts": {"enabled": "boolean"}},
        {"nodeId": "reader", "operatorId": readerId},
    ])
    workflow["edges"].extend([
        {"fromNode": "save", "fromPort": "result", "toNode": "overlap", "toPort": "submitted"},
        {"fromNode": "overlap", "fromPort": "enabled", "toNode": "writer", "toPort": "enabled"},
        {"fromNode": "save", "fromPort": "result", "toNode": "reader", "toPort": "submitted"},
        {"fromNode": "writer", "fromPort": "receipt", "toNode": "reader", "toPort": "written"},
    ])
    compiled = WorkflowCompiler(registry).compile(freezeTargets(ProjectDocument.model_validate(payload), tmp_path))
    token = CancellationToken()
    service = TracedService(token)
    runner = WorkflowRunner(compiled, registry, backgroundTasks=service, eventPublisher=publish,
                            artifactStore=ArtifactStore(tmp_path),
                            previewSnapshotStore=SimpleNamespace(captureArtifact=capture))
    try:
        runner.run("main", {"image": image}, RunContext.root("job", "main"), token)
    finally:
        sqliteWaiting.set()
        service.close()
    runner.pollBackgroundTasks()
    assert order == ["overlap", "artifact.created", "captureArtifact", "writer.started", "sqlite.write.started",
                     "sqlite.write.finished", "reader"]
    with sqlite3.connect(database) as conn:
        [(savedPath,)] = conn.execute("SELECT picture FROM records").fetchall()
    [artifactPath] = (tmp_path / "artifacts").iterdir()
    assert Path(savedPath).read_bytes() == artifactPath.read_bytes()


@pytest.mark.parametrize("policy", ["stop", "continue"])
def testAlreadyFailedSaveDeliveredToSqlitePolicyWithoutFileOrInsert(tmp_path, monkeypatch, setupFiles, policy):
    database, document, registry = setupFiles
    writer = next(n for n in document.workflows["main"].nodes if n.nodeId == "writer")
    writer.params["failurePolicy"] = policy
    compiled = WorkflowCompiler(registry).compile(freezeTargets(document, tmp_path))
    events = []
    token = CancellationToken()
    service = BackgroundTaskService(token, maxRecords=1)

    def failEncode(*args):
        raise OSError("injected encoder failure")

    def publish(**event):
        events.append(event)
        ctx = event["context"]
        if event["eventType"] == "node.completed" and ctx.callerNodeId == "save":
            # A second FIFO task acts as a barrier without observing the save failure.
            task = service.client(ctx, "barrier").submit(lambda control: None,
                                                         taskName="barrier", estimatedBytes=0)
            service.wait(task.id)
            taskId = event["payload"]["outputs"]["result"]["taskId"]
            assert service.status(taskId) == "FAILED"

    monkeypatch.setattr(cv2, "imencode", failEncode)
    runner = WorkflowRunner(compiled, registry, backgroundTasks=service, eventPublisher=publish,
                            artifactStore=ArtifactStore(tmp_path))
    args = ("main", {"image": np.zeros((2, 3), np.uint8)}, RunContext.root("job", "main"), token)
    try:
        if policy == "stop":
            with pytest.raises(WorkflowExecutionError, match="injected encoder failure"):
                runner.run(*args)
        else:
            result = runner.run(*args)
            assert result.diagnostics["sqliteReceipt"]["status"] == "FAILED"
    finally:
        service.close()
    runner.pollBackgroundTasks()
    [failed] = [e["payload"]["receipt"] for e in events if e["eventType"] == "sqlite.write.finished"]
    assert failed["status"] == "FAILED" and failed["error"]["mappingRow"] == 0
    assert "injected encoder failure" in failed["error"]["message"]
    assert all(e["eventType"] not in {"artifact.created", "sqlite.write.started"} for e in events)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT picture FROM records").fetchall() == []
    assert not list((tmp_path / "artifacts").iterdir())
    assert not list(tmp_path.rglob("*.png"))
    [saveEvent] = [e for e in events if e["eventType"] == "node.completed" and e["context"].callerNodeId == "save"]
    taskId = saveEvent["payload"]["outputs"]["result"]["taskId"]
    assert taskId in failed["error"]["message"] and saveEvent["context"].nodeRunId in failed["error"]["message"]
    with pytest.raises(WorkflowExecutionError) as expired:
        service.status(taskId)
    assert expired.value.code == "E_BACKGROUND_TASK_EXPIRED"
