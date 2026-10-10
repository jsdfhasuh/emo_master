from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from emo_master.apps.runtime.context.global_variables import ReadOnlyVariables
from emo_master.apps.runtime.execution.operator_executor import OperatorExecutor
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError as LegacyExecutionError
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.workflow.models import CompiledEdge, CompiledNode, CompiledProject, CompiledWorkflow
from emo_master.plugins.builtins.number_compare.operator import NumberCompareOperator
from emo_master.plugins.builtins.threshold.operator import ThresholdOperator


def nodeFor(operator, *, nodeId="node", **changes):
    meta = operator.meta
    node = CompiledNode(
        nodeId, "operator", meta.operatorId,
        dict(meta.inputPorts), dict(meta.outputPorts), {},
        paramSchema=dict(meta.paramSchema),
    )
    return replace(node, **changes)


def projectFor(node, *, outputPorts=None):
    outputs = dict(node.outputPorts if outputPorts is None else outputPorts)
    start = CompiledNode("input", "workflow_input", None, {}, dict(node.inputPorts), {})
    end = CompiledNode("output", "workflow_output", None, outputs, {}, {})
    inputs = tuple(CompiledEdge("input", port, node.nodeId, port) for port in node.inputPorts)
    outputsEdges = tuple(CompiledEdge(node.nodeId, port, "output", port) for port in outputs)
    workflow = CompiledWorkflow(
        "main", "Main", dict(node.inputPorts), outputs,
        (start, node, end), inputs + outputsEdges,
        {item.nodeId: item for item in (start, node, end)},
        {node.nodeId: inputs, "output": outputsEdges},
        {"input": inputs, node.nodeId: outputsEdges},
        ("input", node.nodeId, "output"),
    )
    return CompiledProject("project", 1, "main", {"main": workflow}, {"main": ()}, {})


def context(nodeId="node"):
    return RunContext.root("owner", "main", projectId="project").forNode(nodeId)


def execute(executor, node, values):
    return executor.execute(node, values, context(node.nodeId), CancellationToken())


def testErrorReexportPreservesExistingCatchSites():
    assert LegacyExecutionError is WorkflowExecutionError


@pytest.mark.parametrize("right,expected", [(0, True), (5, False)])
def testIndependentNumberInvocationMatchesFullWorkflow(right, expected):
    node = nodeFor(NumberCompareOperator, params={"operator": "gte", "rightValue": right})
    registry = {node.operatorId: NumberCompareOperator}
    direct = OperatorExecutor(registry)
    outputs, metrics, diagnostics = execute(direct, node, {"left": 3})
    formal = WorkflowRunner(projectFor(node), registry).run(
        "main", {"left": 3}, RunContext.root("owner", "main", projectId="project"), CancellationToken()
    )
    assert outputs == formal.outputs == {"result": expected}
    assert metrics.keys() == formal.metrics.keys()
    assert diagnostics == formal.diagnostics
    direct.closeSession(context())


def testIndependentImageInvocationMatchesFullWorkflow():
    image = np.arange(64, dtype=np.uint8).reshape(8, 8)
    node = nodeFor(ThresholdOperator, params={"threshold": 30, "mode": "fixed"})
    registry = {node.operatorId: ThresholdOperator}
    executor = OperatorExecutor(registry)
    direct, _, _ = execute(executor, node, {"image": image.copy()})
    formal = WorkflowRunner(projectFor(node, outputPorts={"mask": "image"}), registry).run(
        "main", {"image": image.copy()}, RunContext.root("owner", "main"), CancellationToken()
    )
    np.testing.assert_array_equal(direct["mask"], formal.outputs["mask"])
    executor.closeSession(context())


class Echo:
    meta = SimpleNamespace(
        operatorId="test.echo", inputPorts={"value": {"type": "integer", "required": True}},
        outputPorts={"value": {"type": "integer", "required": True}},
        paramSchema={},
    )

    def executeNode(self, inputs, params, runtimeContext):
        return {"status": "ok", "outputs": dict(inputs), "metrics": {"calls": 1}}


@pytest.mark.parametrize(
    "inputs,code",
    [({}, "E_INPUT_MISSING"), ({"value": True}, "E_INPUT_TYPE"), ({"value": None}, "E_INPUT_TYPE")],
)
def testInvalidInputsFailBeforeConstruction(inputs, code):
    constructed = []

    class Probe(Echo):
        def __init__(self):
            constructed.append(True)

    node = nodeFor(Probe)
    executor = OperatorExecutor({node.operatorId: Probe})
    with pytest.raises(WorkflowExecutionError) as failure:
        execute(executor, node, inputs)
    assert failure.value.code == code
    assert not constructed


@pytest.mark.parametrize(
    "value,ports,code",
    [
        ([], {"value": "integer"}, "E_OUTPUT_TYPE"),
        ({"other": 2}, {"value": "integer"}, "E_OUTPUT_UNDECLARED"),
        ({1: 2}, {"value": "integer"}, "E_OUTPUT_UNDECLARED"),
        ({}, {"value": {"type": "integer", "required": True}}, "E_OUTPUT_MISSING"),
        ({"value": True}, {"value": "integer"}, "E_OUTPUT_TYPE"),
    ],
)
def testOutputValidationHasSameErrorInDirectAndFormalExecution(value, ports, code):
    class Bad(Echo):
        def executeNode(self, inputs, params, runtimeContext):
            return {"status": "ok", "outputs": value}

    node = nodeFor(Bad, outputPorts=ports)
    registry = {node.operatorId: Bad}
    direct = OperatorExecutor(registry)
    with pytest.raises(WorkflowExecutionError) as standalone:
        execute(direct, node, {"value": 3})
    with pytest.raises(WorkflowExecutionError) as workflow:
        WorkflowRunner(projectFor(node), registry).run(
            "main", {"value": 3}, RunContext.root("owner", "main"), CancellationToken()
        )
    assert standalone.value.code == workflow.value.code == code
    assert str(standalone.value) == str(workflow.value)
    assert standalone.value.nodeId == workflow.value.nodeId == node.nodeId


def testOptionalBranchOutputMayBeAbsent():
    node = nodeFor(Echo, outputPorts={"value": "integer", "other": "integer"})
    assert execute(OperatorExecutor({node.operatorId: Echo}), node, {"value": 2})[0] == {"value": 2}


def testEffectiveVariableParametersUseInjectedSnapshotAndPreserveLiteral():
    node = nodeFor(
        NumberCompareOperator,
        params={"operator": "gte", "rightValue": 999},
        globalVariableBindings=({"parameterPath": ["rightValue"], "variableId": "threshold"},),
    )
    variables = ReadOnlyVariables({"threshold": 2})
    executor = OperatorExecutor({node.operatorId: NumberCompareOperator}, globalVariables=variables)
    assert execute(executor, node, {"left": 3})[0] == {"result": True}
    assert node.params["rightValue"] == 999
    assert variables.get("threshold") == 2


def testMissingBindingDoesNotUseLiteralFallback():
    node = nodeFor(
        NumberCompareOperator,
        params={"rightValue": 0},
        globalVariableBindings=({"parameterPath": ["rightValue"], "variableId": "missing"},),
    )
    with pytest.raises(Exception) as failure:
        execute(OperatorExecutor({node.operatorId: NumberCompareOperator}), node, {"left": 3})
    assert getattr(failure.value, "code", "") == "E_VARIABLE_UNKNOWN"


def testInjectedServicesAndBindingValuesReachOperatorUnchanged():
    services = [object(), object(), object()]
    observed = []

    class Probe(Echo):
        def executeNode(self, inputs, params, runtimeContext):
            observed.append(runtimeContext)
            return super().executeNode(inputs, params, runtimeContext)

    node = nodeFor(Probe)
    executor = OperatorExecutor(
        {node.operatorId: Probe},
        globalVariables=services[0], globalCounters=services[1], coordinateSnapshots=services[2],
    )
    call = context()
    executor.execute(node, {"value": 1}, call, CancellationToken(), {4: {"frozen": "value"}})
    runtime = observed[0]
    assert runtime["globalVariables"] is services[0]
    assert runtime["globalCounters"] is services[1]
    assert runtime["coordinateSnapshots"] is services[2]
    assert runtime["mappedOutputs"] == {4: {"frozen": "value"}}
    assert runtime["nodeRunId"] == call.nodeRunId
    assert runtime["workflowRunId"] == call.workflowRunId


def testLifecycleCacheUsesNodeAndWorkflowAndDisposesInReverseOrder():
    calls = []

    class Stateful(Echo):
        def initOperator(self, runtime):
            self.identity = (runtime["workflowId"], runtime["nodeId"])
            calls.append(("init", self.identity))

        def executeNode(self, inputs, params, runtime):
            calls.append(("execute", self.identity))
            return super().executeNode(inputs, params, runtime)

        def disposeOperator(self):
            calls.append(("dispose", self.identity))

    node = nodeFor(Stateful)
    executor = OperatorExecutor({node.operatorId: Stateful})
    token = CancellationToken()
    executor.execute(node, {"value": 1}, context().forIteration(0), token)
    executor.execute(node, {"value": 2}, context().forIteration(1), token)
    second = replace(node, nodeId="second")
    executor.execute(second, {"value": 3}, context("second"), token)
    executor.execute(node, {"value": 4}, RunContext.root("owner", "child").forNode("node"), token)
    executor.closeSession(context())
    assert [item[1] for item in calls if item[0] == "init"] == [
        ("main", "node"), ("main", "second"), ("child", "node")
    ]
    assert [item[1] for item in calls if item[0] == "dispose"] == [
        ("child", "node"), ("main", "second"), ("main", "node")
    ]
    executor.closeSession(context())
    assert len([item for item in calls if item[0] == "dispose"]) == 3


def testDisposeOnlyLifecycleRetainsInstance():
    class Stateful(Echo):
        def __init__(self):
            self.count = 0

        def executeNode(self, inputs, params, runtime):
            self.count += 1
            return {"status": "ok", "outputs": {"value": self.count}}

        def disposeOperator(self):
            self.count = 0

    node = nodeFor(Stateful)
    executor = OperatorExecutor({node.operatorId: Stateful})
    assert execute(executor, node, {"value": 0})[0] == {"value": 1}
    assert execute(executor, node, {"value": 0})[0] == {"value": 2}
    assert executor.disposeOperators() is None
    assert execute(executor, node, {"value": 0})[0] == {"value": 1}
    executor.closeSession(context())


def testOrdinaryClassesAreNotCachedButRegisteredInstancesRemainSupported():
    built = []

    class Ordinary(Echo):
        def __init__(self):
            built.append(self)

    node = nodeFor(Ordinary)
    executor = OperatorExecutor({node.operatorId: Ordinary})
    execute(executor, node, {"value": 1})
    execute(executor, node, {"value": 2})
    assert len(built) == 2
    existing = Ordinary()
    executor = OperatorExecutor({node.operatorId: existing})
    execute(executor, node, {"value": 3})
    assert len(built) == 3


def testCancelledInvocationAndSystemNodeCannotConstructOperator():
    built = []

    class Probe(Echo):
        def __init__(self):
            built.append(True)

    node = nodeFor(Probe)
    executor = OperatorExecutor({node.operatorId: Probe})
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancellationRequested):
        executor.execute(node, {"value": 1}, context(), token)
    with pytest.raises(WorkflowExecutionError, match="requires an operator"):
        executor.execute(replace(node, kind="loop"), {}, context(), CancellationToken())
    assert not built


def testInitFailureKeepsPrimaryAndCleanupDiagnostics():
    events = []

    class Broken(Echo):
        def initOperator(self, runtime):
            raise ValueError("init failed")

        def disposeOperator(self):
            raise RuntimeError("dispose failed")

    node = nodeFor(Broken)
    executor = OperatorExecutor({node.operatorId: Broken}, eventPublisher=lambda **event: events.append(event))
    with pytest.raises(ValueError, match="init failed") as failure:
        execute(executor, node, {"value": 1})
    assert failure.value.diagnostics["resourceCleanup"]["code"] == "E_RESOURCE_CLEANUP_FAILED"
    assert len([event for event in events if event["eventType"] == "resource.cleanup.failed"]) == 1
    assert executor.disposeOperators() is None


def testCloseCleanupFailureOverridesCancellationButKeepsOtherPrimaryError():
    class Broken(Echo):
        def disposeOperator(self):
            raise RuntimeError("held resource")

    node = nodeFor(Broken)
    executor = OperatorExecutor({node.operatorId: Broken})
    execute(executor, node, {"value": 1})
    with pytest.raises(WorkflowExecutionError) as failure:
        executor.closeSession(context(), CancellationRequested("stop"))
    assert failure.value.code == "E_RESOURCE_CLEANUP_FAILED"
    executor = OperatorExecutor({node.operatorId: Broken})
    execute(executor, node, {"value": 1})
    primary = ValueError("original")
    executor.closeSession(context(), primary)
    assert primary.diagnostics["resourceCleanup"]["code"] == "E_RESOURCE_CLEANUP_FAILED"


def testOperatorFailurePreservesDiagnosticsAndClosesLogger():
    events, loggers = [], []

    class Failure(Echo):
        def executeNode(self, inputs, params, runtime):
            loggers.append(runtime["logger"])
            runtime["logger"].info("during execution")
            return {
                "status": "error", "error": {"code": "E_EXPECTED", "message": "expected"},
                "metrics": {"calls": 1}, "diagnostics": {"receipt": {"status": "UNKNOWN"}},
            }

    node = nodeFor(Failure)
    executor = OperatorExecutor({node.operatorId: Failure}, eventPublisher=lambda **event: events.append(event))
    with pytest.raises(WorkflowExecutionError) as failure:
        execute(executor, node, {"value": 1})
    assert failure.value.code == "E_EXPECTED"
    assert failure.value.metrics == {"calls": 1}
    assert failure.value.diagnostics["receipt"]["status"] == "UNKNOWN"
    assert [event["message"] for event in events] == ["during execution"]
    before = len(events)
    loggers[0].info("late")
    assert len(events) == before


def testSqliteServiceInjectionAndReceiptEventUseInvocationIdentity():
    observed, events = [], []

    class SqliteProbe(Echo):
        def executeNode(self, inputs, params, runtime):
            observed.append(runtime)
            runtime["publishSqliteWrite"]("sqlite.write.finished", {"status": "COMMITTED"})
            return super().executeNode(inputs, params, runtime)

    node = nodeFor(SqliteProbe, operatorId="vision.io.sqlite_writer")
    executor = OperatorExecutor({node.operatorId: SqliteProbe}, eventPublisher=lambda **event: events.append(event))
    token, call = CancellationToken(), context()
    executor.execute(node, {"value": 1}, call, token)
    assert callable(observed[0]["sqliteInsert"])
    assert observed[0]["sqliteCancelled"]() is False
    token.cancel()
    assert observed[0]["sqliteCancelled"]() is True
    assert events[0]["context"] is call
    assert events[0]["payload"] == {"receipt": {"status": "COMMITTED"}}


def testDescriptorAndTypedMultiPortExecutionWithoutCompiledProject():
    class Multi:
        def executeNode(self, inputs, params, runtime):
            return {"status": "ok", "outputs": {
                "total": inputs["left"] + inputs["right"],
                "values": inputs["values"], "enabled": inputs["enabled"],
            }}

    node = CompiledNode("multi", "operator", "test.multi",
        {"left": "number", "right": "number", "values": "list<integer>", "enabled": "boolean"},
        {"total": "number", "values": "list<integer>", "enabled": "boolean"}, {})
    executor = OperatorExecutor({"test.multi": SimpleNamespace(operatorClass=Multi)})
    inputs = {"left": 2, "right": 3, "values": [4, 5], "enabled": False}
    assert execute(executor, node, inputs)[0] == {"total": 5, "values": [4, 5], "enabled": False}


def testThrownExceptionClosesExecutionLogger():
    events, captured = [], []

    class Throws(Echo):
        def executeNode(self, inputs, params, runtime):
            captured.append(runtime["logger"])
            runtime["logger"].info("before failure")
            raise ValueError("operator failed")

    node = nodeFor(Throws)
    executor = OperatorExecutor({node.operatorId: Throws}, eventPublisher=lambda **event: events.append(event))
    with pytest.raises(ValueError, match="operator failed"):
        execute(executor, node, {"value": 1})
    before = len(events)
    captured[0].info("late")
    assert len(events) == before


@pytest.mark.parametrize("staleReceipt", [False, True])
def testOutputFailureRetainsOnlyThisInvocationSqliteReceipt(staleReceipt):
    class Committed(Echo):
        def executeNode(self, inputs, params, runtime):
            identity = {key: runtime[key] for key in (
                "jobId", "workflowId", "workflowRunId", "nodeId", "nodeRunId"
            )}
            if staleReceipt:
                identity["nodeRunId"] = "other-invocation"
            receipt = {"writeId": "write-1", "status": "COMMITTED", "rowsAffected": 1,
                       "primaryKey": 1, "execution": identity}
            return {"status": "ok", "outputs": {"value": "invalid-output"},
                    "diagnostics": {"sqliteReceipt": receipt}}

    node = nodeFor(Committed, operatorId="vision.io.sqlite_writer")
    executor = OperatorExecutor({node.operatorId: Committed})
    with pytest.raises(WorkflowExecutionError) as failure:
        execute(executor, node, {"value": 1})
    assert failure.value.code == "E_OUTPUT_TYPE"
    assert ("sqliteReceipt" in failure.value.diagnostics) is not staleReceipt
    if not staleReceipt:
        assert failure.value.diagnostics["sqliteReceipt"]["status"] == "COMMITTED"


@pytest.mark.parametrize("hasPublisher", [False, True])
def testDefaultLoggerAdaptsPublisherAndPreservesDisabledState(hasPublisher):
    events, enabled = [], []

    class Logs(Echo):
        def initOperator(self, runtime):
            runtime["logger"].info("initialized")

        def executeNode(self, inputs, params, runtime):
            enabled.append(runtime["logger"].isEnabledFor("INFO"))
            runtime["logger"].info("executed")
            return super().executeNode(inputs, params, runtime)

    node = nodeFor(Logs)
    publisher = (lambda **event: events.append(event)) if hasPublisher else None
    executor = OperatorExecutor({node.operatorId: Logs}, eventPublisher=publisher)
    call = context()
    _, _, diagnostics = executor.execute(node, {"value": 1}, call, CancellationToken())
    executor.closeSession(call)
    assert enabled == [hasPublisher]
    assert diagnostics == {}
    assert [event["message"] for event in events] == (
        ["initialized", "executed"] if hasPublisher else []
    )
    assert all(event["context"] is call for event in events)
    assert all(event["eventType"] == "node.log" for event in events)


def testCancellationDuringInvocationIsNotReturnedAsSuccess():
    calls, token = [], CancellationToken()

    class Cancels(Echo):
        def executeNode(self, inputs, params, runtime):
            calls.append("execute")
            token.cancel()
            return super().executeNode(inputs, params, runtime)

        def disposeOperator(self):
            calls.append("dispose")

    node = nodeFor(Cancels)
    executor = OperatorExecutor({node.operatorId: Cancels})
    with pytest.raises(CancellationRequested):
        executor.execute(node, {"value": 1}, context(), token)
    executor.closeSession(context())
    assert calls == ["execute", "dispose"]


@pytest.mark.parametrize("staleReceipt", [False, True])
def testCancellationAfterReturnRetainsOnlyThisInvocationSqliteReceipt(staleReceipt):
    token = CancellationToken()

    class Committed(Echo):
        def executeNode(self, inputs, params, runtime):
            identity = {key: runtime[key] for key in (
                "jobId", "workflowId", "workflowRunId", "nodeId", "nodeRunId"
            )}
            if staleReceipt:
                identity["nodeRunId"] = "other-invocation"
            receipt = {"writeId": "write-1", "status": "COMMITTED", "rowsAffected": 1,
                       "primaryKey": 1, "execution": identity}
            token.cancel()
            return {"status": "ok", "outputs": {"value": 1},
                    "diagnostics": {"sqliteReceipt": receipt}}

    node = nodeFor(Committed, operatorId="vision.io.sqlite_writer")
    executor = OperatorExecutor({node.operatorId: Committed})
    with pytest.raises(CancellationRequested) as failure:
        executor.execute(node, {"value": 1}, context(), token)
    assert ("sqliteReceipt" in failure.value.diagnostics) is not staleReceipt
    if not staleReceipt:
        assert failure.value.diagnostics["sqliteReceipt"]["status"] == "COMMITTED"
    executor.closeSession(context(), failure.value)
