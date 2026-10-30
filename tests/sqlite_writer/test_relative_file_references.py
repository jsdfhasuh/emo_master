"""Portable references keep the existing image-save and SQLite write contracts."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from emo_master.apps.runtime.business_sqlite.backend import freezeTargets, initialize
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, parseConfig, projectRelativeFileReference, SqliteWriterError
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.parameter_bindings import BoundValue
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator
from tests.sqlite_writer.test_dependencies import config, mapping, project


def referenceDocument(root, *, mode=None, output=None):
    rows = [mapping(column='picture', node='save', port='result', storage='FILE_REFERENCE')]
    params = config('results.sqlite3', rows)
    if mode is not None:
        params['fileReferencePathMode'] = mode
    nodes = [
        {'nodeId': 'save', 'operatorId': ImageSaverOperator.meta.operatorId,
         'params': {'outputPath': str(output or root / 'outputs' / '骑手.png'), 'overwrite': False}},
        {'nodeId': 'writer', 'operatorId': OPERATOR_ID, 'params': params},
    ]
    # Reuse the existing compatibility fixture; this is not a new delivery project.
    payload = project(nodes=nodes, edges=[{'fromNode': 'input', 'fromPort': 'image', 'toNode': 'save', 'toPort': 'image'}])
    payload['workflows']['main']['inputs'] = {'image': 'image'}
    return ProjectDocument.model_validate(payload)


@pytest.mark.parametrize('mode', [None, 'absolute', 'project_relative'])
def testRealImageSaveBindingAndSqliteWriteSurviveSnapshotAndDifferentCwd(tmp_path, monkeypatch, mode):
    root = tmp_path / '工程'
    root.mkdir()
    database = root / 'results.sqlite3'
    initialize(database, 'records', [{'name': 'picture', 'storageType': 'FILE_REFERENCE', 'nullable': False}])
    document = referenceDocument(root, mode=mode)
    original = deepcopy(document.model_dump())
    frozen = freezeTargets(document, root)
    params = next(n.params for n in frozen.workflows['main'].nodes if n.operatorId == OPERATOR_ID)
    assert params.get('_fileReferenceProjectRoot') == (str(root.resolve()) if mode == 'project_relative' else None)
    assert document.model_dump() == original
    # The execution snapshot crosses a process boundary as JSON.
    frozen = ProjectDocument.model_validate_json(frozen.model_dump_json())
    workspace = tmp_path / 'different-workspace'
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    registry = {OPERATOR_ID: SqliteWriterOperator, ImageSaverOperator.meta.operatorId: ImageSaverOperator}
    compiled = WorkflowCompiler(registry).compile(frozen)
    runner = WorkflowRunner(compiled, registry)
    runner.run('main', {'image': np.full((8, 12, 3), 90, np.uint8)},
               RunContext.root('portable-write', 'main', workspacePath=str(workspace)), CancellationToken())
    with sqlite3.connect(database) as connection:
        stored, writeId = connection.execute('SELECT picture, write_id FROM records').fetchone()
    assert stored == ('outputs/骑手.png' if mode == 'project_relative' else str(root / 'outputs' / '骑手.png'))
    assert writeId
    assert (root / stored).is_file()


def testFreezeReplacesPreviousSourceRootWithoutChangingDraft(tmp_path):
    root = tmp_path / 'moved'
    root.mkdir()
    initialize(root / 'results.sqlite3', 'records', [{'name': 'picture', 'storageType': 'FILE_REFERENCE'}])
    document = referenceDocument(root, mode='project_relative')
    writer = next(n for n in document.workflows['main'].nodes if n.operatorId == OPERATOR_ID)
    writer.params['_fileReferenceProjectRoot'] = str(tmp_path / 'old')
    frozen = freezeTargets(document, root)
    assert next(n.params['_fileReferenceProjectRoot'] for n in frozen.workflows['main'].nodes if n.operatorId == OPERATOR_ID) == str(root)
    assert writer.params['_fileReferenceProjectRoot'] == str(tmp_path / 'old')


@pytest.mark.parametrize('problem', ['missing_root', 'outside', 'missing_file', 'wrong_identity'])
def testRelativeReferencesFailBeforeInsertAndKeepKnownFailureReceipt(tmp_path, problem):
    root = tmp_path / 'source'
    root.mkdir()
    picture = (tmp_path if problem == 'outside' else root) / 'saved.png'
    if problem != 'missing_file':
        picture.write_bytes(b'persisted-image')
    params = config(root / 'records.sqlite3', [mapping(storage='FILE_REFERENCE')])
    params.update(fileReferencePathMode='project_relative')
    if problem != 'missing_root':
        params['_fileReferenceProjectRoot'] = str(root)
    inserted = []
    context = {'workflowRunId': 'current', 'mappedOutputs': {0: BoundValue(
        'stale' if problem == 'wrong_identity' else 'current', 'saved-node', str(picture))},
        'sqliteInsert': lambda *args: inserted.append(args)}
    result = SqliteWriterOperator().executeNode({}, params, context)
    assert result['status'] == 'error'
    assert not inserted
    assert result['diagnostics']['sqliteReceipt']['status'] == 'FAILED'
    assert result['error']['code'] == {'missing_root': 'E_SQLITE_TARGET', 'outside': 'E_SQLITE_VALUE',
                                      'missing_file': 'E_SQLITE_VALUE', 'wrong_identity': 'E_SQLITE_IDENTITY'}[problem]


def testRelativeModeRetainsNullAndRejectsInvalidMode():
    assert projectRelativeFileReference(None, '') is None
    with pytest.raises(SqliteWriterError, match='图片引用路径'):
        parseConfig({**config(), 'fileReferencePathMode': 'relative_to_cwd'})


def testManifestAndPythonMetaPublishSameOptionalParameter():
    manifest = json.loads(Path('src/emo_master/plugins/builtins/sqlite_writer/manifest.json').read_text(encoding='utf-8'))
    assert manifest['version'] == SqliteWriterOperator.meta.version == '1.1.0'
    assert manifest['paramSchema'] == SqliteWriterOperator.meta.paramSchema
    assert manifest['paramSchema']['properties']['fileReferencePathMode']['default'] == 'absolute'
