"""Download only online-declared inputs/assets into an explicitly chosen local root."""
from concurrent.futures import CancelledError
import hashlib
import os
from pathlib import Path
import tempfile

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.project.resources import ResourcePlan


CHUNK_BYTES = 512 * 1024


def _checkCancelled(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise CancelledError('资源下载已取消')


def _digest(path, cancelled):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(CHUNK_BYTES):
            _checkCancelled(cancelled)
            digest.update(block)
    return digest.hexdigest()


def _destination(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('资源路径超出所选本地工程目录')
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError('资源目标不能经过符号链接')
        current = current.parent
    return path


def downloadProjectResource(call, snapshot, resourceId, resource, destination, *, cancelled=None, overwrite=False):
    """Replace one file only after all ranges and its final digest are verified."""
    destination = Path(destination)
    _checkCancelled(cancelled)
    if destination.exists():
        if not destination.is_file():
            raise ValueError('资源目标不是普通文件：' + str(destination))
        if destination.stat().st_size == resource.size and _digest(destination, cancelled) == resource.sha256:
            return 'unchanged'
        if not overwrite:
            raise ValueError('本地已有不同内容，未覆盖；请选择另一目录或先处理此文件：' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.emo-resource-', suffix='.part', dir=destination.parent,
                                         delete=False) as stream:
            staging = Path(stream.name)
            offset = 0
            digest = hashlib.sha256()
            while True:
                _checkCancelled(cancelled)
                reply = call('ReadProjectResource', pb.ReadProjectResourceRequest(
                    runtime_instance_id=snapshot['runtimeInstanceId'],
                    project_id=snapshot['payload']['project']['projectId'],
                    expected_document_revision=snapshot['documentRevision'], resource_id=resourceId,
                    sha256=resource.sha256, offset=offset, max_bytes=CHUNK_BYTES))
                _checkCancelled(cancelled)
                if not reply.ok:
                    raise ValueError(reply.code + ': ' + reply.message)
                content = reply.data
                if (reply.offset != offset or reply.total_size != resource.size or reply.sha256 != resource.sha256
                        or len(content) > CHUNK_BYTES or offset + len(content) > resource.size
                        or bool(reply.eof) != (offset + len(content) == resource.size)
                        or not content and not reply.eof):
                    raise ValueError('资源下载块的偏移、长度或声明不匹配')
                stream.write(content)
                digest.update(content)
                offset += len(content)
                if reply.eof:
                    break
            if digest.hexdigest() != resource.sha256:
                raise ValueError('资源下载内容校验失败，未替换本地文件')
            stream.flush()
            os.fsync(stream.fileno())
        _checkCancelled(cancelled)
        if destination.exists() and not overwrite:
            raise ValueError('下载期间本地已出现同名文件，未覆盖：' + str(destination))
        os.replace(staging, destination)
        staging = None
        return 'downloaded'
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def downloadProjectResources(call, snapshot, directory, *, cancelled=None, overwrite=False):
    """Keep completed files on cancellation; never copies databases or output directories."""
    plan = ResourcePlan.model_validate(snapshot['payload'].get('resources') or {})
    if not snapshot.get('runtimeInstanceId') or not snapshot.get('documentRevision'):
        raise ValueError('请先读取明确的 Runtime 实例和在线工程版本')
    root = Path(directory).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ValueError('请选择本地工程目录')
    paths = {key: _destination(root, resource.path) for key, resource in plan.items.items()}
    result = dict(directory=str(root), downloaded=[], unchanged=[], cancelled=False, errors=[])
    for key, resource in plan.items.items():
        try:
            state = downloadProjectResource(call, snapshot, key, resource, paths[key],
                cancelled=cancelled, overwrite=overwrite)
            result[state].append(dict(resourceId=key, path=resource.path))
        except CancelledError:
            result['cancelled'] = True
            break
        except (OSError, ValueError, RuntimeError) as error:
            result['errors'].append(dict(resourceId=key, path=resource.path, message=str(error)))
            break
    return result
