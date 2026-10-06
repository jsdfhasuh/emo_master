"""Local business SQLite operations, separate from Runtime's state database."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
from typing import Any, Callable

from emo_master.core.contracts.sqlite_writer import (
    SqliteWriterError, identifier, quoteIdentifier, toStorage, parseConfig,
    MAX_RECORD_BYTES, sqliteIdentifierKey, isTransientPath,
)
from emo_master.apps.runtime.business_sqlite.schema import tableFlags

LOCK_TIMEOUT = 2.0
OPERATION_TIMEOUT = 5.0


def resolveTarget(raw: str, projectRoot: Path, protected=()) -> Path:
    if not isinstance(raw, str) or not raw.strip() or raw.strip() == ":memory:" or raw.lower().startswith("file:") or raw.startswith(("\\\\", "//")) or "\x00" in raw:
        raise SqliteWriterError("E_SQLITE_TARGET", "拒绝内存库、SQLite URI 或网络共享路径")
    path = Path(raw)
    if not path.is_absolute():
        if not projectRoot.is_absolute():
            raise SqliteWriterError("E_SQLITE_TARGET", "相对路径需要原工程的绝对目录")
        path = projectRoot / path
    path = path.resolve()
    if os.name == "nt" and ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
        raise SqliteWriterError("E_SQLITE_TARGET", "不支持映射网络盘")
    for item in protected:
        target = Path(item).resolve()
        if path == target or (target.is_dir() and path.is_relative_to(target)):
            raise SqliteWriterError("E_SQLITE_TARGET", "不能使用 Runtime 内部库、缓存或任务临时目录")
        if path.exists() and target.is_file() and os.path.samefile(path, target):
            raise SqliteWriterError("E_SQLITE_TARGET", "目标是 Runtime 内部数据库的文件别名")
    if path.name.casefold() in {"runtime.sqlite3", "runtime.db"} or isTransientPath(path):
        raise SqliteWriterError("E_SQLITE_TARGET", "不能使用 Runtime 内部库或预览缓存")
    if path.exists() and not path.is_file():
        raise SqliteWriterError("E_SQLITE_TARGET", "目标不是普通文件")
    return path


class OperationGuard:
    """SQLite interrupt/progress checks; join the monitor before returning ownership."""
    def __init__(self, cancelled: Callable[[], bool] = lambda: False):
        self.cancelled = cancelled
        self.deadline = time.monotonic() + OPERATION_TIMEOUT
        self.stop = threading.Event()
        self.connection: sqlite3.Connection | None = None
        self.thread: threading.Thread | None = None

    def check(self):
        if self.cancelled():
            from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
            raise CancellationRequested("SQLite 操作已取消")
        if time.monotonic() >= self.deadline:
            raise SqliteWriterError("E_SQLITE_TIMEOUT", "SQLite 操作期限为 5 秒")

    def attach(self, connection):
        self.connection = connection
        connection.set_progress_handler(self.progress, 1000)
        def interrupt():
            while not self.stop.wait(.02):
                if self.cancelled() or time.monotonic() >= self.deadline:
                    connection.interrupt()
                    return
        thread = threading.Thread(target=interrupt, name="sqlite-interrupt")
        thread.start()
        self.thread = thread

    def progress(self):
        return int(self.cancelled() or time.monotonic() >= self.deadline)

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        if self.connection is not None:
            self.connection.set_progress_handler(None, 0)
            self.connection.close()


@contextmanager
def connectionFor(path: Path, *, create=False, readonly=False, guard: OperationGuard):
    guard.check()
    # URI is generated exclusively by this backend, never accepted from a caller.
    mode = "ro" if readonly else "rwc" if create else "rw"
    connection = sqlite3.connect(path.as_uri() + f"?mode={mode}", uri=True, timeout=LOCK_TIMEOUT,
                                isolation_level=None)
    try:
        guard.attach(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        yield connection
    finally:
        guard.close()


def affinity(declared: str) -> str:
    name = declared.upper()
    if "INT" in name:
        return "INTEGER"
    if any(t in name for t in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if any(t in name for t in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    raise SqliteWriterError("E_SQLITE_SCHEMA", f"不支持的列类型：{declared or '(未声明)'}")


def tableStructure(connection, table: str) -> dict[str, Any]:
    identifier(table)
    found = connection.execute("SELECT name,type,sql FROM sqlite_schema WHERE type IN ('table','view') AND name=? COLLATE NOCASE", (table,)).fetchone()
    if not found or found[1] != "table":
        raise SqliteWriterError("E_SQLITE_SCHEMA", "目标必须为普通表，不能为视图或虚拟表")
    kind, withoutRowid = tableFlags(connection, found[0], str(found[2]))
    if kind != 'table':
        raise SqliteWriterError("E_SQLITE_SCHEMA", "目标必须为普通表，不能为视图、虚拟表或其影子表")
    columns = []
    raw = list(connection.execute(f"PRAGMA table_xinfo({quoteIdentifier(table)})"))
    if len(raw) > 256:
        raise SqliteWriterError("E_SQLITE_LIMIT", "目标表超过 256 列检查额度")
    primary = [r for r in raw if r[5]]
    indexes = list(connection.execute(f"PRAGMA index_list({quoteIdentifier(table)})"))
    # INTEGER PRIMARY KEY DESC owns a PK index, so it is not a rowid alias.
    indexedPrimary = any(row[3] == "pk" for row in indexes)
    for cid, name, declared, notnull, default, pk, hidden in raw:
        generated = hidden in {2, 3}
        auto = bool(pk and len(primary) == 1 and declared.upper() == "INTEGER" and not withoutRowid and not indexedPrimary)
        columns.append({"name": name, "declaredType": declared, "affinity": affinity(declared),
                        "nullable": not bool(notnull or pk), "defaultSql": default,
                        "primaryKey": int(pk), "generated": generated, "autoPrimaryKey": auto})
    uniqueWriteId = False
    for row in indexes:
        if row[2]:
            indexName = '"' + row[1].replace('"', '""') + '"'
            names = [sqliteIdentifierKey(r[2]) if isinstance(r[2], str) else r[2]
                     for r in connection.execute(f"PRAGMA index_info({indexName})")]
            if names == ["write_id"] and not row[4]:
                uniqueWriteId = True
    foreignKeys = list(connection.execute(f"PRAGMA foreign_key_list({quoteIdentifier(table)})"))
    triggers = list(connection.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name=?", (found[0],)))
    externalIndexes = [r[0] for r in connection.execute("SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (found[0],))]
    return {"table": found[0], "columns": columns, "createSql": found[2], "uniqueWriteId": uniqueWriteId, "externalIndexes": externalIndexes,
            "foreignKeys": [list(r) for r in foreignKeys], "triggers": [r[0] for r in triggers]}


def inspect(path: Path, table="", cancelled=lambda: False) -> dict[str, Any]:
    answer: dict[str, Any] = {"host": socket.gethostname(), "path": str(path), "exists": path.is_file(), "tables": [], "structure": None}
    if not answer["exists"]:
        return answer
    with connectionFor(path, readonly=True, guard=OperationGuard(cancelled)) as connection:
        tables = connection.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 257").fetchall()
        if len(tables) > 256:
            raise SqliteWriterError("E_SQLITE_LIMIT", "数据库超过 256 表检查额度")
        answer["tables"] = [r[0] for r in tables]
        if table and connection.execute('SELECT 1 FROM sqlite_schema WHERE name=? COLLATE NOCASE', (table,)).fetchone():
            answer["structure"] = tableStructure(connection, table)
    return answer


def validateMappings(config, structure):
    columns = {sqliteIdentifierKey(c["name"]): c for c in structure["columns"]}
    mapped = set()
    for index, row in enumerate(config["mappings"]):
        name = sqliteIdentifierKey(row["column"])
        column = columns.get(name)
        if column is None:
            raise SqliteWriterError("E_SQLITE_SCHEMA", "目标列不存在：" + row["column"], index)
        if name in mapped:
            raise SqliteWriterError("E_SQLITE_SCHEMA", "数据库列重复映射", index)
        if column["generated"]:
            raise SqliteWriterError("E_SQLITE_SCHEMA", "生成列不能显式写入", index)
        expected = {"BOOLEAN": "INTEGER", "JSON": "TEXT", "UTC_TIME": "TEXT", "FILE_REFERENCE": "TEXT"}.get(row["storageType"], row["storageType"])
        if column["affinity"] != expected:
            raise SqliteWriterError("E_SQLITE_TYPE", f"列亲和性 {column['affinity']} 与 {row['storageType']} 不兼容", index)
        if row.get("missing") == "null" and not column["nullable"]:
            raise SqliteWriterError("E_SQLITE_SCHEMA", "此列不允许 NULL", index)
        if row.get("missing") == "default" and not (column["defaultSql"] is not None or column["nullable"] or column["autoPrimaryKey"]):
            raise SqliteWriterError("E_SQLITE_SCHEMA", "此列没有默认值且不可省略", index)
        mapped.add(name)
    for name, column in columns.items():
        if name in mapped or column["nullable"] or column["defaultSql"] is not None or column["generated"] or column["autoPrimaryKey"]:
            continue
        if name == "write_id" and column["affinity"] == "TEXT":
            continue
        raise SqliteWriterError("E_SQLITE_SCHEMA", "未映射必填列：" + column["name"])


def createPlan(table: str, columns: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    identifier(table)
    if not 1 <= len(columns) <= 64:
        raise SqliteWriterError("E_SQLITE_LIMIT", "建表列数必须为 1–64")
    fragments = ['"id" INTEGER PRIMARY KEY AUTOINCREMENT', '"write_id" TEXT NOT NULL UNIQUE']
    seen = {"id", "write_id"}
    normalized = []
    for index, column in enumerate(columns):
        if set(column) - {"name", "storageType", "nullable", "default"}:
            raise SqliteWriterError("E_SQLITE_CONFIG", "建表仅接受结构化列方案", index)
        name = identifier(column.get("name"))
        storage = column.get("storageType")
        nullable = column.get("nullable", False)
        if sqliteIdentifierKey(name) in seen or storage not in {"INTEGER", "REAL", "TEXT", "BOOLEAN", "JSON", "UTC_TIME", "FILE_REFERENCE"} or type(nullable) is not bool:
            raise SqliteWriterError("E_SQLITE_CONFIG", "重复列、保留列或无效类型", index)
        seen.add(sqliteIdentifierKey(name))
        declared = {"BOOLEAN": "INTEGER", "JSON": "TEXT", "UTC_TIME": "TEXT", "FILE_REFERENCE": "TEXT"}.get(storage, storage)
        fragment = quoteIdentifier(name) + " " + declared + ("" if nullable else " NOT NULL")
        item = {"name": name, "storageType": storage, "nullable": nullable}
        if "default" in column:
            value = toStorage(column["default"], storage, checkFile=False)
            if value is None and not nullable:
                raise SqliteWriterError("E_SQLITE_CONFIG", "必填列不能使用 NULL 默认值", index)
            item["default"] = value
            literal = "NULL" if value is None else ("'" + value.replace("'", "''") + "'" if isinstance(value, str) else str(value))
            fragment += " DEFAULT " + literal
        fragments.append(fragment)
        normalized.append(item)
    return "CREATE TABLE " + quoteIdentifier(table) + " (" + ", ".join(fragments) + ")", normalized


def initialize(path: Path, table: str, columns, cancelled=lambda: False):
    sql, normalized = createPlan(table, columns)
    guard = OperationGuard(cancelled)
    # Parent directories are never implicitly created by management RPCs.
    with connectionFor(path, create=True, guard=guard) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name=? COLLATE NOCASE", (table,)).fetchone()
            if exists:
                actual = tableStructure(connection, table)
                byName = {c["name"]: c for c in actual["columns"]}
                if (set(byName) != {"id", "write_id", *(c["name"] for c in normalized)}
                        or not byName["id"]["autoPrimaryKey"] or not actual["uniqueWriteId"]
                        or actual["createSql"] != sql):
                    raise SqliteWriterError("E_SQLITE_SCHEMA", "已有目标表与建表方案不一致，不自动迁移")
                for column in normalized:
                    actualColumn = byName[column["name"]]
                    expected = {"BOOLEAN": "INTEGER", "JSON": "TEXT", "UTC_TIME": "TEXT", "FILE_REFERENCE": "TEXT"}.get(column["storageType"], column["storageType"])
                    default = column.get("default")
                    defaultSql = None if "default" not in column else "NULL" if default is None else "'" + default.replace("'", "''") + "'" if isinstance(default, str) else str(default)
                    if actualColumn["affinity"] != expected or actualColumn["nullable"] != column["nullable"] or actualColumn["defaultSql"] != defaultSql or actualColumn["generated"]:
                        raise SqliteWriterError("E_SQLITE_SCHEMA", "已有列与方案不一致：" + column["name"])
            else:
                connection.execute(sql)
            guard.check()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return inspect(path, table, cancelled)


def insert(path: Path, config, values: dict[str, Any], writeId: str, cancelled=lambda: False):
    guard = OperationGuard(cancelled)
    committing = False
    with connectionFor(path, guard=guard) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            structure = tableStructure(connection, config["table"])
            validateMappings(config, structure)
            if any(sqliteIdentifierKey(k) == "write_id" and v != writeId for k, v in values.items()):
                raise SqliteWriterError("E_SQLITE_VALUE", "write_id 必须与本次回执的 writeId 一致")
            if any(sqliteIdentifierKey(c["name"]) == "write_id" for c in structure["columns"]) and not any(sqliteIdentifierKey(k) == "write_id" for k in values):
                values = {**values, "write_id": writeId}
            if len(json.dumps(values, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_RECORD_BYTES:
                raise SqliteWriterError("E_SQLITE_LIMIT", "单条序列化记录超过 1 MiB（含 write_id）")
            if values:
                sql = f"INSERT INTO {quoteIdentifier(config['table'])} (" + ",".join(quoteIdentifier(k) for k in values) + ") VALUES (" + ",".join("?" for _ in values) + ")"
                cursor = connection.execute(sql, tuple(values.values()))
            else:
                cursor = connection.execute(f"INSERT INTO {quoteIdentifier(config['table'])} DEFAULT VALUES")
            # RAISE(IGNORE) and ON CONFLICT IGNORE may succeed without inserting
            # anything. Reject that outcome before commit, including trigger
            # side effects, rather than fabricate a one-record receipt.
            if cursor.rowcount != 1:
                raise SqliteWriterError("E_SQLITE_NO_INSERT", "数据库未插入一条记录；约束或触发器可能忽略了写入")
            guard.check()
            committing = True
            connection.commit()
            primary = [c for c in structure["columns"] if c["autoPrimaryKey"]]
            return cursor.lastrowid if primary else None
        except BaseException as error:
            uncertain = committing and not connection.in_transaction
            if connection.in_transaction:
                connection.rollback()
            if uncertain:
                raise SqliteWriterError("E_SQLITE_UNKNOWN", "提交结果无法确认；不自动重复 INSERT") from error
            if getattr(error, "code", "") == "E_CANCELLED":
                raise
            guard.check()
            raise


@dataclass
class SqliteManagement:
    """At most two admitted operations, including work surviving RPC cancellation."""
    def __post_init__(self):
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sqlite-management")
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.closing = False
        self.cancelled = threading.Event()

    def run(self, operation, context=None):
        with self.lock:
            if self.closing or self.active >= 2:
                raise SqliteWriterError("E_SQLITE_BUSY", "SQLite 管理操作额度为 2；实际退出后才释放")
            self.active += 1
            self.peak = max(self.peak, self.active)
        def work():
            try:
                return operation(lambda: self.cancelled.is_set() or (context is not None and not context.is_active()))
            finally:
                with self.lock:
                    self.active -= 1
        try:
            future = self.pool.submit(work)
        except BaseException:
            with self.lock:
                self.active -= 1
            raise
        return future.result()

    def close(self):
        with self.lock:
            self.closing = True
            self.cancelled.set()
            if self.active:
                raise RuntimeError("SQLite 管理操作仍在执行，资源尚未释放")
        self.pool.shutdown(wait=True)


@dataclass
class _WriterTarget:
    node: Any
    config: dict[str, Any]
    path: Path
    structure: dict[str, Any]


def _prepareDebugDatabase(writers: list[_WriterTarget], projectRoot: Path, protected, debugRoot: Path, cancelled):
    import hashlib
    template: Path | None = None
    # Select one explicit test template for the whole business database. Distinct
    # templates must not silently overwrite one another or split related tables.
    for writer in writers:
        if writer.config.get("debugDatabasePath"):
            candidate = resolveTarget(writer.config["debugDatabasePath"], projectRoot, protected)
            if not candidate.is_file():
                raise SqliteWriterError("E_SQLITE_DEBUG_SCHEMA", "专用测试库不存在")
            if template is not None and not os.path.samefile(candidate, template):
                raise SqliteWriterError("E_SQLITE_DEBUG_SCHEMA", "同一业务数据库必须使用同一个专用测试库")
            template = candidate
    for writer in writers:
        structure = writer.structure
        complexSchema = structure["foreignKeys"] or structure["triggers"] or structure["externalIndexes"] or any(c["generated"] for c in structure["columns"])
        if complexSchema and template is None:
            raise SqliteWriterError("E_SQLITE_DEBUG_SCHEMA", "此结构需要专用测试库；不复制业务数据或回退正式库")
        if template is not None:
            tested = inspect(template, writer.config["table"], cancelled)
            if tested["structure"] is None:
                raise SqliteWriterError("E_SQLITE_DEBUG_SCHEMA", "专用测试库缺少目标表：" + writer.config["table"])
            validateMappings(writer.config, tested["structure"])
    canonical = min(os.path.normcase(str(writer.path)) for writer in writers)
    debugRoot.mkdir(parents=True, exist_ok=True)
    isolated = debugRoot / (hashlib.sha256(canonical.encode()).hexdigest()[:24] + ".sqlite3")
    existed = isolated.exists()
    with connectionFor(isolated, create=True, guard=OperationGuard(cancelled)) as connection:
        if template is not None and not existed:
            # Copy an explicit test template once. The read-only source is never
            # a business database (including another node's target or an alias).
            sourceGuard = OperationGuard(cancelled)
            with connectionFor(template, readonly=True, guard=sourceGuard) as source:
                source.backup(connection, pages=64, progress=lambda *_: sourceGuard.check(), sleep=.02)
        for writer in writers:
            table = writer.config["table"]
            exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name=? COLLATE NOCASE", (table,)).fetchone()
            if not exists and template is None:
                connection.execute(writer.structure["createSql"])
            validateMappings(writer.config, tableStructure(connection, table))
    return isolated


def freezeTargets(document, projectRoot: Path, protected=(), *, debugRoot: Path | None = None, cancelled=lambda: False):
    """Resolve relative paths before snapshots; inspect all writers before a run."""
    from emo_master.core.contracts.sqlite_writer import OPERATOR_ID
    result = document.model_copy(deep=True)
    writers = []
    for wid, workflow in result.workflows.items():
        for node in workflow.nodes:
            if node.operatorId != OPERATOR_ID:
                continue
            config = parseConfig(node.params)
            path = resolveTarget(config["databasePath"], projectRoot, protected)
            target = inspect(path, config["table"], cancelled)
            if not target["exists"]:
                raise SqliteWriterError("E_SQLITE_TARGET", "数据库不存在；请明确初始化")
            if target['structure'] is None:
                raise SqliteWriterError('E_SQLITE_SCHEMA', '目标表不存在；请明确初始化')
            validateMappings(config, target["structure"])
            writers.append(_WriterTarget(node, config, path, target["structure"]))
    groups: dict[tuple[int, int], list[_WriterTarget]] = {}
    if debugRoot is not None:
        for writer in writers:
            stat = writer.path.stat()
            groups.setdefault((stat.st_dev, stat.st_ino), []).append(writer)
        allProtected = (*protected, *(writer.path for writer in writers))
        for members in groups.values():
            isolated = _prepareDebugDatabase(members, projectRoot, allProtected, debugRoot, cancelled)
            for writer in members:
                writer.node.params = {**writer.config, "databasePath": str(isolated)}
    else:
        for writer in writers:
            writer.node.params = {**writer.config, "databasePath": str(writer.path)}
    return result
