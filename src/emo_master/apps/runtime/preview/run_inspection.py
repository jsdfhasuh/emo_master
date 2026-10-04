"""Opt-in, leased ownership of the last two explicitly started Jobs' images.

Files are private session copies, never a new project format or a history DB.
Readers retain ownership until their generator actually exits. Failed cleanup
remains charged and prevents further copies; a timeout never proves release.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
from uuid import uuid4

from emo_master.core.contracts.port_types import normalizePortType
from . import store as legacy
from .store import PreviewAsset
from .image_headers import imageHeader

JOB_BYTES = 64 * 1024 * 1024
SESSION_BYTES = 2 * JOB_BYTES
TTL_SECONDS = 30
METADATA_BYTES = 512 * 1024


@dataclass
class InspectionJob:
    jobId: str
    revision: int
    definitions: dict
    last: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    ready: bool = False
    status: str = 'ACCEPTED'
    reason: str = ''
    reasons: dict = field(default_factory=dict)
    artifact: dict = field(default_factory=dict)
    readers: int = 0
    retired: bool = False
    directory: Path | None = None


@dataclass
class InspectionSession:
    sessionId: str
    projectKey: str
    expiresAt: float
    jobs: OrderedDict = field(default_factory=OrderedDict)
    retired: list = field(default_factory=list)
    closed: bool = False
    pending: dict = field(default_factory=dict)


class RunInspectionStore:
    def __init__(self, assets, *, clock=time.monotonic, startMaintenance=True):
        self.assets = assets
        self.root = assets.root / '_inspection'
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = assets._lock
        # Event sinks never take the disk/asset lock. This small metadata lock
        # cannot be held by a copy, reader or cleanup operation.
        self._eventLock = threading.RLock()
        self._watches = {}
        self.clock = clock
        self.sessions: dict[str, InspectionSession] = {}
        self._pendingFiles: set[Path] = set()
        self._readers = 0
        self._stop = threading.Event()
        # Session archives never survive a Runtime restart. Existing deferred
        # files count before we admit any replacement allocations.
        for path in self.root.iterdir():
            if path.is_dir():
                try:
                    shutil.rmtree(path)
                except OSError:
                    self._pendingFiles.update(p for p in path.rglob('*') if p.is_file())
        assets.inspectionUsage = self.usage
        self.thread = threading.Thread(target=self._maintenance, name='node-inspection-leases', daemon=True)
        if startMaintenance:
            self.thread.start()

    def _maintenance(self):
        while not self._stop.wait(1):
            self.expire()

    def usage(self, projectKey=None):
        with self._lock:
            root = self.root / projectKey if projectKey else self.root
            return sum(path.stat().st_size for path in root.rglob('*') if path.is_file()) if root.exists() else 0

    def _publicUsage(self, projectKey=None):
        if projectKey:
            root = self.assets.root / projectKey
            total = sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) if root.exists() else 0
            total += sum(a.path.stat().st_size for a in self.assets._assets.values()
                         if a.path.parent == self.assets.transientRoot and a.projectKey == projectKey and a.path.exists())
            return total + self.usage(projectKey)
        return sum(p.stat().st_size for p in self.assets.root.rglob('*') if p.is_file())

    def open(self, projectKey):
        with self._lock:
            self.expire()
            if self._stop.is_set() or len(self.sessions) >= 2:
                raise ValueError('E_INSPECTION_SESSION_BUDGET: Runtime accepts at most two sessions')
            session = InspectionSession(str(uuid4()), projectKey, self.clock() + TTL_SECONDS)
            self.sessions[session.sessionId] = session
            return session.sessionId

    def require(self, sessionId, projectKey):
        with self._lock:
            self.expire()
            session = self.sessions.get(sessionId)
            if session is None or session.closed or session.projectKey != projectKey:
                raise ValueError('E_INSPECTION_SESSION_EXPIRED: session expired or belongs to another project copy')
            return session

    def renew(self, sessionId, projectKey):
        with self._lock:
            session = self.require(sessionId, projectKey)
            session.expiresAt = self.clock() + TTL_SECONDS

    def attach(self, sessionId, projectKey, jobId, document, *, accepted=True):
        with self._lock:
            session = self.require(sessionId, projectKey)
            definitions = {}
            nodes = sorted(((workflowId, node) for workflowId, workflow in document.workflows.items()
                            for node in workflow.nodes), key=lambda item: (item[0], item[1].nodeId))
            for workflowId, node in nodes[:64]:
                if any(len(str(value).encode()) > 128 for value in (workflowId, node.nodeId)):
                    continue
                ports = dict(list(node.outputPorts.items())[:64])
                if len(json.dumps(ports).encode()) <= 4096 and all(len(str(port).encode()) <= 64 for port in ports):
                    definitions[(workflowId, node.nodeId)] = ports
            job = InspectionJob(jobId, document.project.revision, definitions)
            if session.pending:
                raise ValueError('E_INSPECTION_PENDING: another start is not yet resolved')
            session.pending[jobId] = job
            with self._eventLock:
                self._watches[jobId] = job
            if accepted:
                self.confirm(sessionId, projectKey, jobId)

    def confirm(self, sessionId, projectKey, jobId):
        with self._lock:
            session = self.require(sessionId, projectKey)
            session.jobs[jobId] = session.pending.pop(jobId)
            while len(session.jobs) > 2:
                _key, old = session.jobs.popitem(last=False)
                old.retired = True
                session.retired.append(old)
                with self._eventLock:
                    self._watches.pop(old.jobId, None)
            self._cleanup(session)

    def abandon(self, sessionId, projectKey, jobId):
        with self._lock:
            session = self.sessions.get(sessionId)
            if session is None or session.projectKey != projectKey:
                return
            job = session.pending.pop(jobId, None)
            if job:
                session.retired.append(job)
            with self._eventLock:
                self._watches.pop(jobId, None)
            self._cleanup(session)

    def observe(self, event):
        with self._eventLock:
            job = self._watches.get(event.jobId)
            key = (event.workflowId, event.nodeId)
            if job and key in job.definitions and not job.ready:
                if event.eventType in ('node.started', 'node.completed', 'node.failed', 'node.skipped'):
                    previous = job.last.get(key)
                    if (previous and event.eventType in ('node.completed', 'node.failed')
                            and previous['nodeRunId'] != event.nodeRunId):
                        return
                    if previous is None or event.sequence > previous['sequence']:
                        job.last[key] = {'nodeRunId': event.nodeRunId, 'workflowRunId': event.workflowRunId,
                                         'status': event.eventType, 'sequence': event.sequence}
                elif event.eventType == 'artifact.created' and len(event.payloadJson.encode()) <= 16384:
                    artifact = json.loads(event.payloadJson).get('artifact', {})
                    if isinstance(artifact, dict) and str(artifact.get('mimeType', '')).startswith('image/'):
                        job.artifact = {name: str(artifact.get(name, ''))[:1024]
                                        for name in ('path', 'checksum', 'artifactId', 'mimeType')}
                        job.artifact.update(workflowId=event.workflowId, nodeId=event.nodeId,
                                            workflowRunId=event.workflowRunId, nodeRunId=event.nodeRunId,
                                            createdAtMs=event.timestampMs)

    def terminal(self, jobId, workspace, status):
        with self._lock:
            for session in list(self.sessions.values()):
                job = session.jobs.get(jobId) or session.pending.get(jobId)
                if session.closed or job is None or job.ready:
                    continue
                job.status = status
                with self._eventLock:
                    self._watches.pop(jobId, None)
                job.directory = self.root / session.projectKey / session.sessionId / jobId
                try:
                    self._takeover(session, job, workspace)
                except Exception as error:
                    job.reason = 'E_INSPECTION_TAKEOVER: 节点图片接管失败：' + str(error)[:200]
                job.ready = True

    def _takeover(self, session, job, workspace):
        index = workspace / 'preview_staging' / 'index.json'
        if not index.exists():
            job.reason = 'E_INSPECTION_NOT_CAPTURED: 本次未采集或尚无已提交节点图片'
            return
        if index.stat().st_size > METADATA_BYTES:
            raise ValueError('snapshot index metadata exceeds 512 KiB')
        entries = json.loads(index.read_text(encoding='utf-8')).get('entries', [])
        if not isinstance(entries, list) or len(entries) > 4096:
            raise ValueError('snapshot index has too many ports')
        candidates = []
        staging = legacy._ioPath(workspace / 'preview_staging').resolve()
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('portType') != 'image':
                continue
            key = (entry.get('workflowId'), entry.get('nodeId'))
            last = job.last.get(key)
            if (last is None or last['status'] != 'node.completed' or not last['nodeRunId']
                    or entry.get('nodeRunId') != last['nodeRunId']
                    or entry.get('workflowRunId') != last['workflowRunId']
                    or entry.get('originJobId') != job.jobId
                    or entry.get('originProjectRevision') != job.revision or not entry.get('captureId')
                    or normalizePortType(job.definitions.get(key, {}).get(entry.get('port'), '')) != 'image'):
                self._reason(job, (*key, entry.get('port')), 'E_INSPECTION_IDENTITY: 快照与最后一次执行身份不匹配')
                continue
            source = (staging / str(entry.get('relativePath', ''))).resolve()
            if not source.is_relative_to(staging) or not source.is_file():
                continue
            candidates.append((entry, source))
        # Only a completed node's registered workspace copy with a checksum
        # receipt is eligible. Never retain its mutable development output path.
        artifact = job.artifact
        if artifact:
            key = (artifact['workflowId'], artifact['nodeId'])
            last = job.last.get(key, {})
            source = Path(artifact['path']).resolve()
            if (last.get('status') == 'node.completed' and last.get('nodeRunId') == artifact['nodeRunId']
                    and source.is_relative_to((workspace / 'artifacts').resolve()) and source.is_file()
                    and source.stat().st_size <= JOB_BYTES):
                digest = hashlib.sha256()
                with source.open('rb') as handle:
                    for block in iter(lambda: handle.read(256 * 1024), b''):
                        digest.update(block)
                if digest.hexdigest() == artifact['checksum']:
                    try:
                        width, height, mime = imageHeader(source)
                    except ValueError as error:
                        self._reason(job, (*key, '__saved_result__'), str(error))
                    else:
                        candidates.append(({**artifact, 'port': '__saved_result__', 'captureId': artifact['artifactId'],
                                            'width': width, 'height': height, 'mimeType': mime}, source))
        used = 0
        for entry, source in sorted(candidates, key=lambda item: (
                item[0]['workflowId'], item[0]['nodeId'], item[0]['port'])):
            size = source.stat().st_size
            key = (entry['workflowId'], entry['nodeId'], entry['port'])
            if len(job.sources) >= 128:
                self._reason(job, key, 'E_INSPECTION_METADATA_BUDGET: 图片端口元数据额度不足')
                continue
            if (size > JOB_BYTES - used or self.usage(session.projectKey) + size > SESSION_BYTES * 2
                    or self._publicUsage(session.projectKey) + size > legacy._PROJECT_LIMIT_BYTES
                    or self._publicUsage() + size > legacy._GLOBAL_LIMIT_BYTES or self._pendingFiles
                    or any(old.directory and old.directory.exists() and not old.readers for old in session.retired)):
                job.reason = 'E_INSPECTION_ASSET_BUDGET: 图片未接管，存储额度不足或清理未完成'
                self._reason(job, key, job.reason)
                continue
            sessionBytes = sum(p.stat().st_size for p in (self.root / session.projectKey / session.sessionId).rglob('*')
                               if p.is_file())
            if sessionBytes + size > SESSION_BYTES:
                job.reason = 'E_INSPECTION_ASSET_BUDGET: 会话图片超过128 MiB'
                self._reason(job, key, job.reason)
                continue
            identity = hashlib.sha256(('\0'.join((session.sessionId, job.jobId, entry['workflowId'],
                                                entry['nodeId'], entry['captureId'], entry['port']))).encode()).hexdigest()
            assetId = 'inspection-' + identity
            destination = job.directory / (assetId + '.png')
            asset = PreviewAsset(assetId, destination, str(entry.get('mimeType', 'image/png')), int(entry.get('width', 0)),
                                 int(entry.get('height', 0)), session.projectKey, entry['workflowId'],
                                 entry['nodeId'], entry['port'], 'image', tuple(entry.get('iterationPath', ())),
                                 int(entry.get('createdAtMs', 0)), job.jobId, job.revision, entry['captureId'],
                                 entry['workflowRunId'], entry['nodeRunId'])
            if self.metadataBytes(job, asset) > METADATA_BYTES:
                self._reason(job, key, 'E_INSPECTION_METADATA_BUDGET: 节点图片元数据超过512 KiB')
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                legacy._atomicCopy(source, destination)
            except OSError:
                self._pendingFiles.update(p for p in destination.parent.iterdir() if p.is_file() and
                                          p not in {asset.path for asset in job.sources})
                raise
            job.sources.append(asset)
            used += size
        if not job.sources and not job.reason:
            job.reason = '本次没有与最后一次有效节点执行匹配的已提交图片'

    @staticmethod
    def _reason(job, key, text):
        if len(job.reasons) < 128:
            job.reasons[key] = text[:256]

    @staticmethod
    def metadataBytes(job, additional=None):
        value = {'definitions': list(job.definitions.items()), 'last': list(job.last.items()),
                 'sources': [asset.__dict__ for asset in (*job.sources, *((additional,) if additional else ()))],
                 'reasons': list(job.reasons.items()), 'artifact': job.artifact, 'reason': job.reason}
        return len(json.dumps(value, default=str, separators=(',', ':')).encode())

    def list(self, sessionId, projectKey, jobId, workflowId, nodeId):
        with self._lock:
            session = self.require(sessionId, projectKey)
            job = session.jobs.get(jobId)
            if job is None:
                return [], 'EXPIRED', '任务已超出会话保留范围'
            if not job.ready:
                return [], 'PREPARING', '节点图片接管尚未封闭；任务终态不等于资产已经就绪'
            last = job.last.get((workflowId, nodeId))
            if last is None or last['status'] != 'node.completed':
                return [], 'NO_CURRENT_SNAPSHOT', 'E_INSPECTION_NOT_COMPLETED: 此节点最后一次执行没有有效完成输出'
            sources = [asset for asset in job.sources if asset.workflowId == workflowId and asset.nodeId == nodeId]
            reason = '; '.join(value for key, value in job.reasons.items() if key[:2] == (workflowId, nodeId))
            return sources, 'CURRENT_AVAILABLE' if sources else 'NO_CURRENT_SNAPSHOT', \
                reason or job.reason or 'E_INSPECTION_MISSING: 本次未输出或未采集此端口'

    def stream(self, sessionId, projectKey, assetId, active=lambda: True):
        with self._lock:
            session = self.require(sessionId, projectKey)
            match = next(((job, asset) for job in session.jobs.values() for asset in job.sources
                          if asset.assetId == assetId), None)
            if match is None:
                raise KeyError('inspection asset is not retained by this session')
            if self._readers >= 2:
                raise ValueError('E_INSPECTION_READ_BUDGET: at most two actual readers')
            job, asset = match
            job.readers += 1
            self._readers += 1
        try:
            with asset.path.open('rb') as handle:
                while active():
                    chunk = handle.read(256 * 1024)
                    if not chunk:
                        return
                    yield chunk, asset.mimeType
        finally:
            with self._lock:
                job.readers -= 1
                self._readers -= 1
                self._cleanup(session)

    def closeSession(self, sessionId, projectKey=None):
        with self._lock:
            session = self.sessions.get(sessionId)
            if session is None:
                return
            if projectKey is not None and session.projectKey != projectKey:
                raise ValueError('inspection session belongs to another project')
            session.closed = True
            with self._eventLock:
                for job in (*session.jobs.values(), *session.pending.values()):
                    self._watches.pop(job.jobId, None)
            session.retired.extend(session.jobs.values())
            session.retired.extend(session.pending.values())
            session.jobs.clear()
            session.pending.clear()
            self._cleanup(session)

    def _cleanup(self, session):
        for job in list(session.retired):
            # Evicted UI metadata is released even when physical file ownership
            # must remain. The receipt only needs its directory/readers.
            job.definitions.clear()
            job.last.clear()
            job.sources.clear()
            job.artifact.clear()
            job.reasons.clear()
            if job.readers:
                continue
            try:
                if job.directory and job.directory.exists():
                    shutil.rmtree(job.directory)
            except OSError:
                continue
            session.retired.remove(job)
        if session.closed and not session.retired:
            self.sessions.pop(session.sessionId, None)
            root = self.root / session.projectKey / session.sessionId
            if root.exists():
                try:
                    shutil.rmtree(root)
                except OSError:
                    self._pendingFiles.update(p for p in root.rglob('*') if p.is_file())

    def expire(self):
        with self._lock:
            for session in list(self.sessions.values()):
                if session.closed or self.clock() >= session.expiresAt:
                    self.closeSession(session.sessionId)
            for path in list(self._pendingFiles):
                try:
                    path.unlink(missing_ok=True)
                    self._pendingFiles.discard(path)
                except OSError:
                    pass

    def stats(self):
        with self._lock:
            return {'sessions': len(self.sessions), 'readers': self._readers, 'encodedBytes': self.usage(),
                    'jobs': sum(len(s.jobs) for s in self.sessions.values()),
                    'retiredJobs': sum(len(s.retired) for s in self.sessions.values()),
                    'pendingFiles': len(self._pendingFiles)}

    def close(self):
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(2)
        with self._lock:
            for sessionId in list(self.sessions):
                self.closeSession(sessionId)
            self.expire()
            if self.sessions or self._pendingFiles or self._readers or self.thread.is_alive():
                raise RuntimeError('Run inspection still owns readers or files; cleanup incomplete')
