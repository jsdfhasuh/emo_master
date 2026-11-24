"""Capability provenance and side-effect boundaries at the debug entry points."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.execution.background_tasks import BackgroundTaskService
from emo_master.apps.runtime.execution.operator_executor import OperatorExecutor
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.operator_debug import rpc
from emo_master.apps.runtime.operator_debug.contracts import draft, trustedEntries
from emo_master.apps.runtime.preview.executor import PurePreviewExecutor
from emo_master.apps.runtime.preview.store import PreviewAssetStore
from tests.runtime.operator_debug_fixture import BackgroundProbe, project
from tests.runtime.test_operator_debug_resources import connected, run, uploadImage
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor
from tests.runtime.test_operator_executor_shared import execute, nodeFor
from tests.runtime.test_pure_preview_cancellation import _descriptor
from tests.runtime.test_workflow_debug_rpc import FlowClient
from tests.runtime.test_workflow_debug_control import nestedProject


def testOnlyRegisteredCapabilityEnablesTheClient(runtime):
    registry = runtime.pluginScanResult.activeOperators
    entries = trustedEntries(registry)
    saver = "vision.io.image_saver"
    assert registry[saver].manifest.runtimeCapabilities == ("backgroundTasks",)
    assert entries[saver]["runtimeCapabilities"] == ["backgroundTasks"]
    legacy = "vision.value.number"
    raw = project(legacy)
    raw["runtimeCapabilities"] = ["backgroundTasks"]
    spec = draft(json.dumps(raw), "draft", "main", "node", legacy, entries)
    assert spec["runtimeCapabilities"] == []
    descriptor = replace(registry[legacy], operatorClass=BackgroundProbe)
    service = BackgroundTaskService()
    try:
        executor = OperatorExecutor({legacy: descriptor}, backgroundTasks=service,
                                    runtimeCapabilities={legacy: ["backgroundTasks"]})
        assert execute(executor, nodeFor(BackgroundProbe), {})[0] == {"value": 0}
    finally:
        service.close()


def testSingleNodeDebugDrainsBeforeReturningTheImageResult(runtime, tmp_path):
    with connected(runtime, "vision.io.image_saver") as debug:
        image = uploadImage(debug)
        params = {"outputPath": str(tmp_path / "saved.png"), "saveMode": "background", "namingMode": "unique"}
        record = run(debug, params, {"image": image}, confirmSideEffects=True)
        assert record["status"] == "SUCCEEDED", record
        receipt = record["outputs"]["result"]
        assert receipt["state"] == "SUBMITTED" and receipt["taskId"] and not receipt["saved"]
        assert np.all(cv2.imread(receipt["path"]) == 120)

        params["outputPath"] = str(tmp_path / "invalid.unsupported")
        failed = run(debug, params, {"image": image}, confirmSideEffects=True)
        assert failed["status"] == "FAILED", failed
        assert failed["code"] == "E_BACKGROUND_TASK_FAILED"
        source = failed["diagnostics"]["backgroundTask"]
        assert source["workflowId"] == "main" and source["nodeRunId"] == failed["executionId"]
        assert not list(tmp_path.glob("*.unsupported"))


@contextmanager
def _debugStagedFile(runtime, directory, kind):
    def testEntries(registry):
        entries = trustedEntries(registry)
        entries["vision.value.number"].update(entry="tests.runtime.operator_debug_fixture:HeldFileProbe",
                                              runtimeCapabilities=["backgroundTasks"])
        return entries

    with ExitStack() as stack:
        stack.enter_context(patch.object(rpc, "trustedEntries", testEntries))
        if kind == "operator":
            debug = stack.enter_context(connected(runtime, "vision.value.number"))
        else:
            flow = FlowClient(runtime)
            flow.open()
            stack.callback(flow.close)
        session, = runtime.operatorDebugManager.sessions.values()
        workspace = Path(session.worker.workspace.name)
        (workspace / "test-output-directory").write_text(str(directory), encoding="utf-8")
        if kind == "operator":
            debug.execute({"value": 1}, {})
        else:
            flow.command("continue", flow.start())
        waitFor((workspace / "test-write-started").exists)
        staged, = directory.glob(".emo-image-stage-*/.emo-image-*.tmp")
        assert staged.read_bytes() == b"partial"
        yield session.worker, staged


@pytest.mark.parametrize("kind", ["operator", "workflow"])
def testRetiringKilledDebugWorkerRemovesOnlyItsStagedFile(runtime, tmp_path, kind):
    unrelated = tmp_path / "external.tmp"
    unrelated.write_bytes(b"keep")
    with _debugStagedFile(runtime, tmp_path, kind) as (worker, staged):
        assert worker.terminate()
        waitFor(lambda: not runtime.operatorDebugManager.ownsResources())
        assert not staged.exists()
        assert unrelated.read_bytes() == b"keep"


def _crashDebugRuntime(directory, kind):
    # Keep even the abruptly interrupted debug asset workspace inside this test.
    tempRoot = directory / "debug-assets"
    tempRoot.mkdir()
    tempfile.tempdir = str(tempRoot)
    runtime = RuntimeService(dbPath=directory / "runtime.db", workspaceRoot=directory / "jobs")
    try:
        with _debugStagedFile(runtime, directory, kind) as (worker, _staged):
            # Interrupt both processes before the live parent's retirement hook can run.
            with runtime.operatorDebugManager.lock:
                assert worker.terminate()
                worker.process.join(5)
                assert worker.process.exitcode is not None
                os._exit(42)
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", ["operator", "workflow"])
def testRestartReclaimsInterruptedDebugStaging(tmp_path, kind):
    foreign = tmp_path / (".emo-image-" + "f" * 32 + ".tmp")
    foreign.write_bytes(b"keep")
    process = multiprocessing.get_context("spawn").Process(target=_crashDebugRuntime, args=(tmp_path, kind))
    try:
        process.start()
        process.join(25)
        assert process.exitcode == 42, "debug Runtime did not reach the staged write"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()
    staged, = tmp_path.glob(".emo-image-stage-*/.emo-image-*.tmp")
    assert staged.read_bytes() == b"partial"
    replacement = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert not staged.exists()
        assert foreign.read_bytes() == b"keep"
    finally:
        replacement.close()


@pytest.fixture
def probeWorkflow(runtime, monkeypatch):
    def testEntries(registry):
        entries = trustedEntries(registry)
        entries["vision.value.number"].update(
            entry="tests.runtime.operator_debug_fixture:BackgroundProbe", runtimeCapabilities=["backgroundTasks"])
        return entries

    monkeypatch.setattr(rpc, "trustedEntries", testEntries)
    return FlowClient(runtime)


def testWorkflowSessionGetsClientAndCloneSafeTrialCannotPublish(runtime, probeWorkflow):
    client = probeWorkflow
    client.open()
    try:
        first = client.start()
        client.command("into", first)
        paused = client.paused(first["pauseSequence"])
        client.command("trial", paused)
        waitFor(lambda: client.state()["flow"].get("trial") and not client.state()["flow"].get("trialRunning"))
        trial = client.call("GetWorkflowDebugSnapshot", execution_id=client.state()["flow"]["trial"])
        assert trial["outputs"] == {"value": 0}
        session = runtime.operatorDebugManager.sessions[client.identity["session_id"]]
        marker = Path(session.worker.workspace.name) / "marker"
        assert not marker.exists()

        client.command("continue", paused)
        waitFor(lambda: client.state()["state"] == "SUCCEEDED")
        snapshot = client.call("GetWorkflowDebugSnapshot", execution_id=client.state()["flow"]["lastOutput"])
        assert snapshot["outputs"] == {"value": 1}
        assert marker.read_bytes() == b"done"
    finally:
        client.close()


def testWorkflowDebugReportsUnwaitedFailureFromChildNode(probeWorkflow):
    client = probeWorkflow
    payload = nestedProject()
    payload["workflows"]["child"]["nodes"][1]["params"]["value"] = -1
    client.open(payload)
    try:
        client.command("continue", client.start())
        waitFor(lambda: client.state()["state"] == "FAILED")
        failure = client.state()["flow"]["terminal"]
        assert failure["code"] == "E_BACKGROUND_TASK_FAILED"
        assert failure["identity"]["workflowId"] == "child"
        assert failure["identity"]["callerNodeId"] == "number"
        assert failure["identity"]["nodeRunId"]
    finally:
        client.close()


def testPurePreviewWithADeclaredCapabilityCannotSubmit(tmp_path):
    descriptor = _descriptor()
    manifest = replace(descriptor.manifest, runtimeCapabilities=("backgroundTasks",), outputPorts={"value": "number"})
    descriptor = replace(descriptor, manifest=manifest, operatorClass=BackgroundProbe)
    store = PreviewAssetStore(tmp_path)
    executor = PurePreviewExecutor({manifest.operatorId: descriptor}, store)
    try:
        asset = store.addTransientImage(np.zeros((2, 2), np.uint8))
        result = executor.execute(manifest.operatorId, {}, asset.assetId)
        assert result.ok and result.outputs == {"value": 0}, result
    finally:
        executor.close()
        store.close()
