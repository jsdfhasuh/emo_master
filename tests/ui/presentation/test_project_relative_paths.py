"""Local history follows the opened project rather than the process directory."""
import json
from pathlib import Path
import shutil
import sqlite3
import time
from types import SimpleNamespace

import pytest
from PySide2.QtGui import QColor, QImage

from emo_master.apps.designer.page_designer.sqlite_properties import SqliteProperties
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.core.presentation.models import Presentation, SqliteSource
from emo_master.core.presentation.sqlite_reader import inspectDatabase, readPage, readValue
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.ui.presentation.renderer import RuntimePages


def settle(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.005)


def project(root):
    outputs = root / 'outputs'
    outputs.mkdir(parents=True)
    image = QImage(40, 30, QImage.Format_RGB32)
    image.fill(QColor('red'))
    assert image.save(str(outputs / 'frame.png'))
    with sqlite3.connect(outputs / 'history.db') as connection:
        connection.execute('CREATE TABLE records (id INTEGER, image TEXT)')
        connection.execute('INSERT INTO records VALUES (?, ?)', (1, 'outputs/frame.png'))
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    document.presentation = Presentation.model_validate(dict(defaultPageId='history', pageOrder=['history'], pages={
        'history': dict(name='历史', layout={'columns': 2}, components=[
            dict(componentId='records', type='sqlite_table', props={'sqlite': dict(
                databasePath='outputs/history.db', table='records', columns=['id'])}),
            dict(componentId='image', type='image', layout={'column': 1}, props={'imagePath': dict(
                kind='selected_row', componentId='records', field='image', baseDirectory='.')})])}))
    assert validateBindings(document, {}, publish=True) == []
    saveProject(root / 'history.emoproj', document.model_dump(mode='json'))
    return document


def testMovingProjectKeepsRelativeConfigurationAndLoadsFromNewRoot(qtApp, tmp_path, monkeypatch):
    original = tmp_path / 'original'
    document = project(original)
    moved = tmp_path / 'moved'
    shutil.copytree(original, moved)
    # Use a different image to prove neither cwd nor a stale source root won.
    image = QImage(40, 30, QImage.Format_RGB32)
    image.fill(QColor('blue'))
    assert image.save(str(moved / 'outputs/frame.png'))
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    loaded = loadProjectDocument(moved / 'history.emoproj')
    assert loaded.presentation == document.presentation
    WorkflowCompiler().compile(loaded)
    before = json.dumps(loaded.presentation.model_dump(mode='json'), sort_keys=True)
    window = RuntimePages(loaded.presentation, projectRoot=original)
    window.show()
    table = window.widgets['history']['records'][1]
    imageView = window.widgets['history']['image'][1]
    try:
        settle(qtApp, lambda: table.loaded and not table.busy)
        table.table.selectRow(0)
        settle(qtApp, lambda: not imageView.image.isNull())
        assert imageView.image.pixelColor(0, 0) == QColor('red')
        # Save As / a changed local project directory invalidates old selection.
        window.setProjectRoot(moved)
        assert table.selectedRow() is None and imageView.image.isNull()
        table.refreshButton.click()
        settle(qtApp, lambda: table.loaded and not table.busy)
        table.table.selectRow(0)
        settle(qtApp, lambda: not imageView.image.isNull())
        assert imageView.image.pixelColor(0, 0) == QColor('blue')
        assert Path(imageView.key) == moved / 'outputs/frame.png'
        assert json.dumps(window.config.model_dump(mode='json'), sort_keys=True) == before
        assert json.dumps(loaded.presentation.model_dump(mode='json'), sort_keys=True) == before
        assert not any(unrelated.iterdir())
    finally:
        window.close()


def testRelativeDatabaseNeverFallsBackToCwd(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.chdir(tmp_path)
    source = SqliteSource(databasePath='outputs/history.db', table='records', columns=['id'])
    for query in (lambda: readPage(source), lambda: readValue(source),
                  lambda: inspectDatabase(source.databasePath)):
        with pytest.raises(ValueError, match='已打开工程'):
            query()
    assert readPage(source, projectRoot=tmp_path).rows == ((1,),)
    assert readValue(source, projectRoot=tmp_path) == 1
    assert inspectDatabase(source.databasePath, projectRoot=tmp_path)['records'] == ['id', 'image']
    assert source.databasePath == 'outputs/history.db'


def testPropertyMetadataUsesCurrentProjectRootWithoutRewritingPath(qtApp, tmp_path, monkeypatch):
    project(tmp_path / 'project')
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.chdir(other)
    selector = SqliteProperties(projectRoot=lambda: tmp_path / 'project')
    selector.load(SqliteSource(databasePath='outputs/history.db', table='records', columns=['id']), 'sqlite_table')
    selector.readMetadata()
    assert selector.schema['records'] == ['id', 'image']
    assert selector.payload()['databasePath'] == 'outputs/history.db'
    assert not any(other.iterdir())


def testDesignerOpenSaveAsReopenResolvesRelativeHistory(qtApp, tmp_path, monkeypatch):
    original, moved = tmp_path / 'original', tmp_path / 'moved'
    project(original)
    shutil.copytree(original, moved)
    unrelated = tmp_path / 'cwd'
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    preferences = {}
    settings = SimpleNamespace(value=lambda key, default=None: preferences.get(key, default),
                               setValue=lambda key, value: preferences.update({key: value}))
    client = SimpleNamespace(listOperators=lambda: [], loadProject=lambda path: SimpleNamespace(ok=True, message='ok'))
    window = MainWindow(client, settingsStore=settings)
    try:
        assert window.loadProjectDirectory(str(original / 'history.emoproj'))
        window.pageCoordinator.showPages()
        editor = window.pageCoordinator.editor
        editor.tools.select('records')
        editor.tools.sqliteProperties.readMetadata()
        assert editor.tools.sqliteProperties.schema['records'] == ['id', 'image']
        observer = window.pageCoordinator.preview.openObserver()
        observer.source.setCurrentIndex(2)
        settle(qtApp, lambda: not window.pageCoordinator.preview.busy)
        assert Path(observer.projectRoot) == original
        assert window.saveProjectToDirectory(str(moved / 'history.emoproj'))
        assert Path(observer.projectRoot) == moved
        assert Path(editor.renderer.projectRoot) == moved
        assert window.loadProjectDirectory(str(moved / 'history.emoproj'), confirmed=True)
        window.pageCoordinator.showPages()
        observer = window.pageCoordinator.preview.openObserver()
        observer.source.setCurrentIndex(2)
        settle(qtApp, lambda: not window.pageCoordinator.preview.busy)
        table = observer.widgets['history']['records'][1]
        table.refreshButton.click()
        settle(qtApp, lambda: table.loaded and not table.busy)
        assert table.model.rows == ((1,),)
        table.table.selectRow(0)
        image = observer.widgets['history']['image'][1]
        settle(qtApp, lambda: not image.image.isNull())
        assert Path(image.key) == moved / 'outputs/frame.png'
        saved = loadProjectDocument(moved / 'history.emoproj')
        assert saved.presentation.pages['history'].components[0].props.sqlite.databasePath == 'outputs/history.db'
        assert saved.presentation.pages['history'].components[1].props.imagePath.baseDirectory == '.'
        assert not any(unrelated.iterdir())
    finally:
        if window.pageCoordinator.preview.observer:
            window.pageCoordinator.preview.observer.close()
        window.close()
        window.deleteLater()
