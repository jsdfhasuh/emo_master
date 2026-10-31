"""Actual Designer properties retain endpoint ownership through normal editing."""
from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from emo_master.apps.designer.page_designer.sqlite_properties import SqliteProperties
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import SqliteSource


def choose(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def testPageTargetsApplyUndoAndKeepWebPageInEditor(designer, tmp_path):  # noqa: F811
    coordinator, editor, _ = setupPage(designer)
    pageId = editor.pageId
    editor.tools.select(None)
    before = coordinator.session.payload()
    choose(editor.tools.pageTargets, ['web'])
    editor.tools.commitPending()
    assert editor.store.snapshot().pages[editor.pageId].targets == ['web']
    editor.refresh()
    assert editor.pageId in editor.renderer.config.pages
    assert coordinator.session.undo() and coordinator.session.payload() == before
    assert coordinator.session.redo()
    assert designer.saveProjectToDirectory(str(tmp_path / 'web.emoproj'))
    assert designer.loadProjectDirectory(str(tmp_path / 'web.emoproj'))
    assert coordinator.session.document().presentation.pages[pageId].targets == ['web']


def testRuntimeSqliteMetadataUsesSelectedEndpointOnly(qtApp, monkeypatch):
    calls = []
    def inspect(path, table):
        calls.append((path, table))
        return {'records': ['time', 'image']}
    selector = SqliteProperties(inspectRuntime=inspect)
    selector.load(SqliteSource(databasePath='history.db', table='records', columns=['image'], location='runtime'), 'sqlite_table')
    assert not calls and not selector.chooseButton.isEnabled()
    from emo_master.core.presentation import sqlite_reader
    monkeypatch.setattr(sqlite_reader, 'inspectDatabase', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('local read')))
    selector.readMetadata()
    assert calls == [('history.db', 'records')]
    assert selector.payload()['location'] == 'runtime'
    assert selector.payload()['columns'] == ['image']


def testImageLocationAndInheritedTableLocationAreEditable(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'image')
    form = editor.tools.imageProperties
    choose(form.mode, 'path')
    choose(form.location, 'runtime')
    form.path.setText('today.png')
    form.baseDirectory.setText('images')
    assert not form.chooseFile.isEnabled() and not form.chooseDirectory.isEnabled()
    editor.tools.commitPending()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.imagePath.location == 'runtime'
    table = editor.tools.commands().add(editor.pageId, 'sqlite_table', 1, 0)
    coordinator.session.editPresentation(lambda p: setattr(_component(p, editor.pageId, table).props.sqlite, 'location', 'runtime'))
    editor.refresh()
    editor.tools.select(key)
    choose(form.kind, 'selected_row')
    choose(form.component, table)
    form.field.setEditText('image')
    editor.tools.commitPending()
    source = _component(editor.store.snapshot(), editor.pageId, key).props.imagePath
    assert source.location is None and source.componentId == table
    assert '继承' in form.hint.text() and not form.chooseDirectory.isEnabled()
