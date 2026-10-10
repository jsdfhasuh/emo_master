"""Unconfirmed writes become UNKNOWN before the terminal Job event is visible."""
from copy import deepcopy
import json


class SqliteOutcomes:
    def __init__(self, events):
        self.events = events
        self.pending = {}

    def observe(self, event):
        # Called under EventStore's existing append lock, no second mutable pool.
        if event.eventType not in {"sqlite.write.started", "sqlite.write.finished"}:
            return
        receipt = json.loads(event.payloadJson).get("receipt", {})
        key = (event.jobId, receipt.get("writeId"))
        if event.eventType == "sqlite.write.started":
            self.pending[key] = (event, receipt)
        else:
            self.pending.pop(key, None)

    def beforeTerminal(self, jobId):
        for key, (origin, receipt) in tuple(self.pending.items()):
            if key[0] != jobId:
                continue
            del self.pending[key]
            unknown = deepcopy(receipt)
            unknown.update(status="UNKNOWN", rowsAffected=None, error={"code": "E_SQLITE_UNKNOWN",
                "message": "工作进程退出，未收到写入回执；提交结果未知，不自动重复 INSERT"})
            self.events.append(jobId, "node.failed", unknown["error"]["message"], level="ERROR",
                code="E_SQLITE_UNKNOWN", nodeId=origin.nodeId, projectId=origin.projectId,
                workflowId=origin.workflowId, workflowRunId=origin.workflowRunId,
                parentWorkflowRunId=origin.parentWorkflowRunId, nodeRunId=origin.nodeRunId,
                iterationPath=tuple(json.loads(origin.iterationPathJson)),
                payload={"status": "FAILED", "code": "E_SQLITE_UNKNOWN", "message": unknown["error"]["message"],
                         "diagnostics": {"sqliteReceipt": unknown}})
