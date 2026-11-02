"""Small, read-only queries for local SQLite page components."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import sqlite3
import time

from emo_master.core.presentation.local_paths import resolveLocalPath


QUERY_SECONDS = 2.0
CELL_TEXT_LIMIT = 4096
PATH_TEXT_LIMIT = 4096


def quoteIdentifier(value: str) -> str:
    if not value or '\x00' in value:
        raise ValueError('表名或字段名无效')
    return '"' + value.replace('"', '""') + '"'


@contextmanager
def _connection(databasePath: str, projectRoot=None):
    if not databasePath:
        raise ValueError('请先选择 SQLite 数据库')
    path = resolveLocalPath(databasePath, projectRoot, purpose='数据库')
    if not path.is_file():
        raise ValueError('数据库文件不存在，请重新选择')
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.5)
        connection.execute('PRAGMA query_only = ON')
        deadline = time.monotonic() + QUERY_SECONDS
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        yield connection
    except sqlite3.Error as error:
        message = ('查询超时，请缩小筛选范围或为排序字段建立索引' if str(error) == 'interrupted'
                   else '无法读取数据库：' + str(error))
        raise ValueError(message) from error
    finally:
        if connection is not None:
            connection.close()


def _tables(connection):
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT GLOB 'sqlite_*' ORDER BY name")]


def _columns(connection, table):
    if table not in _tables(connection):
        raise ValueError('数据表不存在，请重新选择')
    return [row[1] for row in connection.execute('PRAGMA table_info(' + quoteIdentifier(table) + ')')]


def inspectDatabase(databasePath: str, *, projectRoot=None) -> dict[str, list[str]]:
    """Return selectable tables/views and their actual field names."""
    with _connection(databasePath, projectRoot) as connection:
        return {table: [row[1] for row in connection.execute(
            'PRAGMA table_info(' + quoteIdentifier(table) + ')')] for table in _tables(connection)}


@dataclass(frozen=True)
class SqlitePage:
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    page: int
    hasNext: bool
    selectionRows: tuple[dict, ...] = ()
    rowIds: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathValueError:
    """A selected field error, distinct from a literal filesystem path."""

    message: str


def _selectionExpressions(field, available):
    if field not in available:
        return ["'missing'", 'NULL']
    name = quoteIdentifier(field)
    # A BLOB is never fetched. The byte check also bounds TEXT containing NUL,
    # for which SQLite length(TEXT) only counts the prefix before that NUL.
    return [f'typeof({name})',
            f"CASE WHEN typeof({name}) = 'text' AND length({name}) <= {PATH_TEXT_LIMIT} "
            f'AND length(CAST({name} AS BLOB)) <= {PATH_TEXT_LIMIT * 4} THEN {name} ELSE NULL END']


def _selectionValue(kind, value):
    if kind == 'null':
        return None
    if kind == 'missing':
        return PathValueError('图片路径字段不存在，请重新选择字段')
    if kind != 'text':
        return PathValueError('图片路径字段必须是文字，不能使用数值或 BLOB')
    if value is None or len(value) > PATH_TEXT_LIMIT:
        return PathValueError(f'图片路径超过 {PATH_TEXT_LIMIT} 个字符')
    if '\x00' in value:
        return PathValueError('图片路径包含无效字符')
    return value


def _displayExpression(column):
    name = quoteIdentifier(column)
    # Read a bounded display value even when a table contains image BLOBs.
    return (f"CASE typeof({name}) WHEN 'blob' THEN '[BLOB ' || length({name}) || ' 字节]' "
            f"WHEN 'text' THEN CASE WHEN length({name}) > {CELL_TEXT_LIMIT} "
            f"THEN substr({name}, 1, {CELL_TEXT_LIMIT}) || '…' ELSE {name} END ELSE {name} END")


def readPage(source, *, pageSize=20, page=0, filterColumn='', filterText='',
             orderBy=None, descending=None, selectionFields=(), projectRoot=None) -> SqlitePage:
    if not 1 <= pageSize <= 100 or page < 0:
        raise ValueError('分页参数无效')
    with _connection(source.databasePath, projectRoot) as connection:
        available = _columns(connection, source.table)
        columns = list(source.columns)
        fields = tuple(dict.fromkeys(selectionFields))
        if len(fields) > 32:
            raise ValueError('选行关联字段不能超过 32 个')
        if not columns:
            raise ValueError('请先选择要显示的字段')
        if len(columns) > 32 or len(set(columns)) != len(columns) or any(c not in available for c in columns):
            raise ValueError('显示字段不存在或重复，请重新选择')
        sort = (source.orderBy if orderBy is None else orderBy) or columns[0]
        if sort not in available:
            raise ValueError('排序字段不存在，请重新选择')
        descending = source.descending if descending is None else descending
        where, parameters = '', []
        if filterText:
            if filterColumn not in columns:
                raise ValueError('请选择要筛选的显示字段')
            pattern = filterText.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where = ' WHERE CAST(' + quoteIdentifier(filterColumn) + " AS TEXT) LIKE ? ESCAPE '\\'"
            parameters.append('%' + pattern + '%')
        expressions = [_displayExpression(c) for c in columns]
        expressions.extend(expression for field in fields for expression in _selectionExpressions(field, available))
        primary = sorted((row for row in connection.execute('PRAGMA table_info(' + quoteIdentifier(source.table) + ')')
                          if row[5]), key=lambda row: row[5])
        identity = [quoteIdentifier(row[1]) for row in primary]
        if not identity and connection.execute(
                "SELECT type FROM sqlite_master WHERE name = ?", (source.table,)).fetchone()[0] == 'table':
            identity = [next((name for name in ('_rowid_', 'rowid', 'oid') if name not in available), '')]
            identity = [name for name in identity if name]
        identityOffset = len(expressions)
        expressions.extend(identity)
        sortExpressions = [quoteIdentifier(sort) + (' DESC' if descending else ' ASC')]
        sortExpressions.extend(identity)
        sql = ('SELECT ' + ', '.join(expressions) +
               ' FROM ' + quoteIdentifier(source.table) + where +
               ' ORDER BY ' + ', '.join(sortExpressions) +
               ' LIMIT ? OFFSET ?')
        rows = connection.execute(sql, (*parameters, pageSize + 1, page * pageSize)).fetchall()
        selected = tuple({field: _selectionValue(row[len(columns) + index * 2], row[len(columns) + index * 2 + 1])
                          for index, field in enumerate(fields)} for row in rows[:pageSize])
        return SqlitePage(tuple(columns), tuple(tuple(row[:len(columns)]) for row in rows[:pageSize]),
                          page, len(rows) > pageSize, selected,
                          tuple(hashlib.sha256(json.dumps(list(row[identityOffset:]), default=str,
                              ensure_ascii=True).encode()).hexdigest() if identity else '' for row in rows[:pageSize]))


def readValue(source, *, projectRoot=None, filterColumn='', filterText=''):
    with _connection(source.databasePath, projectRoot) as connection:
        available = _columns(connection, source.table)
        aggregate = source.aggregate
        if aggregate not in {'count', 'sum', 'avg', 'min', 'max'}:
            raise ValueError('统计方式无效')
        table = quoteIdentifier(source.table)
        parameters = []
        if filterText:
            if filterColumn not in source.columns or filterColumn not in available:
                raise ValueError('请选择要筛选的显示字段')
            pattern = filterText.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            table += ' WHERE CAST(' + quoteIdentifier(filterColumn) + " AS TEXT) LIKE ? ESCAPE '\\'"
            parameters.append('%' + pattern + '%')
        if aggregate == 'count':
            return connection.execute('SELECT COUNT(*) FROM ' + table, parameters).fetchone()[0]
        if source.valueColumn not in available:
            raise ValueError('请先选择有效的统计字段')
        column = quoteIdentifier(source.valueColumn)
        if aggregate in {'sum', 'avg'}:
            value, invalid = connection.execute(
                f'SELECT {aggregate}({column}), MAX(CASE WHEN {column} IS NOT NULL '
                f"AND typeof({column}) NOT IN ('integer', 'real') THEN 1 ELSE 0 END) FROM {table}", parameters).fetchone()
            if invalid:
                raise ValueError('统计字段包含非数值，不能求和或计算平均值')
            return value
        # Bound text/BLOB values after selecting the aggregate, not before:
        # truncating each row first would change the minimum/maximum itself.
        expression = _displayExpression('value')
        return connection.execute(
            f'SELECT {expression} FROM (SELECT {aggregate}({column}) AS value FROM {table})', parameters).fetchone()[0]
