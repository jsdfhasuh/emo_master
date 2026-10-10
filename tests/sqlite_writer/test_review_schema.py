"""Table flags come from SQLite metadata, never text inside defaults or names."""
import sqlite3

import pytest

from emo_master.apps.runtime.business_sqlite.backend import initialize, inspect, tableStructure
from emo_master.core.contracts.sqlite_writer import SqliteWriterError, quoteIdentifier
from tests.sqlite_writer.test_backend import actualRun


@pytest.mark.parametrize('literal', ['WITHOUT ROWID', 'CREATE VIRTUAL TABLE', "quoted ' WITHOUT ROWID"])
def testKeywordDefaultsInitializeAndRealRunnerWritesNormally(tmp_path, literal):
    path = tmp_path / 'business.sqlite3'
    plan = [{'name': 'value', 'storageType': 'TEXT', 'default': literal}]
    answer = initialize(path, 'records', plan)
    assert answer['structure']['columns'][0]['autoPrimaryKey']
    initialize(path, 'records', plan)
    receipt, _, _ = actualRun(path, None, rows=[{'column': 'value', 'storageType': 'TEXT',
                                              'source': {'kind': 'constant', 'value': 'actual'}}])
    assert receipt['status'] == 'COMMITTED' and receipt['primaryKey'] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT value FROM records').fetchall() == [('actual',)]


class OldMetadataConnection(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        if sql.startswith('PRAGMA main.table_list'):
            # Older SQLite silently returns no rows for unsupported PRAGMAs.
            return super().execute('SELECT 1 WHERE 0')
        return super().execute(sql, *args, **kwargs)


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('flags', ['', 'WITHOUT ROWID'])
def testQuotedKeywordsCommentsAndTrueWithoutRowidAreDistinguished(tmp_path, legacy, flags):
    path = tmp_path / 'quoted.sqlite3'
    factory = OldMetadataConnection if legacy else sqlite3.Connection
    table = 'CREATE VIRTUAL TABLE'
    with sqlite3.connect(path, factory=factory) as connection:
        connection.execute(f'CREATE TABLE {quoteIdentifier(table)} (id INTEGER PRIMARY KEY, '
                           '"WITHOUT ROWID" TEXT DEFAULT \'CREATE VIRTUAL TABLE\', '
                           'value TEXT CHECK(value != \'WITHOUT ROWID\')) /* WITHOUT ROWID */ ' + flags)
        result = tableStructure(connection, table)
        assert result['columns'][0]['autoPrimaryKey'] == (not flags)
        assert len(result['columns']) == 3


@pytest.mark.parametrize('legacy', [False, True])
def testVirtualAndViewRemainRejectedAndSameNamedTriggerCannotHideTable(tmp_path, legacy):
    path = tmp_path / 'types.sqlite3'
    factory = OldMetadataConnection if legacy else sqlite3.Connection
    with sqlite3.connect(path, factory=factory) as connection:
        connection.execute('CREATE TABLE other(value TEXT)')
        connection.execute('CREATE TRIGGER records AFTER INSERT ON other BEGIN SELECT 1; END')
        connection.execute('CREATE TABLE records(value TEXT)')
        assert tableStructure(connection, 'records')['table'] == 'records'
        connection.execute('CREATE VIEW v AS SELECT value FROM other')
        connection.execute('CREATE VIRTUAL TABLE f USING fts5(value)')
        for table in ('v', 'f'):
            with pytest.raises(SqliteWriterError) as failed:
                tableStructure(connection, table)
            assert failed.value.code == 'E_SQLITE_SCHEMA'
    assert inspect(path, 'records')['structure']['table'] == 'records'
