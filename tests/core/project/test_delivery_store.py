import json
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest

from emo_master.core.project.delivery_store import DeliveryStore, DirectoryOwner, archiveContents
from emo_master.core.project.package_builder import buildPageTestPackage
from emo_master.core.project.test_delivery import canonicalJson, sha, trustedRegistry
from examples.runtime_pages_p2 import sampleProject


@pytest.fixture
def delivered(tmp_path):
    root = tmp_path/'draft'
    root.mkdir()
    doc = sampleProject(root)
    registry = trustedRegistry()
    package = buildPageTestPackage(doc, root, tmp_path/'out', registry)
    store = DeliveryStore(tmp_path/'中文 空目录', registry)
    revision = store.importPackage(package)
    assert store.state()['active'] is None
    store.activate(revision)
    return doc, root, package, store, revision


def rewrite(package, target, modify):
    with zipfile.ZipFile(package) as source:
        files = {n: source.read(n) for n in source.namelist()}
    modify(files)
    with zipfile.ZipFile(target, 'w') as output:
        for name, data in files.items():
            output.writestr(name, data)
    return target


def testRelocationActivationRollbackAndExecutionOwnership(delivered, tmp_path, monkeypatch):
    doc, root, package, store, revision = delivered
    doc.presentation.pages['main'].name = 'second immutable version'
    second = store.importPackage(buildPageTestPackage(doc, root, tmp_path/'out', store.registry))
    assert store.state()['active'] == revision
    root.rename(tmp_path/'unavailable-draft')
    monkeypatch.chdir(tmp_path)
    loaded, _ = store.verify(second)
    assert (store.revisionPath(second)/loaded.resources.items['input'].path).is_file()
    with store.selected() as (_, _, directory):
        assert directory.name == revision
        for action in [lambda: store.activate(second), store.rollback, lambda: store.importPackage(package)]:
            with pytest.raises(RuntimeError, match='in use'):
                action()
    store.activate(second)
    assert store.rollback() == revision
    assert store.state() == {'active': revision, 'previous': second}
    (store.revisionPath(second)/'input.png').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='digest|size'):
        store.rollback()
    assert store.state()['active'] == revision
    assert not list(store.root.glob('.stage-*'))


@pytest.mark.parametrize('failure', ['missing', 'digest', 'version', 'script', 'project-schema', 'component'])
def testBadPackageDoesNotReplaceActive(delivered, tmp_path, failure):
    _, _, package, store, revision = delivered
    def mutate(files):
        if failure == 'missing':
            del files['input.png']
        elif failure == 'digest':
            files['input.png'] = b'wrong'
        elif failure == 'script':
            files['plugin.py'] = b'raise RuntimeError()'
        else:
            manifest = json.loads(files['manifest.json'])
            if failure == 'version':
                manifest['compatibility']['application'] = '999.0'
            else:
                project = json.loads(files['project.json'])
                if failure == 'project-schema':
                    project['schemaVersion'] = '999.0'
                else:
                    project['presentation']['pages']['main']['components'][0]['version'] = '999.0'
                files['project.json'] = canonicalJson(project).encode()
                manifest['files']['project.json'] = {'size': len(files['project.json']), 'sha256': sha(files['project.json'])}
            manifest.pop('revision')
            manifest['revision'] = sha(canonicalJson(manifest).encode())
            files['manifest.json'] = canonicalJson(manifest).encode()
    bad = rewrite(package, tmp_path/'bad.vxpkg', mutate)
    sentinel = store.root/'runtime-data.sqlite3'
    sentinel.write_bytes(b'preserve')
    with pytest.raises((ValueError, KeyError)):
        store.importPackage(bad)
    assert store.state()['active'] == revision
    assert sentinel.read_bytes() == b'preserve'
    assert not list(store.root.glob('.stage-*'))


@pytest.mark.parametrize('name', ['../outside.txt', '/root.txt', 'a\\b', 'nul.png', 'a./b', 'Input.png', 'project.json/x'])
def testUnsafePathsNeverExtract(delivered, tmp_path, name):
    _, _, package, store, revision = delivered
    bad = rewrite(package, tmp_path/'unsafe.zip', lambda f: f.update({name: b'x'}))
    with pytest.raises(ValueError):
        store.importPackage(bad)
    assert store.state()['active'] == revision
    assert not (tmp_path/'outside.txt').exists()
    assert not list(store.root.glob('.stage-*'))


def testDuplicateLinksAndSizeBudgetsBeforeExtraction(delivered, tmp_path):
    _, _, package, store, _ = delivered
    for kind in ['duplicate', 'link', 'large', 'entries']:
        bad = tmp_path/(kind+'.zip')
        with zipfile.ZipFile(package) as source, zipfile.ZipFile(bad, 'w', compression=zipfile.ZIP_DEFLATED) as output:
            for name in source.namelist():
                output.writestr(name, source.read(name))
            if kind == 'duplicate':
                with pytest.warns(UserWarning):
                    output.writestr('input.png', b'x')
            elif kind == 'link':
                info = zipfile.ZipInfo('link.png')
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                output.writestr(info, 'input.png')
            elif kind == 'large':
                output.writestr('large.png', b'0'*(8*1024*1024+1))
            else:
                for i in range(35):
                    output.writestr(str(i), b'x')
        with pytest.raises(ValueError):
            store.importPackage(bad)
        assert not list(store.root.glob('.stage-*'))
    assert set(archiveContents(package)) == {'project.json', 'manifest.json', 'input.png'}


def testCrossProcessOwnerAndNonProjectCwd(delivered, tmp_path):
    _, _, _, store, revision = delivered
    env = dict(__import__('os').environ, PYTHONPATH=str(Path(__file__).resolve().parents[3]/'src'))
    script = Path(__file__).resolve().parents[3]/'scripts/p5_project.py'
    with DirectoryOwner(store.root):
        result = subprocess.run([sys.executable, str(script), 'activate', '--store', str(store.root),
            '--revision', revision], cwd=tmp_path, env=env, capture_output=True, timeout=30)
        assert result.returncode != 0 and b'directory in use' in result.stderr
    result = subprocess.run([sys.executable, str(script), 'status', '--store', str(store.root)],
        cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0 and revision.encode() in result.stdout


def testArchiveAndTotalInflatedBudgetsRejectBeforeStaging(delivered, tmp_path):
    _, _, _, store, revision = delivered
    large = tmp_path/'archive-limit.vxpkg'
    with large.open('wb') as stream:
        stream.truncate(72*1024*1024+1)
    with pytest.raises(ValueError, match='72 MiB'):
        store.importPackage(large)
    bomb = tmp_path/'inflated.vxpkg'
    with zipfile.ZipFile(bomb, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for i in range(9):
            archive.writestr(f'{i}.png', b'0'*(8*1024*1024))
    with pytest.raises(ValueError, match='uncompressed budget'):
        store.importPackage(bomb)
    assert store.state()['active'] == revision
    assert not list(store.root.glob('.stage-*'))
