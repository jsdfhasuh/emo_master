from copy import deepcopy
from dataclasses import asdict, replace
import importlib
import multiprocessing
import os
import threading
from uuid import uuid4

from emo_master.apps.runtime.operator_debug.assets import DebugAssets, freezeOutputs
from emo_master.apps.runtime.operator_debug.contracts import MAX_REQUEST_BYTES, encode, fail, parse
from emo_master.apps.runtime.operator_debug.data import materialize
from emo_master.apps.runtime.operator_debug.state import DebugCounters, DebugVariables
from emo_master.apps.runtime.operator_debug.worker import _retireOrphanWorkspace, watchParent
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.execution.operator_executor import OperatorExecutor
from emo_master.core.workflow.compiler import WorkflowCompiler
from .controller import WorkflowDebugController


def runWorkflowDebug(specJson, commands, events, cancellation, stop, heartbeat, snapshotAck):
    spec = parse(specJson, MAX_REQUEST_BYTES)
    os.chdir(spec["workspacePath"])
    finished, sendLock = threading.Event(), threading.Lock()
    beat = threading.Thread(target=watchParent,
        args=(spec["workspacePath"], cancellation, stop, heartbeat, finished), daemon=True)
    beat.start()
    runner, executionThread, trialThread = None, None, None
    token = CancellationToken(cancellation)
    variables = DebugVariables(spec["variableDefinitions"])
    snapshots = DebugAssets(spec["workspacePath"])
    runDone = threading.Event()
    runDone.set()

    def send(value):
        raw = encode(value).encode()
        with sendLock:
            events.send_bytes(raw)

    def publish(kind, **value):
        if kind in {"paused", "observed", "trial"}:
            token.raise_if_cancelled()
            snapshots.entries = {key: row for key, row in snapshots.entries.items() if snapshots._path(key).is_file()}
            snapshotId = uuid4().hex
            provenance = dict(kind="debug-output", projectId=spec["projectId"], snapshotId=snapshotId)
            data = dict(value)
            for field in ("inputs", "outputs"):
                inline, assets = freezeOutputs(data.pop(field, {}), snapshots, provenance)
                data[field], data[field + "Assets"] = inline, assets
            data.update(snapshotId=snapshotId, kind="flow_snapshot", snapshotKind=kind)
            snapshotAck.clear()
            send(data)
            # Parent owns eviction; don't delete files before it adopts the snapshot.
            while not snapshotAck.wait(.05):
                token.raise_if_cancelled()
        else:
            send(dict(kind="flow_" + kind, **value))

    def logs(**event):
        context = event.pop("context")
        payload = event.get("payload", {})
        send(dict(kind="log", executionId=context.nodeRunId, event=dict(
            type=event["eventType"], identity=asdict(context), level=event.get("level", "INFO"),
            code=event.get("code", ""), message=str(event.get("message", ""))[:2048],
            status=payload.get("status", ""))))

    controller = WorkflowDebugController(token, variables, publish)
    context = RunContext.root("debug:" + uuid4().hex, spec["workflowId"], spec["workspacePath"], spec["projectId"])

    def execute(command):
        primary = None
        try:
            snapshots.budget = command["outputBudget"]
            inputs = materialize(command["inputs"], DebugAssets(spec["workspacePath"]))
            result = runner.run(spec["workflowId"], inputs, context, token)
            publish("observed", phase="workflow.result", identity=asdict(context), nodeId="", inputs={},
                    outputs=result.outputs, variables=variables.values, stack=[])
            publish("terminal", state="SUCCEEDED")
        except BaseException as error:
            primary = error
            publish("terminal", state="CANCELLED" if getattr(error, "code", "") == "E_CANCELLED" else "FAILED",
                    code=getattr(error, "code", "E_EXEC_FAILED"), message=str(error)[:2048],
                    identity=asdict(controller.failureContext or context))
        finally:
            try:
                runner.closeSession(context, primary)
                if getattr(primary, "diagnostics", {}).get("resourceCleanup"):
                    fail("E_RESOURCE_CLEANUP_FAILED", "workflow operator cleanup failed")
            except BaseException as error:
                send(dict(kind="fault", code="E_RESOURCE_CLEANUP_FAILED", message=str(error)[:2048]))
                stop.set()
            runDone.set()

    def trial(command):
        executor = None
        try:
            current = controller.current
            node, trialContext = current["node"], current["context"]
            params = command.get("params", current["params"])
            from emo_master.core.project.global_variables import validateEffectiveParams
            params = validateEffectiveParams(params, (), node.paramSchema)
            validator = getattr(registry[node.operatorId], "validateParams", None)
            if callable(validator):
                try:
                    invalid = validator(params)
                except TypeError:
                    invalid = validator(registry[node.operatorId](), params)
                if isinstance(invalid, dict):
                    fail(str(invalid.get("code", "E_PARAM_INVALID")), str(invalid.get("message", "invalid trial parameters")))
            clone = DebugVariables(spec["variableDefinitions"], variables.values)
            executor = OperatorExecutor(registry, globalVariables=clone, globalCounters=DebugCounters(clone), eventPublisher=logs)
            trialNode = replace(node, params=params, globalVariableBindings=())
            output, metrics, diagnostics = executor.execute(trialNode, deepcopy(current["inputs"]), trialContext, token)
            executor.closeSession(trialContext)
            executor = None
            publish("trial", status="SUCCEEDED", trialId=command["requestId"], pauseSequence=controller.pauseSequence,
                    identity=asdict(trialContext), nodeId=node.nodeId, inputs={}, outputs=output, params=params,
                    metrics=metrics, diagnostics=diagnostics, variables=clone.values)
        except BaseException as error:
            send(dict(kind="flow_trial_failed", status="FAILED", trialId=command["requestId"],
                      code=getattr(error, "code", "E_EXEC_FAILED"), message=str(error)[:2048]))
            if getattr(error, "code", "") == "E_RESOURCE_CLEANUP_FAILED":
                send(dict(kind="fault", code="E_RESOURCE_CLEANUP_FAILED", message=str(error)[:2048]))
                stop.set()
        finally:
            try:
                if executor is not None:
                    executor.closeSession(context)
            except BaseException as error:
                send(dict(kind="fault", code="E_RESOURCE_CLEANUP_FAILED", message=str(error)[:2048]))
                stop.set()
            with controller.condition:
                controller.trialRunning = False
            publish("trial_finished")

    try:
        registry = {}
        for key, entry in spec["entries"].items():
            module, name = entry["entry"].split(":")
            registry[key] = getattr(importlib.import_module(module), name)
        compiled = WorkflowCompiler(registry).compile(spec["project"])
        runner = WorkflowRunner(compiled, registry, eventPublisher=logs, globalVariables=variables,
                                globalCounters=DebugCounters(variables), debugController=controller, retainOperators=True)
        locations = {(workflow.workflowId, node.nodeId) for workflow in compiled.workflows.values()
                     for node in workflow.nodes if node.kind not in {"workflow_input", "workflow_output"}}
        send(dict(kind="ready"))
        while not stop.is_set():
            if not commands.poll(.05):
                continue
            command = parse(commands.recv_bytes(64 * 1024).decode())
            try:
                action = command.get("action")
                if action == "start":
                    if executionThread is not None:
                        fail("E_DEBUG_STALE_SESSION", "workflow already started; open a new session")
                    runDone.clear()
                    executionThread = threading.Thread(target=execute, args=(command,), daemon=True)
                    executionThread.start()
                elif action == "breakpoints" and executionThread is None:
                    # Pre-start configuration must be adopted before the first
                    # checkpoint, including its condition and hit counter.
                    controller.setBreakpoints(command.get("breakpoints"), locations)
                elif executionThread is None or runDone.is_set():
                    fail("E_DEBUG_STALE_SESSION", "workflow is not active")
                elif action == "trial":
                    with controller.condition:
                        controller.requirePause(command.get("pauseSequence"))
                        current = controller.current
                        entry = spec["entries"].get(current["node"].operatorId, {})
                        if current["phase"] != "node.before" or current["node"].kind != "operator" or entry.get("stateful", True):
                            fail("E_DEBUG_UNSUPPORTED", "trial requires a paused clone-safe pure operator")
                        controller.trialRunning = True
                    publish("trial_started", trialId=command["requestId"])
                    trialThread = threading.Thread(target=trial, args=(command,), daemon=True)
                    trialThread.start()
                else:
                    controller.command(command, locations)
                send(dict(kind="flow_ack", requestId=command["requestId"], status="CONFIRMED"))
            except Exception as error:
                send(dict(kind="flow_ack", requestId=command["requestId"], status="REJECTED",
                          code=getattr(error, "code", "E_DEBUG_CONTEXT_INVALID"), message=str(error)[:2048]))
    except BaseException as error:
        send(dict(kind="fault", code=getattr(error, "code", "E_DEBUG_WORKER_FAILED"), message=str(error)[:2048]))
    finally:
        cancellation.set()
        for thread in (executionThread, trialThread):
            if thread is not None:
                thread.join()  # Parent supervision enforces the cancellation grace.
        send(dict(kind="closed"))
        finished.set()
        beat.join(1)
        commands.close()
        events.close()
        parent = multiprocessing.parent_process()
        if parent is not None and not parent.is_alive():
            _retireOrphanWorkspace(spec["workspacePath"])
