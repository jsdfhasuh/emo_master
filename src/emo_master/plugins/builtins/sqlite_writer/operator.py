"""Explicit business-database writer; field sources are compiled dependencies."""
from collections import Counter
from dataclasses import dataclass
from typing import Any
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import time
from uuid import uuid4

from emo_master.core.contracts.sqlite_writer import (
    OPERATOR_ID, parseConfig, SqliteWriterError, SqliteCommittedCleanupError, toStorage,
    projectRelativeFileReference,
)
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


_MAPPING_SCHEMA = {
    "title": "列映射",
    "description": "将一个数据来源转换为指定的存储类型，并写入一列。",
    "type": "object",
    "properties": {
        "column": {
            "title": "目标列", "type": "string",
            "description": "已存在的业务表列名，每列只能配置一次。write_id 仅允许映射执行上下文的 writeId，使用 TEXT 并在缺失时报错。",
        },
        "storageType": {
            "title": "存储类型", "type": "string",
            "description": "支持 INTEGER、REAL、TEXT、BOOLEAN、JSON、UTC_TIME 或 FILE_REFERENCE。布尔值写为 0 或 1，时间转为 UTC，图片引用等待已确认的文件。",
        },
        "missing": {
            "title": "来源缺失时", "type": "string",
            "description": "仅上游节点未产出时使用。error 报错，null 写入 SQL NULL，default 省略该列以采用数据库列默认值。未配置时按 error 处理。",
        },
        "source": {
            "title": "数据来源", "type": "object",
            "description": "可选同次流程输出、常量或执行上下文，实际可用值取决于目标存储类型。",
            "properties": {
                "kind": {
                    "title": "来源类型", "type": "string",
                    "description": "node_output 读取同次流程输出，constant 使用配置常量，context 读取当前执行上下文。",
                },
                "nodeId": {
                    "title": "来源节点", "type": "string",
                    "description": "仅 node_output 使用，指定当前流程中的来源节点标识。",
                },
                "port": {
                    "title": "来源端口", "type": "string",
                    "description": "仅 node_output 使用，指定来源节点实际声明的输出端口。",
                },
                "key": {
                    "title": "执行上下文字段", "type": "string",
                    "description": "仅 context 使用，例如本次写入标识 writeId 或 UTC 时间 timestampUtc。",
                },
            },
            # Constant values can be scalars, null or structured JSON, as allowed by parseConfig.
            "xFieldDescriptions": {
                "value": {
                    "title": "常量值",
                    "description": "仅 constant 使用，值由存储类型校验。JSON 类型可保存标量、空值、数组和对象。",
                },
            },
        },
    },
}


PARAM_SCHEMA: dict[str, Any] = {"type": "object", "properties": {
    "configVersion": {"description": "仅支持1的显式映射配置版本。", "title": "配置版本", "type": "integer", "default": 1},
    "databasePath": {
        "description": "正式本地业务SQLite目标，准入前按原工程根解析冻结为绝对路径。",
        "title": "数据库文件", "type": "string", "default": ""
    },
    "debugDatabasePath": {
        "description": "可选调试库模板；调试复制/初始化隔离库，不写生产库或模板。",
        "title": "调试用文件（可选）", "type": "string"
    },
    "table": {
        "description": "写入现有业务表名，经identifier校验并引用，不是任意SQL。",
        "title": "保存到哪张表", "type": "string", "default": "records"
    },
    "mappings": {
        "description": "1至64行列映射；按行对应runtime mappedOutputs，JSON schema只有object骨架，真正契约在parseConfig。",
        "title": "要保存的内容", "type": "array", "items": _MAPPING_SCHEMA, "default": []
    },
    "fileReferencePathMode": {"title": "图片引用路径", "description": "absolute保留实际持久路径；project_relative只保存原工程目录内的相对路径。",
                              "type": "string", "enum": ["absolute", "project_relative"], "default": "absolute",
                              "xOptionLabels": {"absolute": "绝对路径", "project_relative": "工程相对路径"}},
    "failurePolicy": {
        "description": "stop使节点错误；continue继续并保留FAILED/UNKNOWN/清理错误回执，不能把status=ok说成写入成功。",
        "title": "处理方式", "type": "string", "enum": ["stop", "continue"], "default": "stop"
    }}}


class SqliteWriterOperator:
    meta = OperatorMeta(OPERATOR_ID, "SQLite 数据库写入", "1.1.0",
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
        receipt = {"writeId": writeId, "status": "UNKNOWN", "rowsAffected": None, "primaryKey": None,
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
        mappingSummary = {}
        try:
            check()
            config = parseConfig(params)
            if inputs.get("enabled", True) is False:
                receipt.update(status="SKIPPED", rowsAffected=0)
            else:
                path = Path(config["databasePath"])
                if not path.is_absolute():
                    raise SqliteWriterError("E_SQLITE_TARGET", "数据库目标必须在运行前按原工程目录冻结")
                mappingSummary["mappingSources"] = dict(Counter(row["source"]["kind"] for row in config["mappings"]))
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
                    if (row["storageType"] == "FILE_REFERENCE" and row["column"] in values
                            and config.get("fileReferencePathMode", "absolute") == "project_relative"):
                        try:
                            values[row["column"]] = projectRelativeFileReference(
                                values[row["column"]], config.get("_fileReferenceProjectRoot"))
                        except SqliteWriterError as error:
                            raise SqliteWriterError(error.code, str(error), index) from error
                insert = context.get("sqliteInsert")
                if not callable(insert):
                    raise SqliteWriterError("E_SQLITE_UNSUPPORTED", "执行器不支持正式 SQLite 写入能力")
                publish("sqlite.write.started", receipt)
                primary = insert(path, config, values, writeId, cancelled)
                receipt.update(status="COMMITTED", rowsAffected=1, primaryKey=primary)
        except SqliteCommittedCleanupError as error:
            cleanup = {"code": error.code, "message": str(error)}
            receipt.update(status="COMMITTED", rowsAffected=1, primaryKey=error.primaryKey,
                           cleanupError=cleanup, elapsedMs=(time.monotonic() - begin) * 1000)
            diagnostics = {"sqliteReceipt": receipt, "sqliteCleanup": cleanup, "severity": "ERROR", **mappingSummary}
            publish("sqlite.write.finished", receipt)
            logger.error(str(error), code=error.code, payload={"receipt": receipt})
            try:
                check()
            except Exception as cancelledError:
                # Cancellation still wins task status while the confirmed
                # external write and cleanup diagnostic retain their identity.
                setattr(cancelledError, 'diagnostics', diagnostics)
                raise
            if config is None or config["failurePolicy"] == "stop":
                return {"status": "error", "error": cleanup, "diagnostics": diagnostics}
            return {"status": "ok", "outputs": {"receipt": receipt}, "diagnostics": diagnostics}
        except Exception as error:
            if getattr(error, 'sqliteCleanupError', None) is not None:
                receipt['cleanupError'] = getattr(error, 'sqliteCleanupError')
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
                return {"status": "error", "error": receipt["error"],
                        "diagnostics": {"sqliteReceipt": receipt, **mappingSummary}}
            return {"status": "ok", "outputs": {"receipt": receipt},
                    "diagnostics": {"sqliteReceipt": receipt, "severity": "ERROR", **mappingSummary}}
        receipt["elapsedMs"] = (time.monotonic() - begin) * 1000
        publish("sqlite.write.finished", receipt)
        logger.info("SQLite 数据库写入 " + receipt["status"], payload={"receipt": receipt})
        return {"status": "ok", "outputs": {"receipt": receipt},
                "diagnostics": {"sqliteReceipt": receipt, **mappingSummary}}
