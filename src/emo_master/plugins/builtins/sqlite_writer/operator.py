"""Explicit business-database writer; field sources are compiled dependencies."""
from dataclasses import dataclass
from typing import Any
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import time
from uuid import uuid4

from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, parseConfig, SqliteWriterError, toStorage
from emo_master.core.contracts.operator_logging import getOperatorLogger
from emo_master.core.workflow.parameter_bindings import BoundValue


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


PARAM_SCHEMA: dict[str, Any] = {"type": "object", "properties": {
    "configVersion": {"type": "integer", "default": 1},
    "databasePath": {"type": "string", "default": ""},
    "debugDatabasePath": {"type": "string"},
    "table": {"type": "string", "default": "records"},
    "mappings": {"type": "array", "items": {"type": "object"}, "default": []},
    "failurePolicy": {"type": "string", "enum": ["stop", "continue"], "default": "stop"}}}


class SqliteWriterOperator:
    meta = OperatorMeta(OPERATOR_ID, "SQLite 数据库写入", "1.0.0",
                        {"enabled": {"type": "boolean", "required": False, "nullable": False}},
                        {"receipt": {"type": "json", "required": True, "nullable": False}}, PARAM_SCHEMA)

    @staticmethod
    def validateParams(params):
        try:
            parseConfig(params)
        except SqliteWriterError as error:
            return {"code": error.code, "message": str(error)}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        begin = time.monotonic()
        writeId = str(uuid4())
        context = {**runtimeContext, "writeId": writeId,
                   "timestampUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        receipt = {"writeId": writeId, "status": "UNKNOWN", "rowsAffected": 0, "primaryKey": None,
                   "execution": {k: context.get(k) for k in ("jobId", "projectId", "workflowId", "workflowRunId", "nodeId", "nodeRunId", "iterationPath")},
                   "elapsedMs": 0.0, "error": None}
        logger = getOperatorLogger(context)
        publish = context.get("publishSqliteWrite", lambda *_args: None)
        def isCancelled():
            return bool(context.get("isCancellationRequested", False))
        # Runtime supplies a live cancellation predicate separately from the
        # legacy boolean snapshot used by existing operators.
        cancelled = context.get("sqliteCancelled", isCancelled)
        check = context.get("raiseIfCancellationRequested", lambda: None)
        config = None
        try:
            check()
            config = parseConfig(params)
            if inputs.get("enabled", True) is False:
                receipt["status"] = "SKIPPED"
            else:
                path = Path(config["databasePath"])
                if not path.is_absolute():
                    raise SqliteWriterError("E_SQLITE_TARGET", "数据库目标必须在运行前按原工程目录冻结")
                values = {}
                for index, row in enumerate(config["mappings"]):
                    source = row["source"]
                    if source["kind"] == "constant":
                        values[row["column"]] = toStorage(source["value"], row["storageType"])
                    elif source["kind"] == "context":
                        values[row["column"]] = toStorage(context[source["key"]], row["storageType"])
                    else:
                        bound = context.get("mappedOutputs", {}).get(index)
                        if bound is None:
                            missing = row.get("missing", "error")
                            if missing == "null":
                                values[row["column"]] = None
                            elif missing != "default":
                                raise SqliteWriterError("E_SQLITE_MISSING", "来源被跳过或未产出", index)
                        else:
                            if not isinstance(bound, BoundValue) or bound.workflowRunId != context.get("workflowRunId"):
                                raise SqliteWriterError("E_SQLITE_IDENTITY", "来源不属于本次流程调用", index)
                            if bound.errorCode:
                                raise SqliteWriterError(bound.errorCode, bound.errorMessage, index)
                            values[row["column"]] = bound.value
                insert = context.get("sqliteInsert")
                if not callable(insert):
                    raise SqliteWriterError("E_SQLITE_UNSUPPORTED", "执行器不支持正式 SQLite 写入能力")
                publish("sqlite.write.started", receipt)
                primary = insert(path, config, values, writeId, cancelled)
                receipt.update(status="COMMITTED", rowsAffected=1, primaryKey=primary)
        except Exception as error:
            code = getattr(error, "code", "")
            if not code:
                code = "E_SQLITE_CONSTRAINT" if isinstance(error, sqlite3.IntegrityError) else "E_SQLITE_LOCKED" if isinstance(error, sqlite3.OperationalError) and "locked" in str(error) else "E_SQLITE_WRITE"
            receipt.update(status="UNKNOWN" if code == "E_SQLITE_UNKNOWN" else "FAILED",
                           rowsAffected=None if code == "E_SQLITE_UNKNOWN" else 0,
                           error={"code": code, "message": str(error), "mappingRow": getattr(error, "row", None)})
            receipt["elapsedMs"] = (time.monotonic() - begin) * 1000
            publish("sqlite.write.finished", receipt)
            logger.error("SQLite 数据库写入" + receipt["status"] + "：" + str(error), code=code, payload={"receipt": receipt})
            # A known cancellation always propagates, even with continue policy.
            check()
            if code == "E_CANCELLED":
                raise
            if config is None or config["failurePolicy"] == "stop":
                return {"status": "error", "error": receipt["error"], "diagnostics": {"sqliteReceipt": receipt}}
            return {"status": "ok", "outputs": {"receipt": receipt}, "diagnostics": {"sqliteReceipt": receipt, "severity": "ERROR"}}
        receipt["elapsedMs"] = (time.monotonic() - begin) * 1000
        publish("sqlite.write.finished", receipt)
        logger.info("SQLite 数据库写入 " + receipt["status"], payload={"receipt": receipt})
        return {"status": "ok", "outputs": {"receipt": receipt}, "diagnostics": {"sqliteReceipt": receipt}}
