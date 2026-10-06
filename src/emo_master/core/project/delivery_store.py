"""Staged test-package import and atomic version selection. Never starts a Job."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
from uuid import uuid4
import zipfile

from emo_master.core.project.models import ProjectDocument
from emo_master import __version__
from emo_master.core.project.test_delivery import (
    MAX_ARCHIVE, MAX_ENTRIES, MAX_PROJECT, MAX_RESOURCE, MAX_TOTAL,
    FORMAT, OPERATORS, canonicalJson, manifestFor, packagePath, readJson, sha, trustedRegistry, validateTestProject,
)


class DirectoryOwner:
    """Non-reentrant OS lock. Crash releases the lock; never delete a live lock file."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.stream = None

    def acquire(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root/'.delivery.lock'
        if path.is_symlink():
            raise ValueError('linked lock file')
        stream = path.open('a+b')
        try:
            if stream.seek(0, 2) == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as error:
            stream.close()
            raise RuntimeError('delivery directory in use; stop its Runtime before changing versions') from error
        self.stream = stream
        return self

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()


def atomicJson(path, value):
    temporary = path.with_name('.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(canonicalJson(value).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _names(names):
    keys = set()
    for name in names:
        key = packagePath(name).casefold()
        if key in keys:
            raise ValueError('duplicate / case alias package entry')
        keys.add(key)
    for key in keys:
        if any('/'.join(key.split('/')[:i]) in keys for i in range(1, len(key.split('/')))):
            raise ValueError('file/directory path collision')


def _preflight(stream):
    """Bound the central directory before ZipFile allocates its entry objects."""
    stream.seek(0, 2)
    size = stream.tell()
    if size > MAX_ARCHIVE:
        raise ValueError('archive exceeds 72 MiB')
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    offset = tail.rfind(b'PK\x05\x06')
    if offset < 0 or offset + 22 > len(tail):
        raise ValueError('missing ZIP directory')
    _, disk, startDisk, onDisk, entries, directorySize, directoryOffset, comment = struct.unpack(
        '<4s4H2LH', tail[offset:offset+22])
    if (disk or startDisk or onDisk != entries or entries > MAX_ENTRIES or
            directorySize > 64 * 1024 or directoryOffset + directorySize > size or
            offset + 22 + comment != len(tail)):
        raise ValueError('ZIP directory budget / multidisk / ZIP64 not supported')
    stream.seek(0)


def archiveContents(path):
    with Path(path).open('rb') as stream:
        _preflight(stream)
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError('entry budget')
            _names(i.filename for i in entries)
            total = 0
            for item in entries:
                limit = MAX_PROJECT if item.filename in {'manifest.json', 'project.json'} else MAX_RESOURCE
                mode = stat.S_IFMT(item.external_attr >> 16)
                if (item.is_dir() or mode not in {0, stat.S_IFREG} or item.external_attr & 0x400
                        or item.flag_bits & 1 or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                    raise ValueError('linked, special, encrypted or unsupported ZIP entry')
                if item.file_size > limit:
                    raise ValueError('entry size budget')
                total += item.file_size
            if total > MAX_TOTAL:
                raise ValueError('uncompressed budget')
            result = {}
            for item in entries:
                with archive.open(item) as source:
                    data = source.read(item.file_size + 1)
                if len(data) != item.file_size:
                    raise ValueError('entry length mismatch')
                result[item.filename] = data
            verifyContents(result)
            return result


def verifyContents(files):
    if not {'project.json', 'manifest.json'} <= files.keys():
        raise ValueError('package metadata missing')
    manifest = readJson(files['manifest.json'])
    compatibility = manifest.get('compatibility', {})
    if (manifest.get('format') != FORMAT or compatibility.get('application') != __version__ or
            compatibility.get('projectSchema') != '2.2' or compatibility.get('presentationSchema') != '1.0' or
            any(key not in OPERATORS or value != OPERATORS[key][1]
                for key, value in compatibility.get('operators', {}).items()) or
            any(value != '1.0' for value in compatibility.get('components', {}).values())):
        raise ValueError('incompatible package format / application / plugin / component')
    document = ProjectDocument.model_validate(readJson(files['project.json']))
    if document.schemaVersion != '2.2' or document.resources is None:
        raise ValueError('2.2 resources required')
    declared = {'project.json', 'manifest.json'} | {i.path for i in document.resources.items.values()}
    if files.keys() != declared:
        raise ValueError('package contains missing or undeclared files')
    expected = manifestFor(document, files['project.json'], manifest.get('compatibility'))
    if manifest != expected:
        raise ValueError('invalid manifest / revision / metadata digest')
    for name, expectedFile in manifest['files'].items():
        if len(files[name]) != expectedFile['size'] or sha(files[name]) != expectedFile['sha256']:
            raise ValueError('file digest or size mismatch')
    return document, manifest


class DeliveryStore:
    MAX_VERSIONS = 8  # no automatic deletion of the previous version or its data

    def __init__(self, root, registry=None):
        self.root = Path(root).resolve()
        self.registry = registry if registry is not None else trustedRegistry()

    def owner(self):
        return DirectoryOwner(self.root)

    def _path(self, relative):
        path = self.root / relative
        current = path
        while current != self.root:
            if current.is_symlink() or (current.exists() and current.resolve() != current.absolute()):
                raise ValueError('linked delivery path')
            current = current.parent
        return path

    def state(self):
        path = self._path('active.json')
        if not path.exists():
            return {'active': None, 'previous': None}
        if path.stat().st_size > 4096:
            raise ValueError('active pointer budget')
        value = readJson(path.read_bytes())
        if set(value) != {'active', 'previous'}:
            raise ValueError('invalid active pointer')
        for revision in value.values():
            if revision is not None:
                self.revisionPath(revision)
        return value

    def revisionPath(self, revision):
        if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision):
            raise ValueError('invalid revision')
        return self._path('versions/' + revision)

    def verify(self, revision):
        directory = self.revisionPath(revision)
        files = {}
        total = 0
        for path in directory.rglob('*'):
            relative = path.relative_to(directory).as_posix()
            self._path('versions/' + revision + '/' + relative)
            if path.is_dir():
                continue
            limit = MAX_PROJECT if relative in {'manifest.json', 'project.json'} else MAX_RESOURCE
            size = path.stat().st_size
            total += size
            if size > limit or total > MAX_TOTAL or len(files) >= MAX_ENTRIES:
                raise ValueError('installed resource budget')
            with path.open('rb') as stream:
                files[relative] = stream.read(limit + 1)
            if len(files[relative]) != size:
                raise ValueError('installed file changed')
        _names(files)
        document, manifest = verifyContents(files)
        compatibility = validateTestProject(document, directory, self.registry)
        if manifest['compatibility'] != compatibility or manifest['revision'] != revision:
            raise ValueError('incompatible application / plugin / component / revision')
        return document, manifest

    def importPackage(self, package):
        with self.owner():
            files = archiveContents(package)  # ALL paths, sizes, digests before any extraction
            document, manifest = verifyContents(files)
            versions = self._path('versions')
            versions.mkdir(exist_ok=True)
            revision = manifest['revision']
            destination = self.revisionPath(revision)
            if destination.exists():
                self.verify(revision)
                return revision
            if len(list(versions.iterdir())) >= self.MAX_VERSIONS:
                raise ValueError('version quota (8); no implicit pruning')
            with tempfile.TemporaryDirectory(prefix='.stage-', dir=self.root) as temporary:
                stage = Path(temporary)
                for name, data in files.items():
                    target = stage/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                compatibility = validateTestProject(document, stage, self.registry)
                if manifest['compatibility'] != compatibility:
                    raise ValueError('incompatible application / plugin / component')
                # The directory is complete before publishing its immutable revision.
                os.rename(stage, destination)
            return revision

    def activate(self, revision):
        with self.owner():
            self.verify(revision)
            state = self.state()
            if state['active'] != revision:
                atomicJson(self._path('active.json'), {'active': revision, 'previous': state['active']})

    def rollback(self):
        with self.owner():
            state = self.state()
            if not state['previous']:
                raise ValueError('no complete previous test version')
            self.verify(state['previous'])
            atomicJson(self._path('active.json'), {'active': state['previous'], 'previous': state['active']})
            return state['previous']

    @contextmanager
    def selected(self):
        """Runtime holds ownership from preparation through explicit service shutdown."""
        with self.owner():
            revision = self.state()['active']
            if revision is None:
                raise ValueError('no active test project; import and activate explicitly')
            document, manifest = self.verify(revision)
            yield document, manifest, self.revisionPath(revision)
