"""The shipped .emoproj files run in the real supervised debugger."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.services.workflow_debug import WorkflowDebugConnection
from emo_master.apps.runtime.operator_debug.assets import decode
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


EXAMPLES = Path(__file__).resolve().parents[2] / "examples/workflow_debugger"
PROJECTS = ("01-nested-calls.emoproj", "02-foreach.emoproj", "03-image.emoproj")


@pytest.mark.parametrize("filename", PROJECTS)
def testShippedProjectRunsWithoutProductionMutation(runtime, filename):
    path = EXAMPLES / filename
    original = path.read_bytes()
    payload = json.loads(original)
    before = deepcopy(payload)
    WorkflowCompiler(runtime.pluginScanResult.activeOperators).compile(ProjectDocument.model_validate(payload))
    connection = WorkflowDebugConnection(RuntimeClient(runtime))
    try:
        connection.openWorkflow(payload, "main")
        waitFor(lambda: connection.flowSnapshot()["session"]["state"] == "READY")
        inputs = {}
        if filename == PROJECTS[1]:
            inputs["items"] = {"inline": json.loads((EXAMPLES / "items.json").read_text())}
        elif filename == PROJECTS[2]:
            inputs["image"] = connection.upload((EXAMPLES / "sample.png").read_bytes(), "image/png", {"kind": "upload"})
        connection.start(inputs)
        def paused():
            state = connection.flowSnapshot()["session"]
            assert state["state"] not in {"FAILED", "FAULTED"}, state
            return state if state["state"] == "PAUSED" else None
        state = waitFor(paused)
        connection.control("continue", state["flow"]["pauseSequence"])
        def finished():
            state = connection.flowSnapshot()["session"]
            assert state["state"] not in {"FAILED", "FAULTED"}, state
            return state if state["state"] == "SUCCEEDED" else None
        state = waitFor(finished)
        snapshot = connection.call("GetWorkflowDebugSnapshot", execution_id=state["flow"]["lastOutput"])
        if filename == PROJECTS[0]:
            assert snapshot["outputs"] == {"value": 7}
        elif filename == PROJECTS[1]:
            assert snapshot["outputs"] == {"result": [False, True, True], "index": [0, 1, 2]}
        else:
            assert snapshot["outputs"]["threshold"] == 127
            asset = snapshot["outputsAssets"]["mask"]
            downloaded = connection.download(asset["assetId"])
            mask, size = decode(downloaded["content"], "image/png")
            assert mask.shape == (240, 320) and mask.dtype == np.uint8
            assert size == 240 * 320
            assert set(np.unique(mask)) == {0, 255}
        assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
        assert payload == before and path.read_bytes() == original
    finally:
        connection.close()
    assert not runtime.operatorDebugManager.ownsResources()
