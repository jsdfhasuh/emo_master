from pathlib import Path

from emo_master.apps.runtime.context.global_counters import ProjectGlobalCounters
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.global_counter.operator import GlobalCounterOperator


def testGlobalCounterOperatorHandlesSignalsAndResetPrecedence() -> None:
    class Accessor:
        def __init__(self) -> None:
            self.value = 0

        def apply(self, name: str, *, increment: bool, reset: bool):
            assert name == "parts"
            if reset:
                self.value = 0
            elif increment:
                self.value += 1
            return type("Record", (), {"value": self.value})()

    operator = GlobalCounterOperator()
    accessor = Accessor()
    context = {"globalCounters": accessor}

    assert operator.executeNode({}, {"name": "parts"}, context)["outputs"] == {
        "count": 0
    }
    assert operator.executeNode(
        {"increment": True}, {"name": "parts"}, context
    )["outputs"] == {"count": 1}
    assert operator.executeNode(
        {"increment": True, "reset": True}, {"name": "parts"}, context
    )["outputs"] == {"count": 0}


def testGlobalCounterOperatorReturnsStableInputAndStateErrors() -> None:
    operator = GlobalCounterOperator()

    invalidName = operator.executeNode({}, {"name": " bad"}, {})
    invalidSignal = operator.executeNode(
        {"increment": 1}, {"name": "parts"}, {"globalCounters": object()}
    )
    unavailable = operator.executeNode({}, {"name": "parts"}, {})

    assert invalidName["error"]["code"] == "E_COUNTER_NAME_INVALID"
    assert invalidSignal["error"]["code"] == "E_INPUT_TYPE"
    assert unavailable["error"]["code"] == "E_RUNTIME_STATE_UNAVAILABLE"


def testWorkflowRunnerSharesProjectCounterAcrossSameNameNodes(tmp_path: Path) -> None:
    pluginRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    registry = PluginRegistry(coreVersion="0.6.1").scan(pluginRoot).activeOperators
    document = ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "counter-project",
                "name": "Counter Project",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {"increment": "boolean"},
                    "outputs": {"first": "integer", "second": "integer"},
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "counter-a",
                            "kind": "operator",
                            "operatorId": "vision.state.counter",
                            "params": {"name": "shared"},
                        },
                        {
                            "nodeId": "counter-b",
                            "kind": "operator",
                            "operatorId": "vision.state.counter",
                            "params": {"name": "shared"},
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "input",
                            "fromPort": "increment",
                            "toNode": "counter-a",
                            "toPort": "increment",
                        },
                        {
                            "fromNode": "input",
                            "fromPort": "increment",
                            "toNode": "counter-b",
                            "toPort": "increment",
                        },
                        {
                            "fromNode": "counter-a",
                            "fromPort": "count",
                            "toNode": "output",
                            "toPort": "first",
                        },
                        {
                            "fromNode": "counter-b",
                            "fromPort": "count",
                            "toNode": "output",
                            "toPort": "second",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()
    runner = WorkflowRunner(
        compiled,
        registry,
        globalCounters=ProjectGlobalCounters(store, "counter-project"),
    )

    result = runner.run(
        "main",
        {"increment": True},
        RunContext.root("job", "main", str(tmp_path), projectId="counter-project"),
        CancellationToken(),
    )

    assert result.outputs == {"first": 1, "second": 2}
    assert store.getGlobalCounter("counter-project", "shared").value == 2
