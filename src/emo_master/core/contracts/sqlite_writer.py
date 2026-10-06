"""Versioned SQLite field mappings. No Qt, database I/O or global result cache."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any

from emo_master.core.contracts.port_types import normalizePortType, JSON_PAYLOAD_PORT_TYPES

OPERATOR_ID = "vision.io.sqlite_writer"
MAX_FIELDS = 64
MAX_RECORD_BYTES = 1024 * 1024
STORAGE_TYPES = ("INTEGER", "REAL", "TEXT", "BOOLEAN", "JSON", "UTC_TIME", "FILE_REFERENCE")
MISSING_POLICIES = ("error", "null", "default")
CONTEXT_TYPES = {"jobId": "string", "projectId": "string", "workflowId": "string",
                 "workflowRunId": "string", "nodeRunId": "string", "nodeId": "string",
                 "iterationPath": "list<integer>", "timestampUtc": "string", "writeId": "string"}


class SqliteWriterError(ValueError):
    def __init__(self, code: str, message: str, row: int | None = None):
        self.code, self.row = code, row
        super().__init__((f"映射第 {row + 1} 行：" if row is not None else "") + message)


_IDENTIFIER_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def sqliteIdentifierKey(value: str) -> str:
    """SQLite folds ASCII identifier letters only, not Unicode case pairs."""
    return value.translate(_IDENTIFIER_FOLD)


def isTransientPath(path: Path) -> bool:
    return any(os.path.normcase(part) in {"preview-cache", "preview_staging", "jobs"} for part in path.parts)


def identifier(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise SqliteWriterError("E_SQLITE_CONFIG", "表名和列名必须为 1–128 个可见字符")
    if sqliteIdentifierKey(value).startswith("sqlite_"):
        raise SqliteWriterError("E_SQLITE_CONFIG", "不能使用 SQLite 内部名称")
    return value


def quoteIdentifier(value: str) -> str:
    return '"' + identifier(value).replace('"', '""') + '"'


def parseConfig(params: Mapping[str, Any]) -> dict[str, Any]:
    if type(params.get("configVersion")) is not int or params["configVersion"] != 1:
        raise SqliteWriterError("E_SQLITE_CONFIG", "不支持的 SQLite 配置版本")
    if set(params) - {"configVersion", "databasePath", "debugDatabasePath", "table", "mappings", "failurePolicy"}:
        raise SqliteWriterError("E_SQLITE_CONFIG", "存在不支持的 SQLite 参数")
    path = params.get("databasePath")
    if not isinstance(path, str) or not path.strip():
        raise SqliteWriterError("E_SQLITE_TARGET", "请选择 Runtime 主机的本地数据库文件")
    if path.strip() == ":memory:" or path.lower().startswith("file:") or path.startswith(("\\\\", "//")) or "\x00" in path:
        raise SqliteWriterError("E_SQLITE_TARGET", "不支持内存库、SQLite URI 或网络共享路径")
    table = identifier(params.get("table"))
    failure = params.get("failurePolicy", "stop")
    if failure not in {"stop", "continue"}:
        raise SqliteWriterError("E_SQLITE_CONFIG", "失败策略必须为停止或继续")
    rows = params.get("mappings")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FIELDS:
        raise SqliteWriterError("E_SQLITE_LIMIT", "字段映射必须为 1–64 行")
    seen = set()
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict) or set(row) - {"column", "source", "storageType", "missing"}:
                raise SqliteWriterError("E_SQLITE_CONFIG", "字段映射格式无效")
            column = identifier(row.get("column"))
            if sqliteIdentifierKey(column) in seen:
                raise SqliteWriterError("E_SQLITE_CONFIG", "数据库列重复映射")
            seen.add(sqliteIdentifierKey(column))
            if row.get("storageType") not in STORAGE_TYPES or row.get("missing", "error") not in MISSING_POLICIES:
                raise SqliteWriterError("E_SQLITE_CONFIG", "存储类型或缺失策略无效")
            source = row.get("source")
            if not isinstance(source, dict):
                raise SqliteWriterError("E_SQLITE_CONFIG", "请选择数据来源")
            kind = source.get("kind")
            if kind == "node_output":
                if set(source) != {"kind", "nodeId", "port"} or not all(isinstance(source.get(k), str) and source[k] for k in ("nodeId", "port")):
                    raise SqliteWriterError("E_SQLITE_CONFIG", "来源必须为当前流程的完整输出端口")
            elif kind == "constant":
                if set(source) != {"kind", "value"}:
                    raise SqliteWriterError("E_SQLITE_CONFIG", "常量来源缺少 value")
                toStorage(source["value"], row["storageType"], checkFile=False)
            elif kind == "context":
                if set(source) != {"kind", "key"} or source.get("key") not in CONTEXT_TYPES:
                    raise SqliteWriterError("E_SQLITE_CONFIG", "不支持的执行上下文字段")
                if not acceptsSource(CONTEXT_TYPES[source["key"]], row["storageType"]):
                    raise SqliteWriterError("E_SQLITE_TYPE", "上下文与存储类型不兼容")
            else:
                raise SqliteWriterError("E_SQLITE_CONFIG", "不支持的数据来源")
            if sqliteIdentifierKey(column) == "write_id" and (source != {"kind": "context", "key": "writeId"}
                    or row["storageType"] != "TEXT" or row.get("missing", "error") != "error"):
                raise SqliteWriterError("E_SQLITE_CONFIG", "write_id 为写入身份，只能映射执行上下文 writeId（TEXT，缺失报错）")
        except SqliteWriterError as error:
            raise SqliteWriterError(error.code, str(error), index) from error
    result = {"configVersion": 1, "databasePath": path, "table": table,
              "mappings": deepcopy(rows), "failurePolicy": failure}
    if "debugDatabasePath" in params:
        debugPath = params["debugDatabasePath"]
        if not isinstance(debugPath, str) or not debugPath.strip():
            raise SqliteWriterError("E_SQLITE_TARGET", "专用测试库路径必须为非空文字")
        result["debugDatabasePath"] = debugPath
    return result


def acceptsSource(spec: object, storage: str) -> bool:
    kind = normalizePortType(spec)
    if storage in {"INTEGER", "REAL"}:
        return kind in {"integer", "number"}
    if storage == "BOOLEAN":
        return kind == "boolean"
    if storage in {"TEXT", "UTC_TIME"}:
        return kind == "string"
    if storage == "FILE_REFERENCE":
        return kind in {"string", "json"}
    if storage == "JSON":
        return kind in {"integer", "number", "boolean", "string", "json", "object", "any"} or kind.startswith("list<") or kind in {normalizePortType(t) for t in JSON_PAYLOAD_PORT_TYPES}
    return False


def jsonValue(value: Any, depth: int = 0, budget: list[int] | None = None) -> Any:
    """Full formal payload, never the inspector's lossy/truncated summary."""
    if budget is None:
        budget = [MAX_RECORD_BYTES]
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        raise SqliteWriterError("E_SQLITE_LIMIT", "结构化数据超过深度或元素额度")
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise SqliteWriterError("E_SQLITE_VALUE", "不允许非有限数值")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise SqliteWriterError("E_SQLITE_VALUE", "JSON 对象键必须为文字")
        return {k: jsonValue(v, depth + 1, budget) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonValue(v, depth + 1, budget) for v in value]
    # Only formal payload adapters; arbitrary objects and ndarray are rejected.
    if type(value).__module__ in {"emo_master.core.contracts.geometry2d", "emo_master.core.contracts.communication"} and callable(getattr(value, "toPayload", None)):
        return jsonValue(value.toPayload(), depth + 1, budget)
    raise SqliteWriterError("E_SQLITE_VALUE", f"不可序列化的完整输出：{type(value).__name__}")


def toStorage(value: Any, storage: str, *, checkFile: bool = True) -> Any:
    result: Any
    if value is None:
        return None
    if storage == "JSON":
        payload = jsonValue(value)
        try:
            result = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            size = len(result.encode("utf-8"))
        except (UnicodeError, ValueError, TypeError, OverflowError) as error:
            raise SqliteWriterError("E_SQLITE_VALUE", "完整 JSON 无法序列化或编码为 UTF-8：" + str(error)) from error
        if size > MAX_RECORD_BYTES:
            raise SqliteWriterError("E_SQLITE_LIMIT", "序列化记录超过 1 MiB")
        return result
    if storage == "BOOLEAN" and type(value) is bool:
        return int(value)
    if storage == "INTEGER" and type(value) in {int, float}:
        if type(value) is float and (not math.isfinite(value) or not value.is_integer()):
            raise SqliteWriterError("E_SQLITE_VALUE", "整数转换有损或非有限")
        result = int(value)
        if not -(2**63) <= result < 2**63:
            raise SqliteWriterError("E_SQLITE_VALUE", "整数超出 SQLite 64 位范围")
        return result
    if storage == "REAL" and type(value) in {int, float}:
        try:
            result = float(value)
        except OverflowError as error:
            raise SqliteWriterError("E_SQLITE_VALUE", "数值溢出") from error
        if not math.isfinite(result) or (type(value) is int and int(result) != value):
            raise SqliteWriterError("E_SQLITE_VALUE", "实数转换有损或非有限")
        return result
    if storage == "FILE_REFERENCE":
        if isinstance(value, dict) and value.get("saved") is True:
            value = value.get("path")
        if isinstance(value, str) and value:
            path = Path(value)
            if checkFile:
                try:
                    resolved = path.resolve(strict=True)
                    valid = path.is_absolute() and resolved.is_file() and not isTransientPath(path) and not isTransientPath(resolved)
                except (OSError, RuntimeError):
                    valid = False
                if not valid:
                    raise SqliteWriterError("E_SQLITE_VALUE", "图片引用必须为已保存的持久文件，不能使用任务或预览缓存")
                return str(resolved)
            return value
    if storage == "UTC_TIME" and isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.utcoffset() is None:
                raise ValueError("timezone required")
            return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError as error:
            raise SqliteWriterError("E_SQLITE_VALUE", "UTC 时间需要带时区的 ISO 8601 字符串") from error
    if storage == "TEXT" and isinstance(value, str):
        return value
    raise SqliteWriterError("E_SQLITE_TYPE", f"不能将 {type(value).__name__} 无损保存为 {storage}")


def rebindParams(params: Mapping[str, Any], nodeIdMap: Mapping[str, str]) -> dict[str, Any]:
    result = deepcopy(dict(params))
    for row in result.get("mappings", []):
        source = row.get("source", {}) if isinstance(row, dict) else {}
        if source.get("kind") == "node_output":
            oldId = source.get("nodeId")
            if isinstance(oldId, str):
                source["nodeId"] = nodeIdMap.get(oldId, oldId)
    return result


def receiptSummary(value, identity):
    """Bounded real receipt for the existing node inspector, not a data source."""
    if not isinstance(value, dict) or not isinstance(value.get('status'), str) or value['status'] not in {'COMMITTED', 'SKIPPED', 'FAILED', 'UNKNOWN'}:
        return None
    execution = value.get('execution')
    if not isinstance(execution, dict) or any(execution.get(key) != expected for key, expected in identity.items()):
        return None
    writeId = value.get('writeId')
    if not isinstance(writeId, str):
        return None
    try:
        if not 1 <= len(writeId.encode('utf-8')) <= 128:
            return None
    except UnicodeEncodeError:
        return None
    rows = value.get('rowsAffected')
    if rows is not None and (type(rows) is not int or rows not in {0, 1}):
        return None
    if rows != {'COMMITTED': 1, 'SKIPPED': 0, 'FAILED': 0, 'UNKNOWN': None}[value['status']]:
        return None
    primary = value.get('primaryKey')
    if primary is not None and (type(primary) is not int or not -(2**63) <= primary < 2**63):
        primary = None
    elapsed = value.get('elapsedMs')
    elapsed = elapsed if type(elapsed) in {int, float} and 0 <= elapsed < 2**53 and math.isfinite(elapsed) else None
    error = value.get('error')
    from emo_master.core.contracts.run_inspection import clipText
    problem = ({'code': clipText(error.get('code', '') if isinstance(error.get('code'), str) else '', 64),
                'message': clipText(error.get('message', '') if isinstance(error.get('message'), str) else '', 512)} if isinstance(error, dict) else None)
    return {'writeId': writeId, 'status': value['status'], 'rowsAffected': rows,
            'primaryKey': primary, 'elapsedMs': elapsed, 'error': problem}
