"""Control-only fixture: not a YOLO/PLC/gateway acceptance test."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from emo_master import __version__
from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("control_example", ROOT / "examples/two_station_normal_path/run_simulation.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def registry():
    result = PluginRegistry(coreVersion=__version__).scan(ROOT / "src/emo_master/plugins")
    assert not result.rejectedOperators
    return result.activeOperators


class ProbeRunner(WorkflowRunner):
    def _runWorkflow(self, workflowId, inputs, context, cancellation, **kwargs):
        if workflowId == "mock_holes":
            self.secondObservations.append((inputs["shot"], inputs["after"],
                self.globalVariables.get("station1-shot-count")))
        return super()._runWorkflow(workflowId, inputs, context, cancellation, **kwargs)


@pytest.mark.parametrize("reorder", [False, True])
def testFourFramesSecondResetBeforeProcessingAndUnselectedSideEffects(tmp_path, reorder):
    payload = deepcopy(demo.loadProject())
    if reorder:
        for workflow in payload["workflows"].values():
            workflow["nodes"].reverse()
            workflow["edges"].reverse()
    operators = registry()
    compiled = WorkflowCompiler(operators).compile(payload)
    store = SqliteStore(tmp_path / "state.db")
    store.initialize()
    state = ProjectGlobalVariables(store, compiled.projectId, payload["globalVariables"], "job")
    state.synchronize()
    state.initializeJob()
    events = []
    runner = ProbeRunner(compiled, operators, globalVariables=state, eventPublisher=lambda **event: events.append(event))
    runner.secondObservations = []
    fixed, space = demo.loadReference(1)
    observations = []
    for number in range(1, 5):
        result = runner.run("station1", demo.mockInputs(space, fixed, number), RunContext.root("job", "station1"), CancellationToken())
        observations.append((result.outputs["shot"], state.get("station1-shot-count")))
    assert observations == [(1, 1), (2, 0), (1, 1), (2, 0)]
    assert runner.secondObservations == [(2, 0, 0), (2, 0, 0)]
    starts = [e for e in events if e["eventType"] == "workflow.started"]
    assert [e["context"].workflowId for e in starts].count("mock_a") == 2
    assert [e["context"].workflowId for e in starts].count("mock_b") == 2
    assert [e["context"].workflowId for e in starts].count("mock_holes") == 2
    assert not any(e["eventType"] == "node.started" and e["context"].callerNodeId == "invalid_count" for e in events)


def testIllegalCountStopsInsideGraph(tmp_path):
    payload, operators = demo.loadProject(), registry()
    compiled = WorkflowCompiler(operators).compile(payload)
    state = ProjectGlobalVariables(SqliteStore(tmp_path / "state.db"), compiled.projectId, payload["globalVariables"], "job")
    state.store.initialize()
    state.synchronize()
    state.initializeJob()
    state.set("station1-shot-count", 2)
    events = []
    runner = WorkflowRunner(compiled, operators, globalVariables=state, eventPublisher=lambda **event: events.append(event))
    fixed, space = demo.loadReference(1)
    with pytest.raises(WorkflowExecutionError) as raised:
        runner.run("station1", demo.mockInputs(space, fixed, 1), RunContext.root("job", "station1"), CancellationToken())
    assert raised.value.code == "E_SHOT_COUNTER"
    assert not any(e["eventType"] == "workflow.started" and e["context"].workflowId in {"first", "second1"} for e in events)


def testRequiredAfterAndStaticInterfaceCompilation():
    payload, operators = demo.loadProject(), registry()
    workflow = payload["workflows"]["station1"]
    workflow["edges"] = [e for e in workflow["edges"] if e["toNode"] != "increment"]
    with pytest.raises(WorkflowCompileError) as raised:
        WorkflowCompiler(operators).compile(payload)
    assert any(i.code == "E_VARIABLE_DEPENDENCY_REQUIRED" for i in raised.value.issues)
    for field in ("operation", "variableId"):
        payload = demo.loadProject()
        payload["globalVariables"]["selector"] = dict(name="selector", type="string", kind="variable", lifetime="job", initialValue="increment")
        payload["workflows"]["station1"]["nodes"][1]["globalVariableBindings"] = [{"parameterPath": [field], "variableId": "selector"}]
        with pytest.raises(WorkflowCompileError):
            WorkflowCompiler(operators).compile(payload)
