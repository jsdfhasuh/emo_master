from copy import deepcopy
from dataclasses import asdict
import threading
import time

from emo_master.apps.runtime.operator_debug.contracts import fail
from emo_master.core.project.global_variables import resolveParams, validateEffectiveParams
from .conditions import compileCondition, evaluate


class WorkflowDebugController:
    """Only the Runner confirms pauses; command delivery never suspends a process."""

    def __init__(self, cancellation, variables, publish, *, clock=time.monotonic):
        self.cancellation, self.variables, self.publish = cancellation, variables, publish
        self.clock = clock
        self.condition = threading.Condition(threading.RLock())
        self.state = "RUNNING"
        self.pauseSequence = 0
        self.pausedSeconds = 0.0
        self.pauseStarted = None
        self.mode = "into"
        self.originDepth = 0
        self.originNode = ""
        self.pauseRequested = False
        self.breakpoints = {}
        self.runTo = None
        self.stack = []
        self.current = None
        self.trialRunning = False
        self.failureContext = None

    def activeTime(self):
        with self.condition:
            now = self.clock()
            waiting = now - self.pauseStarted if self.pauseStarted is not None else 0
            return now - self.pausedSeconds - waiting

    def effectiveParams(self, node):
        params = resolveParams(dict(node.params), node.globalVariableBindings,
                               self.variables.readMany([b["variableId"] for b in node.globalVariableBindings]))
        return validateEffectiveParams(params, node.globalVariableBindings, node.paramSchema) if node.kind == "operator" else params

    def setBreakpoints(self, rows, locations):
        if not isinstance(rows, list) or len(rows) > 256:
            fail("E_DEBUG_LIMIT", "at most 256 breakpoints")
        updated = {}
        for row in rows:
            key = (row.get("workflowId"), row.get("nodeId"))
            if key not in locations or key in updated:
                fail("E_DEBUG_CONTEXT_INVALID", "breakpoint target unavailable or duplicated")
            count = row.get("hitCount", 1)
            if type(count) is not int or not 1 <= count <= 1000000 or type(row.get("enabled", True)) is not bool:
                fail("E_DEBUG_CONDITION", "invalid hit count or enabled flag")
            updated[key] = dict(row, tree=compileCondition(row.get("condition", "")),
                                hits=self.breakpoints.get(key, {}).get("hits", 0), hitCount=count)
        with self.condition:
            self.breakpoints = updated

    def command(self, command, locations):
        with self.condition:
            action = command.get("action")
            if action == "breakpoints":
                self.setBreakpoints(command.get("breakpoints"), locations)
                return
            if action == "pause":
                if self.state != "PAUSED":
                    self.pauseRequested = True
                    self.state = "PAUSE_REQUESTED"
                self.publish("state", state=self.state, pauseSequence=self.pauseSequence)
                return
            if action not in {"continue", "into", "over", "out", "runTo"}:
                fail("E_DEBUG_UNSUPPORTED", "unknown workflow control")
            self.requirePause(command.get("pauseSequence"))
            if action == "runTo":
                target = (command.get("workflowId"), command.get("nodeId"))
                if target not in locations:
                    fail("E_DEBUG_CONTEXT_INVALID", "run-to target unavailable")
                self.runTo = target
                action = "continue"
            self.mode = action
            self.originDepth = self.current["depth"]
            self.originNode = self.current["context"].nodeRunId
            self.state = "RUNNING"
            self.publish("state", state=self.state, pauseSequence=self.pauseSequence)
            self.condition.notify_all()

    def requirePause(self, sequence):
        if type(sequence) is not int or self.state != "PAUSED" or sequence != self.pauseSequence or self.trialRunning:
            fail("E_DEBUG_STALE_PAUSE", "pause moved, is not confirmed, or a trial is in progress")

    def before(self, node, inputs, context):
        if node.kind in {"workflow_input", "workflow_output"}:
            return
        params = self.effectiveParams(node)
        self.stack.append(dict(asdict(context), nodeId=node.nodeId, kind=node.kind))
        self.checkpoint("node.before", node, inputs, context, params=params)
        if node.kind == "operator":
            self.publish("active", active=True, identity=asdict(context))

    def after(self, node, inputs, outputs, context):
        if node.kind in {"workflow_input", "workflow_output"}:
            return
        if node.kind == "operator":
            self.publish("active", active=False, identity=asdict(context))
        self.publish("observed", phase="node.after", identity=asdict(context), nodeId=node.nodeId,
                     inputs={}, outputs=outputs, params={}, stack=deepcopy(self.stack), variables=self.variables.values)
        if node.kind in {"subflow", "loop"}:
            self.checkpoint("call.return", node, inputs, context, outputs=outputs)
        self.stack.pop()

    def loopPoint(self, phase, node, inputs, context):
        self.publish("location", phase=phase, identity=asdict(context), nodeId=node.nodeId,
                     condition=inputs.get("condition"))
        self.checkpoint(phase, node, inputs, context, depth=context.callDepth + 1)

    def failed(self, node, inputs, context, error):
        if self.failureContext is not None or getattr(error, "code", "") == "E_CANCELLED":
            return
        self.failureContext = context
        self.publish("observed", phase="node.failed", identity=asdict(context), nodeId=node.nodeId,
                     inputs=inputs, outputs={}, params=dict(node.params), stack=deepcopy(self.stack),
                     variables=self.variables.values, code=getattr(error, "code", "E_EXEC_FAILED"), message=str(error)[:2048])

    def checkpoint(self, phase, node, inputs, context, *, params=None, outputs=None, depth=None):
        self.cancellation.raise_if_cancelled()
        depth = context.callDepth if depth is None else depth
        with self.condition:
            key = (context.workflowId, node.nodeId)
            reason, conditionError = "", ""
            if phase == "node.before":
                breakpoint = self.breakpoints.get(key)
                if breakpoint and breakpoint.get("enabled", True):
                    breakpoint["hits"] += 1
                    try:
                        if breakpoint["hits"] >= breakpoint["hitCount"] and evaluate(breakpoint["tree"],
                                dict(inputs=inputs, params=params or {}, variables=self.variables.values, hits=breakpoint["hits"])):
                            reason = "breakpoint"
                    except ValueError as error:
                        reason, conditionError = "condition-error", str(error)
                if self.runTo == key:
                    reason, self.runTo = "run-to", None
            stepping = phase in {"node.before", "call.return"} and (self.mode == "into" or
                        self.mode == "over" and (depth <= self.originDepth and
                            (phase != "call.return" or context.nodeRunId == self.originNode or depth < self.originDepth)) or
                        self.mode == "out" and depth < self.originDepth)
            if not (reason or stepping or self.pauseRequested):
                return
            pauseReason = reason or ("pause" if self.pauseRequested else "step")
            self.pauseSequence += 1
            self.state, self.pauseRequested = "PAUSED", False
            self.current = dict(node=node, inputs=inputs, context=context, depth=depth, phase=phase, params=params or {})
            self.pauseStarted = self.clock()
            try:
                self.publish("paused", state="PAUSED", pauseSequence=self.pauseSequence,
                    reason=pauseReason, conditionError=conditionError,
                    phase=phase, identity=asdict(context), nodeId=node.nodeId, inputs=inputs, outputs=outputs or {},
                    params=params or {}, stack=deepcopy(self.stack), variables=self.variables.values,
                    hits={"/".join(key): row["hits"] for key, row in self.breakpoints.items()})
                while self.state == "PAUSED":
                    self.cancellation.raise_if_cancelled()
                    self.condition.wait(.05)
            finally:
                self.pausedSeconds += self.clock() - self.pauseStarted
                self.pauseStarted = None
            self.cancellation.raise_if_cancelled()
