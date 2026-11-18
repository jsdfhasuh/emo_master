"""Small, read-only queries for local SQLite page components."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
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
    keyValues: tuple[tuple, ...] = ()
    selectedRowId: str | None = None
    stableRowIdentity: bool = False


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


def _keyColumns(connection, source, available):
    if source.recordKey:
        if source.recordKey not in available:
            raise ValueError('记录键字段不存在，请重新选择')
        return [quoteIdentifier(source.recordKey)]
    table = quoteIdentifier(source.table)
    primary = sorted((row for row in connection.execute(f'PRAGMA table_info({table})') if row[5]),
                     key=lambda row: row[5])
    if primary:
        return [quoteIdentifier(row[1]) for row in primary]
    kind = connection.execute('SELECT type FROM sqlite_master WHERE name = ?', (source.table,)).fetchone()[0]
    if kind == 'table':
        # These are SQLite-reserved aliases unless shadowed by declared columns.
        return [name for name in ('_rowid_', 'rowid', 'oid') if name not in available][:1]
    return []


def _keyValid(values):
    return all(type(value) in (str, int, float)
               and (type(value) is not float or math.isfinite(value)) for value in values)


def _rowId(values):
    return hashlib.sha256(json.dumps(list(values), ensure_ascii=True).encode()).hexdigest()


def _order(sort, descending, identity, *, reverse=False):
    return ', '.join([sort + (' DESC' if descending != reverse else ' ASC')] +
                     [column + (' DESC' if reverse else ' ASC') for column in identity])


def _sideOf(sort, descending, identity, point, *, before):
    """Compare with the same NULL ordering and directions as ORDER BY."""
    value, *key = point
    smaller = before != descending
    if value is None:
        different, parameters = ('0' if smaller else f'{sort} IS NOT NULL'), []
    else:
        different = (f'{sort} IS NULL OR {sort} < ?' if smaller else f'{sort} > ?')
        parameters = [value]
    if len(identity) == 1:
        columns, placeholders = identity[0], '?'
    else:
        columns = '(' + ', '.join(identity) + ')'
        placeholders = '(' + ', '.join('?' for _ in identity) + ')'
    operation = '<' if before else '>'
    tie = f'{sort} IS ? AND {columns} {operation} {placeholders}'
    return f'(({different}) OR ({tie}))', (*parameters, value, *key)


def _locateAnchor(connection, table, where, parameters, sort, descending, identity, key, step):
    if not identity or len(key) != len(identity) or not _keyValid(key):
        raise ValueError('无有效的稳定记录键')
    pointSql = f'SELECT {", ".join([sort, *identity])} FROM {table} WHERE {where}'
    equalKey = ' AND '.join(f'{column} IS ?' for column in identity)
    rows = connection.execute(f'{pointSql} AND {equalKey} LIMIT 2', (*parameters, *key)).fetchall()
    if not rows:
        raise ValueError('所选记录已不存在或不在当前筛选结果内')
    if len(rows) != 1:
        raise ValueError('记录键重复，无法稳定定位')
    point = rows[0]
    if step:
        before = step < 0
        side, sideParameters = _sideOf(sort, descending, identity, point, before=before)
        orderBy = _order(sort, descending, identity, reverse=before)
        neighbor = connection.execute(f'{pointSql} AND {side} ORDER BY {orderBy} LIMIT 1',
                                      (*parameters, *sideParameters)).fetchone()
        if neighbor is not None:
            point = neighbor
    if not _keyValid(point[1:]):
        raise ValueError('记录键必须是非空的数字或文字')
    # A duplicate could straddle a page boundary, so check the actual target
    # explicitly while remaining inside this request's short read transaction.
    if step and point != rows[0]:
        matching = connection.execute(f'{pointSql} AND {equalKey} LIMIT 2',
                                      (*parameters, *point[1:])).fetchall()
        if len(matching) != 1:
            raise ValueError('记录键重复，无法稳定定位')
    side, sideParameters = _sideOf(sort, descending, identity, point, before=True)
    position = connection.execute(f'SELECT COUNT(*) FROM {table} WHERE {where} AND {side}',
                                  (*parameters, *sideParameters)).fetchone()[0]
    return position, _rowId(point[1:])


def readPage(source, *, pageSize=20, page=0, filterColumn='', filterText='',
             orderBy=None, descending=None, selectionFields=(), projectRoot=None,
             anchorKey=None, anchorStep=0, requiresIdentity=False) -> SqlitePage:
    if not 1 <= pageSize <= 100 or page < 0:
        raise ValueError('分页参数无效')
    if anchorStep not in (-1, 0, 1) or (anchorKey is None and anchorStep):
        raise ValueError('历史翻条参数无效')
    if anchorKey is not None and not isinstance(anchorKey, (list, tuple)):
        raise ValueError('记录键必须是标量数组')
    with _connection(source.databasePath, projectRoot) as connection:
        if anchorKey is not None:
            connection.execute('BEGIN')
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
        where, parameters = '1', []
        if filterText:
            if filterColumn not in columns:
                raise ValueError('请选择要筛选的显示字段')
            pattern = filterText.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where = 'CAST(' + quoteIdentifier(filterColumn) + " AS TEXT) LIKE ? ESCAPE '\\'"
            parameters.append('%' + pattern + '%')
        expressions = [_displayExpression(c) for c in columns]
        expressions.extend(expression for field in fields for expression in _selectionExpressions(field, available))
        identity = _keyColumns(connection, source, available)
        if requiresIdentity and not identity:
            raise ValueError('此历史来源需要声明稳定记录键，才能查看同条详情或逐条浏览')
        identityOffset = len(expressions)
        expressions.extend(identity)
        table, sort = quoteIdentifier(source.table), quoteIdentifier(sort)
        selectedRowId = None
        if anchorKey is not None:
            position, selectedRowId = _locateAnchor(connection, table, where, parameters, sort,
                                                     descending, identity, anchorKey, anchorStep)
            page = position // pageSize
        sql = (f'SELECT {", ".join(expressions)} FROM {table} WHERE {where} '
               f'ORDER BY {_order(sort, descending, identity)} LIMIT ? OFFSET ?')
        rows = connection.execute(sql, (*parameters, pageSize + 1, page * pageSize)).fetchall()
        keys = tuple(tuple(row[identityOffset:]) for row in rows[:pageSize])
        rowIds = tuple(_rowId(key) if key and _keyValid(key) else '' for key in keys)
        if identity and (not all(rowIds) or len(set(rowIds)) != len(rowIds)):
            raise ValueError('记录键为空、重复或不是数字或文字，无法稳定定位')
        selected = tuple({field: _selectionValue(row[len(columns) + index * 2], row[len(columns) + index * 2 + 1])
                          for index, field in enumerate(fields)} for row in rows[:pageSize])
        return SqlitePage(tuple(columns), tuple(tuple(row[:len(columns)]) for row in rows[:pageSize]),
                          page, len(rows) > pageSize, selected, rowIds, keys, selectedRowId, bool(identity))


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
