"""Explicit business-database writer; field sources are compiled dependencies."""
from dataclasses import dataclass
from typing import Any

from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, parseConfig, SqliteWriterError


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
        # Replaced by the transaction implementation in batch B. No implicit I/O.
        return {"status": "error", "error": {"code": "E_SQLITE_BACKEND_UNAVAILABLE",
                                               "message": "SQLite 写入后端尚未安装"}}
