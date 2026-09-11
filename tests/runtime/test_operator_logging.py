from __future__ import annotations

import json
import threading

import pytest

from emo_master.apps.runtime.events.operator_logger import OperatorLogManager
from emo_master.apps.runtime.workflow.cancellation import (
    CancellationRequested,
    CancellationToken,
)
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.operator_logging import (
    NullOperatorLogger,
    getOperatorLogger,
)
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


def _publisher(events: list[dict[str, object]]):
    def publish(eventType, context, message, **values) -> None:
        events.append(
            {
                "eventType": eventType,
                "context": context,
                "message": message,
                **values,
            }
        )

    return publish


def _project(operatorId: str) -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "logging-project",
                "name": "logging",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {},
                    "outputs": {},
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "logged",
                            "kind": "operator",
                            "operatorId": operatorId,
                            "inputPorts": {},
                            "outputPorts": {},
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [],
                }
            },
        }
    )


class _FullLifecycleLoggingOperator:
    lifecycleLogger = None
    executeLogger = None

    def initOperator(self, context) -> None:
        type(self).lifecycleLogger = getOperatorLogger(context)
        self.lifecycleLogger.info("initialized")

    def executeNode(self, inputs, params, context):
        _ = inputs, params
        type(self).executeLogger = getOperatorLogger(context)
        self.executeLogger.info("executed")
        return {"status": "ok", "outputs": {}}

    def disposeOperator(self) -> None:
        self.lifecycleLogger.info("disposed")


class _LoopLoggingOperator:
    def executeNode(self, inputs, params, context):
        _ = params
        getOperatorLogger(context).info(
            "loop item",
            payload={"item": inputs["item"]},
        )
        return {"status": "ok", "outputs": {"value": inputs["item"]}}


class _FailingLoggingOperator:
    def executeNode(self, inputs, params, context):
        _ = inputs, params
        getOperatorLogger(context).error("operator will fail", code="E_EXPECTED")
        return {
            "status": "error",
            "error": {"code": "E_EXPECTED", "message": "expected failure"},
        }


class _CancellingLoggingOperator:
    def executeNode(self, inputs, params, context):
        _ = inputs, params
        getOperatorLogger(context).warning("operator cancelled")
        raise CancellationRequested("cancelled")


def testRunnerInjectsExecuteAndLifecycleLoggersWithCorrelationAndOrdering() -> None:
    events: list[dict[str, object]] = []
    registry = {"test.logging": _FullLifecycleLoggingOperator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        _project("test.logging")
    )
    runner = WorkflowRunner(
        compiled,
        registry,
        eventPublisher=lambda **event: events.append(event),
    )

    runner.run(
        "main",
        {},
        RunContext.root(
            "job-1", "main", workspacePath="workspace", projectId="project-1"
        ),
        CancellationToken(),
    )

    nodeEvents = [
        event
        for event in events
        if getattr(event["context"], "callerNodeId", "") == "logged"
    ]
    eventTypes = [str(event["eventType"]) for event in nodeEvents]
    assert eventTypes == [
        "node.started",
        "node.log",
        "node.log",
        "node.completed",
        "node.log",
    ]
    logEvents = [event for event in nodeEvents if event["eventType"] == "node.log"]
    assert [event["payload"]["phase"] for event in logEvents] == [
        "lifecycle",
        "execute",
        "lifecycle",
    ]
    assert {event["payload"]["operatorId"] for event in logEvents} == {
        "test.logging"
    }
    assert all(getattr(event["context"], "jobId") == "job-1" for event in logEvents)
    assert all(
        getattr(event["context"], "nodeRunId") != "" for event in logEvents
    )

    beforeLate = len(events)
    assert _FullLifecycleLoggingOperator.executeLogger is not None
    assert _FullLifecycleLoggingOperator.lifecycleLogger is not None
    _FullLifecycleLoggingOperator.executeLogger.info("late execute")
    _FullLifecycleLoggingOperator.lifecycleLogger.info("late lifecycle")
    assert len(events) == beforeLate


def testLoggerSanitizesSecretsCyclesBinaryAndNonFiniteValues() -> None:
    events: list[dict[str, object]] = []
    manager = OperatorLogManager(
        _publisher(events),
        minimumLevel="DEBUG",
        burst=100,
    )
    context = RunContext.root("job", "main", projectId="project").forNode("node")
    logger = manager.createLogger(context, "test.operator", "execute")
    cycle: dict[str, object] = {}
    cycle["self"] = cycle

    logger.info(
        "坐标" * 3000,
        payload={
            "password": "hidden",
            "nested": {"apiKey": "hidden", "ok": 3},
            "bytes": b"abc",
            "nan": float("nan"),
            "cycle": cycle,
        },
    )
    logger.close()

    assert len(events) == 1
    event = events[0]
    assert len(str(event["message"]).encode("utf-8")) <= 4096
    payload = event["payload"]
    assert payload["truncated"] is True
    data = payload["data"]
    assert data["password"] == "<redacted>"
    assert data["nested"] == {"apiKey": "<redacted>", "ok": 3}
    assert data["bytes"] == "<bytes:3>"
    assert data["nan"] == "<non-finite>"
    assert data["cycle"]["self"] == "<cycle>"
    json.dumps(payload, allow_nan=False)


def testLoopAndSubflowLogsKeepIterationAndParentRunCorrelation() -> None:
    from tests.runtime.test_operator_lifecycle import _loopProject

    events: list[dict[str, object]] = []
    registry = {"test.loop.logging": _LoopLoggingOperator}
    document = ProjectDocument.model_validate(_loopProject("test.loop.logging"))
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    WorkflowRunner(
        compiled,
        registry,
        eventPublisher=lambda **event: events.append(event),
    ).run(
        "main",
        {"items": [4, 5]},
        RunContext.root("loop-job", "main", projectId="project"),
        CancellationToken(),
    )

    logs = [event for event in events if event["eventType"] == "node.log"]
    contexts = [event["context"] for event in logs]
    assert [context.iterationPath for context in contexts] == [(0,), (1,)]
    assert all(context.workflowId == "body" for context in contexts)
    assert all(context.parentWorkflowRunId != "" for context in contexts)
    assert len({context.nodeRunId for context in contexts}) == 2


@pytest.mark.parametrize(
    ("operatorId", "operatorClass", "errorType"),
    [
        ("test.logging.failure", _FailingLoggingOperator, RuntimeError),
        ("test.logging.cancel", _CancellingLoggingOperator, CancellationRequested),
    ],
)
def testFailureAndCancellationLogsPrecedeNodeTerminalEvent(
    operatorId: str,
    operatorClass: type,
    errorType: type[BaseException],
) -> None:
    events: list[dict[str, object]] = []
    registry = {operatorId: operatorClass}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        _project(operatorId)
    )
    runner = WorkflowRunner(
        compiled,
        registry,
        eventPublisher=lambda **event: events.append(event),
    )

    with pytest.raises(errorType):
        runner.run(
            "main",
            {},
            RunContext.root("job", "main"),
            CancellationToken(),
        )

    nodeTypes = [
        event["eventType"]
        for event in events
        if getattr(event["context"], "callerNodeId", "") == "logged"
    ]
    assert nodeTypes == ["node.started", "node.log", "node.failed"]


def testLoggerRateLimitAndJobLimitProduceOneDroppedSummary() -> None:
    events: list[dict[str, object]] = []
    manager = OperatorLogManager(
        _publisher(events),
        minimumLevel="DEBUG",
        ratePerSecond=0.1,
        burst=1,
        maxEvents=2,
        clock=lambda: 0.0,
    )
    logger = manager.createLogger(
        RunContext.root("job", "main").forNode("node"),
        "test.operator",
        "execute",
    )

    logger.info("first")
    logger.info("rate-limited")
    logger.warning("warning bypasses token bucket")
    logger.error("job-limited")
    diagnostics = logger.close()

    assert [event["level"] for event in events] == ["INFO", "WARN", "WARN"]
    assert events[-1]["code"] == "W_OPERATOR_LOG_DROPPED"
    assert events[-1]["payload"]["data"]["dropped"] == {
        "INFO": 1,
        "ERROR": 1,
    }
    assert diagnostics["suppressed"] == 2


def testJobHardLimitCannotBeBypassedByCreatingMoreNodeLoggers() -> None:
    events: list[dict[str, object]] = []
    manager = OperatorLogManager(
        _publisher(events),
        minimumLevel="DEBUG",
        burst=100,
        maxEvents=1,
    )
    context = RunContext.root("job", "main")
    first = manager.createLogger(context.forNode("first"), "test.first", "execute")
    first.info("accepted")
    first.close()
    for index in range(20):
        logger = manager.createLogger(
            context.forNode(f"node-{index}"),
            f"test.{index}",
            "execute",
        )
        logger.error("dropped")
        logger.close()
    manager.finalize()

    summaries = [
        event
        for event in events
        if event["code"] == "W_OPERATOR_LOG_DROPPED"
    ]
    assert len(summaries) == 2
    assert sum(
        int(event["payload"]["data"]["dropped"]["ERROR"])
        for event in summaries
    ) == 20
    beforeSecondFinalize = len(events)
    manager.finalize()
    assert len(events) == beforeSecondFinalize


def testLoggerIsThreadSafeAndNeverRaisesOnPublisherOrBadValues() -> None:
    class BadString:
        def __str__(self) -> str:
            raise RuntimeError("bad string")

    manager = OperatorLogManager(
        lambda **event: (_ for _ in ()).throw(RuntimeError("publisher failed")),
        minimumLevel="DEBUG",
        burst=1000,
    )
    logger = manager.createLogger(
        RunContext.root("job", "main").forNode("node"),
        "test.operator",
        "execute",
    )
    threads = [threading.Thread(target=logger.info, args=(f"log-{index}",)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    logger.info(BadString())  # type: ignore[arg-type]
    diagnostics = logger.close()

    assert int(diagnostics["publishFailures"]) >= 20
    assert logger.isEnabledFor("INFO") is False
    assert logger.isEnabledFor(BadString()) is False  # type: ignore[arg-type]


def testGetOperatorLoggerFallsBackToNoOpLogger() -> None:
    logger = getOperatorLogger({})
    assert isinstance(logger, NullOperatorLogger)
    assert logger.isEnabledFor("INFO") is False
    logger.info("ignored", payload={"token": "ignored"})
