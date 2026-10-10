"""Portable data must outlive its Runtime session without losing exact values."""
from copy import deepcopy
import hashlib
import json
from zipfile import ZipFile

import numpy as np
import pytest

from emo_master.apps.designer.services.debug_artifacts import (
    exportRawResult, exportResult, loadFixture, readFixture, saveFixture, writeArchive,
)
from emo_master.apps.runtime.operator_debug.assets import imageBytes
from emo_master.apps.runtime.operator_debug.contracts import encode


class Connection:
    def __init__(self):
        self.assets = {}
        self.uploads = []
        self.records = {}

    def download(self, key):
        if key not in self.assets:
            raise ValueError('asset expired')
        return deepcopy(self.assets[key])

    def upload(self, raw, mime, provenance):
        key = 'new-' + str(len(self.uploads))
        self.uploads.append((raw, mime, provenance))
        self.assets[key] = dict(content=raw, asset=dict(mime=mime, provenance=provenance,
            bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        return {'assetRef': key}

    def call(self, method, **fields):
        assert method == 'GetOperatorDebugExecution'
        return self.records[fields['execution_id']]


def row(kind, value=None, mode='value'):
    return dict(type=kind, mode=mode, wire=None if mode == 'missing' else {'inline': value})


@pytest.mark.parametrize('value,kind', [(0, 'integer'), (-9, 'integer'), (1, 'integer'),
    (9, 'integer'), (True, 'boolean'), (False, 'boolean'), ('true', 'string'),
    ('', 'string'), (2.5, 'number'), (list(range(350)), 'list<integer>')])
def testFixtureKeepsTypedManualValues(tmp_path, value, kind):
    path = tmp_path / 'inputs.emofixture'
    source = Connection()
    rows = dict(value=row(kind, value), missing=row('integer', mode='missing'),
                null=row('object', None, 'null'))
    saveFixture(path, '中文输入集', rows, {'workflowId': 'main'}, {'value': 17}, source)
    loaded = loadFixture(path, {key: item['type'] for key, item in rows.items()}, Connection())
    assert loaded['values']['value']['wire']['inline'] == value
    assert type(loaded['values']['value']['wire']['inline']) is type(value)
    assert loaded['values']['missing']['wire'] is None
    assert loaded['values']['null']['wire'] == {'inline': None}
    assert loaded['parameters'] == {'value': 17} and loaded['name'] == '中文输入集'


def testFixtureMaterializesPairedImageAndFrameAcrossSessions(tmp_path):
    original, fresh = Connection(), Connection()
    raw = imageBytes(np.full((7, 11, 3), 123, np.uint8))
    provenance = dict(kind='inspection', captureId='same-invocation', jobId='job', iterationPath=[2])
    image = original.upload(raw, 'image/png', provenance)
    frame = original.upload(encode({'width': 11, 'height': 7}).encode(), 'application/json', provenance)
    rows = dict(image=dict(type='image', mode='source', wire=image, companionPort='frame'),
                frame=dict(type='object', mode='source', wire=frame, companionPort=None))
    path = tmp_path / 'paired.emofixture'
    saveFixture(path, 'paired', rows, {'nodeId': 'blob'}, {}, original)
    original.assets.clear()  # original session has expired
    loaded = loadFixture(path, {'image': 'image', 'frame': 'object'}, fresh)
    assert len(fresh.uploads) == 2 and next(item for item in fresh.uploads if item[1] == 'image/png')[0] == raw
    assert fresh.uploads[0][2]['captureId'] == fresh.uploads[1][2]['captureId'] == 'same-invocation'
    assert fresh.download(loaded['values']['image']['wire']['assetRef'])['content'] == raw
    manifest, files = readFixture(path, {'image': 'image', 'frame': 'object'})
    assert manifest['ports']['image']['companionPort'] == 'frame'
    assert all('assetRef' not in item for item in manifest['ports'].values())
    assert len(files) == 2


@pytest.mark.parametrize('change', ['hash', 'path', 'version', 'type', 'extra', 'duplicate', 'value-type', 'name', 'parameters', 'companion'])
def testInvalidFixtureDoesNotUploadOrPartiallyApply(tmp_path, change):
    original = Connection()
    ref = original.upload(imageBytes(np.zeros((2, 3), np.uint8)), 'image/png', {'kind': 'upload'})
    path = tmp_path / 'bad.emofixture'
    saveFixture(path, 'test', dict(image=dict(type='image', mode='source', wire=ref), count=row('integer', 1)), {}, {}, original)
    manifest, files = readFixture(path, {'image': 'image', 'count': 'integer'})
    if change == 'hash':
        files[next(iter(files))] = b'changed'
    elif change == 'path':
        manifest['ports']['image']['file'] = '../outside.png'
    elif change == 'version':
        manifest['version'] = 2
    elif change == 'type':
        manifest['ports']['count']['type'] = 'string'
    elif change == 'extra':
        files['extra.json'] = b'{}'
    elif change == 'value-type':
        manifest['ports']['count']['value'] = True
    elif change == 'name':
        manifest['name'] = ['not a name']
    elif change == 'parameters':
        manifest['parameters'] = None
    elif change == 'companion':
        manifest['ports']['image']['companionPort'] = 'wrong-frame'
    writeArchive(path, manifest, files)
    if change == 'duplicate':
        with ZipFile(path, 'a') as archive:
            archive.writestr('METADATA.JSON', b'{}')
    fresh = Connection()
    with pytest.raises((ValueError, RuntimeError)):
        loadFixture(path, {'image': 'image', 'count': 'integer'}, fresh)
    assert fresh.uploads == []
    assert not (tmp_path / 'outside.png').exists()


def testFixtureRejectsMixedImageFrameCaptureBeforeAnyUpload(tmp_path):
    original = Connection()
    image = original.upload(imageBytes(np.zeros((2, 3), np.uint8)), 'image/png', {'captureId': 'first'})
    frame = original.upload(b'{}', 'application/json', {'captureId': 'second'})
    rows = dict(image=dict(type='image', mode='source', wire=image, companionPort='frame'),
                frame=dict(type='object', mode='source', wire=frame))
    path = tmp_path / 'mixed.emofixture'
    saveFixture(path, 'mixed', rows, {}, {}, original)
    fresh = Connection()
    with pytest.raises(ValueError, match='同一完整来源'):
        loadFixture(path, {'image': 'image', 'frame': 'object'}, fresh)
    assert not fresh.uploads


@pytest.mark.parametrize('mime', ['image/png', 'application/json'])
def testCompleteResultExportPreservesExactRawAssetAndIdentity(tmp_path, mime):
    connection = Connection()
    raw = imageBytes(np.full((9, 13, 3), 191, np.uint8)) if mime == 'image/png' else encode(list(range(1200))).encode()
    wire = connection.upload(raw, mime, dict(kind='debug-output', captureId='run2'))
    path = tmp_path / 'result.emodebug.zip'
    identity = dict(projectId='p', workflowId='child', nodeId='blob', nodeRunId='call2',
                    iterationPath=[2, 1], snapshotKind='trial', selection='history')
    exportResult(path, dict(port='outputs/value', wire=wire, identity=identity), connection)
    with ZipFile(path) as archive:
        metadata = json.loads(archive.read('metadata.json'))
        assert metadata['identity'] == identity
        assert archive.read(metadata['file']) == raw
        assert metadata['sha256'] == hashlib.sha256(raw).hexdigest()
        if mime == 'application/json':
            assert len(json.loads(archive.read(metadata['file']))) == 1200


def testExportInlineAndOutputReferenceAreComplete(tmp_path):
    connection = Connection()
    connection.records['old'] = dict(status='SUCCEEDED', outputs={'numbers': list(range(500))},
                                    sourceIdentity={'workflowId': 'main'})
    path = tmp_path / 'reference.emodebug.zip'
    exportResult(path, dict(port='numbers', identity={'executionId': 'old'},
        wire={'executionId': 'old', 'port': 'numbers'}), connection)
    with ZipFile(path) as archive:
        assert json.loads(archive.read('value.json')) == list(range(500))
        assert json.loads(archive.read('metadata.json'))['provenance']['captureId'] == 'old'


def testCancelledOrExpiredExportKeepsExistingTargetAndNoTemporaryFiles(tmp_path):
    path = tmp_path / 'kept.zip'
    path.write_bytes(b'original')
    with pytest.raises(ValueError, match='取消'):
        writeArchive(path, {}, {'value.json': b'{}'}, cancelled=lambda: True)
    with pytest.raises(ValueError, match='expired'):
        exportResult(path, dict(port='image', identity={}, wire={'assetRef': 'expired'}), Connection())
    assert path.read_bytes() == b'original'
    assert [item.name for item in tmp_path.iterdir()] == ['kept.zip']


@pytest.mark.parametrize('mime,suffix', [('image/png', '.png'), ('application/json', '.json')])
def testRawExportKeepsExactBytesAndDoesNotPretendToHaveMetadata(tmp_path, mime, suffix):
    connection = Connection()
    raw = imageBytes(np.zeros((7, 13), np.uint8)) if mime == 'image/png' else encode(list(range(1300))).encode()
    selection = dict(wire=connection.upload(raw, mime, {'kind': 'debug-output'}))
    path = tmp_path / ('原始结果' + suffix)
    exportRawResult(path, selection, connection)
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match='取消'):
        exportRawResult(path, selection, connection, cancelled=lambda: True)
    assert path.read_bytes() == raw
