"""SQLite fields are explicitly chosen and remain ordinary undoable page props."""
import json
import sqlite3

import pytest
from PySide2.QtCore import Qt

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from emo_master.apps.designer.page_designer.sqlite_properties import SqliteProperties
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import SqliteSource


def database(tmp_path):
    path = tmp_path / '历史记录.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE "检测记录" ("采集时间" TEXT, "结果" TEXT, "数值" REAL)')
        connection.execute('INSERT INTO "检测记录" VALUES (?, ?, ?)', ('2026-10-29', 'OK', 12.5))
        connection.execute('CREATE VIEW "合格记录" AS SELECT * FROM "检测记录" WHERE "结果" = \'OK\'')
    return path


def choose(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def testMetadataIsExplicitAndSelectionsHaveCorrectFields(qtApp, tmp_path, monkeypatch):
    path = database(tmp_path)
    selector = SqliteProperties()
    source = SqliteSource(databasePath=str(path), table='检测记录', columns=['结果', '数值'])
    import emo_master.core.presentation.sqlite_reader as reader
    inspect = reader.inspectDatabase
    calls = []
    def record(value):
        calls.append(value)
        return inspect(value)
    monkeypatch.setattr(reader, 'inspectDatabase', record)
    selector.load(source, 'sqlite_table')
    assert calls == [] and selector.selectedColumns() == ['结果', '数值']
    selector.readMetadata()
    assert calls == [str(path)]
    assert set(selector.schema) == {'检测记录', '合格记录'}
    assert selector.selectedColumns() == ['结果', '数值']
    choose(selector.orderBy, '采集时间')
    selector.direction.setCurrentIndex(selector.direction.findData(False))
    payload = selector.payload()
    assert payload['columns'] == ['结果', '数值']
    assert payload['orderBy'] == '采集时间' and payload['descending'] is False
    choose(selector.table, '合格记录')
    assert selector.selectedColumns() == ['采集时间', '结果', '数值']


def testReadFailurePreservesSavedChoices(qtApp, tmp_path):
    selector = SqliteProperties()
    path = tmp_path / 'missing.db'
    source = SqliteSource(databasePath=str(path), table='records', columns=['x'])
    selector.load(source, 'sqlite_table')
    before = selector.state()
    selector.readMetadata()
    assert selector.state() == before
    assert '读取失败' in selector.message.text()
    assert not path.exists()


def testSqliteChoicesApplyUndoCopyAndSave(designer, tmp_path):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'sqlite_table')
    selector = editor.tools.sqliteProperties
    path = database(tmp_path)
    selector.databasePath.setText(str(path))
    selector.readMetadata()
    choose(selector.table, '检测记录')
    for index in range(selector.columns.count()):
        item = selector.columns.item(index)
        item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in {'采集时间', '结果'} else Qt.Unchecked)
    choose(selector.orderBy, '采集时间')
    editor.tools.fields['pageSize'].setValue(30)
    before = coordinator.session.payload()
    assert not _component(editor.store.snapshot(), editor.pageId, key).props.sqlite.databasePath
    editor.tools.commitPending()
    item = _component(editor.store.snapshot(), editor.pageId, key)
    assert item.props.sqlite.columns == ['采集时间', '结果']
    assert item.props.pageSize == 30
    assert coordinator.session.undo()
    assert coordinator.session.payload() == before
    assert coordinator.session.redo()
    copy = editor.tools.commands().copy(editor.pageId, key)
    assert _component(editor.store.snapshot(), editor.pageId, copy).props == item.props
    assert designer.saveProjectToDirectory(str(tmp_path / 'saved'))
    saved = json.loads((tmp_path / 'saved/project.json').read_text(encoding='utf-8'))
    props = saved['presentation']['pages'][editor.pageId]['components'][0]['props']
    assert props['sqlite']['databasePath'] == str(path)
    assert props['sqlite']['columns'] == ['采集时间', '结果']
    assert not (tmp_path / 'saved' / path.name).exists()


def testSqliteStatisticsRequireValueFieldAndKeepPendingValues(designer, tmp_path):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'sqlite_value')
    selector = editor.tools.sqliteProperties
    selector.databasePath.setText(str(database(tmp_path)))
    selector.readMetadata()
    choose(selector.table, '检测记录')
    choose(selector.aggregate, 'avg')
    before = coordinator.session.payload()
    with pytest.raises(ValueError, match='统计字段'):
        editor.tools.commitPending()
    assert coordinator.session.payload() == before
    assert selector.aggregate.currentData() == 'avg'
    choose(selector.valueColumn, '数值')
    editor.tools.fields['unit'].setText(' mm')
    editor.tools.commitPending()
    props = _component(editor.store.snapshot(), editor.pageId, key).props
    assert props.sqlite.aggregate == 'avg' and props.sqlite.valueColumn == '数值'
    assert props.unit == ' mm'
    assert editor.tools.binding.parentWidget().isHidden()


def testPageTitleDefaultsAndTextCanFollowPageName(designer):  # noqa: F811
    _, editor, key = setupPage(designer, 'page_title')
    props = _component(editor.store.snapshot(), editor.pageId, key).props
    assert props.text == '' and props.fontSize == 24 and props.fontWeight == 'bold'
    editor.tools.fields['text'].setText('自定义看板')
    editor.tools.commitPending()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.text == '自定义看板'
