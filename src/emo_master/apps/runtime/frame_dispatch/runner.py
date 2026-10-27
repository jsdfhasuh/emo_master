"""Worker-side built-in node execution; no worker creates other processes."""
import time

from emo_master.apps.runtime.execution.errors import WorkflowExecutionError


class DispatchRunner:
    def __init__(self, devices, publish):
        self.devices = devices
        self.publish = publish
        self.sequence = 0

    def run(self, node, inputs, context, cancellation):
        if self.devices is None or not self.devices.retainFrames:
            raise WorkflowExecutionError("E_DISPATCH_UNAVAILABLE", "Dispatch requires a managed run group", node.nodeId)
        if node.kind == "dispatch":
            self.sequence += 1
            encoded = self.devices.encode(inputs, context)
            while True:
                cancellation.raise_if_cancelled()
                token = self.devices.framework("dispatchSubmit", dispatchSequence=self.sequence,
                    workflowId=context.workflowId, nodeId=node.nodeId, cycleId=context.cycleId,
                    planRevision=context.planRevision, inputs=encoded["value"])
                if token.get("status") != "WAITING":
                    break
                # Only an explicit, unadmitted capacity wait may continue. A
                # failed/unknown IPC call or a partial receipt is never replayed.
                time.sleep(.02)
            skipped = token.get("status") == "SKIPPED" and node.params.get("queueFullPolicy") == "skip"
            self.publish("dispatch.skipped" if skipped else "dispatch.accepted", context,
                         "dispatch skipped: queue full" if skipped else "dispatch admission receipts", payload=token)
            if skipped:
                return {"token": token}, {}, {}
            if not token["accepted"]:
                error = WorkflowExecutionError("E_DISPATCH_PARTIAL", "not all targets accepted; admitted invocations are not replayed", node.nodeId)
                error.diagnostics = {"dispatchToken": token}
                raise error
            return {"token": token}, {}, {}
        token = inputs["token"]
        if token.get("nodeId") != node.params["dispatchNodeId"] or token.get("workflowId") != context.workflowId:
            raise WorkflowExecutionError("E_DISPATCH_IDENTITY", "Join references a different Dispatch", node.nodeId)
        deadline = time.monotonic() + node.params.get("timeoutMs", 30000) / 1000
        while True:
            cancellation.raise_if_cancelled()
            result = self.devices.framework("dispatchJoin", token=token, cycleId=context.cycleId,
                                       planRevision=context.planRevision)
            if result["status"] != "WAITING":
                self.publish("dispatch.joined", context, "dispatch target outcomes", payload=result)
                if result["status"] != "COMPLETED":
                    error = WorkflowExecutionError("E_DISPATCH_FAILED", "one or more consumers did not complete", node.nodeId)
                    error.diagnostics = result
                    raise error
                return {slot: self.devices.decode(v["outputs"]) for slot, v in result["results"].items()}, {}, {}
            if time.monotonic() >= deadline:
                raise WorkflowExecutionError("E_DISPATCH_JOIN_TIMEOUT", "Join timed out; live consumers retain their inputs", node.nodeId)
            time.sleep(.02)
