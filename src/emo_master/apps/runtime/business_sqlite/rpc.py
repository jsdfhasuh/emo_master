"""Thin structured management RPC; database work runs in the bounded owner."""
import json
from pathlib import Path
import socket

from emo_master.apps.runtime.business_sqlite.backend import resolveTarget, inspect, initialize, createPlan
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.contracts.sqlite_writer import jsonValue


def columnsFromWire(columns):
    if len(columns) > 64:
        raise ValueError("建表列数超过 64")
    result = []
    for column in columns:
        item = {"name": column.name, "storageType": column.storage_type, "nullable": column.nullable}
        if column.has_default:
            if len(column.default_json.encode()) > 1024 * 1024:
                raise ValueError("默认值超过 1 MiB")
            item["default"] = jsonValue(json.loads(column.default_json))
        result.append(item)
    return result


def managementRpc(service, request, context, *, creating=False):
    path = None
    try:
        path = resolveTarget(request.database_path, Path(request.project_directory), service.sqliteProtectedPaths())
        columns = columnsFromWire(request.columns if creating else request.proposed_columns)
        preview = createPlan(request.table, columns)[0] if columns else ""
        if creating and not request.confirmed:
            raise ValueError("初始化必须先预览方案并明确确认")
        result = service.sqliteManagement.run(
            lambda cancelled: initialize(path, request.table, columns, cancelled) if creating else inspect(path, request.table, cancelled), context)
        structure = result.get("structure") or {}
        return pb.SqliteTargetReply(ok=True, runtime_host=result["host"], resolved_path=str(path),
            exists=result["exists"], tables=result["tables"], preview_sql=preview,
            structure_json=json.dumps(structure, ensure_ascii=False), columns=[pb.SqliteColumnInfo(
                name=c["name"], declared_type=c["declaredType"], affinity=c["affinity"], nullable=c["nullable"],
                has_default=c["defaultSql"] is not None, default_sql=c["defaultSql"] or "",
                primary_key=c["primaryKey"], generated=c["generated"], auto_primary_key=c["autoPrimaryKey"])
                for c in structure.get("columns", [])])
    except Exception as error:
        return pb.SqliteTargetReply(ok=False, code=getattr(error, "code", "E_SQLITE_MANAGEMENT"),
            message=str(error), runtime_host=socket.gethostname(), resolved_path=str(path or ""))
