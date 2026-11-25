"""Deliver SQLite outcomes on the execution thread under their original node identity."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

from emo_master.apps.runtime.execution.errors import WorkflowExecutionError

if TYPE_CHECKING:
    from emo_master.apps.runtime.workflow.context import RunContext


_MESSAGES = {"COMMITTED": "SQLite 后台写入已入库", "FAILED": "SQLite 后台写入失败",
             "UNKNOWN": "SQLite 后台提交结果未知", "CANCELLED": "SQLite 后台写入已取消"}


@dataclass
class _Pending:
    context: RunContext
    receipt: dict
    failurePolicy: str
    queuedAt: float


class BackgroundSqliteReceipts:
    def __init__(self, publish):
        self.publish = publish
        self.pending: dict[str, _Pending] = {}

    def register(self, service, context, taskId, receipt, failurePolicy):
        # A completed task may outlive the service's bounded recent history.
        service.pin(taskId)
        self.pending[taskId] = _Pending(context, deepcopy(receipt), failurePolicy, time.monotonic())

    def poll(self, service, *, raiseOnError=True):
        if not self.pending:
            return []
        completed = []
        firstFailure = None
        for taskId, entry in tuple(self.pending.items()):
            state = service.status(taskId)
            if state in {"QUEUED", "RUNNING"}:
                continue
            try:
                receipt = self._result(service, taskId, entry, state)
                status = receipt["status"]
                error = receipt.get("cleanupError") or receipt.get("error") or {}
                message = _MESSAGES[status]
                if error.get("message") and status != "CANCELLED":
                    message += "：" + error["message"]
                level = "ERROR" if error else "INFO"
                if status == "CANCELLED":
                    level = "WARN"
                self.publish("sqlite.write.finished", entry.context, message, level=level,
                             code=error.get("code", ""), payload={"background": True, "receipt": receipt})
                completed.append(receipt)
                if level == "ERROR" and entry.failurePolicy == "stop" and firstFailure is None:
                    source = dict(receipt["execution"], taskId=taskId, taskName="sqlite.insert")
                    firstFailure = WorkflowExecutionError(error["code"], message, entry.context.callerNodeId,
                        diagnostics={"sqliteReceipt": receipt, "backgroundTask": source})
            finally:
                del self.pending[taskId]
                service.release(taskId)
        if firstFailure is not None and raiseOnError:
            raise firstFailure
        return completed

    @staticmethod
    def _result(service, taskId, entry, state):
        if state != "CANCELLED":
            try:
                return service.wait(taskId)
            except WorkflowExecutionError as error:
                # The task did not return a SQLite outcome. Its commit is unproven.
                status, rows, code, message = "UNKNOWN", None, "E_SQLITE_UNKNOWN", str(error)
        else:
            status, rows, code, message = "CANCELLED", 0, "E_CANCELLED", "后台写入已取消"
        elapsed = (time.monotonic() - entry.queuedAt) * 1000 + entry.receipt["elapsedMs"]
        return dict(entry.receipt, status=status, rowsAffected=rows,
                    primaryKey=None, elapsedMs=elapsed, error={"code": code, "message": message})
