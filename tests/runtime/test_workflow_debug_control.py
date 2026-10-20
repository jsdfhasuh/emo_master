from copy import deepcopy
import queue
import threading
import time

import pytest

from emo_master.apps.runtime.operator_debug.contracts import DebugError
from emo_master.apps.runtime.operator_debug.state import DebugVariables
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.workflow_debug.conditions import compileCondition, evaluate
from emo_master.apps.runtime.workflow_debug.controller import WorkflowDebugController
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.test_workflow_while_boolean import runnerFor, booleanPayload, _Advance


class LiveRun:
    def __init__(self, runner, inputs):
        self.events = queue.Queue()
        self.cancel = CancellationToken()
        self.controller = WorkflowDebugController(self.cancel, DebugVariables({}),
            lambda kind, **data: self.events.put((kind, deepcopy(data))))
        runner.debugController = self.controller
        self.locations = {(workflow.workflowId, node.nodeId) for workflow in runner.compiledProject.workflows.values()
                          for node in workflow.nodes if node.kind not in {"workflow_input", "workflow_output"}}
        self.result, self.error = None, None
        def run():
            try:
                self.result = runner.run("main", inputs, RunContext.root("debug", "main"), self.cancel)
            except Exception as error:
                self.error = error
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def paused(self):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            kind, data = self.events.get(timeout=5)
            if kind == "paused":
                return data
        raise AssertionError("no confirmed pause")

    def command(self, action, pause=None, **fields):
        self.controller.command(dict(action=action, pauseSequence=(pause or {}).get("pauseSequence"), **fields), self.locations)

    def close(self):
        self.cancel.cancel()
        self.thread.join(5)
        assert not self.thread.is_alive()


def nestedProject():
    from tests.runtime.test_workflow_loop_contracts_v2 import _metadata
    payload = _metadata("debug")
    payload.update(workflowOrder=["main", "child"], workflows={
        "main": dict(name="Main", inputs={}, outputs={"value": "number"}, nodes=[
            dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="first", kind="subflow", targetWorkflowId="child"),
            dict(nodeId="second", kind="subflow", targetWorkflowId="child"),
            dict(nodeId="output", kind="workflow_output")], edges=[
                dict(fromNode="second", fromPort="value", toNode="output", toPort="value")]),
        "child": dict(name="Child", inputs={}, outputs={"value": "number"}, nodes=[
            dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="number", kind="operator", operatorId="vision.value.number", params={"value": 7}),
            dict(nodeId="output", kind="workflow_output")], edges=[
                dict(fromNode="number", fromPort="value", toNode="output", toPort="value")])})
    return payload


def nestedRunner():
    from emo_master.plugins.builtins.number_value.operator import NumberValueOperator
    registry = {"vision.value.number": NumberValueOperator}
    return WorkflowRunner(WorkflowCompiler(registry).compile(nestedProject()), registry)


def testNestedIntoOutAndRepeatedCallIdentities():
    live = LiveRun(nestedRunner(), {})
    try:
        first = live.paused()
        assert first["nodeId"] == "first"
        live.command("into", first)
        child = live.paused()
        assert child["nodeId"] == "number" and len(child["stack"]) == 2
        live.command("out", child)
        returned = live.paused()
        assert returned["nodeId"] == "first" and returned["phase"] == "call.return"
        assert returned["outputs"] == {"value": 7}
        live.command("into", returned)
        second = live.paused()
        live.command("into", second)
        other = live.paused()
        assert other["identity"]["workflowRunId"] != child["identity"]["workflowRunId"]
        assert other["identity"]["nodeRunId"] != child["identity"]["nodeRunId"]
        live.command("continue", other)
        live.thread.join(5)
        assert live.error is None and live.result.outputs == {"value": 7}
    finally:
        live.close()


def testBreakpointInterruptsOverAndRejectsOldPause():
    live = LiveRun(nestedRunner(), {})
    try:
        first = live.paused()
        live.command("breakpoints", breakpoints=[dict(workflowId="child", nodeId="number", condition='params["value"] == 7')])
        live.command("over", first)
        child = live.paused()
        assert child["reason"] == "breakpoint"
        with pytest.raises(DebugError, match="pause moved"):
            live.command("continue", first)
        live.command("continue", child)
        other = live.paused()
        assert other["hits"] == {"child/number": 2}
    finally:
        live.close()


def testOverLastChildNodePausesAtActualCallerReturn():
    live = LiveRun(nestedRunner(), {})
    try:
        first = live.paused()
        live.command("into", first)
        child = live.paused()
        live.command("over", child)
        returned = live.paused()
        assert returned["nodeId"] == "first" and returned["phase"] == "call.return"
        assert returned["outputs"] == {"value": 7}
    finally:
        live.close()


def testIntoLoopDoesNotSpendStepsOnInternalBoundaryPropagation():
    live = LiveRun(runnerFor(), {"count": 0, "hasNext": True})
    try:
        initial = live.paused()
        live.command("into", initial)
        child = live.paused()
        assert child["nodeId"] == "increment" and child["phase"] == "node.before"
    finally:
        live.close()


@pytest.mark.parametrize("sequence", [True, 1.0, "1", None])
def testPauseSequenceMustBeAnInteger(sequence):
    live = LiveRun(nestedRunner(), {})
    try:
        live.paused()
        with pytest.raises(DebugError):
            live.controller.requirePause(sequence)
        assert live.controller.state == "PAUSED"
    finally:
        live.close()


def testLoopPauseIsExcludedFromTimeoutAndHitCountsAreInvocationScoped():
    payload = booleanPayload()
    payload["workflows"]["main"]["nodes"][1]["loop"]["timeoutMs"] = 100
    live = LiveRun(runnerFor(payload), {"count": 0, "hasNext": True})
    try:
        initial = live.paused()
        live.command("breakpoints", breakpoints=[dict(workflowId="body", nodeId="increment", hitCount=2,
                                                     condition='inputs["count"] == 1')])
        live.command("continue", initial)
        second = live.paused()
        assert second["identity"]["iterationPath"] == (1,)
        time.sleep(.2)
        live.command("continue", second)
        live.thread.join(5)
        assert live.error is None and live.result.outputs["count"] == 3
        assert _Advance.calls == [0, 1, 2]
    finally:
        live.close()


@pytest.mark.parametrize("expression", ['__import__("os")', 'inputs.__class__', '[v for v in inputs]',
    '2 ** 100000', 'inputs[hits]', 'lambda: 1', 'inputs["x"].read()', 'x == 1'])
def testConditionsRejectExecutableOrUnboundedSyntax(expression):
    with pytest.raises(DebugError):
        compileCondition(expression)


def testConditionsRequireScalarComparisonAndStrictBooleans():
    assert evaluate(compileCondition('hits >= 2 and variables["ready"]'), dict(hits=2, variables={"ready": True}))
    for expression in ('inputs["x"] == 1', 'inputs["x"]'):
        with pytest.raises(DebugError):
            evaluate(compileCondition(expression), {"inputs": {"x": [1]}})


def testSignedScalarLiteralsAndLiteralNegativeIndexesRemainBounded():
    assert evaluate(compileCondition('inputs["values"][-1] < -1.5 and +2 > -2'), {"inputs": {"values": [-3]}})
    for expression in ('-hits == 1', '-True == 1', '--1 == 1', 'hits == 1e999', 'inputs[-hits] == 1'):
        with pytest.raises(DebugError):
            compileCondition(expression)


@pytest.mark.parametrize("target", ["operator", "subflow", "loop"])
def testRunToUnselectedBranchNeverExecutesOrPausesTarget(target):
    from tests.runtime.test_control_flow_activation import program, runner, Probe
    executor, _ = runner(program(target=target))
    Probe.calls.clear()
    live = LiveRun(executor, {"condition": True, "data": "shared"})
    try:
        initial = live.paused()
        assert initial["nodeId"] == "branch"
        live.command("runTo", initial, workflowId="main", nodeId="work")
        live.thread.join(5)
        assert live.result.outputs == {} and live.error is None and Probe.calls == []
        assert live.controller.pauseSequence == 1
    finally:
        live.close()


def testNestedForEachKeepsDistinctIterationPathsAndOutputs():
    from tests.runtime.test_workflow_loop_contracts_v2 import _foreachPayload, _MapItemOperator
    payload = _foreachPayload()
    registry = {"test.map_item": _MapItemOperator}
    # Resolve the fixture's registered operator identity directly from its draft.
    node = payload["workflows"]["body"]["nodes"][1]
    registry = {node["operatorId"]: _MapItemOperator}
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry)
    live = LiveRun(runner, {"items": ["a", "b"], "prefix": "p"})
    try:
        first = live.paused()
        live.command("breakpoints", breakpoints=[dict(workflowId="body", nodeId=node["nodeId"])])
        live.command("over", first)
        a = live.paused()
        live.command("continue", a)
        b = live.paused()
        assert a["identity"]["iterationPath"] == (0,) and b["identity"]["iterationPath"] == (1,)
        assert a["identity"]["workflowRunId"] != b["identity"]["workflowRunId"]
        live.command("continue", b)
        live.thread.join(5)
        assert live.error is None and live.result.outputs["edge"] == ["pa", "pb"]
    finally:
        live.close()
